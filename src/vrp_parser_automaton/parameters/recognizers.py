"""Declaration recognizers for bounded, enum, and exact placeholders."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ParameterDeclarationError
from .models import DeclarationRecognition


def _is_boundary(pattern: str, end: int) -> bool:
    return end == len(pattern) or pattern[end].isspace() or pattern[end] in "|}]*&"


def _ascii_lower(value: str) -> str:
    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lower = "abcdefghijklmnopqrstuvwxyz"
    return value.translate(str.maketrans(upper, lower))


@dataclass(frozen=True, slots=True)
class ExactDeclarationRecognizer:
    """Recognize one fixed placeholder spelling."""

    placeholder: str
    minimum: int | None = None
    maximum: int | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    def recognize(self, pattern: str, position: int) -> DeclarationRecognition | None:
        if not pattern.startswith(self.placeholder, position):
            return None
        end = position + len(self.placeholder)
        if not _is_boundary(pattern, end):
            return None
        return DeclarationRecognition(
            end=end,
            minimum=self.minimum,
            maximum=self.maximum,
            metadata=self.metadata,
        )


@dataclass(frozen=True, slots=True)
class BoundedDeclarationRecognizer:
    """Recognize ``NAME<minimum-maximum>`` declarations."""

    name: str
    base: int = 10
    allow_negative: bool = False

    def recognize(self, pattern: str, position: int) -> DeclarationRecognition | None:
        prefix = f"{self.name}<"
        if not pattern.startswith(prefix, position):
            return None
        closing = pattern.find(">", position + len(prefix))
        if closing < 0:
            raise ParameterDeclarationError(
                f"unclosed {self.name} declaration", position, len(pattern)
            )
        end = closing + 1
        bounds = pattern[position + len(prefix) : closing]
        match = re.fullmatch(
            rf"({self._number_pattern()})-({self._number_pattern()})",
            bounds,
        )
        if match is None:
            raise ParameterDeclarationError(
                f"invalid bounds in {self.name} declaration", position, end
            )
        minimum = self._parse_number(match.group(1))
        maximum = self._parse_number(match.group(2))
        if minimum > maximum:
            raise ParameterDeclarationError(
                f"minimum is greater than maximum in {self.name} declaration",
                position,
                end,
            )
        if not _is_boundary(pattern, end):
            return None
        return DeclarationRecognition(end=end, minimum=minimum, maximum=maximum)

    def _number_pattern(self) -> str:
        if self.base == 10:
            return r"[+-]?[0-9]+" if self.allow_negative else r"[0-9]+"
        if self.base == 16:
            return r"(?:0[xX])?[0-9A-Fa-f]+"
        raise ValueError(f"unsupported declaration bound base: {self.base}")

    def _parse_number(self, value: str) -> int:
        return int(value, self.base)


class EnumDeclarationRecognizer:
    """Recognize a complete comma-separated ``ENUM{...}`` declaration."""

    def recognize(self, pattern: str, position: int) -> DeclarationRecognition | None:
        prefix = "ENUM{"
        if not pattern.startswith(prefix, position):
            return None
        closing = pattern.find("}", position + len(prefix))
        if closing < 0:
            raise ParameterDeclarationError(
                "unclosed ENUM declaration", position, len(pattern)
            )
        end = closing + 1
        if not _is_boundary(pattern, end):
            return None
        choices = [
            choice.strip()
            for choice in pattern[position + len(prefix) : closing].split(",")
        ]
        if choices and choices[-1] == "":
            choices.pop()
        if not choices or any(not choice for choice in choices):
            raise ParameterDeclarationError(
                "ENUM must contain non-empty choices", position, end
            )
        if "..." in choices:
            raise ParameterDeclarationError(
                "ENUM cannot contain the abbreviated '...' choice", position, end
            )
        folded = [_ascii_lower(choice) for choice in choices]
        if len(set(folded)) != len(folded):
            raise ParameterDeclarationError(
                "ENUM choices must be unique (case-insensitive)", position, end
            )
        return DeclarationRecognition(end=end, choices=tuple(choices))
