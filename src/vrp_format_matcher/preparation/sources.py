"""Target-side ASTs for offline matching without a runtime command parser."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from vrp_format_matcher.documents.parameters import DocumentParameterRecognizer
from vrp_format_matcher.models import FormatError
from vrp_parser_automaton.automata.model import PatternSource
from vrp_parser_automaton.parameters import default_parameter_registry
from vrp_parser_automaton.patterns import PatternParser
from vrp_parser_automaton.patterns.lexer import RecognizedParameter


class TargetParameterRecognizer:
    def __init__(self) -> None:
        self._named = DocumentParameterRecognizer()
        self._typed = default_parameter_registry()

    def recognize(self, source: str, position: int) -> RecognizedParameter | None:
        return self._named.recognize(source, position) or self._typed.recognize(
            source, position
        )


@dataclass(frozen=True)
class TargetFormats:
    formats: Sequence[str]
    syntax: Literal["device", "document"] = "device"

    def patterns(self) -> tuple[PatternSource, ...]:
        if isinstance(self.formats, (str, bytes)):
            raise FormatError("target formats must be an array of strings")
        if self.syntax not in {"device", "document"}:
            raise ValueError("target syntax must be 'device' or 'document'")
        parser = PatternParser(
            DocumentParameterRecognizer()
            if self.syntax == "document"
            else TargetParameterRecognizer()
        )
        patterns = []
        occurrences: dict[str, int] = defaultdict(int)
        for index, source in enumerate(self.formats):
            if not isinstance(source, str) or not source.strip():
                raise FormatError(f"invalid target format at index {index}")
            ast = parser.parse(source)
            digest = sha256(source.encode("utf-8")).hexdigest()[:20]
            pattern_id = f"pattern:{digest}:{occurrences[source]}"
            occurrences[source] += 1
            patterns.append(PatternSource(pattern_id, index, source, ast))
        return tuple(patterns)
