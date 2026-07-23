from __future__ import annotations

from vrp_parser import CommandLineParser, ErrorLine, MatchStatus, ParsedCommand
from vrp_parser.patterns import Parameter


def test_common_literal_and_parameter_prefixes_are_shared() -> None:
    patterns = [
        "interface STRING<1-63>",
        (
            "interface { STRING<1-63> | "
            "ENUM{Eth-trunk,Vlanif,Vbdif} STRING<1-63> }"
        ),
    ]
    parser = CommandLineParser({"commands": patterns})
    root = parser.command_graph.root

    interface = root.literal_edges["interface"]
    string_edges = [
        edge
        for edge in interface.target.expression_edges
        if isinstance(edge.step.expression, Parameter)
        and edge.step.expression.source == "STRING<1-63>"
    ]

    assert len(string_edges) == 1
    assert len(string_edges[0].route_ids) == 2


def test_route_ownership_prevents_cross_pattern_paths() -> None:
    parser = CommandLineParser(
        {
            "commands": [
                "command one left",
                "command two right",
            ]
        }
    )

    assert isinstance(parser.parse("command one left"), ParsedCommand)
    assert isinstance(parser.parse("command two right"), ParsedCommand)
    assert isinstance(parser.parse("command one right"), ErrorLine)
    assert isinstance(parser.parse("command two left"), ErrorLine)


def test_duplicate_json_entries_remain_separate_sources() -> None:
    pattern = "display clock"
    result = CommandLineParser({"commands": [pattern, pattern]}).parse(pattern)

    assert isinstance(result, ParsedCommand)
    assert result.primary_match.pattern_index == 0
    assert result.alternative_matches[0].pattern_index == 1


def test_merged_literal_variation_is_independent_of_pattern_order() -> None:
    upper = "SHOW UP"
    lower = "show up"

    first = CommandLineParser({"commands": [upper, lower]}).parse(lower)
    reversed_result = CommandLineParser(
        {"commands": [lower, upper]}
    ).parse(lower)

    assert isinstance(first, ParsedCommand)
    assert isinstance(reversed_result, ParsedCommand)
    assert first.status is MatchStatus.EQUIVALENT
    assert reversed_result.status is MatchStatus.EQUIVALENT
    assert [match.variation for match in first.matches] == [lower, lower]
    assert [match.variation for match in reversed_result.matches] == [
        lower,
        lower,
    ]
    first_by_pattern = {
        match.original_pattern: match.variation_id for match in first.matches
    }
    reversed_by_pattern = {
        match.original_pattern: match.variation_id
        for match in reversed_result.matches
    }
    assert first_by_pattern == reversed_by_pattern


def test_symbolic_wide_set_does_not_expand_to_permutations_at_compile_time() -> None:
    choices = " | ".join(f"k{index}" for index in range(24))
    parser = CommandLineParser({"commands": [f"select {{ {choices} }} *"]})

    assert len(parser.command_graph.routes) == 1
    result = parser.parse("select k23 k0 k12")
    assert isinstance(result, ParsedCommand)
    assert result.primary_match.variation == "select k23 k0 k12"


def test_many_equivalent_sources_do_not_create_quadratic_resolution() -> None:
    pattern = "display clock"
    parser = CommandLineParser({"commands": [pattern] * 1_000})

    result = parser.parse(pattern)

    assert isinstance(result, ParsedCommand)
    assert len(result.matches) == 1_000
