"""Compose recognition, final candidate resolution, and failure reporting."""

from __future__ import annotations

from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.diagnostics.progress import MatchDiagnostics
from vrp_parser_automaton.diagnostics.runtime_errors import CommandErrorFactory
from vrp_parser_automaton.diagnostics.suggestions import CommandSuggester
from vrp_parser_automaton.parameters import ParameterTypeRegistry
from vrp_parser_automaton.results import ParseError
from vrp_parser_automaton.text import CommandText

from .recognition import CommandRecognition
from .resolution import MatchResolver, ResolvedMatch


class CommandMatcher:
    def __init__(
        self,
        automaton: CommandAutomaton,
        parameter_types: ParameterTypeRegistry,
        *,
        excluded_starts: frozenset[int] = frozenset(),
    ) -> None:
        self._automaton = automaton
        self._parameter_types = parameter_types
        self._excluded_starts = excluded_starts
        self._resolver = MatchResolver()
        self._errors = CommandErrorFactory(CommandSuggester(automaton, parameter_types))

    def match(
        self,
        text: str,
        *,
        span_offset: int = 0,
        valid_only: bool = False,
        retain_all: bool = False,
    ) -> ResolvedMatch | ParseError:
        diagnostics = MatchDiagnostics()
        candidates = CommandRecognition(
            self._automaton,
            self._parameter_types,
            CommandText(text),
            diagnostics,
            excluded_starts=self._excluded_starts,
        ).candidates()
        if valid_only:
            # A rejected format in one view must not hide a valid fallback in
            # another. Keep invalid candidates only to explain total failure.
            valid = tuple(item for item in candidates if not item.state.rejected)
            if valid:
                candidates = valid
        if candidates:
            return self._resolver.resolve(
                candidates,
                self._automaton,
                span_offset=span_offset,
                retain_all=retain_all,
            )
        return self._errors.create(text, diagnostics, span_offset=span_offset)
