"""View-specific start indexes over shared instructions and global source IDs."""

from __future__ import annotations

from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.catalogs.source import PatternCatalog
from vrp_parser_automaton.parameters import ParameterTypeRegistry
from vrp_parser_automaton.runtime.matcher import CommandMatcher


class ViewMatchers:
    def __init__(
        self,
        graph: CommandAutomaton,
        catalog: PatternCatalog,
        registry: ParameterTypeRegistry,
    ) -> None:
        self._graph = graph
        self._registry = registry
        self._starts: dict[str, set[int]] = {view: set() for view in catalog.views}
        for command, start in zip(catalog.commands, graph.starts, strict=True):
            if command.view is not None:
                self._starts[command.view].add(start)
        self._matchers: dict[str | None, CommandMatcher] = {
            None: CommandMatcher(graph, registry)
        }
        self._outside_matchers: dict[str, CommandMatcher] = {}
        self._entry = catalog.entry_view
        self._global = (
            set() if catalog.global_view is None else self._starts[catalog.global_view]
        )
        self._partitions: dict[bool, CommandMatcher] = {}

    def for_partition(self, nested: bool) -> CommandMatcher:
        """Keep system and non-system entry points strictly separate."""
        if nested not in self._partitions:
            allowed = set().union(
                *(
                    starts
                    for view, starts in self._starts.items()
                    if (view != self._entry) == nested
                )
            )
            self._partitions[nested] = self._restricted(allowed | self._global)
        return self._partitions[nested]

    def outside_view(self, view: str) -> CommandMatcher:
        """Reuse the global graph, excluding only the selected view's entry points."""
        if view not in self._starts:
            raise ValueError(f"unknown view: {view!r}")
        if view not in self._outside_matchers:
            self._outside_matchers[view] = CommandMatcher(
                self._graph,
                self._registry,
                excluded_starts=frozenset(self._starts[view] | self._global),
            )
        return self._outside_matchers[view]

    def for_view(self, view: str | None) -> CommandMatcher:
        if view not in self._matchers:
            if view not in self._starts:
                raise ValueError(f"unknown view: {view!r}")
            self._matchers[view] = self._restricted(self._starts[view] | self._global)
        return self._matchers[view]

    def _restricted(self, allowed: set[int]) -> CommandMatcher:
        # Keep global pattern indices and IDs even when searching a subset.
        graph = CommandAutomaton.create(
            self._graph.states,
            self._graph.patterns,
            self._graph.starts,
            {
                word: selected
                for word, starts in self._graph.literal_starts.items()
                if (selected := tuple(start for start in starts if start in allowed))
            },
            tuple(start for start in self._graph.parameter_starts if start in allowed),
        )
        return CommandMatcher(graph, self._registry)
