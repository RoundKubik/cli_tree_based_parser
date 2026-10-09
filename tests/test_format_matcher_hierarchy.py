"""Hierarchy evidence is name-independent and retains each full match's scope."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from test_format_matcher_catalogs import catalog, command, document
from test_format_matcher_result import restored_pair
from test_format_matcher_runtime_slots import follow_saved_mapping

from vrp_format_matcher import FormatError, FormatMatcher, MappingLimits
from vrp_format_matcher.hierarchy import (
    DocumentTransition,
    HierarchyAnalysis,
    HierarchyEvidence,
    ViewLink,
)
from vrp_format_matcher.preparation.pipeline import PreparationPipeline
from vrp_parser_automaton import CommandLineParser, ParsedCommand
from vrp_parser_automaton.patterns import PatternParser


def grouped_catalogs(target_source="device"):
    rule = (
        command("rule INTEGER<1-9>")
        if target_source == "device"
        else document("rule <id>")
    )
    opening = (
        command("open INTEGER<1-9>")
        if target_source == "device"
        else document("open <id>")
    )
    literal = command if target_source == "device" else document
    devices = catalog(
        target_source,
        views={
            "acl4-advanced": [rule, literal("shared")],
            "system": [opening, literal("server"), literal("idle"), literal("mystery")],
            "acl6-advanced": [rule, literal("shared")],
            "unused-device": [],
        },
    )
    devices["entry_view"] = "system"
    docs = catalog(
        "documentation",
        views={
            "ACL view": [
                {**document("rule <id>"), "switch_to_view": None},
                document("shared"),
            ],
            "System view": [
                {**document("open [ number ] <id>"), "switch_to_view": "ACL view"},
                {**document("server"), "switch_to_view": "Empty target"},
                {**document("idle"), "switch_to_view": None},
                document("mystery"),
            ],
            "Other": [{**document("shared"), "switch_to_view": "ACL view"}],
            "Empty target": [],
        },
    )
    docs["entry_view"] = "System view"
    return devices, docs


def renamed_catalog(source, names):
    renamed = deepcopy(source)
    renamed["entry_view"] = names[source["entry_view"]]
    renamed["views"] = {
        names[view]: records for view, records in renamed["views"].items()
    }
    for records in renamed["views"].values():
        for record in records:
            if record.get("switch_to_view") is not None:
                record["switch_to_view"] = names[record["switch_to_view"]]
    return renamed


def renamed_evidence(evidence, device_names, doc_names):
    links = tuple(
        replace(
            link,
            device=replace(link.device, view=device_names[link.device.view]),
            documentation=replace(
                link.documentation, view=doc_names[link.documentation.view]
            ),
            transition=replace(
                link.transition,
                target_view=doc_names[link.transition.target_view]
                if link.transition.target_view is not None
                else None,
            ),
        )
        for link in evidence.command_links
    )
    by_pair = {(link.pair.pattern_id, link.pair.document_id): link for link in links}
    return HierarchyEvidence(
        links,
        tuple(
            ViewLink(
                device_names[view.device_view],
                doc_names[view.documentation_view],
                tuple(
                    by_pair[link.pair.pattern_id, link.pair.document_id]
                    for link in view.commands
                ),
            )
            for view in evidence.view_links
        ),
    )


@pytest.mark.parametrize("source", ["device", "documentation"])
def test_arbitrary_view_renaming_preserves_matching_and_hierarchy_evidence(source):
    device, docs = grouped_catalogs(source)
    device["views"]["system"][1]["switch_to_view"] = "acl4-advanced"
    device["views"]["system"][2]["switch_to_view"] = None
    device_names = dict(zip(device["views"], ["q9", "q2", "q7", "q1"], strict=True))
    doc_names = dict(zip(docs["views"], ["n1", "n8", "n3", "n2"], strict=True))
    renamed_device = renamed_catalog(device, device_names)
    renamed_docs = renamed_catalog(docs, doc_names)
    original = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    renamed = FormatMatcher(context_mode="hierarchy").compile_catalogs(
        renamed_device, renamed_docs
    )
    assert renamed.devices == original.devices

    expected_json = original.to_dict()
    for side, section, names in (
        ("device", "devices", device_names),
        ("documentation", "documents", doc_names),
    ):
        header = expected_json["catalogs"][side]
        header["entry_view"] = names[header["entry_view"]]
        for record in expected_json[section].values():
            record["source"]["view"] = names[record["source"]["view"]]
    hierarchy = expected_json["hierarchy"]
    hierarchy["entry_views"] = {
        "device": device_names[hierarchy["entry_views"]["device"]],
        "documentation": doc_names[hierarchy["entry_views"]["documentation"]],
    }
    for view in hierarchy["view_links"]:
        view["device_view"] = device_names[view["device_view"]]
        view["documentation_view"] = doc_names[view["documentation_view"]]
    hierarchy["targets"] = {
        doc_names[name]: target for name, target in hierarchy["targets"].items()
    }
    for target in hierarchy["targets"].values():
        if "device_view" in target:
            target["device_view"] = device_names[target["device_view"]]
    for effects in hierarchy["transitions"].values():
        for effect in effects:
            if "target" in effect:
                effect["target"] = doc_names[effect["target"]]
    hierarchy["declared_transitions"] = {
        identifier: device_names[target] if target is not None else None
        for identifier, target in hierarchy["declared_transitions"].items()
    }
    assert renamed.to_dict() == expected_json
    evidence = HierarchyAnalysis(device, docs, original).collect()
    assert HierarchyAnalysis(renamed_device, renamed_docs, renamed).collect() == (
        renamed_evidence(evidence, device_names, doc_names)
    )


def test_many_to_many_view_links_retain_original_pairs_without_new_matching(
    monkeypatch,
):
    device, docs = grouped_catalogs()
    mapping = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    before = mapping.to_dict()

    def forbidden(*args, **kwargs):
        raise AssertionError("hierarchy collection must reuse prepared pairs")

    monkeypatch.setattr(PreparationPipeline, "prepare", forbidden)
    monkeypatch.setattr(PatternParser, "parse", forbidden)
    evidence = HierarchyAnalysis(device, docs, mapping).collect()
    assert {(v.device_view, v.documentation_view) for v in evidence.view_links} == {
        ("acl4-advanced", "ACL view"),
        ("acl6-advanced", "ACL view"),
        ("acl4-advanced", "Other"),
        ("acl6-advanced", "Other"),
        ("system", "System view"),
    }
    assert len(evidence.command_links) == len(mapping.pairs) == 10
    for link, pair in zip(evidence.command_links, mapping.pairs, strict=True):
        assert link.pair is pair
        assert link.device == mapping.device_catalog.entries[pair.pattern_id]
        assert (
            link.documentation
            == mapping.documentation_catalog.entries[pair.document_id]
        )
    for view in evidence.view_links:
        assert all(
            any(link is command_link for command_link in evidence.command_links)
            for link in view.commands
        )
    assert mapping.to_dict() == before


def test_parameterless_transitions_keep_switch_stay_unknown_and_conflicting_targets():
    devices = catalog("device", views={"root": [command("enter")]})
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("enter"), "switch_to_view": "A"},
                {**document("enter"), "switch_to_view": "B"},
                {**document("enter"), "switch_to_view": None},
                document("enter"),
                {**document("enter"), "switch_to_view": "Root"},
            ],
            "A": [],
            "B": [],
        },
    )
    mapping = FormatMatcher(context_mode="hierarchy").compile_catalogs(devices, docs)
    evidence = HierarchyAnalysis(devices, docs, mapping).collect()
    assert [link.transition for link in evidence.command_links] == [
        DocumentTransition("switch", "A"),
        DocumentTransition("switch", "B"),
        DocumentTransition("stay"),
        DocumentTransition("unknown"),
        DocumentTransition("switch", "Root"),
    ]
    assert all(link.pair.bindings == () for link in evidence.command_links)
    assert [link.pair.document_id for link in evidence.command_links] == [
        "doc:0",
        "doc:1",
        "doc:2",
        "doc:3",
        "doc:4",
    ]
    assert len(evidence.view_links) == 1


@pytest.mark.parametrize("opening,closing", [("[", "]"), ("{", "}")])
@pytest.mark.parametrize("parameters", [False, True])
def test_intersection_transition_retains_scope_for_sets_and_source_slots(
    opening, closing, parameters
):
    a = "a INTEGER<1-9>" if parameters else "a"
    b = "b INTEGER<1-9>" if parameters else "b"
    doc_a = "a <first>" if parameters else "a"
    doc_b = "b <second>" if parameters else "b"
    pattern = f"c {opening} {a} | {b} {closing} *"
    devices = catalog("device", views={"root": [command(pattern)]})
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document(f"c {{ {doc_a} | {doc_b} }}"), "switch_to_view": "Child"}
            ],
            "Child": [],
        },
    )
    mapping = FormatMatcher(context_mode="hierarchy").compile_catalogs(devices, docs)
    (link,) = HierarchyAnalysis(devices, docs, mapping).collect().command_links
    assert link.transition == DocumentTransition("switch", "Child")
    assert link.pair is mapping.pairs[0]
    assert link.pair.stage == "intersection"
    assert link.pair.automaton is not None
    data = mapping.to_dict()
    saved = data["devices"][link.pair.pattern_id]["mappings"][0]
    machine = restored_pair(data, link.pair.pattern_id, saved)["automaton"]
    parser = CommandLineParser({"commands": [pattern]}, context_mode="hierarchy")
    single = "c a 1" if parameters else "c a"
    both = "c b 2 a 1" if parameters else "c b a"
    for line in (single, both):
        parsed = parser.parse(line)
        assert isinstance(parsed, ParsedCommand)
        assert bool(follow_saved_mapping(machine, line, parsed.primary_match)) == (
            line == single
        )


def test_reordered_pair_preserves_bindings_and_transition():
    devices = catalog(
        "device",
        views={
            "root": [command("c { b INTEGER<1-9> | a INTEGER<1-9> } *")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("c { a <a> | b <b> } *"), "switch_to_view": "Child"}],
            "Child": [],
        },
    )
    mapping = FormatMatcher(context_mode="hierarchy").compile_catalogs(devices, docs)
    (link,) = HierarchyAnalysis(devices, docs, mapping).collect().command_links
    assert link.pair.stage == "reordered" and len(link.pair.bindings) == 2
    assert link.pair.automaton is None
    assert link.transition == DocumentTransition("switch", "Child")


def test_prefix_unknown_and_unmatched_results_create_no_hierarchy_links():
    devices = catalog(
        "device",
        views={
            "root": [
                command("prefix INTEGER<1-9> device"),
                command("absent"),
                command("unknown [ INTEGER<1-9> ]"),
            ]
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("prefix <id> doc"), "switch_to_view": "Child"},
                {**document("unknown <id>"), "switch_to_view": "Child"},
            ],
            "Child": [],
        },
    )
    mapping = FormatMatcher(context_mode="hierarchy").compile_catalogs(devices, docs)
    prefix = next(p for p in mapping.pairs if p.stage == "prefix")
    assert prefix.bindings
    evidence = HierarchyAnalysis(devices, docs, mapping).collect()
    assert [link.pair.device_format for link in evidence.command_links] == [
        "unknown [ INTEGER<1-9> ]"
    ]
    limited = FormatMatcher(
        MappingLimits(analysis_steps=1), context_mode="hierarchy"
    ).compile_catalogs(devices, docs)
    assert any(p.status == "unknown" for p in limited.pairs)
    assert HierarchyAnalysis(devices, docs, limited).collect() == HierarchyEvidence(
        (), ()
    )


def test_finished_intersection_with_unfinished_inclusion_still_carries_transition():
    devices = catalog("device", views={"root": [command("c [ all ]")]})
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("c all"), "switch_to_view": "Child"}],
            "Child": [],
        },
    )
    mapping = FormatMatcher(
        MappingLimits(comparison_states=1), context_mode="hierarchy"
    ).compile_catalogs(devices, docs)
    (link,) = HierarchyAnalysis(devices, docs, mapping).collect().command_links
    assert link.pair.status == "matched" and link.pair.automaton is not None
    assert link.transition == DocumentTransition("switch", "Child")


def test_command_semantics_is_not_read_or_evaluated():
    device, docs = grouped_catalogs()
    for records in docs["views"].values():
        for record in records:
            record["creates"] = object()
            record["requires"] = object()
    mapping = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    evidence = HierarchyAnalysis(device, docs, mapping).collect()
    assert len(evidence.command_links) == 10


@pytest.mark.parametrize("side", ["device", "documentation"])
def test_changed_formats_cannot_attach_transitions_to_an_old_mapping(side):
    device, docs = grouped_catalogs()
    mapping = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, docs)
    target = device if side == "device" else docs
    next(iter(target["views"].values()))[0]["format"] = "different"
    with pytest.raises(FormatError, match="format differs"):
        HierarchyAnalysis(device, docs, mapping).collect()


def test_hierarchy_requires_catalog_locations_and_grouped_inputs():
    device, docs = grouped_catalogs()
    legacy = FormatMatcher(context_mode="hierarchy").compile_formats(
        ["c"], [{"format": "c"}]
    )
    with pytest.raises(FormatError, match="compile_catalogs"):
        HierarchyAnalysis(device, docs, legacy).collect()
    flat = catalog("device", [command("c")])
    mapping = FormatMatcher(context_mode="hierarchy").compile_catalogs(flat, docs)
    with pytest.raises(FormatError, match="two grouped"):
        HierarchyAnalysis(flat, docs, mapping).collect()
