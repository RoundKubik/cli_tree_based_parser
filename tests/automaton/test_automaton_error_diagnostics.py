"""User-facing runtime diagnostics and command suggestions."""

from __future__ import annotations

from vrp_parser_automaton import (
    CommandLineParser,
    ConfigurationParser,
    ErrorCode,
    ErrorLine,
)


def _error(
    patterns: list[str],
    line: str,
) -> ErrorLine:
    result = CommandLineParser({"commands": patterns}).parse(line)
    assert isinstance(result, ErrorLine)
    return result


def test_unknown_keyword_typo_returns_ranked_pattern_suggestions() -> None:
    result = _error(
        [
            "display clock",
            "display interface [ brief ]",
            "diagnose system",
        ],
        "dispaly clock",
    )

    assert result.error.code is ErrorCode.UNKNOWN_COMMAND
    assert result.error.suggestions[0] == "display clock"
    assert result.error.message.startswith(
        "Command 'dispaly clock' was not recognized. Did you mean:\n  1. display clock"
    )
    assert "Reason: No complete command pattern accepted the first token." in (
        result.error.message
    )


def test_suggestions_are_unique_stable_and_limited_to_five() -> None:
    patterns = [
        "display item0",
        "display item0",
        "display item1",
        "display item2",
        "display item3",
        "display item4",
        "display item5",
        "display item6",
    ]

    result = _error(patterns, "display itemx")

    assert result.error.suggestions == (
        "display item0",
        "display item1",
        "display item2",
        "display item3",
        "display item4",
    )
    assert result.error.message.count("\n  ") == 5


def test_unrelated_command_explains_that_no_suggestion_was_found() -> None:
    result = _error(
        ["display clock", "interface STRING<1-63>"],
        "totally unknown",
    )

    assert result.error.code is ErrorCode.UNKNOWN_COMMAND
    assert result.error.suggestions == ()
    assert result.error.message == (
        "Command 'totally unknown' was not recognized. "
        "No complete command pattern accepted the first token. "
        "No similar literal command patterns were found."
    )


def test_symbolic_literal_group_can_produce_a_suggestion() -> None:
    pattern = "{ show | inspect } status"

    result = _error([pattern], "shwo status")

    assert result.error.suggestions == (pattern,)


def test_parameter_first_pattern_is_not_a_suggestion() -> None:
    parameter_first = "STRING<1-20> activate"

    result = _error(
        [parameter_first, "display clock"],
        "router activate extra",
    )

    assert result.error.code is ErrorCode.SYNTAX_ERROR
    assert result.error.suggestions == ()
    assert parameter_first not in result.error.message
    assert "an applicable parameter-led pattern matched farther" in result.error.message


def test_non_applicable_parameter_route_does_not_hide_keyword_typo() -> None:
    result = _error(
        [
            "INTEGER<1-10> STRING<1-20> done",
            "display clock",
        ],
        "dispaly clock extra",
    )

    assert result.error.suggestions == ("display clock",)


def test_shared_non_root_keyword_is_not_enough_for_a_suggestion() -> None:
    result = _error(
        ["display clock", "delete clock"],
        "totally clock",
    )

    assert result.error.suggestions == ()


def test_suffix_similarity_filters_unrelated_commands_with_same_root() -> None:
    result = _error(
        [
            "display clock",
            "display class",
            "display vlan",
            "display alpha",
        ],
        "display alpah",
    )

    assert result.error.suggestions == ("display alpha",)


def test_exact_noisy_suffix_does_not_hide_closer_fuzzy_command() -> None:
    closer = "display clock status"
    result = _error(
        [closer, "display unrelated foo"],
        "display clok sttus foo",
    )

    assert result.error.suggestions[0] == closer


def test_optional_parameter_can_be_skipped_for_a_literal_led_suggestion() -> None:
    pattern = "[ STRING<1-20> ] display clock"

    result = _error([pattern], "display clok")

    assert result.error.suggestions == (pattern,)


def test_root_text_fallback_is_never_suggested_for_unknown_text() -> None:
    result = _error(
        ["TEXT<1-4096>", "display clock"],
        "totally unknown",
    )

    assert result.error.code is ErrorCode.UNKNOWN_COMMAND
    assert result.error.suggestions == ()
    assert "TEXT<1-4096>" not in result.error.message


def test_syntax_error_reports_absolute_column_and_expected_elements() -> None:
    result = _error(["display clock"], "  display clok")

    assert result.error.code is ErrorCode.SYNTAX_ERROR
    assert result.error.position == len("  display ")
    assert result.error.expected[0].description == "'clock'"
    assert "Parsing stopped at column 11; expected 'clock'." in (result.error.message)
    assert result.error.suggestions == ("display clock",)


def test_validation_error_preserves_machine_readable_reason_details() -> None:
    pattern = "preference INTEGER<1-15>"

    result = _error([pattern], "preference 16")

    assert result.error.code is ErrorCode.VALIDATION_ERROR
    assert result.error.suggestions == ()
    assert result.error.message.startswith(
        f"The command structure matched pattern {pattern!r}, but "
        "1 parameter value failed validation"
    )
    failure = result.error.failures[0]
    assert failure.reason_code == "above_maximum"
    assert failure.expected == "<= 15"
    assert failure.actual == "16"
    assert "value '16' is invalid for INTEGER<1-15>" in result.error.message


def test_not_applicable_parameter_has_a_structured_validation_reason() -> None:
    result = _error(["value INTEGER<1-10>"], "value abc")

    failure = result.error.failures[0]
    assert failure.reason_code == "not_applicable"
    assert failure.expected == "INTEGER<1-10>"
    assert failure.actual == "abc"


def test_new_error_details_are_serialized_by_configuration_parser() -> None:
    parser = CommandLineParser({"commands": ["display clock", "value INTEGER<1-10>"]})

    report = ConfigurationParser(parser).parse("dispaly clock\nvalue 11\n")
    payload = report.to_dict()

    unknown_error = payload["lines"][0]["error"]
    validation_failure = payload["lines"][1]["error"]["failures"][0]
    assert unknown_error["suggestions"] == ["display clock"]
    assert validation_failure["reason_code"] == "above_maximum"
    assert validation_failure["expected"] == "<= 10"
    assert validation_failure["actual"] == "11"
