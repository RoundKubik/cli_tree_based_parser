"""Explicit global catalogs are available in both scopes without renaming views."""

from copy import deepcopy

import pytest
from test_format_matcher_catalogs import catalog, command, document

from examples.semantic_candidates import mapped_candidates
from vrp_format_matcher import FormatError, FormatMatcher
from vrp_parser_automaton import (
    CommandLineParser,
    ConfigurationParser,
    ErrorLine,
    ParsedCommand,
    PatternDocumentError,
)


def grouped():
    result = catalog(
        "device",
        views={
            "root": [{"format": "enter", "switch_to_view": "child"}, command("local")],
            "child": [command("nested"), command("local")],
            "everywhere": [command("quit"), command("local")],
        },
    )
    result["global_view"] = "everywhere"
    return result


def test_global_commands_preserve_source_ids_and_local_alternatives():
    source = grouped()
    before = deepcopy(source)
    parser = CommandLineParser(source)
    assert parser.global_view == "everywhere"
    for line in ("quit", " quit", "  quit"):
        assert isinstance(parser.parse(line), ParsedCommand)
        assert parser.parse(line).primary_match.pattern_index == 4
    assert [m.pattern_index for m in parser.parse("local").matches] == [1, 5]
    assert [m.pattern_index for m in parser.parse(" local").matches] == [3, 5]
    assert isinstance(parser.parse("nested"), ErrorLine)
    assert isinstance(parser.parse(" enter"), ErrorLine)
    assert source == before


def test_global_commands_work_in_explicit_views_without_switching_to_global():
    parser = CommandLineParser(grouped(), context_mode="hierarchy")
    assert isinstance(parser.parse("quit", view="child"), ParsedCommand)
    parsed = ConfigurationParser(parser).parse("enter\n quit\n  nested\nlocal")
    assert [line.view for line in parsed.lines] == ["root", "child", "child", "root"]
    assert all(isinstance(line, ParsedCommand) for line in parsed.lines)


@pytest.mark.parametrize("value", [None, "absent", "root", [], 5])
def test_invalid_global_reference_is_rejected_by_parser_and_matcher(value):
    source = grouped()
    source["global_view"] = value
    with pytest.raises(PatternDocumentError, match="global_view"):
        CommandLineParser(source)
    with pytest.raises(FormatError, match="global_view"):
        FormatMatcher().compile_catalogs(
            source, catalog("documentation", [document("c")])
        )


def test_flat_catalog_does_not_accept_global_view():
    source = catalog("device", [command("quit")])
    source["global_view"] = "global"
    with pytest.raises(PatternDocumentError, match="global_view"):
        CommandLineParser(source)
    with pytest.raises(FormatError, match="global_view"):
        FormatMatcher().compile_catalogs(
            source, catalog("documentation", [document("quit")])
        )


def test_mapping_and_semantics_include_only_global_or_current_scope_records():
    device = grouped()
    docs = catalog(
        "documentation",
        views={
            "System view": [document("enter"), document("local"), document("quit")],
            "Child view": [document("nested"), document("local"), document("quit")],
            "All views": [document("local"), document("quit")],
        },
    )
    docs["global_view"] = "All views"
    saved = FormatMatcher().compile_catalogs(device, docs).to_dict()
    parser = CommandLineParser(device)
    for line, expected in [
        ("quit", {"System view", "All views"}),
        (" quit", {"Child view", "All views"}),
    ]:
        candidates = mapped_candidates(parser.parse(line), saved, docs)
        assert {c["source"]["view"] for c in candidates} == expected
    for line, expected in [
        ("local", {"System view", "All views"}),
        (" local", {"Child view", "All views"}),
    ]:
        candidates = mapped_candidates(parser.parse(line), saved, docs)
        assert {c["source"]["view"] for c in candidates} == expected
    assert saved["catalogs"]["device"]["global_view"] == "everywhere"


def test_global_name_is_not_special_without_explicit_reference():
    source = grouped()
    del source["global_view"]
    assert isinstance(CommandLineParser(source).parse("quit"), ErrorLine)
