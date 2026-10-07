"""One runtime document supplies patterns and an already prepared hierarchy."""

from __future__ import annotations

import json

import pytest
from test_format_matcher_catalogs import catalog, command, document

from vrp_format_matcher import FormatMatcher
from vrp_parser_automaton import (
    BlankLine,
    CommandLineParser,
    ConfigurationLayout,
    ConfigurationParser,
    ErrorLine,
    MatchStatus,
    ParsedCommand,
    PatternDocumentError,
    SeparatorLine,
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


def test_grouped_parsing_limits_recognition_and_suggestions_to_the_requested_view():
    parser = CommandLineParser(hierarchy())
    assert parser.parse("same").primary_match.pattern_index == 4
    assert parser.parse("same", view="child").primary_match.pattern_index == 7
    outside = parser.parse("child-only")
    assert isinstance(outside, ErrorLine) and outside.view == "root"
    assert "child-only" not in outside.error.suggestions
    with pytest.raises(ValueError, match="unknown view"):
        parser.parse("same", view="missing")
    # Source context matters even if the two formats have identical token languages.
    flat = parser.parse_flat("same")
    assert flat.status == MatchStatus.AMBIGUOUS and flat.view is None
    assert len(flat.matches) == 2
    assert parser.child_view(flat) is None


def test_parameters_from_another_view_do_not_change_validation_or_slot_ids():
    data = catalog(
        "device",
        views={
            "root": [command("set INTEGER<1-9>")],
            "child": [command("set STRING<1-9>")],
        },
    )
    parser = CommandLineParser(data)
    result = parser.parse("set abc")
    assert isinstance(result, ErrorLine)
    assert {f.type_id for f in result.error.failures} == {"integer"}
    allowed = parser.parse("set abc", view="child")
    assert isinstance(allowed, ParsedCommand)
    assert allowed.parameters[0].slot_id == "p:4"


def test_nested_blocks_dedents_separators_blanks_and_new_sessions():
    parser = ConfigurationParser(CommandLineParser(hierarchy()))
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
    report = ConfigurationParser(CommandLineParser(hierarchy())).parse(
        "unknown\n same\nroot-only"
    )
    assert not report.has_errors
    assert [r.view for r in report.lines] == ["root", "root", "root"]
    assert report.lines[1].status == MatchStatus.UNIQUE


def test_explicit_unknown_transition_only_disables_context_in_its_child_block():
    data = hierarchy()
    data["views"]["root"][2]["switch_to_view"] = {"status": "unresolved"}
    parser = CommandLineParser(json.loads(json.dumps(data)))
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
    assert isinstance(parser.parse("child-only"), ErrorLine)


def test_manual_resolution_restores_context_without_a_mapping_file():
    data = hierarchy()
    data["views"]["root"][2]["switch_to_view"] = {"status": "unresolved"}
    unresolved = CommandLineParser(data).parse("unknown").primary_match
    data["views"]["root"][2]["switch_to_view"] = "child"
    report = ConfigurationParser(CommandLineParser(data)).parse("unknown\n same")
    assert report.lines[0].primary_match == unresolved
    assert report.lines[1].view == "child"
    assert report.lines[1].status == MatchStatus.UNIQUE


def test_error_in_a_known_view_does_not_silently_retry_other_views():
    report = ConfigurationParser(CommandLineParser(hierarchy())).parse(
        "enter\n leaf-only\n  root-only\n child-only"
    )
    assert isinstance(report.lines[1], ErrorLine)
    assert report.lines[1].view == "child"
    assert report.lines[2].view is None
    assert report.lines[3].view == "child"


def test_null_transition_keeps_context_and_custom_layout_handles_tabs():
    parser = ConfigurationParser(
        CommandLineParser(hierarchy()),
        layout=ConfigurationLayout(separators=("!",), tab_width=4),
    )
    report = parser.parse("keep\n\tenter\n        child-only\n!\nroot-only")
    assert not report.has_errors
    assert [r.view for r in report.lines if isinstance(r, ParsedCommand)] == [
        "root",
        "root",
        "child",
        "root",
    ]
    assert report.lines[2].primary_match.parameters == ()


def test_conflicting_parses_do_not_choose_a_target_but_matching_stays_agree():
    data = hierarchy()
    data["views"]["root"] += [
        {"format": "enter", "switch_to_view": "leaf"},
        {"format": "keep"},
    ]
    parser = CommandLineParser(data)
    assert parser.child_view(parser.parse("enter")) is None
    assert parser.child_view(parser.parse("keep")) == "root"
    report = ConfigurationParser(parser).parse("enter\n same\nroot-only")
    assert report.lines[1].view is None
    assert report.lines[1].status == MatchStatus.AMBIGUOUS
    assert report.lines[2].view == "root"


def test_explicit_flat_mode_keeps_legacy_line_handling():
    parser = ConfigurationParser(CommandLineParser(hierarchy()), contextual=False)
    report = parser.parse("root-only\n leaf-only\n#")
    assert all(r.view is None for r in report.lines)
    assert isinstance(report.lines[2], ErrorLine)
    with pytest.raises(ValueError, match="grouped catalog"):
        ConfigurationParser(CommandLineParser({"commands": ["same"]}), contextual=True)


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
    parser = CommandLineParser(grouped)
    parsed = parser.parse(" c 1 2", view="second")
    flat_parsed = CommandLineParser(flat).parse(" c 1 2")
    assert parsed.primary_match == flat_parsed.matches[1]
    mapping = (
        FormatMatcher()
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
def test_annotations_never_change_pattern_syntax_or_validation(source):
    data = {
        "source": source,
        "metadata": {"ignored": True},
        "commands": [
            {
                "format": "set INTEGER<1-9>",
                "parameter_types": [
                    {"parameter_name": "id", "parameter_type": "string"}
                ],
                "creates": object(),
                "requires": object(),
            }
        ],
    }
    parser = CommandLineParser(data)
    result = parser.parse("set 5")
    assert isinstance(result, ParsedCommand)
    assert result.parameters[0].normalized == 5
    assert isinstance(parser.parse("set not-an-integer"), ErrorLine)


def test_json_only_adds_a_known_view_and_keeps_existing_null_parameter_values():
    data = catalog("device", [command("set INTEGER<1-9>")])
    legacy = ConfigurationParser(CommandLineParser({"commands": ["set INTEGER<1-9>"]}))
    flat = ConfigurationParser(CommandLineParser(data))
    assert (
        flat.parse("set 5\nwrong").to_dict() == legacy.parse("set 5\nwrong").to_dict()
    )
    report = (
        ConfigurationParser(CommandLineParser(hierarchy()))
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
        CommandLineParser(data)


@pytest.mark.parametrize(
    "target", ["absent", 7, [], {}, {"status": "unknown"}, {"status": "resolved"}]
)
def test_invalid_transition_references_are_rejected(target):
    data = hierarchy()
    data["views"]["root"][0]["switch_to_view"] = target
    with pytest.raises(PatternDocumentError):
        CommandLineParser(data)


def test_only_patterns_and_hierarchy_are_needed_and_input_is_not_retained():
    data = hierarchy()
    parser = CommandLineParser(data)
    data["views"]["root"][0]["switch_to_view"] = "leaf"
    data["views"]["root"][0]["format"] = "changed"
    data["views"]["child"].clear()
    report = ConfigurationParser(parser).parse("enter\n child-only")
    assert not report.has_errors
    assert [r.view for r in report.lines] == ["root", "child"]


def test_line_and_file_api_read_one_grouped_document(tmp_path):
    source = tmp_path / "patterns.json"
    source.write_text(json.dumps(hierarchy()), encoding="utf-8")
    parser = CommandLineParser.from_json_file(source)
    assert parser.child_view(parser.parse("enter")) == "child"
    assert parser.child_view(parser.parse("child-only", view="child")) == "child"
