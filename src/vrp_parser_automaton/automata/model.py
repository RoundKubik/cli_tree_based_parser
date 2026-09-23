"""Immutable instructions for a compact, annotated nondeterministic automaton."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from vrp_parser_automaton.patterns import Literal, Parameter, Sequence


@dataclass(frozen=True, slots=True)
class PatternSource:
    pattern_id: str
    index: int
    original: str
    ast: Sequence


@dataclass(frozen=True, slots=True)
class Instruction:
    """Targets are state IDs, never expanded command routes.

    Set/repeat back edges have guards and update the execution's register stack.
    Atom source spans remain available for mapping parameters back to the AST.
    """

    kind: str
    path: str = ""
    target: int = -1
    branches: tuple[int, ...] = ()
    atom: Literal | Parameter | None = None
    minimum: int = 0
    maximum: int = 0
    pattern_index: int = -1
    source_id: str = ""


@dataclass(frozen=True, slots=True)
class CommandAutomaton:
    states: tuple[Instruction, ...]
    patterns: tuple[PatternSource, ...]
    starts: tuple[int, ...]
    literal_starts: Mapping[str, tuple[int, ...]]
    parameter_starts: tuple[int, ...]

    @classmethod
    def create(
        cls,
        states: tuple[Instruction, ...],
        patterns: tuple[PatternSource, ...],
        starts: tuple[int, ...],
        literal_starts: dict[str, tuple[int, ...]],
        parameter_starts: tuple[int, ...],
    ) -> CommandAutomaton:
        return cls(
            states, patterns, starts, MappingProxyType(literal_starts), parameter_starts
        )
