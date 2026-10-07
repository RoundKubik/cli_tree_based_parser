"""Shared documentation programs and lazy candidate indexes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cached_property
from typing import Any

from vrp_format_matcher.documents.catalog import Documentation
from vrp_format_matcher.documents.parameters import type_signature
from vrp_format_matcher.models import MappingLimits
from vrp_parser_automaton.automata.model import PatternSource

from .candidates import CandidateIndex, PrefixCandidateIndex
from .pairs import CompiledPattern

type PatternKey = tuple[str, tuple[tuple[int, str], ...]]


def pattern_key(source: PatternSource) -> PatternKey:
    return source.original, type_signature(source.ast)


class DocumentationIndex:
    """Keep all document IDs; defer fallback indexes until they are needed."""

    def __init__(
        self,
        documents: Sequence[Mapping[str, Any]],
        limits: MappingLimits,
    ) -> None:
        self.documents = tuple(Documentation(documents).documents())
        self.sources = tuple(
            PatternSource(doc.document_id, i, doc.format, doc.ast)
            for i, doc in enumerate(self.documents)
        )
        self.keys = {source.pattern_id: pattern_key(source) for source in self.sources}
        self.patterns: dict[PatternKey, CompiledPattern] = {}
        for source in self.sources:
            cache_key = self.keys[source.pattern_id]
            if cache_key not in self.patterns:
                self.patterns[cache_key] = CompiledPattern(
                    source.ast,
                    limits.automaton_states,
                    document=True,
                )

    @cached_property
    def intersections(self) -> CandidateIndex:
        return CandidateIndex(self.sources)

    @cached_property
    def prefixes(self) -> PrefixCandidateIndex:
        return PrefixCandidateIndex(self.sources)

    def candidates(
        self, pattern: CompiledPattern, stage: str
    ) -> tuple[PatternSource, ...]:
        if stage == "matching":
            return self.intersections.candidates(pattern.ast)
        if stage == "prefix":
            return self.prefixes.candidates(pattern.ast)
        raise ValueError(f"unknown matching stage: {stage}")
