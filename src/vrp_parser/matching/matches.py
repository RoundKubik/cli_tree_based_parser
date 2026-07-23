"""Construction and equivalence signatures for public pattern matches."""

from __future__ import annotations

from hashlib import sha256

from vrp_parser.graph import CommandGraph, RouteSource
from vrp_parser.results import ParameterValue, PatternMatch, TextSpan

from .state import Candidate
from .text import ascii_lower


class PatternMatchFactory:
    """Convert one valid internal candidate into a public value."""

    def create(
        self,
        candidate: Candidate,
        route: RouteSource,
        *,
        span_offset: int,
    ) -> PatternMatch:
        state = candidate.state
        variation = " ".join(state.parts)
        trace = route.static_trace + state.trace
        variation_id = self._variation_id(
            route.pattern.pattern_id,
            variation,
            trace,
        )
        parameters = tuple(
            ParameterValue(
                type_id=item.declaration.type_id,
                declaration=item.declaration.source,
                raw=item.token.raw,
                normalized=item.normalized,
                span=TextSpan(
                    item.token.start + span_offset,
                    item.token.end + span_offset,
                ),
            )
            for item in state.parameters
        )
        return PatternMatch(
            pattern_id=route.pattern.pattern_id,
            pattern_index=route.pattern.index,
            original_pattern=route.pattern.original,
            variation=variation,
            variation_id=variation_id,
            parameters=parameters,
            trace=trace,
        )

    @staticmethod
    def _variation_id(
        pattern_id: str,
        variation: str,
        trace: object,
    ) -> str:
        material = (pattern_id, ascii_lower(variation), repr(trace))
        return sha256("\0".join(material).encode("utf-8")).hexdigest()[:20]


class PatternMatchSet:
    """Order, deduplicate, and compare successful pattern matches."""

    def __init__(self, factory: PatternMatchFactory | None = None) -> None:
        self._factory = factory or PatternMatchFactory()

    def create(
        self,
        candidates: tuple[Candidate, ...],
        graph: CommandGraph,
        *,
        span_offset: int,
    ) -> tuple[PatternMatch, ...]:
        ordered = sorted(
            enumerate(candidates),
            key=lambda item: (
                graph.routes[item[1].route_id].pattern.index,
                item[1].route_id,
                item[1].state.source_order,
                item[0],
            ),
        )
        matches = [
            self._factory.create(
                candidate,
                graph.routes[candidate.route_id],
                span_offset=span_offset,
            )
            for _, candidate in ordered
        ]
        unique: dict[tuple[object, ...], PatternMatch] = {}
        for match in matches:
            unique.setdefault(self._identity(match), match)
        return tuple(unique.values())

    def equivalent(self, matches: tuple[PatternMatch, ...]) -> bool:
        return len({self._signature(item) for item in matches}) == 1

    @staticmethod
    def _identity(match: PatternMatch) -> tuple[object, ...]:
        return (
            match.pattern_id,
            ascii_lower(match.variation),
            tuple(
                (value.type_id, value.declaration, value.raw)
                for value in match.parameters
            ),
        )

    @staticmethod
    def _signature(match: PatternMatch) -> tuple[object, ...]:
        return (
            ascii_lower(match.variation),
            tuple(
                (
                    value.type_id,
                    value.declaration,
                    value.raw,
                    repr(value.normalized),
                )
                for value in match.parameters
            ),
        )
