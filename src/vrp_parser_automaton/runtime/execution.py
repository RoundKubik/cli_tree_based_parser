"""Small transition objects own the register updates of each construct."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from vrp_parser_automaton.automata.model import Instruction
from vrp_parser_automaton.results import VariationStep

from .state import WalkState, source_order_with_parent


@dataclass(frozen=True, slots=True)
class Frame:
    kind: Literal["choice", "optional", "set", "repeat"]
    path: str
    order_start: int
    start_position: int = 0
    selected: tuple[int, ...] = ()
    used: int = 0
    count: int = 0


@dataclass(frozen=True, slots=True)
class Configuration:
    instruction: int
    state: WalkState
    frames: tuple[Frame, ...] = ()

    def at(self, instruction: int, state: WalkState | None = None) -> Configuration:
        return replace(self, instruction=instruction, state=state or self.state)

    def path(self, origin: str) -> str:
        repeats = (
            (frame.path, frame.count) for frame in self.frames if frame.kind == "repeat"
        )
        return origin + "".join(f"@{path}:{index}" for path, index in repeats)

    def opened(self, target: int, frame: Frame) -> Configuration:
        return Configuration(target, self.state, self.frames + (frame,))

    def updated(
        self, target: int, frame: Frame, state: WalkState | None = None
    ) -> Configuration:
        return Configuration(target, state or self.state, self.frames[:-1] + (frame,))

    def closed(
        self, target: int, selected: tuple[int, ...], decision: int | None = None
    ) -> Configuration:
        frame = self.frames[-1]
        parent = replace(self, frames=self.frames[:-1])
        path = (
            frame.path
            if frame.kind in {"choice", "optional"}
            else parent.path(frame.path)
        )
        order = self.state.source_order
        if decision is not None:
            base = replace(self.state, source_order=order[: frame.order_start])
            order = source_order_with_parent(base, self.state, decision)
        state = replace(
            self.state,
            source_order=order,
            trace=self.state.trace + (VariationStep(frame.kind, path, selected),),
        )
        return Configuration(target, state, self.frames[:-1])


@dataclass(frozen=True, slots=True)
class ChoiceTransition:
    node: Instruction
    current: Configuration

    def enter(self) -> tuple[Configuration, ...]:
        optional = self.node.kind == "optional"
        state = self.current.state
        results = []
        if optional:
            skipped = replace(
                state,
                source_order=state.source_order + (0,),
                trace=state.trace
                + (VariationStep("optional", self.current.path(self.node.path)),),
            )
            results.append(self.current.at(self.node.target, skipped))
        for index, branch in enumerate(self.node.branches):
            frame = Frame(
                "optional" if optional else "choice",
                self.current.path(self.node.path),
                len(state.source_order),
                selected=(index,),
            )
            results.append(self.current.opened(branch, frame))
        return tuple(results)

    def leave(self) -> tuple[Configuration, ...]:
        frame = self.current.frames[-1]
        decision = frame.selected[0] + (frame.kind == "optional")
        return (self.current.closed(self.node.target, frame.selected, int(decision)),)


@dataclass(frozen=True, slots=True)
class SetTransition:
    node: Instruction
    current: Configuration

    def enter(self) -> tuple[Configuration, ...]:
        frame = Frame("set", self.node.path, len(self.current.state.source_order))
        return (self.current.opened(self.node.target, frame),)

    def advance(self) -> tuple[Configuration, ...]:
        frame = self.current.frames[-1]
        state = self.current.state
        results = []
        if len(frame.selected) >= self.node.minimum:
            results.append(self.current.closed(self.node.target, frame.selected))
        for index, branch in enumerate(self.node.branches):
            bit = 1 << index
            if not frame.used & bit:
                selected = replace(
                    frame,
                    used=frame.used | bit,
                    selected=frame.selected + (index,),
                    start_position=state.position,
                )
                data = replace(state, source_order=state.source_order + (index,))
                results.append(self.current.updated(branch, selected, data))
        return tuple(results)

    def commit(self) -> tuple[Configuration, ...]:
        frame = self.current.frames[-1]
        if self.current.state.position <= frame.start_position:
            return ()
        return (self.current.at(self.node.target),)


@dataclass(frozen=True, slots=True)
class RepeatTransition:
    node: Instruction
    current: Configuration

    def enter(self) -> tuple[Configuration, ...]:
        frame = Frame("repeat", self.node.path, len(self.current.state.source_order))
        return (self.current.opened(self.node.target, frame),)

    def advance(self) -> tuple[Configuration, ...]:
        frame = self.current.frames[-1]
        results = []
        if frame.count >= self.node.minimum:
            results.append(
                self.current.closed(self.node.target, (frame.count,), frame.count)
            )
        if frame.count < self.node.maximum:
            selected = replace(frame, start_position=self.current.state.position)
            results.append(self.current.updated(self.node.branches[0], selected))
        return tuple(results)

    def commit(self) -> tuple[Configuration, ...]:
        frame = self.current.frames[-1]
        if self.current.state.position <= frame.start_position:
            return ()
        return (
            self.current.updated(
                self.node.target, replace(frame, count=frame.count + 1)
            ),
        )


class ControlFlow:
    """Dispatch one epsilon instruction without interpreting an AST subtree."""

    def follow(
        self, node: Instruction, current: Configuration
    ) -> tuple[Configuration, ...]:
        if node.kind in {"choice", "optional"}:
            return ChoiceTransition(node, current).enter()
        if node.kind == "choice_close":
            return ChoiceTransition(node, current).leave()
        if node.kind == "set_enter":
            return SetTransition(node, current).enter()
        if node.kind == "set_next":
            return SetTransition(node, current).advance()
        if node.kind == "set_commit":
            return SetTransition(node, current).commit()
        if node.kind == "repeat_enter":
            return RepeatTransition(node, current).enter()
        if node.kind == "repeat_next":
            return RepeatTransition(node, current).advance()
        if node.kind == "repeat_commit":
            return RepeatTransition(node, current).commit()
        raise ValueError(f"not an epsilon instruction: {node.kind}")
