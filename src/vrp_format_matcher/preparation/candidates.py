"""A bounded AST prefix cover and a trie of potentially intersecting formats."""

from __future__ import annotations

from dataclasses import dataclass, field

from vrp_format_matcher.comparison.structure import Expression
from vrp_parser_automaton.automata.model import PatternSource
from vrp_parser_automaton.patterns import (
    GroupMode,
    Literal,
    Parameter,
    Repeat,
    Sequence,
)
from vrp_parser_automaton.text import ascii_lower


@dataclass(frozen=True)
class Prefix:
    symbols: tuple[str, ...] = ()
    complete: bool = False


@dataclass(frozen=True)
class PrefixCover:
    """Over-approximate beginnings, never enumerate complete command routes.

    An incomplete prefix stands for every possible continuation. Exhausting
    either budget broadens the cover instead of discarding possible matches.
    """

    depth: int = 8
    width: int = 64

    def prefixes(self, expression: Expression) -> tuple[Prefix, ...]:
        return self._visit(expression, self.depth)

    def _bounded(self, prefixes: list[Prefix]) -> tuple[Prefix, ...]:
        unique = tuple(dict.fromkeys(prefixes))
        if len(unique) <= self.width:
            return unique
        # A common prefix covers all alternatives, including those beyond budget.
        common = unique[0].symbols
        for prefix in unique[1:]:
            length = 0
            for left, right in zip(common, prefix.symbols, strict=False):
                if left != right:
                    break
                length += 1
            common = common[:length]
        return (Prefix(common),)

    def _visit(self, node: Expression, depth: int) -> tuple[Prefix, ...]:
        if depth == 0:
            return (Prefix(),)
        if isinstance(node, Literal):
            return (Prefix(("K:" + ascii_lower(node.value),), True),)
        if isinstance(node, Parameter):
            return (Prefix(("P",), True),)
        if isinstance(node, Sequence):
            result: tuple[Prefix, ...] = (Prefix(complete=True),)
            for item in node.items:
                following = []
                for prefix in result:
                    if not prefix.complete:
                        following.append(prefix)
                        continue
                    for suffix in self._visit(item, depth - len(prefix.symbols)):
                        following.append(
                            Prefix(prefix.symbols + suffix.symbols, suffix.complete)
                        )
                result = self._bounded(following)
                if all(not prefix.complete for prefix in result):
                    break
            return result
        if isinstance(node, Repeat):
            if node.maximum == 0:
                return (Prefix(complete=True),)
            # Inspect only the first occurrence. Later occurrences and the suffix
            # remain unconstrained, so large repetition bounds cost no extra work.
            repeated = [Prefix(p.symbols) for p in self._visit(node.atom, depth)]
            if node.minimum == 0:
                repeated.append(Prefix(complete=True))
            return self._bounded(repeated)
        is_set = node.mode in {GroupMode.REQUIRED_SET, GroupMode.OPTIONAL_SET}
        branches: tuple[Prefix, ...] = ()
        for branch in node.alternatives:
            prefixes = self._visit(branch, depth)
            branches = self._bounded(
                [*branches, *(Prefix(p.symbols) if is_set else p for p in prefixes)]
            )
        if node.mode in {GroupMode.OPTIONAL_ONE, GroupMode.OPTIONAL_SET}:
            branches = self._bounded([*branches, Prefix(complete=True)])
        return branches


@dataclass
class PrefixNode:
    children: dict[str, PrefixNode] = field(default_factory=dict)
    terminals: set[int] = field(default_factory=set)

    def insert(self, symbols: tuple[str, ...], index: int) -> None:
        node = self
        for symbol in symbols:
            node = node.children.setdefault(symbol, PrefixNode())
        node.terminals.add(index)

    def compatible(self, symbols: tuple[str, ...]) -> set[int]:
        result = set(self.terminals)
        node = self
        for symbol in symbols:
            child = node.children.get(symbol)
            if child is None:
                return result
            node = child
            result.update(node.terminals)
        pending = list(node.children.values())
        for descendant in pending:
            result.update(descendant.terminals)
            pending.extend(descendant.children.values())
        return result


class CandidateIndex:
    """Build once for the device catalog; query without scanning the catalog."""

    def __init__(self, sources: tuple[PatternSource, ...]) -> None:
        self._sources = sources
        self._cover = PrefixCover()
        self._root = PrefixNode()
        for index, source in enumerate(sources):
            for prefix in self._cover.prefixes(source.ast):
                self._root.insert(prefix.symbols, index)

    def candidates(self, expression: Expression) -> tuple[PatternSource, ...]:
        indices: set[int] = set()
        for prefix in self._cover.prefixes(expression):
            indices.update(self._root.compatible(prefix.symbols))
        return tuple(self._sources[index] for index in sorted(indices))
