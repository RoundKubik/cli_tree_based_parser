"""Documentation placeholders and type-independent AST structure."""

from __future__ import annotations

import re
from dataclasses import dataclass

from vrp_format_matcher.models import FormatError
from vrp_parser_automaton.patterns import Group, Literal, Node, Parameter
from vrp_parser_automaton.patterns import Sequence as PatternSequence
from vrp_parser_automaton.text import ascii_lower


@dataclass(frozen=True)
class NamedParameter:
    name: str
    end: int


class DocumentParameterRecognizer:
    """Recognize documentation placeholders without any device type mapping."""

    def recognize(self, source: str, position: int) -> NamedParameter | None:
        if source[position : position + 1] != "<":
            return None
        match = re.match(r"<([A-Za-z0-9_][A-Za-z0-9_.:/-]*)>", source[position:])
        if match is None:
            raise FormatError(f"invalid documentation placeholder at {position}")
        return NamedParameter(match[1], position + match.end())


def structural_key(sequence: PatternSequence) -> tuple[object, ...]:
    def key(node: Node) -> object:
        if isinstance(node, Literal):
            return ("keyword", ascii_lower(node.value))
        if isinstance(node, Parameter):
            return ("parameter",)
        if isinstance(node, Group):
            return (
                node.mode.value,
                tuple(structural_key(a) for a in node.alternatives),
            )
        return ("repeat", node.minimum, node.maximum, key(node.atom))

    return tuple(key(node) for node in sequence.items)
