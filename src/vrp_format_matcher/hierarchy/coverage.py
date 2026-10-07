"""Mutual view coverage, including formats split across several records."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from vrp_format_matcher.models import PreparedMapping, PreparedPair

from .catalogs import MatchedCatalog
from .languages import FormatCoverage


@dataclass(frozen=True)
class ViewCoverage:
    device: MatchedCatalog
    documentation: MatchedCatalog
    mapping: PreparedMapping
    languages: FormatCoverage

    def unique_pairs(self) -> dict[str, str]:
        """Prove coverage both ways; competing subsets and unknowns block selection.

        Only pairs with possible complete common commands contribute. Empty views
        supply no identity. Counts and view names never rank correspondences.
        """
        devices: dict[str, set[str]] = defaultdict(set)
        documents: dict[str, set[str]] = defaultdict(set)
        for identifier, location in self.device.sources.entries.items():
            assert location.view is not None
            devices[location.view].add(identifier)
        for identifier, location in self.documentation.sources.entries.items():
            assert location.view is not None
            documents[location.view].add(identifier)

        pairs: dict[tuple[str, str], list[PreparedPair]] = defaultdict(list)
        for match in self.mapping.devices.values():
            for pair in match.mappings:
                if pair.stage == "prefix":
                    continue
                left = self.device.sources.entries[pair.pattern_id].view
                right = self.documentation.sources.entries[pair.document_id].view
                assert left is not None and right is not None
                pairs[left, right].append(pair)

        complete = set()
        by_device: dict[str, set[str]] = defaultdict(set)
        by_document: dict[str, set[str]] = defaultdict(set)
        for (left, right), matches in pairs.items():
            covers_device = self._covered(devices[left], matches, document=False)
            covers_document = self._covered(documents[right], matches, document=True)
            if covers_device is not False or covers_document is not False:
                by_device[left].add(right)
                by_document[right].add(left)
            if covers_device is True and covers_document is True:
                complete.add((left, right))
        return {
            left: right
            for left, right in sorted(complete)
            if len(by_device[left]) == len(by_document[right]) == 1
        }

    def _covered(
        self, subjects: set[str], pairs: list[PreparedPair], *, document: bool
    ) -> bool | None:
        alternatives: dict[str, set[str]] = defaultdict(set)
        proved = set()
        for pair in pairs:
            subject, covering = (
                (pair.document_id, pair.pattern_id)
                if document
                else (pair.pattern_id, pair.document_id)
            )
            alternatives[subject].add(covering)
            relation = "document_subset" if document else "device_subset"
            if (
                pair.status in {"equivalent", relation}
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
