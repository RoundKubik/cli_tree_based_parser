"""All full command matches, followed by prefix fallback for unresolved devices."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from vrp_format_matcher.models import (
    DeviceMatch,
    MappingLimits,
    PreparationProgress,
    PreparedMapping,
    PreparedPair,
)
from vrp_parser_automaton.automata.model import CommandAutomaton, PatternSource

from .indexes import DocumentationIndex, PatternKey, pattern_key
from .pairs import CompiledPattern, PairPreparation
from .views import EntryViewScope


class PreparationPipeline:
    """Share compiled formats without choosing one documentation match over another."""

    def __init__(
        self,
        devices: tuple[PatternSource, ...],
        documents: Sequence[Mapping[str, Any]],
        limits: MappingLimits,
        on_progress: Callable[[PreparationProgress], None] | None = None,
        graph: CommandAutomaton | None = None,
        *,
        entry_scope: EntryViewScope | None = None,
    ) -> None:
        self._devices = devices
        self._documents = DocumentationIndex(documents, limits)
        self._limits = limits
        self._progress = on_progress
        self._entry_scope = entry_scope or EntryViewScope()
        self._keys = {device.pattern_id: pattern_key(device) for device in devices}
        self._patterns: dict[PatternKey, CompiledPattern] = {}
        for device in devices:
            key = self._keys[device.pattern_id]
            if key in self._patterns:
                continue
            self._patterns[key] = CompiledPattern(
                device.ast,
                limits.automaton_states,
                document=False,
                automaton=graph,
                start=graph.starts[device.index] if graph is not None else 0,
            )
        self._unresolved: dict[str, tuple[PreparedPair, ...]] = {}
        self._results: dict[str, DeviceMatch] = {}
        self._pair_count = 0

    def prepare(self) -> PreparedMapping:
        pending = self._devices
        stages = ("matching", "prefix")
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
        groups: dict[tuple[PatternKey, frozenset[int] | None], list[PatternSource]] = (
            defaultdict(list)
        )
        for device in pending:
            scope = self._entry_scope.documents_for(device.index)
            groups[self._keys[device.pattern_id], scope].append(device)
        completed = 0
        for devices in groups.values():
            pairs = self._pairs(devices[0], stage)
            for device in devices:
                identified = tuple(
                    pair
                    if pair.pattern_id == device.pattern_id
                    else replace(pair, pattern_id=device.pattern_id)
                    for pair in pairs
                )
                if not self._resolve(device, identified, stage):
                    remaining.append(device)
                completed += 1
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
            stages = {pair.stage for pair in matches}
            summary = next(iter(stages)) if len(stages) == 1 else "mixed"
            self._finish(device, matches + uncertain, status, summary)
            return True
        if stage == "prefix":
            status = "unknown" if uncertain else "unmatched"
            self._finish(device, uncertain, status, None)
            return True
        if uncertain:
            self._unresolved[device.pattern_id] = uncertain
        return False

    def _pairs(self, device: PatternSource, stage: str) -> tuple[PreparedPair, ...]:
        """Compute each source string/type profile once for this device and pass.

        The local cache expires before the next device format. All source IDs
        survive, while failed comparisons never accumulate across the corpus.
        """
        allowed = self._entry_scope.documents_for(device.index)
        if allowed is not None and not allowed:
            return ()
        pattern = self._patterns[self._keys[device.pattern_id]]
        cache: dict[PatternKey, PreparedPair] = {}
        results = []
        for source in self._documents.candidates(pattern, stage):
            if allowed is not None and source.index not in allowed:
                continue
            key = self._documents.keys[source.pattern_id]
            if key not in cache:
                preparation = PairPreparation(
                    self._documents.documents[source.index],
                    device,
                    self._documents.patterns[key],
                    pattern,
                    self._limits,
                )
                cache[key] = (
                    preparation.prefix()
                    if stage == "prefix"
                    else preparation.prepared()
                )
            pair = cache[key]
            if pair.binding_mode != "unavailable" or pair.status == "unknown":
                results.append(
                    pair
                    if pair.document_id == source.pattern_id
                    else replace(pair, document_id=source.pattern_id)
                )
        return tuple(results)

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
        self._unresolved.pop(device.pattern_id, None)
        self._pair_count += len(pairs)

    def _report(self, stage: str, done: int, total: int) -> None:
        if self._progress is not None:
            self._progress(PreparationProgress(done, total, self._pair_count, stage))
