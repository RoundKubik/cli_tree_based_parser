"""Source identities and syntax recognition, independent of the format matcher."""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from vrp_parser_automaton import CommandLineParser, ParsedCommand

type Source = tuple[str, int]
type Location = tuple[str, int]


def source_dict(source: Source) -> dict[str, Any]:
    return {"file": source[0], "format_index": source[1]}


class Catalog:
    def __init__(self, document: dict[str, Any], corpus: Path) -> None:
        if (
            not isinstance(document, dict)
            or document.get("source") != "documentation"
            or document.get("type") != "grouped"
        ):
            raise ValueError("Expected a grouped documentation catalog")
        self.document = deepcopy(document)
        self.views = self.document.get("views")
        self.entry = self.document.get("entry_view")
        if (
            not isinstance(self.views, dict)
            or not isinstance(self.entry, str)
            or self.entry not in self.views
        ):
            raise ValueError("entry_view must reference an existing views group")
        shared = self.document.get("shared_views", [])
        if not isinstance(shared, list) or any(not isinstance(v, str) for v in shared):
            raise ValueError("shared_views must be an array of view names")
        self.global_views = set(shared)
        if "global_view" in self.document:
            if not isinstance(self.document["global_view"], str):
                raise ValueError("global_view must name a view")
            self.global_views.add(self.document["global_view"])
        if (
            self.entry in self.global_views
            or not self.global_views <= self.views.keys()
        ):
            raise ValueError("Global groups must exist and differ from entry_view")
        self.pages: dict[str, dict[str, Any]] = {}
        self.formats: dict[Source, dict[str, Any]] = {}
        self.members: dict[Source, set[str]] = defaultdict(set)
        self.by_page: dict[str, set[Source]] = defaultdict(set)
        self.by_view: dict[str, set[Source]] = defaultdict(set)
        self.locations: dict[tuple[str, Source], list[Location]] = defaultdict(list)
        self.sources: dict[Location, Source] = {}
        self.seed_issues: list[dict[str, Any]] = []
        corpus = corpus.resolve()
        for view, rows in self.views.items():
            if not isinstance(view, str) or not view or not isinstance(rows, list):
                raise ValueError("views must map nonempty names to arrays")
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError(f"Invalid command at {view}[{index}]")
                ref = row.get("documentation_source", {})
                if not isinstance(ref, dict):
                    raise ValueError(f"Invalid documentation_source at {view}[{index}]")
                file, offset = ref.get("file"), ref.get("format_index")
                if (
                    not isinstance(file, str) or not file
                    or type(offset) is not int or offset < 0
                ):
                    raise ValueError(
                        f"Missing or invalid documentation_source at {view}[{index}]"
                    )
                if file not in self.pages:
                    path = (corpus / file).resolve()
                    if Path(file).is_absolute() or not path.is_relative_to(corpus):
                        raise ValueError(
                            f"Expected a relative corpus source path: {file}"
                        )
                    page = json.loads(path.read_text(encoding="utf-8"))
                    if (
                        not isinstance(page, dict)
                        or not isinstance(page.get("CLIs"), list)
                    ):
                        raise ValueError(f"Invalid source page: {file}")
                    examples = page.get("Examples", [])
                    if not isinstance(examples, list) or any(
                        not isinstance(example, list)
                        or any(not isinstance(line, str) for line in example)
                        for example in examples
                    ):
                        raise ValueError(f"Invalid Examples in {file}")
                    self.pages[file] = page
                clis = self.pages[file]["CLIs"]
                if offset >= len(clis) or row.get("format") != clis[offset]:
                    raise ValueError(
                        f"Source format differs at {view}[{index}]: {file}:{offset}"
                    )
                source = (file, offset)
                syntax = {
                    "format": row["format"],
                    "parameter_types": row.get("parameter_types", []),
                }
                if source in self.formats and syntax != self.formats[source]:
                    raise ValueError(
                        f"Inconsistent syntax annotations for {file}:{offset}"
                    )
                self.formats[source] = syntax
                self.members[source].add(view)
                self.by_page[file].add(source)
                self.by_view[view].add(source)
                self.locations[view, source].append((view, index))
                self.sources[view, index] = source
                self._seed(row, (view, index))
        if not self.formats:
            raise ValueError("The catalog contains no formats")
        self.global_sources = set().union(*(self.by_view[v] for v in self.global_views))
        self.pattern_sources = tuple(self.formats)
        # The existing public grouped parser retains ALL valid matches in its
        # default system partition. One synthetic group provides syntax-only
        # recognition; real source scopes are applied below, without changing it.
        self.parser = CommandLineParser({
            "type": "grouped",
            "entry_view": "syntax",
            "views": {"syntax": list(self.formats.values())},
        })
        self._matches: dict[str, frozenset[Source]] = {}

    def _seed(self, row: dict[str, Any], location: Location) -> None:
        target = row.get("switch_to_view")
        if isinstance(target, str):
            if target in self.views and target not in self.global_views:
                row["switch_to_view"] = [target]
            else:
                self.seed_issues.append({
                    "view": location[0], "index": location[1],
                    "reason": "unknown_seed_target", "target": target,
                })
                row["switch_to_view"] = {"status": "unresolved"}
        elif isinstance(target, list):
            if any(
                not isinstance(v, str) or v not in self.views or v in self.global_views
                for v in target
            ):
                raise ValueError(f"Invalid switch_to_view targets at {location}")
            row["switch_to_view"] = sorted(set(target))
        elif target is not None and target != {"status": "unresolved"}:
            raise ValueError(f"Invalid switch_to_view at {location}")

    def match(self, text: str) -> frozenset[Source]:
        if text not in self._matches:
            result = self.parser.parse(text)
            self._matches[text] = frozenset(
                self.pattern_sources[match.pattern_index] for match in result.matches
            ) if isinstance(result, ParsedCommand) else frozenset()
        return self._matches[text]

    def candidates(self, text: str, states: set[str]) -> dict[Source, list[Location]]:
        result: dict[Source, list[Location]] = defaultdict(list)
        matches = self.match(text)
        for view in sorted(states | self.global_views):
            for source in sorted(matches & self.by_view[view]):
                result[source].extend(self.locations[view, source])
        return dict(result)

    def record(self, location: Location) -> dict[str, Any]:
        return self.views[location[0]][location[1]]

    def targets(self, locations: list[Location]) -> set[str]:
        """Union known alternatives; future passes can add missing branches."""
        result: set[str] = set()
        for location in locations:
            targets = self.record(location).get("switch_to_view")
            if isinstance(targets, list):
                result.update(targets)
        return result

    def describe_candidates(
        self, candidates: dict[Source, list[Location]]
    ) -> list[dict[str, Any]]:
        return [
            {"documentation_source": source_dict(source),
             "format": self.formats[source]["format"],
             "locations": [{"view": v, "index": i} for v, i in locations]}
            for source, locations in sorted(candidates.items())
        ]
