"""Runtime matcher cases that are not exercised by linear route expansion."""

from __future__ import annotations

import pytest

from vrp_parser import (
    CommandLineParser,
    ErrorCode,
    ErrorLine,
    ParsedCommand,
)


def _parsed(parser: CommandLineParser, line: str) -> ParsedCommand:
    result = parser.parse(line)
    assert isinstance(result, ParsedCommand)
    return result


@pytest.mark.parametrize(
    ("pattern", "accepted", "rejected"),
    [
        (
            "mode { alpha | beta }",
            ("mode alpha", "mode beta"),
            ("mode", "mode alpha beta"),
        ),
        (
            "mode [ alpha | beta ]",
            ("mode", "mode beta"),
            ("mode alpha beta",),
        ),
        (
            "mode { alpha | beta } *",
            ("mode alpha", "mode beta alpha"),
            ("mode", "mode alpha alpha"),
        ),
        (
            "mode [ alpha | beta ] *",
            ("mode", "mode beta alpha"),
            ("mode beta beta",),
        ),
    ],
)
def test_all_four_group_modes_have_distinct_runtime_cardinality(
    pattern: str,
    accepted: tuple[str, ...],
    rejected: tuple[str, ...],
) -> None:
    parser = CommandLineParser({"commands": [pattern]})

    assert all(
        isinstance(parser.parse(line), ParsedCommand)
        for line in accepted
    )
    assert all(
        isinstance(parser.parse(line), ErrorLine)
        for line in rejected
    )


def test_runtime_repeat_of_multitoken_group_tracks_each_choice() -> None:
    pattern = "path { via STRING<1-8> | direct } &<2-3>"
    parser = CommandLineParser({"commands": [pattern]})

    result = _parsed(parser, "path via one direct via two")
    too_few = parser.parse("path direct")
    too_many = parser.parse("path direct direct direct direct")

    assert result.primary_match.variation == (
        "path via STRING<1-8> direct via STRING<1-8>"
    )
    assert [value.raw for value in result.parameters] == ["one", "two"]
    assert [
        step.selected
        for step in result.primary_match.trace
        if step.kind == "choice"
    ] == [(0,), (1,), (0,)]
    repeat = next(
        step for step in result.primary_match.trace
        if step.kind == "repeat"
    )
    assert repeat.selected == (3,)
    assert isinstance(too_few, ErrorLine)
    assert isinstance(too_many, ErrorLine)


def test_nested_choice_is_matched_inside_symbolic_required_set() -> None:
    pattern = (
        "policy { { color { red | blue } } | "
        "metric INTEGER<1-10> } *"
    )
    result = _parsed(
        CommandLineParser({"commands": [pattern]}),
        "policy metric 5 color blue",
    )

    assert result.primary_match.variation == (
        "policy metric INTEGER<1-10> color blue"
    )
    assert [(value.raw, value.normalized) for value in result.parameters] == [
        ("5", 5)
    ]
    set_step = next(
        step for step in result.primary_match.trace if step.kind == "set"
    )
    assert set_step.selected == (1, 0)
    assert any(
        step.kind == "choice" and step.selected == (1,)
        for step in result.primary_match.trace
    )


def test_root_standalone_star_is_matched_as_a_literal() -> None:
    parser = CommandLineParser({"commands": ["match *"]})

    result = _parsed(parser, "match *")
    missing = parser.parse("match")

    assert result.primary_match.variation == "match *"
    assert result.parameters == ()
    assert isinstance(missing, ErrorLine)


def test_invalid_structured_value_blocks_generic_fallback() -> None:
    structured = "date YYYY-MM-DD"
    generic = "date STRING<1-20>"
    parser = CommandLineParser({"commands": [structured, generic]})

    invalid = parser.parse("date 2025-02-30")
    unrelated = _parsed(parser, "date release-label")

    assert isinstance(invalid, ErrorLine)
    assert invalid.error.code is ErrorCode.VALIDATION_ERROR
    assert invalid.error.candidate_patterns == (structured,)
    assert invalid.error.failures[0].type_id == "date-iso"
    assert unrelated.primary_match.original_pattern == generic
    assert unrelated.parameters[0].type_id == "string"
