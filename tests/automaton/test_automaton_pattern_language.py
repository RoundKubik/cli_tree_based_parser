from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass

import pytest

from vrp_parser_automaton.parameters import default_parameter_registry
from vrp_parser_automaton.patterns import (
    Group,
    GroupMode,
    Literal,
    Parameter,
    PatternLanguageError,
    PatternLexer,
    PatternParser,
    Repeat,
    Sequence,
    SourceSpan,
    TokenKind,
)


@dataclass(frozen=True)
class _ExampleDeclaration:
    name: str


@dataclass(frozen=True)
class _ExampleMatch:
    declaration: _ExampleDeclaration
    end: int


class _ExampleRecognizer:
    def recognize(self, source: str, position: int) -> _ExampleMatch | None:
        marker = "$ARG"
        if source.startswith(marker, position):
            return _ExampleMatch(
                _ExampleDeclaration("example"),
                position + len(marker),
            )
        return None


@pytest.fixture
def parser() -> PatternParser:
    return PatternParser(default_parameter_registry())


def test_lexer_preserves_exact_text_parameter_metadata_and_spans() -> None:
    source = "peer  STRING<1-10> &<1-3>"

    tokens = PatternLexer(default_parameter_registry()).tokenize(source)

    assert [token.kind for token in tokens] == [
        TokenKind.LITERAL,
        TokenKind.PARAMETER,
        TokenKind.REPEAT,
        TokenKind.END,
    ]
    assert tokens[0].span == SourceSpan(0, 4)
    assert source[tokens[1].span.start : tokens[1].span.end] == "STRING<1-10>"
    assert tokens[1].parameter is not None
    assert tokens[1].parameter.type_id == "string"  # type: ignore[attr-defined]
    assert tokens[2].repeat_bounds == (1, 3)
    assert tokens[-1].span == SourceSpan(len(source), len(source))


def test_parameter_recognition_precedes_structural_symbols() -> None:
    root = PatternParser(default_parameter_registry()).parse("mode ENUM{fast,safe,}")

    parameter = root.items[1]
    assert isinstance(parameter, Parameter)
    assert parameter.source == "ENUM{fast,safe,}"
    assert parameter.declaration.choices == (  # type: ignore[attr-defined]
        "fast",
        "safe",
    )


def test_frontend_accepts_a_small_custom_recognizer() -> None:
    parameter = PatternParser(_ExampleRecognizer()).parse("use $ARG").items[1]

    assert isinstance(parameter, Parameter)
    assert parameter.source == "$ARG"
    assert parameter.declaration == _ExampleDeclaration("example")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("[ left | right ]", GroupMode.OPTIONAL_ONE),
        ("{ left | right }", GroupMode.REQUIRED_ONE),
        ("[ left | right ]*", GroupMode.OPTIONAL_SET),
        ("{ left | right } *", GroupMode.REQUIRED_SET),
    ],
)
def test_group_modes(source: str, expected: GroupMode, parser: PatternParser) -> None:
    group = parser.parse(source).items[0]

    assert isinstance(group, Group)
    assert group.mode is expected
    values: list[str] = []
    for branch in group.alternatives:
        literal = branch.items[0]
        assert isinstance(literal, Literal)
        values.append(literal.value)
    assert values == ["left", "right"]


def test_star_is_literal_when_parser_expects_an_atom(
    parser: PatternParser,
) -> None:
    root = parser.parse("match * { keyword | * }")

    standalone = root.items[1]
    group = root.items[2]
    assert isinstance(standalone, Literal)
    assert standalone.value == "*"
    assert isinstance(group, Group)
    branch_star = group.alternatives[1].items[0]
    assert isinstance(branch_star, Literal)
    assert branch_star.value == "*"


def test_nested_groups_build_small_readable_ast(parser: PatternParser) -> None:
    root = parser.parse(
        "route [ vpn STRING<1-31> ] { preference INTEGER<1-255> | [ tag HEX<0-FFFF> ] }"
    )

    assert isinstance(root, Sequence)
    optional = root.items[1]
    required = root.items[2]
    assert isinstance(optional, Group)
    assert optional.mode is GroupMode.OPTIONAL_ONE
    assert isinstance(optional.alternatives[0].items[1], Parameter)
    assert isinstance(required, Group)
    nested = required.alternatives[1].items[0]
    assert isinstance(nested, Group)
    assert nested.mode is GroupMode.OPTIONAL_ONE


def test_repeat_applies_to_parameter_or_group_with_optional_whitespace(
    parser: PatternParser,
) -> None:
    parameter_repeat = parser.parse("peer STRING<1-10> &<0-3>").items[1]
    group_repeat = parser.parse("{ left | right }*&<1-2>").items[0]

    assert isinstance(parameter_repeat, Repeat)
    assert isinstance(parameter_repeat.atom, Parameter)
    assert (parameter_repeat.minimum, parameter_repeat.maximum) == (0, 3)
    assert isinstance(group_repeat, Repeat)
    assert isinstance(group_repeat.atom, Group)
    assert group_repeat.atom.mode is GroupMode.REQUIRED_SET
    assert (group_repeat.minimum, group_repeat.maximum) == (1, 2)


def test_pipe_is_literal_at_pattern_root(parser: PatternParser) -> None:
    root = parser.parse("display | include STRING<1-20>")

    pipe = root.items[1]
    assert isinstance(pipe, Literal)
    assert pipe.value == "|"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("", "cannot be empty"),
        ("[ left | ]", "alternatives cannot be empty"),
        ("{ left", "expected right_brace"),
        ("left }", "unexpected closing"),
        ("literal&<1-2>", "parameter or group"),
        ("STRING<1-10>&<3-2>", "minimum <= maximum"),
        ("STRING<1-10>&<1-x>", "malformed repeat"),
        ("cmd &", "malformed repeat"),
        ("cmd &foo", "malformed repeat"),
        ("cmd & <1-2>", "malformed repeat"),
    ],
)
def test_syntax_errors_include_character_spans(
    source: str,
    message: str,
    parser: PatternParser,
) -> None:
    with pytest.raises(PatternLanguageError, match=message) as caught:
        parser.parse(source)

    error = caught.value
    assert error.source == source
    assert 0 <= error.span.start <= error.span.end <= len(source)


def test_ast_and_span_value_objects_are_frozen(parser: PatternParser) -> None:
    root = parser.parse("show value")

    with pytest.raises(FrozenInstanceError):
        root.span = SourceSpan(0, 1)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        root.items[0].span.start = 1  # type: ignore[misc]
