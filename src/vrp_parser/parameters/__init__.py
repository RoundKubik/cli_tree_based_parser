"""Isolated, extensible Huawei CLI parameter type system."""

from .builtins import builtin_parameter_types, default_parameter_registry
from .errors import ParameterDeclarationError, ParameterRegistryError
from .models import (
    DeclarationRecognition,
    DeclarationRecognizer,
    ParameterDeclaration,
    ParameterFamily,
    ParameterIssue,
    ParameterReader,
    ParameterResult,
    ParameterStatus,
    ParameterToken,
    ParameterType,
    ParameterValidator,
)
from .readers import RemainderReader, SingleTokenReader
from .recognizers import (
    BoundedDeclarationRecognizer,
    EnumDeclarationRecognizer,
    ExactDeclarationRecognizer,
)
from .registry import ParameterTypeRegistry
from .validators import (
    DateTimeValidator,
    EnumValidator,
    HexValidator,
    IntegerValidator,
    MacValidator,
    TextValidator,
    TokenStringValidator,
)

__all__ = [
    "BoundedDeclarationRecognizer",
    "DateTimeValidator",
    "DeclarationRecognition",
    "DeclarationRecognizer",
    "EnumDeclarationRecognizer",
    "EnumValidator",
    "ExactDeclarationRecognizer",
    "HexValidator",
    "IntegerValidator",
    "MacValidator",
    "ParameterDeclaration",
    "ParameterDeclarationError",
    "ParameterFamily",
    "ParameterIssue",
    "ParameterReader",
    "ParameterRegistryError",
    "ParameterResult",
    "ParameterStatus",
    "ParameterToken",
    "ParameterType",
    "ParameterTypeRegistry",
    "ParameterValidator",
    "RemainderReader",
    "SingleTokenReader",
    "TextValidator",
    "TokenStringValidator",
    "builtin_parameter_types",
    "default_parameter_registry",
]
