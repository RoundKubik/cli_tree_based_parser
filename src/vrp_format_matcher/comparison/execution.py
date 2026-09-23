"""Structural view of the parser's control flow, retaining binding annotations."""

from __future__ import annotations

from dataclasses import dataclass, replace

from vrp_format_matcher.models import (
    AnalysisBudget,
    CaptureTag,
    MappingLimitExceeded,
    PatternProgram,
    RepeatBinding,
)
from vrp_parser_automaton.patterns import Literal
from vrp_parser_automaton.runtime.execution import Configuration, ControlFlow, Frame
from vrp_parser_automaton.runtime.state import WalkState
from vrp_parser_automaton.text import ascii_lower


@dataclass(frozen=True)
class StructuralConfiguration:
    """Discard trace-only distinctions without losing registers or source slots."""

    current: Configuration

    def normalized(self) -> Configuration:
        frames = tuple(self._frame(frame) for frame in self.current.frames)
        return Configuration(
            self.current.instruction,
            WalkState(),
            frames,
        )

    def _frame(self, frame: Frame) -> Frame:
        selection = tuple(sorted(frame.selected)) if frame.kind == "set" else (0,)
        return replace(
            frame,
            selected=selection,
            order_start=0,
            start_position=0
            if frame.start_position == self.current.state.position
            else -1,
        )


@dataclass(frozen=True, slots=True)
class ProgramStep:
    target: Configuration
    label: str
    document: CaptureTag | None = None
    device: CaptureTag | None = None


@dataclass(frozen=True, slots=True)
class Frontier:
    accepts: bool
    steps: tuple[ProgramStep, ...]


class ProgramExecution:
    def __init__(self, program: PatternProgram, maximum_configurations: int) -> None:
        self.program = program
        self.maximum = maximum_configurations
        self.start = Configuration(program.root, WalkState())
        self._cache: dict[Configuration, Frontier] = {}
        self._repeats = {item.path: item for item in program.repetitions}
        self._flow = ControlFlow()

    def frontier(
        self, state: Configuration, budget: AnalysisBudget | None = None
    ) -> Frontier:
        if budget is not None:
            budget.spend()
        if state in self._cache:
            return self._cache[state]
        result = self._visit(state, budget)
        if len(self._cache) < self.maximum:
            self._cache[state] = result
        return result

    def _visit(self, state: Configuration, budget: AnalysisBudget | None) -> Frontier:
        pending = [state]
        visited = {state}
        steps: dict[ProgramStep, None] = {}
        accepts = False
        while pending:
            if budget is not None:
                budget.spend()
            current = pending.pop()
            node = self.program.instructions[current.instruction]
            if node.kind == "accept":
                accepts = True
            elif node.kind == "atom":
                steps[self._step(current, budget)] = None
            else:
                for successor in self._flow.follow(node, current):
                    if budget is not None:
                        budget.spend()
                    following = StructuralConfiguration(successor).normalized()
                    if following in visited:
                        continue
                    if len(visited) >= self.maximum:
                        raise MappingLimitExceeded(
                            "program epsilon configuration limit exceeded"
                        )
                    visited.add(following)
                    pending.append(following)
        return Frontier(accepts, tuple(steps))

    def _step(
        self, current: Configuration, budget: AnalysisBudget | None
    ) -> ProgramStep:
        node = self.program.instructions[current.instruction]
        label = (
            "K:" + ascii_lower(node.atom.value)
            if isinstance(node.atom, Literal)
            else "P"
        )
        slot = self.program.slots[current.instruction]
        state = WalkState(position=current.state.position + 1)
        target = self._settled(current.at(node.target, state), budget)
        return ProgramStep(
            target,
            label,
            self._tag(slot.document, current, document=True),
            self._tag(slot.device, current, document=False),
        )

    def _settled(
        self,
        current: Configuration,
        budget: AnalysisBudget | None,
    ) -> Configuration:
        """Close finished constructs before using a configuration as a graph key.

        A consumed alternative's local choice frame cannot affect its suffix.
        Keeping it until the next frontier duplicates equivalent product states.
        """
        current = StructuralConfiguration(current).normalized()
        while self.program.instructions[current.instruction].kind in {
            "choice_close",
            "set_commit",
            "repeat_commit",
            "set_enter",
            "repeat_enter",
        }:
            if budget is not None:
                budget.spend()
            successors = self._flow.follow(
                self.program.instructions[current.instruction], current
            )
            if not successors:
                break
            current = StructuralConfiguration(successors[0]).normalized()
        return current

    def _tag(
        self, tag: CaptureTag | None, current: Configuration, *, document: bool
    ) -> CaptureTag | None:
        if tag is None:
            return None
        iterations = []
        for frame in current.frames:
            if frame.kind == "repeat":
                binding = self._repeats.get(frame.path, RepeatBinding(frame.path))
                repeat_id = binding.document if document else binding.device
                if repeat_id is not None:
                    iterations.append((repeat_id, frame.count))
        return replace(
            tag,
            iterations=tag.iterations + tuple(iterations),
            repeat_ids=tuple(repeat_id for repeat_id, _ in iterations),
        )
