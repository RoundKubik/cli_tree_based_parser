# Pattern Language, Compilation, and the Shared Command Graph

This document describes the internal path of a command from a string in
`commands.json` to an immutable shared graph. It covers these modules:

- `vrp_parser.compiler`;
- `vrp_parser.errors`;
- `vrp_parser.patterns`;
- `vrp_parser.graph`.

Matching a concrete CLI line is performed by the next project layer. This
document focuses on pattern syntax, the AST, validation of project-level
constraints, route linearization, and preservation of provenance—the
relationship between a route and its original pattern.

## High-Level Flow

```text
tuple[str, ...]
    │
    ▼
PatternLexer ──► tuple[Token, ...]
    │
    ▼
PatternParser ──► Sequence (AST)
    │
    ▼
RuntimePatternPolicy
    │
    ▼
PatternSource
    │
    ▼
RouteExpander ──► LinearRoute
    │
    ▼
GraphStepFactory ──► GraphStep with a semantic key
    │
    ▼
CommandGraphBuilder ──► CommandGraph
```

`PatternCompiler` coordinates this entire process. It processes every source
string, accumulates all errors, and builds the graph only when every pattern
is valid.

## Input Document Format

The public parser accepts a JSON-compatible object:

```json
{
  "commands": [
    "#",
    "TEXT<1-4096>",
    "description TEXT<1-80>",
    "interface STRING<1-63>",
    "peer X.X.X.X",
    "peer X:X::X:X",
    "network X:X::X:X/M",
    "interface { STRING<1-63> | ENUM{Eth-trunk,Vlanif,Vbdif} STRING<1-63> }"
  ]
}
```

The public API validates the shape of the JSON document before it calls
`PatternCompiler`. `PatternCompiler.compile()` therefore receives an already
validated `tuple[str, ...]`.

The main document requirements are:

- the root value is an object;
- `commands` is a non-empty array;
- every element is a non-empty string;
- element order is significant: it determines `pattern_index` and the
  representative result order when multiple patterns match;
- duplicate strings are allowed and remain separate sources.

A document-shape violation produces `PatternDocumentError`. Syntax and
semantic errors inside pattern strings are collected into
`PatternCompilationError`.

## Pattern Grammar

The following is a simplified EBNF for the implemented frontend:

```ebnf
pattern        = root_item, { root_item } ;
root_item      = atom, [ repeat ] ;
atom           = literal
               | parameter
               | group
               | "*"
               | "|" ;             (* "|" is a literal only at the root *)

group          = required_group | optional_group ;
required_group = "{", alternative, { "|", alternative }, "}", [ "*" ] ;
optional_group = "[", alternative, { "|", alternative }, "]", [ "*" ] ;
alternative    = group_item, { group_item } ;
group_item     = group_atom, [ repeat ] ;
group_atom     = literal | parameter | group | "*" ;

repeat         = "&<", unsigned_integer, "-", unsigned_integer, ">" ;
```

The lexer ignores whitespace between elements. Consequently, `}*` and `} *`
are technically recognized in the same way. The canonical notation in
`commands.json` includes a space: `} *` and `] *`.

### Group Semantics

| Syntax | `GroupMode` | Meaning |
|---|---|---|
| `{ x \| y }` | `REQUIRED_ONE` | exactly one alternative is required |
| `[ x \| y ]` | `OPTIONAL_ONE` | one alternative is selected, or the group is omitted |
| `{ x \| y } *` | `REQUIRED_SET` | one or more alternatives, up to all of them, are selected without reusing a branch |
| `[ x \| y ] *` | `OPTIONAL_SET` | zero or more alternatives, up to all of them, are selected without reusing a branch |

For set groups, the order of selected alternatives in the CLI does not have
to match their order in the pattern. These groups remain symbolic graph nodes
and are parsed by the matcher layer at runtime.

### Context-Sensitive `*`

`*` is a set operator only when it immediately follows a group's closing
delimiter. Whitespace before it is insignificant:

```text
{ create | read } *
[ fast | safe ] *
```

At a position where the parser expects a new atom, `*` is an ordinary CLI
literal:

```text
access-operation { { create | read } * | * }
                                             └─ literal "*"
```

Thus, in the pattern above, the inner `*` after `}` changes the mode of the
inner group, while the final `*` specifies a literal CLI token.

### Repetition with `&<min-max>`

The operator repeats only a parameter or a group:

```text
community STRING<3-11> &<1-200>
path { left | right } &<1-3>
```

Constraints:

- bounds are non-negative decimal integers;
- `maximum` must be greater than or equal to `minimum`;
- whitespace between `&` and `<` is not allowed;
- whitespace inside the angle brackets is allowed;
- a literal cannot be repeated;
- a second consecutive repeat is not supported.

Examples:

```text
STRING<1-10>&<0-3>       # valid
STRING<1-10>&< 0 - 3 >   # valid
literal&<1-2>            # error
STRING<1-10>&<3-2>       # invalid bounds
cmd & <1-2>              # malformed repeat
```

### `|` at the Root and Inside a Group

At the top level, `|` is a literal CLI token:

```text
display | include STRING<1-20>
```

Inside `{ ... }` and `[ ... ]`, it always separates alternatives. Empty
alternatives are prohibited:

```text
[ left | ]   # error
```

### Parameters

`PatternLexer` does not contain a list of parameter types. The supplied
`ParameterRecognizer` attempts to recognize a declaration exactly at the
current position. This allows a new placeholder to be added through the
registry without changing the lexer or parser.

By default, runtime policy expects the following built-in spellings to be
recognized correctly:

```text
HEX<min-max>
STRING<min-max>
INTEGER<min-max>
ENUM{value1,value2,...}
PASSWORDEX<min-max>
TEXT<min-max>
YYYY/MM/DD
YYYY-MM-DD
MM-DD
MM-DD-YYYY
YYYY/MM/DD,HH:MM:SS
HH:MM:SS
<hh:mm>
H-H-H
X.X.X.X
X:X::X:X
X:X::X:X/M
```

`TEXT<min-max>` is a bounded remainder parameter. It may follow a keyword,
as in `description TEXT<1-80>`, in which case it accepts all remaining text.
It must terminate every possible route and cannot be repeated. If `TEXT` is
matched at position `0`—on a bare/root route with no previously matched
keyword—the matcher permits it only for CLI lines whose first character
after indentation is `!`. This runtime rule prevents such a remainder branch
from consuming unknown commands, while the frontend still permits terminal
embedded `TEXT`.

The three IP placeholders are built-in exact declarations. The registry
recognizes `X:X::X:X/M` separately from `X:X::X:X` by choosing the longest
match. Runtime policy reserves all three spellings: a malformed suffix such
as `X.X.X.X/suffix` or `X:X::X:X/M-extra` does not become a sequence of
literals and instead produces `PatternLanguageError` during compilation.

## AST Example

Source pattern:

```text
route [ vpn STRING<1-31> ] { preference INTEGER<1-255> | * }
```

Simplified AST representation:

```text
Sequence
├── Literal("route")
├── Group(mode=OPTIONAL_ONE)
│   └── Sequence
│       ├── Literal("vpn")
│       └── Parameter(source="STRING<1-31>")
└── Group(mode=REQUIRED_ONE)
    ├── Sequence
    │   ├── Literal("preference")
    │   └── Parameter(source="INTEGER<1-255>")
    └── Sequence
        └── Literal("*")
```

Every node contains `SourceSpan(start, end)`, a half-open `[start, end)`
character range in the source string. Whitespace separating nodes is
generally not included in their spans.

# `vrp_parser.patterns.tokens`

## `SourceSpan`

```python
@dataclass(frozen=True, slots=True)
class SourceSpan:
    start: int
    end: int
```

An immutable half-open range in a pattern string.

- `start` is inclusive;
- `end` is exclusive;
- the valid invariant is `0 <= start <= end`;
- violating the invariant in `__post_init__()` raises `ValueError`.

An empty range is valid. For example, the `END` token receives
`SourceSpan(len(source), len(source))`.

### `SourceSpan.covering(first, last)`

Returns `SourceSpan(first.start, last.end)`. The method assumes that `first`
does not lie to the right of `last`. It neither sorts its arguments nor
computes `min`/`max`; the caller is responsible for their correct order.

Input:

- `first: SourceSpan`;
- `last: SourceSpan`.

Result: a new `SourceSpan`.

Exception: `ValueError` if the resulting bounds violate the invariant.

## `TokenKind`

A `StrEnum` defining the token kinds:

- `PARAMETER` — a parameter declaration recognized by the registry;
- `LITERAL` — a fixed CLI token;
- `LEFT_BRACE`, `RIGHT_BRACE` — `{`, `}`;
- `LEFT_BRACKET`, `RIGHT_BRACKET` — `[`, `]`;
- `PIPE` — `|`;
- `STAR` — `*`;
- `REPEAT` — a complete `&<min-max>` operator;
- `END` — the synthetic end of the input.

Because this is a `StrEnum`, its string values are `"parameter"`,
`"literal"`, `"left_brace"`, and so on.

## `Token`

```python
@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    text: str
    span: SourceSpan
    parameter: object | None = None
    repeat_bounds: tuple[int, int] | None = None
```

A lexeme with its source text, position, and optional data.

- `parameter` is populated only for `PARAMETER`;
- `repeat_bounds` is populated only for `REPEAT`;
- for `END`, `text` is an empty string.

`__post_init__()` validates consistency between `kind` and the metadata:

- `PARAMETER` without `parameter` → `ValueError`;
- any other kind with `parameter` → `ValueError`;
- `REPEAT` without `repeat_bounds` → `ValueError`;
- any other kind with `repeat_bounds` → `ValueError`.

The class does not verify that the length of `text` equals the length of
`span`; the lexer maintains that relationship.

# `vrp_parser.patterns.lexer`

## `RecognizedParameter`

A protocol describing the minimal result returned by a registry-like
recognizer.

### `end`

A read-only property of type `int`. It is the exclusive end position of the
declaration in the source pattern.

The lexer also looks for a `declaration` attribute on the result:

- if the attribute exists, its value is stored in `Token.parameter`;
- otherwise, the result object itself is stored.

This supports both production `ParameterDeclaration` objects and small
adapters around custom recognizers.

## `ParameterRecognizer`

The dependency protocol used by `PatternLexer` and `PatternParser`.

### `recognize(source, position)`

Input:

- `source: str` — the complete source pattern;
- `position: int` — the exact position at which a declaration may start.

Result:

- an object compatible with `RecognizedParameter` if a declaration begins
  exactly at `position`;
- `None` if no parameter begins at that position.

A recognizer must not skip characters to the left and must return
`end > position`.

## `PatternLexer`

Converts a pattern string into tokens. It understands the language's
structural characters but does not know the concrete parameter types.

### `PatternLexer.__init__(parameter_recognizer)`

Accepts an object implementing `ParameterRecognizer`. The object is retained
and used on every call to `tokenize()`.

### `PatternLexer.tokenize(source)`

Input: one complete pattern string, `source: str`.

Result: `tuple[Token, ...]`, always ending with an `END` token.

Recognition order at each non-empty position:

1. a parameter through the registry;
2. a repeat through the regular expression;
3. one structural character from `{ } [ ] | *`;
4. a literal up to whitespace, a structural character, or `&`.

Parameter recognition occurs first, so internal `{`, `}`, `,`, and other
characters in `ENUM{...}` do not become structural tokens.

Whitespace does not create tokens. Every other non-whitespace sequence
becomes a `LITERAL`.

Exceptions:

- `TypeError` if `source` is not a string;
- `PatternLanguageError("malformed repeat operator", ...)` if an `&` does
  not begin a valid repeat;
- `RuntimeError` if the parameter recognizer violates the position contract.

### `PatternLexer._literal_end(source, position)`

A class method that finds the end of a literal. It moves right until it
reaches:

- whitespace;
- one of `{ } [ ] | *`;
- the `&` character;
- the end of the string.

Returns the exclusive end position. If the starting character is `&`, it
returns the original `position`; `tokenize()` interprets this as a malformed
repeat.

### `PatternLexer._validate_parameter_end(source, start, end)`

Validates the external recognizer's response:

- `end <= start` → `RuntimeError("parameter recognizer did not advance")`;
- `end > len(source)` →
  `RuntimeError("parameter recognizer advanced beyond the source")`.

The method returns nothing. Validation of the boundary between a declaration
and the following element belongs in the declaration recognizer itself.

Internal constants:

- `_SYMBOLS` maps single structural characters to `TokenKind`;
- `_REPEAT` recognizes decimal bounds and optional whitespace inside
  `&<...>`.

# `vrp_parser.patterns.ast`

All AST values are frozen dataclasses with slots. They cannot be modified
after construction.

## `GroupMode`

A `StrEnum` with four modes:

- `OPTIONAL_ONE = "optional_one"`;
- `REQUIRED_ONE = "required_one"`;
- `OPTIONAL_SET = "optional_set"`;
- `REQUIRED_SET = "required_set"`.

It simultaneously describes whether the group is required, how many
alternatives may be selected, and whether their order is significant.

## `Sequence`

```python
@dataclass(frozen=True, slots=True)
class Sequence:
    items: tuple[Node, ...]
    span: SourceSpan
```

A left-to-right ordered sequence of AST nodes. The root of every successfully
parsed pattern is a `Sequence`. Within a group, each alternative is also
represented by a separate `Sequence`.

The parser prohibits an empty root sequence. During parsing, it may
temporarily create an empty sequence so that it can report a precise error
for an empty alternative.

## `Literal`

```python
@dataclass(frozen=True, slots=True)
class Literal:
    value: str
    span: SourceSpan
```

A fixed CLI token. `value` preserves the original case. During graph
construction, ASCII case is excluded from the semantic key, so keywords are
matched without regard to ASCII case.

## `Parameter`

```python
@dataclass(frozen=True, slots=True)
class Parameter:
    declaration: object
    source: str
    span: SourceSpan
```

A parameter placeholder:

- `declaration` is the metadata returned by the recognizer;
- `source` is the exact fragment of the source pattern;
- `span` is the position of that fragment.

The neutral frontend permits an `object`, but production
`RuntimePatternPolicy` and `GraphStepFactory` require an instance of
`ParameterDeclaration`. Otherwise, they raise `TypeError`.

## `Group`

```python
@dataclass(frozen=True, slots=True)
class Group:
    alternatives: tuple[Sequence, ...]
    mode: GroupMode
    span: SourceSpan
```

A group of alternatives. The parser guarantees at least one non-empty
alternative. `span` includes both delimiters and, for a set group, the
trailing `*`.

## `Repeat`

```python
@dataclass(frozen=True, slots=True)
class Repeat:
    atom: Parameter | Group
    minimum: int
    maximum: int
    span: SourceSpan
```

A bounded repetition of a parameter or group. `span` covers the atom and the
`&<min-max>` operator. The parser guarantees that:

- the atom is a `Parameter` or `Group`;
- `minimum <= maximum`.

## `Node`

Type alias:

```python
type Node = Literal | Parameter | Group | Repeat
```

`Sequence` is intentionally not part of `Node`: it is a container for a root
or alternative, not a separate command edge.

# `vrp_parser.patterns.errors`

## `PatternLanguageError`

A `ValueError` subclass representing one pattern-language error.

### `PatternLanguageError.__init__(message, source, span)`

Stores these public attributes:

- `message: str` — a description without coordinates;
- `source: str` — the complete source pattern;
- `span: SourceSpan` — the exact problematic range.

The exception's standard string representation is:

```text
<message> at characters <start>:<end>
```

The lexer, parser, and runtime policy raise this exception.

# `vrp_parser.patterns.parser`

## `PatternParser`

A recursive-descent parser that converts a token stream into an immutable
AST.

An instance stores the current `_source`, `_tokens`, and `_position`, so one
object is not intended for concurrent `parse()` calls from multiple threads.
`PatternCompiler` uses it sequentially.

### `PatternParser.__init__(parameter_recognizer)`

Creates an internal `PatternLexer` with the supplied recognizer and
initializes empty parser state.

### `PatternParser.parse(source)`

Input: a complete pattern string.

Algorithm:

1. call the lexer;
2. reset the position to the first token;
3. parse the root sequence through `END`;
4. require `END`;
5. prohibit an empty root.

At the root level, `pipe_is_literal=True`, so `|` becomes `Literal("|")`.

Result: the root `Sequence`.

Exceptions:

- exceptions raised by `PatternLexer.tokenize()`;
- `PatternLanguageError` for a grammar error.

### `PatternParser._parse_sequence(*, stop, pipe_is_literal)`

Collects a sequence until it reaches a token in `stop`. For each element, it
first calls `_parse_atom()` and then `_parse_repeat()`.

When `pipe_is_literal=False`, it also ends the alternative before `PIPE`
without consuming that token.

Input:

- `stop: frozenset[TokenKind]`;
- `pipe_is_literal: bool`.

Result: a `Sequence`, which may be temporarily empty. Its span starts at the
current token and ends at the end of the last element.

### `PatternParser._parse_atom(*, pipe_is_literal)`

Consumes one token and creates a `Node`:

- `PARAMETER` → `Parameter`;
- `LITERAL` → `Literal`;
- `STAR` → `Literal("*")`;
- a root-level `PIPE` → `Literal("|")`;
- an opening delimiter → `_parse_group()`.

Calls `_fail()` for:

- an unexpected closing delimiter;
- a repeat without a preceding atom;
- `PIPE` where it cannot be a literal;
- `END` or another unexpected token.

### `PatternParser._parse_group(opening)`

Parses a required `{...}` or optional `[...]` group.

Algorithm:

1. determine the expected closing delimiter;
2. parse one or more non-empty alternatives;
3. consume the closing delimiter;
4. if the next token is `STAR`, consume it and select a set mode;
5. otherwise, select a one mode.

Result: a `Group`. Its `span` ends after `*` for a set group or after the
closing delimiter for a one group.

Exceptions: `PatternLanguageError` for an empty alternative, a missing or
incorrect closing delimiter, or another nested error.

### `PatternParser._parse_repeat(atom)`

If the current token is not `REPEAT`, returns the original `atom` unchanged.

If a repeat is present:

1. extract `(minimum, maximum)`;
2. verify `maximum >= minimum`;
3. verify that the atom is a `Parameter` or `Group`;
4. return a `Repeat`.

A `Literal` before a repeat produces `PatternLanguageError`. The method
parses at most one repeat for an atom.

### `PatternParser._current`

A read-only private property. Returns the `Token` at the current `_position`.
The parser always keeps an `END` element at the end of `_tokens`, so the
index remains valid during correct internal operation.

### `PatternParser._advance()`

Returns the current token and advances the position unless the token is
`END`. At `END`, the position does not change, preventing access beyond the
tuple.

### `PatternParser._expect(kind)`

Checks the current token's kind.

- On a match, consumes and returns the token through `_advance()`.
- On a mismatch, calls `_fail()` with the expected kind, actual text, and
  span of the actual token.

### `PatternParser._fail(message, span)`

Always raises `PatternLanguageError`, adding the stored `_source`. Its return
type is `NoReturn`.

# `vrp_parser.patterns.policy`

## `RuntimePatternPolicy`

A layer of project constraints over the neutral grammar. The parser itself
cannot distinguish an unknown placeholder from an ordinary literal. The
policy prevents a typo such as `STRING<1-x>` or a malformed exact placeholder
such as `X.X.X.X/suffix` from silently becoming a literal.

Internal lists:

- `_DECLARATION_PREFIXES` contains known beginnings of parameterized
  declarations;
- `_EXACT_PLACEHOLDERS` contains built-in declarations with fixed spellings,
  including an IPv4 address, IPv6 address, and IPv6 prefix.

### `RuntimePatternPolicy.validate(ast, source)`

Input:

- `ast: Sequence` — an already constructed AST;
- `source: str` — the same source string.

Algorithm:

1. recursively collect every `Parameter`;
2. locate every occurrence of a known declaration prefix in the source;
3. verify that its range is covered by the span of an actual `Parameter`;
4. validate exact placeholders in the same way;
5. verify that every `TEXT` is the final element of every possible route and
   is not beneath a repeat.

Returns nothing.

Exceptions:

- `PatternLanguageError` if a known spelling remains a literal, is
  malformed, or if `TEXT` does not terminate a route or is repeated;
- `TypeError` if a production AST contains an unknown declaration type.

An IP placeholder is valid only when a registered exact recognizer actually
converted its range into a `Parameter`. Policy validation therefore also
catches unsupported suffixes after a reserved spelling.

### `RuntimePatternPolicy._claimed(parameters, start, end)`

Returns `True` if a `Parameter` exists whose span fully covers the
`[start, end)` range.

This is an ownership check for the source fragment. A mere intersection of
ranges is insufficient.

### `RuntimePatternPolicy._validate_text_sequence(sequence, source, *, followed)`

Recursively validates the placement of bounded remainder parameters
`TEXT<min-max>`.

- `TEXT` is valid as the last element of the root sequence:
  `TEXT<1-4096>`;
- `TEXT` is valid after a keyword: `description TEXT<1-80>`;
- when a continuation exists after the current sequence, `followed=True`;
- a `TEXT` followed by a literal, parameter, or an outer group continuation
  is rejected with `PatternLanguageError`;
- for an ordinary `Group`, the method validates every alternative while
  accounting for whether anything follows the group itself.

This constraint follows from reader semantics: `TEXT` consumes the entire
remainder of the line, so no subsequent element can be matched.

### `RuntimePatternPolicy._validate_repeated_text(repeat, source)`

Prohibits a directly repeated `TEXT<min-max>` because the first remainder
already consumes all available input. For a repeated group, recursively
validates every alternative as though it had a continuation: another
repetition may need to begin after the first one.

### `RuntimePatternPolicy._is_text(parameter)`

Returns `True` when `parameter.declaration.type_id == "text"`. Uses
`_declaration()` to enforce the production invariant.

### `RuntimePatternPolicy._parameters(sequence)`

A generator that recursively visits:

- direct `Parameter` nodes;
- every alternative of a `Group`;
- `Repeat.atom` when it is a `Parameter`;
- every alternative of a repeated `Group`.

It skips `Literal`. The result is an `Iterator[Parameter]`.

### `RuntimePatternPolicy._declaration(parameter)`

Validates the production invariant: `parameter.declaration` must be a
`ParameterDeclaration`.

Result: a typed `ParameterDeclaration`.

Exception: `TypeError("parameter AST contains an unknown declaration")`.

### `RuntimePatternPolicy._fail(message, source, start, end)`

Creates `SourceSpan(start, end)` and raises `PatternLanguageError`. It does
not return normally.

# `vrp_parser.patterns.__init__`

The package facade exports:

```text
Group, GroupMode, Literal, Node, Parameter,
ParameterRecognizer, PatternLanguageError, PatternLexer, PatternParser,
RecognizedParameter, Repeat, RuntimePatternPolicy, Sequence,
SourceSpan, Token, TokenKind
```

This is the stable import point for frontend entities within the project:

```python
from vrp_parser.patterns import PatternParser, Sequence
```

# `vrp_parser.errors`

## `PatternDocumentError`

A `ValueError` subclass. It signals an invalid shape for the input
JSON-compatible document, rather than invalid syntax in one pattern.

Example causes:

- `commands` is missing;
- `commands` is not an array of strings;
- the list is empty;
- an element is empty or is not a string.

The class adds no fields or methods of its own.

## `PatternIssue`

```python
@dataclass(frozen=True, slots=True)
class PatternIssue:
    pattern_index: int
    pattern: str
    message: str
    span: SourceSpan
```

One normalized compilation error:

- `pattern_index` is the original position in `commands`;
- `pattern` is the complete string;
- `message` is the source exception's message;
- `span` is the problematic range.

The object is immutable. It performs no additional value validation: the
compiler is responsible for a correct index and span.

## `PatternCompilationError`

A `ValueError` subclass containing all errors from one compilation pass.

### `PatternCompilationError.__init__(issues)`

Input: a non-empty `tuple[PatternIssue, ...]`.

Stores the tuple in the public `issues` attribute. Its order corresponds to
the order of patterns in the input `commands`.

The exception text shows the first problem:

```text
pattern #7: expected right_brace, found ''
```

When there are multiple problems, it adds a suffix:

```text
 (+2 more)
```

If an empty tuple is supplied, the constructor raises an ordinary
`ValueError`, because an exception without issues would violate its
invariant.

# `vrp_parser.compiler`

## `PatternCompiler`

An application service that converts a collection of source strings into one
`CommandGraph`. It is responsible for complete error collection and source
identity, while delegating grammar, policy, and graph construction to
separate objects.

### `PatternCompiler.__init__(parameter_types, graph_builder=None, pattern_policy=None)`

Dependencies:

- `parameter_types: ParameterTypeRegistry` — the registry of declaration
  recognizers;
- `graph_builder: CommandGraphBuilder | None` — an optional replacement
  builder; by default, a `CommandGraphBuilder()` is created;
- `pattern_policy: RuntimePatternPolicy | None` — an optional policy; by
  default, a `RuntimePatternPolicy()` is created.

The constructor creates one `PatternParser(parameter_types)`. The registry
must remain consistent throughout compilation.

### `PatternCompiler.compile(commands)`

Input: `commands: tuple[str, ...]`, already validated by the document layer.

For each element in source order:

1. build the AST;
2. validate runtime policy;
3. on an error, add a `PatternIssue` and continue with the next string;
4. on success, create a `PatternSource`.

The compiler catches:

- `PatternLanguageError`;
- `ParameterDeclarationError`;
- `ParameterRegistryError`.

It does not stop at the first error. If the pass collects at least one issue,
it raises `PatternCompilationError(tuple(issues))` and returns no graph.
Unexpected programming errors are not suppressed.

When there are no errors, it calls:

```python
self._graph_builder.build(tuple(patterns))
```

Result: an immutable `CommandGraph`.

### `PatternCompiler._pattern_id(original, occurrence)`

A static method that creates a stable source ID:

```text
pattern:<first 20 hexadecimal characters of the UTF-8 string's sha256>:<occurrence>
```

For example:

```text
pattern:7d89...e410:0
```

`occurrence` is the zero-based ordinal of an exact duplicate of this string
among successfully processed sources. Therefore:

- inserting a different pattern does not change the ID;
- changing case or whitespace changes the hash;
- exact duplicates receive different IDs;
- reordering the document changes `pattern_index`, but not the content part
  of the ID.

Result: `str`.

### `PatternCompiler._issue(index, pattern, error)`

A static adapter from an arbitrary expected frontend or registry exception to
`PatternIssue`.

Span selection:

1. use `error.span` if it is a `SourceSpan`;
2. otherwise, take integer attributes `error.start` and `error.end`;
3. replace an invalid or missing `start` with `0`;
4. replace an invalid or missing `end` with `len(pattern)`.

The message comes from `error.message`, or from `str(error)` when that
attribute is absent.

Result: `PatternIssue`.

# Route Linearization

Expanding ordinary choice groups during compilation is useful because their
literal and parameter prefixes can then merge with prefixes from other
patterns. Fully expanding set groups or repeats would have factorial or
exponential complexity, so they remain single symbolic steps.

Example:

```text
show { interface | version }
```

produces two `LinearRoute` objects:

```text
("show", "interface")
("show", "version")
```

The pattern:

```text
select { red | green | blue } *
```

produces one route:

```text
("select", Group(REQUIRED_SET, ...))
```

# `vrp_parser.graph.routes`

## `LinearRoute`

```python
@dataclass(frozen=True, slots=True)
class LinearRoute:
    steps: tuple[Node, ...]
    trace: tuple[VariationStep, ...] = ()
```

One linear variation of the source AST:

- `steps` is the sequence of future edges;
- `trace` contains statically known choice and optional decisions.

`VariationStep` is imported from the result model. This layer uses:

- `kind="choice"` with `selected=(alternative_index,)`;
- `kind="optional"` with an empty `selected` when omitted;
- `kind="optional"` with an index when a branch is selected.

## `RouteLimitExceeded`

An internal `RuntimeError`. It signals that expansion of one pattern exceeded
the permitted number of variations.

This is not a user-facing compilation error: the public `expand()` method
catches it and returns the original sequence as one symbolic route.

## `RouteExpander`

Expands `REQUIRED_ONE` and `OPTIONAL_ONE` in a controlled manner while
retaining complex constructs as symbolic nodes.

### `RouteExpander.__init__(maximum_routes=512)`

`maximum_routes` is the maximum number of linear variations for one pattern.

- The value must be positive.
- `maximum_routes < 1` raises `ValueError`.

The limit protects both an individual group and the Cartesian product of
multiple groups in a sequence.

### `RouteExpander.expand(sequence)`

Input: the root `Sequence`.

Normal result: a tuple of expanded `LinearRoute` objects.

If `RouteLimitExceeded` occurs at any level, the method abandons all partial
expansion and returns:

```python
(LinearRoute(sequence.items),)
```

This is important: the fallback preserves correctness of the entire source
pattern and does not combine partially static provenance with symbolic
provenance.

### `RouteExpander._sequence(sequence, *, path)`

Starts with one empty route. For each node:

1. obtain its routes through `_node()`;
2. multiply the accumulated routes by the node's alternatives through
   `_product()`.

`path` identifies an AST location for the trace. The root begins with
`"root"`, and the element index is appended after a dot: `"root.0"`,
`"root.1"`.

Result: `tuple[LinearRoute, ...]`.

### `RouteExpander._node(node, *, path)`

Behavior by node type:

- literal, parameter, repeat → one route containing the node itself;
- `OPTIONAL_SET`, `REQUIRED_SET` → one route containing a symbolic group;
- `REQUIRED_ONE` → routes from all alternatives;
- `OPTIONAL_ONE` → first an empty omitted route, followed by routes from all
  alternatives.

For a nested alternative, its index is appended to the path before recursive
processing. After the nested branch's trace, the method appends the
`VariationStep` that selects the current group.

Example for the second element at the root:

```text
[ brief | detail ]
```

Trace for omission:

```python
VariationStep(kind="optional", path="root.1", selected=())
```

Trace for selecting `detail`:

```python
VariationStep(kind="optional", path="root.1", selected=(1,))
```

### `RouteExpander._product(left, right)`

Builds the Cartesian product of routes:

- steps are joined by concatenation;
- traces are joined by concatenation;
- order is deterministic: `left` order first, with `right` order nested
  inside it.

Before materialization, it validates `len(left) * len(right)` through
`_check_limit()`.

Result: `tuple[LinearRoute, ...]`.

### `RouteExpander._check_limit(size)`

If `size > self._maximum_routes`, raises `RouteLimitExceeded`. Otherwise,
returns nothing.

# Semantic Steps

AST dataclasses cannot be compared directly when merging prefixes:
`SourceSpan` and literal case belong to the source representation, not to the
meaning of a step. `GraphStepFactory` creates a separate hashable key without
spans.

# `vrp_parser.graph.steps`

## `GraphStepFactory`

Converts an AST node into `GraphStep(expression, key)`.

### `GraphStepFactory.create(expression)`

Input: one `Node`.

Result:

```python
GraphStep(
    expression=expression,
    key=self._node_key(expression),
)
```

`expression` preserves the readable AST from the first source encountered;
`key` is used for merging.

### `GraphStepFactory._node_key(node)`

Builds a recursive tuple:

```python
Literal:
("literal", ascii_lower(value))

Parameter:
(
    "parameter",
    declaration.type_id,
    declaration.source,
    declaration.minimum,
    declaration.maximum,
    declaration.choices,
    declaration.metadata,
)

Group:
(
    "group",
    mode.value,
    tuple(sequence_key for each alternative),
)

Repeat:
(
    "repeat",
    key of the repeated atom,
    minimum,
    maximum,
)
```

Consequences:

- spans do not affect merging;
- ASCII case of a literal does not affect merging;
- group alternative order does affect merging;
- group mode and repeat bounds do affect merging;
- the exact `declaration.source` is part of the key;
- metadata must follow the typed `tuple[tuple[str, str], ...]` contract and
  contain hashable values; `ParameterDeclaration` itself does not validate
  hashability at runtime.

An unknown node type produces `TypeError`.

### `GraphStepFactory._sequence_key(sequence)`

Returns a tuple containing the key for every `Sequence` element in source
order. It is used recursively for group alternatives.

### `GraphStepFactory._declaration(node)`

Verifies that `Parameter.declaration` is a production
`ParameterDeclaration` and returns it.

Exception: `TypeError("parameter AST contains an unknown declaration")`.

# Shared Graph Model

The graph is a trie-like structure with shared prefixes. Unlike an ordinary
trie, every edge stores the set of `route_ids` that may traverse it.

This solves the false-crossover problem:

```text
command one left
command two right
```

After merging, shared parts may appear next to one another in the same graph,
but the first pattern's route must not finish through the second pattern's
edge. The runtime matcher carries the set of active route IDs and intersects
it with `edge.route_ids` at each step. At the end, it accepts only IDs in
`node.accepting_routes`.

# `vrp_parser.graph.model`

## `ascii_lower(value)`

A module-level case-folding function for CLI keywords.

Input: `value: str`.

Algorithm: `str.translate()` replaces only ASCII `A-Z` with `a-z`.
Non-ASCII characters remain unchanged.

Result: `str`.

This is intentionally narrower than `str.lower()` or `str.casefold()`: the
network CLI's behavior for ASCII keywords is independent of Unicode case
rules.

## `PatternSource`

```python
@dataclass(frozen=True, slots=True)
class PatternSource:
    pattern_id: str
    index: int
    original: str
    ast: Sequence
```

One entry from `commands` after successful frontend processing:

- `pattern_id` is a content-based ID with a duplicate ordinal;
- `index` is the position in the source JSON array;
- `original` is the unnormalized source string;
- `ast` is the immutable root.

## `GraphStep`

```python
@dataclass(frozen=True, slots=True)
class GraphStep:
    expression: Node
    key: tuple[object, ...]
```

The semantic expression for an edge:

- `expression` is needed by the runtime matcher;
- `key` is needed by the builder to merge equivalent steps.

## `RouteSource`

```python
@dataclass(frozen=True, slots=True)
class RouteSource:
    route_id: int
    pattern: PatternSource
    static_trace: tuple[VariationStep, ...]
```

Provenance for one linear route:

- the global integer `route_id`;
- a reference to the complete source `PatternSource`;
- decisions already made by `RouteExpander`.

Even exact duplicate patterns have distinct `PatternSource` objects and
routes.

## `CommandEdge`

```python
@dataclass(frozen=True, slots=True)
class CommandEdge:
    step: GraphStep
    target: CommandNode
    route_ids: frozenset[int]
```

A directed edge:

- `step` describes what must be recognized;
- `target` is the next node;
- `route_ids` are the routes that own this transition.

The builder never creates an empty `route_ids`.

## `CommandNode`

```python
@dataclass(frozen=True, slots=True)
class CommandNode:
    literal_edges: Mapping[str, CommandEdge]
    expression_edges: tuple[CommandEdge, ...]
    accepting_routes: frozenset[int]
```

One node in the shared-prefix graph:

- `literal_edges` is a fast index by `ascii_lower(keyword)`;
- `expression_edges` contains parameters, groups, and repeats;
- `accepting_routes` contains routes that may end at this exact node.

Literals are kept in a separate mapping so runtime lookup does not have to
scan every expression.

### `CommandNode.create(literal_edges, expression_edges, accepting_routes)`

A class method for defensive construction.

Copies the input `dict` and wraps it in `MappingProxyType`. The tuple and
frozenset are already immutable.

Result: a `CommandNode`. Subsequent mutation of the source dictionary does
not change the node.

## `CommandGraph`

```python
@dataclass(frozen=True, slots=True)
class CommandGraph:
    root: CommandNode
    patterns: tuple[PatternSource, ...]
    routes: Mapping[int, RouteSource]
```

The completed compilation artifact:

- `root` is the matching start node;
- `patterns` contains all sources in JSON order;
- `routes` provides provenance by `route_id`.

### `CommandGraph.create(root, patterns, routes)`

Copies the mutable `routes: dict[int, RouteSource]` and protects it with
`MappingProxyType`.

Result: a `CommandGraph`.

# `vrp_parser.graph.builder`

## `_DraftEdge`

An internal mutable dataclass:

```python
@dataclass(slots=True)
class _DraftEdge:
    step: GraphStep
    target: _DraftNode
    route_ids: set[int]
```

Used only during the build stage. `route_ids` is extended as each route is
inserted.

## `_DraftNode`

An internal mutable dataclass:

```python
@dataclass(slots=True)
class _DraftNode:
    edges: dict[tuple[object, ...], _DraftEdge]
    accepting_routes: set[int]
```

`edges` is indexed by the semantic `GraphStep.key`. Therefore, equal steps
from different routes physically share one draft edge.

## `CommandGraphBuilder`

Builds a shared-prefix graph and then recursively freezes it.

### `CommandGraphBuilder.__init__(route_expander=None, step_factory=None)`

Optional dependencies:

- `RouteExpander`, by default a new instance with a limit of 512;
- `GraphStepFactory`, by default a new instance.

Dependency injection allows the route-linearization or key strategy to be
tested or replaced independently.

### `CommandGraphBuilder.build(patterns)`

Input: `tuple[PatternSource, ...]`.

Algorithm:

1. create an empty `_DraftNode` root;
2. for each pattern in source order, call `expand(pattern.ast)`;
3. assign the next global integer ID to each resulting route;
4. create a `RouteSource` with the source pattern and static trace;
5. insert the steps into the draft graph through `_insert()`;
6. recursively freeze the root;
7. return `CommandGraph.create(...)`.

Route numbering starts at zero and depends on pattern order and the order of
linearized alternatives.

Result: an immutable `CommandGraph`. An empty tuple technically creates an
empty graph, although the public document layer prohibits an empty
`commands`.

### `CommandGraphBuilder._insert(root, expressions, route_id)`

For each expression:

1. obtain a `GraphStep`;
2. find the draft edge by `step.key`;
3. create the edge and target if this is the key's first occurrence;
4. add `route_id` to the ownership set;
5. advance to the target.

After the final expression, the route ID is added to
`node.accepting_routes`.

The method mutates only the draft structure and returns nothing.

### `CommandGraphBuilder._freeze(draft)`

Recursively converts the mutable draft tree into a `CommandNode`.

Order of operations:

1. sort semantic keys by `repr` for deterministic output;
2. freeze the target of every edge;
3. replace `set[int]` with `frozenset[int]`;
4. place a literal edge into the index by `ascii_lower(value)`;
5. append every other edge to the ordered `expression_edges` tuple;
6. call `CommandNode.create()`.

Result: an immutable `CommandNode`.

The builder assumes an acyclic draft structure: every insert advances only
to the next sequence level.

# `vrp_parser.graph.__init__`

The package facade exports the production graph API:

```text
CommandEdge
CommandGraph
CommandGraphBuilder
CommandNode
GraphStep
PatternSource
RouteSource
```

`LinearRoute`, `RouteExpander`, `RouteLimitExceeded`, `GraphStepFactory`, and
the draft types remain internal package details and are imported from their
modules only where implementation customization is needed.

# Provenance Algorithm from JSON to Result

Provenance makes it possible to return the original pattern and concrete
variation even after aggressive merging of shared prefixes.

## 1. Source Identity

A `PatternSource` is created for every valid string:

```text
pattern_id = sha256(original) + duplicate occurrence
index      = position in commands
original   = source text
ast        = parsed tree
```

Literal normalization does not change `original`.

## 2. Route Identity

Every `LinearRoute` receives a distinct `route_id` and `RouteSource`.
Choice and optional decisions made at compile time are stored in
`static_trace`.

## 3. Edge Ownership

When edges are merged, each edge contains the union of all routes that own
that semantic transition:

```text
edge.route_ids = {route_1, route_7, route_19}
```

The terminal node separately stores `accepting_routes`.

## 4. Runtime Intersection

The matcher begins with the routes allowed at the root and retains only
routes present on each traversed edge. It therefore cannot construct a
command from the beginning of one pattern and the ending of another.

## 5. Source Reconstruction

After a successful match, the matcher uses:

- `graph.routes[route_id].pattern.original`;
- `pattern.pattern_id`;
- `pattern.index`;
- `static_trace`;
- the dynamic trace of symbolic groups, repeats, and enums.

It uses these values to build a `PatternMatch` with `original_pattern`,
`variation`, `variation_id`, and the complete trace.

If multiple source patterns accept one CLI line, provenance for each one is
preserved separately. The primary match is selected by the original JSON
order, and the remaining matches are returned as alternatives.

# Invariants and Responsibility Boundaries

- The lexer is responsible for exact spans and structural tokens.
- The registry is responsible for the syntax of concrete parameter
  declarations.
- The parser is responsible only for grammar and cardinality syntax.
- Runtime policy prevents known malformed placeholders from silently
  becoming literals; this includes exact built-in IP declarations.
- The compiler collects all expected errors and does not construct a partial
  graph.
- The route expander expands only a safe number of ordinary choices.
- The step factory separates semantic equality from source location.
- The builder preserves ownership of every route on every edge.
- The graph is immutable after construction.
- CLI parameter-value validation and construction of runtime ambiguity are
  outside the layer described here.
