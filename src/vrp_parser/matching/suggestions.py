"""Deterministic suggestions for commands that fail to match."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from vrp_parser.graph import CommandGraph, PatternSource
from vrp_parser.parameters import (
    ParameterDeclaration,
    ParameterStatus,
    ParameterTypeRegistry,
)
from vrp_parser.patterns import (
    Group,
    GroupMode,
    Literal,
    Node,
    Parameter,
    Repeat,
    Sequence,
)

from .text import ascii_lower


@dataclass(frozen=True, slots=True)
class _TemplateAtom:
    """One literal or parameter slot used only for fuzzy comparison."""

    literal: str | None = None
    declaration: ParameterDeclaration | None = None

    def __post_init__(self) -> None:
        if (self.literal is None) == (self.declaration is None):
            raise ValueError(
                "a suggestion atom must contain one literal or one parameter"
            )

    @classmethod
    def from_node(cls, node: Literal | Parameter) -> _TemplateAtom:
        if isinstance(node, Literal):
            return cls(literal=ascii_lower(node.value))
        declaration = node.declaration
        if not isinstance(declaration, ParameterDeclaration):
            raise TypeError("parameter AST contains an unknown declaration")
        return cls(declaration=declaration)


@dataclass(frozen=True, slots=True)
class _SuggestionTemplate:
    atoms: tuple[_TemplateAtom, ...]


@dataclass(frozen=True, slots=True)
class _SuggestionPattern:
    pattern_index: int
    original_pattern: str
    templates: tuple[_SuggestionTemplate, ...]


class SuggestionTemplateFactory:
    """Create a bounded set of searchable variations from one pattern AST."""

    def __init__(self, maximum_templates: int = 64) -> None:
        if maximum_templates < 1:
            raise ValueError("maximum_templates must be positive")
        self._maximum_templates = maximum_templates

    def create(
        self,
        pattern: PatternSource,
    ) -> tuple[_SuggestionTemplate, ...]:
        templates = (
            _SuggestionTemplate(atoms)
            for atoms in self._sequence(pattern.ast)
            if atoms and atoms[0].literal is not None
        )
        return tuple(dict.fromkeys(templates))

    def _sequence(
        self,
        sequence: Sequence,
    ) -> tuple[tuple[_TemplateAtom, ...], ...]:
        templates: tuple[tuple[_TemplateAtom, ...], ...] = ((),)
        for node in sequence.items:
            templates = self._product(templates, self._node(node))
            if not templates:
                break
        return templates

    def _node(
        self,
        node: Node,
    ) -> tuple[tuple[_TemplateAtom, ...], ...]:
        if isinstance(node, (Literal, Parameter)):
            return ((_TemplateAtom.from_node(node),),)
        if isinstance(node, Group):
            return self._group(node)
        if isinstance(node, Repeat):
            return self._repeat(node)
        raise TypeError(f"unsupported pattern node: {type(node).__name__}")

    def _group(
        self,
        group: Group,
    ) -> tuple[tuple[_TemplateAtom, ...], ...]:
        alternatives = tuple(
            self._sequence(alternative) for alternative in group.alternatives
        )
        flattened = tuple(
            template for alternative in alternatives for template in alternative
        )
        if group.mode in {
            GroupMode.REQUIRED_ONE,
            GroupMode.OPTIONAL_ONE,
        }:
            optional = ((),) if group.mode is GroupMode.OPTIONAL_ONE else ()
            return self._unique((*optional, *flattened))

        optional = ((),) if group.mode is GroupMode.OPTIONAL_SET else ()
        canonical = self._set_order(alternatives)
        reverse = self._set_order(tuple(reversed(alternatives)))
        return self._unique((*optional, *flattened, *canonical, *reverse))

    def _set_order(
        self,
        alternatives: tuple[
            tuple[tuple[_TemplateAtom, ...], ...],
            ...,
        ],
    ) -> tuple[tuple[_TemplateAtom, ...], ...]:
        templates: tuple[tuple[_TemplateAtom, ...], ...] = ((),)
        for alternative in alternatives:
            templates = self._product(templates, alternative)
        return templates

    def _repeat(
        self,
        repeat: Repeat,
    ) -> tuple[tuple[_TemplateAtom, ...], ...]:
        atom_templates = self._node(repeat.atom)
        counts = {repeat.minimum}
        if repeat.minimum == 0 and repeat.maximum >= 1:
            counts.add(1)
        if repeat.maximum > repeat.minimum:
            counts.add(min(repeat.maximum, max(1, repeat.minimum + 1)))

        templates: list[tuple[_TemplateAtom, ...]] = []
        for count in sorted(counts):
            repeated: tuple[tuple[_TemplateAtom, ...], ...] = ((),)
            for _ in range(count):
                repeated = self._product(repeated, atom_templates)
            templates.extend(repeated)
        return self._unique(tuple(templates))

    def _product(
        self,
        left: tuple[tuple[_TemplateAtom, ...], ...],
        right: tuple[tuple[_TemplateAtom, ...], ...],
    ) -> tuple[tuple[_TemplateAtom, ...], ...]:
        result: list[tuple[_TemplateAtom, ...]] = []
        seen: set[tuple[_TemplateAtom, ...]] = set()
        for first in left:
            for second in right:
                combined = first + second
                if combined in seen:
                    continue
                seen.add(combined)
                result.append(combined)
                if len(result) == self._maximum_templates:
                    return tuple(result)
        return tuple(result)

    def _unique(
        self,
        templates: tuple[tuple[_TemplateAtom, ...], ...],
    ) -> tuple[tuple[_TemplateAtom, ...], ...]:
        return tuple(dict.fromkeys(templates))[: self._maximum_templates]


class SuggestionCatalog:
    """Index literal-led pattern variations by their searchable keywords."""

    def __init__(
        self,
        graph: CommandGraph,
        template_factory: SuggestionTemplateFactory | None = None,
    ) -> None:
        factory = template_factory or SuggestionTemplateFactory()
        entries: list[_SuggestionPattern] = []
        by_root: dict[str, set[int]] = {}

        for pattern in graph.patterns:
            templates = factory.create(pattern)
            if not templates:
                continue
            entry_index = len(entries)
            entries.append(
                _SuggestionPattern(
                    pattern_index=pattern.index,
                    original_pattern=pattern.original,
                    templates=templates,
                )
            )
            for template in templates:
                root = template.atoms[0].literal
                if root is not None:
                    by_root.setdefault(root, set()).add(entry_index)

        self._entries = tuple(entries)
        self._by_root = {
            keyword: frozenset(indices) for keyword, indices in by_root.items()
        }
        self._root_keywords = tuple(sorted(self._by_root))

    @property
    def root_keywords(self) -> tuple[str, ...]:
        return self._root_keywords

    def candidates(
        self,
        nearby_roots: tuple[str, ...],
    ) -> tuple[_SuggestionPattern, ...]:
        indices: set[int] = set()
        for root in nearby_roots:
            indices.update(self._by_root.get(root, ()))
        return tuple(self._entries[index] for index in sorted(indices))


class TokenDistance:
    """Measure token typos with adjacent transpositions counted once."""

    _SCALE = 1000

    def distance(self, left: str, right: str) -> int:
        if left == right:
            return 0
        if not left:
            return len(right)
        if not right:
            return len(left)

        previous_previous: list[int] | None = None
        previous = list(range(len(right) + 1))
        for left_index, left_character in enumerate(left, start=1):
            current = [0] * (len(right) + 1)
            current[0] = left_index
            for right_index, right_character in enumerate(right, start=1):
                substitution = previous[right_index - 1] + (
                    left_character != right_character
                )
                current[right_index] = min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    substitution,
                )
                if (
                    previous_previous is not None
                    and left_index > 1
                    and right_index > 1
                    and left_character == right[right_index - 2]
                    and left[left_index - 2] == right_character
                ):
                    current[right_index] = min(
                        current[right_index],
                        previous_previous[right_index - 2] + 1,
                    )
            previous_previous, previous = previous, current
        return previous[len(right)]

    def cost(self, left: str, right: str) -> int:
        length = max(len(left), len(right), 1)
        return min(
            self._SCALE,
            round(self._SCALE * self.distance(left, right) / length),
        )

    def similarity(self, left: str, right: str) -> int:
        return self._SCALE - self.cost(left, right)


@dataclass(frozen=True, slots=True, order=True)
class _SimilarityScore:
    normalized_cost: int
    negative_exact_prefix: int
    negative_exact_matches: int
    token_count_delta: int


class CommandSimilarity:
    """Compare concrete CLI tokens with literal/parameter template atoms."""

    _MAX_RELEVANT_COST = 600
    _MIN_TOKEN_SIMILARITY = 550
    _MIN_STRONG_ROOT_SIMILARITY = 700
    _IDENTICAL_TOKEN_SIMILARITY = 1000
    _EXTRA_TOKEN_COST = 900
    _MISSING_LITERAL_COST = 900
    _MISSING_PARAMETER_COST = 750
    _TRANSPOSED_LITERAL_COST = 400
    _VALID_PARAMETER_COST = 100
    _INVALID_PARAMETER_COST = 350
    _NOT_APPLICABLE_PARAMETER_COST = 900
    _UNKNOWN_PARAMETER_COST = 1000

    def __init__(
        self,
        parameter_types: ParameterTypeRegistry,
        token_distance: TokenDistance | None = None,
    ) -> None:
        self._parameter_types = parameter_types
        self._token_distance = token_distance or TokenDistance()

    def score(
        self,
        query_tokens: tuple[str, ...],
        template: _SuggestionTemplate,
    ) -> _SimilarityScore:
        atoms = template.atoms
        costs = [[0] * (len(query_tokens) + 1) for _ in range(len(atoms) + 1)]
        for query_index in range(1, len(query_tokens) + 1):
            costs[0][query_index] = costs[0][query_index - 1] + self._EXTRA_TOKEN_COST
        for atom_index, atom in enumerate(atoms, start=1):
            costs[atom_index][0] = costs[atom_index - 1][0] + self._missing_cost(atom)
            for query_index, token in enumerate(query_tokens, start=1):
                costs[atom_index][query_index] = min(
                    costs[atom_index - 1][query_index] + self._missing_cost(atom),
                    costs[atom_index][query_index - 1] + self._EXTRA_TOKEN_COST,
                    costs[atom_index - 1][query_index - 1]
                    + self._substitution_cost(atom, token),
                )
                if self._is_transposition(
                    atoms,
                    query_tokens,
                    atom_index,
                    query_index,
                ):
                    costs[atom_index][query_index] = min(
                        costs[atom_index][query_index],
                        costs[atom_index - 2][query_index - 2]
                        + self._TRANSPOSED_LITERAL_COST,
                    )

        size = max(len(atoms), len(query_tokens), 1)
        normalized = round(costs[-1][-1] / size)
        exact_matches = self._exact_literal_matches(query_tokens, atoms)
        exact_prefix = self._exact_literal_prefix(query_tokens, atoms)
        return _SimilarityScore(
            normalized_cost=normalized,
            negative_exact_prefix=-exact_prefix,
            negative_exact_matches=-exact_matches,
            token_count_delta=abs(len(atoms) - len(query_tokens)),
        )

    def is_relevant(
        self,
        query_tokens: tuple[str, ...],
        template: _SuggestionTemplate,
        score: _SimilarityScore,
    ) -> bool:
        if not self.could_be_relevant(query_tokens, template):
            return self.root_typo_is_strong(query_tokens, template)
        first_literal = template.atoms[0].literal
        if first_literal is None:
            return False
        if self.root_typo_is_strong(query_tokens, template):
            return True
        return score.normalized_cost <= self._MAX_RELEVANT_COST

    def could_be_relevant(
        self,
        query_tokens: tuple[str, ...],
        template: _SuggestionTemplate,
    ) -> bool:
        if not query_tokens:
            return False
        atoms = template.atoms
        first_literal = atoms[0].literal
        if first_literal is None:
            return False
        first_similarity = self._token_distance.similarity(
            query_tokens[0],
            first_literal,
        )
        if first_similarity < self._MIN_TOKEN_SIMILARITY:
            return False
        if len(query_tokens) == 1:
            return True
        if len(atoms) == 1:
            return True

        query_suffix = query_tokens[1:]
        literal_suffix = tuple(
            atom.literal for atom in atoms[1:] if atom.literal is not None
        )
        if set(query_suffix) & set(literal_suffix):
            return True

        for token, atom in zip(query_suffix, atoms[1:], strict=False):
            if atom.literal is not None:
                if (
                    self._token_distance.similarity(token, atom.literal)
                    >= self._MIN_TOKEN_SIMILARITY
                ):
                    return True
                continue
            declaration = atom.declaration
            if (
                declaration is not None
                and self._parameter_types.probe(token, declaration).status
                is not ParameterStatus.NOT_APPLICABLE
            ):
                return True
        return False

    def root_typo_is_strong(
        self,
        query_tokens: tuple[str, ...],
        template: _SuggestionTemplate,
    ) -> bool:
        if not query_tokens:
            return False
        first_literal = template.atoms[0].literal
        if first_literal is None:
            return False
        similarity = self._token_distance.similarity(
            query_tokens[0],
            first_literal,
        )
        return (
            self._MIN_STRONG_ROOT_SIMILARITY
            <= similarity
            < self._IDENTICAL_TOKEN_SIMILARITY
        )

    def root_is_near(self, query: str, root: str) -> bool:
        allowed_distance = 1 if len(query) <= 4 else 2 if len(query) <= 8 else 3
        return (
            self._token_distance.distance(query, root) <= allowed_distance
            and self._token_distance.similarity(query, root)
            >= self._MIN_TOKEN_SIMILARITY
        )

    def _substitution_cost(self, atom: _TemplateAtom, token: str) -> int:
        if atom.literal is not None:
            return self._token_distance.cost(atom.literal, token)
        declaration = atom.declaration
        if declaration is None:
            return self._UNKNOWN_PARAMETER_COST
        status = self._parameter_types.probe(token, declaration).status
        if status is ParameterStatus.VALID:
            return self._VALID_PARAMETER_COST
        if status is ParameterStatus.INVALID:
            return self._INVALID_PARAMETER_COST
        return self._NOT_APPLICABLE_PARAMETER_COST

    def _missing_cost(self, atom: _TemplateAtom) -> int:
        if atom.literal is not None:
            return self._MISSING_LITERAL_COST
        return self._MISSING_PARAMETER_COST

    @staticmethod
    def _is_transposition(
        atoms: tuple[_TemplateAtom, ...],
        query_tokens: tuple[str, ...],
        atom_index: int,
        query_index: int,
    ) -> bool:
        if atom_index < 2 or query_index < 2:
            return False
        first = atoms[atom_index - 2].literal
        second = atoms[atom_index - 1].literal
        return (
            first is not None
            and second is not None
            and first == query_tokens[query_index - 1]
            and second == query_tokens[query_index - 2]
        )

    @staticmethod
    def _exact_literal_matches(
        query_tokens: tuple[str, ...],
        atoms: tuple[_TemplateAtom, ...],
    ) -> int:
        query_counts = Counter(query_tokens)
        literal_counts = Counter(
            atom.literal for atom in atoms if atom.literal is not None
        )
        return sum((query_counts & literal_counts).values())

    @staticmethod
    def _exact_literal_prefix(
        query_tokens: tuple[str, ...],
        atoms: tuple[_TemplateAtom, ...],
    ) -> int:
        matched = 0
        for token, atom in zip(query_tokens, atoms, strict=False):
            if atom.literal is None or atom.literal != token:
                break
            matched += 1
        return matched


class CommandSuggester:
    """Return up to five relevant original patterns in deterministic order."""

    _MAXIMUM_SUGGESTIONS = 5
    _MAXIMUM_CACHE_ENTRIES = 256

    def __init__(
        self,
        graph: CommandGraph,
        parameter_types: ParameterTypeRegistry,
        catalog: SuggestionCatalog | None = None,
        similarity: CommandSimilarity | None = None,
    ) -> None:
        self._catalog = catalog or SuggestionCatalog(graph)
        self._similarity = similarity or CommandSimilarity(parameter_types)
        self._cache: dict[tuple[str, ...], tuple[str, ...]] = {}

    def suggest(self, command: str, limit: int = 5) -> tuple[str, ...]:
        if limit < 1:
            return ()
        query_tokens = tuple(ascii_lower(token) for token in command.split())
        if not query_tokens:
            return ()
        cached = self._cache.get(query_tokens)
        if cached is None:
            cached = self._rank(query_tokens)
            if len(self._cache) >= self._MAXIMUM_CACHE_ENTRIES:
                self._cache.pop(next(iter(self._cache)))
            self._cache[query_tokens] = cached
        return cached[:limit]

    def _rank(self, query_tokens: tuple[str, ...]) -> tuple[str, ...]:
        nearby_roots = tuple(
            root
            for root in self._catalog.root_keywords
            if self._similarity.root_is_near(query_tokens[0], root)
        )
        candidates = self._catalog.candidates(nearby_roots)
        ranked: list[tuple[_SimilarityScore, int, str]] = []
        normalized_command = " ".join(query_tokens)
        searchable = self._searchable(query_tokens, candidates)

        for candidate, templates in searchable:
            scored = tuple(
                (self._similarity.score(query_tokens, template), template)
                for template in templates
            )
            score, template = min(scored, key=lambda item: item[0])
            if not self._similarity.is_relevant(
                query_tokens,
                template,
                score,
            ):
                continue
            if (
                ascii_lower(" ".join(candidate.original_pattern.split()))
                == normalized_command
            ):
                continue
            ranked.append(
                (
                    score,
                    candidate.pattern_index,
                    candidate.original_pattern,
                )
            )

        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        suggestions: list[str] = []
        seen: set[str] = set()
        for _, _, original in ranked:
            if original in seen:
                continue
            seen.add(original)
            suggestions.append(original)
            if len(suggestions) == self._MAXIMUM_SUGGESTIONS:
                break
        return tuple(suggestions)

    def _searchable(
        self,
        query_tokens: tuple[str, ...],
        candidates: tuple[_SuggestionPattern, ...],
    ) -> tuple[
        tuple[_SuggestionPattern, tuple[_SuggestionTemplate, ...]],
        ...,
    ]:
        preferred = tuple(
            (candidate, templates)
            for candidate in candidates
            if (
                templates := tuple(
                    template
                    for template in candidate.templates
                    if self._similarity.could_be_relevant(
                        query_tokens,
                        template,
                    )
                )
            )
        )
        if preferred:
            return preferred
        return tuple(
            (candidate, templates)
            for candidate in candidates
            if (
                templates := tuple(
                    template
                    for template in candidate.templates
                    if self._similarity.root_typo_is_strong(
                        query_tokens,
                        template,
                    )
                )
            )
        )
