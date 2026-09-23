"""Automatic catalog passes and parameter bindings on unfinished traces."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_format_matcher.preparation.candidates import (
    CandidateIndex,
    PrefixCandidateIndex,
)
from vrp_format_matcher.preparation.pipeline import DocumentationIndex
from vrp_parser_automaton import CommandLineParser, ParsedCommand


def test_four_global_passes_only_visit_remaining_devices():
    devices = [
        "exact INTEGER<1-10>",
        "iface { INTEGER<1-10> INTEGER<1-10> | INTEGER<1-10> }",
        "range INTEGER<1-10> [ to INTEGER<1-10> ]",
        "prefix INTEGER<1-10> device INTEGER<1-10>",
        "absent INTEGER<1-10>",
    ]
    documents = [
        {"id": "exact", "format": "exact <id>"},
        {"id": "exact-partial", "format": "exact { <id> | all }"},
        {"id": "reordered", "format": "iface { <name> | <type> <name> }"},
        {"id": "iface-partial", "format": "iface <name>"},
        {"id": "intersection", "format": "range <single>"},
        {"id": "range-prefix", "format": "range <id> documentation"},
        {"id": "prefix", "format": "prefix <first> doc <tail>"},
    ]
    progress = []
    result = FormatMatcher().compile_formats(
        devices, documents, on_progress=progress.append
    )
    assert [
        (event.stage, event.devices_total)
        for event in progress
        if event.devices_done == 0
    ] == [
        ("exact", 5),
        ("reordered", 4),
        ("intersection", 3),
        ("prefix", 2),
    ]
    assert list(dict.fromkeys(e.stage for e in progress)) == [
        "exact",
        "reordered",
        "intersection",
        "prefix",
    ]
    assert [d.device_format for d in result.devices.values()] == devices
    assert [d.stage for d in result.devices.values()] == [
        "exact",
        "reordered",
        "intersection",
        "prefix",
        None,
    ]
    assert [d.status for d in result.devices.values()] == [
        "matched",
        "matched",
        "matched",
        "partial",
        "unmatched",
    ]
    assert [p.document_id for p in result.pairs] == [
        "exact",
        "reordered",
        "intersection",
        "prefix",
    ]
    prefix = result.pairs[-1]
    assert prefix.status == "prefix_match" and prefix.binding_mode == "prefix_dependent"
    assert [b.document.name for b in prefix.bindings] == ["first"]
    assert progress[-1].pairs_prepared == 4


def test_exact_pass_does_not_build_even_the_reordered_index(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no later stage may run after an exact match")

    monkeypatch.setattr(DocumentationIndex, "reordered", property(forbidden))
    monkeypatch.setattr(CandidateIndex, "__init__", forbidden)
    monkeypatch.setattr(PrefixCandidateIndex, "__init__", forbidden)
    branch = " | ".join(f"k{i} <id{i}>" for i in range(24))
    source = f"c {{ {branch} }} *"
    result = FormatMatcher().compile_formats(
        [source],
        [{"format": source}],
        target_syntax="document",
    )
    assert result.pairs[0].stage == "exact"
    assert len(result.pairs[0].bindings) == 24


def test_exact_document_wins_even_if_reordered_document_appears_first():
    device = "interface { STRING<1-20> STRING<1-20> | STRING<1-20> }"
    documents = [
        {"id": "reordered", "format": "interface { <name> | <type> <name> }"},
        {"id": "exact", "format": "interface { <type> <name> | <name> }"},
        {
            "id": "exact-copy",
            "format": "interface { <kind> <identifier> | <identifier> }",
        },
    ]
    result = FormatMatcher().compile_formats([device, device], documents)
    for target in result.devices.values():
        assert target.stage == "exact"
        assert [p.document_id for p in target.mappings] == ["exact", "exact-copy"]
    assert len(result.devices) == 2


def test_reordered_pass_maps_the_users_interface_example_to_runtime_slots():
    device = "interface { STRING<1-20> STRING<1-20> | STRING<1-20> }"
    document = "interface { <interface-name> | <interface-type> <interface-name> }"
    result = FormatMatcher().compile_formats([device], [{"format": document}])
    pair = result.pairs[0]
    assert pair.stage == "reordered"
    parser = CommandLineParser({"commands": [device]})
    parsed = parser.parse("interface Vlanif 10")
    assert isinstance(parsed, ParsedCommand)
    by_slot = {b.device.slot_id: b.document.name for b in pair.bindings}
    assert [(by_slot[p.slot_id], p.raw) for p in parsed.parameters] == [
        ("interface-type", "Vlanif"),
        ("interface-name", "10"),
    ]


def test_intersection_stage_keeps_all_full_matches_and_skips_prefix_stage(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a full match must prevent prefix fallback")

    monkeypatch.setattr(PrefixCandidateIndex, "__init__", forbidden)
    result = FormatMatcher().compile_formats(
        ["c { all | INTEGER<1-10> }"],
        [{"id": "all", "format": "c all"}, {"id": "value", "format": "c <id>"}],
    )
    assert [p.document_id for p in result.pairs] == ["all", "value"]
    assert all(p.stage == "intersection" for p in result.pairs)
    assert result.pairs[0].bindings == ()  # No parameter is needed for a match.
    assert result.pairs[1].bindings[0].document.name == "id"


def test_prefix_index_keeps_divergent_suffixes_and_never_rejoins_after_them():
    result = FormatMatcher().compile_formats(
        ["c INTEGER<1-10> right INTEGER<1-10> shared INTEGER<1-10>"],
        [{"format": "c <first> left <second> shared <third>"}],
    )
    pair = result.pairs[0]
    assert pair.stage == "prefix"
    assert [b.document.name for b in pair.bindings] == ["first"]
    assert pair.automaton is not None
    assert all(
        arc.label != "K:shared" for edges in pair.automaton.edges for arc in edges
    )
    saved = json.loads(json.dumps(asdict(result)))
    entry = next(iter(saved["devices"].values()))
    assert entry["status"] == "partial" and entry["stage"] == "prefix"


def accepted_parameter_counts(machine):
    """Count parameter-consuming transitions on accepting paths in a small graph."""
    pending = [(machine.start, 0)]
    seen = set(pending)
    accepted = set()
    for state, count in pending:
        if state == machine.final:
            accepted.add(count)
        for arc in machine.edges[state]:
            next_item = (arc.target, count + (arc.label == "P"))
            if next_item not in seen:
                seen.add(next_item)
                pending.append(next_item)
    return accepted


def test_prefix_scope_retains_short_and_long_repeated_beginnings():
    pair = (
        FormatMatcher()
        .compile_formats(
            ["c INTEGER<1-10> &<1-3> device"],
            [{"format": "c <id> &<1-3> doc"}],
        )
        .pairs[0]
    )
    assert pair.stage == "prefix"
    assert len(pair.bindings) == 1
    assert pair.automaton is not None
    assert accepted_parameter_counts(pair.automaton) == {0, 1, 2, 3}
    captures = [
        arc.device for edges in pair.automaton.edges for arc in edges if arc.device
    ]
    assert {tag.iterations[0][1] for tag in captures} == {0, 1, 2}


def test_empty_and_keyword_only_prefixes_produce_no_parameter_mappings():
    result = FormatMatcher().compile_formats(
        ["other INTEGER<1-10>", "c device"],
        [{"format": "c doc"}],
    )
    first, second = result.devices.values()
    assert first.status == "unmatched" and first.mappings == ()
    assert second.status == "unmatched" and second.mappings == ()


def test_exhausted_analysis_is_not_misreported_as_unmatched():
    result = FormatMatcher(MappingLimits(analysis_steps=1)).compile_formats(
        ["c { INTEGER<1-10> | all }"],
        [{"format": "c <id>"}],
    )
    target = next(iter(result.devices.values()))
    assert target.status == "unknown" and target.stage is None
    assert {p.stage for p in target.mappings} == {"intersection", "prefix"}
    assert all(p.status == "unknown" and p.bindings == () for p in target.mappings)


@pytest.mark.parametrize("mode", ["best", "all"])
def test_removed_mode_argument_is_not_silently_accepted(mode):
    with pytest.raises(TypeError, match="mode"):
        FormatMatcher().compile_formats(["c"], [{"format": "c"}], mode=mode)
