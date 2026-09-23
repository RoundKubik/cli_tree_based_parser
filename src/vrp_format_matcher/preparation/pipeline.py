"""Four catalog passes; only unresolved device formats reach the next pass."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from functools import cached_property
from typing import Any

from vrp_format_matcher.documents.catalog import Documentation
from vrp_format_matcher.models import (
    DeviceMatch,
    MappingLimits,
    PreparationProgress,
    PreparedMapping,
    PreparedPair,
)
from vrp_parser_automaton.automata.model import CommandAutomaton, PatternSource

from .candidates import CandidateIndex, PrefixCandidateIndex
from .pairs import CompiledPattern, PairPreparation


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
        self.patterns: dict[str, CompiledPattern] = {}
        self.exact: dict[tuple[object, ...], list[PatternSource]] = {}
        for source in self.sources:
            if source.original not in self.patterns:
                self.patterns[source.original] = CompiledPattern(
                    source.ast,
                    limits.automaton_states,
                    document=True,
                )
            key = self.patterns[source.original].structure
            self.exact.setdefault(key, []).append(source)

    @cached_property
    def reordered(self) -> dict[tuple[object, ...], list[PatternSource]]:
        shapes: dict[tuple[object, ...], list[PatternSource]] = {}
        for source in self.sources:
            key = self.patterns[source.original].canonical
            shapes.setdefault(key, []).append(source)
        return shapes

    @cached_property
    def intersections(self) -> CandidateIndex:
        return CandidateIndex(self.sources)

    @cached_property
    def prefixes(self) -> PrefixCandidateIndex:
        return PrefixCandidateIndex(self.sources)

    def candidates(
        self, pattern: CompiledPattern, stage: str
    ) -> tuple[PatternSource, ...]:
        if stage == "exact":
            return tuple(self.exact.get(pattern.structure, ()))
        if stage == "reordered":
            return tuple(self.reordered.get(pattern.canonical, ()))
        if stage == "intersection":
            return self.intersections.candidates(pattern.ast)
        if stage == "prefix":
            return self.prefixes.candidates(pattern.ast)
        raise ValueError(f"unknown matching stage: {stage}")


class PreparationPipeline:
    """One offline run owns its pending devices, pair cache and progress."""

    def __init__(
        self,
        devices: tuple[PatternSource, ...],
        documents: Sequence[Mapping[str, Any]],
        limits: MappingLimits,
        on_progress: Callable[[PreparationProgress], None] | None = None,
        graph: CommandAutomaton | None = None,
    ) -> None:
        self._devices = devices
        self._documents = DocumentationIndex(documents, limits)
        self._limits = limits
        self._progress = on_progress
        self._patterns = {
            device.pattern_id: CompiledPattern(
                device.ast,
                limits.automaton_states,
                document=False,
                automaton=graph,
                start=graph.starts[device.index] if graph is not None else 0,
            )
            for device in devices
        }
        self._cache: dict[tuple[str, str, str], PreparedPair] = {}
        self._unresolved: dict[str, tuple[PreparedPair, ...]] = {}
        self._results: dict[str, DeviceMatch] = {}
        self._pair_count = 0

    def prepare(self) -> PreparedMapping:
        pending = self._devices
        stages = ("exact", "reordered", "intersection", "prefix")
        for stage in stages:
            if not pending:
                break
            pending = self._pass(pending, stage)
        # Preserve input catalog order even though devices finish at different stages.
        return PreparedMapping(
            {
                device.pattern_id: self._results[device.pattern_id]
                for device in self._devices
            }
        )

    def _pass(
        self, pending: tuple[PatternSource, ...], stage: str
    ) -> tuple[PatternSource, ...]:
        remaining = []
        self._report(stage, 0, len(pending))
        for completed, device in enumerate(pending, 1):
            pattern = self._patterns[device.pattern_id]
            pairs = tuple(
                self._pair(device, source, stage)
                for source in self._documents.candidates(pattern, stage)
            )
            if not self._resolve(device, pairs, stage):
                remaining.append(device)
            self._report(stage, completed, len(pending))
        return tuple(remaining)

    def _resolve(
        self,
        device: PatternSource,
        pairs: tuple[PreparedPair, ...],
        stage: str,
    ) -> bool:
        matches = tuple(p for p in pairs if p.binding_mode != "unavailable")
        unknown = tuple(p for p in pairs if p.status == "unknown")
        previous = self._unresolved.get(device.pattern_id, ())
        uncertain = tuple(dict.fromkeys((*previous, *unknown)))
        if matches:
            status = "partial" if stage == "prefix" else "matched"
            self._finish(device, matches + uncertain, status, stage)
            return True
        if stage == "prefix":
            status = "unknown" if uncertain else "unmatched"
            self._finish(device, uncertain, status, None)
            return True
        if uncertain:
            self._unresolved[device.pattern_id] = uncertain
        return False

    def _pair(
        self, device: PatternSource, source: PatternSource, stage: str
    ) -> PreparedPair:
        key = (
            device.original,
            source.original,
            "prefix" if stage == "prefix" else "full",
        )
        if key not in self._cache:
            pair = PairPreparation(
                self._documents.documents[source.index],
                device,
                self._documents.patterns[source.original],
                self._patterns[device.pattern_id],
                self._limits,
            )
            self._cache[key] = pair.prefix() if stage == "prefix" else pair.prepared()
        return replace(
            self._cache[key],
            document_id=source.pattern_id,
            pattern_id=device.pattern_id,
        )

    def _finish(
        self,
        device: PatternSource,
        pairs: tuple[PreparedPair, ...],
        status: str,
        stage: str | None,
    ) -> None:
        self._results[device.pattern_id] = DeviceMatch(
            device.original, status, pairs, stage
        )
        self._pair_count += len(pairs)

    def _report(self, stage: str, done: int, total: int) -> None:
        if self._progress is not None:
            self._progress(PreparationProgress(done, total, self._pair_count, stage))
