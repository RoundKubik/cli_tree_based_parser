"""A bounded path search using the same control transitions as recognition."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, replace
from itertools import count

from vrp_parser_automaton.automata.model import CommandAutomaton, Instruction
from vrp_parser_automaton.patterns import Literal, Parameter
from vrp_parser_automaton.runtime.execution import Configuration, ControlFlow
from vrp_parser_automaton.runtime.state import WalkState
from vrp_parser_automaton.text import ascii_lower

from .similarity import CommandSimilarity


@dataclass(frozen=True, slots=True)
class SearchPath:
    configuration: Configuration
    row: tuple[int, ...]
    atoms: tuple[Literal | Parameter, ...] = ()
    previous_row: tuple[int, ...] = ()

    def consuming(
        self, node: Instruction, query: tuple[str, ...], similarity: CommandSimilarity
    ) -> SearchPath:
        atom = node.atom
        assert isinstance(atom, (Literal, Parameter))
        missing = 900 if isinstance(atom, Literal) else 750
        row = [self.row[0] + missing]
        for j, token in enumerate(query, 1):
            cost = similarity.cost(atom, token)
            value = min(self.row[j] + missing, row[-1] + 900, self.row[j - 1] + cost)
            if self.atoms and self.previous_row and j >= 2:
                previous = self.atoms[-1]
                if (
                    isinstance(previous, Literal)
                    and isinstance(atom, Literal)
                    and ascii_lower(previous.value) == query[j - 1]
                    and ascii_lower(atom.value) == query[j - 2]
                ):
                    value = min(value, self.previous_row[j - 2] + 400)
            row.append(value)
        state = replace(
            self.configuration.state, position=self.configuration.state.position + 1
        )
        next_path = SearchPath(
            self.configuration.at(node.target, state),
            tuple(row),
            self.atoms + (atom,),
            self.row,
        )
        return next_path


class SuggestionSearch:
    def __init__(
        self, automaton: CommandAutomaton, similarity: CommandSimilarity
    ) -> None:
        self.automaton = automaton
        self.similarity = similarity
        self.flow = ControlFlow()

    def search(
        self, start: int, query: tuple[str, ...], budget: int
    ) -> tuple[tuple[int, int, int, int] | None, int]:
        serial = count()
        initial = SearchPath(
            Configuration(start, WalkState()),
            tuple(900 * i for i in range(len(query) + 1)),
        )
        queue = [(0, next(serial), initial)]
        seen = set()
        best = None
        work = 0
        while queue and work < budget:
            _, _, path = heapq.heappop(queue)
            current = path.configuration
            key = (
                current.instruction,
                current.frames,
                current.state.position,
                path.row,
                tuple(
                    atom.value if isinstance(atom, Literal) else atom.source
                    for atom in path.atoms
                ),
            )
            if key in seen:
                continue
            seen.add(key)
            work += 1
            node = self.automaton.states[current.instruction]
            if node.kind == "accept":
                if path.atoms and self.similarity.relevant(query, path.atoms):
                    score = self.similarity.score(query, path)
                    if score[0] <= 600 or self.similarity.strong_root_typo(
                        query, path.atoms
                    ):
                        best = min(best, score) if best is not None else score
                continue
            if node.kind != "atom":
                for following in self.flow.follow(node, current):
                    heapq.heappush(
                        queue,
                        (
                            min(path.row),
                            next(serial),
                            replace(path, configuration=following),
                        ),
                    )
                continue
            atom = node.atom
            assert isinstance(atom, (Literal, Parameter))
            if not path.atoms and isinstance(atom, Parameter):
                continue
            if len(path.atoms) >= len(query) + 3:
                continue
            next_path = path.consuming(node, query, self.similarity)
            heapq.heappush(queue, (min(next_path.row), next(serial), next_path))
        return best, work
