"""Conservative proof that normalized tokens determine a unique source path."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from vrp_parser.graph.model import ascii_lower
from vrp_parser.patterns import GroupMode, Literal, Parameter, Repeat
from vrp_parser.patterns import Sequence as PatternSequence

from .programs import Expression


@dataclass(frozen=True)
class PredictiveExpression:
    expression: Expression

    @cached_property
    def children(self) -> tuple[PredictiveExpression, ...]:
        node = self.expression
        if isinstance(node, PatternSequence):
            return tuple(PredictiveExpression(item) for item in node.items)
        if isinstance(node, (Literal, Parameter)):
            return ()
        if isinstance(node, Repeat):
            return (PredictiveExpression(node.atom),)
        return tuple(PredictiveExpression(branch) for branch in node.alternatives)

    @cached_property
    def nullable(self) -> bool:
        node = self.expression
        if isinstance(node, (Literal, Parameter)):
            return False
        if isinstance(node, PatternSequence):
            return all(child.nullable for child in self.children)
        if isinstance(node, Repeat):
            return node.minimum == 0
        if node.mode in {GroupMode.OPTIONAL_ONE, GroupMode.OPTIONAL_SET}:
            return True
        return node.mode == GroupMode.REQUIRED_ONE and any(
            child.nullable for child in self.children
        )

    @cached_property
    def first(self) -> frozenset[str]:
        node = self.expression
        if isinstance(node, Literal):
            return frozenset({"K:" + ascii_lower(node.value)})
        if isinstance(node, Parameter):
            return frozenset({"P"})
        symbols: set[str] = set()
        for child in self.children:
            symbols.update(child.first)
            if isinstance(node, PatternSequence) and not child.nullable:
                break
        return frozenset(symbols)

    def unambiguous(self, following: frozenset[str] = frozenset()) -> bool:
        node = self.expression
        if isinstance(node, (Literal, Parameter)):
            return True
        if isinstance(node, PatternSequence):
            for child in reversed(self.children):
                if not child.unambiguous(following):
                    return False
                following = child.first | (following if child.nullable else frozenset())
            return True
        if isinstance(node, Repeat):
            body = self.children[0]
            if body.nullable:
                return False
            if node.minimum != node.maximum and body.first & following:
                return False
            return body.unambiguous(
                following | (body.first if node.maximum > 1 else frozenset())
            )
        is_set = node.mode in {GroupMode.REQUIRED_SET, GroupMode.OPTIONAL_SET}
        if (self.nullable or is_set) and self.first & following:
            return False
        seen: frozenset[str] = frozenset()
        nullable_count = 0
        for child in self.children:
            if seen & child.first:
                return False
            seen |= child.first
            nullable_count += child.nullable
            if is_set and child.nullable:
                return False
            suffix = following | (self.first - child.first if is_set else frozenset())
            if not child.unambiguous(suffix):
                return False
        return nullable_count <= 1
