"""Typed language inclusion in a union, with lazy states and separate programs."""

from __future__ import annotations

from collections import deque

from vrp_format_matcher.models import (
    AnalysisBudget,
    MappingLimitExceeded,
    PatternProgram,
)
from vrp_parser_automaton.runtime.execution import Configuration

from .execution import ProgramExecution
from .languages import LanguageStates

type UnionState = frozenset[tuple[int, Configuration]]


class LanguageUnion:
    """Program identity keeps equally numbered states and repeat frames separate.

    This is the virtual common start of several NFAs. No instructions, captures
    or paths are copied into a new graph.
    """

    def __init__(
        self,
        programs: tuple[PatternProgram, ...],
        maximum_states: int,
        budget: AnalysisBudget,
    ) -> None:
        budget.spend(len(programs))
        self._languages = tuple(
            LanguageStates(ProgramExecution(program, maximum_states), budget, True)
            for program in programs
        )
        self.start: UnionState = frozenset(
            (index, language.execution.start)
            for index, language in enumerate(self._languages)
        )

    def accepts(self, states: UnionState) -> bool:
        return any(
            self._languages[index].accepts(frozenset({state}))
            for index, state in states
        )

    def moves(self, states: UnionState) -> dict[str, UnionState]:
        targets: dict[str, set[tuple[int, Configuration]]] = {}
        for index, state in states:
            language = self._languages[index]
            for label, successors in language.moves(frozenset({state})).items():
                targets.setdefault(label, set()).update(
                    (index, successor) for successor in successors
                )
        return {label: frozenset(group) for label, group in targets.items()}


def covered_by(
    subject: PatternProgram,
    alternatives: tuple[PatternProgram, ...],
    *,
    maximum_states: int,
    budget: AnalysisBudget,
) -> bool:
    """Prove L(subject) <= union L(alternatives), or find an accepted exception.

    Exceeding a limit raises MappingLimitExceeded; it never returns False. Only
    subject moves matter: extra words accepted by the union do not affect inclusion.
    """
    language = LanguageStates(ProgramExecution(subject, maximum_states), budget, True)
    union = LanguageUnion(alternatives, maximum_states, budget)
    start = (frozenset({language.execution.start}), union.start)
    pending = deque([start])
    visited = {start}
    while pending:
        budget.spend()
        left, right = pending.popleft()
        if language.accepts(left) and not union.accepts(right):
            return False
        left_moves = language.moves(left)
        if not left_moves:
            continue
        right_moves = union.moves(right)
        for label, successors in left_moves.items():
            budget.spend()
            following = successors, right_moves.get(label, frozenset())
            if following in visited:
                continue
            if len(visited) >= maximum_states:
                raise MappingLimitExceeded("coverage state limit exceeded")
            visited.add(following)
            pending.append(following)
    return True
