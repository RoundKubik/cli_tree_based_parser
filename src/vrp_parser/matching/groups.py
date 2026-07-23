"""Matchers for choice and unordered-set group expressions."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Literal

from vrp_parser.patterns import Group, GroupMode
from vrp_parser.results import VariationStep

from .alternatives import AlternativeOutcome, AlternativeStateFrontier
from .diagnostics import MatchDiagnostics
from .state import WalkState, source_order_with_parent
from .text import CommandText
from .walking import ExpressionWalker


class GroupExpressionMatcher:
    """Apply the four Huawei group cardinality modes."""

    def __init__(
        self,
        alternatives: AlternativeStateFrontier | None = None,
    ) -> None:
        self._alternatives = alternatives or AlternativeStateFrontier()

    def match(
        self,
        expression: Group,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
        walker: ExpressionWalker,
    ) -> Iterator[WalkState]:
        if expression.mode in {
            GroupMode.OPTIONAL_SET,
            GroupMode.REQUIRED_SET,
        }:
            yield from self._set(
                expression,
                state,
                command,
                diagnostics,
                path=path,
                walker=walker,
            )
            return
        yield from self._choice(
            expression,
            state,
            command,
            diagnostics,
            path=path,
            walker=walker,
        )

    def _choice(
        self,
        expression: Group,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
        walker: ExpressionWalker,
    ) -> Iterator[WalkState]:
        optional = expression.mode is GroupMode.OPTIONAL_ONE
        kind: Literal["choice", "optional"] = (
            "optional" if optional else "choice"
        )
        if optional:
            yield replace(
                state,
                source_order=state.source_order + (0,),
                trace=state.trace
                + (VariationStep(kind="optional", path=path),),
            )
        outcomes = tuple(
            AlternativeOutcome(index, result)
            for index, alternative in enumerate(expression.alternatives)
            for result in walker.sequence(
                alternative.items,
                state,
                command,
                diagnostics,
                path=f"{path}.{index}",
            )
        )
        for outcome in self._alternatives.select(outcomes, state):
            selected_order = (
                outcome.alternative + 1
                if optional
                else outcome.alternative
            )
            yield replace(
                outcome.state,
                source_order=source_order_with_parent(
                    state,
                    outcome.state,
                    selected_order,
                ),
                trace=outcome.state.trace
                + (
                    VariationStep(
                        kind=kind,
                        path=path,
                        selected=(outcome.alternative,),
                    ),
                ),
            )

    def _set(
        self,
        expression: Group,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
        walker: ExpressionWalker,
    ) -> Iterator[WalkState]:
        minimum = 0 if expression.mode is GroupMode.OPTIONAL_SET else 1

        def visit(
            current: WalkState,
            used: frozenset[int],
            order: tuple[int, ...],
        ) -> Iterator[WalkState]:
            if len(order) >= minimum:
                yield replace(
                    current,
                    trace=current.trace
                    + (
                        VariationStep(
                            kind="set",
                            path=path,
                            selected=order,
                        ),
                    ),
                )
            outcomes = tuple(
                AlternativeOutcome(index, result)
                for index, alternative in enumerate(expression.alternatives)
                if index not in used
                for result in walker.sequence(
                    alternative.items,
                    current,
                    command,
                    diagnostics,
                    path=f"{path}.{index}",
                )
                if result.position > current.position
            )
            selected = tuple(
                outcome
                for index in range(len(expression.alternatives))
                for outcome in self._alternatives.select(
                    tuple(
                        item
                        for item in outcomes
                        if item.alternative == index
                    ),
                    current,
                )
            )
            for outcome in selected:
                selected_state = replace(
                    outcome.state,
                    source_order=source_order_with_parent(
                        current,
                        outcome.state,
                        outcome.alternative,
                    ),
                )
                yield from visit(
                    selected_state,
                    used | {outcome.alternative},
                    order + (outcome.alternative,),
                )

        yield from visit(state, frozenset(), ())
