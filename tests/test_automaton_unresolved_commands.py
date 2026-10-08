"""Keep useful syntax matches without treating a foreign view as confirmed."""

from dataclasses import asdict

from vrp_parser_automaton import (
    CommandLineParser,
    ConfigurationParser,
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
    parser = CommandLineParser(catalog())
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
    parser = CommandLineParser(catalog())
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
    parser = CommandLineParser(catalog())
    result = parser.parse("set 5")
    assert type(result) is ParsedCommand
    assert result.view == "root" and result.context_issue is None
    assert result.parameters[0].type_id == "integer"
    assert result.alternative_matches == ()


def test_report_counts_unresolved_as_recognized_but_distinguishes_it_from_errors():
    report = ConfigurationParser(CommandLineParser(catalog())).parse(
        "enter\n set abc\n missing\nroot-only"
    )
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
    assert "view" not in line and "error" not in line
    assert data["summary"]["unresolved"] == 1


def test_flat_catalog_serialization_keeps_the_existing_result_shape():
    grouped = ConfigurationParser(CommandLineParser(catalog()), contextual=False)
    flat = ConfigurationParser(CommandLineParser({"commands": ["set STRING<1-9>"]}))
    for parser in (grouped, flat):
        report = parser.parse("set abc\nmissing")
        assert not report.has_unresolved
        assert type(report.lines[0]) is ParsedCommand
        assert "unresolved" not in report.to_dict()["summary"]
        assert "context_issue" not in report.to_dict()["lines"][0]
    assert asdict(flat.parse("set abc").lines[0])["kind"] == "command"


def test_unique_foreign_entry_does_not_apply_its_declared_transition():
    data = catalog()
    data["views"]["a"] = [{"format": "foreign-entry", "switch_to_view": "b"}]
    parser = CommandLineParser(data)
    report = ConfigurationParser(parser).parse("foreign-entry\n set abc\nroot-only")
    assert isinstance(report.lines[0], UnresolvedCommand)
    assert report.lines[0].status == MatchStatus.UNIQUE
    assert isinstance(report.lines[1], UnresolvedCommand)
    assert report.lines[1].view is None
    assert report.lines[1].context_issue == report.lines[0].context_issue
    assert type(report.lines[2]) is ParsedCommand and report.lines[2].view == "root"
    assert report.summary.unresolved == 2
