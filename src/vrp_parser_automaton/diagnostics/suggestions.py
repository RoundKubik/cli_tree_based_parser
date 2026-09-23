"""Select candidate sources and cache ranked suggestions."""

from __future__ import annotations

from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.parameters import ParameterTypeRegistry
from vrp_parser_automaton.text import ascii_lower

from .similarity import CommandSimilarity, TokenDistance
from .suggestion_search import SuggestionSearch


class CommandSuggester:
    """Advisory search budgets never limit actual command recognition."""

    def __init__(
        self, automaton: CommandAutomaton, parameter_types: ParameterTypeRegistry
    ) -> None:
        self.automaton = automaton
        self.registry = parameter_types
        self.distance = TokenDistance()
        self.search = SuggestionSearch(automaton, CommandSimilarity(parameter_types))
        self.cache: dict[str, tuple[str, ...]] = {}
        self.pattern_by_start = dict(
            zip(automaton.starts, automaton.patterns, strict=True)
        )

    def suggest(self, command: str) -> tuple[str, ...]:
        if command in self.cache:
            return self.cache[command]
        tokens = tuple(ascii_lower(word) for word in command.split())
        if not tokens:
            return ()
        starts: set[int] = set()
        allowed = 1 if len(tokens[0]) <= 4 else 2 if len(tokens[0]) <= 8 else 3
        for root, entries in self.automaton.literal_starts.items():
            if (
                self.distance.distance(tokens[0], root) <= allowed
                and self.distance.cost(tokens[0], root) <= 450
            ):
                starts.update(entries)
        ranked = []
        remaining = 20_000
        for start in sorted(starts, key=lambda item: self.pattern_by_start[item].index):
            score, visited = self.search.search(start, tokens, min(600, remaining))
            remaining -= visited
            if score is not None:
                pattern = self.pattern_by_start[start]
                ranked.append((score, pattern.index, pattern.original))
            if remaining <= 0:
                break
        result = tuple(dict.fromkeys(pattern for _, _, pattern in sorted(ranked)))[:5]
        if len(self.cache) >= 256:
            self.cache.clear()
        self.cache[command] = result
        return result
