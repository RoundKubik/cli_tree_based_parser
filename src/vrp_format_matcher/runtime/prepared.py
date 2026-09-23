"""Public prepared-metadata facade: lookup, execution and persistence."""

from __future__ import annotations

from typing import Any

from vrp_format_matcher.models import (
    MappingLimits,
    MetadataError,
    MetadataReport,
    PreparedPair,
)
from vrp_format_matcher.storage.artifacts import ArtifactReader, MetadataArtifact
from vrp_parser_automaton.results import ParsedCommand

from .evaluation import PairEvaluation


class PreparedMetadata:
    """Executable data only: no document AST, parser, or type correspondence table."""

    def __init__(self, pairs: tuple[PreparedPair, ...], limits: MappingLimits) -> None:
        self._artifact = MetadataArtifact(pairs, limits)
        self._by_pattern: dict[str, list[PairEvaluation]] = {}
        for pair in pairs:
            self._by_pattern.setdefault(pair.pattern_id, []).append(
                PairEvaluation(pair, limits.runtime_configurations)
            )

    @property
    def pairs(self) -> tuple[PreparedPair, ...]:
        return self._artifact.pairs

    @property
    def limits(self) -> MappingLimits:
        return self._artifact.limits

    def evaluate(self, line: ParsedCommand) -> MetadataReport:
        if not isinstance(line, ParsedCommand):
            raise MetadataError(
                "metadata evaluation requires a successfully parsed command"
            )
        return MetadataReport(
            tuple(
                evaluation.evaluate(line, match)
                for match in line.matches
                for evaluation in self._by_pattern.get(match.pattern_id, ())
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return self._artifact.to_dict()

    def to_json(self) -> str:
        return self._artifact.to_json()

    @classmethod
    def from_json(cls, source: str) -> PreparedMetadata:
        artifact = ArtifactReader(source).read()
        return cls(artifact.pairs, artifact.limits)
