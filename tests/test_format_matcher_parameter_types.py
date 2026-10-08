"""Type filtering preserves compatible source slots and path-dependent bindings."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from test_format_matcher_catalogs import catalog
from test_format_matcher_result import restored_pair
from test_format_matcher_runtime_slots import follow_saved_mapping

from vrp_format_matcher import FormatError, FormatMatcher, MappingLimits
from vrp_parser_automaton import CommandLineParser, ParsedCommand
from vrp_parser_automaton.automata.building import AutomatonBuilder

DECLARATIONS = [
    ("INTEGER<1-100>", "integer", "integer"),
    ("STRING<1-100>", "string", "string"),
    ("TEXT<1-100>", "text", "string"),
    ("X.X.X.X", "ipv4-address", "ipv4-address"),
    ("X:X::X:X", "ipv6-address", "ipv6-address"),
]


def document(pattern, **types):
    return {
        "format": pattern,
        "parameter_types": [
            {"parameter_name": name, "parameter_type": type_id}
            for name, type_id in types.items()
        ],
    }


@pytest.mark.parametrize("declaration,original,normalized", DECLARATIONS)
@pytest.mark.parametrize(
    "doc_type", ["string", "integer", "ipv4-address", "ipv6-address"]
)
def test_supported_categories_filter_bindings_but_preserve_original_types(
    declaration, original, normalized, doc_type
):
    result = FormatMatcher().compile_formats(
        ["c " + declaration], [document("c <value>", value=doc_type)]
    )
    target = next(iter(result.devices.values()))
    if doc_type != normalized:
        assert target.status == "unmatched" and result.pairs == ()
        return
    pair = result.pairs[0]
    assert pair.stage == "exact" and pair.status == "equivalent"
    (binding,) = pair.bindings
    assert binding.device.type_id == original
    assert binding.document.type_id == doc_type
    assert binding.device.slot_id == binding.document.slot_id == "p:2"
    assert binding.device.declaration == declaration


@pytest.mark.parametrize("doc_type", ["integer", "string", "ipv4-address"])
@pytest.mark.parametrize(
    "declaration,type_id", [("H-H-H", "mac"), ("HEX<0-ff>", "hex")]
)
def test_supported_mac_and_hex_exclude_other_known_categories(
    doc_type, declaration, type_id
):
    result = FormatMatcher().compile_formats(
        ["c " + declaration], [document("c <address>", address=doc_type)]
    )
    assert result.pairs == ()


@pytest.mark.parametrize(
    "declaration,type_id",
    [
        ("TEXT<1-100>", "text"),
        ("X:X::X:X/M", "ipv6-prefix"),
        ("H-H-H", "mac"),
        ("HEX<0-ff>", "hex"),
        ("PASSWORDEX<1-10>", "passwordex"),
        ("YYYY/MM/DD", "date-slash"),
        ("YYYY-MM-DD", "date-iso"),
        ("MM-DD", "month-day"),
        ("MM-DD-YYYY", "date-us"),
        ("YYYY/MM/DD,HH:MM:SS", "datetime-slash"),
        ("HH:MM:SS", "time-seconds"),
        ("<hh:mm>", "time"),
    ],
)
def test_extended_types_preserve_annotations_and_slots(declaration, type_id):
    result = FormatMatcher().compile_formats(
        ["c " + declaration], [document("c <value>", value=type_id)]
    )
    (pair,) = result.pairs
    assert pair.status == "equivalent"
    assert pair.bindings[0].document.type_id == type_id
    assert pair.bindings[0].device.slot_id == "p:2"


def test_ipv6_prefix_and_address_are_distinct_categories():
    result = FormatMatcher().compile_formats(
        ["c X:X::X:X", "c X:X::X:X/M"],
        [document("c <value>", value="ipv6-prefix")],
    )
    assert len(result.pairs) == 1
    assert result.pairs[0].device_format == "c X:X::X:X/M"


def test_legacy_document_without_types_keeps_type_independent_matching():
    result = FormatMatcher().compile_formats(
        ["c INTEGER<1-100>", "c X.X.X.X"], [{"format": "c <value>"}]
    )
    assert len(result.pairs) == 2
    assert all(pair.stage == "exact" for pair in result.pairs)
    assert all(pair.bindings[0].document.type_id is None for pair in result.pairs)


@pytest.mark.parametrize(
    "types",
    [
        None,
        {},
        "integer",
        [None],
        [{"parameter_name": "x"}],
        [{"parameter_name": "", "parameter_type": "integer"}],
        [{"parameter_name": "x", "parameter_type": "number"}],
        [{"parameter_name": "x", "parameter_type": []}],
        [{"parameter_name": "<x>", "parameter_type": "integer"}],
        [{"parameter_name": "extra", "parameter_type": "integer"}],
        [{"parameter_name": "x", "parameter_type": "integer"}] * 2,
        [],
    ],
)
def test_invalid_or_incomplete_annotations_are_rejected(types):
    with pytest.raises(FormatError, match="parameter|unsupported|duplicate"):
        FormatMatcher().compile_formats(
            ["c INTEGER<1-10>"], [{"format": "c <x>", "parameter_types": types}]
        )


def test_empty_types_for_keywords_and_one_type_for_repeated_name():
    result = FormatMatcher().compile_formats(
        ["c all", "c INTEGER<1-10> to INTEGER<1-10>"],
        [document("c all"), document("c <id> to <id>", id="integer")],
    )
    first, repeated = result.pairs
    assert first.bindings == ()
    assert {b.document.name for b in repeated.bindings} == {"id"}
    assert {b.document.slot_id for b in repeated.bindings} == {"p:2", "p:10"}


def test_catalog_type_annotations_are_required_only_for_documentation():
    device = catalog("device", [{"format": "c INTEGER<1-10>"}])
    missing = catalog("documentation", [{"format": "c <id>"}])
    with pytest.raises(FormatError, match="requires parameter_types"):
        FormatMatcher().compile_catalogs(device, missing)
    with pytest.raises(FormatError, match="device types belong in the format"):
        FormatMatcher().compile_catalogs(
            catalog("device", [document("c INTEGER<1-10>")]),
            catalog("documentation", [document("c <id>", id="integer")]),
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_same_document_text_with_different_types_has_separate_cache_entries(reverse):
    docs = [
        {"id": "number", **document("c <value>", value="integer")},
        {"id": "address", **document("c <value>", value="ipv4-address")},
    ]
    result = FormatMatcher().compile_formats(
        ["c INTEGER<1-10>", "c X.X.X.X"], list(reversed(docs)) if reverse else docs
    )
    assert [p.document_id for p in result.pairs] == ["number", "address"]


def test_documentation_targets_keep_separate_type_profiles_across_views():
    source = catalog(
        "documentation",
        views={
            "Numbers": [document("c <value>", value="integer")],
            "Addresses": [document("c <value>", value="ipv4-address")],
        },
    )
    result = FormatMatcher().compile_catalogs(source, source)
    assert len(result.pairs) == 2
    for pair in result.pairs:
        device = result.device_catalog.entries[pair.pattern_id]
        doc = result.documentation_catalog.entries[pair.document_id]
        assert device == doc
        assert pair.bindings[0].document.type_id == pair.bindings[0].device.type_id


@pytest.mark.parametrize("opening,closing", [("[", "]"), ("{", "}")])
def test_reordered_types_in_equal_shaped_branches_do_not_expand_wide_sets(
    opening, closing, monkeypatch
):
    branches = [f"k{i} STRING<1-10> | k{i} INTEGER<1-10>" for i in range(12)]
    device = f"c {opening} " + " | ".join(branches) + f" {closing} *"
    doc_branches = [f"k{i} <int{i}> | k{i} <str{i}>" for i in range(12)]
    doc_format = f"c {opening} " + " | ".join(doc_branches) + f" {closing} *"
    types = {
        f"{prefix}{i}": kind
        for i in range(12)
        for prefix, kind in [("int", "integer"), ("str", "string")]
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("structural type alignment must not expand an automaton")

    monkeypatch.setattr(AutomatonBuilder, "build", forbidden)
    pair = (
        FormatMatcher(MappingLimits(analysis_steps=1))
        .compile_formats([device], [document(doc_format, **types)])
        .pairs[0]
    )
    assert pair.stage == "reordered" and pair.binding_mode == "structural"
    assert len(pair.bindings) == 24
    for binding in pair.bindings:
        assert binding.document.type_id == binding.device.type_id
        assert (
            binding.document.slot_id
            == f"p:{doc_format.index(binding.document.declaration)}"
        )
        assert device[int(binding.device.slot_id[2:]) :].startswith(
            binding.device.declaration
        )


@pytest.mark.parametrize("opening,closing", [("[", "]"), ("{", "}")])
def test_incompatible_set_branch_is_removed_with_its_paths_and_bindings(
    opening, closing
):
    device = f"c {opening} a INTEGER<1-10> | b STRING<1-10> {closing} *"
    doc = document(f"c {opening} a <a> | b <b> {closing} *", a="integer", b="integer")
    result = FormatMatcher().compile_formats([device], [doc])
    (pair,) = result.pairs
    assert pair.stage == "intersection" and pair.status == "overlap"
    assert [b.document.name for b in pair.bindings] == ["a"]
    assert pair.binding_mode == "path_dependent"
    machine = json.loads(json.dumps(asdict(pair.automaton)))
    parser = CommandLineParser({"commands": [device]})
    for line, expected in [
        ("c a 1", {(("a", 1),)}),
        ("c b text", set()),
        ("c a 1 b text", set()),
        ("c b text a 1", set()),
    ]:
        parsed = parser.parse(line)
        assert isinstance(parsed, ParsedCommand)
        assert follow_saved_mapping(machine, line, parsed.primary_match) == expected
    data = json.loads(json.dumps(result.to_dict()))
    saved = data["devices"][pair.pattern_id]["mappings"][0]
    assert restored_pair(data, pair.pattern_id, saved) == json.loads(
        json.dumps(asdict(pair))
    )


def test_incompatible_optional_parameter_keeps_keyword_only_intersection():
    pair = (
        FormatMatcher()
        .compile_formats(
            ["c [ INTEGER<1-10> ]"], [document("c [ <value> ]", value="ipv4-address")]
        )
        .pairs[0]
    )
    assert pair.stage == "intersection" and pair.status == "overlap"
    assert pair.bindings == ()
    assert all(arc.label != "P" for edges in pair.automaton.edges for arc in edges)


def test_type_mismatch_stops_prefix_without_resynchronizing_later():
    pair = (
        FormatMatcher()
        .compile_formats(
            ["c INTEGER<1-10> STRING<1-10> INTEGER<1-10>"],
            [
                document(
                    "c <first> <middle> <last>",
                    first="integer",
                    middle="integer",
                    last="integer",
                )
            ],
        )
        .pairs[0]
    )
    assert pair.stage == "prefix" and pair.status == "prefix_match"
    assert [b.document.name for b in pair.bindings] == ["first"]
    assert all(
        arc.document is None or arc.document.name == "first"
        for edges in pair.automaton.edges
        for arc in edges
    )


def test_nested_repeat_slots_and_coordinates_survive_type_filtering():
    device = "c { { a INTEGER<1-10> | b STRING<1-10> } &<1-2> end } &<1-2>"
    doc = document(
        "c { { a <a> | b <b> } &<1-2> end } &<1-2>", a="integer", b="integer"
    )
    parser = CommandLineParser({"commands": [device]})
    # Exercise the already-compiled parser API, not just offline format parsing.
    result = FormatMatcher().compile(parser, [doc])
    (pair,) = result.pairs
    assert pair.stage == "intersection" and pair.status == "overlap"
    (binding,) = pair.bindings
    assert binding.document.name == "a"
    line = "c a 1 a 2 end a 3 end"
    parsed = parser.parse(line)
    assert isinstance(parsed, ParsedCommand)
    assert {p.slot_id for p in parsed.parameters} == {binding.device.slot_id}
    assert len(binding.device.repeat_ids) == len(binding.document.repeat_ids) == 2
    machine = json.loads(json.dumps(asdict(pair.automaton)))
    assert follow_saved_mapping(machine, line, parsed.primary_match) == {
        (("a", 1), ("a", 2), ("a", 3))
    }


def test_partly_incompatible_candidate_keeps_intersection_beside_reordered_match():
    result = FormatMatcher().compile_formats(
        ["c { a INTEGER<1-10> | b STRING<1-10> }"],
        [
            {
                "id": "wrong",
                **document("c { a <a> | b <b> }", a="integer", b="integer"),
            },
            {"id": "right", **document("c { b <b> | a <a> }", a="integer", b="string")},
        ],
    )
    partial, reordered = result.pairs
    assert partial.document_id == "wrong" and partial.stage == "intersection"
    assert [binding.document.name for binding in partial.bindings] == ["a"]
    assert partial.automaton is not None
    assert reordered.document_id == "right" and reordered.stage == "reordered"
    assert {binding.document.name for binding in reordered.bindings} == {"a", "b"}
