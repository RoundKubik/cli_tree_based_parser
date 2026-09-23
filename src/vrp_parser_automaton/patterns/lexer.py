"""Lexer for Huawei command patterns."""

from __future__ import annotations

import re
from typing import Protocol

from .errors import PatternLanguageError
from .tokens import SourceSpan, Token, TokenKind


class RecognizedParameter(Protocol):
    """Minimum result expected from a parameter registry."""

    @property
    def end(self) -> int:
        """Exclusive end position of the recognized declaration."""


class ParameterRecognizer(Protocol):
    """Registry-like service used by :class:`PatternLexer`."""

    def recognize(self, source: str, position: int) -> RecognizedParameter | None:
        """Recognize a parameter beginning exactly at ``position``."""


class PatternLexer:
    """Convert source text into tokens without knowing parameter syntax."""

    _SYMBOLS = {
        "{": TokenKind.LEFT_BRACE,
        "}": TokenKind.RIGHT_BRACE,
        "[": TokenKind.LEFT_BRACKET,
        "]": TokenKind.RIGHT_BRACKET,
        "|": TokenKind.PIPE,
        "*": TokenKind.STAR,
    }
    _REPEAT = re.compile(r"&<\s*(\d+)\s*-\s*(\d+)\s*>")

    def __init__(self, parameter_recognizer: ParameterRecognizer) -> None:
        self._parameter_recognizer = parameter_recognizer

    def tokenize(self, source: str) -> tuple[Token, ...]:
        """Tokenize one complete pattern and append an ``END`` token."""

        if not isinstance(source, str):
            raise TypeError("pattern source must be a string")

        tokens: list[Token] = []
        position = 0
        while position < len(source):
            if source[position].isspace():
                position += 1
                continue

            parameter = self._parameter_recognizer.recognize(source, position)
            if parameter is not None:
                end = int(parameter.end)
                self._validate_parameter_end(source, position, end)
                tokens.append(
                    Token(
                        TokenKind.PARAMETER,
                        source[position:end],
                        SourceSpan(position, end),
                        parameter=getattr(
                            parameter,
                            "declaration",
                            parameter,
                        ),
                    )
                )
                position = end
                continue

            repeat = self._REPEAT.match(source, position)
            if repeat is not None:
                end = repeat.end()
                tokens.append(
                    Token(
                        TokenKind.REPEAT,
                        repeat.group(0),
                        SourceSpan(position, end),
                        repeat_bounds=(
                            int(repeat.group(1)),
                            int(repeat.group(2)),
                        ),
                    )
                )
                position = end
                continue

            kind = self._SYMBOLS.get(source[position])
            if kind is not None:
                tokens.append(
                    Token(
                        kind,
                        source[position],
                        SourceSpan(position, position + 1),
                    )
                )
                position += 1
                continue

            end = self._literal_end(source, position)
            if end == position:
                raise PatternLanguageError(
                    "malformed repeat operator",
                    source,
                    SourceSpan(position, min(position + 2, len(source))),
                )
            tokens.append(
                Token(
                    TokenKind.LITERAL,
                    source[position:end],
                    SourceSpan(position, end),
                )
            )
            position = end

        end_span = SourceSpan(len(source), len(source))
        tokens.append(Token(TokenKind.END, "", end_span))
        return tuple(tokens)

    @classmethod
    def _literal_end(cls, source: str, position: int) -> int:
        end = position
        while (
            end < len(source)
            and not source[end].isspace()
            and source[end] not in cls._SYMBOLS
            and source[end] != "&"
        ):
            end += 1
        return end

    @staticmethod
    def _validate_parameter_end(source: str, start: int, end: int) -> None:
        if end <= start:
            raise RuntimeError("parameter recognizer did not advance")
        if end > len(source):
            raise RuntimeError("parameter recognizer advanced beyond the source")
