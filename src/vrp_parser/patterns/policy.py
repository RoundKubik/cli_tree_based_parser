"""Project-level policies layered on top of the neutral pattern grammar."""

from __future__ import annotations

from collections.abc import Iterator

from vrp_parser.parameters import ParameterDeclaration

from .ast import Group, Parameter, Repeat, Sequence
from .errors import PatternLanguageError
from .tokens import SourceSpan


class RuntimePatternPolicy:
    """Reject declarations that the runtime would otherwise mistake for literals."""

    _DECLARATION_PREFIXES = (
        "HEX<",
        "STRING<",
        "INTEGER<",
        "ENUM{",
        "PASSWORDEX<",
        "TEXT<",
    )
    _DISABLED_PLACEHOLDERS = (
        "X:X::X:X/M",
        "X:X::X:X",
        "X.X.X.X",
    )
    _EXACT_PLACEHOLDERS = (
        "YYYY/MM/DD,HH:MM:SS",
        "MM-DD-YYYY",
        "YYYY/MM/DD",
        "YYYY-MM-DD",
        "HH:MM:SS",
        "<hh:mm>",
        "MM-DD",
        "H-H-H",
    )

    def validate(self, ast: Sequence, source: str) -> None:
        parameters = tuple(self._parameters(ast))
        for prefix in self._DECLARATION_PREFIXES:
            position = source.find(prefix)
            while position >= 0:
                if not self._claimed(parameters, position, position + len(prefix)):
                    self._fail(
                        f"unsupported or malformed parameter beginning with {prefix}",
                        source,
                        position,
                        position + len(prefix),
                    )
                position = source.find(prefix, position + 1)

        for placeholder in self._DISABLED_PLACEHOLDERS:
            position = source.find(placeholder)
            while position >= 0:
                if not self._claimed(
                    parameters,
                    position,
                    position + len(placeholder),
                ):
                    self._fail(
                        f"parameter {placeholder} is not supported",
                        source,
                        position,
                        position + len(placeholder),
                    )
                position = source.find(placeholder, position + 1)
        for placeholder in self._EXACT_PLACEHOLDERS:
            position = source.find(placeholder)
            while position >= 0:
                if not self._claimed(
                    parameters,
                    position,
                    position + len(placeholder),
                ):
                    self._fail(
                        f"unsupported or malformed parameter {placeholder}",
                        source,
                        position,
                        position + len(placeholder),
                    )
                position = source.find(placeholder, position + 1)
        self._validate_text_sequence(ast, source, followed=False)

    @staticmethod
    def _claimed(
        parameters: tuple[Parameter, ...],
        start: int,
        end: int,
    ) -> bool:
        return any(
            item.span.start <= start and item.span.end >= end
            for item in parameters
        )

    def _validate_text_sequence(
        self,
        sequence: Sequence,
        source: str,
        *,
        followed: bool,
    ) -> None:
        for index, node in enumerate(sequence.items):
            has_continuation = followed or index < len(sequence.items) - 1
            if isinstance(node, Parameter) and self._is_text(node):
                if has_continuation:
                    self._fail(
                        f"{node.source} must be the final pattern element",
                        source,
                        node.span.start,
                        node.span.end,
                    )
            elif isinstance(node, Group):
                for alternative in node.alternatives:
                    self._validate_text_sequence(
                        alternative,
                        source,
                        followed=has_continuation,
                    )
            elif isinstance(node, Repeat):
                self._validate_repeated_text(node, source)

    def _validate_repeated_text(self, repeat: Repeat, source: str) -> None:
        if isinstance(repeat.atom, Parameter) and self._is_text(repeat.atom):
            self._fail(
                f"{repeat.atom.source} cannot be repeated",
                source,
                repeat.span.start,
                repeat.span.end,
            )
        if isinstance(repeat.atom, Group):
            for alternative in repeat.atom.alternatives:
                self._validate_text_sequence(
                    alternative,
                    source,
                    followed=True,
                )

    def _is_text(self, parameter: Parameter) -> bool:
        return self._declaration(parameter).type_id == "text"

    def _parameters(self, sequence: Sequence) -> Iterator[Parameter]:
        for node in sequence.items:
            if isinstance(node, Parameter):
                yield node
            elif isinstance(node, Group):
                for alternative in node.alternatives:
                    yield from self._parameters(alternative)
            elif isinstance(node, Repeat) and isinstance(node.atom, Parameter):
                yield node.atom
            elif isinstance(node, Repeat) and isinstance(node.atom, Group):
                for alternative in node.atom.alternatives:
                    yield from self._parameters(alternative)

    @staticmethod
    def _declaration(parameter: Parameter) -> ParameterDeclaration:
        declaration = parameter.declaration
        if not isinstance(declaration, ParameterDeclaration):
            raise TypeError("parameter AST contains an unknown declaration")
        return declaration

    @staticmethod
    def _fail(
        message: str,
        source: str,
        start: int,
        end: int,
    ) -> None:
        raise PatternLanguageError(
            message,
            source,
            SourceSpan(start, end),
        )
