"""Bind original grouped command records to the matcher's source locations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from vrp_format_matcher.models import CatalogSources, FormatError
from vrp_format_matcher.preparation.sources import CommandCatalog


@dataclass(frozen=True)
class MatchedCatalog:
    records: dict[str, Mapping[str, Any]]
    sources: CatalogSources

    @classmethod
    def bind(
        cls, source: Mapping[str, Any], saved: CatalogSources | None
    ) -> MatchedCatalog:
        catalog = CommandCatalog.read(source)
        if catalog.info["type"] != "grouped":
            raise FormatError("hierarchy evidence requires two grouped catalogs")
        if saved is None:
            raise FormatError("hierarchy evidence requires compile_catalogs results")
        if catalog.info != saved.info or catalog.locations != tuple(
            saved.entries.values()
        ):
            raise FormatError("catalog header or source locations differ from mapping")
        return cls(dict(zip(saved.entries, catalog.commands, strict=True)), saved)

    def command(self, identifier: str, pattern: str) -> Mapping[str, Any]:
        record = self.records.get(identifier)
        if record is None or record["format"] != pattern:
            raise FormatError(f"catalog format differs from mapping: {identifier}")
        return record
