"""Two independent search scopes preserve alternatives and offline source identity."""

from copy import deepcopy

import pytest
from test_format_matcher_catalogs import catalog, command, document

from vrp_format_matcher import FormatError, FormatMatcher
from vrp_parser_automaton import (
    BlankLine,
    CommandLineParser,
    ConfigurationParser,
    ErrorLine,
    MatchStatus,
    ParsedCommand,
    PatternDocumentError,
    SeparatorLine,
)


@pytest.mark.parametrize("target_source", ["device", "documentation"])
def test_partitioned_matching_preserves_ids_locations_and_bindings(target_source):
    record = (
        command("acl INTEGER<1-9999>")
        if target_source == "device"
        else document("acl <id>")
    )
    target = catalog(
        target_source,
        views={
            "arbitrary-root": [record],
            "interface-a": [record],
            "interface-b": [record],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [document("acl <created>")],
            "GRPC": [document("acl <reference>")],
            "Other": [document("acl <another>")],
        },
    )
    target["views"]["arbitrary-root"][0]["switch_to_view"] = "Absent generic view"
    before = deepcopy((target, docs))
    prepared = FormatMatcher().prepare_catalogs(target, docs)
    assert prepared.catalog == target and (target, docs) == before
    assert prepared.mapping.hierarchy is None and not prepared.unresolved
    saved = prepared.mapping.to_dict()
    assert "hierarchy" not in saved
    assert [
        [p["document_id"] for p in d["mappings"]] for d in saved["devices"].values()
    ] == [["doc:0"], ["doc:1", "doc:2"], ["doc:1", "doc:2"]]
    lines = (
        ConfigurationParser(CommandLineParser(prepared.catalog))
        .parse("acl 2018\n acl 2018")
        .lines
    )
    assert [len(line.matches) for line in lines] == [1, 2]
    for line in lines:
        for match in line.matches:
            device = saved["devices"][match.pattern_id]
            assert match.original_pattern == device["device_format"]
            assert match.source_view == device["source"]["view"]
            assert (
                device["source"]["view"] == list(target["views"])[match.pattern_index]
            )
            for pair in device["mappings"]:
                assert pair["bindings"][0]["device"] == match.parameters[0].slot_id


def test_partitioned_search_never_matches_the_opposite_scope():
    target = catalog(
        "device",
        views={"root": [command("only-nested")], "child": [command("only-root")]},
    )
    docs = catalog(
        "documentation",
        views={"R": [document("only-root")], "C": [document("only-nested")]},
    )
    mapping = FormatMatcher().compile_catalogs(target, docs)
    assert all(d.status == "unmatched" for d in mapping.devices.values())
    parser = CommandLineParser(target)
    assert isinstance(parser.parse("only-root"), ErrorLine)
    assert isinstance(parser.parse(" only-nested"), ErrorLine)


def test_first_valid_source_wins_and_less_specific_valid_matches_remain():
    data = catalog(
        "device",
        views={
            "root": [command("set all")],
            "generic": [command("set STRING<1-20>")],
            "specific": [command("set all"), command("set INTEGER<1-9>")],
        },
    )
    parser = CommandLineParser(data)
    line = parser.parse(" set all")
    assert type(line) is ParsedCommand and line.view is None
    assert line.context_issue is None and line.status == MatchStatus.AMBIGUOUS
    assert [m.pattern_index for m in line.matches] == [1, 2]
    assert line.primary_match.parameters[0].raw == "all"
    assert [m.pattern_index for m in parser.parse(" set 5").matches] == [1, 3]
    assert [m.pattern_index for m in parser.parse(" set 99").matches] == [1]
    # Explicit flat parsing retains the previous specificity rules.
    assert [m.pattern_index for m in parser.parse_flat("set all").matches] == [0, 2]


def test_indentation_alone_selects_the_scope_despite_parent_errors_and_switches():
    data = catalog(
        "device",
        views={
            "root": [
                {"format": "enter", "switch_to_view": "root"},
                command("ssh enable"),
            ],
            "a": [command("nested")],
            "b": [command("deep")],
        },
    )
    report = ConfigurationParser(CommandLineParser(data)).parse(
        "enter\n nested\n  deep\nmissing-parent\n\tnested\n#\nssh enable\n\nssh enable"
    )
    assert report.summary.errors == 1
    assert isinstance(report.lines[3], ErrorLine)
    assert all(type(report.lines[i]) is ParsedCommand for i in [0, 1, 2, 4, 6, 8])
    assert [report.lines[i].view for i in [0, 1, 2, 4, 6, 8]] == [
        "root",
        None,
        None,
        None,
        "root",
        "root",
    ]
    assert isinstance(report.lines[5], SeparatorLine)
    assert isinstance(report.lines[7], BlankLine)


def test_global_group_and_old_shared_annotations_cannot_cross_the_boundary():
    data = catalog(
        "device",
        views={
            "root": [command("enter")],
            "global": [command("quit")],
            "child": [command("leaf")],
        },
    )
    data["shared_views"] = ["global"]
    docs = catalog(
        "documentation",
        views={
            "R": [document("enter"), document("quit")],
            "G": [document("quit")],
            "C": [document("leaf"), document("quit")],
        },
    )
    docs["shared_views"] = ["G"]
    saved = FormatMatcher().compile_catalogs(data, docs).to_dict()
    shared = list(saved["devices"].values())[1]
    assert [p["document_id"] for p in shared["mappings"]] == ["doc:2", "doc:4"]
    parser = CommandLineParser(data)
    root, nested = parser.parse("quit"), parser.parse(" quit")
    assert isinstance(root, ErrorLine)
    assert type(nested) is ParsedCommand and nested.view is None


def test_system_keeps_its_own_ambiguity_and_parameter_validation():
    data = catalog(
        "device",
        views={
            "root": [command("value STRING<1-9>"), command("value INTEGER<1-9>")],
            "child": [command("number INTEGER<1-9>")],
        },
    )
    parser = CommandLineParser(data)
    assert len(parser.parse("value 5").matches) == 2
    assert isinstance(parser.parse(" number invalid"), ErrorLine)
    assert type(parser.parse(" number 5")) is ParsedCommand


def test_empty_non_system_scope_does_not_fall_back_to_system():
    data = catalog("device", views={"root": [command("only-root")], "empty": []})
    parser = CommandLineParser(data)
    assert type(parser.parse("only-root")) is ParsedCommand
    assert isinstance(parser.parse(" only-root"), ErrorLine)


def test_grouped_mode_ignores_transitions_and_hierarchy_mode_validates_them():
    data = catalog(
        "device", views={"root": [{"format": "enter", "switch_to_view": "missing"}]}
    )
    docs = catalog("documentation", views={"R": [document("enter")]})
    assert FormatMatcher().prepare_catalogs(data, docs).catalog == data
    assert type(CommandLineParser(data).parse("enter")) is ParsedCommand
    with pytest.raises(FormatError, match="switch_to_view"):
        FormatMatcher(context_mode="hierarchy").compile_catalogs(data, docs)
    with pytest.raises(PatternDocumentError, match="switch_to_view"):
        CommandLineParser(data, context_mode="hierarchy")
