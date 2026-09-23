"""Compose document parsing, cached programs, and pair preparation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from vrp_format_matcher.comparison.determinism import PredictiveExpression
from vrp_format_matcher.comparison.languages import compare, display, intersection
from vrp_format_matcher.comparison.structure import canonical_key
from vrp_format_matcher.comparison.witnesses import ProgramWords
from vrp_format_matcher.documents.catalog import Documentation, MetadataDocument
from vrp_format_matcher.documents.parameters import structural_key
from vrp_format_matcher.models import (
    Comparison,
    MappingLimitExceeded,
    MappingLimits,
    PatternProgram,
    PreparedPair,
)
from vrp_format_matcher.runtime.prepared import PreparedMetadata
from vrp_parser_automaton.api import CommandLineParser
from vrp_parser_automaton.automata.model import CommandAutomaton, PatternSource
from vrp_parser_automaton.patterns import Sequence as PatternSequence

from .programs import ProgramCompiler


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
    def unambiguous(self) -> bool:
        return PredictiveExpression(self.ast).unambiguous()


@dataclass(frozen=True)
class PairPreparation:
    document: MetadataDocument
    device: PatternSource
    document_pattern: CompiledPattern
    device_pattern: CompiledPattern
    limits: MappingLimits

    def prepared(self) -> PreparedPair:
        machine = None
        program = None
        strategy = "intersection"
        try:
            document_machine = self.document_pattern.build.require()
            device_machine = self.device_pattern.build.require()
            same_structure = (
                self.document_pattern.structure == self.device_pattern.structure
            )
            same_canonical = (
                self.document_pattern.canonical == self.device_pattern.canonical
            )
            if same_canonical:
                witness = ProgramWords(
                    self.document_pattern.ast, self.limits.automaton_states
                ).shortest()
                comparison = Comparison(
                    "equivalent",
                    structurally_identical=same_structure,
                    common_example=display(witness) if witness is not None else None,
                )
                if (
                    self.document_pattern.unambiguous
                    and self.device_pattern.unambiguous
                ):
                    program = ProgramCompiler(self.limits.product_states).paired(
                        self.document_pattern.ast,
                        self.device_pattern.ast,
                        device_machine,
                    )
                    strategy = "structural"
            else:
                comparison = compare(
                    document_machine,
                    device_machine,
                    maximum_states=self.limits.comparison_states,
                    structurally_identical=same_structure,
                )
            if program is None and comparison.common_example is not None:
                machine = intersection(
                    document_machine,
                    device_machine,
                    maximum_states=self.limits.product_states,
                )
        except MappingLimitExceeded as error:
            comparison = Comparison("unknown", reason=str(error))
        return PreparedPair(
            document_id=self.document.document_id,
            document_format=self.document.format,
            pattern_id=self.device.pattern_id,
            device_format=self.device.original,
            comparison=comparison,
            creates=self.document.creates,
            requires=self.document.requires,
            automaton=machine,
            program=program,
            strategy=strategy,
        )


class FormatMatcher:
    def __init__(self, limits: MappingLimits | None = None) -> None:
        self.limits = limits or MappingLimits()

    def compare(self, document_format: str, device_format: str) -> Comparison:
        """Compare one documentation format with one built-in device format."""
        parser = CommandLineParser({"commands": [device_format]})
        return self.compile(parser, [{"format": document_format}]).pairs[0].comparison

    def compile(
        self,
        device_parser: CommandLineParser,
        documents: Sequence[Mapping[str, Any]],
    ) -> PreparedMetadata:
        """Prepare every source pair with independent resource budgets."""
        devices = device_parser.automaton.patterns
        device_patterns = {
            device.pattern_id: CompiledPattern(
                device.ast,
                self.limits.automaton_states,
                document=False,
                automaton=device_parser.automaton,
                start=device_parser.automaton.starts[device.index],
            )
            for device in devices
        }
        pairs: list[PreparedPair] = []
        for document in Documentation(documents).documents():
            document_pattern = CompiledPattern(
                document.ast,
                self.limits.automaton_states,
                document=True,
            )
            pairs.extend(
                PairPreparation(
                    document=document,
                    device=device,
                    document_pattern=document_pattern,
                    device_pattern=device_patterns[device.pattern_id],
                    limits=self.limits,
                ).prepared()
                for device in devices
            )
        return PreparedMetadata(tuple(pairs), self.limits)


MetadataCompiler = FormatMatcher
