# Offline format and metadata matching

The `vrp_parser.metadata` package compares documentation and device formats,
precompiles parameter correspondences, and applies prepared rules to
`CommandLineParser` results. The existing parser is unchanged.

The first version focuses on format matching. Predicates are implemented as a small
supporting mechanism, without an expression language or `eval`.

## Code organization

The public entry points are `MetadataCompiler` and `PreparedMetadata` from
`vrp_parser.metadata`. Internally, responsibilities are split among small objects:

| File | Responsibility |
|---|---|
| `documents.py` | `Documentation` reads a catalog; `DocumentSource` and `DocumentRules` validate records |
| `patterns.py` | Named documentation parameters and structural AST properties |
| `programs.py` | A compact program compiled from the AST and prebound programs for equivalent structures |
| `determinism.py` | Conservative determinism checks for safe direct mapping |
| `execution.py` | Program execution with set masks and repetition counters |
| `comparison.py` | Language comparison, intersection, and removal of useless states |
| `compiler.py` | `CompiledPattern` caches compilation; `PairPreparation` prepares one format pair |
| `artifacts.py` | `MetadataArtifact` and `ArtifactReader` save JSON version 2 and load versions 1 and 2 |
| `prepared.py` | `PreparedMetadata` provides the public API for prepared results |
| `bindings.py` | `CommandAtoms`, `BindingPath`, and `BindingSearch` recover bindings |
| `rules.py` | `MetadataRule` applies a rule and checks agreement between alternatives |
| `runtime.py` | `PairEvaluation` connects automaton execution to rule evaluation |

Configurations and values are mostly immutable objects. Mutable queues and
intermediate graphs are scoped to one algorithm run. Traversal states have named
fields instead of nested positional tuples. Pure functions remain where they are
simpler than objects, for example when comparing AST structure.

## Manual execution

From the project root, without installing the package:

```bash
python3.13 manual_metadata_test.py
python3.13 manual_metadata_test.py --case conditional
python3.13 manual_metadata_test.py --case ambiguous
python3.13 manual_metadata_test.py --case overlap
python3.13 manual_metadata_test.py --case prefix
python3.13 manual_metadata_test.py --case set
python3.13 manual_metadata_test.py --case wide-set
python3.13 manual_metadata_test.py --case wide-optional-set
python3.13 manual_metadata_test.py --case large-repeat
```

Edit patterns, metadata, and input lines in the script's `CASES` dictionary.
The default example is `port trunk allow-pass vlan`, including reordered branches,
ranges, repetitions, a separate documentation record without `all`, and an invalid
VLAN value.

The script shows the language relation, AST structural equality, examples of shared
and nonshared structural commands, possible source parameter correspondences,
concrete bindings of captured values, and rule results.
`STRATEGY: structural` means a compact program with direct bindings;
`intersection` means a prepared intersection. Programs report instruction counts;
intersections report state counts.

`wide-set` and `wide-optional-set` exercise 24 reordered branches with parameters:
75 instructions each, without enumerating subsets. `large-repeat` exercises
`&<1-100000>`: 4 instructions, with repetition coordinates calculated during execution.

```bash
# Custom data: commands JSON and an array of documentation records.
python3.13 manual_metadata_test.py \
  --patterns device.json --documents documents.json \
  --line 'port trunk allow-pass vlan 10 to 20 30'

# Preparation happens only here.
python3.13 manual_metadata_test.py --case vlan --save /tmp/vlan-mapping.json

# Load the prepared binding automaton. Documentation formats are not parsed.
# The artifact supplies device formats for the ordinary parser.
python3.13 manual_metadata_test.py --load /tmp/vlan-mapping.json \
  --line 'port trunk allow-pass vlan 10 to 20 30'
```

`--line` can be repeated. `--json` prints complete runtime results.
With custom files or `--load`, omitting `--line` only displays the prepared
correspondences. `--save` writes the prepared JSON.

## Input documents

`documents.json` contains an array, for example:

```json
[
  {
    "id": "vlan-allow",
    "format": "port trunk allow-pass vlan { all | { <vlan-id1> [ to <vlan-id2> ] }&<1-10> }",
    "creates": [],
    "requires": [
      {
        "when": "always",
        "condition": "parameter_name",
        "parameter_name": "vlan-id1",
        "entity_type": "vlan"
      },
      {
        "when": "always",
        "condition": "parameter_name",
        "parameter_name": "vlan-id2",
        "entity_type": "vlan"
      },
      {
        "when": "always",
        "condition": "command",
        "context": "current",
        "command": "port link-type trunk"
      }
    ]
  }
]
```

`id` is optional; it defaults to `doc:<index>`. Identifiers must be unique within one
compilation. `creates` and `requires` may be omitted: format comparison and binding
retrieval work without rules.

Parameter names use `<name>`: an initial letter or `_`, followed by ASCII letters,
digits, `_`, `-`, `.`, or `:`. Different occurrences of the same name retain distinct
node identifiers. A rule referring to that name applies to all corresponding captures.

## API

```python
from pathlib import Path

from vrp_parser import CommandLineParser, ParsedCommand
from vrp_parser.metadata import MetadataCompiler, PreparedMetadata

parser = CommandLineParser({
    "commands": ["command INTEGER<1-100> [ to INTEGER<1-100> ]"]
})
prepared = MetadataCompiler().compile(parser, [{
    "format": "command { <a> | <b> to <c> }",
    "requires": [{
        "condition": "parameter_name",
        "parameter_name": "b",
        "entity_type": "vlan",
        "when": "always",
    }],
}])

pair = prepared.pairs[0]
print(pair.comparison.relation)  # equivalent
print(pair.comparison.common_example)

Path("/tmp/prepared.json").write_text(prepared.to_json(), encoding="utf-8")
runtime = PreparedMetadata.from_json(
    Path("/tmp/prepared.json").read_text(encoding="utf-8")
)

line = parser.parse("command 10 to 20")
if isinstance(line, ParsedCommand):
    report = runtime.evaluate(line)
    print(report.to_dict())
```

Each `PreparedPair` represents a specific pair of documentation and device patterns.
Its `recognizer` returns either a compact `program` or an `automaton` graph.
Both representations store parameters that are already linked: documentation and
device identifiers, names, and declarations. In a program, repetition coordinates
are added during execution. Source strings are needed for diagnostics; the runtime
does not parse the documentation format.

## What is compared

The structural language alphabet consists of:

- `K:<word>`: a literal converted to ASCII lowercase;
- `P`: a parameter, regardless of its name, type, range, or ENUM values.

`P` does not match a literal. Thus `<mode>` and `ENUM{access,trunk}` are structurally
compatible, but `{ access | trunk }` describes a different language structure.
Every parameter, including `TEXT`, is one structural atom. During execution, `TEXT`
receives the entire value captured by the parser.

Group grammar comes from the existing `PatternParser`. Sets marked with `*` retain
unique-alternative semantics, and repetitions and set members count only if they
consume at least one atom.

The `Comparison` result:

| `relation` | Meaning |
|---|---|
| `equivalent` | The structural languages are equal |
| `document_subset` | The documentation language is a strict subset of the device language |
| `device_subset` | The device language is a strict subset of the documentation language |
| `overlap` | There are shared commands and commands exclusive to each side |
| `prefix_only` | There are no shared complete commands, but there is a nonempty common prefix |
| `disjoint` | There are no shared complete commands or nonempty common prefixes |
| `unknown` | Preparation did not finish because of a resource limit |

`structurally_identical` additionally compares trees with anonymized parameters.
Reordering branches changes this flag but does not change language equality.

`common_example`, `document_only_example`, and `device_only_example` are the shortest
witnesses found in the structural alphabet. `<PARAM>` denotes a parameter position,
not a suggested real value. `common_prefix` is a diagnostic example of a shared
prefix, not a guarantee of the globally longest prefix. Metadata is not transferred
automatically for `prefix_only`.

## Preparation algorithm

1. Both ASTs are compiled into compact programs without `RouteExpander`. A set stores
   alternatives; a repetition stores its body and bounds. Branch subsets and body
   copies are not created in advance.
2. A canonical key ignores parameter names/types and alternative order while
   preserving nesting, branch multiplicity, and repetition bounds. Equal keys prove
   structural language equivalence without traversing configurations.
3. If normalized tokens uniquely determine the path, a compact program with parameter
   bindings is built (`strategy="structural"`). The determinism check is conservative:
   it considers branch starts, empty alternatives, and sequence and repetition
   boundaries. For example, `[<a>] [<b>]` does not allow this simplification: one
   parameter can belong to different nodes.
4. For different structures, languages are compared by lazily traversing pairs of
   configuration sets. Sets use selected-branch masks; repetitions use counters.
   Only visited configurations are created. Three witnesses, a shared command and
   a command exclusive to each side, immediately prove `overlap`.
5. If direct mapping is impossible and the intersection is nonempty, a graph of
   synchronous consuming transitions is built (`strategy="intersection"`).
   Independent ε-transitions are not multiplied. All valid bindings are retained,
   and paths that do not lead to a complete match are removed.

The runtime executes the already bound result rather than matching formats again.
In a compact program, the mask prevents reselecting a branch only within the current
set; a new outer repetition iteration starts that set with an empty mask.

Bindings are not reduced to an unconditional table. For example, in
`command { <a> | <b> to <c> }`, the first device parameter corresponds to `a` or `b`
depending on the complete continuation of the command. This condition is already
encoded in the prepared automaton.

Identifiers `p:<position>` and `r:<position>` refer to original parameter and
repetition positions in their respective formats. They are valid within a specific
pattern version. Repetition indices are zero-based; nested repetitions are represented
by a chain of coordinates. Rebuild the artifact after changing formats.

## Execution without parser changes

The runtime uses `ParsedCommand.matches`, the original line, and parameter spans.
It reconstructs only atom boundaries: literals and already validated captures.
The prepared automaton executes over this sequence. Runtime execution does not
calculate intersections or match formats.

Comparing a concrete device value against its own parameter declaration during
execution preserves the typed interpretation selected by the parser. This is not a
type correspondence table between documentation and device formats.

An additional traversal is needed because the main parser may merge parses that
assign parameters to different source groups. For example,
`command [ INTEGER<1-100> ] [ INTEGER<1-100> ]` currently returns one match for
`command 10`. The prepared automaton retains both bindings. This traversal could
later be integrated with matcher events.

A separate `MetadataApplication` is returned for each pair and each successful
`PatternMatch`. Results from different documentation records or alternative device
interpretations are not merged into an unconditional list.

- `binding_status`: `unique`, `ambiguous`, or `unavailable`;
- `alternatives`: complete alternative binding sets and their effects;
- `rules`: each rule's result, `active`, `inactive`, or `ambiguous`;
- `status`: `applied`, `inactive`, `ambiguous`, `not_applicable`, or `unknown`.

A rule can be unambiguous even when bindings are ambiguous: for example, all
interpretations produce the same dependency for the same value at the same line
position. If interpretations disagree, `RuleEvaluation.effects` is empty, and the
possible effects remain in `alternatives`. A true predicate does not select one of
the ambiguous interpretations.

## Minimal predicates

Supported forms include `"always"`, JSON booleans, and objects:

```json
{"op": "exists", "parameter": "vlan-id2"}
```

```json
{"op": "gt", "parameter": "vlan-id1", "value": 10}
```

Available operators are `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `all`/`any` with an `args`
array, and `not` with an `arg` field. A comparison is true if at least one applicable
value satisfies it. Missing values produce `false`. For a parameter rule, lookup
is restricted to compatible repetition coordinates; command rules can access all
captures.

Metadata is returned as data. The module does not check whether a VLAN exists,
interface state, `context: current`, or execution of a required command.
A rule for an absent parameter produces no effect. The `all` alternative has no VLAN
parameter captures. The range `10 to 20` remains two captures; VLANs within the range
are not enumerated.

## First-version limitations

- Preparation compares every pair of supplied formats; for interactive use, supply
  a selected catalog. A candidate index is not implemented yet.
- Compact storage does not eliminate the complexity of comparing arbitrary languages.
  Wide sets with different structures or ambiguous branches may still require
  exponential traversal. Exceeding a limit produces `unknown`.
- `MappingLimits` field names are preserved: `automaton_states` limits source program
  instructions and structural witness length; `comparison_states` limits pairs of
  configuration sets; `product_states` limits intersection states or bound program
  instructions; `runtime_configurations` limits execution configurations with capture
  histories. Traversal limits also bound the work of one ε-closure. Defaults are
  20,000. A structural proof does not consume the comparison budget.
- State counts are not strict bounds on total memory or execution time.
- Version 2 artifacts are saved; legacy version 1 graphs can be read. This is an
  application format, not a standard automaton export. Treat artifacts as trusted
  and do not modify their internal structures after preparation.
- Preparation and runtime must use compatible device parameter type definitions.
  The artifact does not contain custom validator implementations.
- Syntactic equivalence does not prove that different commands have identical
  semantics or that requirements extracted from documentation are correct.
