# Parameter Subsystem

This document describes every production entity in the
`vrp_parser.parameters` package: data models, extension protocols, readers,
recognizers, validators, the registry, and built-in types.

The subsystem answers three separate questions:

1. Is a pattern fragment a parameter declaration such as `INTEGER<1-15>`?
2. How should the parameter value be extracted from a real CLI line?
3. Does the extracted value match the type, does it satisfy its constraints,
   and what should its normalized representation be?

These responsibilities are deliberately separated. A new type can be added by
composing a `ParameterType` without changing the pattern lexer, command graph,
or matcher.

## Public Imports

All public entities are exported from `vrp_parser.parameters`:

```python
from vrp_parser.parameters import (
    BoundedDeclarationRecognizer,
    DateTimeValidator,
    DeclarationRecognition,
    DeclarationRecognizer,
    EnumDeclarationRecognizer,
    EnumValidator,
    ExactDeclarationRecognizer,
    HexValidator,
    IntegerValidator,
    IPv4AddressValidator,
    IPv6AddressValidator,
    IPv6PrefixValidator,
    MacValidator,
    ParameterDeclaration,
    ParameterDeclarationError,
    ParameterFamily,
    ParameterIssue,
    ParameterReader,
    ParameterRegistryError,
    ParameterResult,
    ParameterStatus,
    ParameterToken,
    ParameterType,
    ParameterTypeRegistry,
    ParameterValidator,
    RemainderReader,
    SingleTokenReader,
    TextValidator,
    TokenStringValidator,
    builtin_parameter_types,
    default_parameter_registry,
)
```

Some of these objects are needed only when developing a custom type. For
ordinary use, calling `default_parameter_registry()` is sufficient, or the
registry can be omitted entirely when constructing `CommandLineParser`: the
parser creates the default registry itself.

## General Data Flow

```text
pattern string
    │
    ▼
DeclarationRecognizer.recognize()
    │ DeclarationRecognition
    ▼
ParameterType.recognize()
    │ ParameterDeclaration
    ▼
ParameterReader.read() ───────────── real CLI line
    │ ParameterToken
    ▼
ParameterValidator.probe()
    │ ParameterResult
    ▼
VALID / INVALID / NOT_APPLICABLE
```

`ParameterTypeRegistry` selects the appropriate `ParameterType` and delegates
all three operations to it.

## Tri-State Validation Results

Each validator returns a `ParameterResult` with one of three statuses rather
than a `bool`:

| Status | Value | Meaning |
|---|---|---|
| `ParameterStatus.VALID` | `"valid"` | The type is applicable and the value passed validation. |
| `ParameterStatus.INVALID` | `"invalid"` | The type is applicable by shape, but the value violates a constraint. This status must contain a `ParameterIssue`. |
| `ParameterStatus.NOT_APPLICABLE` | `"not_applicable"` | The value does not lexically belong to this type. This is not a validation error for the type. |

The distinction matters when selecting a tree branch. For example:

```python
registry = default_parameter_registry()

registry.evaluate("INTEGER<1-15>", "7").status
# ParameterStatus.VALID

registry.evaluate("INTEGER<1-15>", "16").status
# ParameterStatus.INVALID: this is an integer, but it exceeds the maximum

registry.evaluate("INTEGER<1-15>", "Vlanif").status
# ParameterStatus.NOT_APPLICABLE: the string does not resemble an integer
```

`INVALID` lets the parser report the precise constraint error instead of
unconditionally moving to a less specific branch. `NOT_APPLICABLE` permits
searching for another suitable branch.

## Built-in Parameter Formats

`builtin_parameter_types()` creates 17 types:

| Pattern placeholder | `type_id` | Family | Input value | Successful `normalized` value |
|---|---|---|---|---|
| `HEX<min-max>` | `hex` | `numeric` | Hexadecimal number with optional `0x`/`0X` | `int` |
| `STRING<min-max>` | `string` | `generic` | One non-empty token without whitespace | Original `str` |
| `INTEGER<min-max>` | `integer` | `numeric` | Signed decimal integer | `int` |
| `ENUM{a,b,...}` | `enum` | `enum` | One explicitly listed choice, compared without ASCII case sensitivity | The choice with the spelling used in the pattern |
| `PASSWORDEX<min-max>` | `passwordex` | `generic` | One non-empty token without whitespace | Original `str` |
| `H-H-H` | `mac` | `structured` | Three groups of 1–4 hexadecimal digits | Three lowercase groups of four digits |
| `X.X.X.X` | `ipv4-address` | `structured` | Four decimal octets in `0..255` | Decimal IPv4 without leading zeros |
| `X:X::X:X` | `ipv6-address` | `structured` | Standard IPv6, including compressed and IPv4-mapped forms | Canonical lowercase/compressed IPv6 |
| `X:X::X:X/M` | `ipv6-prefix` | `structured` | IPv6 with a decimal prefix length in `0..128` | Canonical address and prefix; host bits retained |
| `TEXT<min-max>` | `text` | `remainder` | Non-empty remainder with length in the configured range | Original `str` |
| `YYYY/MM/DD` | `date-slash` | `structured` | Fixed-width calendar date | Original `str` |
| `YYYY-MM-DD` | `date-iso` | `structured` | Fixed-width calendar date | Original `str` |
| `MM-DD` | `month-day` | `structured` | Fixed-width month and day | Original `str` |
| `MM-DD-YYYY` | `date-us` | `structured` | Fixed-width calendar date | Original `str` |
| `YYYY/MM/DD,HH:MM:SS` | `datetime-slash` | `structured` | Fixed-width date and time | Original `str` |
| `HH:MM:SS` | `time-seconds` | `structured` | Fixed-width time | Original `str` |
| `<hh:mm>` | `time` | `structured` | Fixed-width time | Original `str` |

Important format details:

- `min` and `max` for `INTEGER` are decimal numeric bounds.
- `min` and `max` for `HEX` are written and interpreted as hexadecimal. For
  example, `HEX<80-FD>` means the range from `0x80` through `0xFD`.
- `min` and `max` for `STRING`, `PASSWORDEX`, and `TEXT` count Python
  characters according to `len(value)`, not bytes.
- In this subsystem, `PASSWORDEX` validates only the token and its length.
  There is no special cryptographic processing or password complexity check.
- Calendar validity for `MM-DD` is checked using the arbitrary leap year 2000,
  so `02-29` is accepted.
- At the `RemainderReader` level, `TEXT<min-max>` reads the complete remainder
  passed to it, including internal and trailing whitespace. Consequently,
  `description TEXT<1-80>` accepts a multiword description as one parameter.
  Before matching, the public `CommandLineParser` removes trailing whitespace
  from the complete command. The top-level restriction applies only to `TEXT`
  matched at position `0` on a bare/root route: that branch accepts only CLI
  lines starting with `!`. A nested `TEXT` preceded by a keyword has no such
  restriction. The rule is implemented outside the parameter validator. In
  the bare/root case, the leading `!` is included in `raw` and counted by
  `len()` when checking bounds.
- IPv4 permits leading zeros in octets: `192.168.001.001` is normalized to
  `192.168.1.1`.
- IPv6 follows the standard `ipaddress.IPv6Address` rules, including compressed
  and IPv4-mapped forms. Zone identifiers containing `%` are forbidden.
- An IPv6 prefix is not converted into a network: host bits are retained. For
  example, `2001:0DB8::0001/064` is normalized to `2001:db8::1/64`, not
  `2001:db8::/64`.
- All IP types belong to `STRUCTURED`, so a matching IP type is preferred over
  a generic `STRING`. An invalid address-like value returns `INVALID`; a
  lexically unrelated token returns `NOT_APPLICABLE`.
- `STRING<min-max>/<min-max>` is not included in the built-in registry.

## `models.py`: Models and Interfaces

All dataclass models in this module are declared with
`frozen=True, slots=True`. Their fields cannot be modified after construction.

### `ParameterStatus`

```python
class ParameterStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    VALID = "valid"
    INVALID = "invalid"
```

A string enum for the result of `probe()`. Because it is a `StrEnum`, its
values can be serialized as ordinary strings.

### `ParameterFamily`

```python
class ParameterFamily(StrEnum):
    ENUM = "enum"
    STRUCTURED = "structured"
    NUMERIC = "numeric"
    GENERIC = "generic"
    REMAINDER = "remainder"
```

A broad category describing the type’s behavior. The matcher uses the family
to prefer more specific parameters deterministically: `ENUM`, followed by
`STRUCTURED`, `NUMERIC`, `GENERIC`, and `REMAINDER`. A command’s literal branch
ranks even higher and is not a `ParameterFamily`.

- `ENUM` — a finite set of known values.
- `STRUCTURED` — a value with a fixed structure, such as a date or MAC address.
- `NUMERIC` — numbers and numeric ranges.
- `GENERIC` — an arbitrary token.
- `REMAINDER` — the remaining portion of a line.

`family` is part of a custom type’s contract: choosing the wrong family can
change which branch is considered more specific.

### `ParameterIssue`

Constructor:

```python
ParameterIssue(
    code: str,
    message: str,
    expected: str | None = None,
    actual: str | None = None,
)
```

A machine-readable error description:

- `code` — a stable short identifier for the cause;
- `message` — human-readable text;
- `expected` — an optional description of the expected constraint or format;
- `actual` — an optional actual value or measurable property of it.

The object does not prescribe a list of codes. Built-in validators use the codes
listed in the error section below.

### `ParameterResult`

Constructor:

```python
ParameterResult(
    status: ParameterStatus,
    normalized: object | None = None,
    issue: ParameterIssue | None = None,
)
```

Fields:

- `status` — one of the three results;
- `normalized` — the normalized value for `VALID`;
- `issue` — the reason for `INVALID`.

The direct constructor is usually unnecessary; using the class methods is
safer.

#### `__post_init__() -> None`

Validates these invariants after construction:

- `INVALID` must contain an `issue`;
- `VALID` and `NOT_APPLICABLE` cannot contain an `issue`;
- `NOT_APPLICABLE` cannot contain `normalized`.

A violation raises `ValueError`. The current contract permits
`ParameterResult.success(None)`: it is still `VALID`, so success cannot be
determined from the `normalized` field.

#### `applicable: bool`

Read-only property. Returns `True` for `VALID` and `INVALID`, and `False` only
for `NOT_APPLICABLE`.

#### `valid: bool`

Read-only property. Returns `True` only for `VALID`.

#### `message: str | None`

Read-only property. Returns `issue.message` when an issue exists, otherwise
`None`. By invariant, a non-empty message occurs only for `INVALID`.

#### `not_applicable() -> ParameterResult`

Class-method constructor:

```python
ParameterResult.not_applicable()
```

Creates:

```python
ParameterResult(
    status=ParameterStatus.NOT_APPLICABLE,
    normalized=None,
    issue=None,
)
```

#### `success(normalized: object) -> ParameterResult`

Class-method constructor for a successful result. `normalized` can have any
type, including `str`, `int`, `bool`, and `None`.

```python
ParameterResult.success(42)
```

#### `failure(...) -> ParameterResult`

Signature:

```python
ParameterResult.failure(
    code: str,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
) -> ParameterResult
```

Creates `INVALID` with a nested `ParameterIssue`. `expected` and `actual` are
keyword-only.

### `ParameterDeclaration`

Constructor:

```python
ParameterDeclaration(
    type_id: str,
    source: str,
    start: int,
    end: int,
    minimum: int | None = None,
    maximum: int | None = None,
    choices: tuple[str, ...] = (),
    metadata: tuple[tuple[str, str], ...] = (),
)
```

This represents an already recognized placeholder inside a complete command
pattern.

- `type_id` — the ID of the registered `ParameterType`;
- `source` — the exact placeholder substring;
- `start` — the index of its first character in the complete pattern;
- `end` — its exclusive right boundary;
- `minimum`, `maximum` — parsed bounds when present;
- `choices` — enum choices;
- `metadata` — immutable custom attributes represented as key/value pairs.

Indices count Python characters, not bytes. The span is half-open:
`pattern[start:end] == source`.

#### `__post_init__() -> None`

Validates:

- `0 <= start <= end`;
- `end - start == len(source)`.

A violation raises `ValueError`. The method does not verify that `type_id` is
registered, that `minimum <= maximum`, or that the span actually refers to a
particular external string; the code that creates the object is responsible
for those conditions.

### `DeclarationRecognition`

Constructor:

```python
DeclarationRecognition(
    end: int,
    minimum: int | None = None,
    maximum: int | None = None,
    choices: tuple[str, ...] = (),
    metadata: tuple[tuple[str, str], ...] = (),
)
```

An intermediate recognizer result. It does not yet contain `type_id`, `source`,
or `start`; `ParameterType.recognize()` adds them.

- `end` — the exclusive end position of the declaration in the complete
  pattern;
- the remaining fields are copied to `ParameterDeclaration` unchanged.

The class performs no additional runtime field validation. A custom recognizer
must return a valid `end`.

### `ParameterToken`

Constructor:

```python
ParameterToken(
    raw: str,
    start: int,
    end: int,
    next_position: int,
)
```

The result of reading a value from a real CLI line:

- `raw` — the extracted text;
- `start`, `end` — the value’s half-open span;
- `next_position` — the position from which the matcher should continue
  reading.

Ordinary readers return `next_position == end`. A custom reader can skip its
own delimiter and return a position greater than `end`.

#### `__post_init__() -> None`

Validates:

- `0 <= start <= end`;
- `end - start == len(raw)`;
- `next_position >= end`.

Otherwise, it raises `ValueError`. Like `ParameterDeclaration`, the object does
not retain the source string and cannot verify that the span corresponds to
that string.

### `DeclarationRecognizer`

A structural-typing protocol. Inheriting from it is optional; a method with a
compatible signature is sufficient.

```python
recognize(
    pattern: str,
    position: int,
) -> DeclarationRecognition | None
```

Input:

- `pattern` — the complete command pattern;
- `position` — the exact position where a placeholder is expected.

Result:

- `DeclarationRecognition` if a declaration starts exactly at `position`;
- `None` if the recognizer does not apply to this fragment;
- `ParameterDeclarationError` if the fragment begins like a declaration known
  to this recognizer but is syntactically malformed.

### `ParameterReader`

A structural-typing protocol:

```python
read(text: str, position: int) -> ParameterToken | None
```

`text` is the complete real CLI command, and `position` is where the search
starts. Returns a token or `None` when no value remains.

A reader does not validate the value’s meaning. It only determines its
boundaries.

### `ParameterValidator`

A structural-typing protocol:

```python
probe(
    raw: str,
    declaration: ParameterDeclaration,
) -> ParameterResult
```

A validator must follow the tri-state contract:

- valid value → `success(...)`;
- shape belongs to the type but violates a constraint → `failure(...)`;
- shape does not belong to the type → `not_applicable()`.

### `ParameterType`

Constructor:

```python
ParameterType(
    type_id: str,
    family: ParameterFamily,
    declaration_recognizer: DeclarationRecognizer,
    reader: ParameterReader,
    validator: ParameterValidator,
)
```

The composition root for one type:

- the recognizer understands its spelling in a pattern;
- the reader extracts its value from the CLI;
- the validator validates and normalizes the value;
- the family communicates specificity to the matcher.

The dataclass itself does not validate the `type_id` format;
`ParameterTypeRegistry.register()` does.

#### `recognize(pattern: str, position: int = 0) -> ParameterDeclaration | None`

Calls `declaration_recognizer.recognize(pattern, position)`.

- Returns `None` when recognition returns `None`.
- On success, attaches its own `type_id`, calculates
  `source=pattern[position:recognized.end]`, and copies bounds, choices, and
  metadata into an immutable `ParameterDeclaration`.
- Does not catch errors from the recognizer.

#### `read(text: str, position: int = 0) -> ParameterToken | None`

Delegates directly to `reader.read(text, position)` without additional
processing.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

If `declaration.type_id != self.type_id`, the validator is not called and the
method returns `NOT_APPLICABLE`. Otherwise, it delegates to the validator.

## `readers.py`: Reading CLI Values

### `_skip_whitespace(text: str, position: int) -> int`

A private module-level helper. It verifies that
`0 <= position <= len(text)` and otherwise raises `ValueError` with the message
`parameter position is outside the command line`.

It then skips every character for which Python `str.isspace()` returns `True`
and returns the position of the first non-whitespace character, or `len(text)`.

### `SingleTokenReader`

A reader for one non-whitespace token.

#### `read(text: str, position: int) -> ParameterToken | None`

1. Skips leading whitespace through `_skip_whitespace()`.
2. Returns `None` if the end of the string has been reached.
3. Reads through the next Unicode whitespace character or the end of the
   string.

Punctuation has no special meaning and remains part of `raw`.

```python
token = SingleTokenReader().read("  Vlanif100 next", 0)
# ParameterToken(raw="Vlanif100", start=2, end=11, next_position=11)
```

An invalid position causes `_skip_whitespace()` to raise `ValueError`.

### `RemainderReader`

A reader for the complete non-empty remainder of a line.

#### `read(text: str, position: int) -> ParameterToken | None`

Skips whitespace before the value. Returns `None` if nothing remains.
Otherwise, it reads through the physical end of `text`.

Internal and trailing whitespace is included in `raw`:

```python
token = RemainderReader().read("  description with spaces  ", 0)
# raw == "description with spaces  "
# start == 2
# end == next_position == len(text)
```

## `recognizers.py`: Pattern Declarations

### `_is_boundary(pattern: str, end: int) -> bool`

A private helper that checks a placeholder’s right boundary. Returns `True` if
`end` is:

- exactly at the end of the pattern;
- before whitespace;
- before one of `|`, `}`, `]`, `*`, or `&`.

This prevents partial recognition: a recognizer for `YES` must not accept the
beginning of the literal `YES-NO`.

### `_ascii_lower(value: str) -> str`

A private ASCII case-folding helper. It replaces only `A`–`Z` with `a`–`z` and
does not perform Unicode case folding. It is used to check enum-choice
uniqueness.

### `ExactDeclarationRecognizer`

Constructor:

```python
ExactDeclarationRecognizer(
    placeholder: str,
    minimum: int | None = None,
    maximum: int | None = None,
    metadata: tuple[tuple[str, str], ...] = (),
)
```

Recognizes one exact placeholder spelling. The additional fields attach bounds
or metadata to a fixed spelling.

#### `recognize(pattern: str, position: int) -> DeclarationRecognition | None`

Recognition succeeds only when:

1. `pattern.startswith(placeholder, position)`;
2. a valid `_is_boundary()` immediately follows the placeholder.

On success, it returns a `DeclarationRecognition` with
`end=position + len(placeholder)` and the configured `minimum`, `maximum`, and
`metadata`. Otherwise, it returns `None`. This recognizer never raises
`ParameterDeclarationError`.

### `BoundedDeclarationRecognizer`

Constructor:

```python
BoundedDeclarationRecognizer(
    name: str,
    base: int = 10,
    allow_negative: bool = False,
)
```

Recognizes `NAME<minimum-maximum>`.

- `name` — a case-sensitive prefix such as `INTEGER`;
- `base` — the numeral base for the bounds; only `10` and `16` are supported;
- `allow_negative` — permits `+` and `-` signs in base-10 bounds.

This flag controls the declaration-bound syntax. A separate validator controls
the syntax of an actual value.

#### `recognize(pattern: str, position: int) -> DeclarationRecognition | None`

Behavior:

- returns `None` if `position` does not contain the exact `f"{name}<"` prefix;
- finds the first closing `>`;
- parses two bounds separated by `-`;
- converts them to `int`;
- requires `minimum <= maximum`;
- requires a valid right boundary.

On success, it returns
`DeclarationRecognition(end, minimum, maximum)`.

`ParameterDeclarationError` messages:

- `unclosed NAME declaration` — no `>` is present;
- `invalid bounds in NAME declaration` — the bounds do not conform to the
  configured base;
- `minimum is greater than maximum in NAME declaration`.

If the declaration itself is valid but there is no boundary after `>`, the
method returns `None` rather than an error.

#### `_number_pattern() -> str`

A private method returning the regex for one bound:

- base 10: `[0-9]+`, or `[+-]?[0-9]+` when `allow_negative=True`;
- base 16: an optional `0x`/`0X` and one or more hexadecimal digits.

For any other base, it raises
`ValueError("unsupported declaration bound base: …")`.

#### `_parse_number(value: str) -> int`

A private method. Calls `int(value, self.base)` and returns the Python `int`.

### `EnumDeclarationRecognizer`

Constructor without arguments:

```python
EnumDeclarationRecognizer()
```

#### `recognize(pattern: str, position: int) -> DeclarationRecognition | None`

Recognizes the complete, case-sensitive spelling
`ENUM{choice1,choice2,...}`.

Algorithm:

1. Checks the exact `ENUM{` prefix.
2. Finds the first `}`.
3. Checks the boundary after `}`.
4. Splits the contents at commas and strips whitespace around each choice.
5. Permits one trailing comma.
6. Requires non-empty, case-insensitively unique choices.

Returns `DeclarationRecognition(end=end, choices=tuple(choices))`.

`ParameterDeclarationError` messages:

- `unclosed ENUM declaration`;
- `ENUM must contain non-empty choices`;
- `ENUM cannot contain the abbreviated '...' choice`;
- `ENUM choices must be unique (case-insensitive)`.

Uniqueness comparison uses ASCII case folding only. Choice order and original
case are retained.

## `validators.py`: Validation and Normalization

### `_bounded_number(value: int, declaration: ParameterDeclaration) -> ParameterResult | None`

A private numeric-bound helper:

- below `minimum` → `INVALID`, code `below_minimum`;
- above `maximum` → `INVALID`, code `above_maximum`;
- within the range or when the relevant bound is absent → `None`.

`expected` contains `>= minimum` or `<= maximum`, while `actual` contains the
normalized `int` as a decimal string.

### `_bounded_length(value: str, declaration: ParameterDeclaration) -> ParameterResult | None`

A private length-bound helper:

- shorter than `minimum` → `INVALID`, code `too_short`;
- longer than `maximum` → `INVALID`, code `too_long`;
- valid length → `None`.

Length is calculated with `len(value)`. `actual` contains the length as a
decimal string, not the original value.

### `_ascii_lower(value: str) -> str`

A private ASCII-only conversion from `A`–`Z` to `a`–`z`, used by
`EnumValidator`. Unicode case is not normalized.

### `_looks_like_ipv6(value: str) -> bool`

A private applicability heuristic for IPv6 validators. Returns `True` when a
value contains at least two colons. This is not complete IPv6 validation:
`ipaddress.IPv6Address` performs the final check.

The heuristic distinguishes a malformed address-like token such as
`2001:db8::gg` (`INVALID`) from an unrelated name such as `foo:bar`
(`NOT_APPLICABLE`).

### `IntegerValidator`

Constructor without arguments.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

- Does not match `[+-]?[0-9]+` → `NOT_APPLICABLE`.
- Matches, but `int(raw, 10)` is outside the bounds → `INVALID` from
  `_bounded_number()`.
- Valid → `VALID`, with a Python `int` as `normalized`.

For example, `+7` and `-15` are lexically applicable; `1.0` and `12ms` are not.

### `HexValidator`

Constructor without arguments.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

- Does not match `(?:0[xX])?[0-9A-Fa-f]+` → `NOT_APPLICABLE`.
- Hexadecimal number is outside the bounds → `INVALID`.
- Valid → `VALID`, with `normalized=int(raw, 16)`.

The `0x` prefix is optional. Therefore, raw `10` is normalized to `16`, not
`10`.

### `TokenStringValidator`

Used by `STRING` and `PASSWORDEX`.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

- Empty raw or any character for which `str.isspace()` is true → `INVALID`,
  code `invalid_token`;
- token shorter or longer than the bounds → `INVALID` with
  `too_short`/`too_long`;
- otherwise → `VALID`, with `normalized` equal to the original raw.

This validator considers a whitespace-containing string applicable but
invalid. In the normal parser flow, `SingleTokenReader` extracts one token in
advance, but the rule matters when calling `evaluate()` directly or using a
custom reader.

### `TextValidator`

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

Imposes no structural or whitespace requirements. It checks only
`_bounded_length()`:

- invalid length → `INVALID`;
- otherwise → `VALID` with the original raw.

The validator itself does not check for a leading `!`: that command-parser
rule applies only to bare/root `TEXT<min-max>` matched at position `0`. In the
pattern `description TEXT<1-80>`, the validator normally receives a multiword
remainder without the `description` keyword.

### `EnumValidator`

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

Searches for raw in `declaration.choices` without ASCII case sensitivity.

- Match → `VALID`; `normalized` is the original choice from the declaration.
- No match → `NOT_APPLICABLE`.

This validator never returns `INVALID`.

```python
registry.evaluate("ENUM{Eth-trunk,Vlanif,}", "vLaNiF").normalized
# "Vlanif"
```

### `DateTimeValidator`

Constructor:

```python
DateTimeValidator(
    expression: str,
    datetime_format: str,
    description: str,
    prefix_for_parsing: str = "",
)
```

- `expression` — a regex for the complete lexical form;
- `datetime_format` — a `datetime.strptime` format;
- `description` — the expected-format text used in an issue;
- `prefix_for_parsing` — a prefix added only for calendar validation. For
  example, `MM-DD` is checked as `"2000-" + raw`.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

`declaration` is deliberately unused.

1. If `re.fullmatch(expression, raw)` does not match → `NOT_APPLICABLE`.
2. If the shape matches but `datetime.strptime(prefix + raw, format)` rejects
   the calendar value → `_failure(raw)`.
3. If the value is valid → `VALID`, with `normalized` equal to the original
   raw.

Thus, `2025-2-3` is `NOT_APPLICABLE` to the ISO type because of its width,
while `2025-02-30` is `INVALID` because its shape is correct but the date does
not exist.

#### `_failure(raw: str) -> ParameterResult`

A private method. Creates:

```python
ParameterResult.failure(
    "invalid_datetime",
    f"value must be a valid {description}",
    expected=description,
    actual=raw,
)
```

### `MacValidator`

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

`declaration` is unused. The complete accepted format is:

```text
1–4 hexadecimal digits - 1–4 hexadecimal digits - 1–4 hexadecimal digits
```

Results:

- valid shape → `VALID`; every group is converted to lowercase and padded on
  the left with zeros to four characters;
- the string consists of hexadecimal characters and hyphens and contains a
  hyphen, but grouping is invalid → `INVALID`, code `invalid_mac`;
- the string contains other characters or no hyphen → `NOT_APPLICABLE`.

For example, `1-aB-CD09` is normalized to `0001-00ab-cd09`.

### `IPv4AddressValidator`

Validates the `X.X.X.X` placeholder.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

`declaration` is unused.

1. `_looks_like_address(raw)` determines whether the token belongs to the IPv4
   branch. If it does not, the result is `NOT_APPLICABLE`.
2. The value is split at periods. Exactly four non-empty components are
   required, each containing `1..3` decimal digits.
3. Each octet is converted to `int` and must be within `0..255`.
4. On success, the result is `VALID`; `normalized` is assembled from the
   decimal values, so leading zeros are removed.

For example, the calling parser preserves raw `192.168.001.001`, while the
validator returns normalized `192.168.1.1`.

#### `_looks_like_address(raw: str) -> bool`

A static private heuristic. Returns `True` when raw contains a period and
consists entirely of digits, periods, `+`, and `-`. This is sufficient only to
determine applicability: `1.2.3` and `-1.2.3.4` subsequently become `INVALID`,
while the hostname `router.example.com` is `NOT_APPLICABLE`.

#### `_failure(raw: str) -> ParameterResult`

A static private factory for an `INVALID` result:

- code: `invalid_ipv4_address`;
- message: `value must be a valid IPv4 address`;
- expected: `four decimal octets from 0 to 255`;
- actual: the original raw.

### `IPv6AddressValidator`

Validates the `X:X::X:X` placeholder. It supports the complete standard IPv6
form: eight groups, `::` compression, and IPv4-mapped addresses.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

`declaration` is unused.

1. If `_looks_like_ipv6(raw)` returns `False`, the result is
   `NOT_APPLICABLE`.
2. The presence of `%` makes an address-like token `INVALID`: zone identifiers
   are not supported.
3. `ipaddress.IPv6Address(raw)` performs syntactic validation.
4. On success, `str(address)` becomes `normalized`: case is lowercase, and
   valid `::` compression is applied canonically.

For example, `2001:0DB8:0:0:0:0:0:1` is normalized to `2001:db8::1`.

#### `_failure(raw: str) -> ParameterResult`

A static private factory for an `INVALID` result:

- code: `invalid_ipv6_address`;
- message: `value must be a valid IPv6 address`;
- expected: `IPv6 colon-hexadecimal notation`;
- actual: the original raw.

### `IPv6PrefixValidator`

Validates the `X:X::X:X/M` placeholder.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

`declaration` is unused.

1. The token applies to the type when `_looks_like_ipv6(raw)` returns `True` or
   raw starts with `/`; otherwise, the result is `NOT_APPLICABLE`.
2. Exactly one `/`, a non-empty IPv6 value on the left, and only decimal digits
   on the right are required. A prefix-length spelling longer than three
   characters is rejected.
3. A zone identifier `%` is forbidden in the address portion.
4. The address is validated with `IPv6Address`, and the prefix length must be
   in `0..128`.
5. On success, `normalized` has the form
   `f"{address}/{prefix_length}"`.

`IPv6Address`, rather than `IPv6Network`, is used, so host bits are
deliberately retained. Raw `2001:0DB8::0001/064` produces normalized
`2001:db8::1/64`. The forms `::/0`, `::1/128`, and an IPv4-mapped prefix are
accepted.

#### `_failure(raw: str) -> ParameterResult`

A static private factory for an `INVALID` result:

- code: `invalid_ipv6_prefix`;
- message: `value must be a valid IPv6 prefix`;
- expected: `IPv6 address followed by /0 through /128`;
- actual: the original raw.

## `registry.py`: `ParameterTypeRegistry`

### `__init__(parameter_types: tuple[ParameterType, ...] = ()) -> None`

```python
ParameterTypeRegistry(
    parameter_types: tuple[ParameterType, ...] = (),
)
```

Creates a mutable registry and registers the supplied types in sequence through
`register()`. The same ID-format and duplicate-ID checks therefore apply to
constructor input.

Insertion order is retained in `parameter_types`, but declaration recognition
selects the longest match rather than the first type.

### `is_frozen: bool`

Read-only property. `False` for a new registry and `True` after `freeze()`.

### `parameter_types: tuple[ParameterType, ...]`

Read-only property. Returns a new tuple of registered objects in registration
order. The tuple cannot be modified, but the strategies inside
`ParameterType` should themselves be designed as stateless or safe for shared
use.

### `register(parameter_type: ParameterType) -> ParameterTypeRegistry`

Registers a type and returns the same `self`, permitting chaining:

```python
registry.register(first).register(second)
```

Requirements for `type_id`:

```text
[a-z][a-z0-9-]*
```

The first character must be a lowercase ASCII letter, followed by lowercase
ASCII letters, digits, or hyphens.

`ParameterRegistryError` is raised when:

- the registry is frozen;
- the ID has an invalid format;
- the ID is already registered.

The method does not check for overlap between the syntax of different
recognizers. Any ambiguity is detected by `recognize()`.

### `freeze() -> ParameterTypeRegistry`

Prevents subsequent calls to `register()` and returns the same `self`.
Repeated calls to `freeze()` are safe.

The operation does not create deep copies of the types.

### `clone() -> ParameterTypeRegistry`

Creates a new **unfrozen** registry with the same `ParameterType` objects and
the same order. The source registry’s `is_frozen` state is not copied.

`CommandLineParser` uses exactly `source_registry.clone().freeze()`.
Therefore, subsequent registration in the source registry does not change an
already constructed parser.

### `get(type_id: str) -> ParameterType | None`

Returns the registered type for the exact ID, or `None`.

### `family_of(type_id: str) -> ParameterFamily | None`

Returns the registered type’s family, or `None` for an unknown ID.

### `recognize(pattern: str, position: int = 0) -> ParameterDeclaration | None`

Runs the recognizer of every registered type exactly at `position`.

- No matches → `None`.
- Matches of different lengths → the declaration with the greatest `end` is
  selected.
- Multiple matches with the same greatest `end` →
  `ParameterRegistryError("ambiguous declaration at character …")`.

Longest-match behavior is needed, for example, so that the exact
`YYYY/MM/DD,HH:MM:SS` placeholder is not mistakenly shortened to another
prefix.

A `ParameterDeclarationError` from an individual recognizer propagates. The
position must be valid for the recognizers being used; the registry does not
validate it separately.

### `read(declaration, text, position=0) -> ParameterToken | None`

Complete signature:

```python
read(
    declaration: ParameterDeclaration,
    text: str,
    position: int = 0,
) -> ParameterToken | None
```

Finds the type by `declaration.type_id` and delegates to
`ParameterType.read()`. An unknown type ID produces `None`.

### `probe(raw, declaration) -> ParameterResult`

```python
probe(
    raw: str,
    declaration: ParameterDeclaration,
) -> ParameterResult
```

Finds the type and delegates to `ParameterType.probe()`. An unknown type ID
produces `NOT_APPLICABLE`.

### `evaluate(declaration_pattern, raw) -> ParameterResult`

```python
evaluate(
    declaration_pattern: str,
    raw: str,
) -> ParameterResult
```

A convenient combined call for one complete placeholder:

1. `recognize(declaration_pattern, position=0)`;
2. verifies that the declaration ends exactly at the end of the string;
3. calls `probe(raw, declaration)`.

Results:

- a complete known placeholder and value → the validator’s result;
- an unknown or only partially recognized placeholder → `NOT_APPLICABLE`;
- `ParameterDeclarationError` → `INVALID` with code
  `invalid_declaration`, `message=error.message`, and
  `actual=declaration_pattern`.

`ParameterRegistryError`, including recognizer ambiguity, is not caught.

## `errors.py`: Type-Definition Exceptions

### `ParameterDeclarationError`

Inherits from `ValueError`.

#### `__init__(message: str, start: int, end: int) -> None`

```python
ParameterDeclarationError(
    message: str,
    start: int,
    end: int,
)
```

Public attributes:

- `message` — the cause without coordinates;
- `start`, `end` — the malformed declaration’s span.

Its string representation is:

```text
{message} at characters {start}:{end}
```

The exception means that a recognizer identified the beginning of its
placeholder, but the declaration is syntactically malformed.

### `ParameterRegistryError`

Inherits from `ValueError` and adds no fields or methods. It is used for
invalid registration, mutation of a frozen registry, and ambiguous declaration
recognition.

## `builtins.py`: Building the Default Registry

### `_date_time_types(reader: SingleTokenReader) -> tuple[ParameterType, ...]`

A private factory for seven date/time types. Every created type:

- belongs to `ParameterFamily.STRUCTURED`;
- uses the supplied `SingleTokenReader`;
- is recognized through `ExactDeclarationRecognizer`;
- is validated by a separately configured `DateTimeValidator` instance.

The types are returned in this order:

1. `date-slash`;
2. `date-iso`;
3. `month-day`;
4. `date-us`;
5. `datetime-slash`;
6. `time-seconds`;
7. `time`.

### `builtin_parameter_types() -> tuple[ParameterType, ...]`

Creates a fresh tuple containing all 17 built-in definitions. A new
`SingleTokenReader` is shared by the token-based types within one call;
`TEXT` is recognized through `BoundedDeclarationRecognizer("TEXT")` and uses
`RemainderReader`.

`ipv4-address`, `ipv6-address`, and `ipv6-prefix` use
`ExactDeclarationRecognizer`, the shared `SingleTokenReader`, the
`STRUCTURED` family, and their corresponding specialized validators.

Tuple order:

1. `hex`;
2. `string`;
3. `integer`;
4. `enum`;
5. `passwordex`;
6. `mac`;
7. `ipv4-address`;
8. `ipv6-address`;
9. `ipv6-prefix`;
10. `text`;
11. the seven date/time types in `_date_time_types()` order.

The function does not return a singleton: every call creates fresh immutable
`ParameterType` objects and stateless strategies.

### `default_parameter_registry() -> ParameterTypeRegistry`

Equivalent to:

```python
ParameterTypeRegistry(builtin_parameter_types())
```

Returns a new **mutable** registry. It can be extended before being passed to
`CommandLineParser`.

## Built-in Error Codes

| Code | Source | When it occurs | `actual` |
|---|---|---|---|
| `invalid_declaration` | `ParameterTypeRegistry.evaluate()` | A known declaration is syntactically malformed | Declaration string |
| `below_minimum` | `_bounded_number()` | Number is below `minimum` | Normalized decimal number |
| `above_maximum` | `_bounded_number()` | Number is above `maximum` | Normalized decimal number |
| `too_short` | `_bounded_length()` | Length is below `minimum` | Length |
| `too_long` | `_bounded_length()` | Length is above `maximum` | Length |
| `invalid_token` | `TokenStringValidator` | Empty value or internal whitespace | Original value |
| `invalid_datetime` | `DateTimeValidator` | Shape is correct, but the calendar value is impossible | Original value |
| `invalid_mac` | `MacValidator` | String resembles a MAC address but has invalid groups | Original value |
| `invalid_ipv4_address` | `IPv4AddressValidator` | Token resembles IPv4 but does not contain four octets in `0..255` | Original value |
| `invalid_ipv6_address` | `IPv6AddressValidator` | Token resembles IPv6 but has invalid syntax or contains a zone identifier | Original value |
| `invalid_ipv6_prefix` | `IPv6PrefixValidator` | Address-like token does not contain valid IPv6 followed by `/0..128` | Original value |

A custom validator can define its own stable codes.

## Custom Type Example

The following example adds a `BOOLEAN` placeholder that accepts `yes` and `no`
and normalizes them to Python `bool`.

```python
from vrp_parser import CommandLineParser
from vrp_parser.parameters import (
    ExactDeclarationRecognizer,
    ParameterDeclaration,
    ParameterFamily,
    ParameterResult,
    ParameterType,
    SingleTokenReader,
    default_parameter_registry,
)


class BooleanValidator:
    def probe(
        self,
        raw: str,
        declaration: ParameterDeclaration,
    ) -> ParameterResult:
        del declaration
        normalized = raw.lower()
        if normalized == "yes":
            return ParameterResult.success(True)
        if normalized == "no":
            return ParameterResult.success(False)
        if raw.isalpha():
            return ParameterResult.failure(
                "invalid_boolean",
                "value must be yes or no",
                expected="yes | no",
                actual=raw,
            )
        return ParameterResult.not_applicable()


registry = default_parameter_registry()
registry.register(
    ParameterType(
        type_id="boolean",
        family=ParameterFamily.ENUM,
        declaration_recognizer=ExactDeclarationRecognizer("BOOLEAN"),
        reader=SingleTokenReader(),
        validator=BooleanValidator(),
    )
)

parser = CommandLineParser(
    {"commands": ["feature BOOLEAN"]},
    parameter_types=registry,
)

enabled = parser.parse("feature yes")
disabled = parser.parse("feature no")
invalid = parser.parse("feature maybe")
```

Design rules for a custom type:

1. Use a unique `type_id` matching `[a-z][a-z0-9-]*`.
2. The recognizer must accept a placeholder only at the exact `position`.
3. The reader must return valid character spans and a continuation position.
4. The validator must distinguish `INVALID` from `NOT_APPLICABLE`.
5. Return a stable `normalized` type so calling code does not have to guess its
   format.
6. Choose the family based on value specificity, not the placeholder’s name.
7. Register every custom type before constructing `CommandLineParser`: the
   parser clones and freezes the supplied registry.

## Standalone Parameter Validation

Use `evaluate()` to validate a type without constructing a parser:

```python
from vrp_parser.parameters import ParameterStatus, default_parameter_registry

registry = default_parameter_registry()
result = registry.evaluate("H-H-H", "1-aB-CD09")

assert result.status is ParameterStatus.VALID
assert result.normalized == "0001-00ab-cd09"
assert result.issue is None
```

Individual operations are available for step-by-step integration:

```python
declaration = registry.recognize(
    "set preference INTEGER<1-15>",
    position=len("set preference "),
)
assert declaration is not None

token = registry.read(declaration, "set preference 10", len("set preference "))
assert token is not None

result = registry.probe(token.raw, declaration)
assert result.valid
assert result.normalized == 10
```
