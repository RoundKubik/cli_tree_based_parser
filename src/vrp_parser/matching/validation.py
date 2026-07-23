"""Build public validation errors from rejected terminal candidates."""

from __future__ import annotations

from vrp_parser.graph import CommandGraph
from vrp_parser.parameters import ParameterStatus
from vrp_parser.results import (
    ErrorCode,
    ParseError,
    TextSpan,
    ValidationFailure,
)

from .state import Candidate, RejectedParameter


class ValidationErrorFactory:
    """Preserve every relevant pattern and rejected parameter."""

    def create(
        self,
        candidates: tuple[Candidate, ...],
        graph: CommandGraph,
        *,
        span_offset: int,
    ) -> ParseError:
        ordered = sorted(
            candidates,
            key=lambda item: (
                graph.routes[item.route_id].pattern.index,
                item.route_id,
            ),
        )
        failures: list[ValidationFailure] = []
        patterns: list[str] = []
        variations: list[str] = []
        for candidate in ordered:
            route = graph.routes[candidate.route_id]
            patterns.append(route.pattern.original)
            variations.append(" ".join(candidate.state.parts))
            failures.extend(
                self._failure(item, span_offset=span_offset)
                for item in candidate.state.rejected
            )

        unique_failures = tuple(dict.fromkeys(failures))
        unique_patterns = tuple(dict.fromkeys(patterns))
        unique_variations = tuple(dict.fromkeys(variations))
        return ParseError(
            code=ErrorCode.VALIDATION_ERROR,
            message=self._message(unique_failures, unique_patterns),
            position=(
                unique_failures[0].span.start if unique_failures else None
            ),
            failures=unique_failures,
            candidate_patterns=unique_patterns,
            candidate_variations=unique_variations,
        )

    @staticmethod
    def _failure(
        rejected: RejectedParameter,
        *,
        span_offset: int,
    ) -> ValidationFailure:
        issue = rejected.result.issue
        expected: str | None
        actual: str | None
        if rejected.result.status is ParameterStatus.NOT_APPLICABLE:
            message = f"value does not match {rejected.declaration.source}"
            reason_code = "not_applicable"
            expected = rejected.declaration.source
            actual = rejected.token.raw
        else:
            message = issue.message if issue else "invalid parameter value"
            reason_code = issue.code if issue else "invalid_value"
            expected = issue.expected if issue else None
            actual = issue.actual if issue else rejected.token.raw
        return ValidationFailure(
            type_id=rejected.declaration.type_id,
            declaration=rejected.declaration.source,
            raw=rejected.token.raw,
            span=TextSpan(
                rejected.token.start + span_offset,
                rejected.token.end + span_offset,
            ),
            message=message,
            reason_code=reason_code,
            expected=expected,
            actual=actual,
        )

    @staticmethod
    def _message(
        failures: tuple[ValidationFailure, ...],
        patterns: tuple[str, ...],
    ) -> str:
        if not failures:
            return (
                "The command structure matched a known pattern, but its "
                "parameters failed validation. Inspect failures for details."
            )

        visible = failures[:3]
        details = "; ".join(
            (
                f"value {failure.raw!r} is invalid for "
                f"{failure.declaration}: {failure.message}"
            )
            for failure in visible
        )
        if len(failures) > len(visible):
            remaining = len(failures) - len(visible)
            noun = "failure" if remaining == 1 else "failures"
            details += f"; and {remaining} more {noun}"

        if len(patterns) == 1:
            matched = f"pattern {patterns[0]!r}"
        else:
            matched = f"{len(patterns)} candidate patterns"
        value_noun = "value" if len(failures) == 1 else "values"
        return (
            f"The command structure matched {matched}, but "
            f"{len(failures)} parameter {value_noun} failed validation: "
            f"{details}. Inspect failures, candidate_patterns, and "
            "candidate_variations for complete details."
        )
