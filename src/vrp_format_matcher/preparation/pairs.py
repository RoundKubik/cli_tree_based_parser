"""Prepare one pair, preserving source bindings and the scope of its match."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property

from vrp_format_matcher.comparison.languages import compare, intersection
from vrp_format_matcher.comparison.structure import canonical_key
from vrp_format_matcher.documents.catalog import DocumentFormat
from vrp_format_matcher.documents.parameters import structural_key
from vrp_format_matcher.models import (
    AnalysisBudget,
    Automaton,
    MappingLimitExceeded,
    MappingLimits,
    ParameterCorrespondence,
    PatternProgram,
    PreparedPair,
)
from vrp_parser_automaton.automata.model import CommandAutomaton, PatternSource
from vrp_parser_automaton.patterns import Sequence as PatternSequence

from .programs import ProgramCompiler, SourceAlignment


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


@dataclass(frozen=True)
class PairPreparation:
    document: DocumentFormat
    device: PatternSource
    document_pattern: CompiledPattern
    device_pattern: CompiledPattern
    limits: MappingLimits

    def prepared(self) -> PreparedPair:
        same_structure = (
            self.document_pattern.structure == self.device_pattern.structure
        )
        if (
            same_structure
            or self.document_pattern.canonical == self.device_pattern.canonical
        ):
            return self._result(
                "equivalent",
                SourceAlignment(
                    self.document_pattern.ast,
                    self.device_pattern.ast,
                    ordered=same_structure,
                ).bindings(),
                "structural",
                same_structure,
            )
        machine = None
        bindings: tuple[ParameterCorrespondence, ...] = ()
        status = "unknown"
        try:
            document = self.document_pattern.build.require()
            device = self.device_pattern.build.require()
            # Finish the useful result first. An expensive inclusion proof must
            # not discard an already complete intersection and its bindings.
            common = intersection(
                document,
                device,
                maximum_states=self.limits.product_states,
                budget=AnalysisBudget(self.limits.analysis_steps),
            )
            if common.edges[common.start]:
                machine = common
                bindings = self._bindings(common)
                status = "matched"
            status = compare(
                document,
                device,
                maximum_states=self.limits.comparison_states,
                budget=AnalysisBudget(self.limits.analysis_steps),
            ).relation
        except MappingLimitExceeded:
            pass
        return self._result(
            status,
            bindings,
            "path_dependent" if machine is not None else "unavailable",
            same_structure,
            machine,
        )

    def prefix(self) -> PreparedPair:
        """Bind only the common, nonempty beginning of unfinished traces."""
        try:
            machine = intersection(
                self.document_pattern.build.require(),
                self.device_pattern.build.require(),
                maximum_states=self.limits.product_states,
                budget=AnalysisBudget(self.limits.analysis_steps),
                prefixes=True,
            )
        except MappingLimitExceeded:
            return self._result("unknown", (), "unavailable", False, stage="prefix")
        if not machine.edges[machine.start]:
            return self._result("disjoint", (), "unavailable", False, stage="prefix")
        return self._result(
            "prefix_match",
            self._bindings(machine),
            "prefix_dependent",
            False,
            machine,
            stage="prefix",
        )

    @staticmethod
    def _bindings(machine: Automaton) -> tuple[ParameterCorrespondence, ...]:
        links = {
            ParameterCorrespondence(
                replace(arc.document, iterations=()),
                replace(arc.device, iterations=()),
            )
            for edges in machine.edges
            for arc in edges
            if arc.document is not None and arc.device is not None
        }
        return tuple(
            sorted(
                links,
                key=lambda link: (
                    int(link.device.slot_id[2:]),
                    int(link.document.slot_id[2:]),
                ),
            )
        )

    def _result(
        self,
        status: str,
        bindings: tuple[ParameterCorrespondence, ...],
        mode: str,
        identical: bool,
        machine: Automaton | None = None,
        stage: str | None = None,
    ) -> PreparedPair:
        return PreparedPair(
            document_id=self.document.document_id,
            document_format=self.document.format,
            pattern_id=self.device.pattern_id,
            device_format=self.device.original,
            status=status,
            bindings=bindings,
            binding_mode=mode,
            structurally_identical=identical,
            automaton=machine,
            stage=stage
            or (
                "exact"
                if identical
                else "reordered"
                if mode == "structural"
                else "intersection"
            ),
        )
