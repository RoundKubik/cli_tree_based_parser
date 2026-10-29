"""Populate parent command annotations; no device mapping or global graph."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import Catalog, Location, Source, source_dict
from .examples import NAVIGATION, Command, parse_example


@dataclass(frozen=True)
class RecoveryResult:
    catalog: dict[str, Any]
    report: dict[str, Any]


@dataclass(frozen=True)
class Observation:
    file: str
    example: int
    parent: Command
    child: Command
    source: Source

    def evidence(self) -> dict[str, Any]:
        return {
            "file": self.file, "example_index": self.example,
            "parent_line": self.parent.line, "parent_command": self.parent.text,
            "child_line": self.child.line, "child_command": self.child.text,
            "child_documentation_source": source_dict(self.source),
        }


def _observations(
    catalog: Catalog,
) -> tuple[list[Observation], list[dict[str, Any]], list[dict[str, Any]], int]:
    result, issues, skipped = [], [], []
    count = 0
    for file, page in catalog.pages.items():
        for index, lines in enumerate(page.get("Examples", [])):
            count += 1
            commands, problems = parse_example(lines)
            issues.extend({"file": file, "example_index": index, **p} for p in problems)
            contradictions = [p for p in problems if p["reason"] in {
                "unexpected_prompt_after_exit", "unexpected_prompt_after_sysname",
            }]
            if contradictions:
                # Validate the WHOLE example before accepting any of its facts.
                # Other examples on the same source page are still independent.
                skipped.append({
                    "file": file, "example_index": index,
                    "reason": "contradictory_example", "issues": contradictions,
                })
                continue
            for child in commands:
                if child.text in NAVIGATION or not child.ancestors:
                    continue
                matches = catalog.match(child.text) & catalog.by_page[file]
                for source in sorted(matches - catalog.global_sources):
                    result.append(Observation(
                        file, index, child.ancestors[-1], child, source
                    ))
    return result, issues, skipped, count


def _locations(candidates: dict[Source, list[Location]]) -> list[Location]:
    return list(dict.fromkeys(
        location for locations in candidates.values() for location in locations
    ))


def _parent_locations(
    observation: Observation, catalog: Catalog
) -> tuple[list[Location], dict[str, Any] | None]:
    if not observation.parent.rooted:
        return [], {"reason": "source_context_unknown"}
    states = {catalog.entry}
    for ancestor in observation.parent.ancestors:
        candidates = catalog.candidates(ancestor.text, states)
        if not candidates:
            return [], {
                "reason": "ancestor_format_not_matched", "command": ancestor.text,
                "candidates": catalog.describe_candidates(candidates),
            }
        targets = catalog.targets(_locations(candidates))
        if not targets:
            return [], {"reason": "ancestor_switch_unknown", "command": ancestor.text}
        states = targets
    candidates = catalog.candidates(observation.parent.text, states)
    if not candidates:
        return [], {
            "reason": "parent_format_not_matched", "source_views": sorted(states),
            "candidates": catalog.describe_candidates(candidates),
        }
    return _locations(candidates), None


def recover_hierarchy(document: dict[str, Any], corpus: str | Path) -> RecoveryResult:
    """Return an enriched copy and evidence; never mutate the input document.

    Each source example anchors its own documented child command. The containing
    entry is recognized within known source views. All matching parent formats
    receive the child's catalog memberships as possible switch_to_view targets.
    """
    catalog = Catalog(document, Path(corpus))
    observations, issues, skipped, example_count = _observations(catalog)
    evidence: dict[tuple[Location, int], dict[str, Any]] = {}
    passes = 0
    while True:
        passes += 1
        changed = False
        pending: dict[int, dict[str, Any]] = {}
        proposals: dict[Location, set[str]] = defaultdict(set)
        multiple_parent_matches: set[int] = set()
        for index, observation in enumerate(observations):
            locations, problem = _parent_locations(observation, catalog)
            if problem is not None:
                pending[index] = {**observation.evidence(), **problem}
                continue
            targets = catalog.members[observation.source] - catalog.global_views
            matched_formats = {catalog.sources[location] for location in locations}
            if len(matched_formats) > 1:
                multiple_parent_matches.add(index)
            for location in locations:
                proposals[location].update(targets)
                first_pass = evidence.get((location, index), {}).get(
                    "first_pass", passes
                )
                evidence[location, index] = {
                    **observation.evidence(),
                    "parent_view": location[0], "parent_index": location[1],
                    "parent_documentation_source": source_dict(
                        catalog.sources[location]
                    ),
                    "target_views": sorted(targets),
                    "matched_parent_formats": len(matched_formats),
                    "first_pass": first_pass,
                }
        # Apply a complete pass together: newly discovered source scopes become
        # available on the NEXT iteration, independently of source page ordering.
        for location, targets in proposals.items():
            row = catalog.record(location)
            existing = row.get("switch_to_view")
            previous = set(existing) if isinstance(existing, list) else set()
            if targets - previous:
                row["switch_to_view"] = sorted(previous | targets)
                changed = True
        if not changed:
            break
    changes = []
    for location, source in catalog.sources.items():
        original = document["views"][location[0]][location[1]]
        row = catalog.record(location)
        if row.get("switch_to_view") != original.get("switch_to_view"):
            changes.append({
                "view": location[0], "index": location[1],
                "documentation_source": source_dict(source), "format": row["format"],
                "previous_switch_to_view": original.get("switch_to_view"),
                "switch_to_view": row.get("switch_to_view"),
            })
    report = {
        "summary": {
            "command_records": len(catalog.sources),
            "source_formats": len(catalog.formats), "source_pages": len(catalog.pages),
            "examples": example_count, "observations": len(observations),
            "passes": passes, "changed_commands": len(changes),
            "parents_with_evidence": len({location for location, _ in evidence}),
            "unresolved_observations": len(pending), "structure_issues": len(issues),
            "skipped_examples": len(skipped),
            "multiple_parent_observations": len(multiple_parent_matches),
        },
        "changes": changes, "evidence": list(evidence.values()),
        "unresolved": list(pending.values()), "structure_issues": issues,
        "skipped_examples": skipped,
        "seed_issues": catalog.seed_issues,
    }
    return RecoveryResult(catalog.document, report)
