"""Bind a command's named placeholders to existing runtime parameter types."""

from collections.abc import Mapping
from dataclasses import replace

from .errors import ParameterRegistryError
from .models import ParameterDeclaration, ParameterFamily
from .registry import ParameterTypeRegistry


class AnnotatedDeclarations:
    """Recognize original spans, changing only the type of named declarations."""

    def __init__(
        self, registry: ParameterTypeRegistry, annotations: Mapping[str, str]
    ) -> None:
        self._registry = registry
        self._annotations = annotations
        self._unused = set(annotations)
        for name, type_id in annotations.items():
            if type_id == "unknown":
                continue
            parameter_type = registry.get(type_id)
            if parameter_type is None or parameter_type.family is ParameterFamily.ENUM:
                raise ParameterRegistryError(
                    f"Unsupported parameter type {type_id!r} for {name!r}; "
                    "use a registered value type or 'unknown'"
                )

    def recognize(self, source: str, position: int) -> ParameterDeclaration | None:
        declaration = self._registry.recognize(source, position)
        if declaration is None:
            return None
        spelling = declaration.source
        if not (spelling.startswith("<") and spelling.endswith(">")):
            return declaration
        name = spelling[1:-1]
        self._unused.discard(name)
        type_id = self._annotations.get(name, "unknown")
        # Explicit built-ins and custom recognizers keep their readers and bounds.
        if declaration.type_id != "named" or type_id == "unknown":
            return declaration
        return replace(declaration, type_id=type_id)

    def validate(self) -> None:
        if self._unused:
            raise ParameterRegistryError(
                "Type annotations reference absent named parameters: "
                f"{sorted(self._unused)}"
            )
