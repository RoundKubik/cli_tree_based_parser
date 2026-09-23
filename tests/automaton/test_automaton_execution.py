"""Automaton-specific guarantees beyond the copied public API contract tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from itertools import product
from pathlib import Path

import pytest

from vrp_parser_automaton import (
    CommandLineParser,
    ConfigurationParser,
    ErrorLine,
    ParsedCommand,
)

STATIC_ROUTE = (
    "ip route-static X.X.X.X { X.X.X.X | INTEGER<0-32> } X.X.X.X "
    "[ recursive-lookup host-route [ arp-vlink-only ] ] "
    "[ preference INTEGER<1-255> | tag INTEGER<1-4294967295> ] * "
    "[ [ bfd enable | track { bfd-session STRING<1-15> | "
    "nqa STRING<1-32> STRING<1-32> } ] [ inherit-cost ] | permanent ] "
    "[ arp-detect { STRING<1-64> | ENUM{Vlanif,GigabitEthernet} STRING<1-32> } ] "
    "[ inter-protocol-ecmp ] [ description TEXT<1-80> ]"
)


def test_package_executes_with_original_package_imports_forbidden() -> None:
    root = Path(__file__).resolve().parents[2]
    script = """
import sys
from importlib.abc import MetaPathFinder
class DenyOriginal(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "vrp_parser" or fullname.startswith("vrp_parser."):
            raise AssertionError("original parser imported: " + fullname)
sys.meta_path.insert(0, DenyOriginal())
from vrp_parser_automaton import CommandLineParser, ParsedCommand
p = CommandLineParser({"commands": ["c { a | b } * INTEGER<1-10>"]})
assert isinstance(p.parse("c b a 3"), ParsedCommand)
assert not any(
    name == "vrp_parser" or name.startswith("vrp_parser.") for name in sys.modules
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    assert result.returncode == 0, result.stderr


def test_many_optional_groups_compile_linearly_without_expansion() -> None:
    count = 40
    parser = CommandLineParser(
        {"commands": ["c " + " ".join(f"[ k{i} ]" for i in range(count)) + " end"]}
    )
    assert len(parser.automaton.states) == count * 3 + 3
    assert parser.command_graph is parser.automaton
    assert isinstance(parser.parse("c end"), ParsedCommand)
    assert isinstance(
        parser.parse("c " + " ".join(f"k{i}" for i in range(count)) + " end"),
        ParsedCommand,
    )
    assert isinstance(parser.parse("c k30 k2 end"), ErrorLine)


@pytest.mark.parametrize("optional", [False, True])
def test_wide_set_preserves_uniqueness_without_materializing_masks(
    optional: bool,
) -> None:
    opening, closing = ("[", "]") if optional else ("{", "}")
    pattern = (
        "c "
        + opening
        + " | ".join(f"k{i} INTEGER<1-100>" for i in range(24))
        + closing
        + " * end"
    )
    parser = CommandLineParser({"commands": [pattern]})
    assert len(parser.automaton.states) < 60
    order = tuple(reversed(range(24)))
    line = "c " + " ".join(f"k{i} {i + 1}" for i in order) + " end"
    result = parser.parse(line)
    assert isinstance(result, ParsedCommand)
    assert [value.normalized for value in result.parameters] == [i + 1 for i in order]
    assert (
        next(step for step in result.primary_match.trace if step.kind == "set").selected
        == order
    )
    assert isinstance(parser.parse("c end"), ParsedCommand) is optional
    assert isinstance(parser.parse("c k1 1 k1 2 end"), ErrorLine)


def test_huge_repeat_bound_does_not_copy_the_body() -> None:
    parser = CommandLineParser({"commands": ["c INTEGER<1-100> &<1-100000>"]})
    assert len(parser.automaton.states) == 6
    result = parser.parse("c 1 2 3")
    assert isinstance(result, ParsedCommand)
    assert [item.normalized for item in result.parameters] == [1, 2, 3]
    assert result.primary_match.trace[-1].selected == (3,)


def test_nested_sets_reset_on_each_repeat_and_retain_nested_coordinates() -> None:
    pattern = "c { { a ENUM{x,y} | b INTEGER<1-100> } * end } &<1-3>"
    parser = CommandLineParser({"commands": [pattern]})
    result = parser.parse("c b 10 a x end a y end")
    assert isinstance(result, ParsedCommand)
    assert [item.normalized for item in result.parameters] == [10, "x", "y"]
    sets = [item for item in result.primary_match.trace if item.kind == "set"]
    assert [item.selected for item in sets] == [(1, 0), (0,)]
    assert sets[0].path != sets[1].path
    assert isinstance(parser.parse("c a x a y end"), ErrorLine)


def test_continuations_are_shared_after_choice() -> None:
    parser = CommandLineParser({"commands": ["c { a | b } tail end"]})
    atoms = [
        node.atom.value
        for node in parser.automaton.states
        if node.atom is not None and hasattr(node.atom, "value")
    ]
    assert atoms.count("tail") == atoms.count("end") == 1
    assert isinstance(parser.parse("c a tail end"), ParsedCommand)
    assert isinstance(parser.parse("c b tail end"), ParsedCommand)


def test_pattern_sources_never_mix_and_ids_do_not_depend_on_catalog_order() -> None:
    first = "c { a | b } left"
    second = "c { b | a } right"
    parser = CommandLineParser({"commands": [first, second, first]})
    assert isinstance(parser.parse("c a left right"), ErrorLine)
    result = parser.parse("c a left")
    assert isinstance(result, ParsedCommand) and len(result.matches) == 2
    other = CommandLineParser({"commands": [second, first]}).parse("c a left")
    assert isinstance(other, ParsedCommand)
    assert result.primary_match.variation_id == other.primary_match.variation_id


def test_static_route_pattern_combines_all_constructs() -> None:
    parser = CommandLineParser({"commands": [STATIC_ROUTE]})
    assert len(parser.automaton.states) < 100
    base = "ip route-static 10.0.0.0 24 192.0.2.1"
    accepted = [
        base,
        base
        + " tag 7 preference 10 permanent inter-protocol-ecmp description hello world",
        base + " recursive-lookup host-route arp-vlink-only preference 20 tag 30"
        " track nqa admin probe inherit-cost arp-detect Vlanif 10",
        base + " inherit-cost",
        base + " bfd enable inherit-cost",
    ]
    for line in accepted:
        assert isinstance(parser.parse(line), ParsedCommand), line
    for suffix in [
        "tag 1 tag 2",
        "permanent inherit-cost",
        "arp-vlink-only",
        "bfd enable track bfd-session name",
    ]:
        assert isinstance(parser.parse(base + " " + suffix), ErrorLine), suffix


@pytest.mark.parametrize(
    "pattern",
    [
        "c [ a ] [ b ]",
        "c { a | b } *",
        "c [ a | b ] *",
        "c { [ a ] | b } *",
        "c { [ a ] } &<1-2>",
        "c { a [ b ] } &<0-2>",
        "c { [ a ] [ b ] | a } [ b ]",
        "c { { a | b } * } &<1-2>",
    ],
)
def test_small_languages_agree_with_existing_parser(pattern: str) -> None:
    # The test oracle is allowed to import the original package; production isn't.
    from vrp_parser import CommandLineParser as OriginalParser
    from vrp_parser import ParsedCommand as OriginalCommand

    old = OriginalParser({"commands": [pattern]})
    new = CommandLineParser({"commands": [pattern]})
    for length in range(5):
        for suffix in product(("a", "b"), repeat=length):
            line = " ".join(("c", *suffix))
            assert isinstance(new.parse(line), ParsedCommand) == isinstance(
                old.parse(line), OriginalCommand
            ), line


def test_failed_all_parameter_branches_keep_dispatch_priority() -> None:
    pattern = "c { STRING<1-10> | INTEGER<1-100> } end"
    # Structured/numeric INVALID must still block a valid generic fallback.
    parser = CommandLineParser({"commands": [pattern]})
    result = parser.parse("c 101 end")
    assert isinstance(result, ErrorLine)
    assert result.error.failures[0].type_id == "integer"


def test_json_roundtrip_api_preserves_indentation_and_multiple_lines() -> None:
    parser = CommandLineParser.from_json(json.dumps({"commands": ["c INTEGER<1-10>"]}))
    report = ConfigurationParser(parser).parse("  c 3\r\n\nc 11\n")
    assert report.summary.total == 3
    assert report.summary.blank == 1 and report.summary.errors == 1
    command = report.lines[0]
    assert isinstance(command, ParsedCommand)
    assert command.parameters[0].span.start == 4
    assert (
        report.to_dict()["lines"][0]["primary_match"]["parameters"][0]["normalized"]
        == 3
    )


def test_multi_token_reader_does_not_prune_a_path_before_common_suffix() -> None:
    import re

    from vrp_parser_automaton import (
        MatchStatus,
        ParameterFamily,
        ParameterResult,
        ParameterType,
        default_parameter_registry,
    )
    from vrp_parser_automaton.parameters import (
        ExactDeclarationRecognizer,
        ParameterToken,
    )

    class TwoWords:
        def read(self, text, position):
            match = re.match(r"\s*(\S+\s+\S+)", text[position:])
            if match is None:
                return None
            start, end = position + match.start(1), position + match.end(1)
            return ParameterToken(text[start:end], start, end, end)

    class ValidPair:
        def probe(self, raw, declaration):
            return ParameterResult.success(raw.split())

    registry = default_parameter_registry()
    registry.register(
        ParameterType(
            "pair",
            ParameterFamily.STRUCTURED,
            ExactDeclarationRecognizer("PAIR"),
            TwoWords(),
            ValidPair(),
        )
    )
    parser = CommandLineParser(
        {"commands": ["c { PAIR | STRING<1-10> b } TEXT<1-80>"]},
        parameter_types=registry,
    )
    result = parser.parse("c a b tail")
    assert isinstance(result, ParsedCommand)
    assert result.status is MatchStatus.AMBIGUOUS
    assert len(result.matches) == 2


def test_automaton_builder_can_be_reused_without_leaking_previous_states() -> None:
    from vrp_parser_automaton.automata.building import AutomatonBuilder

    parser = CommandLineParser({"commands": ["c { a | b } tail"]})
    builder = AutomatonBuilder()
    first = builder.build(parser.automaton.patterns)
    second = builder.build(parser.automaton.patterns)
    assert first == second == parser.automaton
