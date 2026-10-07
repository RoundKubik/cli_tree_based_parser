"""All proven full matches survive, including commands without parameters."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from test_format_matcher_catalogs import catalog, command, document
from test_format_matcher_result import restored_pair
from test_format_matcher_runtime_slots import follow_saved_mapping

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_parser_automaton import CommandLineParser, ParsedCommand


@pytest.mark.parametrize("source", ["device", "documentation"])
def test_parameterless_commands_keep_every_match_and_its_source_view(source):
    targets = catalog(
        source,
        views={
            "root": [],
            "child": [document("c all")]
            if source == "documentation"
            else [command("c all")],
        },
    )
    switch = {**document("c all"), "switch_to_view": "Child"}
    docs = catalog(
        "documentation",
        views={
            "Root": [],
            "First": [switch],
            "Second": [document("c [ all ]")],
            "Child": [],
        },
    )
    result = FormatMatcher().compile_catalogs(targets, docs)
    (match,) = result.devices.values()
    assert match.status == "matched" and match.stage == "mixed"
    assert [pair.stage for pair in match.mappings] == ["exact", "intersection"]
    assert all(pair.bindings == () for pair in match.mappings)
    data = json.loads(json.dumps(result.to_dict()))
    assert "view_candidates" not in data
    saved = next(iter(data["devices"].values()))
    assert saved["slots"] == {}
    assert [
        data["documents"][p.document_id]["source"]["view"] for p in match.mappings
    ] == ["First", "Second"]
    for pair, record in zip(match.mappings, saved["mappings"], strict=True):
        assert restored_pair(data, pair.pattern_id, record) == json.loads(
            json.dumps(asdict(pair))
        )
    # Command-level semantics remains accessible through the source location.
    location = data["documents"]["doc:0"]["source"]
    assert docs["views"][location["view"]][location["index"]] == switch


@pytest.mark.parametrize("opening,closing", [("[", "]"), ("{", "}")])
@pytest.mark.parametrize("parameters", [False, True])
def test_set_keeps_exact_reordered_and_partial_scopes(opening, closing, parameters):
    a = "a INTEGER<1-10>" if parameters else "a"
    b = "b INTEGER<1-10>" if parameters else "b"
    doc_a = "a <first>" if parameters else "a"
    doc_b = "b <second>" if parameters else "b"
    device = f"c {opening} {a} | {b} {closing} *"
    docs = [
        {"format": f"c {opening} {doc_a} | {doc_b} {closing} *"},
        {"format": f"c {opening} {doc_b} | {doc_a} {closing} *"},
        {"format": f"c {{ {doc_a} | {doc_b} }}"},
    ]
    result = FormatMatcher().compile_formats([device], docs)
    assert [p.stage for p in result.pairs] == ["exact", "reordered", "intersection"]
    assert all(len(p.bindings) == (2 if parameters else 0) for p in result.pairs)
    partial = result.pairs[-1]
    data = json.loads(json.dumps(result.to_dict()))
    saved = data["devices"][partial.pattern_id]["mappings"][-1]
    machine = restored_pair(data, partial.pattern_id, saved)["automaton"]
    parser = CommandLineParser({"commands": [device]})
    single = "c a 1" if parameters else "c a"
    both = "c b 2 a 1" if parameters else "c b a"
    for line in (single, both):
        parsed = parser.parse(line)
        assert isinstance(parsed, ParsedCommand)
        answers = follow_saved_mapping(machine, line, parsed.primary_match)
        assert bool(answers) == (line == single)


def test_unknown_pair_survives_beside_exact_match_without_prefix_fallback():
    result = FormatMatcher(MappingLimits(analysis_steps=1)).compile_formats(
        ["c [ INTEGER<1-10> ]"],
        [{"format": "c [ <id> ]"}, {"format": "c <id>"}],
    )
    (match,) = result.devices.values()
    exact, unknown = match.mappings
    assert match.status == "matched" and match.stage == "exact"
    assert exact.stage == "exact" and exact.bindings
    assert unknown.status == "unknown" and unknown.stage == "intersection"


def test_catalog_keeps_all_full_pairs_found_by_individual_comparison():
    formats = [
        "c",
        "c a",
        "c b",
        "c <x>",
        "c [ a ]",
        "c [ <x> ]",
        "c { a | b }",
        "c { b | a }",
        "c [ a | b ] *",
        "c { a | b } *",
        "c { a <x> | b <y> } *",
        "c [ a <x> | b <y> ] *",
        "c a <x>",
        "c <x> [ to <y> ]",
        "c { <x> | <x> to <y> }",
        "c { <x> [ to <y> ] } &<1-2>",
        "other <x>",
    ]
    docs = [{"id": str(i), "format": source} for i, source in enumerate(formats)]
    matcher = FormatMatcher()
    result = matcher.compile_formats(formats, docs, target_syntax="document")
    for target in result.devices.values():
        expected = {
            doc["id"]
            for doc in docs
            if matcher.compare(doc["format"], target.device_format).relation
            in {"equivalent", "document_subset", "device_subset", "overlap"}
        }
        assert {pair.document_id for pair in target.mappings} == expected
        assert all(pair.binding_mode != "unavailable" for pair in target.mappings)
