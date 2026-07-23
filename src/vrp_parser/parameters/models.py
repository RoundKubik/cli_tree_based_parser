"""Immutable values and small interfaces for parameter types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ParameterStatus(StrEnum):
    """Outcome of probing a value against one parameter type."""

    NOT_APPLICABLE = "not_applicable"
    VALID = "valid"
    INVALID = "invalid"


class ParameterFamily(StrEnum):
    """Broad behavior category used without hard-coding concrete type IDs."""

    ENUM = "enum"
    STRUCTURED = "structured"
    NUMERIC = "numeric"
    GENERIC = "generic"
    REMAINDER = "remainder"


@dataclass(frozen=True, slots=True)
class ParameterIssue:
    """Machine-readable details of an invalid declaration or value."""

    code: str
    message: str
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterResult:
    """Tri-state result returned by every parameter type."""

    status: ParameterStatus
    normalized: object | None = None
    issue: ParameterIssue | None = None

    def __post_init__(self) -> None:
        if self.status is ParameterStatus.INVALID and self.issue is None:
            raise ValueError("an INVALID parameter result requires an issue")
        if self.status is not ParameterStatus.INVALID and self.issue is not None:
            raise ValueError("only an INVALID parameter result can contain an issue")
        if (
            self.status is ParameterStatus.NOT_APPLICABLE
            and self.normalized is not None
        ):
            raise ValueError("a NOT_APPLICABLE result cannot contain a value")

    @property
    def applicable(self) -> bool:
        return self.status is not ParameterStatus.NOT_APPLICABLE

    @property
    def valid(self) -> bool:
        return self.status is ParameterStatus.VALID

    @property
    def message(self) -> str | None:
        return self.issue.message if self.issue else None

    @classmethod
    def not_applicable(cls) -> ParameterResult:
        return cls(ParameterStatus.NOT_APPLICABLE)

    @classmethod
    def success(cls, normalized: object) -> ParameterResult:
        return cls(ParameterStatus.VALID, normalized=normalized)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        expected: str | None = None,
        actual: str | None = None,
    ) -> ParameterResult:
        return cls(
            ParameterStatus.INVALID,
            issue=ParameterIssue(
                code=code,
                message=message,
                expected=expected,
                actual=actual,
            ),
        )


@dataclass(frozen=True, slots=True)
class ParameterDeclaration:
    """A parameter placeholder recognized inside a command pattern."""

    type_id: str
    source: str
    start: int
    end: int
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("declaration span must satisfy 0 <= start <= end")
        if self.end - self.start != len(self.source):
            raise ValueError("declaration source length must match its span")


@dataclass(frozen=True, slots=True)
class DeclarationRecognition:
    """Recognizer output before a registry attaches its type ID."""

    end: int
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ParameterToken:
    """One value read from a concrete CLI command line."""

    raw: str
    start: int
    end: int
    next_position: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("token span must satisfy 0 <= start <= end")
        if self.end - self.start != len(self.raw):
            raise ValueError("token raw length must match its span")
        if self.next_position < self.end:
            raise ValueError("next_position cannot precede the token end")


class DeclarationRecognizer(Protocol):
    """Recognize one declaration beginning at an exact pattern offset."""

    def recognize(
        self, pattern: str, position: int
    ) -> DeclarationRecognition | None: ...


class ParameterReader(Protocol):
    """Read one value from a concrete CLI line."""

    def read(self, text: str, position: int) -> ParameterToken | None: ...


class ParameterValidator(Protocol):
    """Validate a value for an already recognized declaration."""

    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult: ...


@dataclass(frozen=True, slots=True)
class ParameterType:
    """Composition root for one independently extensible parameter type."""

    type_id: str
    family: ParameterFamily
    declaration_recognizer: DeclarationRecognizer
    reader: ParameterReader
    validator: ParameterValidator

    def recognize(
        self, pattern: str, position: int = 0
    ) -> ParameterDeclaration | None:
        recognized = self.declaration_recognizer.recognize(pattern, position)
        if recognized is None:
            return None
        return ParameterDeclaration(
            type_id=self.type_id,
            source=pattern[position : recognized.end],
            start=position,
            end=recognized.end,
            minimum=recognized.minimum,
            maximum=recognized.maximum,
            choices=recognized.choices,
            metadata=recognized.metadata,
        )

    def read(self, text: str, position: int = 0) -> ParameterToken | None:
        return self.reader.read(text, position)

    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        if declaration.type_id != self.type_id:
            return ParameterResult.not_applicable()
        return self.validator.probe(raw, declaration)
