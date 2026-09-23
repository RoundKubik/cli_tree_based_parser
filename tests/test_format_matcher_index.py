"""Candidate filtering must reduce work without losing language intersections."""

from __future__ import annotations

import pytest

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_format_matcher.documents.catalog import Documentation
from vrp_format_matcher.preparation.candidates import CandidateIndex, PrefixCover
from vrp_format_matcher.preparation.pairs import PairPreparation
from vrp_parser_automaton import CommandLineParser


def test_large_catalog_compares_candidates_instead_of_cartesian_product(monkeypatch):
    parser = CommandLineParser(
        {"commands": [f"feature{i} INTEGER<1-100>" for i in range(1100)]}
    )
    documents = [{"format": f"feature{i} <id>"} for i in range(900)]
    visited = []
    original = PairPreparation.prepared

    def observed(pair):
        visited.append((pair.document.document_id, pair.device.pattern_id))
        return original(pair)

    monkeypatch.setattr(PairPreparation, "prepared", observed)
    progress = []
    prepared = FormatMatcher().compile(parser, documents, on_progress=progress.append)
    assert len(visited) == len(prepared.pairs) == 900
    assert all(pair.comparison.relation == "equivalent" for pair in prepared.pairs)
    assert progress[0].devices_done == progress[0].pairs_prepared == 0
    assert progress[-1].devices_done == progress[-1].devices_total == 200
    assert progress[-1].pairs_prepared == 900


def test_filter_preserves_all_intersections_found_by_exhaustive_comparison():
    patterns = [
        "c",
        "c a",
        "c b",
        "d a",
        "c a b",
        "c INTEGER<1-100>",
        "c [ a ] b",
        "[ undo ] c INTEGER<1-100>",
        "{ c | d } INTEGER<1-100>",
        "c { a | b } *",
        "c [ a | b ] *",
        "c { a [ b ] } &<1-3>",
        "c { a | b } &<0-2> tail",
        "c { [ a ] } *",
        "c { [ a ] } &<1-2>",
        "c INTEGER<1-100> &<0-0> tail",
        "[ c ] [ a ]",
        "c [ INTEGER<1-100> ] [ INTEGER<1-100> ]",
        "c [ [ a ] b ] [ c ]",
        "[ undo ] { c | d } [ a | b ] *",
    ]
    parser = CommandLineParser({"commands": patterns})
    documents = [{"format": p.replace("INTEGER<1-100>", "<id>")} for p in patterns]
    matcher = FormatMatcher()
    index = CandidateIndex(parser.automaton.patterns)
    for document in Documentation(documents).documents():
        retained = {p.original for p in index.candidates(document.ast)}
        for pattern in patterns:
            relation = matcher.compare(document.format, pattern).relation
            if relation in {
                "equivalent",
                "document_subset",
                "device_subset",
                "overlap",
            }:
                assert pattern in retained


@pytest.mark.parametrize("size", [64, 65, 100])
def test_wide_choices_broaden_the_index_instead_of_losing_candidates(size):
    pattern = "c { " + " | ".join(f"option{i}" for i in range(size)) + " } tail"
    parser = CommandLineParser({"commands": [pattern]})
    index = CandidateIndex(parser.automaton.patterns)
    for i in range(size):
        document = next(Documentation([{"format": f"c option{i} tail"}]).documents())
        assert index.candidates(document.ast) == parser.automaton.patterns
    # The same bound must be safe when it is the document that contains a choice.
    document = next(Documentation([{"format": pattern}]).documents())
    parser = CommandLineParser({"commands": [f"c option{i} tail" for i in range(size)]})
    assert CandidateIndex(parser.automaton.patterns).candidates(document.ast) == (
        parser.automaton.patterns
    )
    assert len(PrefixCover().prefixes(document.ast)) <= 64


def test_nested_optional_prefixes_do_not_require_enumerating_all_routes():
    prefix = " ".join(f"[ option{i} ]" for i in range(30))
    document = next(Documentation([{"format": prefix + " c <id>"}]).documents())
    parser = CommandLineParser(
        {
            "commands": [
                "c INTEGER<1-100>",
                "option29 c INTEGER<1-100>",
                "option0 option29 c INTEGER<1-100>",
            ]
        }
    )
    assert CandidateIndex(parser.automaton.patterns).candidates(document.ast) == (
        parser.automaton.patterns
    )
    assert len(PrefixCover().prefixes(document.ast)) <= 64


def test_index_depth_is_a_conservative_cutoff():
    prefix = "a b c d e f g h "
    parser = CommandLineParser({"commands": [prefix + "left", prefix + "right"]})
    document = next(Documentation([{"format": prefix + "right"}]).documents())
    assert CandidateIndex(parser.automaton.patterns).candidates(document.ast) == (
        parser.automaton.patterns[1],
    )


def test_excluded_formats_create_no_artifact_or_runtime_entries():
    parser = CommandLineParser({"commands": ["a INTEGER<1-100>", "b INTEGER<1-100>"]})
    prepared = FormatMatcher().compile(parser, [{"format": "c <id>"}])
    assert prepared.pairs == ()
    assert all(device.status == "unmatched" for device in prepared.devices.values())
    assert FormatMatcher().compare("c <id>", "a INTEGER<1-100>").relation == "disjoint"


@pytest.mark.parametrize("prefix", ["", "shared "])
def test_diverged_languages_stop_after_difference_witnesses(prefix):
    wide = "{ " + " | ".join(f"option{i}" for i in range(24)) + " } *"
    comparison = FormatMatcher(MappingLimits(comparison_states=100)).compare(
        prefix + "documentation " + wide, prefix + "device " + wide
    )
    assert comparison.relation == ("prefix_only" if prefix else "disjoint")
