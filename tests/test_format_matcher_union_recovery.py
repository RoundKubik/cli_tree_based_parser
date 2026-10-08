"""Recover contexts from split formats while retaining every original binding."""

from __future__ import annotations

import copy

import pytest
from test_format_matcher_catalogs import catalog, command, document

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_format_matcher.hierarchy.languages import FormatCoverage
from vrp_format_matcher.preparation.pairs import CompiledPattern
from vrp_format_matcher.preparation.programs import ProgramCompiler
from vrp_format_matcher.preparation.sources import TargetFormats
from vrp_parser_automaton import CommandLineParser, ConfigurationParser, ParsedCommand


def catalogs(devices, documents):
    return (
        catalog(
            "device",
            views={"root": [command("enter")], "child": list(map(command, devices))},
        ),
        catalog(
            "documentation",
            views={
                "Root": [{**document("enter"), "switch_to_view": "Child"}],
                "Child": list(map(document, documents)),
            },
        ),
    )


@pytest.mark.parametrize(
    "devices,documents",
    [
        (["c { a | b } INTEGER<1-9>"], ["c a <first>", "c b <second>"]),
        (["c a INTEGER<1-9>", "c b INTEGER<1-9>"], ["c { a | b } <id>"]),
        (
            ["c a { x | y } INTEGER<1-9>", "c b { x | y } INTEGER<1-9>"],
            ["c { a | b } x <id>", "c { a | b } y <id>"],
        ),
        (["c { a | b }"], ["c a", "c b"]),
        (["c [ a | b ] *"], ["c", "c a", "c b", "c a b", "c b a"]),
        (["c { a | b } *"], ["c a", "c b", "c a b", "c b a"]),
        (["c INTEGER<1-9> &<1-2>"], ["c <first>", "c <first> <second>"]),
    ],
)
def test_mutual_union_coverage_recovers_views(devices, documents):
    device, docs = catalogs(devices, documents)
    unchanged = copy.deepcopy((device, docs))
    old_pairs = FormatMatcher().compile_catalogs(device, docs).pairs
    prepared = FormatMatcher().prepare_catalogs(device, docs)
    assert (device, docs) == unchanged
    assert prepared.catalog["type"] == "grouped" and not prepared.unresolved
    assert prepared.catalog["views"]["root"][0]["switch_to_view"] == "child"
    assert prepared.mapping.hierarchy.resolved_views == {
        "root": "Root",
        "child": "Child",
    }
    assert prepared.mapping.pairs == old_pairs


@pytest.mark.parametrize(
    "devices,documents,covered",
    [
        (["c { a | b | d }"], ["c a", "c b"], True),
        (["c a", "c b"], ["c { a | b | d }"], False),
        (["c [ a | b ] *"], ["c a", "c b", "c a b", "c b a"], True),
        (["c { a | b } *"], ["c a", "c b", "c a b"], True),
        (["c { a | b } { x | y }"], ["c a x", "c b y"], True),
    ],
)
def test_sample_coverage_does_not_prove_effects_of_extra_device_branches(
    devices, documents, covered
):
    prepared = FormatMatcher().prepare_catalogs(*catalogs(devices, documents))
    assert prepared.catalog["type"] == "grouped" and prepared.unresolved
    assert prepared.mapping.hierarchy.targets["Child"].status == (
        "resolved" if covered else "unresolved"
    )
    assert prepared.catalog["views"]["root"][0]["switch_to_view"] == (
        "child" if covered else {"status": "unresolved"}
    )
    assert all(
        command["switch_to_view"] == {"status": "unresolved"}
        for command in prepared.catalog["views"]["child"]
    )


def test_formats_from_different_documentation_views_are_never_unioned():
    device, docs = catalogs(["c { a | b }"], ["c a"])
    docs["views"]["Other"] = [document("c b")]
    prepared = FormatMatcher().prepare_catalogs(device, docs)
    assert prepared.catalog["type"] == "grouped" and prepared.unresolved
    assert prepared.mapping.hierarchy.resolved_views == {"root": "Root"}


@pytest.mark.parametrize("same_target", [False, True])
def test_split_entry_formats_transfer_only_a_common_effect(same_target):
    device = catalog(
        "device",
        views={
            "root": [command("enter { a | b } INTEGER<1-9>")],
            "first": [command("first-rule")],
            "second": [command("second-rule")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("enter a <first>"), "switch_to_view": "First"},
                {
                    **document("enter b <second>"),
                    "switch_to_view": "First" if same_target else "Second",
                },
            ],
            "First": [document("first-rule")],
            "Second": [document("second-rule")],
        },
    )
    prepared = FormatMatcher().prepare_catalogs(device, docs)
    assert prepared.catalog["type"] == "grouped"
    assert prepared.catalog["views"]["root"][0]["switch_to_view"] == (
        "first" if same_target else {"status": "unresolved"}
    )
    identifier = next(iter(prepared.mapping.devices))
    assert (identifier in prepared.unresolved) is not same_target
    saved = prepared.mapping.to_dict()
    parser = CommandLineParser(prepared.catalog)
    for branch in ("a", "b"):
        parsed = parser.parse(f"enter {branch} 1")
        assert isinstance(parsed, ParsedCommand)
        match = parsed.primary_match
        assert match.pattern_id == identifier
        assert {p.slot_id for p in match.parameters} == set(
            saved["devices"][identifier]["slots"]
        )
    if same_target:
        report = ConfigurationParser(parser).parse("enter a 1\n first-rule")
        assert report.lines[1].view == "first"
        assert all(isinstance(line, ParsedCommand) for line in report.lines)
    else:
        report = ConfigurationParser(parser).parse("enter a 1\n first-rule")
        assert report.lines[1].view is None
        assert parser.parse("first-rule", view="first").view == "first"
        assert parser.parse("second-rule", view="second").view == "second"
    assert len(prepared.mapping.devices[identifier].mappings) == 2
    assert {
        b.document.name
        for p in prepared.mapping.devices[identifier].mappings
        for b in p.bindings
    } == {"first", "second"}


def test_union_coverage_is_available_for_documentation_targets():
    target = catalog(
        "documentation",
        views={
            "r": [document("stay")],
            "v": [document("c { a | b } <id>")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [document("stay")],
            "Child": [document("c a <a>"), document("c b <b>")],
        },
    )
    prepared = FormatMatcher().prepare_catalogs(target, docs)
    assert prepared.mapping.hierarchy.resolved_views == {"r": "Root", "v": "Child"}
    assert prepared.catalog["type"] == "grouped" and not prepared.unresolved


def test_coverage_budget_failure_preserves_bindings_and_unknown_context():
    device, docs = catalogs(["c { a | b } INTEGER<1-9>"], ["c a <a>", "c b <b>"])
    prepared = FormatMatcher(MappingLimits(comparison_states=1)).prepare_catalogs(
        device, docs
    )
    assert prepared.catalog["type"] == "grouped" and prepared.unresolved
    assert prepared.mapping.hierarchy.targets["Child"].status == "unresolved"
    child = list(prepared.mapping.devices.values())[1]
    assert len(child.mappings) == 2
    assert all(pair.bindings and pair.automaton is not None for pair in child.mappings)


@pytest.mark.parametrize("same_effect", [False, True])
def test_proved_coverage_can_tolerate_uncertain_pair_only_with_same_effect(same_effect):
    device = catalog(
        "device",
        views={"root": [command("enter { a | b }")], "child": [command("rule")]},
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("enter { a | b }"), "switch_to_view": "Child"},
                {
                    **document("enter a"),
                    "switch_to_view": "Child" if same_effect else None,
                },
            ],
            "Child": [document("rule")],
        },
    )
    prepared = FormatMatcher(MappingLimits(product_states=1)).prepare_catalogs(
        device, docs
    )
    root = next(iter(prepared.mapping.devices.values()))
    assert any(pair.status == "unknown" for pair in root.mappings)
    assert prepared.catalog["type"] == "grouped"
    assert prepared.catalog["views"]["root"][0]["switch_to_view"] == (
        "child" if same_effect else {"status": "unresolved"}
    )


def test_exact_view_recovery_does_not_run_union_automata(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("existing whole-format proofs must suffice")

    monkeypatch.setattr(FormatCoverage, "_prove", forbidden)
    prepared = FormatMatcher().prepare_catalogs(
        *catalogs(["c INTEGER<1-9>"], ["c <id>"])
    )
    assert prepared.catalog["type"] == "grouped"


def test_equal_languages_share_a_cached_proof_and_existing_programs(monkeypatch):
    patterns = TargetFormats(
        ["c { a | b }", "c a", "c b", "c { b | a }", "{ c a }"]
    ).patterns()
    compiled = {
        source.pattern_id: CompiledPattern(source.ast, 20_000, False)
        for source in patterns
    }
    for pattern in compiled.values():
        pattern.build.require()
    coverage = FormatCoverage(compiled, MappingLimits())

    def forbidden(*args, **kwargs):
        raise AssertionError("programs and equal language proofs must be reused")

    monkeypatch.setattr(ProgramCompiler, "single", forbidden)
    ids = list(compiled)
    assert coverage.covers(ids[0], ids[1:3]) is True
    monkeypatch.setattr(FormatCoverage, "_prove", forbidden)
    assert coverage.covers(ids[3], [ids[4], ids[2], ids[1]]) is True
