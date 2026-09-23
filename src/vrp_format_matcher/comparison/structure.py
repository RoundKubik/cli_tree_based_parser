"""Canonical shape and source correspondence for unambiguous equivalent ASTs."""

from __future__ import annotations

from vrp_parser_automaton.patterns import Literal, Node, Parameter, Repeat
from vrp_parser_automaton.patterns import Sequence as PatternSequence
from vrp_parser_automaton.text import ascii_lower

type Expression = Node | PatternSequence


def canonical_key(expression: Expression) -> tuple[object, ...]:
    """Preserve multiplicity and cardinality, ignoring alternative order/types."""
    if isinstance(expression, PatternSequence):
        return ("sequence", tuple(canonical_key(item) for item in expression.items))
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
