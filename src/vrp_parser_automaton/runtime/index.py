"""Select distinct programs by their mandatory leading keywords."""

from __future__ import annotations

from dataclasses import dataclass, field

from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.diagnostics.progress import MatchDiagnostics
from vrp_parser_automaton.patterns import Literal
from vrp_parser_automaton.text import CommandText, ascii_lower

from .state import Candidate


@dataclass(slots=True)
class KeywordPrefix:
    """A trie edge retains original spellings for syntax diagnostics."""

    spellings: set[str] = field(default_factory=set)
    children: dict[str, KeywordPrefix] = field(default_factory=dict)
    starts: set[int] = field(default_factory=set)

    def add(self, words: tuple[str, ...], start: int) -> None:
        node = self
        for word in words:
            node = node.children.setdefault(ascii_lower(word), KeywordPrefix())
            node.spellings.add(word)
        node.starts.add(start)

    def select(
        self, command: CommandText, position: int, diagnostics: MatchDiagnostics
    ) -> set[int]:
        starts: set[int] = set()
        node = self
        while True:
            starts.update(node.starts)
            if not node.children:
                return starts
            token = command.token(position)
            word = ascii_lower(token.raw) if token else None
            # Skipped programs would fail at this same keyword. Keep their
            # expectations without traversing their preceding instructions.
            for key, child in node.children.items():
                if key != word:
                    for spelling in child.spellings:
                        diagnostics.record(
                            command.skip_space(position),
                            repr(spelling),
                            parameter_led=False,
                        )
            if token is None or word not in node.children:
                return starts
            node = node.children[word]
            position = token.end


class RecognitionIndex:
    """Execute identical formats once within the selected search scope.

    Original sources, instructions, IDs and slot positions remain untouched.
    Accepted states are expanded back to every participating source before
    resolution. Different parameter declarations never share a program.
    """

    def __init__(
        self, automaton: CommandAutomaton, excluded_starts: frozenset[int]
    ) -> None:
        allowed = {
            start for starts in automaton.literal_starts.values() for start in starts
        } | set(automaton.parameter_starts)
        allowed.difference_update(excluded_starts)
        representatives: dict[tuple[str, int], int] = {}
        selected: dict[int, int] = {}
        sources: dict[int, list[int]] = {}
        prefixes: dict[int, tuple[str, ...]] = {}
        for pattern, start in zip(automaton.patterns, automaton.starts, strict=True):
            if start not in allowed:
                continue
            # PatternSources interns ASTs by original text and annotations.
            # Identity avoids recursively hashing the same AST for every view.
            key = (pattern.original, id(pattern.ast))
            representative = representatives.setdefault(key, start)
            selected[start] = representative
            sources.setdefault(representative, []).append(pattern.index)
            if representative == start:
                words = []
                for node in pattern.ast.items:
                    if not isinstance(node, Literal):
                        break
                    words.append(node.value)
                prefixes[start] = tuple(words)
        self._sources = {indices[0]: tuple(indices) for indices in sources.values()}
        self._roots = {}
        for word, starts in automaton.literal_starts.items():
            root = KeywordPrefix()
            for start in dict.fromkeys(selected[s] for s in starts if s in selected):
                prefix = prefixes[start]
                # Groups, optional keywords and parameter-led alternatives keep
                # the original first-token index and run through the automaton.
                root.add(prefix[1:] if prefix else (), start)
            self._roots[word] = root
        self._parameters = {
            selected[start] for start in automaton.parameter_starts if start in selected
        }

    def starts(
        self, command: CommandText, diagnostics: MatchDiagnostics
    ) -> tuple[int, ...]:
        first = command.token(0)
        assert first is not None
        starts = self._parameters.copy()
        root = self._roots.get(ascii_lower(first.raw))
        if root is not None:
            starts.update(root.select(command, first.end, diagnostics))
        return tuple(sorted(starts))

    def expand(self, candidates: tuple[Candidate, ...]) -> tuple[Candidate, ...]:
        return tuple(
            Candidate(index, candidate.state)
            for candidate in candidates
            for index in self._sources[candidate.pattern_index]
        )
