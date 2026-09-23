"""Offline JSON joins runtime captures by source identity across set permutations."""

from __future__ import annotations

import json
from dataclasses import asdict
from itertools import permutations

import pytest

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_parser_automaton import CommandLineParser, ConfigurationParser, ParsedCommand


def mappings(formats, documents, **options):
    result = FormatMatcher().compile_formats(formats, documents, **options)
    return json.loads(json.dumps(asdict(result)))


@pytest.mark.parametrize("opening,closing", [("[", "]"), ("{", "}")])
def test_wide_wrapped_reordered_sets_preserve_every_binding(opening, closing):
    branches = [f"option{i} INTEGER<1-100>" for i in range(24)]
    device = f"c {{ {opening} " + " | ".join(branches) + f" {closing} * }}"
    document = (
        f"c {opening} "
        + " | ".join(f"option{i} <value{i}>" for i in reversed(range(24)))
        + f" {closing} *"
    )
    result = FormatMatcher(MappingLimits(analysis_steps=1)).compile_formats(
        [device],
        [{"id": "set", "format": document}],
    )
    pair = result.pairs[0]
    assert pair.status == "equivalent"
    assert pair.binding_mode == "structural"
    assert len(pair.bindings) == 24
    data = json.loads(json.dumps(asdict(result)))
    parser = CommandLineParser({"commands": [device]})
    line = "c " + " ".join(f"option{i} {i + 1}" for i in reversed(range(24)))
    parsed = parser.parse(line)
    assert isinstance(parsed, ParsedCommand)
    match = parsed.primary_match
    saved = data["devices"][match.pattern_id]["mappings"][0]
    names = {b["device"]["slot_id"]: b["document"]["name"] for b in saved["bindings"]}
    assert [(names[p.slot_id], p.normalized) for p in match.parameters] == [
        (f"value{i}", i + 1) for i in reversed(range(24))
    ]


def test_serialized_parser_and_offline_mapping_share_ids_and_repeat_coordinates():
    device = "vlan { INTEGER<1-4096> [ to INTEGER<1-4096> ] } &<1-10>"
    document = "vlan { <first> [ to <last> ] } &<1-10>"
    data = mappings([device], [{"id": "vlan", "format": document}])
    parser = CommandLineParser({"commands": [device]})
    report = ConfigurationParser(parser).parse("  vlan 10 to 20 30")
    match = report.to_dict()["lines"][0]["primary_match"]
    saved = data["devices"][match["pattern_id"]]["mappings"][0]
    by_slot = {b["device"]["slot_id"]: b for b in saved["bindings"]}
    values = []
    for value in match["parameters"]:
        binding = by_slot[value["slot_id"]]
        doc = binding["document"]
        coordinates = dict(value["iterations"])
        assert set(coordinates) == set(binding["device"]["repeat_ids"])
        iteration = tuple(coordinates[r] for r in binding["device"]["repeat_ids"])
        values.append((doc["name"], iteration, value["normalized"]))
    assert values == [("first", (0,), 10), ("last", (0,), 20), ("first", (1,), 30)]
    assert match["parameters"][0]["span"] == {"start": 7, "end": 9}


def test_nested_repetition_coordinates_reset_at_each_outer_iteration():
    device = "c { { INTEGER<1-100> } &<1-3> end } &<1-3>"
    document = "c { { <value> } &<1-3> end } &<1-3>"
    pair = FormatMatcher().compile_formats([device], [{"format": document}]).pairs[0]
    parsed = CommandLineParser({"commands": [device]}).parse("c 1 2 end 3 end")
    assert isinstance(parsed, ParsedCommand)
    assert len({p.slot_id for p in parsed.parameters}) == 1
    assert [tuple(i for _, i in p.iterations) for p in parsed.parameters] == [
        (0, 0),
        (0, 1),
        (1, 0),
    ]
    assert (
        tuple(r for r, _ in parsed.parameters[0].iterations)
        == pair.bindings[0].device.repeat_ids
    )


def test_inclusion_limit_does_not_discard_complete_bindings():
    pair = (
        FormatMatcher(MappingLimits(comparison_states=1))
        .compile_formats(
            ["c INTEGER<1-100> [ to INTEGER<1-100> ]"],
            [{"format": "c { <a> | <b> to <c> }"}],
        )
        .pairs[0]
    )
    assert pair.status == "matched"
    assert pair.binding_mode == "path_dependent"
    assert {b.document.name for b in pair.bindings} == {"a", "b", "c"}
    assert pair.automaton is not None


def follow_saved_mapping(machine, line, match):
    """Test consumer: use parsed spans/slots, never reparse parameter values."""
    symbols = []
    position = 0
    for value in match.parameters:
        symbols.extend(
            ("K:" + w.lower(), None) for w in line[position : value.span.start].split()
        )
        symbols.append(("P", value))
        position = value.span.end
    symbols.extend(("K:" + w.lower(), None) for w in line[position:].split())
    pending = [(machine["start"], 0, ())]
    answers = set()
    for state, offset, captures in pending:
        if offset == len(symbols) and state == machine["final"]:
            answers.add(captures)
        for arc in machine["edges"][state]:
            if arc["label"] is None:
                pending.append((arc["target"], offset, captures))
            elif offset < len(symbols) and arc["label"] == symbols[offset][0]:
                value = symbols[offset][1]
                if value is None:
                    pending.append((arc["target"], offset + 1, captures))
                elif arc["device"]["slot_id"] == value.slot_id and arc["device"][
                    "iterations"
                ] == [list(i) for i in value.iterations]:
                    pending.append(
                        (
                            arc["target"],
                            offset + 1,
                            captures + ((arc["document"]["name"], value.normalized),),
                        )
                    )
    return answers


@pytest.mark.parametrize("opening,closing", [("[", "]"), ("{", "}")])
def test_partial_set_bindings_apply_only_to_accepted_full_paths(opening, closing):
    device = f"c {opening} a INTEGER<1-100> | b INTEGER<1-100> {closing} *"
    document = "c { a <a> | b <b> }"  # Exactly one branch, not a set.
    data = mappings([device], [{"format": document}])
    parser = CommandLineParser({"commands": [device]})
    for tail in ["a 1", "b 2", "a 1 b 2", "b 2 a 1"]:
        line = "c " + tail
        parsed = parser.parse(line)
        assert isinstance(parsed, ParsedCommand)
        match = parsed.primary_match
        pair = data["devices"][match.pattern_id]["mappings"][0]
        assert {b["document"]["name"] for b in pair["bindings"]} == {"a", "b"}
        answers = follow_saved_mapping(pair["automaton"], line, match)
        expected = (
            {(("a", 1),)}
            if tail == "a 1"
            else {(("b", 2),)}
            if tail == "b 2"
            else set()
        )
        assert answers == expected


def test_suffix_resolves_doc_parameter_for_the_same_device_slot():
    device = "c INTEGER<1-100> [ to INTEGER<1-100> ]"
    data = mappings([device], [{"format": "c { <single> | <first> to <last> }"}])
    parser = CommandLineParser({"commands": [device]})
    for line, expected in [
        ("c 1", (("single", 1),)),
        ("c 1 to 2", (("first", 1), ("last", 2))),
    ]:
        parsed = parser.parse(line)
        assert isinstance(parsed, ParsedCommand)
        match = parsed.primary_match
        machine = data["devices"][match.pattern_id]["mappings"][0]["automaton"]
        assert follow_saved_mapping(machine, line, match) == {expected}


def test_set_permutations_do_not_change_parameter_identity():
    device = "c [ a INTEGER<1-100> | b INTEGER<1-100> | d INTEGER<1-100> ] *"
    parser = CommandLineParser({"commands": [device]})
    slots = {}
    for items in permutations([("a", "1"), ("b", "2"), ("d", "3")]):
        result = parser.parse("c " + " ".join(f"{k} {v}" for k, v in items))
        assert isinstance(result, ParsedCommand)
        for value in result.parameters:
            assert value.slot_id == slots.setdefault(value.raw, value.slot_id)
            assert value.iterations == ()


def test_required_optional_sets_keep_bindings_with_a_small_product_budget():
    branches = " | ".join(f"k{i} <v{i}>" for i in range(6))
    pair = (
        FormatMatcher(MappingLimits(product_states=300))
        .compile_formats(
            [f"c [ {branches} ] *"],
            [{"format": f"c {{ {branches} }} *"}],
            target_syntax="document",
        )
        .pairs[0]
    )
    assert pair.status == "document_subset"
    assert len(pair.bindings) == 6
    assert pair.binding_mode == "path_dependent"
