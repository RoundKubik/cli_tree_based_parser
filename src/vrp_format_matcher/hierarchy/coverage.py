"""Identify views from a possibly incomplete set of documented local formats."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from vrp_format_matcher.models import PreparedMapping, PreparedPair

from .catalogs import MatchedCatalog
from .languages import FormatCoverage


@dataclass(frozen=True)
class CoverageResult:
    references: dict[str, tuple[str, ...]]
    checks: dict[tuple[str, str], str]


@dataclass(frozen=True)
class ViewCoverage:
    device: MatchedCatalog
    documentation: MatchedCatalog
    mapping: PreparedMapping
    languages: FormatCoverage

    def assess(self) -> CoverageResult:
        """Require one device view to cover the documented sample, not its converse.

        Unknown coverage blocks uniqueness. Several documentation scopes can fit
        the same device scope; keep them all rather than choose one by name/count.
        Shared commands and the explicitly anchored entry views supply no identity.
        """
        devices = self._local_commands(self.device)
        documents = self._local_commands(self.documentation)

        pairs: dict[tuple[str, str], list[PreparedPair]] = defaultdict(list)
        for match in self.mapping.devices.values():
            for pair in match.mappings:
                if (
                    pair.stage == "prefix"
                    or pair.pattern_id not in devices
                    or pair.document_id not in documents
                ):
                    continue
                left = devices[pair.pattern_id]
                right = documents[pair.document_id]
                pairs[left, right].append(pair)

        subjects: dict[str, set[str]] = defaultdict(set)
        for identifier, view in documents.items():
            subjects[view].add(identifier)
        complete = set()
        checks = {}
        by_document: dict[str, set[str]] = defaultdict(set)
        for (left, right), matches in pairs.items():
            covered = self._covered(subjects[right], matches)
            checks[left, right] = (
                "covered"
                if covered is True
                else "partial"
                if covered is False
                else "unknown"
            )
            if covered is not False:
                by_document[right].add(left)
            if covered is True:
                complete.add((left, right))
        references: dict[str, list[str]] = defaultdict(list)
        for left, right in sorted(complete):
            if len(by_document[right]) == 1:
                references[left].append(right)
        return CoverageResult(
            {view: tuple(names) for view, names in references.items()}, checks
        )

    def _local_commands(self, catalog: MatchedCatalog) -> dict[str, str]:
        shared = set(catalog.sources.info.get("shared_views", ()))
        shared_languages = {
            self.languages.identity(identifier)
            for identifier, location in catalog.sources.entries.items()
            if location.view in shared
        }
        excluded = shared | {catalog.sources.info["entry_view"]}
        return {
            identifier: location.view
            for identifier, location in catalog.sources.entries.items()
            if location.view is not None
            and location.view not in excluded
            and self.languages.identity(identifier) not in shared_languages
        }

    def _covered(self, subjects: set[str], pairs: list[PreparedPair]) -> bool | None:
        alternatives: dict[str, set[str]] = defaultdict(set)
        proved = set()
        for pair in pairs:
            subject, covering = pair.document_id, pair.pattern_id
            alternatives[subject].add(covering)
            if (
                pair.status in {"equivalent", "document_subset"}
                and self.languages.known(subject)
                and self.languages.known(covering)
            ):
                proved.add(subject)
        # A missing full pair is an immediate counterexample. Budget failures
        # remain explicit pairs and therefore do not enter this shortcut.
        if not subjects <= alternatives.keys():
            return False
        result: bool | None = True
        for subject in sorted(subjects - proved):
            covered = self.languages.covers(subject, alternatives[subject])
            if covered is False:
                return False
            if covered is None:
                result = None
        return result
