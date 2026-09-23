"""Matchers for fixed literals and parameter declarations."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

from vrp_parser_automaton.diagnostics.progress import MatchDiagnostics
from vrp_parser_automaton.parameters import (
    ParameterDeclaration,
    ParameterFamily,
    ParameterStatus,
    ParameterTypeRegistry,
)
from vrp_parser_automaton.patterns import Literal, Parameter
from vrp_parser_automaton.results import VariationStep
from vrp_parser_automaton.text import CommandText, ascii_lower

from .state import CapturedParameter, RejectedParameter, WalkState


class DispatchOrder:
    """One central family ordering shared by all command patterns."""

    _RANKS = {
        ParameterFamily.ENUM: 1,
        ParameterFamily.STRUCTURED: 2,
        ParameterFamily.NUMERIC: 3,
        ParameterFamily.GENERIC: 4,
        ParameterFamily.REMAINDER: 5,
    }

    def rank(
        self,
        declaration: ParameterDeclaration,
        registry: ParameterTypeRegistry,
    ) -> int:
        parameter_type = registry.get(declaration.type_id)
        if parameter_type is None:
            return 6
        return self._RANKS[parameter_type.family]


class LiteralTransition:
    """Match one fixed whitespace-delimited CLI token."""

    def match(
        self,
        expression: Literal,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
    ) -> Iterator[WalkState]:
        token = command.token(state.position)
        position = command.skip_space(state.position)
        if token is None or ascii_lower(token.raw) != ascii_lower(expression.value):
            diagnostics.record(
                position,
                repr(expression.value),
                parameter_led=state.parameter_led,
            )
            return
        yield replace(
            state,
            position=token.end,
            parts=state.parts + (ascii_lower(expression.value),),
            dispatch=state.dispatch + (0,),
            parameter_led=(
                False if state.parameter_led is None else state.parameter_led
            ),
        )


class ParameterTransition:
    """Read, probe, and capture one registered parameter value."""

    def __init__(
        self,
        parameter_types: ParameterTypeRegistry,
        dispatch_order: DispatchOrder | None = None,
    ) -> None:
        self._parameter_types = parameter_types
        self._dispatch_order = dispatch_order or DispatchOrder()

    def match(
        self,
        expression: Parameter,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
        iterations: tuple[tuple[str, int], ...] = (),
    ) -> Iterator[WalkState]:
        declaration = expression.declaration
        if not isinstance(declaration, ParameterDeclaration):
            raise TypeError("parameter AST contains an unknown declaration")
        if not self._text_policy_allows(declaration, state, command):
            return
        token = self._parameter_types.read(
            declaration,
            command.value,
            state.position,
        )
        if token is None:
            diagnostics.record(
                command.skip_space(state.position),
                declaration.source,
                parameter_led=state.parameter_led,
            )
            return

        result = self._parameter_types.probe(token.raw, declaration)
        rank = self._dispatch_order.rank(declaration, self._parameter_types)
        trace = self._trace(declaration, result.valid, result.normalized, state, path)

        if result.status is ParameterStatus.VALID:
            parameters = state.parameters + (
                CapturedParameter(
                    declaration,
                    token,
                    result.normalized,
                    f"p:{expression.span.start}",
                    iterations,
                ),
            )
            rejected = state.rejected
        else:
            parameters = state.parameters
            rejected = state.rejected + (RejectedParameter(declaration, token, result),)

        yield replace(
            state,
            position=token.next_position,
            parts=state.parts + (declaration.source,),
            parameters=parameters,
            rejected=rejected,
            dispatch=state.dispatch + (rank,),
            parameter_led=self._parameter_led(state, result.status),
            trace=trace,
        )

    @staticmethod
    def _parameter_led(
        state: WalkState,
        status: ParameterStatus,
    ) -> bool | None:
        if state.parameter_led is not None:
            return state.parameter_led
        return status is not ParameterStatus.NOT_APPLICABLE

    @staticmethod
    def _text_policy_allows(
        declaration: ParameterDeclaration,
        state: WalkState,
        command: CommandText,
    ) -> bool:
        if declaration.type_id != "text" or state.position != 0:
            return True
        start = command.skip_space(0)
        return start < len(command.value) and command.value[start] == "!"

    def _trace(
        self,
        declaration: ParameterDeclaration,
        valid: bool,
        normalized: object | None,
        state: WalkState,
        path: str,
        iterations: tuple[tuple[str, int], ...] = (),
    ) -> tuple[VariationStep, ...]:
        if (
            self._parameter_types.family_of(declaration.type_id)
            is not ParameterFamily.ENUM
            or not valid
        ):
            return state.trace
        return state.trace + (
            VariationStep(
                kind="enum",
                path=path,
                selected=(str(normalized),),
            ),
        )
