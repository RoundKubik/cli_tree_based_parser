"""A fresh compilation workspace owns instruction allocation and back edges."""

from __future__ import annotations

from collections import defaultdict

from vrp_parser_automaton.patterns import (
    Group,
    GroupMode,
    Literal,
    Node,
    Parameter,
    Repeat,
    Sequence,
)

from .first_tokens import FirstTokens
from .model import CommandAutomaton, Instruction, PatternSource


class AutomatonBuilder:
    """Reusable builder: mutations are confined to a single build call."""

    def build(self, patterns: tuple[PatternSource, ...]) -> CommandAutomaton:
        return _CompilationWorkspace().build(patterns)


class _CompilationWorkspace:
    """One workspace per build; each source expression is compiled once."""

    def __init__(self) -> None:
        self.states: list[Instruction] = []

    def add(self, instruction: Instruction) -> int:
        index = len(self.states)
        self.states.append(instruction)
        return index

    def build(self, patterns: tuple[PatternSource, ...]) -> CommandAutomaton:
        starts = []
        literals: dict[str, list[int]] = defaultdict(list)
        parameters = []
        for pattern in patterns:
            end = self.add(Instruction("accept", pattern_index=pattern.index))
            start = self.sequence(pattern.ast, end, "root")
            starts.append(start)
            first = FirstTokens.sequence(pattern.ast)
            for literal in first.literals:
                literals[literal].append(start)
            if first.parameter:
                parameters.append(start)
        return CommandAutomaton.create(
            tuple(self.states),
            patterns,
            tuple(starts),
            {key: tuple(value) for key, value in literals.items()},
            tuple(parameters),
        )

    def sequence(self, sequence: Sequence, target: int, path: str) -> int:
        for index in reversed(range(len(sequence.items))):
            target = self.node(sequence.items[index], target, f"{path}.{index}")
        return target

    def node(self, node: Node, target: int, path: str) -> int:
        if isinstance(node, (Literal, Parameter)):
            return self.add(Instruction("atom", path, target, atom=node))
        if isinstance(node, Repeat):
            return self.repeat(node, target, path)
        return self.group(node, target, path)

    def repeat(self, node: Repeat, target: int, path: str) -> int:
        loop = self.add(Instruction("pending"))
        commit = self.add(Instruction("repeat_commit", path, loop))
        body = self.node(node.atom, commit, f"{path}.body")
        self.states[loop] = Instruction(
            "repeat_next",
            path,
            target,
            (body,),
            minimum=node.minimum,
            maximum=node.maximum,
        )
        return self.add(Instruction("repeat_enter", path, loop))

    def group(self, node: Group, target: int, path: str) -> int:
        is_set = node.mode in {GroupMode.REQUIRED_SET, GroupMode.OPTIONAL_SET}
        optional = node.mode in {GroupMode.OPTIONAL_ONE, GroupMode.OPTIONAL_SET}
        if is_set:
            loop = self.add(Instruction("pending"))
            commit = self.add(Instruction("set_commit", path, loop))
            branches = tuple(
                self.sequence(branch, commit, f"{path}.{index}")
                for index, branch in enumerate(node.alternatives)
            )
            self.states[loop] = Instruction(
                "set_next", path, target, branches, minimum=0 if optional else 1
            )
            return self.add(Instruction("set_enter", path, loop))
        close = self.add(Instruction("choice_close", path, target))
        branches = tuple(
            self.sequence(branch, close, f"{path}.{index}")
            for index, branch in enumerate(node.alternatives)
        )
        return self.add(
            Instruction("optional" if optional else "choice", path, target, branches)
        )
