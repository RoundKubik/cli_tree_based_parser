"""Immutable values that form the merged command graph."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from vrp_parser.patterns import Node, Sequence
from vrp_parser.results import VariationStep

if TYPE_CHECKING:
    from collections.abc import Mapping


def ascii_lower(value: str) -> str:
    """Fold ASCII keyword letters without changing non-ASCII characters."""

    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lower = "abcdefghijklmnopqrstuvwxyz"
    return value.translate(str.maketrans(upper, lower))


@dataclass(frozen=True, slots=True)
class PatternSource:
    """One entry from the JSON command array."""

    pattern_id: str
    index: int
    original: str
    ast: Sequence


@dataclass(frozen=True, slots=True)
class GraphStep:
    """One semantic expression stored on a graph edge."""

    expression: Node
    key: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class RouteSource:
    """One linearized route belonging to an original pattern."""

    route_id: int
    pattern: PatternSource
    static_trace: tuple[VariationStep, ...]


@dataclass(frozen=True, slots=True)
class CommandEdge:
    """A shared expression edge and the routes allowed to traverse it."""

    step: GraphStep
    target: CommandNode
    route_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class CommandNode:
    """One immutable node in the prefix graph."""

    literal_edges: Mapping[str, CommandEdge]
    expression_edges: tuple[CommandEdge, ...]
    accepting_routes: frozenset[int]

    @classmethod
    def create(
        cls,
        literal_edges: dict[str, CommandEdge],
        expression_edges: tuple[CommandEdge, ...],
        accepting_routes: frozenset[int],
    ) -> CommandNode:
        """Protect the literal index from accidental mutation."""

        return cls(
            MappingProxyType(dict(literal_edges)),
            expression_edges,
            accepting_routes,
        )


@dataclass(frozen=True, slots=True)
class CommandGraph:
    """A merged prefix graph plus immutable route provenance."""

    root: CommandNode
    patterns: tuple[PatternSource, ...]
    routes: Mapping[int, RouteSource]

    @classmethod
    def create(
        cls,
        root: CommandNode,
        patterns: tuple[PatternSource, ...],
        routes: dict[int, RouteSource],
    ) -> CommandGraph:
        return cls(root, patterns, MappingProxyType(dict(routes)))
