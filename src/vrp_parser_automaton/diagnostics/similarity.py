"""Typo costs and relevance, separate from automaton traversal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vrp_parser_automaton.parameters import (
    ParameterDeclaration,
    ParameterStatus,
    ParameterTypeRegistry,
)
from vrp_parser_automaton.patterns import Literal, Parameter
from vrp_parser_automaton.text import ascii_lower

if TYPE_CHECKING:
    from .suggestion_search import SearchPath


class TokenDistance:
    """Damerau edit distance: a transposition counts as one typo."""

    def distance(self, left: str, right: str) -> int:
        previous_previous: list[int] | None = None
        previous = list(range(len(right) + 1))
        for i, first in enumerate(left, 1):
            current = [i] + [0] * len(right)
            for j, second in enumerate(right, 1):
                current[j] = min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (first != second),
                )
                if (
                    previous_previous is not None
                    and i > 1
                    and j > 1
                    and first == right[j - 2]
                    and left[i - 2] == second
                ):
                    current[j] = min(current[j], previous_previous[j - 2] + 1)
            previous_previous, previous = previous, current
        return previous[-1]

    def cost(self, left: str, right: str) -> int:
        return min(
            1000,
            round(1000 * self.distance(left, right) / max(len(left), len(right), 1)),
        )


class CommandSimilarity:
    def __init__(self, registry: ParameterTypeRegistry) -> None:
        self.registry = registry
        self.distance = TokenDistance()

    def cost(self, atom: Literal | Parameter, token: str) -> int:
        if isinstance(atom, Literal):
            return self.distance.cost(ascii_lower(atom.value), token)
        declaration = atom.declaration
        assert isinstance(declaration, ParameterDeclaration)
        status = self.registry.probe(token, declaration).status
        return {
            ParameterStatus.VALID: 100,
            ParameterStatus.INVALID: 350,
            ParameterStatus.NOT_APPLICABLE: 900,
        }[status]

    def strong_root_typo(
        self, query: tuple[str, ...], atoms: tuple[Literal | Parameter, ...]
    ) -> bool:
        root = atoms[0]
        return (
            isinstance(root, Literal)
            and 0 < self.distance.cost(query[0], ascii_lower(root.value)) <= 300
        )

    def relevant(
        self, query: tuple[str, ...], atoms: tuple[Literal | Parameter, ...]
    ) -> bool:
        root = atoms[0]
        if (
            not isinstance(root, Literal)
            or self.distance.cost(query[0], ascii_lower(root.value)) > 450
        ):
            return False
        if self.strong_root_typo(query, atoms) or len(query) == 1 or len(atoms) == 1:
            return True
        if set(query[1:]) & {
            ascii_lower(atom.value) for atom in atoms[1:] if isinstance(atom, Literal)
        }:
            return True
        return any(
            self.cost(atom, token) <= 450
            for atom, token in zip(atoms[1:], query[1:], strict=False)
        )

    def score(
        self, query: tuple[str, ...], path: SearchPath
    ) -> tuple[int, int, int, int]:
        literals = [
            ascii_lower(atom.value) if isinstance(atom, Literal) else None
            for atom in path.atoms
        ]
        prefix = 0
        for token, literal in zip(query, literals, strict=False):
            if token != literal:
                break
            prefix += 1
        exact = sum(min(query.count(word), literals.count(word)) for word in set(query))
        return (
            round(path.row[-1] / max(len(query), len(literals), 1)),
            -prefix,
            -exact,
            abs(len(query) - len(literals)),
        )
