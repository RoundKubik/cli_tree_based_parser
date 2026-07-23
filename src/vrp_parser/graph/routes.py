"""Controlled linearization of ordinary choice groups.

Simple choices are expanded so their literal and parameter prefixes can merge
with other patterns.  Unordered sets and bounded repetitions remain symbolic,
which avoids factorial or exponential compilation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vrp_parser.patterns import Group, GroupMode, Node, Sequence
from vrp_parser.results import VariationStep


@dataclass(frozen=True, slots=True)
class LinearRoute:
    steps: tuple[Node, ...]
    trace: tuple[VariationStep, ...] = ()


class RouteLimitExceeded(RuntimeError):
    """Internal signal used to retain a complex group symbolically."""


class RouteExpander:
    """Expand regular choices up to a predictable per-pattern limit."""

    def __init__(self, maximum_routes: int = 512) -> None:
        if maximum_routes < 1:
            raise ValueError("maximum_routes must be positive")
        self._maximum_routes = maximum_routes

    def expand(self, sequence: Sequence) -> tuple[LinearRoute, ...]:
        try:
            return self._sequence(sequence, path="root")
        except RouteLimitExceeded:
            return (LinearRoute(sequence.items),)

    def _sequence(self, sequence: Sequence, *, path: str) -> tuple[LinearRoute, ...]:
        routes: tuple[LinearRoute, ...] = (LinearRoute(()),)
        for index, node in enumerate(sequence.items):
            node_routes = self._node(node, path=f"{path}.{index}")
            routes = self._product(routes, node_routes)
        return routes

    def _node(self, node: Node, *, path: str) -> tuple[LinearRoute, ...]:
        if not isinstance(node, Group) or node.mode in {
            GroupMode.OPTIONAL_SET,
            GroupMode.REQUIRED_SET,
        }:
            return (LinearRoute((node,)),)

        kind: Literal["choice", "optional"] = (
            "optional"
            if node.mode is GroupMode.OPTIONAL_ONE
            else "choice"
        )
        routes: list[LinearRoute] = []
        if node.mode is GroupMode.OPTIONAL_ONE:
            routes.append(
                LinearRoute(
                    (),
                    (VariationStep(kind="optional", path=path),),
                )
            )

        for alternative_index, alternative in enumerate(node.alternatives):
            for route in self._sequence(
                alternative,
                path=f"{path}.{alternative_index}",
            ):
                routes.append(
                    LinearRoute(
                        route.steps,
                        route.trace
                        + (
                            VariationStep(
                                kind=kind,
                                path=path,
                                selected=(alternative_index,),
                            ),
                        ),
                    )
                )
                self._check_limit(len(routes))
        return tuple(routes)

    def _product(
        self,
        left: tuple[LinearRoute, ...],
        right: tuple[LinearRoute, ...],
    ) -> tuple[LinearRoute, ...]:
        size = len(left) * len(right)
        self._check_limit(size)
        return tuple(
            LinearRoute(
                first.steps + second.steps,
                first.trace + second.trace,
            )
            for first in left
            for second in right
        )

    def _check_limit(self, size: int) -> None:
        if size > self._maximum_routes:
            raise RouteLimitExceeded
