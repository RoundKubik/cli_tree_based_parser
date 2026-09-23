"""Recover parameter bindings by executing a prepared automaton."""

from __future__ import annotations

import re
from dataclasses import dataclass

from vrp_parser.graph.model import ascii_lower
from vrp_parser.results import ParameterValue

from .execution import Configuration, ProgramExecution, ProgramStep
from .models import (
    Arc,
    Automaton,
    CaptureTag,
    MappingLimitExceeded,
    MetadataError,
    ParameterBinding,
    PatternProgram,
)


@dataclass(frozen=True)
class InputAtom:
    label: str
    value: ParameterValue | None = None

    def accepts(self, arc: Arc | ProgramStep) -> bool:
        if self.label != arc.label:
            return False
        if self.label != "P":
            return True
        assert self.value is not None and arc.device is not None
        # Follow the validated device interpretation, not a doc/device type table.
        return (
            self.value.declaration == arc.device.declaration
            and self.value.type_id == arc.device.type_id
        )


@dataclass(frozen=True)
class CommandAtoms:
    """A parsed line viewed as literals and already validated parameter captures."""

    raw: str
    parameters: tuple[ParameterValue, ...]

    def read(self) -> tuple[InputAtom, ...]:
        atoms: list[InputAtom] = []
        position = 0
        for parameter in self.parameters:
            self._validate_span(parameter, position)
            atoms.extend(self._keywords(position, parameter.span.start))
            atoms.append(InputAtom("P", parameter))
            position = parameter.span.end
        atoms.extend(self._keywords(position, len(self.raw)))
        return tuple(atoms)

    def _validate_span(self, parameter: ParameterValue, position: int) -> None:
        span = parameter.span
        if span.start < position or span.end > len(self.raw):
            raise MetadataError("invalid or overlapping parameter spans")
        if self.raw[span.start : span.end] != parameter.raw:
            raise MetadataError("parameter span does not match the original line")

    def _keywords(self, start: int, end: int) -> tuple[InputAtom, ...]:
        return tuple(
            InputAtom("K:" + ascii_lower(token))
            for token in re.findall(r"\S+", self.raw[start:end])
        )


@dataclass(frozen=True)
class BoundCapture:
    """A path-local slot pairing; input position avoids hashing custom values."""

    document: CaptureTag
    device: CaptureTag
    position: int

    def resolve(self, atoms: tuple[InputAtom, ...]) -> ParameterBinding:
        value = atoms[self.position].value
        assert value is not None
        return ParameterBinding(self.document, self.device, value)


@dataclass(frozen=True)
class BindingPath:
    state: int | Configuration
    position: int = 0
    captures: tuple[BoundCapture, ...] = ()

    def follow(
        self, arc: Arc | ProgramStep, atoms: tuple[InputAtom, ...]
    ) -> BindingPath | None:
        if arc.label is None:
            return BindingPath(arc.target, self.position, self.captures)
        if self.position == len(atoms) or not atoms[self.position].accepts(arc):
            return None
        captures = self.captures
        if arc.label == "P":
            assert arc.document is not None and arc.device is not None
            captures += (BoundCapture(arc.document, arc.device, self.position),)
        return BindingPath(arc.target, self.position + 1, captures)


@dataclass(frozen=True)
class BindingSearch:
    machine: Automaton | PatternProgram
    maximum_configurations: int

    def match(
        self, atoms: tuple[InputAtom, ...]
    ) -> tuple[tuple[ParameterBinding, ...], ...]:
        execution = (
            ProgramExecution(self.machine, self.maximum_configurations)
            if isinstance(self.machine, PatternProgram)
            else None
        )
        initial = BindingPath(execution.start if execution else self._automaton().start)
        pending = [initial]
        visited = {initial}
        accepted: set[tuple[BoundCapture, ...]] = set()
        while pending:
            path = pending.pop()
            if execution is not None:
                assert isinstance(path.state, tuple)
                frontier = execution.frontier(path.state)
                accepts = frontier.accepts
                outgoing: tuple[Arc | ProgramStep, ...] = frontier.steps
            else:
                assert isinstance(path.state, int)
                machine = self._automaton()
                accepts = path.state == machine.final
                outgoing = machine.edges[path.state]
            if accepts and path.position == len(atoms):
                accepted.add(path.captures)
            for arc in outgoing:
                continuation = path.follow(arc, atoms)
                if continuation is None or continuation in visited:
                    continue
                if len(visited) >= self.maximum_configurations:
                    raise MappingLimitExceeded("binding execution limit exceeded")
                visited.add(continuation)
                pending.append(continuation)
        return tuple(
            tuple(capture.resolve(atoms) for capture in captures)
            for captures in sorted(accepted, key=self._source_order)
        )

    def _automaton(self) -> Automaton:
        assert isinstance(self.machine, Automaton)
        return self.machine

    def _source_order(self, captures: tuple[BoundCapture, ...]) -> str:
        # Source tags and input positions give an order independent of traversal.
        return repr(
            tuple(
                (capture.document, capture.device, capture.position)
                for capture in captures
            )
        )
