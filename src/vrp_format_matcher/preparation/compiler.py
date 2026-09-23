"""Compose document parsing, cached programs, and pair preparation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cached_property
from typing import Any, Literal

from vrp_format_matcher.comparison.execution import ProgramExecution
from vrp_format_matcher.comparison.languages import compare, intersection
from vrp_format_matcher.comparison.structure import canonical_key
from vrp_format_matcher.documents.catalog import Documentation, DocumentFormat
from vrp_format_matcher.documents.parameters import structural_key
from vrp_format_matcher.models import (
    AnalysisBudget,
    Comparison,
    DocumentMatch,
    MappingLimitExceeded,
    MappingLimits,
    ParameterCorrespondence,
    PatternProgram,
    PreparationProgress,
    PreparedMapping,
    PreparedPair,
)
from vrp_parser_automaton.api import CommandLineParser
from vrp_parser_automaton.automata.model import CommandAutomaton, PatternSource
from vrp_parser_automaton.patterns import Sequence as PatternSequence

from .candidates import CandidateIndex
from .programs import ProgramCompiler, SourceAlignment
from .sources import TargetFormats


@dataclass(frozen=True)
class ProgramBuild:
    """A cached outcome stores a failure reason, not an exception with a traceback."""

    machine: PatternProgram | None = None
    failure: str | None = None

    def require(self) -> PatternProgram:
        if self.failure is not None:
            raise MappingLimitExceeded(self.failure)
        assert self.machine is not None
        return self.machine


@dataclass(frozen=True)
class CompiledPattern:
    """One source AST, compiled at most once even when many pairs refer to it."""

    ast: PatternSequence
    maximum_states: int
    document: bool
    automaton: CommandAutomaton | None = None
    start: int = 0
    comparison_states: int = 20_000

    @cached_property
    def build(self) -> ProgramBuild:
        try:
            compiler = ProgramCompiler(self.maximum_states)
            machine = (
                compiler.existing(self.automaton, self.start, self.ast)
                if self.automaton is not None
                else compiler.single(self.ast, document=self.document)
            )
            return ProgramBuild(machine=machine)
        except MappingLimitExceeded as error:
            return ProgramBuild(failure=str(error))

    @cached_property
    def structure(self) -> tuple[object, ...]:
        return structural_key(self.ast)

    @cached_property
    def canonical(self) -> tuple[object, ...]:
        return canonical_key(self.ast)

    @cached_property
    def execution(self) -> ProgramExecution:
        """Reuse the document's frontiers across its candidate comparisons."""
        return ProgramExecution(self.build.require(), self.comparison_states)


@dataclass(frozen=True)
class PairPreparation:
    document: DocumentFormat
    device: PatternSource
    document_pattern: CompiledPattern
    device_pattern: CompiledPattern
    limits: MappingLimits

    def prepared(self) -> PreparedPair:
        machine = None
        bindings: tuple[ParameterCorrespondence, ...] = ()
        strategy = "intersection"
        budget = AnalysisBudget(self.limits.analysis_steps)
        try:
            same_structure = (
                self.document_pattern.structure == self.device_pattern.structure
            )
            same_canonical = (
                self.document_pattern.canonical == self.device_pattern.canonical
            )
            if same_canonical:
                # Slot identity is defined by source structure. Ambiguity of a
                # future CLI line must not force an offline language expansion.
                bindings = SourceAlignment(
                    self.document_pattern.ast,
                    self.device_pattern.ast,
                ).bindings()
                strategy = "structural"
                comparison = Comparison(
                    "equivalent",
                    structurally_identical=same_structure,
                )
            else:
                document_machine = self.document_pattern.build.require()
                device_machine = self.device_pattern.build.require()
                comparison = compare(
                    document_machine,
                    device_machine,
                    maximum_states=self.limits.comparison_states,
                    structurally_identical=same_structure,
                    document_execution=self.document_pattern.execution,
                    budget=budget,
                )
            if strategy != "structural" and comparison.common_example is not None:
                machine = intersection(
                    document_machine,
                    device_machine,
                    maximum_states=self.limits.product_states,
                    budget=budget,
                )
                links = {
                    ParameterCorrespondence(
                        replace(arc.document, iterations=()),
                        replace(arc.device, iterations=()),
                    )
                    for edges in machine.edges
                    for arc in edges
                    if arc.document is not None and arc.device is not None
                }
                bindings = tuple(
                    sorted(
                        links,
                        key=lambda link: (
                            int(link.document.slot_id[2:]),
                            int(link.device.slot_id[2:]),
                        ),
                    )
                )
        except MappingLimitExceeded as error:
            comparison = Comparison("unknown", reason=str(error))
        return PreparedPair(
            document_id=self.document.document_id,
            document_format=self.document.format,
            pattern_id=self.device.pattern_id,
            device_format=self.device.original,
            comparison=comparison,
            automaton=machine,
            bindings=bindings,
            strategy=strategy,
        )


class FormatMatcher:
    def __init__(self, limits: MappingLimits | None = None) -> None:
        self.limits = limits or MappingLimits()

    def compare(self, document_format: str, device_format: str) -> Comparison:
        """Compare one documentation format with one built-in device format."""
        parser = CommandLineParser({"commands": [device_format]})
        return (
            self.compile(parser, [{"format": document_format}], exhaustive=True)
            .pairs[0]
            .comparison
        )

    def compile(
        self,
        device_parser: CommandLineParser,
        documents: Sequence[Mapping[str, Any]],
        *,
        exhaustive: bool = False,
        mode: Literal["best", "all"] = "best",
        on_progress: Callable[[PreparationProgress], None] | None = None,
    ) -> PreparedMapping:
        """Prefer all canonical-equal targets; fall back to indexed intersections.

        ``mode='all'`` also investigates partial matches when exact matches exist.
        ``exhaustive=True`` bypasses both indexes for small diagnostic comparisons.
        """
        return self._compile(
            device_parser.automaton, documents, exhaustive, mode, on_progress
        )

    def compile_formats(
        self,
        device_formats: Sequence[str],
        documents: Sequence[Mapping[str, Any]],
        *,
        exhaustive: bool = False,
        mode: Literal["best", "all"] = "best",
        target_syntax: Literal["device", "document"] = "device",
        on_progress: Callable[[PreparationProgress], None] | None = None,
    ) -> PreparedMapping:
        """Offline matching also accepts named placeholders on the target side."""
        return self._compile(
            TargetFormats(device_formats, target_syntax).patterns(),
            documents,
            exhaustive,
            mode,
            on_progress,
        )

    def _compile(
        self,
        automaton: CommandAutomaton | tuple[PatternSource, ...],
        documents: Sequence[Mapping[str, Any]],
        exhaustive: bool,
        mode: Literal["best", "all"],
        on_progress: Callable[[PreparationProgress], None] | None,
    ) -> PreparedMapping:
        if mode not in {"best", "all"}:
            raise ValueError("matching mode must be 'best' or 'all'")
        graph = automaton if isinstance(automaton, CommandAutomaton) else None
        devices = (
            automaton.patterns if isinstance(automaton, CommandAutomaton) else automaton
        )
        index = None
        device_patterns = {
            device.pattern_id: CompiledPattern(
                device.ast,
                self.limits.automaton_states,
                document=False,
                automaton=graph,
                start=graph.starts[device.index] if graph is not None else 0,
            )
            for device in devices
        }
        by_shape: dict[tuple[object, ...], list[PatternSource]] = {}
        if mode == "best" and not exhaustive:
            for device in devices:
                shape = device_patterns[device.pattern_id].canonical
                by_shape.setdefault(shape, []).append(device)
        pairs: list[PreparedPair] = []
        summaries: list[DocumentMatch] = []
        if on_progress is not None:
            on_progress(PreparationProgress(0, len(documents), 0))
        for completed, document in enumerate(Documentation(documents).documents(), 1):
            document_pattern = CompiledPattern(
                document.ast,
                self.limits.automaton_states,
                document=True,
                comparison_states=self.limits.comparison_states,
            )
            if exhaustive:
                candidates = devices
            elif mode == "best" and document_pattern.canonical in by_shape:
                candidates = tuple(by_shape[document_pattern.canonical])
            else:
                if index is None:
                    index = CandidateIndex(devices)
                candidates = index.candidates(document.ast)
            prepared = tuple(
                PairPreparation(
                    document=document,
                    device=device,
                    document_pattern=document_pattern,
                    device_pattern=device_patterns[device.pattern_id],
                    limits=self.limits,
                ).prepared()
                for device in candidates
            )
            pairs.extend(prepared)
            matched_ids = tuple(
                pair.pattern_id
                for pair in prepared
                if pair.status == "equivalent"
                or pair.comparison.common_example is not None
            )
            status = (
                "matched"
                if matched_ids
                else "unknown"
                if any(pair.status == "unknown" for pair in prepared)
                else "unmatched"
            )
            summaries.append(
                DocumentMatch(
                    document.document_id, document.format, status, matched_ids
                )
            )
            if on_progress is not None:
                on_progress(PreparationProgress(completed, len(documents), len(pairs)))
        return PreparedMapping(tuple(pairs), tuple(summaries))
