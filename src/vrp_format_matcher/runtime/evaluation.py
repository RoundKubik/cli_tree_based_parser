"""Execute one prepared pair; binding traversal and rules have separate owners."""

from __future__ import annotations

from dataclasses import dataclass, replace

from vrp_format_matcher.models import (
    MappingLimitExceeded,
    MetadataApplication,
    MetadataError,
    PreparedPair,
)
from vrp_parser_automaton.results import ParsedCommand, PatternMatch

from .bindings import BindingSearch, CommandAtoms
from .rules import MetadataRules, RuleOutcomes


@dataclass(frozen=True)
class PairEvaluation:
    pair: PreparedPair
    maximum_configurations: int

    def evaluate(self, line: ParsedCommand, match: PatternMatch) -> MetadataApplication:
        if self.pair.device_format != match.original_pattern:
            raise MetadataError("prepared metadata does not match device pattern")
        result = MetadataApplication(
            document_id=self.pair.document_id,
            pattern_id=self.pair.pattern_id,
            variation_id=match.variation_id,
            status="not_applicable",
        )
        if self.pair.comparison.relation == "unknown":
            return replace(result, status="unknown", reason=self.pair.comparison.reason)
        if self.pair.recognizer is None:
            return result

        atoms = CommandAtoms(line.raw, match.parameters).read()
        try:
            histories = BindingSearch(
                self.pair.recognizer,
                self.maximum_configurations,
            ).match(atoms)
        except MappingLimitExceeded as error:
            return replace(result, status="unknown", reason=str(error))
        if not histories:
            return result

        metadata = MetadataRules(self.pair)
        alternatives = tuple(metadata.alternative(bindings) for bindings in histories)
        rules = metadata.agreement(alternatives)
        return replace(
            result,
            status=RuleOutcomes(rules).status(),
            binding_status="unique" if len(alternatives) == 1 else "ambiguous",
            alternatives=alternatives,
            rules=rules,
        )
