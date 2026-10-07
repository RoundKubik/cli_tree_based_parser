"""Reuse compiled formats for coverage proofs without modifying their bindings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from vrp_format_matcher.comparison.coverage import covered_by
from vrp_format_matcher.comparison.parameter_types import normalized_type
from vrp_format_matcher.documents.parameters import parameters
from vrp_format_matcher.models import (
    AnalysisBudget,
    MappingLimitExceeded,
    MappingLimits,
)
from vrp_format_matcher.preparation.pairs import CompiledPattern


class FormatCoverage:
    """Keep compact proof results; execution frontiers expire after each proof.

    Equal typed ASTs share a language ID, including repeated catalog entries with
    different source slots. The IDs are internal to proofs, never mapping IDs.
    """

    def __init__(
        self, patterns: Mapping[str, CompiledPattern], limits: MappingLimits
    ) -> None:
        self._limits = limits
        self._patterns: list[CompiledPattern] = []
        self._known: list[bool] = []
        self._ids: dict[str, int] = {}
        shapes: dict[tuple[object, ...], int] = {}
        for identifier, pattern in patterns.items():
            key = pattern.typed_canonical
            if key not in shapes:
                shapes[key] = len(self._patterns)
                self._patterns.append(pattern)
                self._known.append(
                    all(
                        normalized_type(getattr(node.declaration, "type_id", None))
                        is not None
                        for node in parameters(pattern.ast)
                    )
                )
            self._ids[identifier] = shapes[key]
        self._proofs: dict[tuple[int, frozenset[int]], bool | None] = {}

    def known(self, identifier: str) -> bool:
        return self._known[self._ids[identifier]]

    def covers(self, subject: str, alternatives: Iterable[str]) -> bool | None:
        """True proves coverage; False finds an exception; None lacks data or budget."""
        source = self._ids[subject]
        choices = frozenset(self._ids[identifier] for identifier in alternatives)
        if not self._known[source]:
            return None
        if source in choices:
            return True
        if not choices:
            return False
        key = source, choices
        if key not in self._proofs:
            self._proofs[key] = self._prove(source, choices)
        return self._proofs[key]

    def _prove(self, source: int, choices: frozenset[int]) -> bool | None:
        known = tuple(index for index in sorted(choices) if self._known[index])
        if not known:
            return None
        try:
            complete = covered_by(
                self._patterns[source].build.require(),
                tuple(self._patterns[index].build.require() for index in known),
                maximum_states=self._limits.comparison_states,
                budget=AnalysisBudget(self._limits.analysis_steps),
            )
        except MappingLimitExceeded:
            return None
        # Unknown alternatives cannot disprove coverage. Known ones can prove it.
        return complete if complete or len(known) == len(choices) else None
