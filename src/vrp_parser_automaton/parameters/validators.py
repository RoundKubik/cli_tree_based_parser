"""Focused validators used by built-in parameter types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv6Address

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


def _looks_like_ipv6(value: str) -> bool:
    return value.count(":") >= 2


class IntegerValidator:
    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        if re.fullmatch(r"[+-]?[0-9]+", raw) is None:
            return ParameterResult.not_applicable()
        value = int(raw, 10)
        return _bounded_number(value, declaration) or ParameterResult.success(value)


class HexValidator:
    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        if re.fullmatch(r"(?:0[xX])?[0-9A-Fa-f]+", raw) is None:
            return ParameterResult.not_applicable()
        value = int(raw, 16)
        return _bounded_number(value, declaration) or ParameterResult.success(value)


class TokenStringValidator:
    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        if not raw or any(character.isspace() for character in raw):
            return ParameterResult.failure(
                "invalid_token",
                "value must be one non-whitespace token",
                actual=raw,
            )
        return _bounded_length(raw, declaration) or ParameterResult.success(raw)


class TextValidator:
    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        return _bounded_length(raw, declaration) or ParameterResult.success(raw)


class EnumValidator:
    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
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

    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
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
    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
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
        normalized = "-".join(group.lower().zfill(4) for group in match.groups())
        return ParameterResult.success(normalized)


class IPv4AddressValidator:
    """Validate dotted-decimal IPv4 syntax without command-specific rules."""

    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        del declaration
        if not self._looks_like_address(raw):
            return ParameterResult.not_applicable()

        octets = raw.split(".")
        if len(octets) != 4 or any(
            re.fullmatch(r"[0-9]{1,3}", octet) is None for octet in octets
        ):
            return self._failure(raw)

        values = tuple(int(octet, 10) for octet in octets)
        if any(value > 255 for value in values):
            return self._failure(raw)
        return ParameterResult.success(".".join(str(value) for value in values))

    @staticmethod
    def _looks_like_address(raw: str) -> bool:
        return "." in raw and re.fullmatch(r"[0-9.+-]+", raw) is not None

    @staticmethod
    def _failure(raw: str) -> ParameterResult:
        return ParameterResult.failure(
            "invalid_ipv4_address",
            "value must be a valid IPv4 address",
            expected="four decimal octets from 0 to 255",
            actual=raw,
        )


class IPv6AddressValidator:
    """Validate IPv6 syntax and return a canonical compressed address."""

    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        del declaration
        if not _looks_like_ipv6(raw):
            return ParameterResult.not_applicable()
        if "%" in raw:
            return self._failure(raw)
        try:
            address = IPv6Address(raw)
        except ValueError:
            return self._failure(raw)
        return ParameterResult.success(str(address))

    @staticmethod
    def _failure(raw: str) -> ParameterResult:
        return ParameterResult.failure(
            "invalid_ipv6_address",
            "value must be a valid IPv6 address",
            expected="IPv6 colon-hexadecimal notation",
            actual=raw,
        )


class IPv6PrefixValidator:
    """Validate an IPv6 address followed by a decimal prefix length."""

    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        del declaration
        if not _looks_like_ipv6(raw) and not raw.startswith("/"):
            return ParameterResult.not_applicable()

        parts = raw.split("/")
        if (
            len(parts) != 2
            or "%" in parts[0]
            or re.fullmatch(r"[0-9]+", parts[1]) is None
            or len(parts[1]) > 3
        ):
            return self._failure(raw)

        try:
            address = IPv6Address(parts[0])
        except ValueError:
            return self._failure(raw)
        prefix_length = int(parts[1], 10)
        if prefix_length > 128:
            return self._failure(raw)
        return ParameterResult.success(f"{address}/{prefix_length}")

    @staticmethod
    def _failure(raw: str) -> ParameterResult:
        return ParameterResult.failure(
            "invalid_ipv6_prefix",
            "value must be a valid IPv6 prefix",
            expected="IPv6 address followed by /0 through /128",
            actual=raw,
        )
