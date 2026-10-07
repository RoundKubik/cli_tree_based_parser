"""Documentation placeholders and type-independent AST structure."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from vrp_format_matcher.comparison.parameter_types import DOCUMENT_PARAMETER_TYPES
from vrp_format_matcher.models import FormatError
from vrp_parser_automaton.patterns import Group, Literal, Node, Parameter, Repeat
from vrp_parser_automaton.patterns import Sequence as PatternSequence
from vrp_parser_automaton.text import ascii_lower


@dataclass(frozen=True)
class NamedParameter:
    name: str
    end: int
    type_id: str | None = None


class DocumentParameterRecognizer:
    """Recognize documentation placeholders without any device type mapping."""

    def __init__(self, types: Mapping[str, str] | None = None) -> None:
        self._types = types or {}

    def recognize(self, source: str, position: int) -> NamedParameter | None:
        if source[position : position + 1] != "<":
            return None
        match = re.match(r"<([A-Za-z0-9_][A-Za-z0-9_.:/-]*)>", source[position:])
        if match is None:
            raise FormatError(f"invalid documentation placeholder at {position}")
        return NamedParameter(
            match[1], position + match.end(), self._types.get(match[1])
        )


def declared_types(document: Mapping[str, Any]) -> dict[str, str]:
    """Legacy inputs may omit types; an explicit list must be well formed."""
    if "parameter_types" not in document:
        return {}
    records = document["parameter_types"]
    if not isinstance(records, list):
        raise FormatError("parameter_types must be an array")
    result: dict[str, str] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise FormatError("parameter_types entries must be objects")
        name, type_id = record.get("parameter_name"), record.get("parameter_type")
        if not isinstance(name, str) or not name:
            raise FormatError("parameter_name must be a nonempty string")
        if name in result:
            raise FormatError(f"duplicate parameter type: {name}")
        if not isinstance(type_id, str) or type_id not in DOCUMENT_PARAMETER_TYPES:
            raise FormatError(f"unsupported parameter type for {name}: {type_id!r}")
        result[name] = type_id
    return result


def parameters(node: Node | PatternSequence) -> Iterator[Parameter]:
    if isinstance(node, Parameter):
        yield node
    elif isinstance(node, PatternSequence):
        for item in node.items:
            yield from parameters(item)
    elif isinstance(node, Group):
        for branch in node.alternatives:
            yield from parameters(branch)
    elif isinstance(node, Repeat):
        yield from parameters(node.atom)


def type_signature(ast: PatternSequence) -> tuple[tuple[int, str], ...]:
    """External annotations distinguish otherwise identical source strings."""
    return tuple(
        (node.span.start, node.declaration.type_id)
        for node in parameters(ast)
        if isinstance(node.declaration, NamedParameter)
        and node.declaration.type_id is not None
    )


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
