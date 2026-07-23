"""Small cursor operations for whitespace-delimited CLI input."""

from __future__ import annotations

from dataclasses import dataclass


def ascii_lower(value: str) -> str:
    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lower = "abcdefghijklmnopqrstuvwxyz"
    return value.translate(str.maketrans(upper, lower))


@dataclass(frozen=True, slots=True)
class CommandToken:
    raw: str
    start: int
    end: int


class CommandText:
    """Read tokens while preserving their original character spans."""

    def __init__(self, value: str) -> None:
        self.value = value

    def skip_space(self, position: int) -> int:
        while position < len(self.value) and self.value[position].isspace():
            position += 1
        return position

    def token(self, position: int) -> CommandToken | None:
        start = self.skip_space(position)
        if start == len(self.value):
            return None
        end = start
        while end < len(self.value) and not self.value[end].isspace():
            end += 1
        return CommandToken(self.value[start:end], start, end)

    def at_end(self, position: int) -> bool:
        return self.skip_space(position) == len(self.value)
