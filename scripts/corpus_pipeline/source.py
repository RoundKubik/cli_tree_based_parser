"""Original documentation pages and their existing source references."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vrp_format_matcher.documents.parameters import (
    DocumentParameterRecognizer,
    parameters,
)
from vrp_parser_automaton.patterns import PatternParser


def read_json(path: Path) -> Any:
    def unique_keys(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def pattern_parameters(pattern: str) -> tuple[Any, ...]:
    ast = PatternParser(DocumentParameterRecognizer()).parse(pattern)
    return tuple(parameters(ast))


@dataclass(frozen=True)
class Page:
    path: Path
    data: dict[str, Any]
    source: dict[str, Any]

    @property
    def formats(self) -> list[str]:
        return self.data["CLIs"]

    @property
    def included_formats(self) -> dict[int, str]:
        """Keep original indices when console-only display variants are removed."""
        return {
            index: pattern
            for index, pattern in enumerate(self.formats)
            if pattern.split(maxsplit=1)[0].casefold() != "display"
        }

    @property
    def views(self) -> list[str]:
        return self.data["ParentView"]

    def validate_structure(self) -> None:
        if not isinstance(self.data, dict):
            raise ValueError("A corpus page must be an object")
        for field in ("CLIs", "ParentView"):
            values = self.data.get(field)
            if (
                not isinstance(values, list)
                or not values
                or any(
                    not isinstance(value, str) or not value.strip() for value in values
                )
            ):
                raise ValueError(f"{field} must be a nonempty array of strings")

    def validate(self) -> None:
        self.validate_structure()
        for pattern in self.included_formats.values():
            if "\n" in pattern or "\r" in pattern:
                raise ValueError("A command format cannot contain line breaks")
            pattern_parameters(pattern)


class Corpus:
    """Load source pages once; use explicit links to supply prerequisite formats."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        manifest_path = self.directory.parent / "manifest.json"
        manifest = read_json(manifest_path) if manifest_path.exists() else {}
        references = {
            Path(item["file"]).name: item for item in manifest.get("commands", [])
        }
        self.pages: dict[str, Page] = {}
        self.errors: dict[str, str] = {}
        self.display_pages_skipped = 0
        self.display_formats_skipped = 0
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = read_json(path)
                reference = references.get(path.name, {})
                source = {"file": path.name}
                if manifest.get("source_url"):
                    source["url"] = manifest["source_url"]
                for key in ("section", "first_page", "last_page"):
                    if key in reference:
                        source[key] = reference[key]
                page = Page(path, data, source)
                page.validate_structure()
                self.display_formats_skipped += len(page.formats) - len(
                    page.included_formats
                )
                if not page.included_formats:
                    self.display_pages_skipped += 1
                    continue
                self.pages[path.name] = page
            except (ValueError, OSError, TypeError) as error:
                self.errors[path.name] = str(error)
        if not self.pages and not self.errors and not self.display_pages_skipped:
            raise ValueError(f"No corpus JSON files found in {directory}")
        self._sections = {
            page.source["section"]: page
            for page in self.pages.values()
            if "section" in page.source
        }
        self.command_formats: dict[str, set[str]] = {}
        for page in self.pages.values():
            self.command_formats.setdefault(
                page.data.get("PageTitle", ""), set()
            ).update(page.included_formats.values())

    def related_commands(self, page: Page) -> list[dict[str, Any]]:
        related = {page.path.name: page}
        for topic in page.data.get("related_topics", []):
            for filename in topic.get("target_files", []):
                if filename in self.pages:
                    related[filename] = self.pages[filename]
            for section in topic.get("target_sections", []):
                if section in self._sections:
                    target = self._sections[section]
                    related[target.path.name] = target
        return [
            {
                "name": p.data.get("PageTitle", ""),
                "formats": list(p.included_formats.values()),
                "parent_views": p.views,
            }
            for p in related.values()
        ]

    def selected(self, pilot: int | None = None) -> list[Page]:
        pages = list(self.pages.values())
        if pilot is None:
            return pages
        # Include entry commands and their child commands before filling the sample.
        anchors = (
            "acl number",
            "acl ipv6",
            "bgp",
            "interface",
            "ipv4-family",
            "ipv6-family",
            "rule",
            "peer",
            "port trunk allow-pass vlan",
        )
        chosen: dict[str, Page] = {}
        for title in anchors:
            matches = [
                p
                for p in pages
                if p.data.get("PageTitle", "") == title
                or p.data.get("PageTitle", "").startswith(title + " (")
            ]
            for page in matches[:2]:
                chosen[page.path.name] = page
        # A stable spread across the corpus, not just the first chapter's displays.
        for page in sorted(
            pages, key=lambda p: hashlib.sha256(p.path.name.encode()).digest()
        ):
            chosen.setdefault(page.path.name, page)
            if len(chosen) >= pilot:
                break
        return list(chosen.values())[:pilot]
