"""Offline targets remain conservative and JSON reuses original pair scopes."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_format_matcher_catalogs import catalog, command, document
from test_format_matcher_hierarchy import grouped_catalogs
from test_format_matcher_result import restored_pair
from test_format_matcher_runtime_slots import follow_saved_mapping

from vrp_format_matcher import FormatMatcher
from vrp_format_matcher.hierarchy import HierarchyAnalysis
from vrp_format_matcher.preparation.pipeline import PreparationPipeline
from vrp_parser_automaton import CommandLineParser, ParsedCommand
from vrp_parser_automaton.patterns import PatternParser


@pytest.mark.parametrize("count", [0, 1, 2])
def test_zero_one_or_many_positive_candidates_do_not_establish_a_target(count):
    device = catalog(
        "device",
        views={
            "root": [command("enter")],
            **{f"v{i}": [command("rule INTEGER<1-9>")] for i in range(count)},
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter"), "switch_to_view": "Child"}],
            "Child": [document("rule <id>")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    hierarchy = result.hierarchy
    target = hierarchy.targets["Child"]
    assert target.status == "unresolved" and target.device_view is None
    assert {v.device_view for v in target.candidates} == {f"v{i}" for i in range(count)}
    assert all(
        v.documentation_view == "Child" and v.commands for v in target.candidates
    )
    assert hierarchy.targets["Root"].status == "resolved"
    assert hierarchy.targets["Root"].device_view == "root"


def test_entry_target_is_known_even_without_matching_commands_in_the_entry_views():
    device = catalog("device", views={"root": [], "child": [command("return")]})
    docs = catalog(
        "documentation",
        views={
            "Root": [],
            "Child": [{**document("return"), "switch_to_view": "Root"}],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    target = result.hierarchy.targets["Root"]
    assert target.status == "resolved" and target.device_view == "root"
    assert target.candidates == ()
    (link,) = result.hierarchy.evidence.command_links
    assert link.device.view == "child" and link.documentation.view == "Child"
    # Knowing the target does not certify the source-view correspondence.
    data = result.to_dict()["hierarchy"]
    assert data["targets"]["Root"] == {
        "status": "resolved",
        "device_view": "root",
        "candidates": [],
    }
    assert "status" not in data


def test_matching_names_and_prefixes_do_not_create_target_evidence():
    device = catalog(
        "device",
        views={
            "root": [command("enter")],
            "Child": [command("rule INTEGER<1-9> device")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter"), "switch_to_view": "Child"}],
            "Child": [document("rule <id> documentation")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    assert any(pair.stage == "prefix" and pair.bindings for pair in result.pairs)
    assert result.hierarchy.targets["Child"].candidates == ()


def test_known_incompatible_parameter_types_do_not_supply_a_target_candidate():
    device = catalog(
        "device",
        views={
            "root": [command("enter")],
            "child": [command("rule STRING<1-9>")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter"), "switch_to_view": "Child"}],
            "Child": [document("rule <id>")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    assert result.hierarchy.targets["Child"].candidates == ()


def test_resolution_reuses_original_pairs_and_view_links_without_comparisons(
    monkeypatch,
):
    device, docs = grouped_catalogs()
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)

    def forbidden(*args, **kwargs):
        raise AssertionError("resolving hierarchy must not parse or match again")

    monkeypatch.setattr(PreparationPipeline, "prepare", forbidden)
    monkeypatch.setattr(PatternParser, "parse", forbidden)
    hierarchy = HierarchyAnalysis(device, docs, result).resolve()
    assert hierarchy == result.hierarchy
    for target in hierarchy.targets.values():
        for candidate in target.candidates:
            assert any(candidate is link for link in hierarchy.evidence.view_links)
    for link in hierarchy.evidence.command_links:
        assert any(
            link.pair is pair for pair in result.devices[link.pair.pattern_id].mappings
        )


def test_json_references_recover_supporting_pairs_and_all_documented_effects():
    device, docs = grouped_catalogs()
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    data = json.loads(json.dumps(result.to_dict()))
    hierarchy = data["hierarchy"]
    # Hierarchy serialization adds no copies or modifications of pair data.
    assert {k: v for k, v in data.items() if k != "hierarchy"} == (
        replace(result, hierarchy=None).to_dict()
    )
    for original, view in zip(
        result.hierarchy.evidence.view_links, hierarchy["view_links"], strict=True
    ):
        references = []
        for pattern_id, indices in view["mappings"].items():
            for index in indices:
                pair = result.devices[pattern_id].mappings[index]
                references.append((pattern_id, pair.document_id))
                assert (
                    data["devices"][pattern_id]["source"]["view"] == view["device_view"]
                )
                assert (
                    data["documents"][pair.document_id]["source"]["view"]
                    == view["documentation_view"]
                )
        assert references == [
            (link.pair.pattern_id, link.pair.document_id) for link in original.commands
        ]
    for name, target in hierarchy["targets"].items():
        assert all(
            hierarchy["view_links"][index]["documentation_view"] == name
            for index in target["candidates"]
        )
    for pattern_id, effects in hierarchy["transitions"].items():
        for effect in effects:
            pair = data["devices"][pattern_id]["mappings"][effect["mapping_index"]]
            source = data["documents"][pair["document_id"]]["source"]
            record = docs["views"][source["view"]][source["index"]]
            if "switch_to_view" not in record:
                assert effect["kind"] == "unknown" and "target" not in effect
            elif record["switch_to_view"] is None:
                assert effect["kind"] == "stay" and "target" not in effect
            else:
                assert effect["kind"] == "switch"
                assert effect["target"] == record["switch_to_view"]
    assert result.to_dict() == data


def test_conflicting_effects_and_unknown_are_not_collapsed_into_one_transition():
    device = catalog("device", views={"root": [command("c")]})
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("c"), "switch_to_view": "A"},
                {**document("c"), "switch_to_view": "B"},
                {**document("c"), "switch_to_view": None},
                document("c"),
            ],
            "A": [],
            "B": [],
        },
    )
    data = (
        FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs).to_dict()
    )
    (effects,) = data["hierarchy"]["transitions"].values()
    assert effects == [
        {"mapping_index": 0, "kind": "switch", "target": "A"},
        {"mapping_index": 1, "kind": "switch", "target": "B"},
        {"mapping_index": 2, "kind": "stay"},
        {"mapping_index": 3, "kind": "unknown"},
    ]


@pytest.mark.parametrize("source", ["device", "documentation"])
def test_explicit_input_transitions_survive_without_documentation_matches(source):
    literal = command if source == "device" else document
    device = catalog(
        source,
        views={
            "root": [
                {**literal("enter"), "switch_to_view": "actual-child"},
                {**literal("stay"), "switch_to_view": None},
                literal("unknown"),
            ],
            "actual-child": [],
        },
    )
    docs = catalog("documentation", views={"Root": [document("unrelated")]})
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    enter, stay, unknown = result.devices
    assert result.pairs == ()
    assert result.hierarchy.declared_transitions == {enter: "actual-child", stay: None}
    data = result.to_dict()["hierarchy"]
    assert data["declared_transitions"] == {enter: "actual-child", stay: None}
    assert unknown not in data["declared_transitions"]
    assert data["transitions"] == {}


def test_explicit_device_switch_and_different_documented_effect_remain_separate():
    device = catalog(
        "device",
        views={
            "root": [{**command("enter"), "switch_to_view": "child"}],
            "child": [],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter"), "switch_to_view": None}],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    (identifier,) = result.devices
    data = result.to_dict()["hierarchy"]
    assert data["declared_transitions"][identifier] == "child"
    assert data["transitions"][identifier] == [{"mapping_index": 0, "kind": "stay"}]


@pytest.mark.parametrize("parameters", [False, True])
def test_saved_transition_uses_its_intersection_scope(parameters):
    first = "a INTEGER<1-9>" if parameters else "a"
    second = "b INTEGER<1-9>" if parameters else "b"
    doc_first = "a <id>" if parameters else "a"
    pattern = f"c [ {first} | {second} ] *"
    device = catalog("device", views={"root": [command(pattern)]})
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document(f"c {doc_first}"), "switch_to_view": "Child"}],
            "Child": [],
        },
    )
    data = (
        FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs).to_dict()
    )
    parser = CommandLineParser({"commands": [pattern]}, context_mode="hierarchy")
    single = "c a 1" if parameters else "c a"
    both = "c b 2 a 1" if parameters else "c b a"
    for line in (single, both):
        parsed = parser.parse(line)
        assert isinstance(parsed, ParsedCommand)
        pattern_id = parsed.primary_match.pattern_id
        (effect,) = data["hierarchy"]["transitions"][pattern_id]
        pair = data["devices"][pattern_id]["mappings"][effect["mapping_index"]]
        machine = restored_pair(data, pattern_id, pair)["automaton"]
        assert bool(follow_saved_mapping(machine, line, parsed.primary_match)) == (
            line == single
        )


def test_target_evidence_is_serialized_once_for_many_entry_commands():
    device = catalog(
        "device",
        views={
            "root": [command(f"enter{i}") for i in range(100)],
            "child": [command("rule INTEGER<1-9>")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document(f"enter{i}"), "switch_to_view": "Child"} for i in range(100)
            ],
            "Child": [document("rule <id>")],
        },
    )
    data = (
        FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs).to_dict()
    )
    hierarchy = data["hierarchy"]
    assert len(hierarchy["targets"]) == 2
    assert len(hierarchy["view_links"]) == 2
    assert len(hierarchy["targets"]["Child"]["candidates"]) == 1
    assert len(hierarchy["transitions"]) == 101
    encoded = json.dumps(hierarchy)
    assert "bindings" not in encoded and "automaton" not in encoded
    assert "slot_id" not in encoded and "device_format" not in encoded


@pytest.mark.parametrize(
    "device_grouped,doc_grouped", [(False, False), (False, True), (True, False)]
)
def test_flat_or_mixed_inputs_do_not_create_hierarchy(device_grouped, doc_grouped):
    device_records, doc_records = [command("c")], [document("c")]
    device = catalog(
        "device", device_records, {"root": device_records} if device_grouped else None
    )
    docs = catalog(
        "documentation", doc_records, {"Root": doc_records} if doc_grouped else None
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    assert result.hierarchy is None and "hierarchy" not in result.to_dict()


def test_manual_script_saves_the_hierarchy_with_existing_catalog_arguments(tmp_path):
    device, docs = grouped_catalogs()
    patterns = tmp_path / "device.json"
    documents = tmp_path / "docs.json"
    output = tmp_path / "mapping.json"
    patterns.write_text(json.dumps(device))
    documents.write_text(json.dumps(docs))
    completed = subprocess.run(
        [
            sys.executable,
            "manual_format_matcher_test.py",
            "--context-mode",
            "hierarchy",
            "--patterns",
            str(patterns),
            "--documents",
            str(documents),
            "--summary",
            "--save",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "HIERARCHY TARGETS:" in completed.stdout
    assert (
        json.loads(output.read_text())
        == FormatMatcher(context_mode="hierarchy")
        .compile_catalogs(device, docs)
        .to_dict()
    )
