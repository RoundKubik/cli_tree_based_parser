"""Focused validators used by built-in parameter types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .models import ParameterDeclaration, ParameterResult


def _bounded_number(
    value: int, declaration: ParameterDeclaration
) -> ParameterResult | None:
    if declaration.minimum is not None and value < declaration.minimum:
        return ParameterResult.failure(
            "below_minimum",
            f"value must be at least {declaration.minimum}",
            expected=f">= {declaration.minimum}",
            actual=str(value),
        )
    if declaration.maximum is not None and value > declaration.maximum:
        return ParameterResult.failure(
            "above_maximum",
            f"value must be at most {declaration.maximum}",
            expected=f"<= {declaration.maximum}",
            actual=str(value),
        )
    return None


def _bounded_length(
    value: str, declaration: ParameterDeclaration
) -> ParameterResult | None:
    length = len(value)
    if declaration.minimum is not None and length < declaration.minimum:
        return ParameterResult.failure(
            "too_short",
            f"value must contain at least {declaration.minimum} characters",
            expected=f"length >= {declaration.minimum}",
            actual=str(length),
        )
    if declaration.maximum is not None and length > declaration.maximum:
        return ParameterResult.failure(
            "too_long",
            f"value must contain at most {declaration.maximum} characters",
            expected=f"length <= {declaration.maximum}",
            actual=str(length),
        )
    return None


def _ascii_lower(value: str) -> str:
    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lower = "abcdefghijklmnopqrstuvwxyz"
    return value.translate(str.maketrans(upper, lower))


class IntegerValidator:
    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        if re.fullmatch(r"[+-]?[0-9]+", raw) is None:
            return ParameterResult.not_applicable()
        value = int(raw, 10)
        return _bounded_number(value, declaration) or ParameterResult.success(value)


class HexValidator:
    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        if re.fullmatch(r"(?:0[xX])?[0-9A-Fa-f]+", raw) is None:
            return ParameterResult.not_applicable()
        value = int(raw, 16)
        return _bounded_number(value, declaration) or ParameterResult.success(value)


class TokenStringValidator:
    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        if not raw or any(character.isspace() for character in raw):
            return ParameterResult.failure(
                "invalid_token",
                "value must be one non-whitespace token",
                actual=raw,
            )
        return _bounded_length(raw, declaration) or ParameterResult.success(raw)


class TextValidator:
    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        return _bounded_length(raw, declaration) or ParameterResult.success(raw)


class EnumValidator:
    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        wanted = _ascii_lower(raw)
        for choice in declaration.choices:
            if _ascii_lower(choice) == wanted:
                return ParameterResult.success(choice)
        return ParameterResult.not_applicable()


@dataclass(frozen=True, slots=True)
class DateTimeValidator:
    """Validate an exact-width calendar/time representation."""

    expression: str
    datetime_format: str
    description: str
    prefix_for_parsing: str = ""

    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        del declaration
        if re.fullmatch(self.expression, raw) is None:
            return ParameterResult.not_applicable()
        try:
            datetime.strptime(
                self.prefix_for_parsing + raw,
                self.datetime_format,
            )
        except ValueError:
            return self._failure(raw)
        return ParameterResult.success(raw)

    def _failure(self, raw: str) -> ParameterResult:
        return ParameterResult.failure(
            "invalid_datetime",
            f"value must be a valid {self.description}",
            expected=self.description,
            actual=raw,
        )


class MacValidator:
    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        del declaration
        match = re.fullmatch(
            r"([0-9A-Fa-f]{1,4})-([0-9A-Fa-f]{1,4})-([0-9A-Fa-f]{1,4})",
            raw,
        )
        if match is None:
            if re.fullmatch(r"[0-9A-Fa-f-]+", raw) is None or "-" not in raw:
                return ParameterResult.not_applicable()
            return ParameterResult.failure(
                "invalid_mac",
                "value must contain three hexadecimal groups",
                expected="H-H-H",
                actual=raw,
            )
        normalized = "-".join(
            group.lower().zfill(4) for group in match.groups()
        )
        return ParameterResult.success(normalized)
