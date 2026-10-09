"""Keep useful syntax matches without treating a foreign view as confirmed."""

from dataclasses import asdict

import pytest

from vrp_parser_automaton import (
    CommandLineParser,
    ConfigurationParser,
    ErrorCode,
    ErrorLine,
    MatchStatus,
    ParsedCommand,
    UnresolvedCommand,
)


def catalog():
    return {
        "type": "grouped",
        "entry_view": "root",
        "views": {
            "root": [
                {"format": "enter", "switch_to_view": {"status": "unresolved"}},
                {"format": "root-only"},
                {"format": "set INTEGER<1-9>"},
            ],
            "a": [{"format": "set STRING<1-9>", "switch_to_view": "b"}],
            "b": [{"format": "set STRING<1-9>", "switch_to_view": "b"}],
            "generic": [{"format": "set <value>"}],
        },
    }


def test_equal_foreign_matches_keep_both_ids_and_original_indented_value_spans():
    parser = CommandLineParser(catalog(), context_mode="hierarchy")
    result = parser.parse("  set abc", line_number=7)
    assert isinstance(result, UnresolvedCommand)
    assert result.parsed and result.view is None
    assert result.status == MatchStatus.AMBIGUOUS
    assert result.primary_match == parser.parse("  set abc", view="a").primary_match
    assert result.alternative_matches == (
        parser.parse("  set abc", view="b").primary_match,
        parser.parse("  set abc", view="generic").primary_match,
    )
    for match in result.matches:
        (value,) = match.parameters
        assert value.slot_id == "p:4"
        assert result.raw[value.span.start : value.span.end] == "abc"
    assert result.context_issue.source_line == 7
    assert parser.child_view(result) is None


def test_unknown_parent_uses_valid_foreign_formats_despite_rejected_specific_format():
    parser = CommandLineParser(catalog(), context_mode="hierarchy")
    report = ConfigurationParser(parser).parse("enter\n set 10\n  set abc\nroot-only")
    assert type(report.lines[0]) is ParsedCommand
    for line in report.lines[1:3]:
        assert isinstance(line, UnresolvedCommand)
        assert line.context_issue.code == "unresolved_transition"
        assert line.context_issue.source_line == 1
        assert line.view is None
        assert len(line.matches) == 3
    assert type(report.lines[3]) is ParsedCommand and report.lines[3].view == "root"
    # Deliberately flat parsing still applies the original validation precedence.
    assert isinstance(parser.parse_flat("set 10"), ErrorLine)


def test_known_context_success_does_not_consider_foreign_patterns():
    parser = CommandLineParser(catalog(), context_mode="hierarchy")
    result = parser.parse("set 5")
    assert type(result) is ParsedCommand
    assert result.view == "root" and result.context_issue is None
    assert result.parameters[0].type_id == "integer"
    assert result.alternative_matches == ()


def test_report_counts_unresolved_as_recognized_but_distinguishes_it_from_errors():
    report = ConfigurationParser(
        CommandLineParser(catalog(), context_mode="hierarchy")
    ).parse("enter\n set abc\n missing\nroot-only")
    assert report.has_errors and report.has_unresolved
    assert report.summary.commands == 3
    assert report.summary.unresolved == 1
    assert report.summary.errors == 1
    assert report.summary.ambiguous == 1
    data = report.to_dict()
    line = data["lines"][1]
    assert line["kind"] == "unresolved_command"
    assert line["primary_match"]["parameters"][0]["slot_id"] == "p:4"
    assert len(line["alternative_matches"]) == 2
    assert line["primary_match"]["source_view"] == "a"
    assert [match["source_view"] for match in line["alternative_matches"]] == [
        "b",
        "generic",
    ]
    assert "view" not in line and "error" not in line
    assert data["summary"]["unresolved"] == 1


def test_flat_catalog_serialization_keeps_the_existing_result_shape():
    grouped = ConfigurationParser(
        CommandLineParser(catalog(), context_mode="hierarchy"), contextual=False
    )
    flat = ConfigurationParser(
        CommandLineParser(
            {"commands": ["set STRING<1-9>", "set STRING<1-9>"]},
            context_mode="hierarchy",
        )
    )
    for parser in (grouped, flat):
        report = parser.parse("set abc\nmissing")
        assert not report.has_unresolved
        assert type(report.lines[0]) is ParsedCommand
        assert "unresolved" not in report.to_dict()["summary"]
        assert "context_issue" not in report.to_dict()["lines"][0]
    assert asdict(flat.parse("set abc").lines[0])["kind"] == "command"
    assert [m.source_view for m in grouped.parse("set abc").lines[0].matches] == [
        "a",
        "b",
        "generic",
    ]
    flat_report = flat.parse("set abc")
    assert len(flat_report.lines[0].matches) == 2
    assert all(match.source_view is None for match in flat_report.lines[0].matches)
    line = flat_report.to_dict()["lines"][0]
    for match in (line["primary_match"], *line["alternative_matches"]):
        assert "source_view" not in match


def test_unique_foreign_entry_does_not_apply_its_declared_transition():
    data = catalog()
    data["views"]["a"] = [{"format": "foreign-entry", "switch_to_view": "b"}]
    parser = CommandLineParser(data, context_mode="hierarchy")
    report = ConfigurationParser(parser).parse("foreign-entry\n set abc\nroot-only")
    assert isinstance(report.lines[0], UnresolvedCommand)
    assert report.lines[0].status == MatchStatus.UNIQUE
    assert isinstance(report.lines[1], UnresolvedCommand)
    assert report.lines[1].view is None
    assert report.lines[1].context_issue == report.lines[0].context_issue
    assert type(report.lines[2]) is ParsedCommand and report.lines[2].view == "root"
    assert report.summary.unresolved == 2


@pytest.mark.parametrize("view", ["root", "selected"])
@pytest.mark.parametrize("prefix", ["set ", ""])
def test_current_view_generic_match_cannot_turn_validation_error_into_fallback(
    view, prefix
):
    views = {"root": [{"format": "root-only"}]}
    views[view] = [
        {"format": prefix + "INTEGER<1-9>"},
        {"format": prefix + "STRING<1-20>"},
    ]
    parser = CommandLineParser(
        {"type": "grouped", "entry_view": "root", "views": views},
        context_mode="hierarchy",
    )
    line = "  " + prefix + "10"
    result = parser.parse(line, view=view)
    flat = parser.parse_flat(line)

    assert isinstance(result, ErrorLine)
    assert result.view == view and result.context_issue is None
    assert result.error.code == ErrorCode.VALIDATION_ERROR
    assert result.error.failures == flat.error.failures
    assert result.error.candidate_patterns == flat.error.candidate_patterns
    assert "No complete match with valid parameters was found in other views" in (
        result.error.message
    )


@pytest.mark.parametrize("foreign_type", ["STRING<1-20>", "TEXT<1-40>"])
def test_current_view_matches_are_excluded_before_ranking_foreign_candidates(
    foreign_type,
):
    parser = CommandLineParser(
        {
            "type": "grouped",
            "entry_view": "root",
            "views": {
                "root": [
                    {"format": "set INTEGER<1-9>"},
                    {"format": "set STRING<1-20>"},
                ],
                "other": [{"format": "set " + foreign_type}],
            },
        },
        context_mode="hierarchy",
    )
    result = parser.parse("  set 10", line_number=7)
    expected = parser.parse("  set 10", view="other").primary_match

    assert isinstance(result, UnresolvedCommand)
    assert result.matches == (expected,)
    assert result.status == MatchStatus.UNIQUE
    assert result.context_issue.code == "outside_view"
    assert result.context_issue.source_line == 7
    assert result.parameters[0].slot_id == "p:4"
    assert parser.child_view(result) is None
    # Reusing the parser must not restrict explicit flat searches.
    assert isinstance(parser.parse_flat("set 10"), ErrorLine)
    assert parser.parse("set 5").primary_match.pattern_index == 0


def test_unknown_context_still_searches_formats_from_the_parent_view():
    parser = CommandLineParser(
        {
            "type": "grouped",
            "entry_view": "root",
            "views": {
                "root": [
                    {"format": "enter", "switch_to_view": {"status": "unresolved"}},
                    {"format": "set INTEGER<1-9>"},
                    {"format": "set STRING<1-20>"},
                ]
            },
        },
        context_mode="hierarchy",
    )
    report = ConfigurationParser(parser).parse("enter\n set 10\nset 10")
    unknown, known = report.lines[1:]

    assert isinstance(unknown, UnresolvedCommand)
    assert unknown.context_issue.code == "unresolved_transition"
    assert unknown.primary_match.pattern_index == 2
    assert isinstance(known, ErrorLine)
    assert known.error.code == ErrorCode.VALIDATION_ERROR
