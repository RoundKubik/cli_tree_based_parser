"""Documentation inputs, validated before they enter structural matching."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vrp_parser.patterns import PatternParser
from vrp_parser.patterns import Sequence as PatternSequence

from .models import MetadataError
from .patterns import DocumentParameterRecognizer, parameter_names
from .predicates import validate_predicate


@dataclass(frozen=True)
class MetadataDocument:
    document_id: str
    format: str
    ast: PatternSequence
    creates: tuple[dict[str, Any], ...]
    requires: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class DocumentRules:
    """Rules whose parameter references belong to one documentation pattern."""

    source: Any
    kind: str
    parameter_names: frozenset[str]

    def validated(self) -> tuple[dict[str, Any], ...]:
        if not isinstance(self.source, list):
            raise MetadataError(f"{self.kind} must be an array")
        for rule in self.source:
            if not isinstance(rule, dict):
                raise MetadataError(f"each {self.kind} rule must be an object")
            self._validate_target(rule)
            validate_predicate(rule.get("when", "always"), self.parameter_names)
        return tuple(self.source)

    def _validate_target(self, rule: dict[str, Any]) -> None:
        if rule.get("condition") == "command":
            return
        if rule.get("condition") != "parameter_name":
            raise MetadataError("condition must be 'command' or 'parameter_name'")
        name = rule.get("parameter_name")
        if not isinstance(name, str) or name not in self.parameter_names:
            raise MetadataError(f"unknown target parameter: {name!r}")


@dataclass(frozen=True)
class DocumentSource:
    source: Mapping[str, Any]
    default_id: str

    def parsed(self) -> MetadataDocument:
        document = self._owned_copy()
        document_id = document.get("id", self.default_id)
        if not isinstance(document_id, str) or not document_id:
            raise MetadataError("document ids must be nonempty and unique")
        pattern = document.get("format")
        if not isinstance(pattern, str) or not pattern.strip():
            raise MetadataError("each document requires a nonempty format")

        ast = PatternParser(DocumentParameterRecognizer()).parse(pattern)
        names = parameter_names(ast)
        return MetadataDocument(
            document_id=document_id,
            format=pattern,
            ast=ast,
            creates=DocumentRules(
                document.get("creates", []), "creates", names
            ).validated(),
            requires=DocumentRules(
                document.get("requires", []), "requires", names
            ).validated(),
        )

    def _owned_copy(self) -> dict[str, Any]:
        if not isinstance(self.source, Mapping):
            raise MetadataError("each document must be a metadata object")
        try:
            copied: dict[str, Any] = json.loads(
                json.dumps(dict(self.source), allow_nan=False)
            )
            return copied
        except (TypeError, ValueError) as error:
            raise MetadataError("document metadata must contain JSON values") from error


@dataclass(frozen=True)
class Documentation:
    sources: Sequence[Mapping[str, Any]]

    def documents(self) -> Iterator[MetadataDocument]:
        if not isinstance(self.sources, Sequence) or isinstance(
            self.sources, (str, bytes)
        ):
            raise MetadataError("documents must be an array of metadata objects")
        seen_ids: set[str] = set()
        for index, source in enumerate(self.sources):
            document = DocumentSource(source, f"doc:{index}").parsed()
            if document.document_id in seen_ids:
                raise MetadataError("document ids must be nonempty and unique")
            seen_ids.add(document.document_id)
            yield document
