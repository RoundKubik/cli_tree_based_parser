"""Recursive-descent parser for Huawei command patterns."""

from __future__ import annotations

from typing import NoReturn

from .ast import Group, GroupMode, Literal, Node, Parameter, Repeat, Sequence
from .errors import PatternLanguageError
from .lexer import ParameterRecognizer, PatternLexer
from .tokens import SourceSpan, Token, TokenKind


class PatternParser:
    """Parse one source pattern using a caller-supplied parameter registry."""

    def __init__(self, parameter_recognizer: ParameterRecognizer) -> None:
        self._lexer = PatternLexer(parameter_recognizer)
        self._source = ""
        self._tokens: tuple[Token, ...] = ()
        self._position = 0

    def parse(self, source: str) -> Sequence:
        """Parse a complete pattern into an immutable AST."""

        self._source = source
        self._tokens = self._lexer.tokenize(source)
        self._position = 0

        root = self._parse_sequence(
            stop=frozenset({TokenKind.END}),
            pipe_is_literal=True,
        )
        self._expect(TokenKind.END)
        if not root.items:
            self._fail("a command pattern cannot be empty", root.span)
        return root

    def _parse_sequence(
        self,
        *,
        stop: frozenset[TokenKind],
        pipe_is_literal: bool,
    ) -> Sequence:
        items: list[Node] = []
        start = self._current.span.start

        while self._current.kind not in stop:
            if self._current.kind is TokenKind.PIPE and not pipe_is_literal:
                break
            atom = self._parse_atom(pipe_is_literal=pipe_is_literal)
            items.append(self._parse_repeat(atom))

        end = items[-1].span.end if items else start
        return Sequence(tuple(items), SourceSpan(start, end))

    def _parse_atom(self, *, pipe_is_literal: bool) -> Node:
        token = self._advance()
        if token.kind is TokenKind.PARAMETER:
            assert token.parameter is not None
            return Parameter(token.parameter, token.text, token.span)
        if token.kind is TokenKind.LITERAL:
            return Literal(token.text, token.span)
        if token.kind is TokenKind.STAR:
            return Literal("*", token.span)
        if token.kind is TokenKind.PIPE and pipe_is_literal:
            return Literal("|", token.span)
        if token.kind in {TokenKind.LEFT_BRACE, TokenKind.LEFT_BRACKET}:
            return self._parse_group(token)
        if token.kind in {TokenKind.RIGHT_BRACE, TokenKind.RIGHT_BRACKET}:
            self._fail(f"unexpected closing delimiter {token.text!r}", token.span)
        if token.kind is TokenKind.REPEAT:
            self._fail(
                "repeat operator has no preceding parameter or group",
                token.span,
            )
        self._fail("expected a literal, parameter, or group", token.span)

    def _parse_group(self, opening: Token) -> Group:
        optional = opening.kind is TokenKind.LEFT_BRACKET
        closing_kind = (
            TokenKind.RIGHT_BRACKET if optional else TokenKind.RIGHT_BRACE
        )
        alternatives: list[Sequence] = []

        while True:
            alternative = self._parse_sequence(
                stop=frozenset({closing_kind, TokenKind.END}),
                pipe_is_literal=False,
            )
            if not alternative.items:
                self._fail(
                    "group alternatives cannot be empty",
                    self._current.span,
                )
            alternatives.append(alternative)
            if self._current.kind is TokenKind.PIPE:
                self._advance()
                continue
            break

        closing = self._expect(closing_kind)
        if self._current.kind is TokenKind.STAR:
            suffix = self._advance()
            mode = (
                GroupMode.OPTIONAL_SET
                if optional
                else GroupMode.REQUIRED_SET
            )
            end = suffix.span.end
        else:
            mode = (
                GroupMode.OPTIONAL_ONE
                if optional
                else GroupMode.REQUIRED_ONE
            )
            end = closing.span.end

        return Group(
            alternatives=tuple(alternatives),
            mode=mode,
            span=SourceSpan(opening.span.start, end),
        )

    def _parse_repeat(self, atom: Node) -> Node:
        if self._current.kind is not TokenKind.REPEAT:
            return atom

        token = self._advance()
        assert token.repeat_bounds is not None
        minimum, maximum = token.repeat_bounds
        if maximum < minimum:
            self._fail(
                "repeat bounds must satisfy minimum <= maximum",
                token.span,
            )
        if not isinstance(atom, (Parameter, Group)):
            self._fail(
                "only a parameter or group can be repeated",
                token.span,
            )
        return Repeat(
            atom=atom,
            minimum=minimum,
            maximum=maximum,
            span=SourceSpan(atom.span.start, token.span.end),
        )

    @property
    def _current(self) -> Token:
        return self._tokens[self._position]

    def _advance(self) -> Token:
        token = self._current
        if token.kind is not TokenKind.END:
            self._position += 1
        return token

    def _expect(self, kind: TokenKind) -> Token:
        token = self._current
        if token.kind is not kind:
            self._fail(
                f"expected {kind.value}, found {token.text!r}",
                token.span,
            )
        return self._advance()

    def _fail(self, message: str, span: SourceSpan) -> NoReturn:
        raise PatternLanguageError(message, self._source, span)
