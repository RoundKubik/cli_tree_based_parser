from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from vrp_parser.parameters import (
    ExactDeclarationRecognizer,
    ParameterDeclaration,
    ParameterFamily,
    ParameterResult,
    ParameterStatus,
    ParameterType,
    ParameterTypeRegistry,
    SingleTokenReader,
    default_parameter_registry,
)


def _declaration(
    registry: ParameterTypeRegistry, source: str
) -> ParameterDeclaration:
    declaration = registry.recognize(source)
    assert declaration is not None
    assert declaration.end == len(source)
    return declaration


@pytest.mark.parametrize(
    ("source", "type_id"),
    [
        ("HEX<80-FD>", "hex"),
        ("<hh:mm>", "time"),
        ("STRING<1-63>", "string"),
        ("INTEGER<-15-15>", "integer"),
        ("ENUM{Eth-trunk,Vlanif,}", "enum"),
        ("YYYY/MM/DD", "date-slash"),
        ("YYYY-MM-DD", "date-iso"),
        ("MM-DD", "month-day"),
        ("MM-DD-YYYY", "date-us"),
        ("YYYY/MM/DD,HH:MM:SS", "datetime-slash"),
        ("HH:MM:SS", "time-seconds"),
        ("PASSWORDEX<1-64>", "passwordex"),
        ("H-H-H", "mac"),
        ("TEXT<1-4096>", "text"),
    ],
)
def test_registry_recognizes_each_builtin_declaration(
    source: str, type_id: str
) -> None:
    declaration = _declaration(default_parameter_registry(), source)

    assert declaration.type_id == type_id
    assert declaration.source == source


def test_longest_date_time_declaration_wins() -> None:
    declaration = _declaration(
        default_parameter_registry(), "YYYY/MM/DD,HH:MM:SS"
    )

    assert declaration.type_id == "datetime-slash"


def test_declaration_span_is_relative_to_the_complete_pattern() -> None:
    declaration = default_parameter_registry().recognize(
        "clock date YYYY-MM-DD", position=len("clock date ")
    )

    assert declaration is not None
    assert declaration.source == "YYYY-MM-DD"
    assert (declaration.start, declaration.end) == (11, 21)


def test_ipv4_and_ipv6_are_not_registered_in_the_new_registry() -> None:
    registry = default_parameter_registry()

    assert registry.recognize("X.X.X.X") is None
    assert registry.recognize("X:X::X:X") is None


@pytest.mark.parametrize(
    ("type_id", "family"),
    [
        ("enum", ParameterFamily.ENUM),
        ("date-iso", ParameterFamily.STRUCTURED),
        ("time", ParameterFamily.STRUCTURED),
        ("mac", ParameterFamily.STRUCTURED),
        ("integer", ParameterFamily.NUMERIC),
        ("hex", ParameterFamily.NUMERIC),
        ("string", ParameterFamily.GENERIC),
        ("passwordex", ParameterFamily.GENERIC),
        ("text", ParameterFamily.REMAINDER),
    ],
)
def test_registry_exposes_type_families(
    type_id: str, family: ParameterFamily
) -> None:
    registry = default_parameter_registry()

    assert registry.family_of(type_id) is family
    parameter_type = registry.get(type_id)
    assert parameter_type is not None
    assert parameter_type.family is family


@pytest.mark.parametrize(
    ("declaration_source", "raw", "normalized"),
    [
        ("HEX<80-FD>", "0xfd", 253),
        ("<hh:mm>", "23:59", "23:59"),
        ("STRING<1-3>", "абв", "абв"),
        ("INTEGER<-15-15>", "-15", -15),
        ("ENUM{Eth-trunk,Vlanif,}", "vLaNiF", "Vlanif"),
        ("YYYY/MM/DD", "2024/02/29", "2024/02/29"),
        ("YYYY-MM-DD", "2024-02-29", "2024-02-29"),
        ("MM-DD", "02-29", "02-29"),
        ("MM-DD-YYYY", "02-29-2024", "02-29-2024"),
        (
            "YYYY/MM/DD,HH:MM:SS",
            "2024/02/29,23:59:58",
            "2024/02/29,23:59:58",
        ),
        ("HH:MM:SS", "23:59:58", "23:59:58"),
        (
            "PASSWORDEX<1-30>",
            'afsd!##24"value"',
            'afsd!##24"value"',
        ),
        ("H-H-H", "1-aB-CD09", "0001-00ab-cd09"),
        ("TEXT<1-4096>", "description with spaces", "description with spaces"),
    ],
)
def test_valid_values_are_normalized(
    declaration_source: str, raw: str, normalized: object
) -> None:
    result = default_parameter_registry().evaluate(declaration_source, raw)

    assert result.status is ParameterStatus.VALID
    assert result.valid
    assert result.applicable
    assert result.normalized == normalized
    assert result.issue is None


@pytest.mark.parametrize(
    ("declaration_source", "raw"),
    [
        ("INTEGER<1-15>", "abc"),
        ("HEX<80-FD>", "not-hex"),
        ("<hh:mm>", "foo"),
        ("YYYY-MM-DD", "abc"),
        ("H-H-H", "abc"),
        ("ENUM{up,down,}", "unknown"),
    ],
)
def test_lexically_unrelated_values_are_not_applicable(
    declaration_source: str, raw: str
) -> None:
    result = default_parameter_registry().evaluate(declaration_source, raw)

    assert result.status is ParameterStatus.NOT_APPLICABLE
    assert not result.valid
    assert not result.applicable
    assert result.normalized is None
    assert result.issue is None


@pytest.mark.parametrize(
    ("declaration_source", "raw"),
    [
        ("INTEGER<1-15>", "16"),
        ("HEX<80-FD>", "7f"),
        ("<hh:mm>", "25:00"),
        ("YYYY/MM/DD", "2023/02/29"),
        ("YYYY-MM-DD", "2025-02-30"),
        ("MM-DD", "02-30"),
        ("MM-DD-YYYY", "02-29-2023"),
        ("YYYY/MM/DD,HH:MM:SS", "2024/02/29,24:00:00"),
        ("HH:MM:SS", "23:60:00"),
        ("H-H-H", "00000-0-0"),
        ("STRING<1-3>", "four"),
        ("PASSWORDEX<2-4>", "x"),
        ("TEXT<1-4096>", "x" * 4097),
    ],
)
def test_applicable_values_that_violate_constraints_are_invalid(
    declaration_source: str, raw: str
) -> None:
    result = default_parameter_registry().evaluate(declaration_source, raw)

    assert result.status is ParameterStatus.INVALID
    assert not result.valid
    assert result.applicable
    assert result.issue is not None
    assert result.message


def test_malformed_and_unknown_declarations_use_tri_state() -> None:
    registry = default_parameter_registry()

    malformed = registry.evaluate("INTEGER<bad>", "5")
    unknown = registry.evaluate("NOT-A-PARAMETER", "5")

    assert malformed.status is ParameterStatus.INVALID
    assert malformed.issue is not None
    assert malformed.issue.code == "invalid_declaration"
    assert unknown.status is ParameterStatus.NOT_APPLICABLE


def test_parameter_type_returns_not_applicable_for_another_declaration() -> None:
    registry = default_parameter_registry()
    integer_type = registry.get("integer")
    string_declaration = _declaration(registry, "STRING<1-10>")
    assert integer_type is not None

    result = integer_type.probe("5", string_declaration)

    assert result.status is ParameterStatus.NOT_APPLICABLE


def test_readers_preserve_token_spans_and_text_remainder() -> None:
    registry = default_parameter_registry()
    string = _declaration(registry, "STRING<1-20>")
    text = _declaration(registry, "TEXT<1-4096>")

    token = registry.read(string, "  first second")
    remainder = registry.read(text, "  first second")

    assert token is not None
    assert (token.raw, token.start, token.end) == ("first", 2, 7)
    assert remainder is not None
    assert (remainder.raw, remainder.start, remainder.end) == (
        "first second",
        2,
        14,
    )


class _YesValidator:
    def probe(
        self, raw: str, declaration: ParameterDeclaration
    ) -> ParameterResult:
        del declaration
        if raw == "yes":
            return ParameterResult.success(True)
        return ParameterResult.not_applicable()


def test_new_type_is_added_by_registration_without_registry_changes() -> None:
    registry = ParameterTypeRegistry()
    registry.register(
        ParameterType(
            type_id="yes",
            family=ParameterFamily.ENUM,
            declaration_recognizer=ExactDeclarationRecognizer("YES"),
            reader=SingleTokenReader(),
            validator=_YesValidator(),
        )
    )

    valid = registry.evaluate("YES", "yes")
    unrelated = registry.evaluate("YES", "no")

    assert valid.status is ParameterStatus.VALID
    assert valid.normalized is True
    assert unrelated.status is ParameterStatus.NOT_APPLICABLE


def test_declarations_and_results_are_immutable() -> None:
    registry = default_parameter_registry()
    declaration = _declaration(registry, "INTEGER<1-10>")
    result = registry.probe("5", declaration)

    with pytest.raises(FrozenInstanceError):
        declaration.source = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = ParameterStatus.INVALID  # type: ignore[misc]


def test_frozen_registry_rejects_late_registration() -> None:
    registry = default_parameter_registry().freeze()

    with pytest.raises(ValueError, match="frozen"):
        registry.register(
            ParameterType(
                type_id="example",
                family=ParameterFamily.STRUCTURED,
                declaration_recognizer=ExactDeclarationRecognizer("EXAMPLE"),
                reader=SingleTokenReader(),
                validator=_YesValidator(),
            )
        )
