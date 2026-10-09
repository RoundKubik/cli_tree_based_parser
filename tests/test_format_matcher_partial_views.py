"""Recover views from incomplete samples without inventing absent evidence."""

from copy import deepcopy

import pytest
from test_format_matcher_catalogs import catalog, command, document
from test_format_matcher_parameter_types import document as typed_document

from vrp_format_matcher import FormatError, FormatMatcher, MappingLimits
from vrp_parser_automaton import CommandLineParser, ConfigurationParser, ParsedCommand


@pytest.mark.parametrize("v4_mask", range(8))
@pytest.mark.parametrize("v6_mask", range(8))
def test_all_partial_samples_recover_only_views_with_distinguishing_types(
    v4_mask, v6_mask
):
    device = catalog(
        "device",
        views={
            "v0": [command("enter-four"), command("enter-six")],
            "v9": [
                command("enable"),
                command("acl INTEGER<1-99>"),
                command("source-ip X.X.X.X"),
                command("extra-four"),
            ],
            "v2": [
                command("enable"),
                command("acl INTEGER<1-99>"),
                command("source-ip X:X::X:X"),
                command("extra-six"),
            ],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "R": [
                {**document("enter-four"), "switch_to_view": "A"},
                {**document("enter-six"), "switch_to_view": "B"},
            ],
            "A": [],
            "B": [],
        },
    )
    for view, mask, address_type in (
        ("A", v4_mask, "ipv4-address"),
        ("B", v6_mask, "ipv6-address"),
    ):
        complete = [
            document("enable"),
            document("acl <number>"),
            typed_document("source-ip <address>", address=address_type),
        ]
        docs["views"][view] = [c for i, c in enumerate(complete) if mask & (1 << i)]
    original = deepcopy((device, docs))
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert (device, docs) == original
    expected = {"v0": "R"}
    for index, mask, target, reference in (
        (0, v4_mask, "v9", "A"),
        (1, v6_mask, "v2", "B"),
    ):
        transition = prepared.catalog["views"]["v0"][index]["switch_to_view"]
        if mask & 4:
            assert transition == target
            expected[target] = reference
        else:
            # Resolving the other view must not assign this view by elimination.
            assert transition == {"status": "unresolved"}
    assert prepared.mapping.hierarchy.resolved_views == expected
    parsed = ConfigurationParser(
        CommandLineParser(prepared.catalog, context_mode="hierarchy")
    ).parse("enter-four\n source-ip 192.0.2.1\nenter-six\n source-ip 2001:db8::1")
    assert all(isinstance(line, ParsedCommand) for line in parsed.lines)
    assert [line.view for line in parsed.lines] == [
        "v0",
        "v9" if v4_mask & 4 else None,
        "v0",
        "v2" if v6_mask & 4 else None,
    ]
    saved = prepared.mapping.to_dict()
    for line in parsed.lines:
        match = line.primary_match
        record = saved["devices"][match.pattern_id]
        if record["mappings"]:
            assert {p.slot_id for p in match.parameters} <= record["slots"].keys()


@pytest.mark.parametrize("copied", [False, True])
def test_declared_shared_commands_are_available_but_supply_no_view_identity(copied):
    device = catalog(
        "device",
        views={"r": [command("enter")], "c": [command("rule"), command("quit")]},
    )
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "C"}],
            "C": [document("rule")] + ([document("quit")] if copied else []),
            "g": [document("quit"), document("return")],
        },
    )
    docs["shared_views"] = ["g"]
    prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert prepared.mapping.hierarchy.resolved_views == {"r": "R", "c": "C"}
    assert not prepared.unresolved
    quit_match = list(prepared.mapping.devices.values())[-1]
    assert quit_match.mappings
    assert all(pair.document_format == "quit" for pair in quit_match.mappings)
    assert list(prepared.catalog["views"]) == ["r", "c"]


def test_shared_only_group_and_device_shared_scope_cannot_be_selected():
    device = catalog(
        "device",
        views={"r": [command("enter")], "c": [command("quit")], "g": [command("quit")]},
    )
    device["shared_views"] = ["g"]
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "C"}],
            "C": [document("quit")],
            "Common": [document("quit")],
        },
    )
    docs["shared_views"] = ["Common"]
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert result.mapping.hierarchy.resolved_views == {"r": "R"}
    assert result.catalog["views"]["r"][0]["switch_to_view"] == {"status": "unresolved"}


def test_shared_exclusion_uses_types_not_only_the_format_text():
    device = catalog(
        "device", views={"r": [command("enter")], "c": [command("rule INTEGER<1-9>")]}
    )
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "C"}],
            "C": [document("rule <id>")],
            "Common": [typed_document("rule <id>", id="string")],
        },
    )
    docs["shared_views"] = ["Common"]
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert result.catalog["views"]["r"][0]["switch_to_view"] == "c"


def test_view_named_all_views_is_ordinary_unless_explicitly_shared():
    device = catalog("device", views={"r": [command("enter")], "c": [command("rule")]})
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "All views"}],
            "All views": [document("rule")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert result.mapping.hierarchy.resolved_views == {"r": "R", "c": "All views"}


def test_several_documented_scopes_can_share_one_device_scope_without_losing_pairs():
    device = catalog(
        "device",
        views={
            "r": [command("enter-a"), command("enter-b")],
            "combined": [
                command("first"),
                command("second"),
                command("rule INTEGER<1-9>"),
            ],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "R": [
                {**document("enter-a"), "switch_to_view": "A"},
                {**document("enter-b"), "switch_to_view": "B"},
            ],
            "A": [document("first"), document("rule <a>")],
            "B": [document("second"), document("rule <b>")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert [c["switch_to_view"] for c in result.catalog["views"]["r"]] == [
        "combined",
        "combined",
    ]
    assert result.mapping.hierarchy.resolved_views == {"r": "R"}
    assert result.mapping.hierarchy.targets["A"].device_view == "combined"
    assert result.mapping.hierarchy.targets["B"].device_view == "combined"
    rule = list(result.mapping.devices.values())[-1]
    assert {p.document_format for p in rule.mappings} == {"rule <a>", "rule <b>"}
    assert {b.document.name for p in rule.mappings for b in p.bindings} == {"a", "b"}
    # A common, fully covered effect can be used; a missing effect in one scope cannot.
    assert "switch_to_view" not in result.catalog["views"]["combined"][-1]
    assert result.catalog["views"]["combined"][0]["switch_to_view"] == {
        "status": "unresolved"
    }


def test_distinct_device_groups_are_not_unioned_to_force_a_generic_target():
    device = catalog(
        "device",
        views={"r": [command("enter")], "a": [command("one")], "b": [command("two")]},
    )
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "Family"}],
            "Family": [document("one"), document("two")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert result.mapping.hierarchy.resolved_views == {"r": "R"}
    assert result.catalog["views"]["r"][0]["switch_to_view"] == {"status": "unresolved"}
    assert {
        v.device_view for v in result.mapping.hierarchy.targets["Family"].candidates
    } == {"a", "b"}


@pytest.mark.parametrize("shared", [None, "c", ["missing"], ["c", "c"], ["r"], [1]])
def test_invalid_shared_scopes_are_rejected(shared):
    device = catalog("device", views={"r": [command("enter")], "c": [command("rule")]})
    device["shared_views"] = shared
    docs = catalog("documentation", views={"R": [document("enter")]})
    with pytest.raises(FormatError, match="shared_views"):
        FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)


@pytest.mark.parametrize("entry", ["enter extra", "unrelated"])
def test_known_target_does_not_supply_missing_entry_command_evidence(entry):
    device = catalog(
        "device",
        views={"r": [command("enter [ extra ]")], "c": [command("rule")]},
    )
    docs = catalog(
        "documentation",
        views={
            "R": [{**document(entry), "switch_to_view": "C"}],
            "C": [document("rule")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    assert result.mapping.hierarchy.resolved_views == {"r": "R", "c": "C"}
    assert result.catalog["views"]["r"][0]["switch_to_view"] == {"status": "unresolved"}


def test_unknown_device_type_blocks_uniqueness_but_keeps_bindings():
    device = catalog(
        "device",
        views={
            "r": [command("enter")],
            "known": [command("rule INTEGER<1-9>")],
            "unknown": [command("rule <value>")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "C"}],
            "C": [document("rule <id>")],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    target = result.mapping.hierarchy.targets["C"]
    assert target.status == "unresolved"
    assert {c.device_view: c.coverage for c in target.candidates} == {
        "known": "covered",
        "unknown": "unknown",
    }
    unknown_match = list(result.mapping.devices.values())[-1]
    assert unknown_match.mappings[0].bindings[0].document.name == "id"
    assert unknown_match.mappings[0].bindings[0].device.slot_id == "p:5"
    assert result.catalog["views"]["r"][0]["switch_to_view"] == {"status": "unresolved"}


@pytest.mark.parametrize(
    "annotations",
    [None, [], [{"parameter_name": "id", "parameter_type": "unknown"}]],
)
def test_unknown_reference_type_keeps_the_format_without_proving_a_transition(
    annotations,
):
    device = catalog(
        "device", views={"r": [command("enter")], "c": [command("rule INTEGER<1-9>")]}
    )
    record = {"format": "rule <id>"}
    if annotations is not None:
        record["parameter_types"] = annotations
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "C"}],
            "C": [record],
        },
    )
    result = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, docs)
    (pair,) = list(result.mapping.devices.values())[-1].mappings
    assert pair.bindings[0].document.type_id == ("unknown" if annotations else None)
    assert pair.bindings[0].device.slot_id == pair.bindings[0].document.slot_id == "p:5"
    saved = result.mapping.to_dict()
    assert saved["hierarchy"]["view_links"][-1]["coverage"] == "unknown"
    assert result.mapping.hierarchy.resolved_views == {"r": "R"}
    assert result.catalog["views"]["r"][0]["switch_to_view"] == {"status": "unresolved"}


def test_unfinished_comparison_is_visible_without_completed_bindings():
    device = catalog(
        "device",
        views={"r": [command("enter")], "c": [command("rule [ a ] [ b ]")]},
    )
    docs = catalog(
        "documentation",
        views={
            "R": [{**document("enter"), "switch_to_view": "C"}],
            "C": [document("rule [ a b ]")],
        },
    )
    result = FormatMatcher(
        MappingLimits(analysis_steps=1), context_mode="hierarchy"
    ).prepare_catalogs(device, docs)
    (candidate,) = result.mapping.hierarchy.targets["C"].candidates
    assert candidate.coverage == "unknown"
    assert candidate.commands == ()
    assert result.catalog["views"]["r"][0]["switch_to_view"] == {"status": "unresolved"}
