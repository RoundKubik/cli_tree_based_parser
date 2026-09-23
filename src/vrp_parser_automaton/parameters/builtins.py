"""Construction of the built-in Huawei parameter type registry."""

from __future__ import annotations

from .models import ParameterFamily, ParameterType
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
    IPv4AddressValidator,
    IPv6AddressValidator,
    IPv6PrefixValidator,
    MacValidator,
    TextValidator,
    TokenStringValidator,
)


def _date_time_types(reader: SingleTokenReader) -> tuple[ParameterType, ...]:
    definitions = (
        (
            "date-slash",
            "YYYY/MM/DD",
            r"[0-9]{4}/[0-9]{2}/[0-9]{2}",
            "%Y/%m/%d",
            "date in YYYY/MM/DD format",
            "",
        ),
        (
            "date-iso",
            "YYYY-MM-DD",
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}",
            "%Y-%m-%d",
            "date in YYYY-MM-DD format",
            "",
        ),
        (
            "month-day",
            "MM-DD",
            r"[0-9]{2}-[0-9]{2}",
            "%Y-%m-%d",
            "date in MM-DD format",
            "2000-",
        ),
        (
            "date-us",
            "MM-DD-YYYY",
            r"[0-9]{2}-[0-9]{2}-[0-9]{4}",
            "%m-%d-%Y",
            "date in MM-DD-YYYY format",
            "",
        ),
        (
            "datetime-slash",
            "YYYY/MM/DD,HH:MM:SS",
            r"[0-9]{4}/[0-9]{2}/[0-9]{2},"
            r"[0-9]{2}:[0-9]{2}:[0-9]{2}",
            "%Y/%m/%d,%H:%M:%S",
            "date and time in YYYY/MM/DD,HH:MM:SS format",
            "",
        ),
        (
            "time-seconds",
            "HH:MM:SS",
            r"[0-9]{2}:[0-9]{2}:[0-9]{2}",
            "%H:%M:%S",
            "time in HH:MM:SS format",
            "",
        ),
        (
            "time",
            "<hh:mm>",
            r"[0-9]{2}:[0-9]{2}",
            "%H:%M",
            "time in hh:mm format",
            "",
        ),
    )
    return tuple(
        ParameterType(
            type_id=type_id,
            family=ParameterFamily.STRUCTURED,
            declaration_recognizer=ExactDeclarationRecognizer(placeholder),
            reader=reader,
            validator=DateTimeValidator(
                expression,
                datetime_format,
                description,
                prefix_for_parsing,
            ),
        )
        for (
            type_id,
            placeholder,
            expression,
            datetime_format,
            description,
            prefix_for_parsing,
        ) in definitions
    )


def builtin_parameter_types() -> tuple[ParameterType, ...]:
    """Return fresh, stateless definitions for every built-in type."""

    token = SingleTokenReader()
    bounded = (
        ParameterType(
            "hex",
            ParameterFamily.NUMERIC,
            BoundedDeclarationRecognizer("HEX", base=16),
            token,
            HexValidator(),
        ),
        ParameterType(
            "string",
            ParameterFamily.GENERIC,
            BoundedDeclarationRecognizer("STRING"),
            token,
            TokenStringValidator(),
        ),
        ParameterType(
            "integer",
            ParameterFamily.NUMERIC,
            BoundedDeclarationRecognizer("INTEGER", allow_negative=True),
            token,
            IntegerValidator(),
        ),
        ParameterType(
            "enum",
            ParameterFamily.ENUM,
            EnumDeclarationRecognizer(),
            token,
            EnumValidator(),
        ),
        ParameterType(
            "passwordex",
            ParameterFamily.GENERIC,
            BoundedDeclarationRecognizer("PASSWORDEX"),
            token,
            TokenStringValidator(),
        ),
        ParameterType(
            "mac",
            ParameterFamily.STRUCTURED,
            ExactDeclarationRecognizer("H-H-H"),
            token,
            MacValidator(),
        ),
        ParameterType(
            "ipv4-address",
            ParameterFamily.STRUCTURED,
            ExactDeclarationRecognizer("X.X.X.X"),
            token,
            IPv4AddressValidator(),
        ),
        ParameterType(
            "ipv6-address",
            ParameterFamily.STRUCTURED,
            ExactDeclarationRecognizer("X:X::X:X"),
            token,
            IPv6AddressValidator(),
        ),
        ParameterType(
            "ipv6-prefix",
            ParameterFamily.STRUCTURED,
            ExactDeclarationRecognizer("X:X::X:X/M"),
            token,
            IPv6PrefixValidator(),
        ),
        ParameterType(
            "text",
            ParameterFamily.REMAINDER,
            BoundedDeclarationRecognizer("TEXT"),
            RemainderReader(),
            TextValidator(),
        ),
    )
    return (*bounded, *_date_time_types(token))


def default_parameter_registry() -> ParameterTypeRegistry:
    """Create a mutable registry with all built-in parameter types."""

    return ParameterTypeRegistry(builtin_parameter_types())
