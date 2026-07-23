"""Polymorphic coordinator for matching AST expressions."""

from __future__ import annotations

from collections.abc import Iterator

from vrp_parser.parameters import ParameterTypeRegistry
from vrp_parser.patterns import Group, Literal, Node, Parameter, Repeat

from .atoms import (
    DispatchOrder,
    LiteralExpressionMatcher,
    ParameterExpressionMatcher,
)
from .deduplication import WalkStateSet
from .diagnostics import MatchDiagnostics
from .groups import GroupExpressionMatcher
from .repeats import RepeatExpressionMatcher
from .state import WalkState
from .text import CommandText


class ExpressionMatcher:
    """Delegate each AST type to one focused matcher."""

    def __init__(
        self,
        parameter_types: ParameterTypeRegistry,
        dispatch_order: DispatchOrder | None = None,
        states: WalkStateSet | None = None,
    ) -> None:
        self._states = states or WalkStateSet()
        self._literal = LiteralExpressionMatcher()
        self._parameter = ParameterExpressionMatcher(
            parameter_types,
            dispatch_order,
        )
        self._group = GroupExpressionMatcher()
        self._repeat = RepeatExpressionMatcher(self._states)

    def match(
        self,
        expression: Node,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
    ) -> tuple[WalkState, ...]:
        return self._states.unique(
            tuple(
                self.walk(
                    expression,
                    state,
                    command,
                    diagnostics,
                    path=path,
                )
            )
        )

    def walk(
        self,
        expression: Node,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
    ) -> Iterator[WalkState]:
        if isinstance(expression, Literal):
            yield from self._literal.match(
                expression,
                state,
                command,
                diagnostics,
            )
        elif isinstance(expression, Parameter):
            yield from self._parameter.match(
                expression,
                state,
                command,
                diagnostics,
                path=path,
            )
        elif isinstance(expression, Group):
            yield from self._group.match(
                expression,
                state,
                command,
                diagnostics,
                path=path,
                walker=self,
            )
        elif isinstance(expression, Repeat):
            yield from self._repeat.match(
                expression,
                state,
                command,
                diagnostics,
                path=path,
                walker=self,
            )
        else:
            raise TypeError(
                f"unsupported pattern node: {type(expression).__name__}"
            )

    def sequence(
        self,
        expressions: tuple[Node, ...],
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
    ) -> tuple[WalkState, ...]:
        states: tuple[WalkState, ...] = (state,)
        for index, expression in enumerate(expressions):
            next_states = tuple(
                result
                for current in states
                for result in self.walk(
                    expression,
                    current,
                    command,
                    diagnostics,
                    path=f"{path}.{index}",
                )
            )
            states = self._states.unique(next_states)
            if not states:
                break
        return states
