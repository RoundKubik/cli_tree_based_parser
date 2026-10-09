"""Known entry views restrict every stage; other contexts retain global search."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict

import pytest
from test_format_matcher_catalogs import catalog, command, document
from test_format_matcher_parameter_types import document as typed_document
from test_format_matcher_result import restored_pair

from vrp_format_matcher import FormatMatcher
from vrp_format_matcher.preparation.indexes import DocumentationIndex
from vrp_format_matcher.preparation.pairs import PairPreparation
from vrp_parser_automaton import CommandLineParser, ParsedCommand


@pytest.mark.parametrize("target_source", ["device", "documentation"])
@pytest.mark.parametrize(
    "device_format,doc_format,line,stage",
    [
        ("acl INTEGER<1-100>", "acl <number>", "acl 10", "exact"),
        (
            "iface { INTEGER<1-100> INTEGER<1-100> | INTEGER<1-100> }",
            "iface { <name> | <kind> <name> }",
            "iface 10 20",
            "reordered",
        ),
        ("acl INTEGER<1-100>", "acl [ number ] <number>", "acl 10", "intersection"),
        (
            "acl INTEGER<1-100> device",
            "acl <number> documentation",
            "acl 10 device",
            "prefix",
        ),
    ],
)
def test_entry_view_is_filtered_before_stages_without_leaking_duplicate_results(
    target_source, device_format, doc_format, line, stage
):
    named_format = device_format.replace("INTEGER<1-100>", "<id>")
    entry = (
        command(device_format) if target_source == "device" else document(named_format)
    )
    # Neither entry view is first. Local indices are 0 in both views, while
    # device IDs and document IDs retain their global catalog positions.
    targets = catalog(target_source, views={"unresolved": [entry], "root-id": [entry]})
    targets["entry_view"] = "root-id"
    docs = catalog(
        "documentation",
        views={
            "Foreign view": [document(named_format)],
            "Initial context": [document(doc_format)],
        },
    )
    docs["entry_view"] = "Initial context"
    before = copy.deepcopy((targets, docs))
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(targets, docs)
    unresolved, root = result.devices.values()
    assert root.stage == stage
    assert [p.document_id for p in root.mappings] == ["doc:1"]
    assert unresolved.stage == (
        "mixed" if stage in {"reordered", "intersection"} else "exact"
    )
    assert [p.document_id for p in unresolved.mappings] == (
        ["doc:0"] if stage == "prefix" else ["doc:0", "doc:1"]
    )
    assert (targets, docs) == before

    (pair,) = root.mappings
    data = json.loads(json.dumps(result.to_dict()))
    saved = data["devices"][pair.pattern_id]
    assert saved["source"] == {"view": "root-id", "index": 0}
    assert data["documents"]["doc:1"]["source"] == {
        "view": "Initial context",
        "index": 0,
    }
    assert restored_pair(data, pair.pattern_id, saved["mappings"][0]) == json.loads(
        json.dumps(asdict(pair))
    )
    if target_source == "device":
        parser = CommandLineParser(
            {"commands": [device_format, device_format]}, context_mode="hierarchy"
        )
        parsed = parser.parse(line)
        assert isinstance(parsed, ParsedCommand)
        match = next(m for m in parsed.matches if m.pattern_id == pair.pattern_id)
        bound_slots = {b.device.slot_id for b in pair.bindings}
        parsed_slots = {value.slot_id for value in match.parameters}
        if stage == "prefix":
            assert bound_slots <= parsed_slots
        else:
            assert parsed_slots <= bound_slots


@pytest.mark.parametrize("root_first", [False, True])
def test_missing_entry_match_does_not_fall_back_to_foreign_views(root_first):
    entry = command("acl INTEGER<1-100>")
    views = {"root": [entry], "unresolved": [entry]}
    if not root_first:
        views = dict(reversed(list(views.items())))
    targets = catalog("device", views=views)
    targets["entry_view"] = "root"
    docs = catalog(
        "documentation",
        views={
            "Root": [document("unrelated <id>")],
            "Other": [document("acl <id>")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(targets, docs)
    for identifier, match in result.devices.items():
        view = result.device_catalog.entries[identifier].view
        if view == "root":
            assert match.status == "unmatched" and match.mappings == ()
        else:
            assert match.stage == "exact"
            assert [p.document_id for p in match.mappings] == ["doc:1"]


def test_empty_documentation_entry_view_is_a_closed_search_scope(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("an empty allowed scope must not search other views")

    monkeypatch.setattr(DocumentationIndex, "candidates", forbidden)
    targets = catalog("device", views={"root": [command("acl INTEGER<1-100>")]})
    docs = catalog(
        "documentation",
        views={
            "Root": [],
            "Other": [document("acl <id>")],
        },
    )
    (match,) = (
        FormatMatcher(context_mode="hierarchy")
        .compile_catalogs(targets, docs)
        .devices.values()
    )
    assert match.status == "unmatched" and match.mappings == ()


@pytest.mark.parametrize(
    "target_grouped,doc_grouped", [(False, False), (False, True), (True, False)]
)
def test_flat_or_mixed_catalogs_keep_global_search(target_grouped, doc_grouped):
    entries = [command("acl INTEGER<1-100>")]
    root_docs = [document("acl [ number ] <id>")]
    other_docs = [document("acl <id>")]
    targets = catalog("device", entries, {"root": entries} if target_grouped else None)
    docs = catalog(
        "documentation",
        root_docs + other_docs,
        {"Root": root_docs, "Other": other_docs} if doc_grouped else None,
    )
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(targets, docs)
    baseline = FormatMatcher(context_mode="hierarchy").compile_formats(
        [entries[0]["format"]], root_docs + other_docs
    )
    assert result.devices == baseline.devices
    assert [p.document_id for p in result.pairs] == ["doc:0", "doc:1"]
    assert [p.stage for p in result.pairs] == ["intersection", "exact"]


def test_foreign_views_are_excluded_before_pair_comparison(monkeypatch):
    calls = []
    original = PairPreparation.structural

    def observed(pair, **options):
        calls.append(pair.document.document_id)
        return original(pair, **options)

    monkeypatch.setattr(PairPreparation, "structural", observed)
    targets = catalog("device", views={"root": [command("acl INTEGER<1-100>")]})
    docs = catalog(
        "documentation",
        views={
            "Other": [document("acl <id>") for _ in range(250)],
            "Root": [document("acl <id>"), document("acl <id>")],
        },
    )
    docs["entry_view"] = "Root"
    result = FormatMatcher(context_mode="hierarchy").compile_catalogs(targets, docs)
    assert calls == ["doc:250"]  # The allowed duplicate reuses the pair computation.
    assert [p.document_id for p in result.pairs] == ["doc:250", "doc:251"]


def test_incompatible_entry_parameter_does_not_use_a_foreign_compatible_parameter():
    targets = catalog("device", views={"root": [command("acl INTEGER<1-100>")]})
    docs = catalog(
        "documentation",
        views={
            "Root": [typed_document("acl <id>", id="string")],
            "Other": [document("acl <id>")],
        },
    )
    (match,) = (
        FormatMatcher(context_mode="hierarchy")
        .compile_catalogs(targets, docs)
        .devices.values()
    )
    assert match.status == "unmatched" and match.mappings == ()
