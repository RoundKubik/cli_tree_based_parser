"""Source ownership, stable IDs, and aggregated pattern syntax errors."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256

from vrp_parser_automaton.errors import PatternCompilationError, PatternIssue
from vrp_parser_automaton.parameters import (
    ParameterDeclarationError,
    ParameterRegistryError,
    ParameterTypeRegistry,
)
from vrp_parser_automaton.patterns import (
    PatternLanguageError,
    PatternParser,
    RuntimePatternPolicy,
    SourceSpan,
)

from .model import PatternSource


@dataclass(frozen=True)
class PatternFailure:
    index: int
    source: str
    error: Exception

    def issue(self) -> PatternIssue:
        span = getattr(self.error, "span", None)
        if not isinstance(span, SourceSpan):
            span = SourceSpan(
                getattr(self.error, "start", 0),
                getattr(self.error, "end", len(self.source)),
            )
        return PatternIssue(
            self.index,
            self.source,
            str(getattr(self.error, "message", self.error)),
            span,
        )


@dataclass(frozen=True)
class PatternSources:
    commands: tuple[str, ...]
    parameter_types: ParameterTypeRegistry

    def parsed(self) -> tuple[PatternSource, ...]:
        parser = PatternParser(self.parameter_types)
        policy = RuntimePatternPolicy()
        patterns = []
        issues = []
        occurrences: dict[str, int] = defaultdict(int)
        for index, original in enumerate(self.commands):
            try:
                ast = parser.parse(original)
                policy.validate(ast, original)
            except (
                PatternLanguageError,
                ParameterDeclarationError,
                ParameterRegistryError,
            ) as error:
                issues.append(PatternFailure(index, original, error).issue())
                continue
            digest = sha256(original.encode("utf-8")).hexdigest()[:20]
            pattern_id = f"pattern:{digest}:{occurrences[original]}"
            occurrences[original] += 1
            patterns.append(PatternSource(pattern_id, index, original, ast))
        if issues:
            raise PatternCompilationError(tuple(issues))
        return tuple(patterns)
