"""The possible first tokens of an expression, without enumerating routes."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_parser_automaton.patterns import (
    GroupMode,
    Literal,
    Node,
    Parameter,
    Repeat,
    Sequence,
)
from vrp_parser_automaton.text import ascii_lower


@dataclass(frozen=True, slots=True)
class FirstTokens:
    literals: frozenset[str] = frozenset()
    parameter: bool = False
    nullable: bool = False

    @classmethod
    def sequence(cls, sequence: Sequence) -> FirstTokens:
        literals: set[str] = set()
        parameter = False
        nullable = True
        for node in sequence.items:
            first = cls.node(node)
            literals.update(first.literals)
            parameter |= first.parameter
            if not first.nullable:
                nullable = False
                break
        return cls(frozenset(literals), parameter, nullable)

    @classmethod
    def node(cls, node: Node) -> FirstTokens:
        if isinstance(node, Literal):
            return cls(frozenset({ascii_lower(node.value)}))
        if isinstance(node, Parameter):
            return cls(parameter=True)
        if isinstance(node, Repeat):
            first = cls.node(node.atom)
            return cls(
                first.literals if node.maximum else frozenset(),
                first.parameter and node.maximum > 0,
                node.minimum == 0,
            )
        branches = [cls.sequence(branch) for branch in node.alternatives]
        nullable = node.mode in {GroupMode.OPTIONAL_ONE, GroupMode.OPTIONAL_SET}
        if node.mode is GroupMode.REQUIRED_ONE:
            nullable = any(branch.nullable for branch in branches)
        return cls(
            frozenset().union(*(branch.literals for branch in branches)),
            any(branch.parameter for branch in branches),
            nullable,
        )
