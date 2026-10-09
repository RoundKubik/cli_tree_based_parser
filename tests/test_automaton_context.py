"""One runtime document supplies patterns and an already prepared hierarchy."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_format_matcher_catalogs import catalog, command, document

from vrp_format_matcher import FormatMatcher
from vrp_parser_automaton import (
    BlankLine,
    CommandLineParser,
    ConfigurationParser,
    ErrorLine,
    MatchStatus,
    ParsedCommand,
    PatternDocumentError,
    SeparatorLine,
    UnresolvedCommand,
)


def hierarchy():
    record = command
    return {
        "type": "grouped",
        "entry_view": "root",
        "views": {
            "root": [
                {**record("enter"), "switch_to_view": "child"},
                {**record("keep"), "switch_to_view": None},
                record("unknown"),
                record("root-only"),
                record("same"),
            ],
            "child": [
                {**record("nested"), "switch_to_view": "leaf"},
                {**record("child-only"), "switch_to_view": None},
                record("same"),
            ],
            "leaf": [record("leaf-only")],
        },
    }


def test_grouped_parsing_prefers_requested_view_and_marks_foreign_matches():
    parser = CommandLineParser(hierarchy(), context_mode="hierarchy")
    assert parser.parse("same").primary_match.pattern_index == 4
    assert parser.parse("same", view="child").primary_match.pattern_index == 7
    outside = parser.parse("child-only")
    assert isinstance(outside, UnresolvedCommand) and outside.view is None
    assert (
        outside.primary_match == parser.parse("child-only", view="child").primary_match
    )
    assert outside.context_issue.code == "outside_view"
    with pytest.raises(ValueError, match="unknown view"):
        parser.parse("same", view="missing")
    # Source context matters even if the two formats have identical token languages.
    flat = parser.parse_flat("same")
    assert flat.status == MatchStatus.AMBIGUOUS and flat.view is None
    assert len(flat.matches) == 2
    assert parser.child_view(flat) is None


@pytest.mark.parametrize("value", ["abc", "10"])
def test_valid_parameters_from_another_view_keep_captures_and_slot_ids(value):
    data = catalog(
        "device",
        views={
            "root": [command("set INTEGER<1-9>")],
            "child": [command("set STRING<1-9>")],
        },
    )
    parser = CommandLineParser(data, context_mode="hierarchy")
    result = parser.parse(f"set {value}")
    assert isinstance(result, UnresolvedCommand)
    assert result.context_issue.code == "outside_view"
    assert result.parameters[0].type_id == "string"
    allowed = parser.parse(f"set {value}", view="child")
    assert isinstance(allowed, ParsedCommand)
    assert allowed.parameters[0].slot_id == "p:4"
    assert result.primary_match == allowed.primary_match
    assert parser.child_view(result) is None


def test_nested_blocks_dedents_separators_blanks_and_new_sessions():
    parser = ConfigurationParser(
        CommandLineParser(hierarchy(), context_mode="hierarchy")
    )
    report = parser.parse(
        "enter\n child-only\n nested\n  leaf-only\n\n child-only\n#\nroot-only\n"
    )
    assert not report.has_errors
    assert [r.view for r in report.lines if isinstance(r, ParsedCommand)] == [
        "root",
        "child",
        "child",
        "leaf",
        "child",
        "root",
    ]
    assert isinstance(report.lines[4], BlankLine)
    assert isinstance(report.lines[6], SeparatorLine)
    assert report.summary.commands == 6 and report.summary.total == 8
    assert parser.parse("root-only").lines[0].view == "root"


def test_missing_transition_keeps_the_prepared_context():
    report = ConfigurationParser(
        CommandLineParser(hierarchy(), context_mode="hierarchy")
    ).parse("unknown\n same\nroot-only")
    assert not report.has_errors
    assert [r.view for r in report.lines] == ["root", "root", "root"]
    assert report.lines[1].status == MatchStatus.UNIQUE


def test_explicit_unknown_transition_only_disables_context_in_its_child_block():
    data = hierarchy()
    data["views"]["root"][2]["switch_to_view"] = {"status": "unresolved"}
    parser = CommandLineParser(json.loads(json.dumps(data)), context_mode="hierarchy")
    report = ConfigurationParser(parser).parse(
        "unknown\n same\n  leaf-only\nenter\n child-only\nroot-only"
    )
    assert not report.has_errors
    assert [line.view for line in report.lines] == [
        "root",
        None,
        None,
        "root",
        "child",
        "root",
    ]
    assert report.lines[1].status == MatchStatus.AMBIGUOUS
    assert parser.child_view(report.lines[0]) is None
    assert isinstance(parser.parse("child-only"), UnresolvedCommand)
    assert all(isinstance(line, UnresolvedCommand) for line in report.lines[1:3])


def test_manual_resolution_restores_context_without_a_mapping_file():
    data = hierarchy()
    data["views"]["root"][2]["switch_to_view"] = {"status": "unresolved"}
    unresolved = (
        CommandLineParser(data, context_mode="hierarchy").parse("unknown").primary_match
    )
    data["views"]["root"][2]["switch_to_view"] = "child"
    report = ConfigurationParser(
        CommandLineParser(data, context_mode="hierarchy")
    ).parse("unknown\n same")
    assert report.lines[0].primary_match == unresolved
    assert report.lines[1].view == "child"
    assert report.lines[1].status == MatchStatus.UNIQUE


def test_foreign_match_does_not_change_the_context_of_siblings():
    report = ConfigurationParser(
        CommandLineParser(hierarchy(), context_mode="hierarchy")
    ).parse("enter\n leaf-only\n  root-only\n child-only")
    assert isinstance(report.lines[1], UnresolvedCommand)
    assert report.lines[1].view is None
    assert report.lines[1].context_issue.code == "outside_view"
    assert isinstance(report.lines[2], UnresolvedCommand)
    assert report.lines[2].view is None
    assert report.lines[2].context_issue == report.lines[1].context_issue
    assert report.lines[3].view == "child"


def test_null_transition_keeps_context_with_one_space_per_nested_level():
    parser = ConfigurationParser(
        CommandLineParser(hierarchy(), context_mode="hierarchy")
    )
    report = parser.parse("keep\n enter\n  child-only\n#\nroot-only")
    assert not report.has_errors
    assert [r.view for r in report.lines if isinstance(r, ParsedCommand)] == [
        "root",
        "root",
        "child",
        "root",
    ]
    assert [r.indent for r in report.lines] == ["", " ", "  ", "", ""]
    assert report.lines[2].primary_match.parameters == ()


def test_conflicting_parses_do_not_choose_a_target_but_matching_stays_agree():
    data = hierarchy()
    data["views"]["root"] += [
        {"format": "enter", "switch_to_view": "leaf"},
        {"format": "keep"},
    ]
    parser = CommandLineParser(data, context_mode="hierarchy")
    assert parser.child_view(parser.parse("enter")) is None
    assert parser.child_view(parser.parse("keep")) == "root"
    report = ConfigurationParser(parser).parse("enter\n same\nroot-only")
    assert report.lines[1].view is None
    assert report.lines[1].status == MatchStatus.AMBIGUOUS
    assert report.lines[2].view == "root"


def test_explicit_flat_mode_keeps_legacy_line_handling():
    parser = ConfigurationParser(
        CommandLineParser(hierarchy(), context_mode="hierarchy"), contextual=False
    )
    report = parser.parse("root-only\n leaf-only\n#")
    assert all(r.view is None for r in report.lines)
    assert isinstance(report.lines[2], ErrorLine)
    with pytest.raises(ValueError, match="grouped catalog"):
        ConfigurationParser(
            CommandLineParser({"commands": ["same"]}, context_mode="hierarchy"),
            contextual=True,
        )


def test_external_mapping_keeps_global_ids_and_runtime_slot_locations():
    pattern = "c INTEGER<1-9> &<1-3>"
    record = command
    grouped = catalog(
        "device",
        views={
            "first": [record(pattern)],
            "second": [record(pattern)],
        },
    )
    flat = catalog("device", [record(pattern), record(pattern)])
    parser = CommandLineParser(grouped, context_mode="hierarchy")
    parsed = parser.parse(" c 1 2", view="second")
    flat_parsed = CommandLineParser(flat, context_mode="hierarchy").parse(" c 1 2")
    assert parsed.primary_match.source_view == "second"
    assert flat_parsed.matches[1].source_view is None
    assert replace(parsed.primary_match, source_view=None) == flat_parsed.matches[1]
    mapping = (
        FormatMatcher(context_mode="hierarchy")
        .compile_catalogs(
            grouped, catalog("documentation", [document("c <id> &<1-3>")])
        )
        .to_dict()
    )
    saved = mapping["devices"][parsed.primary_match.pattern_id]
    assert saved["source"] == {"view": "second", "index": 0}
    assert {p.slot_id for p in parsed.parameters} <= saved["slots"].keys()
    assert [p.raw for p in parsed.parameters] == ["1", "2"]
    assert parsed.parameters[0].iterations != parsed.parameters[1].iterations
    assert [parsed.raw[p.span.start : p.span.end] for p in parsed.parameters] == [
        "1",
        "2",
    ]


@pytest.mark.parametrize("source", ["device", "documentation", "external"])
def test_annotations_preserve_explicit_device_syntax_and_ignore_semantics(source):
    data = {
        "source": source,
        "metadata": {"ignored": True},
        "commands": [
            {
                "format": "set INTEGER<1-9> <id>",
                "parameter_types": [
                    {"parameter_name": "id", "parameter_type": "string"}
                ],
                "creates": object(),
                "requires": object(),
            }
        ],
    }
    parser = CommandLineParser(data, context_mode="hierarchy")
    result = parser.parse("set 5 label")
    assert isinstance(result, ParsedCommand)
    assert result.parameters[0].normalized == 5
    assert isinstance(parser.parse("set not-an-integer label"), ErrorLine)
    assert isinstance(parser.parse("set 10 label"), ErrorLine)


def test_json_only_adds_a_known_view_and_keeps_existing_null_parameter_values():
    data = catalog("device", [command("set INTEGER<1-9>")])
    legacy = ConfigurationParser(
        CommandLineParser({"commands": ["set INTEGER<1-9>"]}, context_mode="hierarchy")
    )
    flat = ConfigurationParser(CommandLineParser(data, context_mode="hierarchy"))
    assert (
        flat.parse("set 5\nwrong").to_dict() == legacy.parse("set 5\nwrong").to_dict()
    )
    report = (
        ConfigurationParser(CommandLineParser(hierarchy(), context_mode="hierarchy"))
        .parse("unknown\n same\n#\nwrong")
        .to_dict()
    )
    assert report["lines"][0]["view"] == "root"
    assert report["lines"][1]["view"] == "root"
    assert report["lines"][2] == {
        "line_number": 3,
        "raw": "#",
        "indent": "",
        "kind": "separator",
    }
    assert report["lines"][3]["view"] == "root"
    assert "view" not in report["lines"][0]["primary_match"]
    assert (
        not {"context", "candidates", "transition", "mode"} & report["lines"][0].keys()
    )
    assert "separators" not in report["summary"]
    json.dumps(report)


@pytest.mark.parametrize(
    "change",
    [
        {"entry_view": []},
        {"entry_view": "absent"},
        {"views": []},
        {"commands": []},
    ],
)
def test_malformed_grouped_catalogs_raise_document_errors(change):
    data = hierarchy()
    data.update(change)
    with pytest.raises(PatternDocumentError):
        CommandLineParser(data, context_mode="hierarchy")


@pytest.mark.parametrize(
    "target", ["absent", 7, [], {}, {"status": "unknown"}, {"status": "resolved"}]
)
def test_invalid_transition_references_are_rejected(target):
    data = hierarchy()
    data["views"]["root"][0]["switch_to_view"] = target
    with pytest.raises(PatternDocumentError):
        CommandLineParser(data, context_mode="hierarchy")


def test_only_patterns_and_hierarchy_are_needed_and_input_is_not_retained():
    data = hierarchy()
    parser = CommandLineParser(data, context_mode="hierarchy")
    data["views"]["root"][0]["switch_to_view"] = "leaf"
    data["views"]["root"][0]["format"] = "changed"
    data["views"]["child"].clear()
    report = ConfigurationParser(parser).parse("enter\n child-only")
    assert not report.has_errors
    assert [r.view for r in report.lines] == ["root", "child"]


def test_line_and_file_api_read_one_grouped_document(tmp_path):
    source = tmp_path / "patterns.json"
    source.write_text(json.dumps(hierarchy()), encoding="utf-8")
    parser = CommandLineParser.from_json_file(source, context_mode="hierarchy")
    assert parser.child_view(parser.parse("enter")) == "child"
    assert parser.child_view(parser.parse("child-only", view="child")) == "child"


def test_context_fallback_keeps_best_valid_formats_without_switching_views():
    data = hierarchy()
    data["views"]["child"].append({"format": "set INTEGER<1-9>"})
    data["views"]["leaf"].append({"format": "set <value>"})
    parser = CommandLineParser(data, context_mode="hierarchy")

    result = parser.parse("set 5")

    assert isinstance(result, UnresolvedCommand)
    assert result.view is None
    assert result.primary_match == parser.parse("set 5", view="child").primary_match
    assert result.alternative_matches == ()
    assert result.context_issue.message.isascii()
    assert "Parsing failed in view 'root'" in result.context_issue.message
    assert parser.child_view(result) is None
    assert result.matches == parser.parse_flat("set 5").matches


@pytest.mark.parametrize("line", ["set", "set 10", "set 5 extra", "absent"])
def test_context_diagnostics_require_a_complete_match_with_valid_parameters(line):
    data = hierarchy()
    data["views"]["child"].append({"format": "set INTEGER<1-9>"})
    result = (
        ConfigurationParser(CommandLineParser(data, context_mode="hierarchy"))
        .parse(line)
        .to_dict()
    )

    error = result["lines"][0]["error"]
    assert error["catalog_matches"] == []
    assert "No complete match with valid parameters" in error["message"]


@pytest.mark.parametrize(
    ("parent", "code"),
    [
        ("missing-parent", "parent_parse_error"),
        ("unknown", "unresolved_transition"),
        ("enter", "ambiguous_transition"),
    ],
)
def test_unknown_context_keeps_its_origin_through_nested_successes_and_errors(
    parent,
    code,
):
    data = hierarchy()
    data["views"]["root"][2]["switch_to_view"] = {"status": "unresolved"}
    data["views"]["root"].append({"format": "enter", "switch_to_view": "leaf"})
    parser = ConfigurationParser(CommandLineParser(data, context_mode="hierarchy"))

    report = parser.parse(f"{parent}\n child-only\n  absent\n   leaf-only\nroot-only")

    assert isinstance(report.lines[1], UnresolvedCommand)
    assert isinstance(report.lines[2], ErrorLine)
    assert isinstance(report.lines[3], UnresolvedCommand)
    issue = report.lines[1].context_issue
    assert issue.code == code and issue.source_line == 1
    assert issue.message.isascii()
    for line in report.lines[1:4]:
        assert line.context_issue == issue
        assert line.view is None
    assert report.lines[4].view == "root"
    assert report.lines[4].context_issue is None
    payload = report.to_dict()
    assert payload["lines"][2]["context_issue"]["source_line"] == 1
    assert "view" not in payload["lines"][2]
    assert "context_issue" not in payload["lines"][4]
    assert parser.parse("root-only").lines[0].context_issue is None


@pytest.mark.parametrize("grouped", [False, True])
def test_flat_parsing_omits_context_diagnostics_even_after_errors(grouped):
    data = hierarchy() if grouped else {"commands": ["root-only"]}
    report = ConfigurationParser(
        CommandLineParser(data, context_mode="hierarchy"), contextual=False
    ).parse("absent\n root-only")

    assert isinstance(report.lines[0], ErrorLine)
    assert report.lines[0].error.catalog_matches is None
    for line in report.to_dict()["lines"]:
        assert "context_issue" not in line and "view" not in line
        if line["kind"] == "error":
            assert "catalog_matches" not in line["error"]
