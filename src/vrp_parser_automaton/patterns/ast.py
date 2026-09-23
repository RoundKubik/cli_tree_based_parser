"""Small immutable AST for Huawei command patterns."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .tokens import SourceSpan


class GroupMode(StrEnum):
    """Cardinality and ordering semantics of one group."""

    OPTIONAL_ONE = "optional_one"
    REQUIRED_ONE = "required_one"
    OPTIONAL_SET = "optional_set"
    REQUIRED_SET = "required_set"


@dataclass(frozen=True, slots=True)
class Sequence:
    """Items consumed from left to right."""

    items: tuple[Node, ...]
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Literal:
    """A fixed CLI token."""

    value: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Parameter:
    """A declaration recognized by the caller-supplied registry."""

    declaration: object
    source: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Group:
    """A choice or an unordered set of alternatives."""

    alternatives: tuple[Sequence, ...]
    mode: GroupMode
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Repeat:
    """A bounded repetition of a parameter or group."""

    atom: Parameter | Group
    minimum: int
    maximum: int
    span: SourceSpan


type Node = Literal | Parameter | Group | Repeat
