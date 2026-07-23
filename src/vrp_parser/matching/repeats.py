"""Matcher for bounded repetition expressions."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

from vrp_parser.patterns import Repeat
from vrp_parser.results import VariationStep

from .deduplication import WalkStateSet
from .diagnostics import MatchDiagnostics
from .state import WalkState, source_order_with_parent
from .text import CommandText
from .walking import ExpressionWalker


class RepeatExpressionMatcher:
    """Repeat only consuming matches, bounded by the source declaration."""

    def __init__(self, states: WalkStateSet | None = None) -> None:
        self._states = states or WalkStateSet()

    def match(
        self,
        expression: Repeat,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
        walker: ExpressionWalker,
    ) -> Iterator[WalkState]:
        frontier: tuple[WalkState, ...] = (state,)
        if expression.minimum == 0:
            yield self._with_count(state, state, path, 0)

        for count in range(1, expression.maximum + 1):
            frontier = self._next(
                expression,
                frontier,
                command,
                diagnostics,
                path=path,
                count=count,
                walker=walker,
            )
            if not frontier:
                return
            if count >= expression.minimum:
                for current in frontier:
                    yield self._with_count(state, current, path, count)

    def _next(
        self,
        expression: Repeat,
        frontier: tuple[WalkState, ...],
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
        count: int,
        walker: ExpressionWalker,
    ) -> tuple[WalkState, ...]:
        results: list[WalkState] = []
        for current in frontier:
            results.extend(
                result
                for result in walker.walk(
                    expression.atom,
                    current,
                    command,
                    diagnostics,
                    path=f"{path}.{count - 1}",
                )
                if result.position > current.position
            )
        return self._states.unique(tuple(results))

    @staticmethod
    def _with_count(
        base: WalkState,
        state: WalkState,
        path: str,
        count: int,
    ) -> WalkState:
        return replace(
            state,
            source_order=source_order_with_parent(base, state, count),
            trace=state.trace
            + (
                VariationStep(
                    kind="repeat",
                    path=path,
                    selected=(count,),
                ),
            ),
        )
