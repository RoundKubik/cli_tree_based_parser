"""Furthest-progress syntax diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field

from vrp_parser.results import ExpectedElement


@dataclass(slots=True)
class MatchDiagnostics:
    """Collect only expectations at the furthest reached character."""

    position: int = 0
    expected: set[str] = field(default_factory=set)

    def record(self, position: int, description: str) -> None:
        if position > self.position:
            self.position = position
            self.expected = {description}
        elif position == self.position:
            self.expected.add(description)

    def elements(self, *, offset: int = 0) -> tuple[ExpectedElement, ...]:
        return tuple(
            ExpectedElement(item, self.position + offset)
            for item in sorted(self.expected)
        )
