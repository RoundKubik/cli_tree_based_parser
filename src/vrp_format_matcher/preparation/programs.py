"""Annotate parser instructions; never compile a second execution language."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace

from vrp_format_matcher.comparison.structure import Expression, canonical_key
from vrp_format_matcher.documents.parameters import NamedParameter
from vrp_format_matcher.models import (
    CaptureTag,
    MappingLimitExceeded,
    ParameterCorrespondence,
    PatternProgram,
    RepeatBinding,
    SlotBinding,
)
from vrp_parser_automaton.automata.building import AutomatonBuilder
from vrp_parser_automaton.automata.model import CommandAutomaton, PatternSource
from vrp_parser_automaton.parameters import ParameterDeclaration
from vrp_parser_automaton.patterns import Group, Literal, Parameter, Repeat, Sequence


@dataclass(frozen=True)
class ParameterSource:
    node: Parameter

    def tag(self) -> CaptureTag:
        declaration = self.node.declaration
        return CaptureTag(
            f"p:{self.node.span.start}",
            declaration.name if isinstance(declaration, NamedParameter) else None,
            self.node.source,
            declaration.type_id
            if isinstance(declaration, ParameterDeclaration)
            else None,
        )


@dataclass(frozen=True)
class RepeatSources:
    ast: Sequence

    def paths(self) -> dict[str, str]:
        result: dict[str, str] = {}
        self._visit(self.ast, "root", result)
        return result

    def _visit(self, expression: Expression, path: str, result: dict[str, str]) -> None:
        if isinstance(expression, Sequence):
            for index, node in enumerate(expression.items):
                self._visit(node, f"{path}.{index}", result)
        elif isinstance(expression, Group):
            for index, branch in enumerate(expression.alternatives):
                self._visit(branch, f"{path}.{index}", result)
        elif isinstance(expression, Repeat):
            result[path] = f"r:{expression.span.start}"
            self._visit(expression.atom, f"{path}.body", result)


@dataclass(frozen=True)
class ProgramCompiler:
    maximum_instructions: int

    def single(self, ast: Sequence, *, document: bool) -> PatternProgram:
        machine = AutomatonBuilder().build((PatternSource("document", 0, "", ast),))
        return self.existing(machine, machine.starts[0], ast, document=document)

    def existing(
        self,
        machine: CommandAutomaton,
        start: int,
        ast: Sequence,
        *,
        document: bool = False,
    ) -> PatternProgram:
        indices = {start: 0}
        pending = [start]
        instructions = []
        slots = []
        for index in pending:
            node = machine.states[index]
            for successor in (
                *node.branches,
                *((node.target,) if node.target >= 0 else ()),
            ):
                if successor not in indices:
                    if len(indices) >= self.maximum_instructions:
                        raise MappingLimitExceeded(
                            "automaton instruction limit exceeded"
                        )
                    indices[successor] = len(indices)
                    pending.append(successor)
            instructions.append(
                replace(
                    node,
                    target=indices[node.target] if node.target >= 0 else -1,
                    branches=tuple(indices[item] for item in node.branches),
                )
            )
            tag = (
                ParameterSource(node.atom).tag()
                if isinstance(node.atom, Parameter)
                else None
            )
            slots.append(
                SlotBinding(tag if document else None, tag if not document else None)
            )
        repeats = tuple(
            RepeatBinding(
                path, value if document else None, value if not document else None
            )
            for path, value in RepeatSources(ast).paths().items()
        )
        return PatternProgram(0, tuple(instructions), tuple(slots), repeats)


@dataclass(frozen=True)
class SourceAlignment:
    """Align source occurrences in canonical-equal ASTs, without language expansion.

    Equal-shaped alternatives are paired by occurrence order within their shape.
    This is a structural correspondence, not every possible cross-parse binding.
    """

    document: Sequence
    device: Sequence

    def bindings(self) -> tuple[ParameterCorrespondence, ...]:
        parameters: list[ParameterCorrespondence] = []
        self._align(self.document, self.device, parameters)
        return tuple(
            sorted(parameters, key=lambda item: int(item.document.slot_id[2:]))
        )

    def _align(
        self,
        document: Expression,
        device: Expression,
        parameters: list[ParameterCorrespondence],
    ) -> None:
        if isinstance(device, Parameter):
            assert isinstance(document, Parameter)
            parameters.append(
                ParameterCorrespondence(
                    ParameterSource(document).tag(), ParameterSource(device).tag()
                )
            )
        elif isinstance(device, Sequence):
            assert isinstance(document, Sequence)
            for left, right in zip(document.items, device.items, strict=True):
                self._align(left, right, parameters)
        elif isinstance(device, Repeat):
            assert isinstance(document, Repeat)
            self._align(document.atom, device.atom, parameters)
        elif isinstance(device, Group):
            assert isinstance(document, Group)
            by_shape: dict[tuple[object, ...], deque[Sequence]] = defaultdict(deque)
            for branch in document.alternatives:
                by_shape[canonical_key(branch)].append(branch)
            for branch in device.alternatives:
                self._align(
                    by_shape[canonical_key(branch)].popleft(), branch, parameters
                )
        else:
            assert isinstance(document, Literal)
