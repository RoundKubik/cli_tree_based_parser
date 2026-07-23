"""Compile source strings into the merged command graph."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256

from vrp_parser.errors import PatternCompilationError, PatternIssue
from vrp_parser.graph import CommandGraph, CommandGraphBuilder, PatternSource
from vrp_parser.parameters import (
    ParameterDeclarationError,
    ParameterRegistryError,
    ParameterTypeRegistry,
)
from vrp_parser.patterns import (
    PatternLanguageError,
    PatternParser,
    RuntimePatternPolicy,
    SourceSpan,
)


class PatternCompiler:
    """Parse every pattern, report all failures, then build one graph."""

    def __init__(
        self,
        parameter_types: ParameterTypeRegistry,
        graph_builder: CommandGraphBuilder | None = None,
        pattern_policy: RuntimePatternPolicy | None = None,
    ) -> None:
        self._parser = PatternParser(parameter_types)
        self._graph_builder = graph_builder or CommandGraphBuilder()
        self._pattern_policy = pattern_policy or RuntimePatternPolicy()

    def compile(self, commands: tuple[str, ...]) -> CommandGraph:
        patterns: list[PatternSource] = []
        issues: list[PatternIssue] = []
        occurrences: dict[str, int] = defaultdict(int)

        for index, original in enumerate(commands):
            try:
                ast = self._parser.parse(original)
                self._pattern_policy.validate(ast, original)
            except (
                PatternLanguageError,
                ParameterDeclarationError,
                ParameterRegistryError,
            ) as error:
                issues.append(self._issue(index, original, error))
                continue
            patterns.append(
                PatternSource(
                    pattern_id=self._pattern_id(
                        original,
                        occurrences[original],
                    ),
                    index=index,
                    original=original,
                    ast=ast,
                )
            )
            occurrences[original] += 1

        if issues:
            raise PatternCompilationError(tuple(issues))
        return self._graph_builder.build(tuple(patterns))

    @staticmethod
    def _pattern_id(original: str, occurrence: int) -> str:
        digest = sha256(original.encode("utf-8")).hexdigest()[:20]
        return f"pattern:{digest}:{occurrence}"

    @staticmethod
    def _issue(
        index: int,
        pattern: str,
        error: Exception,
    ) -> PatternIssue:
        span = getattr(error, "span", None)
        if not isinstance(span, SourceSpan):
            start = getattr(error, "start", 0)
            end = getattr(error, "end", len(pattern))
            span = SourceSpan(
                start if isinstance(start, int) else 0,
                end if isinstance(end, int) else len(pattern),
            )
        message = getattr(error, "message", str(error))
        return PatternIssue(
            pattern_index=index,
            pattern=pattern,
            message=str(message),
            span=span,
        )
