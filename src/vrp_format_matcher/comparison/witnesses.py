"""Shortest structural witnesses computed from expressions, without enumeration."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_format_matcher.models import MappingLimitExceeded
from vrp_parser_automaton.patterns import (
    GroupMode,
    Literal,
    Parameter,
    Repeat,
    Sequence,
)
from vrp_parser_automaton.text import ascii_lower

from .structure import Expression

type Word = tuple[str, ...]


@dataclass(frozen=True)
class WordChoices:
    shortest: Word | None
    consuming: Word | None


class ProgramWords:
    def __init__(self, expression: Expression, maximum_length: int) -> None:
        self.expression = expression
        self.maximum = maximum_length

    def shortest(self) -> Word | None:
        return self._words(self.expression).shortest

    def _minimum(self, words: list[Word | None]) -> Word | None:
        valid = [word for word in words if word is not None]
        return min(valid, key=lambda word: (len(word), word)) if valid else None

    def _join(self, words: list[Word | None]) -> Word | None:
        if any(word is None for word in words):
            return None
        if sum(len(word) for word in words if word is not None) > self.maximum:
            raise MappingLimitExceeded("structural witness length limit exceeded")
        return tuple(symbol for word in words if word is not None for symbol in word)

    def _words(self, expression: Expression) -> WordChoices:
        if isinstance(expression, Literal):
            word = ("K:" + ascii_lower(expression.value),)
            return WordChoices(word, word)
        if isinstance(expression, Parameter):
            return WordChoices(("P",), ("P",))
        if isinstance(expression, Sequence):
            parts = [self._words(node) for node in expression.items]
            shortest = self._join([part.shortest for part in parts])
            consuming = self._minimum(
                [
                    self._join(
                        [
                            part.consuming if i == selected else part.shortest
                            for i, part in enumerate(parts)
                        ]
                    )
                    for selected in range(len(parts))
                ]
            )
            return WordChoices(shortest, consuming)
        if isinstance(expression, Repeat):
            body = self._words(expression.atom).consuming
            consuming = None
            if body is not None and expression.maximum > 0:
                count = max(1, expression.minimum)
                if len(body) * count > self.maximum:
                    raise MappingLimitExceeded(
                        "structural witness length limit exceeded"
                    )
                consuming = body * count
            return WordChoices(() if expression.minimum == 0 else consuming, consuming)
        choices = [self._words(branch) for branch in expression.alternatives]
        consuming = self._minimum([choice.consuming for choice in choices])
        if expression.mode in {GroupMode.OPTIONAL_ONE, GroupMode.OPTIONAL_SET}:
            shortest = ()
        elif expression.mode is GroupMode.REQUIRED_SET:
            shortest = consuming
        else:
            shortest = self._minimum([choice.shortest for choice in choices])
        return WordChoices(shortest, consuming)
