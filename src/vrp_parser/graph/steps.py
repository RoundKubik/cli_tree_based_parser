"""Semantic graph-step keys independent of source spans."""

from __future__ import annotations

from vrp_parser.parameters import ParameterDeclaration
from vrp_parser.patterns import Group, Literal, Node, Parameter, Repeat, Sequence

from .model import GraphStep, ascii_lower


class GraphStepFactory:
    """Create mergeable steps while keeping the readable source AST."""

    def create(self, expression: Node) -> GraphStep:
        return GraphStep(expression, self._node_key(expression))

    def _node_key(self, node: Node) -> tuple[object, ...]:
        if isinstance(node, Literal):
            return ("literal", ascii_lower(node.value))
        if isinstance(node, Parameter):
            declaration = self._declaration(node)
            return (
                "parameter",
                declaration.type_id,
                declaration.source,
                declaration.minimum,
                declaration.maximum,
                declaration.choices,
                declaration.metadata,
            )
        if isinstance(node, Group):
            return (
                "group",
                node.mode.value,
                tuple(self._sequence_key(item) for item in node.alternatives),
            )
        if isinstance(node, Repeat):
            return (
                "repeat",
                self._node_key(node.atom),
                node.minimum,
                node.maximum,
            )
        raise TypeError(f"unsupported pattern node: {type(node).__name__}")

    def _sequence_key(self, sequence: Sequence) -> tuple[object, ...]:
        return tuple(self._node_key(item) for item in sequence.items)

    @staticmethod
    def _declaration(node: Parameter) -> ParameterDeclaration:
        declaration = node.declaration
        if not isinstance(declaration, ParameterDeclaration):
            raise TypeError("parameter AST contains an unknown declaration")
        return declaration
