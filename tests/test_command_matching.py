"""Black-box command-matching tests for the public parser facade."""

from __future__ import annotations

import pytest

from vrp_parser import (
    CommandLineParser,
    ErrorCode,
    ErrorLine,
    MatchStatus,
    ParsedCommand,
)


def _parsed(parser: CommandLineParser, line: str) -> ParsedCommand:
    result = parser.parse(line)
    assert isinstance(result, ParsedCommand)
    return result


def test_literal_matching_is_ascii_case_insensitive() -> None:
    parser = CommandLineParser({"commands": ["display clock"]})

    result = _parsed(parser, "DISPLAY CLOCK")

    assert result.status is MatchStatus.UNIQUE
    assert result.primary_match.original_pattern == "display clock"
    assert result.primary_match.variation == "display clock"
    assert result.parameters == ()


def test_required_choice_selects_one_branch_and_records_trace() -> None:
    pattern = "port { up | down }"
    result = _parsed(CommandLineParser({"commands": [pattern]}), "port down")

    assert result.primary_match.original_pattern == pattern
    assert result.primary_match.variation == "port down"
    assert [(step.kind, step.selected) for step in result.primary_match.trace] == [
        ("choice", (1,))
    ]


def test_optional_group_matches_present_and_absent_variations() -> None:
    parser = CommandLineParser({"commands": ["display interface [ brief ]"]})

    omitted = _parsed(parser, "display interface")
    selected = _parsed(parser, "display interface brief")

    assert omitted.primary_match.variation == "display interface"
    assert omitted.primary_match.trace[0].kind == "optional"
    assert omitted.primary_match.trace[0].selected == ()
    assert selected.primary_match.variation == "display interface brief"
    assert selected.primary_match.trace[0].kind == "optional"
    assert selected.primary_match.trace[0].selected == (0,)


def test_required_set_with_spaced_star_tracks_input_order() -> None:
    pattern = "features { alpha | beta | gamma } *"
    parser = CommandLineParser({"commands": [pattern]})

    result = _parsed(parser, "features gamma alpha")
    missing = parser.parse("features")

    assert result.primary_match.original_pattern == pattern
    assert result.primary_match.variation == "features gamma alpha"
    set_step = next(
        step for step in result.primary_match.trace if step.kind == "set"
    )
    assert set_step.selected == (2, 0)
    assert isinstance(missing, ErrorLine)


def test_optional_set_with_spaced_star_accepts_zero_or_more_distinct_branches() -> None:
    pattern = "filters [ source | destination ] *"
    parser = CommandLineParser({"commands": [pattern]})

    omitted = _parsed(parser, "filters")
    selected = _parsed(parser, "filters destination source")

    assert omitted.primary_match.variation == "filters"
    assert omitted.primary_match.trace[-1].kind == "set"
    assert omitted.primary_match.trace[-1].selected == ()
    assert selected.primary_match.variation == "filters destination source"
    assert selected.primary_match.trace[-1].selected == (1, 0)


def test_set_keeps_incomparable_assignments_that_change_remaining_branches() -> None:
    pattern = "cmd { ENUM{a,b} | STRING<1-10> } *"
    result = _parsed(CommandLineParser({"commands": [pattern]}), "cmd a b")

    assert result.status is MatchStatus.AMBIGUOUS
    assert {match.variation for match in result.matches} == {
        "cmd ENUM{a,b} STRING<1-10>",
        "cmd STRING<1-10> ENUM{a,b}",
    }


def test_set_can_use_generic_branch_before_valid_structured_branch() -> None:
    pattern = "cmd { YYYY-MM-DD | STRING<1-20> } *"
    result = _parsed(
        CommandLineParser({"commands": [pattern]}),
        "cmd 2024-02-30 2024-01-01",
    )

    assert result.primary_match.variation == (
        "cmd STRING<1-20> YYYY-MM-DD"
    )


def test_standalone_star_remains_a_literal_inside_an_alternative() -> None:
    pattern = "access-operation { { create | read } * | * }"
    result = _parsed(CommandLineParser({"commands": [pattern]}), "access-operation *")

    assert result.primary_match.original_pattern == pattern
    assert result.primary_match.variation == "access-operation *"
    assert any(
        step.kind == "choice" and step.selected == (1,)
        for step in result.primary_match.trace
    )


def test_nested_groups_preserve_each_choice_in_the_trace() -> None:
    pattern = "route { direct | [ vpn { red | blue } ] }"
    result = _parsed(CommandLineParser({"commands": [pattern]}), "route vpn blue")

    assert result.primary_match.variation == "route vpn blue"
    selections = [
        (step.kind, step.selected) for step in result.primary_match.trace
    ]
    assert ("choice", (1,)) in selections
    assert ("optional", (0,)) in selections
    assert selections.count(("choice", (1,))) == 2


def test_bounded_repeat_captures_every_parameter_and_selected_count() -> None:
    pattern = "peer STRING<1-10> &<1-3>"
    result = _parsed(
        CommandLineParser({"commands": [pattern]}),
        "peer one two three",
    )

    assert [value.raw for value in result.parameters] == [
        "one",
        "two",
        "three",
    ]
    assert result.primary_match.variation == (
        "peer STRING<1-10> STRING<1-10> STRING<1-10>"
    )
    repeat = next(
        step for step in result.primary_match.trace if step.kind == "repeat"
    )
    assert repeat.selected == (3,)


def test_required_set_and_repeat_do_not_count_empty_nested_choices() -> None:
    required_set = CommandLineParser(
        {"commands": ["cmd { [ a ] | [ b ] } *"]}
    )
    repeat = CommandLineParser({"commands": ["cmd [ x ] &<1-3>"]})

    assert isinstance(required_set.parse("cmd"), ErrorLine)
    assert isinstance(repeat.parse("cmd"), ErrorLine)
    assert isinstance(required_set.parse("cmd a"), ParsedCommand)
    assert isinstance(repeat.parse("cmd x x"), ParsedCommand)


def test_repeat_prunes_non_applicable_and_dominated_group_alternatives() -> None:
    pattern = (
        "community { STRING<3-11> | INTEGER<0-4294967295> | internet } "
        "&<1-200>"
    )
    parser = CommandLineParser({"commands": [pattern]})

    strings = _parsed(parser, "community " + " ".join(["abc"] * 20))
    integers = _parsed(parser, "community " + " ".join(["123"] * 20))

    assert len(strings.parameters) == 20
    assert all(item.type_id == "string" for item in strings.parameters)
    assert len(integers.parameters) == 20
    assert all(item.type_id == "integer" for item in integers.parameters)


def test_match_keeps_original_pattern_variation_parameters_and_trace() -> None:
    pattern = (
        "route { preference INTEGER<1-10> | tag HEX<0-FF> } [ additive ]"
    )
    result = _parsed(
        CommandLineParser({"commands": [pattern]}),
        "route tag ff additive",
    )
    match = result.primary_match

    assert match.original_pattern == pattern
    assert match.variation == "route tag HEX<0-FF> additive"
    assert len(match.variation_id) == 20
    assert [(value.raw, value.normalized) for value in match.parameters] == [
        ("ff", 255)
    ]
    assert [(step.kind, step.selected) for step in match.trace] == [
        ("choice", (1,)),
        ("optional", (0,)),
    ]


def test_enum_beats_string_but_search_backtracks_to_a_viable_string_route() -> None:
    patterns = [
        "action ENUM{run,stop,} now",
        "action STRING<1-10> now",
        "action STRING<1-10> later",
    ]
    parser = CommandLineParser({"commands": patterns})

    enum_winner = _parsed(parser, "action RUN now")
    backtracked = _parsed(parser, "action run later")

    assert enum_winner.primary_match.original_pattern == patterns[0]
    assert enum_winner.parameters[0].normalized == "run"
    assert any(
        step.kind == "enum" and step.selected == ("run",)
        for step in enum_winner.primary_match.trace
    )
    assert backtracked.primary_match.original_pattern == patterns[2]
    assert backtracked.parameters[0].type_id == "string"


@pytest.mark.parametrize(
    ("line", "expected_pattern", "type_id"),
    [
        ("value 2024-02-29", "value YYYY-MM-DD", "date-iso"),
        ("value 42", "value INTEGER<1-100>", "integer"),
        ("value label", "value STRING<1-20>", "string"),
    ],
)
def test_dispatch_prefers_structured_then_numeric_then_generic(
    line: str,
    expected_pattern: str,
    type_id: str,
) -> None:
    parser = CommandLineParser(
        {
            "commands": [
                "value YYYY-MM-DD",
                "value INTEGER<1-100>",
                "value STRING<1-20>",
            ]
        }
    )

    result = _parsed(parser, line)

    assert result.primary_match.original_pattern == expected_pattern
    assert result.parameters[0].type_id == type_id


def test_equivalent_match_uses_first_json_pattern_as_primary() -> None:
    first_pattern = "show { up | down }"
    second_pattern = "show up"
    first_order = _parsed(
        CommandLineParser({"commands": [first_pattern, second_pattern]}),
        "show up",
    )
    reversed_order = _parsed(
        CommandLineParser({"commands": [second_pattern, first_pattern]}),
        "show up",
    )

    assert first_order.status is MatchStatus.EQUIVALENT
    assert first_order.primary_match.original_pattern == first_pattern
    assert first_order.alternative_matches[0].original_pattern == second_pattern
    assert reversed_order.status is MatchStatus.EQUIVALENT
    assert reversed_order.primary_match.original_pattern == second_pattern
    assert reversed_order.alternative_matches[0].original_pattern == first_pattern


def test_first_source_alternative_is_stable_for_identical_variations() -> None:
    pattern = "show { up | up }"
    first = _parsed(CommandLineParser({"commands": [pattern]}), "show up")
    shifted = _parsed(
        CommandLineParser({"commands": ["unrelated", pattern]}),
        "show up",
    )

    assert first.primary_match.trace[-1].selected == (0,)
    assert shifted.primary_match.trace[-1].selected == (0,)
    assert first.primary_match.variation_id == shifted.primary_match.variation_id


def test_symbolic_fallback_keeps_source_order_for_ambiguous_variations() -> None:
    optionals = " ".join(
        f"[ option-{index}-a | option-{index}-b ]"
        for index in range(6)
    )
    pattern = (
        "cmd { STRING<1-10> STRING<1-10> | PASSWORDEX<1-10> } "
        f"STRING<1-10>&<0-1> {optionals}"
    )
    parser = CommandLineParser({"commands": [pattern]})

    result = _parsed(parser, "cmd a b")

    assert len(parser.command_graph.routes) == 1
    assert result.status is MatchStatus.AMBIGUOUS
    assert result.primary_match.variation == (
        "cmd STRING<1-10> STRING<1-10>"
    )
    assert result.primary_match.trace[0].selected == (0,)
    assert result.primary_match.trace[1].selected == (0,)


def test_symbolic_fallback_deduplicates_to_first_identical_derivation() -> None:
    optionals = " ".join(
        f"[ option-{index}-a | option-{index}-b ]"
        for index in range(6)
    )
    pattern = (
        "cmd { STRING<1-10> STRING<1-10> | STRING<1-10> } "
        f"STRING<1-10>&<0-1> {optionals}"
    )
    parser = CommandLineParser({"commands": [pattern]})

    result = _parsed(parser, "cmd a b")

    assert len(parser.command_graph.routes) == 1
    assert result.status is MatchStatus.UNIQUE
    assert result.primary_match.trace[0].selected == (0,)
    assert result.primary_match.trace[1].selected == (0,)


def test_symbolic_fallback_orders_nested_choices_parent_first() -> None:
    optionals = " ".join(
        f"[ option-{index}-a | option-{index}-b ]"
        for index in range(6)
    )
    pattern = (
        "cmd { { unreachable | STRING<1-10> } | "
        f"{{ STRING<1-10> | unreachable }} }} {optionals}"
    )
    parser = CommandLineParser({"commands": [pattern]})

    result = _parsed(parser, "cmd value")
    choices = [
        step.selected
        for step in result.primary_match.trace
        if step.kind == "choice"
    ]

    assert len(parser.command_graph.routes) == 1
    assert result.status is MatchStatus.UNIQUE
    # Trace is stored child-first, but source ordering must choose outer branch
    # zero before considering that branch's nested selection.
    assert choices[:2] == [(1,), (0,)]


def test_incomparable_numeric_matches_are_a_successful_ambiguity() -> None:
    patterns = ["value INTEGER<1-10>", "value HEX<1-A>"]
    result = _parsed(CommandLineParser({"commands": patterns}), "value 5")

    assert result.parsed
    assert result.status is MatchStatus.AMBIGUOUS
    assert [match.original_pattern for match in result.matches] == patterns
    assert [match.parameters[0].type_id for match in result.matches] == [
        "integer",
        "hex",
    ]


def test_incomparable_dispatch_orders_remain_ambiguous() -> None:
    patterns = [
        "pair INTEGER<1-10> STRING<1-10>",
        "pair STRING<1-10> INTEGER<1-10>",
    ]

    result = _parsed(CommandLineParser({"commands": patterns}), "pair 5 5")

    assert result.status is MatchStatus.AMBIGUOUS
    assert [match.original_pattern for match in result.matches] == patterns


def test_specific_validation_error_is_not_hidden_by_generic_fallback() -> None:
    specific = "preference INTEGER<1-15>"
    parser = CommandLineParser(
        {"commands": [specific, "preference STRING<1-20>"]}
    )

    result = parser.parse("preference 16")

    assert isinstance(result, ErrorLine)
    assert result.error.code is ErrorCode.VALIDATION_ERROR
    assert result.error.candidate_patterns == (specific,)
    assert result.error.failures[0].raw == "16"


def test_partly_non_applicable_specific_route_does_not_block_valid_fallback() -> None:
    parser = CommandLineParser(
        {
            "commands": [
                "cmd INTEGER<1-10> STRING<1-3>",
                "cmd STRING<1-10> STRING<1-10>",
            ]
        }
    )

    result = _parsed(parser, "cmd abc abcd")

    assert result.primary_match.original_pattern == (
        "cmd STRING<1-10> STRING<1-10>"
    )


def test_real_invalid_candidate_wins_over_non_applicable_enum_diagnostic() -> None:
    parser = CommandLineParser(
        {
            "commands": [
                "cmd ENUM{up,down}",
                "cmd INTEGER<1-10>",
            ]
        }
    )

    result = parser.parse("cmd 16")

    assert isinstance(result, ErrorLine)
    assert result.error.candidate_patterns == ("cmd INTEGER<1-10>",)
    assert result.error.failures[0].message == "value must be at most 10"


def test_unknown_command_returns_a_structured_error() -> None:
    result = CommandLineParser({"commands": ["display clock"]}).parse(
        "totally unknown"
    )

    assert isinstance(result, ErrorLine)
    assert not result.parsed
    assert result.error.code is ErrorCode.UNKNOWN_COMMAND
    assert result.error.position == 0


def test_standalone_text_accepts_only_a_leading_bang_after_indentation() -> None:
    parser = CommandLineParser({"commands": ["#", "TEXT<1-4096>"]})

    comment = _parsed(parser, " \t! generated by device")
    unknown = parser.parse(" \tnot a known command")

    assert comment.indent == " \t"
    assert comment.parameters[0].raw == "! generated by device"
    assert comment.parameters[0].span.start == len(" \t")
    assert isinstance(unknown, ErrorLine)
    assert unknown.error.code is ErrorCode.UNKNOWN_COMMAND
