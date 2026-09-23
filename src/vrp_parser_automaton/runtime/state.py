"""Immutable execution data, independent of compiled automaton instructions."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_parser_automaton.parameters import (
    ParameterDeclaration,
    ParameterResult,
    ParameterStatus,
    ParameterToken,
)
from vrp_parser_automaton.results import VariationStep


@dataclass(frozen=True, slots=True)
class CapturedParameter:
    declaration: ParameterDeclaration
    token: ParameterToken
    normalized: object | None
    slot_id: str = ""
    iterations: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class RejectedParameter:
    declaration: ParameterDeclaration
    token: ParameterToken
    result: ParameterResult

    @property
    def applicable(self) -> bool:
        return self.result.status is ParameterStatus.INVALID


@dataclass(frozen=True, slots=True)
class WalkState:
    position: int = 0
    parts: tuple[str, ...] = ()
    parameters: tuple[CapturedParameter, ...] = ()
    rejected: tuple[RejectedParameter, ...] = ()
    dispatch: tuple[int, ...] = ()
    parameter_led: bool | None = None
    source_order: tuple[int, ...] = ()
    trace: tuple[VariationStep, ...] = ()


def source_order_with_parent(
    base: WalkState,
    result: WalkState,
    decision: int,
) -> tuple[int, ...]:
    """Insert a parent's choice before choices made inside its branch."""

    nested = result.source_order[len(base.source_order) :]
    return (*base.source_order, decision, *nested)


@dataclass(frozen=True, slots=True)
class Candidate:
    pattern_index: int
    state: WalkState
