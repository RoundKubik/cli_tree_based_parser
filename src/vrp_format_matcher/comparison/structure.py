"""Canonical AST shape, independent of parameter names and alternative order."""

from __future__ import annotations

from vrp_parser_automaton.patterns import (
    Group,
    GroupMode,
    Literal,
    Node,
    Parameter,
    Repeat,
)
from vrp_parser_automaton.patterns import Sequence as PatternSequence
from vrp_parser_automaton.text import ascii_lower

type Expression = Node | PatternSequence


def canonical_key(expression: Expression) -> tuple[object, ...]:
    """Preserve multiplicity and cardinality, ignoring alternative order/types."""
    expression = unwrapped(expression)
    if isinstance(expression, PatternSequence):
        return (
            "sequence",
            tuple(canonical_key(item) for item in sequence_items(expression)),
        )
    if isinstance(expression, Literal):
        return ("literal", ascii_lower(expression.value))
    if isinstance(expression, Parameter):
        return ("parameter",)
    if isinstance(expression, Repeat):
        return (
            "repeat",
            expression.minimum,
            expression.maximum,
            canonical_key(expression.atom),
        )
    return (
        expression.mode.value,
        tuple(
            sorted(
                (canonical_key(branch) for branch in expression.alternatives), key=repr
            )
        ),
    )


def unwrapped(expression: Expression) -> Expression:
    """Required single alternatives add no language or capture identity."""
    while (
        isinstance(expression, Group)
        and expression.mode == GroupMode.REQUIRED_ONE
        and len(expression.alternatives) == 1
    ):
        expression = expression.alternatives[0]
    return expression


def sequence_items(sequence: PatternSequence) -> tuple[Node, ...]:
    result: list[Node] = []
    for item in sequence.items:
        inner = unwrapped(item)
        if isinstance(inner, PatternSequence):
            result.extend(sequence_items(inner))
        else:
            result.append(inner)
    return tuple(result)
