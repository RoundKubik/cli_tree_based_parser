"""Errors raised while defining or discovering parameter types."""

from __future__ import annotations


class ParameterDeclarationError(ValueError):
    """A placeholder starts like a known type but is malformed."""

    def __init__(self, message: str, start: int, end: int) -> None:
        self.message = message
        self.start = start
        self.end = end
        super().__init__(f"{message} at characters {start}:{end}")


class ParameterRegistryError(ValueError):
    """Invalid registration or ambiguous declaration recognition."""
