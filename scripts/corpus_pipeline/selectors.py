"""Composite selectors must be adjacent placeholders in the original format."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParameterCombination:
    pattern: str
    names: tuple[str, ...]

    def exists(self) -> bool:
        if len(self.names) < 2 or len(set(self.names)) != len(self.names):
            return False
        sequence = r"\s+".join(re.escape(f"<{name}>") for name in self.names)
        return re.search(sequence, self.pattern) is not None
