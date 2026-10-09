"""Public offline matching API; the staged pipeline owns catalog processing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from typing import Any, Literal

from vrp_format_matcher.documents.catalog import DocumentSource
from vrp_format_matcher.hierarchy import HierarchyAnalysis
from vrp_format_matcher.hierarchy.catalogs import MatchedCatalog
from vrp_format_matcher.hierarchy.models import ViewTarget
from vrp_format_matcher.hierarchy.recovery import HierarchyRecovery
from vrp_format_matcher.models import (
    CatalogSources,
    Comparison,
    FormatError,
    MappingLimits,
    PreparationProgress,
    PreparedMapping,
)
from vrp_parser_automaton.api import CommandLineParser

from .catalog import PreparedCatalog
from .pairs import CompiledPattern, PairPreparation
from .pipeline import PreparationPipeline
from .sources import CommandCatalog, TargetFormats
from .views import ViewScope


class FormatMatcher:
    def __init__(
        self,
        limits: MappingLimits | None = None,
        *,
        context_mode: Literal["system", "hierarchy"] = "system",
    ) -> None:
        if context_mode not in {"system", "hierarchy"}:
            raise ValueError("context_mode must be 'system' or 'hierarchy'")
        self.limits = limits or MappingLimits()
        self.context_mode = context_mode

    def compare(self, document_format: str, device_format: str) -> Comparison:
        """Diagnose the full-language relationship of one explicitly selected pair."""
        document = DocumentSource({"format": document_format}, "doc:0").parsed()
        device = TargetFormats([device_format]).patterns()[0]
        return (
            PairPreparation(
                document,
                device,
                CompiledPattern(
                    document.ast, self.limits.automaton_states, document=True
                ),
                CompiledPattern(
                    device.ast, self.limits.automaton_states, document=False
                ),
                self.limits,
            )
            .prepared()
            .comparison
        )

    def compile(
        self,
        device_parser: CommandLineParser,
        documents: Sequence[Mapping[str, Any]],
        *,
        on_progress: Callable[[PreparationProgress], None] | None = None,
    ) -> PreparedMapping:
        """Keep every full match; use prefix fallback for remaining devices."""
        return PreparationPipeline(
            device_parser.automaton.patterns,
            documents,
            self.limits,
            on_progress,
            device_parser.automaton,
        ).prepare()

    def compile_formats(
        self,
        device_formats: Sequence[str],
        documents: Sequence[Mapping[str, Any]],
        *,
        target_syntax: Literal["device", "document"] = "device",
        on_progress: Callable[[PreparationProgress], None] | None = None,
    ) -> PreparedMapping:
        """Parse offline inputs, then find all full matches and prefix fallbacks."""
        return PreparationPipeline(
            TargetFormats(device_formats, target_syntax).patterns(),
            documents,
            self.limits,
            on_progress,
        ).prepare()

    def compile_catalogs(
        self,
        device_catalog: Mapping[str, Any],
        documentation_catalog: Mapping[str, Any],
        *,
        on_progress: Callable[[PreparationProgress], None] | None = None,
    ) -> PreparedMapping:
        """Match v1 catalogs inside system/non-system scopes by default."""
        prepared, _, _, _ = self._catalogs(
            device_catalog, documentation_catalog, on_progress
        )
        return prepared

    def prepare_catalogs(
        self,
        device_catalog: Mapping[str, Any],
        documentation_catalog: Mapping[str, Any],
        *,
        on_progress: Callable[[PreparationProgress], None] | None = None,
    ) -> PreparedCatalog:
        """Return the runtime catalog and mapping; hierarchy recovery is opt-in."""
        prepared, pipeline, device, documentation = self._catalogs(
            device_catalog, documentation_catalog, on_progress
        )
        if self.context_mode == "system":
            return PreparedCatalog(deepcopy(dict(device_catalog)), prepared)
        if prepared.hierarchy is None:
            if device.info["type"] == "grouped":
                raise FormatError("hierarchy recovery requires grouped documentation")
            return PreparedCatalog(deepcopy(dict(device_catalog)), prepared)
        recovery = HierarchyRecovery(
            MatchedCatalog.bind(device_catalog, prepared.device_catalog),
            MatchedCatalog.bind(documentation_catalog, prepared.documentation_catalog),
            prepared,
            pipeline.coverage(),
        ).recover()
        final = pipeline.refine(
            prepared, ViewScope.recovered(device, documentation, recovery.references)
        )
        hierarchy = HierarchyAnalysis(
            device_catalog,
            documentation_catalog,
            final,
            complete_documentation=True,
            coverage=recovery.coverage,
        ).resolve()
        targets = dict(hierarchy.targets)
        recovered_targets = recovery.targets
        for reference, target in targets.items():
            if reference in recovered_targets:
                targets[reference] = ViewTarget(
                    "resolved", target.candidates, recovered_targets[reference]
                )
        final = replace(
            final,
            hierarchy=replace(
                hierarchy, targets=targets, resolved_views=recovery.views
            ),
        )
        return PreparedCatalog.recovered(device_catalog, final, recovery)

    def _catalogs(
        self,
        device_catalog: Mapping[str, Any],
        documentation_catalog: Mapping[str, Any],
        on_progress: Callable[[PreparationProgress], None] | None,
    ) -> tuple[PreparedMapping, PreparationPipeline, CommandCatalog, CommandCatalog]:
        recover = self.context_mode == "hierarchy"
        device = CommandCatalog.read(device_catalog, validate_transitions=recover)
        documentation = CommandCatalog.read(
            documentation_catalog, validate_transitions=recover
        )
        if documentation.info["source"] != "documentation":
            raise FormatError("documentation catalog source must be documentation")
        syntax: Literal["device", "document"] = (
            "document" if device.info["source"] == "documentation" else "device"
        )
        targets = TargetFormats(
            [command["format"] for command in device.commands],
            syntax,
            device.commands if syntax == "document" else None,
        )
        pipeline = PreparationPipeline(
            targets.patterns(),
            documentation.commands,
            self.limits,
            on_progress,
            view_scope=(
                ViewScope.between(device, documentation)
                if recover
                else ViewScope.partitioned(device, documentation)
            ),
        )
        prepared = pipeline.prepare()
        located = replace(
            prepared,
            device_catalog=CatalogSources(
                device.info,
                dict(zip(prepared.devices, device.locations, strict=True)),
            ),
            documentation_catalog=CatalogSources(
                documentation.info,
                {
                    f"doc:{i}": location
                    for i, location in enumerate(documentation.locations)
                },
            ),
        )
        if recover and device.info["type"] == documentation.info["type"] == "grouped":
            located = replace(
                located,
                hierarchy=HierarchyAnalysis(
                    device_catalog, documentation_catalog, located
                ).resolve(),
            )
        return located, pipeline, device, documentation
