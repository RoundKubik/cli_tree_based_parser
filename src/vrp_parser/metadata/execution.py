"""Lazy execution of compact programs; only visited configurations exist."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .models import CaptureTag, MappingLimitExceeded, PatternProgram

type Coordinates = tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class Frame:
    instruction: int
    document_used: int = 0
    device_used: int = 0
    count: int = 0
    document_iterations: Coordinates = ()
    device_iterations: Coordinates = ()
    # A barrier checks that a set alternative / repetition consumed input.
    barrier: bool = False
    consumed: bool = False

    def child(self, instruction: int) -> Frame:
        return Frame(
            instruction,
            document_iterations=self.document_iterations,
            device_iterations=self.device_iterations,
        )


type Configuration = tuple[Frame, ...]


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
    """Resolve epsilon work on demand, retaining source tags on consuming steps."""

    def __init__(self, program: PatternProgram, maximum_configurations: int) -> None:
        self.program = program
        self.maximum = maximum_configurations
        self.start: Configuration = (Frame(program.root),)
        self._cache: dict[Configuration, Frontier] = {}

    def frontier(self, state: Configuration) -> Frontier:
        if state in self._cache:
            return self._cache[state]
        pending = [state]
        visited = {state}
        steps: dict[ProgramStep, None] = {}
        accepts = False
        while pending:
            current = pending.pop()
            if not current:
                accepts = True
                continue
            frame, *tail = current
            rest = tuple(tail)
            node = self.program.instructions[frame.instruction]
            if not frame.barrier and node.kind == "atom":
                assert node.label is not None
                target = tuple(
                    replace(item, consumed=True) if item.barrier else item
                    for item in rest
                )
                steps[
                    ProgramStep(
                        target,
                        node.label,
                        self._tag(node.document, frame.document_iterations),
                        self._tag(node.device, frame.device_iterations),
                    )
                ] = None
                continue
            for continuation in self._epsilon(frame, rest):
                if continuation in visited:
                    continue
                if len(visited) >= self.maximum:
                    raise MappingLimitExceeded(
                        "program epsilon configuration limit exceeded"
                    )
                visited.add(continuation)
                pending.append(continuation)
        result = Frontier(accepts, tuple(steps))
        # Bound the cache independently of the caller's visited-state budget.
        if len(self._cache) < self.maximum:
            self._cache[state] = result
        return result

    def _tag(
        self, tag: CaptureTag | None, iterations: Coordinates
    ) -> CaptureTag | None:
        return replace(tag, iterations=tag.iterations + iterations) if tag else None

    def _epsilon(self, frame: Frame, rest: Configuration) -> tuple[Configuration, ...]:
        if frame.barrier:
            return (rest,) if frame.consumed else ()
        node = self.program.instructions[frame.instruction]
        if node.kind == "sequence":
            return (tuple(frame.child(child) for child in node.children) + rest,)
        if node.kind == "choice":
            branches = tuple(
                (frame.child(branch.target), *rest) for branch in node.branches
            )
            return branches + ((rest,) if node.minimum == 0 else ())
        barrier = replace(frame, barrier=True, consumed=False)
        if node.kind == "set":
            selected = max(
                frame.document_used.bit_count(), frame.device_used.bit_count()
            )
            paths = [rest] if selected >= node.minimum else []
            for branch in node.branches:
                if (
                    branch.document_bit & frame.document_used
                    or branch.device_bit & frame.device_used
                ):
                    continue
                following = replace(
                    frame,
                    document_used=frame.document_used | branch.document_bit,
                    device_used=frame.device_used | branch.device_bit,
                )
                paths.append((frame.child(branch.target), barrier, following, *rest))
            return tuple(paths)
        assert node.kind == "repeat"
        paths = [rest] if frame.count >= node.minimum else []
        if frame.count < node.maximum:
            child = frame.child(node.children[0])
            child = replace(
                child,
                document_iterations=child.document_iterations
                + (
                    ((node.document_repeat, frame.count),)
                    if node.document_repeat
                    else ()
                ),
                device_iterations=child.device_iterations
                + (((node.device_repeat, frame.count),) if node.device_repeat else ()),
            )
            paths.append((child, barrier, replace(frame, count=frame.count + 1), *rest))
        return tuple(paths)
