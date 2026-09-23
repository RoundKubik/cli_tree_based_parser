"""Public offline matching API; the staged pipeline owns catalog processing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from vrp_format_matcher.documents.catalog import DocumentSource
from vrp_format_matcher.models import (
    Comparison,
    MappingLimits,
    PreparationProgress,
    PreparedMapping,
)
from vrp_parser_automaton.api import CommandLineParser

from .pairs import CompiledPattern, PairPreparation
from .pipeline import PreparationPipeline
from .sources import TargetFormats


class FormatMatcher:
    def __init__(self, limits: MappingLimits | None = None) -> None:
        self.limits = limits or MappingLimits()

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
        """Match each device through exact, reordered, full and prefix stages."""
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
        """Parse offline inputs, then automatically run the four matching stages."""
        return PreparationPipeline(
            TargetFormats(device_formats, target_syntax).patterns(),
            documents,
            self.limits,
            on_progress,
        ).prepare()
