"""Catalog adapters preserve source context and use the same matching pipeline."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from vrp_format_matcher import FormatError, FormatMatcher
from vrp_parser_automaton import CommandLineParser, ParsedCommand


def catalog(source, commands=None, views=None):
    result = {
        "schema_version": 1,
        "source": source,
        "vendor": "Huawei",
        "device": "Huawei VRP",
        "model_type": "Test",
        "software_version": "test-release",
    }
    if views is None:
        result.update(type="flat", commands=commands)
    else:
        result.update(type="grouped", views=views, entry_view=next(iter(views)))
    return result


def command(pattern):
    return {"format": pattern}


def document(pattern):
    return {
        "format": pattern,
        "parameter_types": [
            {"parameter_name": name, "parameter_type": "integer"}
            for name in dict.fromkeys(re.findall(r"(?<!&)<([^<>]+)>", pattern))
        ],
    }


@pytest.mark.parametrize("device_grouped", [False, True])
@pytest.mark.parametrize("doc_grouped", [False, True])
def test_layouts_preserve_bindings_slots_and_runtime_pattern_ids(
    device_grouped, doc_grouped
):
    formats = ["c INTEGER<1-100> [ to INTEGER<1-100> ]", "unmatched"]
    records = [command(pattern) for pattern in formats]
    docs = [document("c { <single> | <first> to <last> }")]
    device = catalog("device", records, {"system": records} if device_grouped else None)
    documentation = catalog(
        "documentation", docs, {"System view": docs} if doc_grouped else None
    )
    before = copy.deepcopy((device, documentation))
    matcher = FormatMatcher()
    prepared = matcher.compile_catalogs(device, documentation)
    baseline = matcher.compile_formats(formats, docs)
    assert prepared.devices == baseline.devices
    assert (device, documentation) == before

    data = json.loads(json.dumps(prepared.to_dict()))
    for index, (pattern_id, record) in enumerate(data["devices"].items()):
        assert record["source"] == {
            "view": "system" if device_grouped else None,
            "index": index,
        }
        assert prepared.device_catalog.entries[pattern_id].index == index
    assert data["documents"]["doc:0"]["source"] == {
        "view": "System view" if doc_grouped else None,
        "index": 0,
    }
    assert data["catalogs"]["device"]["model_type"] == "Test"
    assert data["catalogs"]["documentation"]["software_version"] == "test-release"

    parsed = CommandLineParser({"commands": formats}).parse("c 10 to 20")
    assert isinstance(parsed, ParsedCommand)
    saved = data["devices"][parsed.primary_match.pattern_id]
    assert {p.slot_id for p in parsed.parameters} <= saved["slots"].keys()
    assert "automaton_id" in saved["mappings"][0]
    assert "catalogs" not in baseline.to_dict()
    assert all("source" not in d for d in baseline.to_dict()["devices"].values())


def test_duplicate_formats_keep_distinct_view_and_record_locations():
    pattern = "acl INTEGER<2000-3999>"
    device = catalog(
        "device",
        views={
            "system": [command(pattern)],
            "grpc-server": [command(pattern)],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "System view": [document("acl <number>")],
            "GRPC server view": [document("acl <number>"), document("acl <number>")],
        },
    )
    data = FormatMatcher().compile_catalogs(device, docs).to_dict()
    assert len(data["devices"]) == 2
    assert len(data["documents"]) == 3
    assert [d["source"] for d in data["documents"].values()] == [
        {"view": "System view", "index": 0},
        {"view": "GRPC server view", "index": 0},
        {"view": "GRPC server view", "index": 1},
    ]
    # Both scopes keep their original locations and cannot cross the system boundary.
    for record in data["devices"].values():
        expected_ids = (
            ["doc:0"] if record["source"]["view"] == "system" else ["doc:1", "doc:2"]
        )
        assert [pair["document_id"] for pair in record["mappings"]] == expected_ids
        for pair in record["mappings"]:
            assert pair["bindings"] == [{"device": "p:4", "document": "p:4"}]
            assert "source" not in pair


def test_documentation_target_selects_document_grammar_automatically():
    docs = catalog("documentation", [document("clock { YYYY-MM-DD | MM-DD-YYYY }")])
    result = FormatMatcher().compile_catalogs(docs, docs)
    assert result.pairs[0].status == "equivalent"
    assert result.pairs[0].bindings == ()
    assert result.to_dict()["catalogs"]["device"]["source"] == "documentation"


def test_source_location_resolves_semantics_without_evaluating_or_copying_them():
    entry = document("vlan <id>")
    entry.update(
        creates=[{"kind": "future-rule"}], requires=object(), switch_to_view="VLAN view"
    )
    docs = catalog("documentation", views={"System view": [entry], "VLAN view": []})
    device = catalog("device", [command("vlan INTEGER<1-4095>")])
    result = FormatMatcher().compile_catalogs(device, docs)
    location = result.documentation_catalog.entries[result.pairs[0].document_id]
    assert docs["views"][location.view][location.index] is entry
    saved = json.dumps(result.to_dict())
    assert "future-rule" not in saved
    assert "requires" not in saved
    device["vendor"] = "Changed later"
    data = result.to_dict()
    data["catalogs"]["device"]["vendor"] = "Changed result"
    assert result.to_dict()["catalogs"]["device"]["vendor"] == "Huawei"


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"source": "unknown"},
        {"vendor": ""},
        {"model_type": None},
        {"type": "unknown"},
        {"views": {}},
        {"entry_view": "system"},
        {"commands": []},
        {"commands": ["c"]},
        {"commands": [{"format": "c\nd"}]},
        {"commands": [{"format": "c", "id": "external"}]},
        {"commands": [{"format": "c", "switch_to_view": None}]},
    ],
)
def test_invalid_flat_containers_are_rejected(change):
    device = catalog("device", [command("c")])
    device.update(change)
    with pytest.raises(FormatError):
        FormatMatcher().compile_catalogs(
            device, catalog("documentation", [document("c")])
        )


@pytest.mark.parametrize(
    "change",
    [
        {"entry_view": "missing"},
        {"commands": []},
        {"views": {"system": "c"}},
        {"views": {"system": [], "": []}},
        {"views": {"system": [{"format": "c", "switch_to_view": "missing"}]}},
    ],
)
def test_invalid_grouped_containers_are_rejected(change):
    device = catalog("device", views={"system": [command("c")]})
    device.update(change)
    with pytest.raises(FormatError):
        FormatMatcher(context_mode="hierarchy").compile_catalogs(
            device, catalog("documentation", [document("c")])
        )


@pytest.mark.parametrize("source", ["device", "documentation"])
def test_explicit_unknown_transition_is_not_an_authoritative_declared_edge(source):
    record = command("c") if source == "device" else document("c")
    record["switch_to_view"] = {"status": "unresolved"}
    data = catalog(source, views={"Root": [record]})
    docs = catalog(
        "documentation",
        views={
            "Reference": [{**document("c"), "switch_to_view": {"status": "unresolved"}}]
        },
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(data, docs)
    assert not result.hierarchy.declared_transitions
    assert result.hierarchy.evidence.command_links[0].transition.kind == "unknown"


@pytest.mark.parametrize("source", ["device", "documentation"])
@pytest.mark.parametrize(
    "transition", [{}, {"status": "resolved"}, {"status": "unresolved", "target": "x"}]
)
def test_malformed_transition_markers_are_rejected(source, transition):
    record = command("c") if source == "device" else document("c")
    record["switch_to_view"] = transition
    with pytest.raises(FormatError, match="switch_to_view"):
        FormatMatcher(context_mode="hierarchy").compile_catalogs(
            catalog(source, views={"root": [record]}),
            catalog("documentation", [document("c")]),
        )


def test_device_catalog_cannot_be_used_as_documentation():
    source = catalog("device", [command("c")])
    with pytest.raises(FormatError, match="documentation catalog source"):
        FormatMatcher().compile_catalogs(source, source)


def test_cli_accepts_catalog_files(tmp_path):
    device = tmp_path / "device.json"
    docs = tmp_path / "docs.json"
    output = tmp_path / "mapping.json"
    device.write_text(json.dumps(catalog("device", views={"system": [command("c")]})))
    docs.write_text(json.dumps(catalog("documentation", [document("c")])))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "vrp_format_matcher",
            "--patterns",
            str(device),
            "--documents",
            str(docs),
            "--summary",
            "--save",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(output.read_text())
    assert next(iter(data["devices"].values()))["source"]["view"] == "system"
    assert data["documents"]["doc:0"]["source"]["view"] is None
