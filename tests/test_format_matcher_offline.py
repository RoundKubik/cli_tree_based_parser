"""Offline parameter correspondences and realistic catalog fast paths."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_format_matcher.comparison.execution import ProgramExecution
from vrp_parser_automaton import CommandLineParser


def test_offline_mapping_does_not_read_or_serialize_predicates():
    document = {
        "format": "vlan <vlan-id>",
        "requires": object(),  # Not even JSON: matcher must not inspect metadata.
        "creates": [{"when": {"op": "unimplemented"}}],
    }
    parser = CommandLineParser({"commands": ["vlan INTEGER<1-4096>"]})
    prepared = FormatMatcher().compile(parser, [document])
    pair = prepared.pairs[0]
    assert pair.status == "equivalent"
    assert pair.binding_mode == "structural"
    assert len(pair.bindings) == 1
    binding = pair.bindings[0]
    assert binding.document.name == "vlan-id"
    assert binding.document.slot_id == binding.device.slot_id == "p:5"
    assert binding.device.declaration == "INTEGER<1-4096>"
    saved = asdict(prepared)
    assert "version" not in saved
    assert saved["pairs"][0]["bindings"][0]["document"]["name"] == "vlan-id"
    assert "creates" not in saved["pairs"][0]
    assert "requires" not in saved["pairs"][0]
    assert not hasattr(prepared, "evaluate")
    assert not hasattr(prepared, "from_json")


def test_same_ambiguous_structures_never_execute_language_product(monkeypatch):
    from vrp_parser_automaton.automata.building import AutomatonBuilder
    def forbidden(*args, **kwargs):
        raise AssertionError("identical source structures must not enumerate languages")

    monkeypatch.setattr(ProgramExecution, "frontier", forbidden)
    monkeypatch.setattr(AutomatonBuilder, "build", forbidden)
    documents = [
        "c { <first> | <second> } &<1-100000>",
        "c [ <first> ] [ <second> ]",
        "c { { <first> | <second> } * } &<1-100000>",
        "rule [ <id> ] { permit | deny } [ source <src> | target <dst> ] *",
    ]
    prepared = FormatMatcher(MappingLimits(analysis_steps=1)).compile_formats(
        documents, [{"format": f} for f in documents], target_syntax="document"
    )
    assert len(prepared.pairs) == len(documents)
    assert all(p.status == "equivalent" for p in prepared.pairs)
    for pair in prepared.pairs:
        assert pair.automaton is None
        assert all(link.document == link.device for link in pair.bindings)


def test_full_shape_index_handles_a_catalog_with_identical_eight_token_prefixes(
    monkeypatch,
):
    from vrp_format_matcher.preparation.candidates import CandidateIndex

    def forbidden(*args, **kwargs):
        raise AssertionError("shape matches must bypass broad prefix candidates")

    monkeypatch.setattr(CandidateIndex, "candidates", forbidden)
    prefix = "one two three four five six seven eight "
    devices = [prefix + f"feature{i} [ INTEGER<1-10> ]" for i in range(1100)]
    documents = [{"format": prefix + f"feature{i} [ <id> ]"} for i in range(900)]
    prepared = FormatMatcher().compile_formats(devices, documents)
    assert len(prepared.pairs) == 900
    assert all(
        p.status == "equivalent" and len(p.bindings) == 1 for p in prepared.pairs
    )


def test_best_and_all_modes_have_explicit_different_scopes():
    parser = CommandLineParser({"commands": ["c all", "c { all | other }"]})
    matcher = FormatMatcher()
    best = matcher.compile(parser, [{"format": "c all"}])
    all_pairs = matcher.compile(parser, [{"format": "c all"}], mode="all")
    assert [p.status for p in best.pairs] == ["equivalent"]
    assert [p.status for p in all_pairs.pairs] == ["equivalent", "document_subset"]
    fallback = matcher.compile_formats(["c { all | other }"], [{"format": "c all"}])
    assert fallback.pairs[0].status == "document_subset"


def test_duplicate_canonical_targets_are_all_preserved():
    prepared = FormatMatcher().compile_formats(
        ["c <x>", "c <y>", "c <x>"],
        [{"format": "c <id>"}],
        target_syntax="document",
    )
    assert len(prepared.pairs) == 3
    assert len(set(prepared.documents[0].pattern_ids)) == 3
    assert [p.bindings[0].device.name for p in prepared.pairs] == ["x", "y", "x"]


def test_equal_shaped_alternatives_are_paired_by_occurrence_without_overwriting():
    prepared = FormatMatcher().compile_formats(
        ["c { flag | <x> | <y> }"],
        [{"format": "c { <a> | <b> | flag }"}],
        target_syntax="document",
    )
    pair = prepared.pairs[0]
    assert not pair.comparison.structurally_identical
    assert [(b.document.name, b.device.name) for b in pair.bindings] == [
        ("a", "x"),
        ("b", "y"),
    ]


@pytest.mark.parametrize(
    "name", ["8021p-value", "ipv6-address/prefix-length", "1", "yyyy/mm/dd"]
)
def test_corpus_placeholder_names_are_preserved(name):
    source = f"c <{name}>"
    prepared = FormatMatcher().compile_formats(
        [source], [{"format": source}], target_syntax="document"
    )
    assert prepared.pairs[0].bindings[0].document.name == name


def test_document_target_does_not_reinterpret_date_keywords_as_device_parameters():
    source = "clock date-format { YYYY-MM-DD | MM-DD-YYYY }"
    pair = (
        FormatMatcher()
        .compile_formats([source], [{"format": source}], target_syntax="document")
        .pairs[0]
    )
    assert pair.status == "equivalent" and pair.bindings == ()


def test_unmatched_and_unknown_documents_have_explicit_statuses():
    result = FormatMatcher(MappingLimits(analysis_steps=1)).compile_formats(
        ["c { INTEGER<1-100> | all }"],
        [{"format": "c <id>"}, {"format": "unrelated <id>"}],
    )
    assert [d.status for d in result.documents] == ["unknown", "unmatched"]
    assert result.pairs[0].status == "unknown"
    assert "operation limit" in result.pairs[0].comparison.reason
    assert result.pairs[0].bindings == ()


def test_corpus_reader_reports_invalid_formats_and_benchmark_checks_all_slots(tmp_path):
    formats = [
        "port trunk allow-pass vlan "
        "{ { { <vlan-id1> [ to <vlan-id2> ] } &<1-40> } | all }",
        "rule [ <rule-id> ] { permit | deny } { <a> | <b> } &<1-32>",
        "clock date-format { YYYY-MM-DD | MM-DD-YYYY }",
        "set priority 8021p <8021p-value>",
        "display example <id>",
        "reset example [ a | ]",
    ]
    (tmp_path / "page.json").write_text(json.dumps({"CLIs": formats}))
    completed = subprocess.run(
        [
            sys.executable,
            "benchmark_format_matcher.py",
            "--corpus",
            str(tmp_path),
            "--skip-invalid",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(completed.stdout)
    assert report["verified_documents"] == 4
    assert report["document_statuses"] == {"matched": 4}
    assert "INVALID page:5" in completed.stderr
