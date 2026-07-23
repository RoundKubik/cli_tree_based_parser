# Command matching subsystem (`vrp_parser.matching`)

This document describes the runtime part of the parser: how an already
compiled `CommandGraph` is matched against a single CLI line, how parameters
are validated, how the most specific route is selected, and how a result or
error is produced.

The document covers all production modules in
`src/vrp_parser/matching`.

## Public API boundary

Application code should parse individual lines through `CommandLineParser`
and complete files through `ConfigurationParser`:

```python
from vrp_parser import CommandLineParser, ConfigurationParser

line_parser = CommandLineParser.from_json_file("data/commands.json")

line_result = line_parser.parse("interface Vlanif100")
report = ConfigurationParser(line_parser).parse(configuration_text)
```

The `vrp_parser.matching` package is an internal layer between
`CommandLineParser` and the public result dataclasses from
`vrp_parser.results`. At the `matching.__init__` level, only the following are
exported:

- `CommandMatcher` — a low-level matcher for one line without indentation;
- `ResolvedMatch` — the matcher's internal successful result.

The remaining entities have public names to simplify composition and unit
testing, but they are not a stable application-facing API. In particular,
application code should not instantiate `WalkState` or `Candidate` manually,
or call `_walk()`.

## Overall data flow

```text
line without indentation
        │
        ▼
CommandText ──► traverse CommandGraph ──► Candidate[]
                      │
                      ├─ ExpressionMatcher
                      │    ├─ LiteralExpressionMatcher
                      │    ├─ ParameterExpressionMatcher
                      │    ├─ GroupExpressionMatcher
                      │    └─ RepeatExpressionMatcher
                      │
                      └─ MatchDiagnostics
        │
        ▼
deduplication ──► Pareto frontier ──► MatchResolver
                                                │
                         ┌──────────────────────┴──────────────────────┐
                         ▼                                             ▼
                  ResolvedMatch                                    ParseError
                         │
                         ▼
        CommandLineParser converts it to ParsedCommand
```

1. The compiler merges the prefixes of all patterns into one `CommandGraph`
   in advance.
2. `CommandMatcher` traverses applicable graph edges. On each edge,
   `ExpressionMatcher` returns zero, one, or several subsequent immutable
   states.
3. Reaching a terminal node after consuming the complete command produces a
   `Candidate`. A candidate may contain both accepted and rejected parameters;
   this is necessary to distinguish a syntax error from a validation error.
4. `MatchResolver` retains routes that are not dominated by a more specific
   route.
5. If a more specific parameter is applicable by shape but violates a
   constraint, it may block a less specific valid route.
6. All remaining equally ranked matches are preserved. Ambiguity is a
   successful result with the `ambiguous` status, not an error.
7. If there is no complete candidate, dedicated diagnostic components produce
   a detailed English `ParseError` and, in an appropriate literal-led
   scenario, add up to five similar original patterns.

## Common formats and conventions

### Positions and spans

- `position`, `start`, and `end` are Python string indices, meaning positions
  in Unicode code points rather than UTF-8 bytes.
- Spans are half-open: `[start, end)`.
- The matcher receives a command with its leading indentation already
  removed.
- `span_offset` is added when public spans are created so that coordinates
  refer to the original line, including indentation.
- Whitespace between tokens is skipped through `str.isspace()`.

### Immutable collections

States and results use `tuple` and `frozenset`. This makes it safe to branch
the search: one branch cannot change another branch's state.

- Methods returning `Iterator[WalkState]` yield all possible continuations
  lazily.
- Methods returning `tuple[WalkState, ...]` have already computed and usually
  deduplicated all continuations.
- Tuple order is stable and participates in selecting the first match.

### Three parameter states

`ParameterResult.status` has three possible values:

- `VALID` — the type is applicable and the value passed validation;
- `INVALID` — the type is definitely applicable by shape, but the value
  violates its constraints;
- `NOT_APPLICABLE` — the string does not resemble this type at all.

This distinction is essential. For example, a number outside its allowed
range must produce a validation error and must not silently become a
`STRING`. A non-numeric token for `INTEGER`, however, has the
`NOT_APPLICABLE` status and does not block a matching `STRING`.

The same logic applies to IP addresses. `192.0.2.999` has an IPv4-like shape,
so `IPv4AddressValidator` returns `INVALID`, and the structured route blocks
a generic `STRING`. `router.example.com` is not lexically IPv4-like and
returns `NOT_APPLICABLE`, so it may be accepted by `STRING`. For IPv6, an
address-like token is defined as a value containing at least two colons; zone
identifiers containing `%` are considered applicable but invalid.

### Dispatch vector

Every successfully traversed expression adds a rank to
`WalkState.dispatch`:

| Expression/family | Rank |
|---|---:|
| literal | 0 |
| `ENUM` | 1 |
| structured type (`X.X.X.X`, `X:X::X:X`, dates, MAC) | 2 |
| numeric type | 3 |
| generic type, such as `STRING` | 4 |
| remainder parameter | 5 |
| type unknown to the registered registry | 6 |

The lower the rank, the more specific the match. Vectors are compared using
Pareto dominance rather than lexicographically.

## `state.py`: internal traversal state

### `CapturedParameter`

```python
@dataclass(frozen=True, slots=True)
class CapturedParameter:
    declaration: ParameterDeclaration
    token: ParameterToken
    normalized: object | None
```

The value of a parameter that passed validation.

- `declaration` — the placeholder description from the source pattern:
  `type_id`, source text, constraints, and metadata;
- `token` — the source value and its span in the CLI line;
- `normalized` — the value after validator normalization. Its type is
  deliberately `object | None` because a pluggable type may return its own
  object; the matcher does not require deep immutable semantics from it.

The object is created by `ParameterExpressionMatcher` and later converted to
the public `ParameterValue`.

### `RejectedParameter`

```python
@dataclass(frozen=True, slots=True)
class RejectedParameter:
    declaration: ParameterDeclaration
    token: ParameterToken
    result: ParameterResult
```

The reader consumed a token for this parameter, but the validator returned
`INVALID` or `NOT_APPLICABLE`.

#### `applicable`

```python
@property
def applicable(self) -> bool
```

Returns `True` only for `ParameterStatus.INVALID`. The property means “the
value had the shape of this type but did not pass validation,” rather than
merely “validation was unsuccessful.” The resolver uses it when blocking a
generic valid route.

### `WalkState`

```python
@dataclass(frozen=True, slots=True)
class WalkState:
    position: int = 0
    parts: tuple[str, ...] = ()
    parameters: tuple[CapturedParameter, ...] = ()
    rejected: tuple[RejectedParameter, ...] = ()
    dispatch: tuple[int, ...] = ()
    parameter_led: bool | None = None
    source_order: tuple[int, ...] = ()
    trace: tuple[VariationStep, ...] = ()
```

A complete snapshot of one backtracking search branch.

- `position` — the read position in `CommandText.value`;
- `parts` — normalized parts of the resulting variation. Literals are stored
  in ASCII lowercase, while parameters use their declaration text, for
  example `("interface", "STRING<1-63>")`;
- `parameters` — valid captured parameters;
- `rejected` — parameters that were read but rejected;
- `dispatch` — the sequence of ranks for traversed atoms;
- `parameter_led` — diagnostic classification of the first consumed atom:
  `True` for an applicable parameter, `False` for a literal or
  `NOT_APPLICABLE` parameter, and `None` before anything is consumed;
- `source_order` — group and repeat decisions in outer-pattern order;
- `trace` — detailed provenance information (`choice`, `optional`, `set`,
  `repeat`, `enum`).

The initial state is `WalkState()` with position `0` and empty tuples. Modified
states are created through `dataclasses.replace`; the object itself is
immutable.

### `source_order_with_parent()`

```python
def source_order_with_parent(
    base: WalkState,
    result: WalkState,
    decision: int,
) -> tuple[int, ...]
```

Inserts the outer construct's decision before decisions made inside its
branch.

- `base` — the state before entering the group or repeat;
- `result` — the state after parsing the nested expression;
- `decision` — the selected alternative index or repetition count;
- the result is a new `source_order`.

The algorithm preserves the `base.source_order` prefix, appends `decision`,
and then appends only decisions made after `base`. This provides stable source
order for nested groups.

### `Candidate`

```python
@dataclass(frozen=True, slots=True)
class Candidate:
    route_id: int
    state: WalkState
```

A complete match of the command shape against one linear graph route.
`route_id` associates the state with `RouteSource`, the source JSON pattern,
and the compile-time trace. The presence of items in `state.rejected`
distinguishes an invalid candidate from a valid one.

## `text.py`: reading a CLI line

### `ascii_lower()`

```python
def ascii_lower(value: str) -> str
```

Converts only `A-Z` to `a-z` through `str.translate`. It does not perform
Unicode case folding and does not change any other characters. It is used for
case-insensitive comparison of VRP keywords and for stable variations and
signatures.

### `CommandToken`

```python
@dataclass(frozen=True, slots=True)
class CommandToken:
    raw: str
    start: int
    end: int
```

A single whitespace-delimited CLI token:

- `raw` — the text without surrounding whitespace;
- `start`, `end` — its half-open span in the line.

### `CommandText`

A wrapper around one command line.

#### `__init__(value)`

Stores the string in the subsystem-public `value` attribute. Type checking is
performed at the higher `CommandLineParser` level.

#### `skip_space(position)`

```python
def skip_space(self, position: int) -> int
```

Moves to the right while `value[position].isspace()` is true. Returns the
first non-whitespace position or `len(value)`.

#### `token(position)`

```python
def token(self, position: int) -> CommandToken | None
```

Skips whitespace and reads until the next whitespace character. Returns a
`CommandToken`, or `None` if there are no tokens after the given position. The
method does not mutate an internal cursor: the caller always supplies the
position.

#### `at_end(position)`

```python
def at_end(self, position: int) -> bool
```

Returns `True` if skipping whitespace reaches the end of the line.

## `diagnostics.py`: syntax expectations

### `MatchDiagnostics`

```python
@dataclass(slots=True)
class MatchDiagnostics:
    position: int = 0
    expected: set[str] = field(default_factory=set)
    non_parameter_path_progress: int = -1
    parameter_path_progress: int = -1
```

The subsystem's only deliberately mutable accumulator. It is shared across
traversal branches and retains expectations only at the furthest position
reached. The two progress fields additionally show how far routes advanced
when their first consumed atom was a literal/non-applicable parameter or an
applicable parameter. They are used only to decide whether to offer keyword
suggestions and do not affect matching.

#### `record(position, description, *, parameter_led=None)`

- if the new position is further than the current one, replaces the entire
  set of expectations;
- if the position equals the current one, adds `description`;
- if the position is earlier, ignores the record;
- `parameter_led=True` means that the first parameter was applicable;
  `False` means a literal or a non-applicable first parameter;
  `None` means the route has not consumed anything yet;
- the corresponding progress field records the maximum position reached even
  if the global `expected` value already refers to another branch.

This makes the error report the most useful point rather than an early failed
route.

#### `allows_keyword_suggestions`

The property returns `True` when a route without an applicable first parameter
advanced at least as far as a parameter-first route. If an applicable
parameter-first route explains the line better, literal keyword
recommendations are suppressed so that commands from another recognition
space are not displayed.

#### `elements(offset=0)`

```python
def elements(self, *, offset: int = 0) -> tuple[ExpectedElement, ...]
```

Sorts textual expectations and returns a tuple of public `ExpectedElement`
objects. `offset` is added to each position. The `set` prevents duplicates.

#### `_record_path_progress(position, parameter_led)`

A private helper that updates one of the progress fields according to the
route's explicit classification. `None` means that the route has not consumed
anything yet, so that record does not participate in suggestion selection. A
`NOT_APPLICABLE` parameter cannot suppress a useful keyword recommendation by
itself.

## `walking.py`: recursive traversal interface

### `ExpressionWalker`

A `Protocol` on which group and repeat matchers depend instead of depending on
the concrete `ExpressionMatcher`.

#### `walk(expression, state, command, diagnostics, *, path)`

Returns an `Iterator[WalkState]` containing every state after one AST node.

#### `sequence(expressions, state, command, diagnostics, *, path)`

Returns a deduplicated `tuple[WalkState, ...]` after sequentially parsing a
tuple of AST nodes.

`ExpressionMatcher` satisfies this protocol structurally; inheritance is not
required.

## `atoms.py`: literals and parameters

### `DispatchOrder`

Provides a centralized mapping from `ParameterFamily` to specificity rank.

#### `rank(declaration, registry)`

```python
def rank(
    self,
    declaration: ParameterDeclaration,
    registry: ParameterTypeRegistry,
) -> int
```

Looks up the type through `registry.get(declaration.type_id)` and returns the
rank from the dispatch table. Returns `6` if the type is absent. The method
does not know concrete type IDs, so a new type is connected through the
registry and selects behavior through its `family`.

### `LiteralExpressionMatcher`

#### `match(expression, state, command, diagnostics)`

```python
def match(...) -> Iterator[WalkState]
```

Matches one `Literal` against one whitespace-delimited token.

- Comparison is case-insensitive for ASCII only.
- On a mismatch, the iterator is empty and diagnostics receives the `repr()`
  of the expected literal at the token's starting position.
- On a match, exactly one new state is yielded:
  - `position = token.end`;
  - the lowercase literal is appended to `parts`;
  - `0` is appended to `dispatch`;
  - if the route's beginning has not yet been classified,
    `parameter_led=False`.

Parameters, rejected values, source order, and trace remain unchanged.

### `ParameterExpressionMatcher`

#### `__init__(parameter_types, dispatch_order=None)`

Stores the `ParameterTypeRegistry`. The optional `DispatchOrder` allows the
strategy to be replaced in a test or composition root.

#### `match(expression, state, command, diagnostics, *, path)`

```python
def match(...) -> Iterator[WalkState]
```

Algorithm:

1. Checks that `expression.declaration` is a `ParameterDeclaration`; otherwise
   raises `TypeError`.
2. Applies `_text_policy_allows()` to the declaration and current position.
3. Asks the registry reader to read a value from `state.position`.
4. If the reader finds no value, yields nothing and records the declaration in
   diagnostics.
5. Passes `token.raw` to the validator through `registry.probe()`.
6. Appends the family rank to dispatch.
7. Appends `CapturedParameter` for `VALID`, or `RejectedParameter` for
   `INVALID` and `NOT_APPLICABLE`.
8. For the first parameter, sets `parameter_led=True` if the status is
   applicable (`VALID`/`INVALID`), or `False` for `NOT_APPLICABLE`.
9. In either case, consumes the token, appends the original declaration text
   to `parts`, and yields exactly one state.

Important: a rejected parameter does not terminate the route immediately. The
branch must reach a graph terminal so the resolver can confirm that the full
command shape matched and return an accurate validation error.

`path` is the stable address of the expression within the route, such as
`step:2` or `step:2.1.0`; it is included in the provenance trace.

#### `_text_policy_allows(declaration, state, command)`

Protects against the catch-all behavior of a bare/root `TEXT<min-max>`:

- returns `True` for a declaration whose type is not `text`;
- returns `True` for `TEXT` matched at any position other than `0`;
- for `TEXT` at position `0`, returns `True` only when the command's first
  non-whitespace character is `!`.

The guard is located directly in `ParameterExpressionMatcher`, so it applies
to every `Parameter` regardless of nesting: on a regular graph edge, inside a
symbolic `Group` or `Repeat`, or on a symbolic route retained after fallback
route expansion. `description TEXT<1-80>` remains allowed because its keyword
has already advanced `state.position`.

#### `_trace(declaration, valid, normalized, state, path)`

Appends `VariationStep(kind="enum", path=path,
selected=(str(normalized),))` only for a valid parameter in the `ENUM` family.
For all other types and invalid enum values, returns the existing trace
unchanged.

## `deduplication.py`: stable deduplication

### `WalkStateIdentity`

#### `key(state)`

Returns a hashable tuple containing:

- the position and variation parts;
- declaration, raw value, and `repr(normalized)` for each captured parameter;
- declaration, raw value, status, and `repr(issue)` for each rejected
  parameter;
- dispatch;
- `parameter_led`;
- source order;
- trace.

`repr()` is used because an application plugin may return an unhashable
normalized object. The practical requirement for a plugin is that `repr()`
must be stable within the process and distinguish semantically different
values.

### `WalkStateSet`

#### `__init__(identity=None)`

Accepts an optional `WalkStateIdentity` strategy.

#### `unique(states)`

Traverses `tuple[WalkState, ...]` from left to right, preserves the first state
for each identity key, and returns a tuple. First-occurrence order is
preserved.

### `CandidateSet`

#### `__init__(identity=None)`

Uses the supplied or default `WalkStateIdentity`.

#### `unique(candidates)`

Removes duplicates from `list[Candidate]`, but adds `route_id` to the state
key. Consequently, identical states from different source routes are not
merged: the provenance of each JSON pattern is preserved. Returns a tuple in
the original order.

## `frontier.py`: Pareto specificity

### `DispatchDominance`

#### `dominates(left, right)`

Compares two `tuple[int, ...]` values element by element:

- `left` dominates `right` if its rank is no greater at every shared
  position;
- its rank must be strictly lower in at least one shared position;
- if there are no shared positions, neither vector dominates the other.

Comparison uses `zip(..., strict=False)`. If lengths differ, additional
trailing elements do not participate.

Examples:

```text
(1,)    dominates (4,)       # ENUM is preferred over STRING
(1, 4) dominates (4, 4)
(1, 4) and (4, 1) are incomparable  # genuine ambiguity is preserved
(3,)    does not dominate (3,)      # equality is not dominance
()      does not dominate (4,)
```

### `CandidateFrontier`

#### `__init__(dominance=None)`

Accepts a comparison strategy or creates `DispatchDominance`.

#### `select(candidates)`

1. Groups candidates by their complete dispatch vector.
2. Compares only unique vectors, which speeds up processing of many identical
   patterns.
3. Retains all buckets not dominated by another vector.
4. Returns candidates in their original order.

All candidates with the same vector are retained; this is necessary for the
`equivalent` and `ambiguous` statuses.

#### `dominates(left, right)`

A resolver-facing delegate to the configured dominance strategy.

## `alternatives.py`: early branch pruning

### `AlternativeOutcome`

```python
@dataclass(frozen=True, slots=True)
class AlternativeOutcome:
    alternative: int
    state: WalkState
```

The result of one group branch: the alternative index and the state reached.

### `AlternativeStateFrontier`

Limits combinatorial growth within group sets and repeats before the end of
the complete command is found.

#### `__init__(dominance=None, identity=None)`

Accepts Pareto comparison and state identity strategies.

#### `select(outcomes, base)`

Groups outcomes by final `state.position`, processes each position through
`_at_position()`, and combines results in ascending position order.

States at different positions cannot be compared by specificity: they have
consumed different amounts of input and may have different continuations.

#### `_at_position(outcomes, base)`

For one final position:

1. Finds branches that did not append a new `NOT_APPLICABLE` after `base`.
2. If any exist, excludes branches with a new `NOT_APPLICABLE`; otherwise,
   temporarily retains them all.
3. Compares only the new dispatch suffix created by this alternative.
4. Retains the non-dominated Pareto frontier.
5. Removes identical states while preserving the first one.
6. If every alternative contains a new `NOT_APPLICABLE`, retains only one
   representative. One is sufficient for a useful validation error, while
   retaining all such states would cause exponential growth.

#### `_unique(outcomes)`

Stably removes identical states through `WalkStateIdentity`. The alternative
index is not part of the key: if two branches produce completely identical
states, the first one remains.

#### `_dispatch(state, base)`

Returns only the part of dispatch appended after entering the alternative:
`state.dispatch[len(base.dispatch):]`.

#### `_has_new_not_applicable(state, base)`

Checks only newly rejected parameters and returns `True` if any have
`ParameterStatus.NOT_APPLICABLE`.

## `groups.py`: choices and unordered sets

### `GroupExpressionMatcher`

Supports four `GroupMode` values:

| Pattern syntax | `GroupMode` | Semantics |
|---|---|---|
| `{ a \| b }` | `REQUIRED_ONE` | exactly one alternative |
| `[ a \| b ]` | `OPTIONAL_ONE` | zero or one alternative |
| `{ a \| b } *` | `REQUIRED_SET` | one or more, up to all, each at most once, in any order |
| `[ a \| b ] *` | `OPTIONAL_SET` | zero or more, up to all, each at most once, in any order |

#### `__init__(alternatives=None)`

Accepts an `AlternativeStateFrontier` or creates the default one.

#### `match(expression, state, command, diagnostics, *, path, walker)`

Selects `_set()` for `OPTIONAL_SET`/`REQUIRED_SET`; otherwise selects
`_choice()`. Lazily yields all valid states.

#### `_choice(...)`

- For an optional group, first yields the skip branch. Its source order value
  is `0`, and trace receives
  `VariationStep(kind="optional", path=path, selected=())`.
- Then runs the sequence for each alternative from the same initial state.
- Results pass through the early `AlternativeStateFrontier`.
- The outer decision is inserted before nested decisions through
  `source_order_with_parent()`.
- For a required choice, the source-order decision equals the alternative
  index.
- For an optional choice, selected alternatives receive `index + 1` because
  the value `0` is already used by the skip branch.
- Trace receives `kind="choice"` or `"optional"` and
  `selected=(alternative_index,)`.

#### `_set(...)`

Implemented as recursive backtracking:

- `minimum = 0` for an optional set and `1` for a required set;
- `used: frozenset[int]` prevents an alternative from being selected twice;
- `order: tuple[int, ...]` stores the actual selection order;
- as soon as the minimum is reached, the current state is yielded with
  `VariationStep(kind="set", selected=order)`;
- the matcher then tries every alternative that has not yet been used;
- results that do not advance the position are discarded, so a nullable branch
  cannot create infinite recursion;
- the early frontier is applied **separately to each alternative**. Different
  alternative indices cannot be compared at this stage because the choice
  affects the set of remaining branches and otherwise a valid permutation
  would be lost;
- source order is updated for every choice, then the search continues with an
  expanded `used`.

In the worst case, the number of set permutations is factorial. Prohibiting
repeated choices, discarding zero-progress results, and early deduplication
substantially limit the search in practice.

##### Local function `visit(current, used, order)`

A nested recursive helper of `_set()`. It accepts the current `WalkState`, an
immutable set of already used alternative indices, and the selection order.
It lazily returns an `Iterator[WalkState]`: first the valid current set, then
the states of all recursive continuations with one new consuming alternative.

## `repeats.py`: bounded repetition

### `RepeatExpressionMatcher`

Processes the `Repeat(atom, minimum, maximum)` AST produced by `&<min-max>`.

#### `__init__(states=None)`

Accepts a `WalkStateSet` for deduplicating the frontier after each step.

#### `match(expression, state, command, diagnostics, *, path, walker)`

1. The initial frontier contains the source state.
2. If `minimum == 0`, immediately yields the zero-repetition variant.
3. For `count` from `1` through `maximum`, builds the next frontier through
   `_next()`.
4. An empty frontier terminates the loop: no further repetition is possible.
5. When `count >= minimum`, yields every frontier state with provenance for
   the actual count.

The method returns an iterator over every allowed cardinality, not only the
maximum count. The pattern continuation determines which variant can reach a
terminal.

#### `_next(expression, frontier, command, diagnostics, *, path, count, walker)`

Applies `walker.walk()` to the atom for every current state. The path of a
specific repetition has the form `"{path}.{count - 1}"`. A result is accepted
only if its position advanced; all results are then stably deduplicated.

#### `_with_count(base, state, path, count)`

Returns a copy of the state:

- inserts `count` into the outer source order;
- appends `VariationStep(kind="repeat", path=path,
  selected=(count,))`.

## `expressions.py`: polymorphic AST coordinator

### `ExpressionMatcher`

The composition root for atom, group, and repeat matchers, and the
implementation of `ExpressionWalker`.

#### `__init__(parameter_types, dispatch_order=None, states=None)`

Creates:

- a shared `WalkStateSet`;
- `LiteralExpressionMatcher`;
- `ParameterExpressionMatcher`;
- `GroupExpressionMatcher`;
- `RepeatExpressionMatcher`, using the same state set.

#### `match(expression, state, command, diagnostics, *, path)`

Fully evaluates `walk()`, stably removes duplicates, and returns
`tuple[WalkState, ...]`. This is the primary entry point for one graph edge.

#### `walk(expression, state, command, diagnostics, *, path)`

Dispatches by the concrete AST type:

- `Literal` → literal matcher;
- `Parameter` → parameter matcher;
- `Group` → group matcher with `walker=self`;
- `Repeat` → repeat matcher with `walker=self`.

Raises `TypeError("unsupported pattern node: ...")` for an unknown `Node`.
The result is a lazy iterator.

#### `sequence(expressions, state, command, diagnostics, *, path)`

Applies a tuple of expressions sequentially:

1. the frontier starts with one source state;
2. each expression is applied to every state in the frontier;
3. the resulting Cartesian set is stably deduplicated;
4. processing stops early if the frontier becomes empty.

The index of each expression is appended to `path`. The method returns an
evaluated tuple. This method provides backtracking within alternatives: every
intermediate variant continues independently.

## `matcher.py`: traversing the merged graph

### `_Traversal`

```python
@dataclass(frozen=True, slots=True)
class _Traversal:
    node: CommandNode
    state: WalkState
    route_ids: frozenset[int] | None
    depth: int
```

An internal recursive traversal frame:

- `node` — the current graph node;
- `state` — the state after the prefix;
- `route_ids` — routes compatible with the entire path traversed so far;
  `None` at the root means there is no initial restriction;
- `depth` — the graph step number, used in the trace path.

### `CommandMatcher`

A low-level recognizer for one command. It does not handle indentation,
`line_number`, or an empty line; those are `CommandLineParser`
responsibilities.

#### `__init__(graph, parameter_types, expression_matcher=None, resolver=None, candidate_set=None, error_factory=None)`

Required dependencies:

- `graph: CommandGraph` — the immutable merged prefix graph;
- `parameter_types: ParameterTypeRegistry` — the same set of types with which
  the graph was compiled.

Optional dependencies allow components to be tested independently. By
default, the matcher creates a `CommandSuggester` for the given graph and
registry and passes it to `CommandErrorFactory`. Both runtime diagnostic
strategies can be replaced together through `error_factory`.

#### `match(text, *, span_offset=0)`

```python
def match(
    self,
    text: str,
    *,
    span_offset: int = 0,
) -> ResolvedMatch | ParseError
```

Creates `CommandText`, diagnostics, and a candidate list, then starts `_walk`
from the root with `WalkState()`.

- If at least one terminal candidate is found, candidates are deduplicated and
  passed to `MatchResolver.resolve()`.
- If there are no complete candidates, `CommandErrorFactory` returns a
  detailed syntax-error or unknown-command `ParseError` and, when appropriate,
  top-five suggestions.
- `span_offset` does not affect matching; it only shifts public positions.

The method does not validate the type of `text` or the sign of `span_offset`;
`CommandLineParser` enforces the correct external contract.

#### `_walk(traversal, command, diagnostics, candidates)`

A recursive depth-first traversal of the graph.

1. Checks whether the cursor is at the end of the command.
2. At the end, intersects `node.accepting_routes` with the active `route_ids`
   and appends a `Candidate` for each allowed terminal route.
3. At the same point, records possible next literals in diagnostics. If there
   are no expression edges, terminates the branch.
4. If input remains but the node is already terminal, records the expectation
   `"end of command"`.
5. `_edges()` selects potential edges.
6. For each edge, intersects its route IDs with the active IDs. This is
   essential: shared graph nodes must not allow a traversal to “start with one
   pattern and finish with another.”
7. Matches the expression and recursively continues from each resulting
   state. The TEXT policy is applied inside `ParameterExpressionMatcher` when
   necessary.

Candidates include both valid and validation-rejected complete routes. A
partial route never becomes a candidate.

#### `_edges(node, command, position)`

Reads the current token and looks up a literal edge by its ASCII-lower key.

- If no matching literal exists, returns only `expression_edges`.
- If one exists, returns the tuple `(literal, *expression_edges)`.

The literal is therefore tried first, but generic/parameter branches are not
discarded prematurely. The Pareto resolver makes the final selection after
checking complete continuations.

If no candidates remain after deduplication, `match()` passes `command.value`,
diagnostics, and `span_offset` to `self._errors.create()`. Message and
recommendation construction is separated from the graph walker in
`runtime_errors.py`.

## `suggestions.py`: similar literal-led commands

The suggestion subsystem is a separate diagnostic index. It does not add
graph routes, create successful candidates, or affect matcher priorities in
any way. Its only result is up to five `original_pattern` strings for
`ParseError.suggestions`.

### `_TemplateAtom`

One element of a lightweight search template:

- `literal` stores an ASCII-lower keyword;
- `declaration` stores a `ParameterDeclaration`;
- only one field is populated at a time.

`from_node(node)` accepts only `Literal | Parameter`. For a parameter, the
declaration type is checked; an unknown object indicates an internal invariant
violation and raises `TypeError`.

### `_SuggestionTemplate`

An immutable tuple of atoms for one search variation. This is not a runtime
route: a template is used only for fuzzy comparison and may represent one
characteristic group/repeat variant.

### `_SuggestionPattern`

Combines `pattern_index`, the source string, and a tuple of searchable
templates for one source pattern.

### `SuggestionTemplateFactory`

Creates a bounded set of searchable variations from the AST. The constructor
accepts `maximum_templates=64` and rejects a non-positive limit.

#### `create(pattern)`

Recursively converts `pattern.ast`, removes duplicates, and keeps only
templates whose first atom is a literal. A pure
`STRING<1-20> activate` pattern therefore does not enter the index. A pattern
with an optional parameter may enter it if a literal-led variation exists,
such as `[ STRING<1-20> ] display clock`.

#### `_sequence(sequence)` and `_node(node)`

`_sequence()` builds the Cartesian product of variants for sequential AST
nodes. `_node()` dispatches `Literal`, `Parameter`, `Group`, and `Repeat`; an
unknown node raises `TypeError`.

#### `_group(group)` and `_set_order(alternatives)`

- a one-choice group adds templates from every alternative;
- optional-one additionally adds an empty template;
- a set group adds individual alternatives, canonical order, and reverse
  order;
- optional-set also adds an empty variant.

This is deliberately a bounded representation, not a complete enumeration of
all set-group permutations.

#### `_repeat(repeat)`

Representative counts are sufficient for searching: `minimum`, a count of one
when `minimum == 0`, and the nearest larger allowed count. Each variant is
built with the same bounded product.

#### `_product(left, right)` and `_unique(templates)`

These helpers stably remove duplicates and truncate the result to
`maximum_templates`.

### `SuggestionCatalog`

Builds an immutable index once when `CommandMatcher` is created.

#### `__init__(graph, template_factory=None)`

Traverses source patterns in JSON order, creates `_SuggestionPattern` only
when a literal-led template exists, and builds the index
`root literal → pattern entries`.

Bare/root `TEXT<min-max>` and other pure parameter-first patterns have no
templates and are absent from the index.

#### `root_keywords`

Returns a sorted tuple of all indexed root keywords.

#### `candidates(nearby_roots)`

Builds a candidate set only for an exact or similar root. A match on a
non-root keyword alone is insufficient. Secondary tokens are analyzed later
by `CommandSimilarity.could_be_relevant()`, so a noisy exact suffix cannot
prematurely exclude a closer fuzzy pattern. Entry order is stable.

### `TokenDistance`

Compares individual tokens without third-party libraries.

- `distance(left, right)` calculates edit distance; transposing adjacent
  characters counts as one operation;
- `cost(left, right)` normalizes distance to the `0..1000` range;
- `similarity(left, right)` returns `1000 - cost`.

### `_SimilarityScore`

A sortable immutable score with four components:

1. normalized edit cost;
2. negative count of exact prefix literals;
3. negative count of all exact literal matches;
4. difference in token counts.

A smaller tuple denotes a more relevant template.

### `CommandSimilarity`

Compares concrete CLI tokens against atoms in a searchable template.

#### `score(query_tokens, template)`

Dynamic programming permits insertion, deletion, replacement, and
transposition of two adjacent literals. For a parameter atom, it calls the
same `ParameterTypeRegistry.probe()` used by the matcher:

- a `VALID` parameter has a low cost;
- an `INVALID` parameter remains similar but receives a penalty;
- `NOT_APPLICABLE` receives a high penalty.

The result is normalized by the maximum length, after which exact-prefix,
exact-match, and token-count tie-breakers are added.

#### `is_relevant(query_tokens, template, score)`

Finally filters out accidental matches by normalized cost. An obvious typo in
the root is allowed even when the suffix differs significantly because the
root keyword is the strongest signal of the intended command.

#### `could_be_relevant(query_tokens, template)`

A cheap pre-filter before dynamic programming. A template must have an exact
or similar literal root. A multi-token command must additionally have an exact
or similar secondary literal, or an applicable parameter slot. This prevents
a common root such as `display` from turning an unrelated suffix into five
arbitrary recommendations.

#### `root_is_near(query, root)`

A fast pre-filter for root keywords. The allowed edit distance depends on the
query token's length, and normalized similarity must not fall below the
threshold.

#### Internal helpers

- `_substitution_cost(atom, token)` selects literal distance or a parameter
  probe result;
- `_missing_cost(atom)` assigns different penalties to literals and
  parameters;
- `_is_transposition(...)` recognizes transposed adjacent literal tokens;
- `_exact_literal_matches(...)` counts the multiset intersection of literals;
- `_exact_literal_prefix(...)` counts a contiguous exact literal prefix.

### `CommandSuggester`

The matching layer's public strategy for top-five recommendations.

#### `__init__(graph, parameter_types, catalog=None, similarity=None)`

Creates `SuggestionCatalog` and `CommandSimilarity` by default. Injecting
either strategy allows orchestration to be tested independently.

#### `suggest(command, limit=5)`

Splits the line on whitespace, converts tokens to ASCII lowercase, and returns
at most `min(limit, 5)` source patterns. An empty command or a limit below one
produces an empty tuple. The latest 256 normalized queries are cached; the
cache is not part of the public result.

#### `_rank(query_tokens)`

1. selects nearby roots;
2. obtains candidates from the index;
3. uses `_searchable()` to retain templates with a relevant suffix; if none
   exist, a strong root typo enables a root-based fallback;
4. finds the best template for each source pattern;
5. removes irrelevant entries and exact textual self-suggestions;
6. sorts by `_SimilarityScore`, then by JSON pattern index;
7. removes duplicate `original_pattern` values;
8. returns at most five strings.

Groups and placeholders in recommendations are not expanded: the user sees
the exact source pattern from JSON.

## `runtime_errors.py`: English matching error messages

### `ExpectedElementFormatter`

Converts a structured tuple of expectations into a short English phrase.

#### `format(expected, maximum=5)`

Takes `ExpectedElement.description`, displays no more than `maximum` items, and
collapses the rest into `"and N more options"`. An empty tuple becomes
`"a valid continuation"`.

#### `_join(items)`

Uses English `or` and the Oxford comma for one, two, or several visible
expectations.

### `CommandErrorFactory`

Builds `UNKNOWN_COMMAND` and `SYNTAX_ERROR`. Validation errors remain the
responsibility of `ValidationErrorFactory`.

#### `__init__(suggester, expected_formatter=None)`

Accepts a required `CommandSuggester` and an optional expectation-formatting
strategy.

#### `create(command, diagnostics, *, span_offset)`

1. selects `UNKNOWN_COMMAND` if the furthest position is `0`; otherwise
   selects `SYNTAX_ERROR`;
2. translates `position` and `ExpectedElement.position` into coordinates in
   the source line;
3. calls the suggester only when
   `diagnostics.allows_keyword_suggestions`;
4. creates a `ParseError` with `expected` and `suggestions`.

`failures`, `candidate_patterns`, and `candidate_variations` are empty for
these errors.

#### `_message(command, code, position, expected, suggestions, suggestions_allowed)`

All messages are produced in English.

When recommendations are available, the format is stable:

```text
Command 'dispaly clock' was not recognized. Did you mean:
  1. display clock
Reason: No complete command pattern accepted the first token.
```

For a syntax error, the `Reason` line reports a 1-based column and briefly
lists expectations. The structured `position` and `expected` values remain
complete and use 0-based string offsets.

If recommendations are absent, the message explains why. For an ordinary
`UNKNOWN_COMMAND`, it says
`"No similar literal command patterns were found."`; for a syntax error with
no relevant candidate, it gives a similar explanation about insufficient
similarity. If recommendations were suppressed by an applicable
parameter-led route, the text explicitly states that as well.

#### `_numbered(suggestions)` and `_no_suggestion_message(code, suggestions_allowed)`

The first helper formats a numbered top-five block. The second adds an
explicit explanation of an empty result or suppressed keyword suggestions.

## `resolver.py`: final match resolution

### `ResolvedMatch`

```python
@dataclass(frozen=True, slots=True)
class ResolvedMatch:
    status: MatchStatus
    primary_match: PatternMatch
    alternative_matches: tuple[PatternMatch, ...]
```

The internal successful result:

- `status` — `UNIQUE`, `EQUIVALENT`, or `AMBIGUOUS`;
- `primary_match` — the first representative in source JSON order;
- `alternative_matches` — all other matches that survived resolution.

`CommandLineParser` wraps it in the public `ParsedCommand`, adding the source
line, indentation, and line number.

### `MatchResolver`

#### `__init__(frontier=None, matches=None, validation_errors=None)`

Accepts replaceable:

- `CandidateFrontier`;
- `PatternMatchSet`;
- `ValidationErrorFactory`.

#### `resolve(candidates, graph, *, span_offset)`

```python
def resolve(...) -> ResolvedMatch | ParseError
```

Algorithm:

1. Divides candidates into:
   - `valid` — `state.rejected` is empty;
   - `invalid` — at least one rejected parameter is present.
2. Computes the Pareto frontier of valid candidates.
3. Retains the applicable frontier among invalid candidates: every rejected
   parameter in the candidate must have `INVALID`, not `NOT_APPLICABLE`.
4. Removes from the valid frontier any candidate dominated by an
   applicable-invalid candidate.
5. If valid candidates existed but were all blocked, builds a validation
   error only from the actual blockers.
6. If there are no valid results, builds a validation error from the
   applicable frontier; if that is empty, uses the Pareto frontier of all
   invalid candidates, including `NOT_APPLICABLE`.
7. Otherwise, creates public pattern matches and computes the success status.

Blocking example:

```text
patterns: value INTEGER<1-10>
          value STRING<1-20>
input:    value 99
```

The numeric route has dispatch `(0, 3)` and is applicable, but violates its
range. It dominates the generic valid `(0, 4)` route, so the result is
`VALIDATION_ERROR`, not a successful `STRING`.

The input `value abc` produces `NOT_APPLICABLE` for the integer and therefore
does not block `STRING`.

Similarly, with the patterns `peer X.X.X.X` and `peer STRING<1-64>`, the input
`peer 192.0.2.999` returns `VALIDATION_ERROR` from `ipv4-address`, while
`peer router.example.com` successfully uses the generic string route.

#### `_applicable_frontier(invalid)`

Selects invalid candidates for which:

- the rejected tuple is not empty;
- every `RejectedParameter.applicable` is `True`.

Returns their `CandidateFrontier.select()`.

#### `_unblocked(valid, invalid)`

Returns valid candidates whose dispatch is not dominated by any
applicable-invalid dispatch.

#### `_blockers(invalid, valid)`

The inverse operation: returns invalid candidates that dominate at least one
valid candidate. They are used for an accurate validation report.

#### `_status(matches)`

- one match → `MatchStatus.UNIQUE`;
- several with the same semantic signature →
  `MatchStatus.EQUIVALENT`;
- several different signatures → `MatchStatus.AMBIGUOUS`.

## `matches.py`: public matches and provenance

### `PatternMatchFactory`

#### `create(candidate, route, *, span_offset)`

Converts one valid internal candidate to a `PatternMatch`.

- `variation` is built as `" ".join(state.parts)`;
- the full trace is `route.static_trace + state.trace`, so compile-time
  expanded choices are not lost;
- `variation_id` is computed by `_variation_id()`;
- each `CapturedParameter` is converted to a `ParameterValue`;
- the parameter span is shifted by `span_offset`;
- `pattern_id`, `pattern_index`, and `original_pattern` are taken from
  `RouteSource.pattern`.

Result format:

```python
PatternMatch(
    pattern_id="pattern:<20 hex chars>:<duplicate occurrence>",
    pattern_index=17,
    original_pattern="interface { STRING<1-63> | ENUM{Vlanif,...} }",
    variation="interface ENUM{Vlanif,...}",
    variation_id="<20 hex chars>",
    parameters=(ParameterValue(...),),
    trace=(VariationStep(...),),
)
```

#### `_variation_id(pattern_id, variation, trace)`

Creates material from:

- the pattern ID;
- the ASCII-lower variation;
- `repr(trace)`.

The elements are joined with a NUL character and hashed with SHA-256; the first
20 hexadecimal characters are returned. The ID is deterministic for one
pattern content/occurrence and one variation path; reordering unrelated JSON
patterns does not change it.

### `PatternMatchSet`

#### `__init__(factory=None)`

Accepts a `PatternMatchFactory`.

#### `create(candidates, graph, *, span_offset)`

Sorts candidates by:

1. `pattern.index` — source JSON order;
2. `route_id`;
3. `state.source_order`;
4. candidate order in the input tuple.

It then creates `PatternMatch` objects and stably deduplicates them by
`_identity()`. The first match after this operation becomes the primary match.

#### `equivalent(matches)`

Computes `_signature()` for every match. Returns `True` if there is exactly one
unique signature. It also returns `False` for an empty tuple
(`len(set()) != 1`), but the resolver calls the method only for multiple
matches.

#### `_identity(match)`

A key for removing completely duplicate matches:

- `pattern_id`;
- ASCII-lower variation;
- for each parameter: `type_id`, declaration, and raw value.

Trace and normalized values are deliberately excluded. Two paths through one
pattern that produce the same visible variation and the same raw parameters
are represented by the first match.

#### `_signature(match)`

A semantic signature for the `equivalent` status:

- ASCII-lower variation;
- for each parameter: `type_id`, declaration, raw value, and
  `repr(normalized)`.

`pattern_id` is excluded, so identical results from different source patterns
are considered equivalent, while every pattern match is still returned in
`alternative_matches`.

## `validation.py`: building parameter errors

### `ValidationErrorFactory`

#### `create(candidates, graph, *, span_offset)`

Sorts candidates by JSON pattern index and route ID. For each one, it:

- preserves `route.pattern.original`;
- builds the candidate variation with `" ".join(state.parts)`;
- converts every rejected parameter to `ValidationFailure`.

It then stably removes duplicate failures, patterns, and variations and
returns:

```python
ParseError(
    code=ErrorCode.VALIDATION_ERROR,
    message=<detailed English description>,
    position=<start of the first failure or None>,
    failures=(...),
    candidate_patterns=(...),
    candidate_variations=(...),
    suggestions=(),
)
```

The message names the one matched pattern or gives the number of candidate
patterns, states the number of invalid values, and includes up to three
reasons. If there are more failures, the remainder is collapsed into
`"and N more failures"`. The complete set is never lost and remains in the
structured `failures` field.

`expected` and `suggestions` remain empty for a validation error: the command
structure was found, so suggesting similar keywords would be incorrect.

#### `_failure(rejected, *, span_offset)`

Creates one `ValidationFailure`.

- For `NOT_APPLICABLE`, the message is:
  `"value does not match <declaration>"`, `reason_code="not_applicable"`,
  `expected=<declaration>`, and `actual=<raw token>`.
- For `INVALID`, `ParameterIssue.message` is used; if a plugin violates the
  expected contract and the issue is absent, the fallback is
  `"invalid parameter value"` with `reason_code="invalid_value"`.
- When `ParameterIssue` is present, its `code`, `expected`, and `actual`
  values are copied to the corresponding machine-readable failure fields.
- `type_id`, declaration, and raw value are copied unchanged.
- The token span is shifted by `span_offset`.

#### `_message(failures, patterns)`

Builds a human-readable summary without attempting to replace the structured
fields. For one source pattern, it uses its full `repr`; for several, it
reports the candidate count. Each visible reason includes the raw value,
declaration, and validator message. The final sentence directs the API user to
`failures`, `candidate_patterns`, and `candidate_variations`.

## `__init__.py`: subsystem exports

The module exports:

```python
from vrp_parser.matching import CommandMatcher, ResolvedMatch
```

`__all__ = ["CommandMatcher", "ResolvedMatch"]`.

These exports connect internal project layers. Application code should import
`CommandLineParser` and `ConfigurationParser` directly from `vrp_parser`.

## Backtracking and protection against false crossover

The graph merges identical prefixes from different patterns, but every edge
stores a `frozenset[route_id]`. During a transition, `_walk()` intersects the
edge's set with the set of routes allowed before that transition.

For example, consider this hypothetical graph:

```text
a STRING x
a INTEGER y
```

The shared `a` prefix and graph node do not allow traversal through `STRING`
followed by the terminal of the `INTEGER y` route: after every transition,
only route IDs participating in the entire prefix remain.

Backtracking is preserved at three levels:

- the graph walker tries the literal edge and all expression edges;
- a sequence continues every state from the preceding expression;
- groups and repeats yield every valid choice and count.

Early Pareto pruning is applied only where branches have the same position and
the same continuation. For an unordered set, alternatives are pruned
separately because the selected index changes future possibilities.

## Interpreting the result

### Success

`ResolvedMatch` always contains at least one `PatternMatch`.

- `unique` — one match remains;
- `equivalent` — several sources matched, but their variations and parameters
  are semantically identical;
- `ambiguous` — several incomparable interpretations remain.

Both `equivalent` and `ambiguous` are successful parsing outcomes. Every
variant is available in `ParsedCommand.matches`, and the first one in JSON
order is available in `primary_match`.

### Unknown command

`ErrorCode.UNKNOWN_COMMAND` means that no branch advanced beyond position
zero. Bare/root `TEXT<min-max>` at position `0` does not hide unknown commands:
it is allowed only for lines beginning with `!`. This restriction does not
apply to a remainder parameter after a matched keyword, such as
`description TEXT<1-80>`.

For a literal-led typo, `suggestions` may contain up to five relevant original
patterns, and `message` displays the same list after `"Did you mean:"`. Root
`TEXT` and parameter-first patterns do not become recommendation targets. If
there are no similar literals, the tuple is empty and the message explicitly
explains that no similar commands were found.

### Syntax error

`ErrorCode.SYNTAX_ERROR` means that a prefix matched, but no route accepted the
entire line. `expected` contains the combined expectations at the furthest
position. The message displays the 1-based column, up to five expectations,
and top-five suggestions when available. If a route starting with an
applicable parameter advanced furthest, keyword recommendations are
suppressed.

### Validation error

`ErrorCode.VALIDATION_ERROR` means that at least one complete pattern shape
matched, but no acceptable candidate remained: a validator may have returned
`INVALID` or, when no other branch completed, `NOT_APPLICABLE`. One important
special case is an applicable, more-specific `INVALID` parameter blocking a
less-specific valid fallback. `failures` contains validator tokens, spans, and
messages, while `candidate_patterns`/`candidate_variations` contain every
relevant source. `ValidationFailure.reason_code`, `expected`, and `actual` are
intended for automated diagnostics. `ParseError.message` provides a detailed
English summary, but `suggestions` is always empty.

## Low-level example

This approach is useful in infrastructure tests, but does not replace
`CommandLineParser`:

```python
from vrp_parser.compiler import PatternCompiler
from vrp_parser.matching import CommandMatcher, ResolvedMatch
from vrp_parser.parameters import default_parameter_registry
from vrp_parser.results import ParseError

registry = default_parameter_registry().freeze()
graph = PatternCompiler(registry).compile(
    (
        "preference INTEGER<1-15>",
        "preference STRING<1-20>",
    )
)
matcher = CommandMatcher(graph, registry)

outcome = matcher.match("preference 100")

if isinstance(outcome, ParseError):
    # This is validation_error: INTEGER is applicable but outside the range.
    print(outcome.code, outcome.failures)
else:
    assert isinstance(outcome, ResolvedMatch)
    print(outcome.status, outcome.primary_match)
```

When calling this API directly, the caller is responsible for keeping the
registry and graph consistent, removing indentation, enforcing one physical
line, and supplying the correct `span_offset`.
