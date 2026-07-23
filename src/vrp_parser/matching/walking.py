"""Protocol used by recursive group and repeat matchers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from vrp_parser.patterns import Node

from .diagnostics import MatchDiagnostics
from .state import WalkState
from .text import CommandText


class ExpressionWalker(Protocol):
    """Minimal recursive operations needed by compound expressions."""

    def walk(
        self,
        expression: Node,
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
    ) -> Iterator[WalkState]: ...

    def sequence(
        self,
        expressions: tuple[Node, ...],
        state: WalkState,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        *,
        path: str,
    ) -> tuple[WalkState, ...]: ...
