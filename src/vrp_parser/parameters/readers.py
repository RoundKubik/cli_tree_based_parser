"""Reusable strategies for reading values from CLI text."""

from __future__ import annotations

from .models import ParameterToken


def _skip_whitespace(text: str, position: int) -> int:
    if position < 0 or position > len(text):
        raise ValueError("parameter position is outside the command line")
    while position < len(text) and text[position].isspace():
        position += 1
    return position


class SingleTokenReader:
    """Read one non-whitespace token; punctuation remains ordinary data."""

    def read(self, text: str, position: int) -> ParameterToken | None:
        start = _skip_whitespace(text, position)
        if start == len(text):
            return None
        end = start
        while end < len(text) and not text[end].isspace():
            end += 1
        return ParameterToken(text[start:end], start, end, end)


class RemainderReader:
    """Read the non-empty remainder, preserving its internal whitespace."""

    def read(self, text: str, position: int) -> ParameterToken | None:
        start = _skip_whitespace(text, position)
        if start == len(text):
            return None
        end = len(text)
        return ParameterToken(text[start:end], start, end, end)
