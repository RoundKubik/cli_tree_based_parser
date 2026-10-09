"""Whole-language coverage by unions, including guards and accepting prefixes."""

from __future__ import annotations

from itertools import combinations, product

import pytest

from vrp_format_matcher.comparison.coverage import covered_by
from vrp_format_matcher.models import AnalysisBudget, MappingLimitExceeded
from vrp_format_matcher.preparation.programs import ProgramCompiler
from vrp_format_matcher.preparation.sources import TargetFormats
from vrp_parser_automaton import CommandLineParser, ParsedCommand


def program(pattern):
    ast = TargetFormats([pattern]).patterns()[0].ast
    return ProgramCompiler(20_000).single(ast, document=False)


def covers(subject, alternatives, *, states=20_000, steps=200_000):
    return covered_by(
        program(subject),
        tuple(program(p) for p in alternatives),
        maximum_states=states,
        budget=AnalysisBudget(steps),
    )


@pytest.mark.parametrize(
    "subject,alternatives,expected",
    [
        ("c { a | b }", ["c a", "c b"], True),
        ("c { a | b | d }", ["c a", "c b"], False),
        ("c { a | b }", ["c a extra", "c b extra"], False),
        ("c { a | b } extra", ["c a", "c b"], False),
        ("c { a | b } { x | y }", ["c a x", "c b y"], False),
        ("c [ a ]", ["c", "c a"], True),
        ("c [ a ]", ["c a"], False),
        ("[ a ]", ["a"], False),
        ("[ a ]", ["[ a ]"], True),
        ("c", [], False),
        ("c [ a | b ] *", ["c", "c a", "c b", "c a b", "c b a"], True),
        ("c { a | b } *", ["c a", "c b", "c a b", "c b a"], True),
        ("c { a | b } *", ["c a", "c b", "c a b"], False),
        ("c { a } &<1-3>", ["c a", "c a a", "c a a a"], True),
        ("c { a } &<1-3>", ["c a", "c a a a"], False),
        (
            "c { a [ x ] | b } *",
            ["c a", "c a x", "c b", "c a b", "c b a", "c a x b", "c b a x"],
            True,
        ),
        (
            "c { INTEGER<1-9> | X.X.X.X }",
            ["c INTEGER<1-9>", "c X.X.X.X"],
            True,
        ),
        (
            "c { INTEGER<1-9> | X.X.X.X }",
            ["c INTEGER<1-9>", "c X:X::X:X"],
            False,
        ),
        ("c STRING<1-9>", ["c TEXT<1-99>"], True),
    ],
)
def test_coverage(subject, alternatives, expected):
    assert covers(subject, alternatives) is expected


@pytest.mark.parametrize("limits", [{"states": 1}, {"steps": 1}])
def test_exhausted_proof_is_not_reported_as_false(limits):
    with pytest.raises(MappingLimitExceeded):
        covers("c { a | b }", ["c a", "c b"], **limits)


def test_union_inclusion_agrees_with_exhaustive_runtime_recognition():
    # All languages below are finite and contain at most two tokens after c.
    patterns = [
        "c a",
        "c b",
        "c [ a ]",
        "c { a | b }",
        "c { a | b } *",
        "c { a } &<1-2>",
    ]
    lines = [
        " ".join(("c", *tail))
        for length in range(3)
        for tail in product(("a", "b"), repeat=length)
    ]
    languages = []
    for pattern in patterns:
        parser = CommandLineParser({"commands": [pattern]}, context_mode="hierarchy")
        languages.append(
            {line for line in lines if isinstance(parser.parse(line), ParsedCommand)}
        )
    programs = [program(pattern) for pattern in patterns]
    for subject, language in enumerate(languages):
        for size in (1, 2, 3):
            for indices in combinations(range(len(patterns)), size):
                expected = language <= set().union(*(languages[i] for i in indices))
                actual = covered_by(
                    programs[subject],
                    tuple(programs[i] for i in indices),
                    maximum_states=20_000,
                    budget=AnalysisBudget(200_000),
                )
                assert actual == expected, (patterns[subject], indices)
