"""Conversion of public result values to JSON-compatible Python objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any


class JsonValueConverter:
    """Preserve JSON values and stringify unsupported plugin objects."""

    def convert(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: self.convert(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, Mapping):
            return {
                str(key): self.convert(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.convert(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return [
                self.convert(item)
                for item in sorted(value, key=repr)
            ]
        return str(value)
