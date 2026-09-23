"""Language comparison and intersections over lazy, epsilon-free frontiers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from vrp_format_matcher.comparison.execution import ProgramExecution, ProgramStep
from vrp_format_matcher.models import (
    AnalysisBudget,
    Arc,
    Automaton,
    Comparison,
    MappingLimitExceeded,
    PatternProgram,
)
from vrp_parser_automaton.runtime.execution import Configuration


@dataclass(frozen=True)
class LanguageStates:
    execution: ProgramExecution
    budget: AnalysisBudget | None = None

    def accepts(self, states: frozenset[Configuration]) -> bool:
        return any(
            self.execution.frontier(state, self.budget).accepts for state in states
        )

    def moves(
        self, states: frozenset[Configuration]
    ) -> dict[str, frozenset[Configuration]]:
        targets: dict[str, set[Configuration]] = {}
        for state in states:
            for step in self.execution.frontier(state, self.budget).steps:
                if self.budget is not None:
                    self.budget.spend()
                targets.setdefault(step.label, set()).add(step.target)
        return {label: frozenset(group) for label, group in targets.items()}


def compare(
    document: PatternProgram,
    device: PatternProgram,
    *,
    maximum_states: int,
    budget: AnalysisBudget | None = None,
) -> Comparison:
    left_language = LanguageStates(ProgramExecution(document, maximum_states), budget)
    right_language = LanguageStates(ProgramExecution(device, maximum_states), budget)
    start = (
        frozenset({left_language.execution.start}),
        frozenset({right_language.execution.start}),
    )
    pending = deque([start])
    visited = {start}
    common = document_only = device_only = prefix = False
    while pending:
        left, right = pending.popleft()
        if (not right and document_only) or (not left and device_only):
            continue
        accepts_left = left_language.accepts(left)
        accepts_right = right_language.accepts(right)
        common |= accepts_left and accepts_right
        document_only |= accepts_left and not accepts_right
        device_only |= accepts_right and not accepts_left
        if common and document_only and device_only:
            break
        if (not right and document_only) or (not left and device_only):
            continue
        left_moves, right_moves = left_language.moves(left), right_language.moves(right)
        for label in sorted(left_moves.keys() | right_moves.keys()):
            pair = (
                left_moves.get(label, frozenset()),
                right_moves.get(label, frozenset()),
            )
            prefix |= bool(pair[0] and pair[1])
            if pair in visited:
                continue
            if len(visited) >= maximum_states:
                raise MappingLimitExceeded("language comparison state limit exceeded")
            visited.add(pair)
            pending.append(pair)
    if not document_only and not device_only:
        relation = "equivalent"
    elif not document_only:
        relation = "document_subset"
    elif not device_only:
        relation = "device_subset"
    elif not common:
        relation = "prefix_only" if prefix else "disjoint"
    else:
        relation = "overlap"
    return Comparison(relation)


def intersection(
    document: PatternProgram,
    device: PatternProgram,
    *,
    maximum_states: int,
    budget: AnalysisBudget | None = None,
    prefixes: bool = False,
) -> Automaton:
    """Pair consuming transitions directly, avoiding epsilon Cartesian products."""
    left_execution = ProgramExecution(document, maximum_states)
    right_execution = ProgramExecution(device, maximum_states)
    start = (left_execution.start, right_execution.start)
    ids = {start: 0}
    pending = [start]
    edges: list[list[Arc]] = [[], []]

    def add(pair: tuple[Configuration, Configuration]) -> int:
        if pair not in ids:
            if len(edges) >= maximum_states:
                raise MappingLimitExceeded("intersection state limit exceeded")
            ids[pair] = len(edges)
            edges.append([])
            pending.append(pair)
        return ids[pair]

    if maximum_states < 2:
        raise MappingLimitExceeded("intersection state limit exceeded")
    for left, right in pending:
        arcs = edges[ids[(left, right)]]
        left_frontier = left_execution.frontier(left, budget)
        right_frontier = right_execution.frontier(right, budget)
        if not prefixes and left_frontier.accepts and right_frontier.accepts:
            arcs.append(Arc(1))
        right_labels: dict[str, list[ProgramStep]] = {}
        for step in right_frontier.steps:
            right_labels.setdefault(step.label, []).append(step)
        for first in left_frontier.steps:
            for second in right_labels.get(first.label, ()):
                if budget is not None:
                    budget.spend()
                arcs.append(
                    Arc(
                        add((first.target, second.target)),
                        first.label,
                        first.document,
                        second.device,
                    )
                )
        # Every nonempty synchronized beginning is a valid unfinished trace.
        # A shorter command may diverge here even if another command can keep
        # following shared transitions (for example, a bounded repetition).
        if prefixes and (left, right) != start:
            arcs.append(Arc(1))
    return trim(Automaton(0, 1, tuple(tuple(dict.fromkeys(arcs)) for arcs in edges)))


def trim(machine: Automaton) -> Automaton:
    """Retain states reachable from start and able to reach the final state."""
    predecessors: list[set[int]] = [set() for _ in machine.edges]
    for state, outgoing in enumerate(machine.edges):
        for arc in outgoing:
            predecessors[arc.target].add(state)
    productive = {machine.final}
    pending = [machine.final]
    for state in pending:
        for previous in sorted(predecessors[state]):
            if previous not in productive:
                productive.add(previous)
                pending.append(previous)
    if machine.start not in productive:
        return Automaton(0, 1, ((), ()))
    ids = {machine.start: 0}
    pending = [machine.start]
    result: list[tuple[Arc, ...]] = []
    for state in pending:
        arcs = []
        for arc in sorted(machine.edges[state], key=repr):
            if arc.target not in productive:
                continue
            if arc.target not in ids:
                ids[arc.target] = len(ids)
                pending.append(arc.target)
            arcs.append(Arc(ids[arc.target], arc.label, arc.document, arc.device))
        result.append(tuple(arcs))
    return Automaton(0, ids[machine.final], tuple(result))
