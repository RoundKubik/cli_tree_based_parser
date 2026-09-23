"""Documentation inputs, validated before they enter structural matching."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vrp_format_matcher.models import FormatError
from vrp_parser_automaton.patterns import PatternParser
from vrp_parser_automaton.patterns import Sequence as PatternSequence

from .parameters import DocumentParameterRecognizer


@dataclass(frozen=True)
class DocumentFormat:
    document_id: str
    format: str
    ast: PatternSequence


@dataclass(frozen=True)
class DocumentSource:
    source: Mapping[str, Any]
    default_id: str

    def parsed(self) -> DocumentFormat:
        document = self.source
        if not isinstance(document, Mapping):
            raise FormatError("each document must be a format object")
        document_id = document.get("id", self.default_id)
        if not isinstance(document_id, str) or not document_id:
            raise FormatError("document ids must be nonempty and unique")
        pattern = document.get("format")
        if not isinstance(pattern, str) or not pattern.strip():
            raise FormatError("each document requires a nonempty format")

        ast = PatternParser(DocumentParameterRecognizer()).parse(pattern)
        return DocumentFormat(
            document_id=document_id,
            format=pattern,
            ast=ast,
        )


@dataclass(frozen=True)
class Documentation:
    sources: Sequence[Mapping[str, Any]]

    def documents(self) -> Iterator[DocumentFormat]:
        if not isinstance(self.sources, Sequence) or isinstance(
            self.sources, (str, bytes)
        ):
            raise FormatError("documents must be an array of format objects")
        seen_ids: set[str] = set()
        for index, source in enumerate(self.sources):
            document = DocumentSource(source, f"doc:{index}").parsed()
            if document.document_id in seen_ids:
                raise FormatError("document ids must be nonempty and unique")
            seen_ids.add(document.document_id)
            yield document
