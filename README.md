# Huawei VRP command parser

The project compiles Huawei VRP command patterns into a reusable command graph
and uses that graph to parse real CLI configuration files. Every successful
result preserves:

- the original input line and indentation;
- the original pattern from JSON;
- the concrete pattern variation that matched;
- validated and normalized parameter values;
- every equally good alternative match.

The code targets Python 3.13 and is organized as small OOP components with
separate responsibilities.

## Full documentation

The detailed Russian-language documentation is split into a short navigation
tree:

- [project guide](docs/PROJECT_GUIDE.md) — data formats, usage, outputs,
  algorithms, architecture, extension points, and limitations;
- [public API and results](docs/reference/public-api-results.md);
- [parameter subsystem](docs/reference/parameters.md);
- [pattern compiler and command graph](docs/reference/patterns-graph.md);
- [runtime matcher](docs/reference/matching.md).

Together, the reference documents describe every production class, function,
property, and private helper.

## Installation

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e .
```

Install development tools with:

```bash
.venv/bin/pip install -e '.[dev]'
```

The editable scratchpad needs no installation:

```bash
python3 manual_test.py
```

`manual_test.py` is intentionally not a stable smoke test: it contains an
editable `PATTERN_DOCUMENT` and hardcoded input in `main()`. Depending on the
experiment, construction may deliberately raise `PatternCompilationError`.

## Pattern document

The runtime catalogue is [data/commands.json](data/commands.json). It has one
required property:

```json
{
  "commands": [
    "#",
    "TEXT<1-4096>",
    "interface STRING<1-63>",
    "preference INTEGER<1-15>",
    "value { INTEGER<1-10> | HEX<1-A> }"
  ]
}
```

Patterns are compiled once when `CommandLineParser` is constructed. Invalid
JSON, malformed reserved parameter declarations, malformed groups, and invalid
repeat bounds are reported at construction time. An arbitrary unregistered
word is treated as a literal unless runtime policy reserves its spelling.

## Python API

```python
from vrp_parser import (
    CommandLineParser,
    ConfigurationParser,
    ErrorLine,
    ParsedCommand,
)

line_parser = CommandLineParser.from_json_file("data/commands.json")

# Exactly one physical line, without a line terminator.
line = line_parser.parse(" interface Vlanif100", line_number=12)

if isinstance(line, ParsedCommand):
    print(line.status)
    print(line.primary_match.original_pattern)
    print(line.primary_match.variation)
    print(line.parameters)
elif isinstance(line, ErrorLine):
    print(line.error.code, line.error.message)

# A complete configuration. One bad line does not stop later lines.
configuration_parser = ConfigurationParser(line_parser)
report = configuration_parser.parse(
    "interface Vlanif100\nunknown command\n"
)
print(report.summary)
print(report.to_dict())
```

`CommandLineParser.parse()` returns `BlankLine`, `ParsedCommand`, or
`ErrorLine`. `ConfigurationParser.parse()` returns a `ParseReport` with one
result per physical input line and a summary. Result dataclasses are frozen;
a custom parameter type may still place its own mutable object in
`ParameterValue.normalized`.

For an in-memory pattern document, construct the line parser directly:

```python
line_parser = CommandLineParser(
    {"commands": ["interface STRING<1-63>"]}
)
```

`CommandLineParser.from_json(source)` accepts JSON text, while
`CommandLineParser.from_json_file(path)` reads it from a file. A
`ConfigurationParser` always receives an already constructed line parser, so
the command catalogue is compiled only once and reused for every line.

### Successful resolution

A successful `ParsedCommand.status` has one of three values:

- `unique`: one best pattern variation matched;
- `equivalent`: several source patterns describe the same accepted variation;
- `ambiguous`: several equally good but different interpretations remain.

All three values mean that parsing succeeded. `primary_match` is the first
matching source pattern in JSON order, `alternative_matches` contains the
remaining matches, and `matches` returns both as one tuple. Every
`PatternMatch` contains `original_pattern`, `variation`, `variation_id`,
captured `parameters`, and a structured selection `trace`.

An ambiguity is deliberately not hidden by an arbitrary tie-break. Callers that
need exactly one semantic interpretation can reject `ambiguous`; callers that
only need syntactic coverage can accept it and retain all alternatives.

### Errors and validation

The matcher first identifies command structure and then validates its
parameters. If a specific command shape is recognized but its value violates a
constraint, the result is `validation_error`; a generic fallback does not hide
that error.

For example, `preference 16` against `preference INTEGER<1-15>` reports the
rejected value, its span, declaration, and validation message. A line whose
command prefix is not recognized produces `unknown_command`. A recognized
prefix that cannot reach a complete pattern produces `syntax_error`.

The exact standalone pattern `TEXT<1-4096>` has a narrow runtime role: it
matches only when the first non-whitespace input character is `!`. Such a line
is returned as an ordinary `ParsedCommand`; the leading `!` is captured in its
text parameter. It is not a catch-all for unknown commands. `#` is also an
ordinary literal command when the `"#"` pattern is present.

## Huawei pattern syntax

| Form | Meaning |
| --- | --- |
| `literal` | One fixed CLI token |
| `{ x \| y }` | Exactly one alternative |
| `[ x \| y ]` | Zero or one alternative |
| `{ x \| y } *` | One or more distinct alternatives, in any input order |
| `[ x \| y ] *` | Zero or more distinct alternatives, in any input order |
| `PARAM &<m-n>` | Repeat a parameter from `m` to `n` times |
| `{ ... } &<m-n>` | Repeat a group from `m` to `n` times |

Groups can be nested. Runtime JSON uses the documentation spelling
`{ x | y } *`. The parser also understands the compact legacy spelling
`{ x | y }*`; in both cases the suffix has identical group semantics.

The meaning of `*` is contextual. Immediately after a closing `}` or `]`, it is
the set operator. Where the parser expects a normal atom, it is a literal CLI
token. For example, this pattern supports both uses:

```text
access-operation { { create | read } * | * }
```

At pattern root, `|` is also a literal token; it separates alternatives only
inside a group. Literals are compared case-insensitively for ASCII letters and
rendered in `variation` in canonical ASCII lowercase. The untouched spelling
always remains available in `original_pattern`.

## Built-in parameter declarations

The default registry recognizes exactly these declarations:

| Declaration | Validation and normalization |
| --- | --- |
| `INTEGER<min-max>` | Signed decimal integer in the inclusive range |
| `HEX<min-max>` | Hexadecimal integer in the inclusive hexadecimal range |
| `STRING<min-max>` | One non-whitespace token with bounded character length |
| `PASSWORDEX<min-max>` | One non-whitespace token with bounded character length |
| `ENUM{x,y,...,}` | One listed value, compared case-insensitively |
| `H-H-H` | Three hexadecimal MAC groups, normalized to `hhhh-hhhh-hhhh` |
| `YYYY/MM/DD` | Valid calendar date |
| `YYYY-MM-DD` | Valid calendar date |
| `MM-DD` | Valid month and day |
| `MM-DD-YYYY` | Valid calendar date |
| `YYYY/MM/DD,HH:MM:SS` | Valid date and time |
| `HH:MM:SS` | Valid time with seconds |
| `<hh:mm>` | Valid hour and minute |
| `TEXT<1-4096>` | The complete remaining text; standalone pattern only |

The default runtime deliberately rejects `X.X.X.X`, `X:X::X:X`, and
`X:X::X:X/M`: IPv4 and IPv6 placeholders are disabled and do not occur in the
shipped catalogue. They are not generic aliases for `address`. Likewise,
`address` itself has no special meaning in the runtime language: unless a
registered declaration recognizer claims it, it is simply a literal keyword.

Composite declarations such as `STRING<1-64>/<0-128>` are also unsupported.
The slash and its second range are not automatically combined with the
preceding `STRING` parameter.

### Adding a parameter type

A parameter type is an object composed from four focused strategies:

1. a declaration recognizer;
2. a CLI value reader;
3. a validator/normalizer;
4. a broad `ParameterFamily` used for deterministic dispatch.

Registering a `ParameterType` is enough to opt into new parameter behavior; the
pattern lexer, AST, graph compiler, and matcher do not need type-specific
branches. The following example intentionally enables the otherwise disabled
`X:X::X:X/M` declaration for one caller-supplied registry:

```python
import ipaddress

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


class IPv6PrefixValidator:
    def probe(
        self,
        raw: str,
        declaration: ParameterDeclaration,
    ) -> ParameterResult:
        del declaration
        try:
            normalized = str(ipaddress.IPv6Interface(raw))
        except ValueError:
            return ParameterResult.failure(
                "invalid_ipv6_prefix",
                "value must be a valid IPv6 address with prefix length",
                actual=raw,
            )
        return ParameterResult.success(normalized)


registry = default_parameter_registry()
registry.register(
    ParameterType(
        type_id="ipv6-prefix",
        family=ParameterFamily.STRUCTURED,
        declaration_recognizer=ExactDeclarationRecognizer("X:X::X:X/M"),
        reader=SingleTokenReader(),
        validator=IPv6PrefixValidator(),
    )
)

line_parser = CommandLineParser(
    {"commands": ["peer X:X::X:X/M"]},
    parameter_types=registry,
)
```

The parser clones and freezes the supplied registry during construction. Build
and register all custom types before passing it to `CommandLineParser`.

## Command line

The installed package exposes two commands:

```bash
vrp-parser check-patterns data/commands.json
vrp-parser parse --patterns data/commands.json --config running.cfg
```

`check-patterns` compiles the JSON and prints its command count.
`parse` prints a JSON representation of `ParseReport`; it exits with status `1`
when the configuration contains line errors and `2` for caught filesystem,
pattern-document, or pattern-compilation errors. Files are expected to be
UTF-8; a `UnicodeDecodeError` currently propagates to the caller.

## Architecture

```text
                         +----------------+
pattern JSON ----------> | patterns       |
                         | lexer + AST     |
                         +-------+--------+
                                 |
                                 v
                         +----------------+
                         | graph          |
                         | compiler/model |
                         +-------+--------+
                                 |
configuration text              v
        |                +----------------+
        +--------------> | matching       |
                         | traversal +    |
                         | resolution     |
                         +-------+--------+
                                 |
                                 v
                         immutable results

parameters: declaration recognition, reading, validation, normalization
api:        reusable line/configuration facades
cli:        JSON input and output only
```

- `patterns` owns the Huawei pattern language and its immutable AST.
- `graph` compiles patterns into a shared-prefix graph while retaining source
  provenance.
- `matching` walks that graph, validates candidates, and resolves successful
  alternatives without hiding ambiguity.
- `parameters` contains the extensible parameter type registry and focused
  strategies.

This separation keeps the control flow readable for junior developers and lets
new parameter syntaxes be added without rewriting the command grammar or graph
matcher.
