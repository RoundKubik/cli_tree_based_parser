"""Language comparison and intersections over lazy, epsilon-free frontiers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .execution import Configuration, ProgramExecution, ProgramStep
from .models import Arc, Automaton, Comparison, MappingLimitExceeded, PatternProgram


def display(word: tuple[str, ...]) -> tuple[str, ...]:
    return tuple("<PARAM>" if label == "P" else label[2:] for label in word)


@dataclass(frozen=True)
class LanguageStates:
    execution: ProgramExecution

    def accepts(self, states: frozenset[Configuration]) -> bool:
        return any(self.execution.frontier(state).accepts for state in states)

    def moves(
        self, states: frozenset[Configuration]
    ) -> dict[str, frozenset[Configuration]]:
        targets: dict[str, set[Configuration]] = {}
        for state in states:
            for step in self.execution.frontier(state).steps:
                targets.setdefault(step.label, set()).add(step.target)
        return {label: frozenset(group) for label, group in targets.items()}


def compare(
    document: PatternProgram,
    device: PatternProgram,
    *,
    maximum_states: int,
    structurally_identical: bool = False,
) -> Comparison:
    left_language = LanguageStates(ProgramExecution(document, maximum_states))
    right_language = LanguageStates(ProgramExecution(device, maximum_states))
    start = (
        frozenset({left_language.execution.start}),
        frozenset({right_language.execution.start}),
    )
    pending: deque[
        tuple[
            tuple[frozenset[Configuration], frozenset[Configuration]], tuple[str, ...]
        ]
    ] = deque([(start, ())])
    visited = {start}
    common = document_only = device_only = None
    prefix: tuple[str, ...] = ()
    while pending:
        (left, right), word = pending.popleft()
        accepts_left = left_language.accepts(left)
        accepts_right = right_language.accepts(right)
        if accepts_left and accepts_right and common is None:
            common = display(word)
        elif accepts_left and not accepts_right and document_only is None:
            document_only = display(word)
        elif accepts_right and not accepts_left and device_only is None:
            device_only = display(word)
        if left and right and len(word) > len(prefix):
            prefix = display(word)
        if common is not None and document_only is not None and device_only is not None:
            break  # All three witnesses prove overlap; no exhaustive traversal needed.
        left_moves, right_moves = left_language.moves(left), right_language.moves(right)
        for label in sorted(left_moves.keys() | right_moves.keys()):
            pair = (
                left_moves.get(label, frozenset()),
                right_moves.get(label, frozenset()),
            )
            if pair in visited:
                continue
            if len(visited) >= maximum_states:
                raise MappingLimitExceeded("language comparison state limit exceeded")
            visited.add(pair)
            pending.append((pair, (*word, label)))
    if document_only is None and device_only is None:
        relation = "equivalent"
    elif document_only is None:
        relation = "document_subset"
    elif device_only is None:
        relation = "device_subset"
    elif common is None:
        relation = "prefix_only" if prefix else "disjoint"
    else:
        relation = "overlap"
    return Comparison(
        relation, structurally_identical, common, document_only, device_only, prefix
    )


def intersection(
    document: PatternProgram,
    device: PatternProgram,
    *,
    maximum_states: int,
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
        left_frontier = left_execution.frontier(left)
        right_frontier = right_execution.frontier(right)
        if left_frontier.accepts and right_frontier.accepts:
            arcs.append(Arc(1))
        right_labels: dict[str, list[ProgramStep]] = {}
        for step in right_frontier.steps:
            right_labels.setdefault(step.label, []).append(step)
        for first in left_frontier.steps:
            for second in right_labels.get(first.label, ()):
                arcs.append(
                    Arc(
                        add((first.target, second.target)),
                        first.label,
                        first.document,
                        second.device,
                    )
                )
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
