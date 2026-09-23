"""Metadata rules applied to bindings, with agreement across interpretations."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

from .models import (
    BindingAlternative,
    MetadataEffect,
    ParameterBinding,
    PreparedPair,
    RuleEvaluation,
)
from .predicates import evaluate_predicate


@dataclass(frozen=True)
class MetadataRule:
    rule_id: str
    kind: str
    metadata: dict[str, Any]

    def effects(
        self, bindings: tuple[ParameterBinding, ...]
    ) -> tuple[MetadataEffect, ...]:
        predicate = self.metadata.get("when", "always")
        if self.metadata["condition"] == "command":
            if evaluate_predicate(predicate, bindings, None):
                return (MetadataEffect(self.rule_id, self.kind, self.metadata),)
            return ()
        effects = []
        for binding in bindings:
            if binding.document.name != self.metadata["parameter_name"]:
                continue
            if evaluate_predicate(predicate, bindings, binding.document):
                effects.append(
                    MetadataEffect(
                        self.rule_id,
                        self.kind,
                        self.metadata,
                        binding.value,
                    )
                )
        return tuple(effects)

    def agreement(self, alternatives: tuple[BindingAlternative, ...]) -> RuleEvaluation:
        possibilities = tuple(
            tuple(
                effect
                for effect in alternative.effects
                if effect.rule_id == self.rule_id
            )
            for alternative in alternatives
        )
        identities = {
            tuple(sorted(effect.identity() for effect in effects))
            for effects in possibilities
        }
        if len(identities) > 1:
            return RuleEvaluation(self.rule_id, "ambiguous", ())
        effects = possibilities[0]
        status = "active" if effects else "inactive"
        return RuleEvaluation(self.rule_id, status, effects)


@dataclass(frozen=True)
class MetadataRules:
    pair: PreparedPair

    @cached_property
    def rules(self) -> tuple[MetadataRule, ...]:
        return tuple(
            MetadataRule(f"{self.pair.document_id}:{kind}:{index}", kind, rule)
            for kind, rules in (
                ("creates", self.pair.creates),
                ("requires", self.pair.requires),
            )
            for index, rule in enumerate(rules)
        )

    def alternative(self, bindings: tuple[ParameterBinding, ...]) -> BindingAlternative:
        effects = {
            effect.identity(): effect
            for rule in self.rules
            for effect in rule.effects(bindings)
        }
        return BindingAlternative(bindings, tuple(effects.values()))

    def agreement(
        self, alternatives: tuple[BindingAlternative, ...]
    ) -> tuple[RuleEvaluation, ...]:
        return tuple(rule.agreement(alternatives) for rule in self.rules)


@dataclass(frozen=True)
class RuleOutcomes:
    rules: tuple[RuleEvaluation, ...]

    def status(self) -> str:
        if any(rule.status == "ambiguous" for rule in self.rules):
            return "ambiguous"
        if any(rule.status == "active" for rule in self.rules):
            return "applied"
        return "inactive"
