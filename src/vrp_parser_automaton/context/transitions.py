"""Confirmed child views from input declarations and scoped offline matches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.catalogs.source import PatternCatalog
from vrp_parser_automaton.errors import PatternDocumentError
from vrp_parser_automaton.results import ParsedCommand, PatternMatch

from .scopes import MappingScope


@dataclass(frozen=True)
class ScopedView:
    target: str | None
    scope: MappingScope | None = None


class TransitionRules:
    def __init__(
        self,
        catalog: PatternCatalog,
        graph: CommandAutomaton,
        mapping: Mapping[str, Any] | None = None,
    ) -> None:
        self._direct = {
            source.pattern_id: command.record["switch_to_view"] or command.view
            for source, command in zip(graph.patterns, catalog.commands, strict=True)
            if "switch_to_view" in command.record
        }
        self._rules: dict[str, tuple[ScopedView, ...]] = {}
        self._incomplete: set[str] = set()
        if mapping is not None:
            try:
                self._load(mapping, catalog, graph)
            except (
                KeyError,
                TypeError,
                ValueError,
                IndexError,
                AttributeError,
            ) as error:
                raise PatternDocumentError(
                    f"invalid or mismatched offline mapping: {error}"
                ) from error

    def for_command(self, command: ParsedCommand) -> str | None:
        if command.view is None:
            return None
        targets = {
            target
            for match in command.matches
            for target in self._targets(command.raw, match)
        }
        return targets.pop() if len(targets) == 1 else None

    def _targets(self, raw: str, match: PatternMatch) -> set[str | None]:
        if match.pattern_id in self._direct:
            return {self._direct[match.pattern_id]}
        targets = {
            rule.target
            for rule in self._rules.get(match.pattern_id, ())
            if rule.scope is None or rule.scope.accepts(raw, match)
        }
        if not targets or match.pattern_id in self._incomplete:
            targets.add(None)
        return targets

    def _load(
        self, data: Mapping[str, Any], catalog: PatternCatalog, graph: CommandAutomaton
    ) -> None:
        self._validate_catalog(data, catalog, graph)
        hierarchy = data.get("hierarchy")
        if hierarchy is None:
            return
        entries = hierarchy["entry_views"]
        if (
            entries["device"] != catalog.entry_view
            or entries["documentation"]
            != data["catalogs"]["documentation"]["entry_view"]
        ):
            raise ValueError("entry views differ")
        saved = data["devices"]
        for pattern_id, target in hierarchy.get("declared_transitions", {}).items():
            if pattern_id not in self._direct or self._direct[pattern_id] != (
                target or saved[pattern_id]["source"]["view"]
            ):
                raise ValueError("declared transition differs from catalog")
        scopes: dict[str, MappingScope] = {}
        for pattern_id, effects in hierarchy["transitions"].items():
            # An explicit input declaration needs no copied offline scope.
            if pattern_id in self._direct:
                continue
            targets = []
            for effect in effects:
                index = effect["mapping_index"]
                if type(index) is not int or index < 0:
                    raise ValueError("invalid mapping index")
                pair = saved[pattern_id]["mappings"][index]
                doc_view = data["documents"][pair["document_id"]]["source"]["view"]
                # The current mapper proves only the entry-view correspondence.
                # Knowing a destination does not prove the source correspondence.
                source_known = (
                    doc_view == entries["documentation"]
                    and saved[pattern_id]["source"]["view"] == entries["device"]
                )
                target = self._target(effect, hierarchy) if source_known else None
                targets.append((target, pair))
            # Unknown-only effects cannot restore context. Keep their evidence
            # in the offline JSON instead of loading unused graphs into the parser.
            if any(target is not None for target, _ in targets):
                self._rules[pattern_id] = tuple(
                    ScopedView(target, self._scope(pair, data, scopes))
                    for target, pair in targets
                )

    def _validate_catalog(
        self, data: Mapping[str, Any], catalog: PatternCatalog, graph: CommandAutomaton
    ) -> None:
        if not isinstance(data, Mapping) or catalog.entry_view is None:
            raise ValueError("mapping context requires a grouped catalog")
        saved = data["devices"]
        if set(saved) != {source.pattern_id for source in graph.patterns}:
            raise ValueError("device pattern IDs differ")
        header = data["catalogs"]["device"]
        if (
            header["entry_view"] != catalog.entry_view
            or header["type"] != "grouped"
            or header["source"]
            != ("documentation" if catalog.documentation else "device")
        ):
            raise ValueError("catalog header differs")
        for source, command in zip(graph.patterns, catalog.commands, strict=True):
            record = saved[source.pattern_id]
            if record["device_format"] != source.original or record["source"] != {
                "view": command.view,
                "index": command.index,
            }:
                raise ValueError("device format or source location differs")
            if any(pair["status"] == "unknown" for pair in record["mappings"]):
                self._incomplete.add(source.pattern_id)

    @staticmethod
    def _scope(
        pair: Mapping[str, Any], data: Mapping[str, Any], cache: dict[str, MappingScope]
    ) -> MappingScope | None:
        if pair["status"] not in {
            "equivalent",
            "document_subset",
            "device_subset",
            "overlap",
            "matched",
        }:
            raise ValueError("transition requires a completed full match")
        mode, stage = pair["binding_mode"], pair["stage"]
        if stage in {"exact", "reordered"} and mode == "structural":
            return None
        if stage != "intersection" or mode != "path_dependent":
            raise ValueError("transition requires a full-match scope")
        identifier = pair["automaton_id"]
        if identifier not in cache:
            cache[identifier] = MappingScope(data["automata"][identifier])
        return cache[identifier]

    @staticmethod
    def _target(effect: Mapping[str, Any], hierarchy: Mapping[str, Any]) -> str | None:
        kind = effect["kind"]
        if kind == "unknown":
            return None
        entry: str = hierarchy["entry_views"]["device"]
        if kind == "stay":
            return entry
        if kind != "switch":
            raise ValueError("unknown transition kind")
        name = effect["target"]
        target = hierarchy["targets"][name]
        if target["status"] == "unresolved":
            return None
        if (
            target["status"] != "resolved"
            or name != hierarchy["entry_views"]["documentation"]
            or target["device_view"] != entry
        ):
            raise ValueError("unsupported resolved view relation")
        return entry
