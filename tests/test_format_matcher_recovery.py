"""Offline preparation yields one safe runtime catalog and contextual bindings."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_format_matcher_catalogs import catalog, command, document
from test_format_matcher_parameter_types import document as typed_document

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_format_matcher.preparation.pairs import PairPreparation
from vrp_format_matcher.preparation.pipeline import PreparationPipeline
from vrp_parser_automaton import CommandLineParser, ConfigurationParser, ParsedCommand


def example():
    device = catalog(
        "device",
        views={
            "v1": [command("rule INTEGER<1-9>"), command("shared")],
            "v0": [command("enter"), command("shared")],
        },
    )
    device["entry_view"] = "v0"
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("enter"), "switch_to_view": "Child"},
                document("shared"),
            ],
            "Child": [document("rule <id>"), document("shared")],
        },
    )
    return device, docs


def test_recovery_export_context_and_runtime_slots_end_to_end():
    device, docs = example()
    original = copy.deepcopy((device, docs))
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert (device, docs) == original
    assert prepared.unresolved == ()
    assert prepared.catalog["type"] == "grouped"
    assert prepared.catalog["views"]["v0"][0]["switch_to_view"] == "v1"
    assert prepared.catalog["vendor"] == device["vendor"]
    saved = json.loads(json.dumps(prepared.mapping.to_dict()))
    assert saved["hierarchy"]["resolved_views"] == {"v0": "Root", "v1": "Child"}
    assert {
        effect["kind"]
        for effects in saved["hierarchy"]["transitions"].values()
        for effect in effects
    } == {"switch", "stay"}
    parser = CommandLineParser(prepared.catalog, context_mode="hierarchy")
    report = ConfigurationParser(parser).parse("enter\n rule 2\n shared\nshared")
    assert all(isinstance(line, ParsedCommand) for line in report.lines)
    assert [line.view for line in report.lines] == ["v0", "v1", "v1", "v0"]
    for line in report.lines:
        match = line.primary_match
        record = saved["devices"][match.pattern_id]
        for pair in record["mappings"]:
            assert saved["documents"][pair["document_id"]]["source"]["view"] == (
                "Child" if line.view == "v1" else "Root"
            )
            assert {b["device"] for b in pair["bindings"]} == {
                parameter.slot_id for parameter in match.parameters
            }


def test_recovery_reuses_full_pairs_without_a_second_comparison_pass(monkeypatch):
    device, docs = example()
    original = PreparationPipeline.refine

    def forbidden(*args, **kwargs):
        raise AssertionError("full comparisons must not run again")

    def refine(self, mapping, scope):
        monkeypatch.setattr(PairPreparation, "prepared", forbidden)
        refined = original(self, mapping, scope)
        for identifier, match in refined.devices.items():
            assert all(
                any(
                    pair is previous
                    for previous in mapping.devices[identifier].mappings
                )
                for pair in match.mappings
            )
        return refined

    monkeypatch.setattr(PreparationPipeline, "refine", refine)
    assert (
        FormatMatcher(context_mode="hierarchy")
        .prepare_catalogs(device, docs)
        .catalog["type"]
        == "grouped"
    )


def test_partial_recovery_preserves_known_edges_and_marks_documented_unknown():
    device, docs = example()
    device["views"]["v0"].append(command("interface STRING<1-32>"))
    docs["views"]["Root"].append(
        {
            **typed_document("interface <name>", name="string"),
            "switch_to_view": {"status": "unresolved"},
        }
    )
    original = copy.deepcopy((device, docs))
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert (device, docs) == original
    assert prepared.catalog["type"] == "grouped"
    assert prepared.catalog["entry_view"] == "v0"
    assert list(prepared.catalog["views"]) == list(device["views"])
    root = prepared.catalog["views"]["v0"]
    assert root[0]["switch_to_view"] == "v1"
    assert root[2]["switch_to_view"] == {"status": "unresolved"}
    (identifier,) = prepared.unresolved
    saved = json.loads(json.dumps(prepared.mapping.to_dict()))
    assert saved["devices"][identifier]["source"] == {"view": "v0", "index": 2}
    assert saved["hierarchy"]["transitions"][identifier] == [
        {"mapping_index": 0, "kind": "unknown"}
    ]
    parser = CommandLineParser(
        json.loads(json.dumps(prepared.catalog)), context_mode="hierarchy"
    )
    report = ConfigurationParser(parser).parse(
        "enter\n rule 2\ninterface Port1\n shared\nenter\n rule 3"
    )
    assert not report.has_errors
    assert [line.view for line in report.lines] == ["v0", "v1", "v0", None, "v0", "v1"]
    match = report.lines[2].primary_match
    assert match.pattern_id == identifier
    assert {p.slot_id for p in match.parameters} == set(
        saved["devices"][identifier]["slots"]
    )

    # A manual correction uses the same view keys, formats, bindings and IDs.
    root[2]["switch_to_view"] = "v1"
    repaired = FormatMatcher(context_mode="hierarchy").prepare_catalogs(
        prepared.catalog, docs
    )
    assert not repaired.unresolved
    assert repaired.mapping.devices == prepared.mapping.devices
    assert repaired.catalog["views"]["v0"][2]["switch_to_view"] == "v1"


def test_explicit_unknown_survives_documentation_self_mapping_and_json_round_trip():
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("dynamic"), "switch_to_view": {"status": "unresolved"}},
                document("known-stay"),
            ]
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(docs, docs)
    assert prepared.catalog == docs
    assert len(prepared.unresolved) == 1
    restored = FormatMatcher(context_mode="hierarchy").prepare_catalogs(
        json.loads(json.dumps(prepared.catalog)), docs
    )
    assert restored.catalog == prepared.catalog
    assert restored.unresolved == prepared.unresolved
    assert restored.mapping.to_dict() == prepared.mapping.to_dict()


def test_unknown_effect_blocks_a_known_covering_transition_without_losing_pairs():
    device = catalog(
        "device",
        views={"root": [command("enter [ extra ]")], "child": [command("rule")]},
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("enter [ extra ]"), "switch_to_view": "Child"},
                {
                    **document("enter extra"),
                    "switch_to_view": {"status": "unresolved"},
                },
            ],
            "Child": [document("rule")],
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.catalog["views"]["root"][0]["switch_to_view"] == {
        "status": "unresolved"
    }
    (identifier,) = prepared.unresolved
    assert len(prepared.mapping.devices[identifier].mappings) == 2
    assert {
        effect["kind"]
        for effect in prepared.mapping.to_dict()["hierarchy"]["transitions"][identifier]
    } == {"switch", "unknown"}


def test_interface_targets_remain_unresolved_without_a_parameter_selection_rule():
    reference = typed_document(
        "interface { <interface-name> | <interface-type> <interface-number> }",
        **{
            "interface-name": "string",
            "interface-type": "string",
            "interface-number": "integer",
        },
    )
    device = catalog(
        "device",
        views={
            "root": [
                command("interface { STRING<1-64> | STRING<1-32> INTEGER<0-4095> }")
            ],
            "v1": [command("first-rule")],
            "v2": [command("second-rule")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**reference, "switch_to_view": "First"},
                {**reference, "switch_to_view": "Second"},
            ],
            "First": [document("first-rule")],
            "Second": [document("second-rule")],
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.catalog["views"]["root"][0]["switch_to_view"] == {
        "status": "unresolved"
    }
    (identifier,) = prepared.unresolved
    saved = prepared.mapping.to_dict()
    assert {
        effect["target"] for effect in saved["hierarchy"]["transitions"][identifier]
    } == {"First", "Second"}
    pairs = prepared.mapping.devices[identifier].mappings
    assert len(pairs) == 2
    assert all(
        {binding.document.name for binding in pair.bindings}
        == {"interface-name", "interface-type", "interface-number"}
        for pair in pairs
    )
    parser = CommandLineParser(prepared.catalog, context_mode="hierarchy")
    for line in ("interface Port1", "interface Port 1"):
        parsed = parser.parse(line)
        assert isinstance(parsed, ParsedCommand)
        assert parsed.primary_match.pattern_id == identifier
        assert parser.child_view(parsed) is None


@pytest.mark.parametrize("subset", [False, True])
def test_only_competitors_covering_the_documented_sample_block_uniqueness(subset):
    device = catalog(
        "device",
        views={
            "v0": [command("acl INTEGER<1-9>")],
            "v1": [command("rule INTEGER<1-9>"), command("shared")],
            "v2": [command("shared")]
            if subset
            else [command("rule INTEGER<1-9>"), command("shared")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("acl <id>"), "switch_to_view": "ACL"}],
            "ACL": [document("rule <id>"), document("shared")],
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.catalog["type"] == "grouped"
    assert prepared.catalog["entry_view"] == device["entry_view"]
    assert list(prepared.catalog["views"]) == list(device["views"])
    assert prepared.catalog["views"]["v0"][0]["switch_to_view"] == (
        "v1" if subset else {"status": "unresolved"}
    )
    assert prepared.mapping.hierarchy.targets["ACL"].status == (
        "resolved" if subset else "unresolved"
    )
    assert (next(iter(prepared.mapping.devices)) in prepared.unresolved) is not subset
    parser = CommandLineParser(prepared.catalog, context_mode="hierarchy")
    assert tuple(p.pattern_id for p in parser.automaton.patterns) == tuple(
        prepared.mapping.devices
    )
    parsed = ConfigurationParser(parser).parse("acl 1\n rule 2").lines[1]
    assert isinstance(parsed, ParsedCommand)
    assert parsed.view == ("v1" if subset else None)


def test_types_distinguish_views_and_names_have_no_effect():
    device = catalog(
        "device",
        views={
            "Root": [command("v4"), command("v6")],
            "misleading6": [command("address X.X.X.X")],
            "misleading4": [command("address X:X::X:X")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "opaque": [
                {**document("v4"), "switch_to_view": "x"},
                {**document("v6"), "switch_to_view": "y"},
            ],
            "x": [typed_document("address <ip>", ip="ipv4-address")],
            "y": [typed_document("address <ip>", ip="ipv6-address")],
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.unresolved == ()
    assert [c["switch_to_view"] for c in prepared.catalog["views"]["Root"]] == [
        "misleading6",
        "misleading4",
    ]


def test_existing_edges_disambiguate_identical_inventories_without_view_names():
    device = catalog(
        "documentation",
        views={
            "a": [
                {**document("first"), "switch_to_view": "b"},
                {**document("second"), "switch_to_view": "c"},
            ],
            "b": [document("same")],
            "c": [document("same")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("first"), "switch_to_view": "Y"},
                {**document("second"), "switch_to_view": "X"},
            ],
            "X": [document("same")],
            "Y": [document("same")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert result.catalog["type"] == "grouped" and not result.unresolved
    assert result.mapping.hierarchy.resolved_views == {"a": "Root", "b": "Y", "c": "X"}
    for identifier, match in result.mapping.devices.items():
        if result.mapping.device_catalog.entries[identifier].view == "b":
            assert [p.document_id for p in match.mappings] == ["doc:3"]


@pytest.mark.parametrize("format", ["enter <untyped>", "enter INTEGER<1-9> extra"])
def test_unknown_parameter_types_and_prefixes_do_not_establish_transitions(format):
    device = catalog("device", views={"v0": [command(format)], "v1": [command("c")]})
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter <id>"), "switch_to_view": "Child"}],
            "Child": [document("c")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert result.catalog["type"] == "grouped"
    assert result.catalog["views"]["v0"][0]["switch_to_view"] == {
        "status": "unresolved"
    }
    assert next(iter(result.mapping.devices)) in result.unresolved


def test_conflicting_partial_effect_cannot_be_promoted_to_whole_format_switch():
    device = catalog(
        "device",
        views={"v0": [command("enter [ extra ]")], "v1": [command("rule")]},
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("enter [ extra ]"), "switch_to_view": "Child"},
                document("enter extra"),
            ],
            "Child": [document("rule")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert result.catalog["type"] == "grouped"
    assert result.catalog["views"]["v0"][0]["switch_to_view"] == {
        "status": "unresolved"
    }
    assert next(iter(result.mapping.devices)) in result.unresolved
    assert len(next(iter(result.mapping.devices.values())).mappings) == 2


def test_expensive_unknown_comparison_cannot_prove_a_switch():
    device = catalog("device", views={"v0": [command("enter INTEGER<1-9>")]})
    docs = catalog(
        "documentation",
        views={"Root": [document("enter [ number ] <id>")]},
    )
    result = FormatMatcher(
        MappingLimits(analysis_steps=1), context_mode="hierarchy"
    ).prepare_catalogs(device, docs)
    assert result.catalog["type"] == "grouped" and result.unresolved


@pytest.mark.parametrize(
    "uncertain", ["rule <untyped>", "rule [ number ] INTEGER<1-9>"]
)
def test_uncertain_competitor_cannot_turn_another_candidate_into_unique(uncertain):
    device = catalog(
        "device",
        views={
            "root": [command("enter")],
            "known": [command("rule INTEGER<1-9>")],
            "uncertain": [command(uncertain)],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter"), "switch_to_view": "Child"}],
            "Child": [document("rule <id>")],
        },
    )
    result = FormatMatcher(
        MappingLimits(analysis_steps=1), context_mode="hierarchy"
    ).prepare_catalogs(device, docs)
    assert result.mapping.hierarchy.targets["Child"].status == "unresolved"
    assert result.catalog["type"] == "grouped" and result.unresolved


@pytest.mark.parametrize("brackets", [("[", "]"), ("{", "}")])
def test_reordered_sets_retain_bindings_and_runtime_slot_ids(brackets):
    opening, closing = brackets
    pattern = f"options {opening} a INTEGER<1-9> | b INTEGER<1-9> {closing} *"
    reference = f"options {opening} b <second> | a <first> {closing} *"
    device = catalog(
        "device", views={"root": [command("enter")], "child": [command(pattern)]}
    )
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "C"}],
            "C": [document(reference)],
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.unresolved == ()
    report = ConfigurationParser(
        CommandLineParser(prepared.catalog, context_mode="hierarchy")
    ).parse("enter\n options b 2 a 1")
    match = report.lines[1].primary_match
    (pair,) = prepared.mapping.devices[match.pattern_id].mappings
    assert pair.stage == "reordered"
    assert {b.device.slot_id for b in pair.bindings} == {
        parameter.slot_id for parameter in match.parameters
    }
    assert {b.document.name for b in pair.bindings} == {"first", "second"}


def test_removing_foreign_full_match_retries_prefix_in_the_resolved_view():
    device = catalog(
        "device",
        views={
            "root": [{**command("enter"), "switch_to_view": "child"}],
            "child": [{**command("set INTEGER<1-9> device"), "switch_to_view": None}],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter"), "switch_to_view": "Child"}],
            "Child": [document("set <id> documentation")],
            "Other": [document("set <id> device")],
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.catalog["type"] == "grouped"
    child = list(prepared.mapping.devices.values())[1]
    assert child.status == "partial"
    assert [(p.document_id, p.stage) for p in child.mappings] == [("doc:1", "prefix")]
    assert child.mappings[0].bindings


def test_conflicting_paths_do_not_choose_one_documentation_context():
    device = catalog(
        "device",
        views={
            "r": [
                {**command("a"), "switch_to_view": "c"},
                {**command("b"), "switch_to_view": "c"},
            ],
            "c": [command("same")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "R": [
                {**document("a"), "switch_to_view": "A"},
                {**document("b"), "switch_to_view": "B"},
            ],
            "A": [document("same")],
            "B": [document("same")],
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.mapping.hierarchy.resolved_views == {"r": "R"}
    assert prepared.catalog["type"] == "grouped"
    assert prepared.catalog["views"]["r"] == device["views"]["r"]
    assert prepared.catalog["views"]["c"][0]["switch_to_view"] == {
        "status": "unresolved"
    }
    assert len(list(prepared.mapping.devices.values())[2].mappings) == 2


def test_cycles_and_empty_views_are_resolved_from_existing_edges():
    device = catalog(
        "documentation",
        views={
            "r": [{**document("enter"), "switch_to_view": "c"}],
            "c": [
                {**document("back"), "switch_to_view": "r"},
                {**document("empty"), "switch_to_view": "e"},
            ],
            "e": [],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter"), "switch_to_view": "Child"}],
            "Child": [
                {**document("back"), "switch_to_view": "Root"},
                {**document("empty"), "switch_to_view": "Empty"},
            ],
            "Empty": [],
        },
    )
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.mapping.hierarchy.resolved_views == {
        "r": "Root",
        "c": "Child",
        "e": "Empty",
    }
    assert not prepared.unresolved


def test_cli_preparation_writes_separate_catalog_and_mapping(tmp_path):
    device, docs = example()
    paths = [
        tmp_path / name
        for name in ("device.json", "docs.json", "ready.json", "map.json")
    ]
    paths[0].write_text(json.dumps(device))
    paths[1].write_text(json.dumps(docs))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "vrp_format_matcher",
            "--context-mode",
            "hierarchy",
            "--patterns",
            str(paths[0]),
            "--documents",
            str(paths[1]),
            "--save-catalog",
            str(paths[2]),
            "--save",
            str(paths[3]),
            "--summary",
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert "RUNTIME CATALOG: grouped" in completed.stdout
    expected = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert json.loads(paths[2].read_text()) == expected.catalog
    assert json.loads(paths[3].read_text()) == expected.mapping.to_dict()
