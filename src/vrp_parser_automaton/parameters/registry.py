"""A polymorphic registry of independent parameter types."""

from __future__ import annotations

import re

from .errors import ParameterDeclarationError, ParameterRegistryError
from .models import (
    ParameterDeclaration,
    ParameterFamily,
    ParameterResult,
    ParameterToken,
    ParameterType,
)


class ParameterTypeRegistry:
    """Discover parameter declarations and delegate runtime operations."""

    def __init__(self, parameter_types: tuple[ParameterType, ...] = ()) -> None:
        self._types: dict[str, ParameterType] = {}
        self._frozen = False
        for parameter_type in parameter_types:
            self.register(parameter_type)

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    @property
    def parameter_types(self) -> tuple[ParameterType, ...]:
        return tuple(self._types.values())

    def register(self, parameter_type: ParameterType) -> ParameterTypeRegistry:
        if self._frozen:
            raise ParameterRegistryError("the parameter registry is frozen")
        if re.fullmatch(r"[a-z][a-z0-9-]*", parameter_type.type_id) is None:
            raise ParameterRegistryError(
                f"invalid parameter type ID {parameter_type.type_id!r}"
            )
        if parameter_type.type_id in self._types:
            raise ParameterRegistryError(
                f"parameter type {parameter_type.type_id!r} is already registered"
            )
        self._types[parameter_type.type_id] = parameter_type
        return self

    def freeze(self) -> ParameterTypeRegistry:
        self._frozen = True
        return self

    def clone(self) -> ParameterTypeRegistry:
        return ParameterTypeRegistry(self.parameter_types)

    def get(self, type_id: str) -> ParameterType | None:
        """Return a type by ID, or ``None`` when it is not registered."""

        return self._types.get(type_id)

    def family_of(self, type_id: str) -> ParameterFamily | None:
        """Return the broad family of a registered type."""

        parameter_type = self._types.get(type_id)
        return parameter_type.family if parameter_type is not None else None

    def recognize(self, pattern: str, position: int = 0) -> ParameterDeclaration | None:
        """Recognize the longest declaration beginning at ``position``."""

        matches = tuple(
            declaration
            for parameter_type in self._types.values()
            if (declaration := parameter_type.recognize(pattern, position)) is not None
        )
        if not matches:
            return None
        longest_end = max(match.end for match in matches)
        longest = tuple(match for match in matches if match.end == longest_end)
        if len(longest) > 1:
            type_ids = ", ".join(sorted(match.type_id for match in longest))
            raise ParameterRegistryError(
                f"ambiguous declaration at character {position}: {type_ids}"
            )
        return longest[0]

    def read(
        self,
        declaration: ParameterDeclaration,
        text: str,
        position: int = 0,
    ) -> ParameterToken | None:
        parameter_type = self._types.get(declaration.type_id)
        if parameter_type is None:
            return None
        return parameter_type.read(text, position)

    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        parameter_type = self._types.get(declaration.type_id)
        if parameter_type is None:
            return ParameterResult.not_applicable()
        return parameter_type.probe(raw, declaration)

    def evaluate(self, declaration_pattern: str, raw: str) -> ParameterResult:
        """Recognize a complete declaration and probe ``raw`` in one call."""

        try:
            declaration = self.recognize(declaration_pattern)
        except ParameterDeclarationError as error:
            return ParameterResult.failure(
                "invalid_declaration",
                error.message,
                actual=declaration_pattern,
            )
        if declaration is None or declaration.end != len(declaration_pattern):
            return ParameterResult.not_applicable()
        return self.probe(raw, declaration)
