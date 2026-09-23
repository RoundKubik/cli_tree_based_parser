from __future__ import annotations

import json
import subprocess
import sys
from itertools import product
from pathlib import Path
from typing import Any

import pytest

from vrp_format_matcher import (
    MappingLimits,
    MetadataCompiler,
    MetadataError,
    PreparedMetadata,
)
from vrp_parser_automaton import CommandLineParser, ParsedCommand


def rule(name: str, when: Any = "always") -> dict[str, Any]:
    return {
        "when": when,
        "condition": "parameter_name",
        "parameter_name": name,
        "entity_type": "vlan",
    }


def prepare(
    doc: str,
    device: str,
    rules: list[dict[str, Any]] | None = None,
    limits: MappingLimits | None = None,
) -> tuple[CommandLineParser, PreparedMetadata]:
    parser = CommandLineParser({"commands": [device]})
    prepared = MetadataCompiler(limits).compile(
        parser,
        [
            {
                "id": "example",
                "format": doc,
                "requires": rules or [],
            }
        ],
    )
    return parser, prepared


def application(parser: CommandLineParser, prepared: PreparedMetadata, text: str):
    result = parser.parse(text)
    assert isinstance(result, ParsedCommand)
    report = prepared.evaluate(result)
    assert len(report.applications) == 1
    return report.applications[0]


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


def test_vlan_reordered_branches_repetitions_and_missing_upper_bound() -> None:
    parser, prepared = prepare(
        "port trunk allow-pass vlan { all | { <vlan-id1> [ to <vlan-id2> ] }&<1-10> }",
        "port trunk allow-pass vlan { "
        "{ INTEGER<1-4095> [ to INTEGER<1-4095> ] } &<1-10> | all }",
        [rule("vlan-id1"), rule("vlan-id2")],
    )
    assert prepared.pairs[0].comparison.relation == "equivalent"
    result = application(parser, prepared, "  port trunk allow-pass vlan 10 to 20 30")
    assert result.binding_status == "unique"
    bindings = result.alternatives[0].bindings
    assert [(b.document.name, b.value.normalized) for b in bindings] == [
        ("vlan-id1", 10),
        ("vlan-id2", 20),
        ("vlan-id1", 30),
    ]
    assert [b.document.iterations[-1][1] for b in bindings] == [0, 0, 1]
    assert [b.device.iterations[-1][1] for b in bindings] == [0, 0, 1]
    assert bindings[0].device.slot_id == bindings[2].device.slot_id
    assert bindings[0].device.slot_id != bindings[1].device.slot_id
    assert result.status == "applied"
    assert (
        application(parser, prepared, "port trunk allow-pass vlan all").status
        == "inactive"
    )


def test_partial_scope_does_not_leak_to_other_device_branches() -> None:
    parser, prepared = prepare("c <id>", "c { INTEGER<1-100> | all }", [rule("id")])
    assert application(parser, prepared, "c 10").status == "applied"
    assert application(parser, prepared, "c all").status == "not_applicable"


def test_conditional_bindings_are_decided_by_the_complete_suffix() -> None:
    parser, prepared = prepare(
        "command { <a> | <b> to <c> }",
        "command INTEGER<1-100> [ to INTEGER<1-100> ]",
        [rule("a"), rule("b"), rule("c")],
    )
    short = application(parser, prepared, "command 10")
    long = application(parser, prepared, "command 10 to 20")
    assert [b.document.name for b in short.alternatives[0].bindings] == ["a"]
    assert [b.document.name for b in long.alternatives[0].bindings] == ["b", "c"]
    assert (
        short.alternatives[0].bindings[0].device.slot_id
        == long.alternatives[0].bindings[0].device.slot_id
    )
    assert [r.status for r in short.rules] == ["active", "inactive", "inactive"]
    assert [r.status for r in long.rules] == ["inactive", "active", "active"]


def test_dead_prefix_bindings_never_become_effects() -> None:
    parser, prepared = prepare("c <id> doc", "c INTEGER<1-100> device", [rule("id")])
    pair = prepared.pairs[0]
    assert pair.comparison.relation == "prefix_only"
    assert pair.comparison.common_prefix == ("c", "<PARAM>")
    assert pair.automaton is None
    assert application(parser, prepared, "c 10 device").status == "not_applicable"


def test_device_deduplication_does_not_erase_binding_alternatives() -> None:
    parser, prepared = prepare(
        "command <id>",
        "command [ INTEGER<1-100> ] [ INTEGER<1-100> ]",
        [rule("id")],
    )
    line = parser.parse("command 10")
    assert isinstance(line, ParsedCommand) and len(line.matches) == 1
    result = prepared.evaluate(line).applications[0]
    assert result.binding_status == "ambiguous"
    assert len(result.alternatives) == 2
    assert len({alt.bindings[0].device.slot_id for alt in result.alternatives}) == 2
    # Both interpretations attach the same rule to the same physical value.
    assert result.status == "applied"


def test_ambiguous_document_binding_does_not_choose_a_true_predicate() -> None:
    parser, prepared = prepare(
        "c [ <a> ] [ <b> ]",
        "c INTEGER<1-100>",
        [rule("a", {"op": "exists", "parameter": "a"})],
    )
    result = application(parser, prepared, "c 10")
    assert result.binding_status == "ambiguous"
    assert result.status == "ambiguous"
    assert result.rules[0].effects == ()
    assert sorted(len(alt.effects) for alt in result.alternatives) == [0, 1]


def test_repeat_predicate_uses_the_current_iteration() -> None:
    parser, prepared = prepare(
        "c { <a> [ to <b> ] } &<1-3>",
        "c { INTEGER<1-100> [ to INTEGER<1-100> ] } &<1-3>",
        [rule("a", {"op": "exists", "parameter": "b"})],
    )
    result = application(parser, prepared, "c 10 to 20 30")
    assert [e.target.normalized for e in result.rules[0].effects] == [10]


def test_prepared_roundtrip_has_no_runtime_dependency_on_document_parser(
    monkeypatch,
) -> None:
    parser, prepared = prepare("c <id>", "c INTEGER<1-100>", [rule("id")])
    payload = prepared.to_json()
    restored = PreparedMetadata.from_json(payload)
    assert restored.to_json() == payload

    def forbidden(*args, **kwargs):
        raise AssertionError("document formats must not be parsed at runtime")

    monkeypatch.setattr("vrp_parser_automaton.patterns.PatternParser.parse", forbidden)
    result = application(parser, restored, "c 10")
    assert result.status == "applied"


def test_device_parameter_and_literal_interpretations_remain_distinct() -> None:
    parser, prepared = prepare("c <id>", "c { all | STRING<1-100> }", [rule("id")])
    assert application(parser, prepared, "c all").status == "not_applicable"
    assert application(parser, prepared, "c other").status == "applied"


def test_text_parameter_is_one_structural_atom_with_its_complete_value() -> None:
    parser, prepared = prepare(
        "description <text>", "description TEXT<1-100>", [rule("text")]
    )
    result = application(parser, prepared, " description uplink to core")
    value = result.alternatives[0].bindings[0].value
    assert value.raw == "uplink to core"
    assert value.span.start == len(" description ")


def test_nested_repetitions_keep_both_coordinates() -> None:
    parser, prepared = prepare(
        "c { item <id> &<1-2> } &<1-2>",
        "c { item INTEGER<1-100> &<1-2> } &<1-2>",
        [rule("id")],
    )
    result = application(parser, prepared, "c item 10 20 item 30")
    assert [
        tuple(i for _, i in b.document.iterations)
        for b in result.alternatives[0].bindings
    ] == [
        (0, 0),
        (0, 1),
        (1, 0),
    ]


@pytest.mark.parametrize(
    "limits",
    [
        MappingLimits(automaton_states=3),
        MappingLimits(comparison_states=1),
        MappingLimits(product_states=1),
    ],
)
def test_preparation_limits_are_unknown_not_disjoint(limits: MappingLimits) -> None:
    parser, prepared = prepare("c { <id> }", "c INTEGER<1-100>", limits=limits)
    assert prepared.pairs[0].comparison.relation == "unknown"
    assert application(parser, prepared, "c 10").status == "unknown"


def test_runtime_limit_never_returns_partial_bindings() -> None:
    parser, prepared = prepare(
        "c <id>",
        "c INTEGER<1-100>",
        limits=MappingLimits(runtime_configurations=1),
    )
    result = application(parser, prepared, "c 10")
    assert result.status == "unknown"
    assert result.alternatives == ()


def test_two_document_rules_and_multiple_device_sources_stay_separate() -> None:
    parser = CommandLineParser({"commands": ["c INTEGER<1-100>", "c HEX<1-FF>"]})
    prepared = MetadataCompiler().compile(
        parser,
        [
            {"id": "first", "format": "c <id>", "requires": [rule("id")]},
            {"id": "second", "format": "c <value>", "creates": [rule("value")]},
        ],
    )
    line = parser.parse("c 10")
    assert isinstance(line, ParsedCommand) and len(line.matches) == 2
    report = prepared.evaluate(line)
    assert len(report.applications) == 4
    assert all(len(a.alternatives) == 1 for a in report.applications)
    assert {
        a.alternatives[0].bindings[0].value.normalized for a in report.applications
    } == {10, 16}


@pytest.mark.parametrize(
    "pattern",
    [
        "c { a | b } *",
        "c [ a | b ] *",
        "c { [ a ] | b } *",
        "c { a [ b ] } &<1-2>",
        "c { [ a ] } &<1-2>",
    ],
)
def test_structural_execution_agrees_with_device_runtime_for_small_languages(
    pattern: str,
) -> None:
    parser, prepared = prepare(pattern, pattern)
    assert prepared.pairs[0].comparison.relation == "equivalent"
    from vrp_format_matcher.runtime.bindings import BindingSearch, InputAtom

    machine = prepared.pairs[0].recognizer
    assert machine is not None
    search = BindingSearch(machine, 20_000)

    for length in range(5):
        for suffix in product(("a", "b"), repeat=length):
            words = ("c", *suffix)
            accepted = isinstance(parser.parse(" ".join(words)), ParsedCommand)
            atoms = tuple(InputAtom("K:" + word) for word in words)
            assert bool(search.match(atoms)) is accepted


def test_invalid_metadata_is_rejected_during_preparation() -> None:
    with pytest.raises(MetadataError, match="unknown target parameter"):
        prepare("c <id>", "c INTEGER<1-100>", [rule("missing")])
    with pytest.raises(MetadataError, match="unsupported predicate"):
        prepare("c <id>", "c INTEGER<1-100>", [rule("id", {"op": "python"})])


def test_manual_script_prepares_then_loads_artifact(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    artifact = tmp_path / "prepared.json"
    first = subprocess.run(
        [
            sys.executable,
            "manual_format_matcher_test.py",
            "--case",
            "conditional",
            "--save",
            str(artifact),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "RELATION: equivalent" in first.stdout
    assert json.loads(artifact.read_text())["version"] == 1
    second = subprocess.run(
        [
            sys.executable,
            "manual_format_matcher_test.py",
            "--load",
            str(artifact),
            "--line",
            "command 10 to 20",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "BINDINGS: unique" in second.stdout
    assert "b ()" in second.stdout


@pytest.mark.parametrize("optional", [False, True])
def test_wide_reordered_sets_are_compact_and_bind_every_selected_slot(
    optional: bool,
) -> None:
    opening, closing = ("[", "]") if optional else ("{", "}")
    doc = (
        "c " + opening + " | ".join(f"k{i} <v{i}>" for i in range(24)) + closing + " *"
    )
    device = (
        "c "
        + opening
        + " | ".join(f"k{i} INTEGER<1-100>" for i in reversed(range(24)))
        + closing
        + " *"
    )
    parser, prepared = prepare(
        doc,
        device,
        [rule(f"v{i}") for i in range(24)],
        limits=MappingLimits(comparison_states=1, product_states=100),
    )
    pair = prepared.pairs[0]
    assert pair.comparison.relation == "equivalent"
    assert pair.strategy == "structural"
    assert pair.program is not None and len(pair.program.instructions) < 100
    assert pair.automaton is None
    restored = PreparedMetadata.from_json(prepared.to_json())
    order = [23, 0, 12, *[i for i in range(24) if i not in {23, 0, 12}]]
    line = "c " + " ".join(f"k{i} {i + 1}" for i in order)
    result = application(parser, restored, line)
    assert result.binding_status == "unique"
    assert [
        (b.document.name, b.value.normalized) for b in result.alternatives[0].bindings
    ] == [(f"v{i}", i + 1) for i in order]
    assert isinstance(parser.parse("c"), ParsedCommand) is optional
    assert not isinstance(parser.parse("c k0 1 k0 2"), ParsedCommand)
    # Test the prepared recognizer itself, independent of device rejection.
    from vrp_format_matcher.runtime.bindings import BindingSearch, CommandAtoms

    permissive = CommandLineParser(
        {"commands": ["c k0 INTEGER<1-100> k0 INTEGER<1-100>"]}
    )
    repeated = permissive.parse("c k0 1 k0 2")
    assert isinstance(repeated, ParsedCommand)
    atoms = CommandAtoms(repeated.raw, repeated.matches[0].parameters).read()
    assert BindingSearch(pair.program, 20_000).match(atoms) == ()
    if optional:
        assert len(application(parser, restored, "c").alternatives) == 1


def test_real_catalog_set_of_24_alternatives() -> None:
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "data/commands.json").read_text()
    )
    device = next(
        item
        for item in catalog["commands"]
        if item.startswith("signature algorithm-list {")
    )
    prefix, body = device.split("{", 1)
    alternatives, suffix = body.rsplit("}", 1)
    doc = (
        prefix + "{ " + " | ".join(reversed(alternatives.split(" | "))) + " }" + suffix
    )
    parser, prepared = prepare(doc, device)
    assert len(alternatives.split(" | ")) == 24
    assert prepared.pairs[0].strategy == "structural"
    assert prepared.pairs[0].program is not None
    assert len(prepared.pairs[0].program.instructions) < 60
    result = application(
        parser,
        prepared,
        "signature algorithm-list ed25519 rsa-pkcs1-sha256",
    )
    assert len(result.alternatives) == 1


def test_large_repeat_is_compact_and_keeps_runtime_coordinates() -> None:
    parser, prepared = prepare(
        "c <id> &<1-100000>",
        "c INTEGER<1-100> &<1-100000>",
        [rule("id")],
        limits=MappingLimits(
            automaton_states=10, comparison_states=1, product_states=10
        ),
    )
    pair = prepared.pairs[0]
    assert pair.program is not None and len(pair.program.instructions) == 6
    restored = PreparedMetadata.from_json(prepared.to_json())
    result = application(parser, restored, "c 10 20 30")
    bindings = result.alternatives[0].bindings
    assert [b.value.normalized for b in bindings] == [10, 20, 30]
    assert [b.document.iterations[-1][1] for b in bindings] == [0, 1, 2]


@pytest.mark.parametrize(
    ("doc", "device", "line", "expected"),
    [
        ("c [ <a> ] [ <b> ]", "c [ INTEGER<1-100> ] [ INTEGER<1-100> ]", "c 10", 4),
        (
            "c { a [ <x> ] | a <y> }",
            "c { a [ INTEGER<1-100> ] | a INTEGER<1-100> }",
            "c a 10",
            4,
        ),
        ("c { <a> | <b> } *", "c { INTEGER<1-100> | INTEGER<1-100> } *", "c 10 20", 4),
    ],
)
def test_structural_shortcut_never_discards_cross_branch_bindings(
    doc: str,
    device: str,
    line: str,
    expected: int,
) -> None:
    parser, prepared = prepare(doc, device)
    assert prepared.pairs[0].comparison.relation == "equivalent"
    assert prepared.pairs[0].strategy == "intersection"
    result = application(parser, prepared, line)
    assert result.binding_status == "ambiguous"
    assert len(result.alternatives) == expected
    for alternative in result.alternatives:
        assert len({b.document.slot_id for b in alternative.bindings}) == len(
            alternative.bindings
        )
        assert len({b.device.slot_id for b in alternative.bindings}) == len(
            alternative.bindings
        )


def test_nested_sets_reset_used_branches_for_each_repetition() -> None:
    parser, prepared = prepare(
        "c { { a <x> | b <y> } * end } &<1-100000>",
        "c { { b INTEGER<1-100> | a INTEGER<1-100> } * end } &<1-100000>",
        [rule("x"), rule("y")],
    )
    assert prepared.pairs[0].strategy == "structural"
    result = application(parser, prepared, "c b 10 a 20 end a 30 end")
    bindings = result.alternatives[0].bindings
    assert [(b.document.name, b.document.iterations[-1][1]) for b in bindings] == [
        ("y", 0),
        ("x", 0),
        ("x", 1),
    ]


def test_graph_artifact_without_optional_program_fields_still_executes() -> None:
    parser, prepared = prepare(
        "c { <a> | <b> to <d> }", "c INTEGER<1-100> [ to INTEGER<1-100> ]", [rule("b")]
    )
    data = prepared.to_dict()
    data["version"] = 1
    for pair in data["pairs"]:
        assert pair["automaton"] is not None
        pair.pop("program")
        pair.pop("strategy")
    restored = PreparedMetadata.from_json(json.dumps(data))
    assert application(parser, restored, "c 10 to 20") == application(
        parser, prepared, "c 10 to 20"
    )


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
    assert len(application(parser, prepared, "c k7 k2 k0").alternatives) == 1


def test_public_format_matcher_compares_without_metadata() -> None:
    from vrp_format_matcher import FormatMatcher

    result = FormatMatcher().compare("c { <id> | all }", "c { all | INTEGER<1-100> }")
    assert result.relation == "equivalent"
    assert not result.structurally_identical


def test_device_instructions_are_reused_and_each_document_is_compiled_once(
    monkeypatch,
) -> None:
    from vrp_format_matcher import FormatMatcher
    from vrp_parser_automaton.automata.building import AutomatonBuilder

    parser = CommandLineParser({"commands": ["c INTEGER<1-100>", "d INTEGER<1-100>"]})
    built = []
    original = AutomatonBuilder.build

    def counted(builder, sources):
        built.extend(sources)
        return original(builder, sources)

    monkeypatch.setattr(AutomatonBuilder, "build", counted)
    result = FormatMatcher().compile(parser, [{"format": "c <x>"}, {"format": "d <y>"}])
    assert len(result.pairs) == 4
    assert len(built) == 2
    device_asts = {id(source.ast) for source in parser.automaton.patterns}
    assert all(id(source.ast) not in device_asts for source in built)


def test_binding_program_uses_parser_control_flow_after_loading(monkeypatch) -> None:
    from vrp_parser_automaton.runtime.execution import ControlFlow

    parser, prepared = prepare(
        "c { b <y> | a <x> } *",
        "c { a INTEGER<1-100> | b INTEGER<1-100> } *",
    )
    line = parser.parse("c b 10 a 20")
    assert isinstance(line, ParsedCommand)
    restored = PreparedMetadata.from_json(prepared.to_json())
    visited = []
    original = ControlFlow.follow

    def observed(flow, instruction, configuration):
        visited.append(instruction.kind)
        return original(flow, instruction, configuration)

    monkeypatch.setattr(ControlFlow, "follow", observed)
    application = restored.evaluate(line).applications[0]
    assert application.binding_status == "unique"
    assert "set_next" in visited and "set_commit" in visited


def test_runtime_never_realigns_formats_or_rebuilds_instructions(monkeypatch) -> None:
    from vrp_format_matcher.preparation.programs import SourceAlignment
    from vrp_parser_automaton.automata.building import AutomatonBuilder
    from vrp_parser_automaton.patterns import PatternParser

    parser, prepared = prepare(
        "c [ a <x> | b <y> ] *", "c [ b INTEGER<1-100> | a INTEGER<1-100> ] *"
    )
    restored = PreparedMetadata.from_json(prepared.to_json())

    def forbidden(*args, **kwargs):
        raise AssertionError("preparation called from runtime")

    monkeypatch.setattr(AutomatonBuilder, "build", forbidden)
    monkeypatch.setattr(PatternParser, "parse", forbidden)
    monkeypatch.setattr(SourceAlignment, "bindings", forbidden)
    result = application(parser, restored, "c b 10 a 20")
    assert [b.document.name for b in result.alternatives[0].bindings] == ["y", "x"]


def test_matcher_imports_no_original_parser_in_an_isolated_process() -> None:
    import os

    root = Path(__file__).resolve().parents[1]
    script = """
import sys
from importlib.abc import MetaPathFinder
class DenyOriginal(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "vrp_parser" or fullname.startswith("vrp_parser."):
            raise AssertionError("original parser imported: " + fullname)
sys.meta_path.insert(0, DenyOriginal())
from vrp_format_matcher import FormatMatcher
assert FormatMatcher().compare("c <x>", "c INTEGER<1-100>").relation == "equivalent"
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    assert result.returncode == 0, result.stderr


def test_artifacts_have_a_distinct_format_and_validate_instruction_targets() -> None:
    _, prepared = prepare("c <x>", "c INTEGER<1-100>")
    data = prepared.to_dict()
    assert data["kind"] == "vrp-format-matcher"
    data["pairs"][0]["program"]["instructions"][0]["target"] = 99999
    with pytest.raises(MetadataError, match="invalid instruction"):
        PreparedMetadata.from_json(json.dumps(data))
    data["kind"] = "old-engine-artifact"
    with pytest.raises(MetadataError, match="unsupported"):
        PreparedMetadata.from_json(json.dumps(data))
