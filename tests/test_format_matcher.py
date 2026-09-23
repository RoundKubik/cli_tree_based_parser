from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_parser_automaton import CommandLineParser


def prepare(doc, device, limits=None):
    parser = CommandLineParser({"commands": [device]})
    return parser, FormatMatcher(limits).compile(
        parser, [{"format": doc}], exhaustive=True
    )


@pytest.mark.parametrize(
    ("doc", "device", "relation", "identical"),
    [
        ("COMMAND <x>", "command INTEGER<1-100>", "equivalent", True),
        ("c { a | b }", "c { b | a }", "equivalent", False),
        ("c { a b | a d }", "c a { b | d }", "equivalent", False),
        ("c all", "c { INTEGER<1-100> | all }", "document_subset", False),
        ("c { <id> | all }", "c all", "device_subset", False),
        ("c { a | b }", "c { b | d }", "overlap", False),
        ("c <id> a", "c INTEGER<1-100> b", "prefix_only", False),
        ("a <id>", "b INTEGER<1-100>", "disjoint", False),
        ("c <id>", "c all", "prefix_only", False),
        ("c <id> &<1-2>", "c INTEGER<1-100> &<1-3>", "document_subset", False),
        ("c { a | b } *", "c { b | a } *", "equivalent", False),
        ("c [ a | b ] *", "c { b | a } *", "device_subset", False),
        ("c { [ a ] } &<1-2>", "c a [ a ]", "equivalent", False),
        ("c { [ a ] } *", "c a", "equivalent", False),
    ],
)
def test_language_relations(
    doc: str, device: str, relation: str, identical: bool
) -> None:
    _, prepared = prepare(doc, device)
    result = prepared.pairs[0].comparison
    assert result.relation == relation
    assert result.structurally_identical is identical


def test_language_difference_witnesses() -> None:
    _, prepared = prepare("c { a | b }", "c { b | d }")
    comparison = prepared.pairs[0].comparison
    assert comparison.common_example == ("c", "b")
    assert comparison.document_only_example == ("c", "a")
    assert comparison.device_only_example == ("c", "d")


def test_empty_language_is_a_subset_even_without_a_common_word() -> None:
    # A required set cannot count a zero-width repetition as a selected item.
    _, prepared = prepare("c { <id> &<0-0> } *", "c a")
    comparison = prepared.pairs[0].comparison
    assert comparison.relation == "document_subset"
    assert comparison.common_example is None
    assert comparison.document_only_example is None
    assert comparison.device_only_example == ("c", "a")


def test_lazy_intersection_avoids_epsilon_cartesian_product() -> None:
    # Different ASTs prevent the structural shortcut. Eight alternatives exceeded
    # the previous 20,000-state budget even without parameters.
    alternatives = " | ".join(f"k{i}" for i in range(8))
    parser, prepared = prepare(
        f"c {{ {alternatives} }} *", f"c {{ {{ {alternatives} }} * }}"
    )
    pair = prepared.pairs[0]
    assert pair.comparison.relation == "equivalent"
    assert pair.strategy == "intersection"
    assert pair.automaton is not None and len(pair.automaton.edges) < 3_000
    assert pair.bindings == ()


def test_public_format_matcher_compares_without_metadata() -> None:
    from vrp_format_matcher import FormatMatcher

    result = FormatMatcher().compare("c { <id> | all }", "c { all | INTEGER<1-100> }")
    assert result.relation == "equivalent"
    assert not result.structurally_identical


@pytest.mark.parametrize(
    "limits",
    [
        MappingLimits(automaton_states=3),
        MappingLimits(comparison_states=1),
        MappingLimits(product_states=1),
        MappingLimits(analysis_steps=1),
    ],
)
def test_fallback_limits_return_unknown_without_partial_correspondences(limits):
    _, result = prepare("c { <id> }", "c INTEGER<1-100>", limits)
    assert result.pairs[0].status == "unknown"
    assert result.pairs[0].bindings == ()


def test_partial_mapping_only_contains_parameters_on_complete_common_paths():
    _, result = prepare(
        "c { shared <id> | doc <other> }",
        "c { shared INTEGER<1-100> | device INTEGER<1-100> }",
    )
    pair = result.pairs[0]
    assert pair.status == "overlap"
    assert pair.binding_mode == "path_dependent"
    assert [b.document.name for b in pair.bindings] == ["id"]


def test_prefix_only_has_no_parameter_mapping():
    _, result = prepare("c <id> doc", "c INTEGER<1-100> device")
    pair = result.pairs[0]
    assert pair.status == "prefix_only"
    assert pair.comparison.common_prefix == ("c", "<PARAM>")
    assert pair.bindings == () and pair.automaton is None


def test_different_shapes_keep_conditional_slot_correspondences():
    _, result = prepare(
        "c { <a> | <b> to <c> }", "c INTEGER<1-100> [ to INTEGER<1-100> ]"
    )
    pair = result.pairs[0]
    assert pair.status == "equivalent"
    assert pair.binding_mode == "path_dependent"
    links = pair.bindings
    assert [b.document.name for b in links] == ["a", "b", "c"]
    assert links[0].device.slot_id == links[1].device.slot_id
    assert links[2].device.slot_id != links[0].device.slot_id


def test_reordered_sets_and_nested_repeats_map_without_expanding():
    _, result = prepare(
        "c { { a <x> | b <y> } * end } &<1-100000>",
        "c { { b INTEGER<1-100> | a INTEGER<1-100> } * end } &<1-100000>",
        MappingLimits(analysis_steps=1),
    )
    pair = result.pairs[0]
    assert pair.status == "equivalent" and pair.strategy == "structural"
    assert pair.automaton is None
    assert [b.document.name for b in pair.bindings] == ["x", "y"]
    assert int(pair.bindings[0].device.slot_id[2:]) > int(
        pair.bindings[1].device.slot_id[2:]
    )


def test_manual_script_saves_plain_offline_mapping(tmp_path):
    output = tmp_path / "mapping.json"
    result = subprocess.run(
        [
            sys.executable,
            "manual_format_matcher_test.py",
            "--case",
            "conditional",
            "--save",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "RELATION: equivalent" in result.stdout
    assert "PARAMETER MAPPING:" in result.stdout
    saved = json.loads(output.read_text())
    assert saved["pairs"][0]["comparison"]["relation"] == "equivalent"
    assert [b["document"]["name"] for b in saved["pairs"][0]["bindings"]] == [
        "a",
        "b",
        "c",
    ]
    assert "version" not in saved


def test_matcher_does_not_import_legacy_parser_or_predicate_modules():
    code = r"""
import sys
class Forbidden:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'vrp_parser' or fullname.startswith('vrp_parser.'):
            raise AssertionError(fullname)
        if fullname.startswith((
            'vrp_format_matcher.storage', 'vrp_format_matcher.runtime'
        )):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Forbidden())
from vrp_format_matcher import FormatMatcher
result = FormatMatcher().compile_formats(
    ['c <value>'], [{'format': 'c <id>'}], target_syntax='document'
)
assert result.pairs[0].bindings[0].document.name == 'id'
"""
    import os

    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
