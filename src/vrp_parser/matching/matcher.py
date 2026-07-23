"""Walk the merged graph and delegate final candidate resolution."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_parser.graph import CommandEdge, CommandGraph, CommandNode
from vrp_parser.parameters import ParameterDeclaration, ParameterTypeRegistry
from vrp_parser.patterns import Parameter
from vrp_parser.results import ErrorCode, ParseError

from .deduplication import CandidateSet
from .diagnostics import MatchDiagnostics
from .expressions import ExpressionMatcher
from .resolver import MatchResolver, ResolvedMatch
from .state import Candidate, WalkState
from .text import CommandText, ascii_lower


@dataclass(frozen=True, slots=True)
class _Traversal:
    node: CommandNode
    state: WalkState
    route_ids: frozenset[int] | None
    depth: int


class CommandMatcher:
    """Recognize one indentation-free command against a shared graph."""

    def __init__(
        self,
        graph: CommandGraph,
        parameter_types: ParameterTypeRegistry,
        expression_matcher: ExpressionMatcher | None = None,
        resolver: MatchResolver | None = None,
        candidate_set: CandidateSet | None = None,
    ) -> None:
        self._graph = graph
        self._expressions = expression_matcher or ExpressionMatcher(
            parameter_types
        )
        self._resolver = resolver or MatchResolver()
        self._candidate_set = candidate_set or CandidateSet()

    def match(
        self,
        text: str,
        *,
        span_offset: int = 0,
    ) -> ResolvedMatch | ParseError:
        command = CommandText(text)
        diagnostics = MatchDiagnostics()
        candidates: list[Candidate] = []
        self._walk(
            _Traversal(self._graph.root, WalkState(), None, 0),
            command,
            diagnostics,
            candidates,
        )
        unique = self._candidate_set.unique(candidates)
        if unique:
            return self._resolver.resolve(
                unique,
                self._graph,
                span_offset=span_offset,
            )
        return self._syntax_error(
            diagnostics,
            span_offset=span_offset,
        )

    def _walk(
        self,
        traversal: _Traversal,
        command: CommandText,
        diagnostics: MatchDiagnostics,
        candidates: list[Candidate],
    ) -> None:
        node = traversal.node
        state = traversal.state
        active = traversal.route_ids

        at_end = command.at_end(state.position)
        if at_end:
            accepting = node.accepting_routes
            if active is not None:
                accepting = accepting & active
            candidates.extend(
                Candidate(route_id, state) for route_id in accepting
            )
            position = command.skip_space(state.position)
            for edge in node.literal_edges.values():
                expression = edge.step.expression
                value = getattr(expression, "value", None)
                if isinstance(value, str):
                    diagnostics.record(position, repr(value))
            if not node.expression_edges:
                return
        elif node.accepting_routes:
            diagnostics.record(
                command.skip_space(state.position),
                "end of command",
            )

        edges = self._edges(node, command, state.position)
        if not edges and traversal.depth > 0:
            position = command.skip_space(state.position)
            for edge in node.literal_edges.values():
                expression = edge.step.expression
                value = getattr(expression, "value", None)
                if isinstance(value, str):
                    diagnostics.record(position, repr(value))
            return

        for edge in edges:
            route_ids = edge.route_ids
            if active is not None:
                route_ids = route_ids & active
            if not route_ids:
                continue
            if not self._text_policy_allows(edge, state, command):
                continue
            for result in self._expressions.match(
                edge.step.expression,
                state,
                command,
                diagnostics,
                path=f"step:{traversal.depth}",
            ):
                self._walk(
                    _Traversal(
                        edge.target,
                        result,
                        route_ids,
                        traversal.depth + 1,
                    ),
                    command,
                    diagnostics,
                    candidates,
                )

    @staticmethod
    def _edges(
        node: CommandNode,
        command: CommandText,
        position: int,
    ) -> tuple[CommandEdge, ...]:
        token = command.token(position)
        literal = (
            node.literal_edges.get(ascii_lower(token.raw))
            if token is not None
            else None
        )
        if literal is None:
            return node.expression_edges
        return (literal, *node.expression_edges)

    @staticmethod
    def _text_policy_allows(
        edge: CommandEdge,
        state: WalkState,
        command: CommandText,
    ) -> bool:
        expression = edge.step.expression
        if not isinstance(expression, Parameter):
            return True
        declaration = expression.declaration
        if not isinstance(declaration, ParameterDeclaration):
            return True
        if declaration.type_id != "text" or state.position != 0:
            return True
        start = command.skip_space(0)
        return (
            declaration.source == "TEXT<1-4096>"
            and start < len(command.value)
            and command.value[start] == "!"
        )

    @staticmethod
    def _syntax_error(
        diagnostics: MatchDiagnostics,
        *,
        span_offset: int,
    ) -> ParseError:
        code = (
            ErrorCode.UNKNOWN_COMMAND
            if diagnostics.position == 0
            else ErrorCode.SYNTAX_ERROR
        )
        message = (
            "unknown command"
            if code is ErrorCode.UNKNOWN_COMMAND
            else "command does not match any complete pattern"
        )
        return ParseError(
            code=code,
            message=message,
            position=diagnostics.position + span_offset,
            expected=diagnostics.elements(offset=span_offset),
        )
