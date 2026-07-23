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
        return ParseError(
            code=ErrorCode.VALIDATION_ERROR,
            message="command shape matched, but one or more parameters are invalid",
            position=(
                unique_failures[0].span.start if unique_failures else None
            ),
            failures=unique_failures,
            candidate_patterns=tuple(dict.fromkeys(patterns)),
            candidate_variations=tuple(dict.fromkeys(variations)),
        )

    @staticmethod
    def _failure(
        rejected: RejectedParameter,
        *,
        span_offset: int,
    ) -> ValidationFailure:
        issue = rejected.result.issue
        if rejected.result.status is ParameterStatus.NOT_APPLICABLE:
            message = f"value does not match {rejected.declaration.source}"
        else:
            message = issue.message if issue else "invalid parameter value"
        return ValidationFailure(
            type_id=rejected.declaration.type_id,
            declaration=rejected.declaration.source,
            raw=rejected.token.raw,
            span=TextSpan(
                rejected.token.start + span_offset,
                rejected.token.end + span_offset,
            ),
            message=message,
        )
