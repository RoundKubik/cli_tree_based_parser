"""Build an immutable shared-prefix graph from source patterns."""

from __future__ import annotations

from dataclasses import dataclass, field

from vrp_parser.patterns import Literal, Node

from .model import (
    CommandEdge,
    CommandGraph,
    CommandNode,
    GraphStep,
    PatternSource,
    RouteSource,
    ascii_lower,
)
from .routes import RouteExpander
from .steps import GraphStepFactory


@dataclass(slots=True)
class _DraftEdge:
    step: GraphStep
    target: _DraftNode
    route_ids: set[int] = field(default_factory=set)


@dataclass(slots=True)
class _DraftNode:
    edges: dict[tuple[object, ...], _DraftEdge] = field(default_factory=dict)
    accepting_routes: set[int] = field(default_factory=set)


class CommandGraphBuilder:
    """Merge equivalent prefixes and retain route ownership on every edge."""

    def __init__(
        self,
        route_expander: RouteExpander | None = None,
        step_factory: GraphStepFactory | None = None,
    ) -> None:
        self._route_expander = route_expander or RouteExpander()
        self._step_factory = step_factory or GraphStepFactory()

    def build(self, patterns: tuple[PatternSource, ...]) -> CommandGraph:
        root = _DraftNode()
        routes: dict[int, RouteSource] = {}
        next_route_id = 0

        for pattern in patterns:
            for route in self._route_expander.expand(pattern.ast):
                route_id = next_route_id
                next_route_id += 1
                routes[route_id] = RouteSource(
                    route_id=route_id,
                    pattern=pattern,
                    static_trace=route.trace,
                )
                self._insert(root, route.steps, route_id)

        return CommandGraph.create(
            root=self._freeze(root),
            patterns=patterns,
            routes=routes,
        )

    def _insert(
        self,
        root: _DraftNode,
        expressions: tuple[Node, ...],
        route_id: int,
    ) -> None:
        node = root
        for expression in expressions:
            step = self._step_factory.create(expression)
            edge = node.edges.get(step.key)
            if edge is None:
                edge = _DraftEdge(step=step, target=_DraftNode())
                node.edges[step.key] = edge
            edge.route_ids.add(route_id)
            node = edge.target
        node.accepting_routes.add(route_id)

    def _freeze(self, draft: _DraftNode) -> CommandNode:
        literal_edges: dict[str, CommandEdge] = {}
        expression_edges: list[CommandEdge] = []
        for key in sorted(draft.edges, key=repr):
            item = draft.edges[key]
            edge = CommandEdge(
                step=item.step,
                target=self._freeze(item.target),
                route_ids=frozenset(item.route_ids),
            )
            expression = item.step.expression
            if isinstance(expression, Literal):
                literal_edges[ascii_lower(expression.value)] = edge
            else:
                expression_edges.append(edge)
        return CommandNode.create(
            literal_edges,
            tuple(expression_edges),
            frozenset(draft.accepting_routes),
        )
