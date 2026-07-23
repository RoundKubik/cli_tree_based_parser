"""Furthest-progress syntax diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field

from vrp_parser.results import ExpectedElement


@dataclass(slots=True)
class MatchDiagnostics:
    """Collect only expectations at the furthest reached character."""

    position: int = 0
    expected: set[str] = field(default_factory=set)
    non_parameter_path_progress: int = -1
    parameter_path_progress: int = -1

    def record(
        self,
        position: int,
        description: str,
        *,
        parameter_led: bool | None = None,
    ) -> None:
        """Record one failed expectation and how its route began.

        ``parameter_led`` is true only when the first consumed parameter was
        applicable.  A non-applicable first parameter is recorded as a
        non-parameter route and therefore cannot hide a useful keyword
        suggestion.
        """

        self._record_path_progress(position, parameter_led)
        if position > self.position:
            self.position = position
            self.expected = {description}
        elif position == self.position:
            self.expected.add(description)

    @property
    def allows_keyword_suggestions(self) -> bool:
        """Whether an applicable parameter route did not progress farther."""

        return (
            self.parameter_path_progress
            <= self.non_parameter_path_progress
        )

    def elements(self, *, offset: int = 0) -> tuple[ExpectedElement, ...]:
        return tuple(
            ExpectedElement(item, self.position + offset)
            for item in sorted(self.expected)
        )

    def _record_path_progress(
        self,
        position: int,
        parameter_led: bool | None,
    ) -> None:
        if parameter_led is None:
            return
        if not parameter_led:
            self.non_parameter_path_progress = max(
                self.non_parameter_path_progress,
                position,
            )
            return
        self.parameter_path_progress = max(
            self.parameter_path_progress,
            position,
        )
