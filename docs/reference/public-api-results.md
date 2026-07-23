# Public API, Results, CLI, and Serialization

This document describes the production entities from:

- `src/vrp_parser/api.py`;
- `src/vrp_parser/results.py`;
- `src/vrp_parser/serialization.py`;
- `src/vrp_parser/cli.py`;
- `src/vrp_parser/__init__.py` and `src/vrp_parser/__main__.py`;
- the `manual_test.py` manual test harness.

All string positions are zero-based, and ranges use the half-open
`[start, end)` convention: the character at index `end` is not included.
Physical line numbers start at one.

## `CommandLineParser`

The public entry point for compiling a pattern catalog and parsing one
physical CLI line.

### `CommandLineParser.__init__`

```python
CommandLineParser(
    pattern_document: Mapping[str, Any],
    *,
    parameter_types: ParameterTypeRegistry | None = None,
)
```

The input `pattern_document` must have the following format:

```json
{
  "commands": [
    "#",
    "TEXT<1-4096>",
    "description TEXT<1-80>",
    "peer X.X.X.X",
    "interface STRING<1-63>"
  ]
}
```

Document rules:

- the root is a mapping/object;
- the `commands` key is required;
- `commands` must be a non-empty sequence of strings;
- empty and whitespace-only patterns are rejected;
- string order matters: it determines the primary result for tied matches;
- duplicate patterns are allowed and retained as separate source entries.

If `parameter_types` is omitted, the default registry is created. A supplied
registry is cloned and frozen, so later changes to the source registry do not
affect the constructed parser.

The constructor:

1. validates the document format;
2. parses every pattern into an AST;
3. applies the runtime policy;
4. builds the shared immutable command graph;
5. creates the matcher.

Possible exceptions:

- `PatternDocumentError` — the document structure is invalid;
- `PatternCompilationError` — one or more patterns are invalid.

### `command_count`

```python
parser.command_count -> int
```

The number of source patterns in the compiled document. Duplicates are counted
separately.

### `command_graph`

```python
parser.command_graph -> CommandGraph
```

Read-only access to the internal shared graph. This is useful for diagnostics,
visualization, and tests; ordinary parser users do not need it.

### `parameter_types`

```python
parser.parameter_types -> ParameterTypeRegistry
```

Returns the frozen registry used to compile the parser. Calling `register()`
on it fails.

### `parse`

```python
parser.parse(line: str, line_number: int = 1) -> LineResult
```

Parses exactly one physical line without `\n` or `\r`.

Input:

- `line` — the original string, including indentation and trailing spaces;
- `line_number` — a positive integer stored in the result.

Behavior:

- the original `raw` value is preserved unchanged;
- leading whitespace characters are preserved in `indent`;
- trailing spaces do not participate in recognition;
- an empty or whitespace-only line returns `BlankLine`;
- `description TEXT<1-80>` reads the remainder after the keyword, while
  bare/root `TEXT<min-max>` at position `0` accepts only lines beginning with
  `!`;
- a successful match, including an ambiguous one, returns `ParsedCommand`;
- an unknown, syntactically incomplete, or invalid command returns
  `ErrorLine`.

Exceptions indicate an invalid API call only:

- `TypeError` if `line` is not a string;
- `ValueError` if multiple physical lines are supplied;
- `TypeError` if `line_number` is not an `int` or is a `bool`;
- `ValueError` if `line_number < 1`.

An invalid CLI command does not raise an exception; it is represented by an
`ErrorLine`.

### `from_json`

```python
CommandLineParser.from_json(
    source: str,
    *,
    parameter_types: ParameterTypeRegistry | None = None,
) -> CommandLineParser
```

Accepts JSON text, verifies that the JSON root is an object/mapping, and calls
the constructor. Invalid JSON is converted to `PatternDocumentError`.

### `from_json_file`

```python
CommandLineParser.from_json_file(
    path: str | pathlib.Path,
    *,
    parameter_types: ParameterTypeRegistry | None = None,
) -> CommandLineParser
```

Reads a UTF-8 file and delegates to `from_json()`.

Additional errors:

- `OSError`/`FileNotFoundError` — the file cannot be read;
- `UnicodeDecodeError` — the file is not valid UTF-8.

### Internal `CommandLineParser` Methods

These methods are implementation details and are not intended to be called by
users.

| Method | Purpose and result |
| --- | --- |
| `_parsed(line_number, raw, indent, outcome)` | Converts an internal `ResolvedMatch` into a public `ParsedCommand`. |
| `_validate_input(line, line_number)` | Validates types, the absence of a line terminator, and a positive line number; returns `None` or raises `TypeError`/`ValueError`. |
| `_indent_end(line)` | Returns the index of the first non-whitespace character or `len(line)`. |
| `_commands(document)` | Extracts and validates `commands`, returning `tuple[str, ...]`. |

## `ConfigurationParser`

A wrapper around an already compiled `CommandLineParser`. It does not build a
second graph or change recognition rules.

### `ConfigurationParser.__init__`

```python
ConfigurationParser(
    line_parser: CommandLineParser,
    report_factory: ParseReportFactory | None = None,
)
```

- `line_parser` is reused for every physical line;
- `report_factory` is an optional dependency-injection point for constructing
  the final report.

### `line_parser`

```python
configuration_parser.line_parser -> CommandLineParser
```

Returns the original line parser.

### `parse`

```python
configuration_parser.parse(content: str) -> ParseReport
```

Accepts the complete configuration text. It supports `LF`, `CRLF`, and `CR`.

Behavior:

- empty text produces a report with `lines == ()`;
- one trailing line terminator does not create a synthetic extra line;
- blank lines inside the file are preserved as `BlankLine`;
- an error on one line does not stop parsing of subsequent lines;
- line numbers start at `1`.

If `content` is not a string, `TypeError` is raised.

### `_physical_lines`

```python
ConfigurationParser._physical_lines(content: str) -> tuple[str, ...]
```

An internal splitter for the three line-terminator forms. It returns strings
without the terminator characters.

## Successful Result Format

### `MatchStatus`

`StrEnum`, so its values serialize directly as strings.

| Value | Meaning |
| --- | --- |
| `UNIQUE = "unique"` | One best interpretation remains. |
| `EQUIVALENT = "equivalent"` | Multiple source patterns describe the same interpretation. |
| `AMBIGUOUS = "ambiguous"` | Different but equally ranked interpretations remain. This is a success, not an error. |

### `TextSpan`

```python
TextSpan(start: int, end: int)
```

A half-open range in the original string whose offsets account for
indentation.
`__post_init__()` requires `0 <= start <= end`; otherwise it raises
`ValueError`.

### `ParameterValue`

```python
ParameterValue(
    type_id: str,
    declaration: str,
    raw: str,
    normalized: Any,
    span: TextSpan,
)
```

| Field | Format |
| --- | --- |
| `type_id` | Stable type ID, such as `integer`, `date-iso`, or `ipv4-address`. |
| `declaration` | The placeholder from the pattern, such as `INTEGER<1-15>`. |
| `raw` | The actual CLI fragment without conversion. |
| `normalized` | The validator result: an `int`, string, `None`, or a custom object. |
| `span` | The position of `raw` in the original physical line. |

For example, parsing `peer 192.168.001.001` against `peer X.X.X.X` preserves
`raw="192.168.001.001"` and produces `normalized="192.168.1.1"`. IPv6 types
likewise preserve the source representation in `raw` and return a canonical
lowercase/compressed string in `normalized`.

### `VariationStep`

```python
VariationStep(
    kind: Literal["choice", "optional", "set", "repeat", "enum"],
    path: str,
    selected: tuple[int | str, ...] = (),
)
```

Describes one decision made while producing a concrete variation.

| `kind` | `selected` format |
| --- | --- |
| `choice` | Index of the selected alternative. |
| `optional` | An empty tuple when omitted, or the selected alternative index. |
| `set` | Alternative indices in their order of appearance in the input CLI line. |
| `repeat` | One integer: the actual repetition count. |
| `enum` | The normalized enum string value. |

`path` is a stable internal node/step address useful for comparison and
diagnostics.

### `PatternMatch`

```python
PatternMatch(
    pattern_id: str,
    pattern_index: int,
    original_pattern: str,
    variation: str,
    variation_id: str,
    parameters: tuple[ParameterValue, ...] = (),
    trace: tuple[VariationStep, ...] = (),
)
```

| Field | Meaning |
| --- | --- |
| `pattern_id` | Content-based source-pattern ID with a duplicate occurrence number. |
| `pattern_index` | Pattern index in the JSON `commands` array. |
| `original_pattern` | The unchanged source pattern string. |
| `variation` | The selected linear path; literals use canonical ASCII lowercase, while parameters remain declarations. |
| `variation_id` | Stable hash of the pattern ID, variation, and trace. |
| `parameters` | Parameter values captured by this match. |
| `trace` | Structured group, repetition, and enum decisions. |

### `ParsedCommand`

```python
ParsedCommand(
    line_number: int,
    raw: str,
    indent: str,
    status: MatchStatus,
    primary_match: PatternMatch,
    alternative_matches: tuple[PatternMatch, ...] = (),
)
```

The generated `kind` field has the value `"command"`
(`kind == "command"`).

Properties:

- `parsed -> True`, including when `status == AMBIGUOUS`;
- `matches -> tuple[PatternMatch, ...]`, containing the primary and all
  alternatives;
- `parameters -> tuple[ParameterValue, ...]`, a shortcut to the primary
  match's parameters.

The primary match is selected by source-pattern and variation order, but
alternatives are not discarded.

## Blank and Error Lines

### `BlankLine`

```python
BlankLine(
    line_number: int,
    raw: str,
    indent: str,
)
```

The generated `kind` field has the value `"blank"` (`kind == "blank"`).
`indent` contains the entire line.

### `ErrorCode`

| Value | When it is used |
| --- | --- |
| `UNKNOWN_COMMAND = "unknown_command"` | No complete route was found and the furthest matching position is `0`. |
| `SYNTAX_ERROR = "syntax_error"` | A prefix was recognized and matching advanced beyond position `0`, but no route completed. |
| `VALIDATION_ERROR = "validation_error"` | The command structure completed, but validators rejected its parameters. |

Enum values are the stable programmatic contract. The human-facing `message`
field is always generated in English and can become more detailed without
introducing a new error code.

### `ExpectedElement`

```python
ExpectedElement(description: str, position: int)
```

The literal or placeholder expected at the furthest reached position.

### `ValidationFailure`

```python
ValidationFailure(
    type_id: str,
    declaration: str,
    raw: str,
    span: TextSpan,
    message: str,
    reason_code: str | None = None,
    expected: str | None = None,
    actual: str | None = None,
)
```

Describes one invalid parameter: its type, declaration, actual value,
position, and human-readable reason.

- `message` is the detailed English validator reason, for example
  `"value must be at most 15"`;
- `reason_code` is the stable machine-readable validator category, such as
  `"above_maximum"`; parser fallback values are `"not_applicable"` and
  `"invalid_value"`;
- `expected` is the expected constraint or form, such as `"<= 15"`;
- `actual` is the actual representation, such as `"16"`.

The `None` defaults preserve compatibility when constructing the dataclass
manually. The runtime factory always sets `reason_code`; `expected` or
`actual` can remain `None` when a custom `ParameterIssue` does not provide
them. For `ParameterStatus.NOT_APPLICABLE`, the parser itself sets
`reason_code="not_applicable"`, places the declaration in `expected`, and
places the raw token in `actual`.

### `ParseError`

```python
ParseError(
    code: ErrorCode,
    message: str,
    position: int | None = None,
    expected: tuple[ExpectedElement, ...] = (),
    failures: tuple[ValidationFailure, ...] = (),
    candidate_patterns: tuple[str, ...] = (),
    candidate_variations: tuple[str, ...] = (),
    suggestions: tuple[str, ...] = (),
)
```

- for syntax/unknown errors, the primary fields are `position` and `expected`;
- `suggestions` contains up to five unique, relevant original patterns for an
  eligible literal-led error;
- for validation errors, the primary fields are `failures`,
  `candidate_patterns`, and `candidate_variations`.

`suggestions` is the structured counterpart of the numbered `Did you mean:`
block in `message`. These are source patterns from JSON, not fabricated
concrete CLI commands. Their order is determined by deterministic ranking and
source order.

Recommendations are not generated in three cases:

1. the pattern or furthest-progressing route starts with an applicable
   parameter;
2. the only potential fallback is bare/root `TEXT<min-max>`;
3. the error code is `VALIDATION_ERROR`, meaning the command shape is already
   known.

Root `TEXT` is not indexed as a suggestion. It still accepts eligible lines
beginning with `!`; for an ordinary unknown line, the parser can suggest other
relevant literal-led patterns.

A `NOT_APPLICABLE` root parameter does not suppress a suggestion by itself:
that token was not recognized as a value of the parameter type and can still
be a misspelled literal keyword.

### Runtime Error Examples

#### `UNKNOWN_COMMAND` with a Recommendation

```python
ParseError(
    code=ErrorCode.UNKNOWN_COMMAND,
    message=(
        "Command 'dispaly clock' was not recognized. Did you mean:\n"
        "  1. display clock\n"
        "Reason: No complete command pattern accepted the first token."
    ),
    position=0,
    suggestions=("display clock",),
)
```

If no similar literal patterns exist, `suggestions == ()`, and the message
ends with `"No similar literal command patterns were found."`. For
`syntax_error`, the exact text is
`"No sufficiently similar literal command patterns were found."`. If an
applicable parameter-led route suppresses the search, the message states that
reason explicitly.

#### `SYNTAX_ERROR`

For `display clok` and the pattern `display clock`:

```python
ParseError(
    code=ErrorCode.SYNTAX_ERROR,
    message=(
        "Command 'display clok' was not recognized. Did you mean:\n"
        "  1. display clock\n"
        "Reason: Parsing stopped at column 9; expected 'clock'."
    ),
    position=8,
    expected=(ExpectedElement(description="'clock'", position=8),),
    suggestions=("display clock",),
)
```

`position` uses a zero-based Python index, while the column in the English
message is presented as one-based. Both values account for the original
indentation. The message lists no more than five expectations; the `expected`
field preserves all of them.

#### `VALIDATION_ERROR`

For `preference 16` and the pattern `preference INTEGER<1-15>`:

```python
ValidationFailure(
    type_id="integer",
    declaration="INTEGER<1-15>",
    raw="16",
    span=TextSpan(start=11, end=13),
    message="value must be at most 15",
    reason_code="above_maximum",
    expected="<= 15",
    actual="16",
)
```

The outer `ParseError.message` names the matched pattern or the number of
candidate patterns and includes up to three reasons. Complete data is always
preserved in `failures`, `candidate_patterns`, and `candidate_variations`;
`suggestions == ()`.

### `ErrorLine`

```python
ErrorLine(
    line_number: int,
    raw: str,
    indent: str,
    error: ParseError,
)
```

The generated `kind` field has the value `"error"` (`kind == "error"`), and
the property is `parsed -> False`.

### `LineResult`

Type alias:

```python
LineResult = BlankLine | ParsedCommand | ErrorLine
```

Use `isinstance()` for safe result handling.

## Complete Configuration Report

### `ParseSummary`

```python
ParseSummary(
    total: int,
    blank: int,
    commands: int,
    ambiguous: int,
    errors: int,
)
```

`commands` includes `unique`, `equivalent`, and `ambiguous`.

### `ParseReport`

```python
ParseReport(
    lines: tuple[LineResult, ...],
    summary: ParseSummary,
)
```

#### `has_errors`

Returns `True` when `summary.errors > 0`.

#### `to_dict`

Returns a JSON-compatible mapping:

- a dataclass becomes an object;
- a tuple/list becomes an array;
- a mapping becomes an object with string keys;
- a set/frozenset becomes a stably sorted array;
- an unknown custom normalized value becomes `str(value)`;
- `None` remains JSON `null`.

`to_dict()` does not modify the original `ParseReport` object.

### `ParseReportFactory`

#### `create`

```python
ParseReportFactory.create(
    lines: tuple[LineResult, ...],
) -> ParseReport
```

Counts all `ParseSummary` fields and creates an immutable `ParseReport`. It
does not parse the input again.

## `JsonValueConverter`

Internal service used by `ParseReport.to_dict()`.

### `convert`

```python
JsonValueConverter.convert(value: Any) -> Any
```

Recursively converts a value according to the rules in the `to_dict` section.
For supported trees, the method produces a result suitable for ordinary
`json.dumps()` and uses a string fallback for plugin objects.

## CLI

After installing the package, the `vrp-parser` command is available. Without
installation, use `PYTHONPATH=src python3 -m vrp_parser`.

### `JsonOutput`

#### `write`

```python
JsonOutput.write(value: Any) -> None
```

Prints UTF-8 JSON to stdout with two-space indentation. `ensure_ascii=False`
keeps Unicode characters readable, while `default=str` handles custom values.

### `_parser`

```python
_parser() -> argparse.ArgumentParser
```

Creates an argument parser with required subcommands:

```text
vrp-parser check-patterns PATTERNS_JSON
vrp-parser parse --patterns PATTERNS_JSON --config CONFIG_FILE
```

### `main`

```python
main(argv: list[str] | None = None) -> int
```

| Code | Meaning |
| --- | --- |
| `0` | The catalog is valid, or the configuration was parsed without line errors. |
| `1` | The configuration was parsed but contains at least one `ErrorLine`. |
| `2` | A caught `OSError`, JSON document-shape error, or pattern compilation error. |

Both input files are read as UTF-8. `UnicodeDecodeError` is not part of the
`except` clause handled by `main()`, so it currently propagates instead of
being converted to JSON with exit code `2`.

For the bundled `data/commands.json`, `check-patterns` prints:

```json
{
  "status": "ok",
  "commands": 7269
}
```

`parse` prints the JSON representation of `ParseReport`.

### `__main__.py`

Calling `python3 -m vrp_parser ...` delegates to `cli.main()` and returns its
exit code through `SystemExit`.

### Package exports

`src/vrp_parser/__init__.py` exports only user-facing parsers, result/error
values, and the parameter-registry extension API. In particular, the old
`VRPParser` facade and `parse_line()` method are not part of the API.

## `manual_test.py`

The manual test harness is not part of the library API, but it demonstrates
the actual formats.

| Entity | Purpose |
| --- | --- |
| `PATTERN_DOCUMENT` | A small catalog intended for manual editing. |
| `main()` | Attempts to create line/configuration parsers from the current scratchpad catalog, processes hard-coded input, and prints the result. An invalid experimental pattern lets the compilation exception propagate. |

Run it with:

```bash
python3 manual_test.py
```

The harness does not process command-line arguments. For a different
scenario, edit the pattern document and the input text passed to `parse()` in
the file itself. A successful exit code is not guaranteed for intentionally
invalid content.
