"""Offline structural format matching and execution of prepared metadata."""

from vrp_format_matcher.preparation.compiler import FormatMatcher, MetadataCompiler
from vrp_format_matcher.runtime.prepared import PreparedMetadata

from .models import (
    BindingAlternative,
    Comparison,
    MappingLimitExceeded,
    MappingLimits,
    MetadataApplication,
    MetadataEffect,
    MetadataError,
    MetadataReport,
    ParameterBinding,
    PreparationProgress,
    PreparedPair,
    RuleEvaluation,
)

__all__ = [
    "FormatMatcher",
    "PreparedMapping",
    "BindingAlternative",
    "Comparison",
    "MappingLimitExceeded",
    "MappingLimits",
    "MetadataApplication",
    "MetadataCompiler",
    "MetadataEffect",
    "MetadataError",
    "MetadataReport",
    "ParameterBinding",
    "PreparationProgress",
    "PreparedMetadata",
    "PreparedPair",
    "RuleEvaluation",
]

PreparedMapping = PreparedMetadata
