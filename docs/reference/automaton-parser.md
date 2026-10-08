# Automaton parser

`vrp_parser_automaton` accepts **one document with prepared command formats**,
compiles it, and parses configurations. Flat parsing remains the default.
For `type: "grouped"`, it uses the views and transitions from the same document.

The parser does not read documentation or a mapping file. Hierarchy preparation
happens before parsing; matching results to documentation is handled by separate
code afterward. `pattern_id`, `slot_id`, and repetition coordinates are preserved
for that purpose.

## Input document

The legacy flat format works unchanged:

```json
{
  "commands": [
    "bgp INTEGER<1-65535>",
    "router-id X.X.X.X",
    "#"
  ]
}
```

You can explicitly set `"type": "flat"` and pass commands as
`{"format": "..."}` objects. Both forms search all formats.

For a recovered hierarchy, save the following as `patterns.json`:

```json
{
  "type": "grouped",
  "entry_view": "system",
  "views": {
    "system": [
      {
        "format": "bgp INTEGER<1-65535>",
        "switch_to_view": "bgp"
      }
    ],
    "bgp": [
      {
        "format": "router-id X.X.X.X"
      }
    ]
  }
}
```

`entry_view` is the initial context; `switch_to_view` is the exact key of the target
view. An omitted or `null` `switch_to_view` preserves the current view.
If an external process could not establish a transition, it must mark it explicitly:

```json
{"format": "interface STRING<1-64>", "switch_to_view": {"status": "unresolved"}}
```

This record preserves the catalog's grouping and all known transitions. Only this
command's child block is parsed without view restrictions. Leaving the block
restores the parent context. You can manually replace the marker with a target view
or `null`; the format and parameter identifiers remain unchanged. Grouping commands
with unknown transitions without these markers does not replace preparation.

Supported declarations include `INTEGER<…>`, `STRING<…>`, `TEXT<…>`, IP addresses,
and other registered types, as well as named parameters `<parameter-name>` in both
flat and grouped catalogs. Without a type annotation, `NamedDeclarationRecognizer`
recognizes `<name>`, `SingleTokenReader` reads one token, and `PassValidator` returns
it without validation or conversion. The result retains `type_id="named"`,
`declaration="<name>"`, `raw`, `normalized`, `span`, `slot_id`, and `iterations`.
The name is available as
`value.declaration[1:-1]`; the result has no extra `metadata` or `parameter_name` fields.

Optional `parameter_types` on each command selects the existing type validators:

```json
{
  "commands": [
    {
      "format": "acl <acl-number>",
      "parameter_types": [
        {"parameter_name": "acl-number", "parameter_type": "integer"}
      ]
    }
  ]
}
```

`acl 001` now yields `type_id="integer"`, `raw="001"`, and `normalized=1`;
`acl invalid` fails validation. Annotations apply in both flat and grouped catalogs,
including matches from unknown or foreign views. They are local to each command:
the same name may have a different type in another command or view.

Supported built-in annotations are `integer`, `string`, `text`, `ipv4-address`,
`ipv6-address`, `ipv6-prefix`, `hex`, `mac`, `passwordex`, `date-slash`, `date-iso`,
`month-day`, `date-us`, `datetime-slash`, `time-seconds`, and `time`. Registered custom
value types can also be selected. The type controls reading, validation, dispatch
priority, and normalization, without inventing numeric bounds or string lengths.
`text` reads the rest of the line and must be terminal and non-repeated, like `TEXT`.
`string` reads one non-whitespace token; quoting does not make it a multiword value.
An `enum` annotation alone is insufficient because it supplies no choices.

Missing annotations and `unknown` preserve the unvalidated named behavior. Duplicate
names, references to absent parameters, malformed lists, and unsupported types fail
at construction. Both the parser and matcher accept annotations covering only some
parameters. Original formats, `pattern_id`, `slot_id`, and repetition coordinates
are preserved, so existing offline mappings remain addressable.

Specialized declarations take precedence: `<hh:mm>` is still validated as a time.
`TEXT<…>` reads the rest of the line, while numeric and other types are validated as
before, including their declared bounds. `<text>` alone, without an annotation,
does not imply reading the rest of the line.

`vendor`, `device`, `model_type`, `source`, `schema_version`, `metadata`, and other
descriptive fields can remain in the input file. The parser does not use them to
select a grammar, check documentation semantics, or evaluate `creates` or `requires`.
The strict [catalog specification v1](command-catalog-format.md) applies to the matcher;
its header requirements do not apply to the runtime parser.

### Exporting documentation formats by view

The test script `manual_group_formats_by_view.py` reads corpus pages and groups
their `CLIs` by the original `ParentView` names:

```bash
python3.13 manual_group_formats_by_view.py \
  --corpus /path/to/cmd_corpus --output documentation_grouped.json
```

It sorts and deduplicates formats within each view and preserves source view names,
including `All views`. The checked-in `documentation_grouped.json` is a CloudEngine
v300r024c00 structural export with 282 views and 36,750 view/format records.
Entries contain only `format`; the script does not infer `switch_to_view`,
`parameter_types`, or command semantics. Prepare transitions before contextual
parsing. Parameter annotations improve type filtering in the matcher but may be
incomplete or absent.
The export itself does not establish a recovered hierarchy.

## Running the parser

Python 3.13+ is required. Run the following commands from the repository root.
For the example above, save this as `config.txt`:

```text
bgp 65000
 router-id 192.0.2.1
#
```

Run manually without installing the package:

```bash
python3.13 manual_automaton_test.py --patterns patterns.json --config config.txt
```

The script prints the format count, state count, and result JSON. Individual lines
can be supplied using repeated `--line` options; together they form one configuration.
`--line` and `--config` are mutually exclusive. If neither is supplied, the built-in
lines for the selected `--case` are used.

Validate a catalog and save plain JSON:

```bash
PYTHONPATH=src python3.13 -m vrp_parser_automaton check-patterns patterns.json

PYTHONPATH=src python3.13 -m vrp_parser_automaton parse \
  --patterns patterns.json --config config.txt > parsed.json
```

Module CLI exit codes: `0` means no line errors; `1` means the report contains line
errors; `2` means an input reading or compilation error. An ambiguous successful
parse is not an error. The manual script does not reflect line errors in its exit code.

The `--flat` flag disables view restrictions while retaining the same catalog.
In flat mode, `#` is an ordinary line and needs its own format.
The parser has no `--mapping` argument.

## Catalogs and context

`ConfigurationParser` starts in `entry_view` and searches for commands only in the
current view. An increase in indentation opens a child block using the previous
command's transition. A decrease restores the parent context. Blank lines do not
change the stack. A root-level `#` separator returns to the initial view.
Depth is the length of the original indentation: with one space per level, this is
`0`, `1`, `2`, and so on. Tabs are not expanded into spaces.

In the example, `bgp 65000` is parsed in `system`, while `router-id 192.0.2.1` is parsed
in `bgp`. A transition does not carry over to the next command at the same indentation:
this handles a configuration file, not an interactive session.

`quit` and `return` have no built-in exit semantics; a group named `global` has no
special meaning. View names come from the input document. If a known view has no
valid match, the parser searches other groups and marks a complete fallback match
as `UnresolvedCommand` rather than assigning its source view to the configuration.

Ambiguous results are preserved. If parse alternatives specify different transitions,
the parser does not choose one: the child block is searched globally with `view=None`
(the JSON omits `view`). The same applies to a block following a failed entry command
or an explicit `{"status": "unresolved"}`. Even a unique syntactic match in such a
block does not establish the view. The parser does not recover hierarchies, select
a target view, or execute parameter-dependent conditional transitions.

## Python API

A complete initialization and execution example is available in
[`examples/parse_configuration.py`](../../examples/parse_configuration.py).
It reads one catalog and one configuration, then saves the report to a separate file:

```bash
PYTHONPATH=src python3.13 examples/parse_configuration.py \
  --patterns runtime_catalog.json \
  --config config.txt \
  --output parsed.json
```

For grouped input, use a prepared catalog with transitions and explicit `unresolved`
markers; for flat input, use an ordinary format catalog. The mode is selected
automatically. The script returns `0` if there are no line errors and `1` otherwise;
it saves the complete report in both cases. It does not need a mapping.

After `python3.13 -m pip install -e .`, or with `PYTHONPATH=src`:

```python
import json
from pathlib import Path

from vrp_parser_automaton import CommandLineParser, ConfigurationParser

parser = CommandLineParser.from_json_file("patterns.json")
report = ConfigurationParser(parser).parse(
    Path("config.txt").read_text(encoding="utf-8")
)
Path("parsed.json").write_text(
    json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

The catalog is compiled once and can be reused for multiple files.
Each configuration parse starts with a fresh stack.

```python
line = parser.parse("bgp 65000")  # entry view
line = parser.parse("router-id 192.0.2.1", view="bgp")
line = parser.parse_flat("router-id 192.0.2.1")
flat_report = ConfigurationParser(parser, contextual=False).parse("bgp 65000")
```

The line-level `parse()` accepts one physical line without a line terminator and
does not retain context between calls. Its `view=None` means the entry view;
use `parse_flat()` to search the entire catalog.

`CommandLineParser(document)` and `from_json(text)` are also available for loading.
The `parameter_types=` argument still accepts a custom type registry.

## Results and subsequent processing

```python
from vrp_parser_automaton import ErrorLine, ParsedCommand, UnresolvedCommand

for line in report.lines:
    if isinstance(line, UnresolvedCommand):
        print("Unconfirmed context:", line.context_issue.message)
        # Its captures remain available through the ParsedCommand interface.
    if isinstance(line, ParsedCommand):
        print(line.line_number, line.view, line.status)
        for match in line.matches:
            print(match.pattern_id, match.original_pattern)
            for value in match.parameters:
                print(value.slot_id, value.iterations, value.raw, value.normalized)
    elif isinstance(line, ErrorLine):
        print(line.line_number, line.error.code, line.error.message)
```

A successful line's `status` is `unique`, `equivalent`, or `ambiguous`.
`primary_match` and `alternative_matches` preserve the remaining equally ranked
alternatives. `line.parameters` contains parameters from the first alternative only;
for external matching of ambiguous results, iterate over `line.matches`.

`pattern_id` identifies the original format, `slot_id` identifies the parameter's
position in that format, and `iterations` identifies an occurrence within repetitions.
`span` locates the value in the original line, including indentation. Formats and
the ordering of their duplicates must match those used to calculate the offline mapping.

After parsing, external code can look up `mapping["devices"][match.pattern_id]` and
its `mappings`. The parser does not load this JSON or attach bindings and semantics
to lines. The relation format is described in the
[matcher documentation](automaton-format-matcher.md#linking-json-to-parser-results).

A line has a `view` only when the context is known. In Python, an absent context is
`None`; in JSON, the key is omitted. `kind` is one of `command`, `unresolved_command`,
`error`, `blank`, or `separator`. Separators count toward `summary.total`, but not
`commands`, `blank`, or `errors`. `summary.errors` and `has_errors` report line errors.
`summary.commands` includes `UnresolvedCommand` instances because their syntax was
recognized. `summary.unresolved` counts this subset, and `has_unresolved` indicates
whether it is nonempty. JSON omits a zero `unresolved` count to preserve the flat
report shape. A file with unresolved commands but no errors retains CLI exit code 0;
check `has_unresolved` or the JSON count when confirmed context is required.
One error does not stop parsing subsequent lines.

### Context diagnostics

When a command does not match in the selected view, the parser searches the global
automaton using its entry index, excluding that view's formats before recognition
and ranking. A valid generic format from the same view cannot turn its validation
error into an outside-view match. A complete valid match in another view produces an
`UnresolvedCommand`, with the best match in `primary_match` and equally ranked
alternatives in `alternative_matches`. Existing specificity rules rank the valid
matches; source order selects the representative among ties. A rejected format in
one view cannot suppress a valid fallback from another view. Prefixes, suggestions
and failed parameter validations do not become successful matches.

`UnresolvedCommand` subclasses `ParsedCommand`: `parsed` is true and the usual
parameters, spans, slot IDs and repetition coordinates are available. Its `kind`
is `unresolved_command`, its `view` is `None`, and `context_issue` explains the
unconfirmed context. For dispatch, test this subclass before `ParsedCommand` when
the two cases need different handling. It uses the matched source pattern IDs;
external mapping lookups can still retrieve their original source views.

If no complete valid match exists in another view, the result remains `ErrorLine`
with the original scoped `unknown_command`, `syntax_error`, or `validation_error`, and scoped
suggestions. The compatibility field `error.catalog_matches` is empty in this case.
This does not prove that a format is missing: the input may have a syntax or value
error. Ordinary flat errors continue to omit that field.

When a child block has an unknown view, its command and error results carry
`context_issue` with a `code`, an English `message`, and `source_line` pointing to the
command that made the context unknown:

| Code | Cause |
|---|---|
| `parent_parse_error` | The parent command could not be parsed |
| `unresolved_transition` | A parent match declares an unresolved transition |
| `ambiguous_transition` | Parent matches specify different known target views |
| `outside_view` | A command failed in the selected view but matched in other views |

The cause is preserved through deeper nesting, including successful flat fallback
parses and further errors. Leaving the affected block restores the known context.
A successful fallback uses `UnresolvedCommand` and still counts as a parsed command.
Even a single foreign match does not establish a view or apply its `switch_to_view`:
its child block retains the original context issue. An omitted or null `switch_to_view` still means
stay: the parser cannot infer that this declaration was incorrect.

Ordinary flat catalogs and explicit `contextual=False`/`parse_flat()` calls omit
these diagnostic fields from JSON and keep `ParsedCommand` for successful matches.
Known-context successes omit `context_issue` too.

## Construction and execution

Constructors, normalization, spans, type priorities, and parameter registry extension
are preserved. Import all classes from `vrp_parser_automaton`: copied classes are
not identical to their counterparts in `vrp_parser`. `command_graph` is a compatibility
alias for `automaton`; it returns `CommandAutomaton` with `patterns`, `states`,
`starts`, `literal_starts`, and `parameter_starts`. The old `routes`, `literal_edges`,
and `expression_edges` are absent. `variation_id` is deterministic within the new
engine but may differ from the original parser: its history is based on the AST and
repetition coordinates.

```text
Formats → PatternParser → AST → PatternCompiler → CommandAutomaton
                                                     ↓
Line → CommandMatcher → active configurations → results / diagnostics
```

This is a compact ε-NFA with set and repetition registers. It does not build a full
DFA. Compilation does not enumerate commands, routes, set subsets, or copies of
repetition bodies. `Instruction.target` and `branches` contain state numbers.
Atomic transitions retain the original AST nodes, including parameter spans.

| Construct | Representation |
|---|---|
| Literal / parameter | A consuming transition to the next state |
| Sequence | Connected fragments |
| `{ A \| B }` | A branch and a shared exit |
| `[ A ]` | A branch that can be skipped |
| `{ A \| B } *`, `[ A \| B ] *` | A selection loop with a mask of already used branches |
| `&<m-n>` | One body, a counter, and bounds checks |

A shared suffix after alternatives is stored once per pattern. Different source
patterns have their own fragments; merging their shared prefixes is not implemented
yet. The catalog uses an index of allowed first literals and also considers patterns
that begin with a parameter.

The executor uses a queue ordered by position in the input line. It processes the
ε-transitions at a position first, then the consuming transitions. Nested expressions
do not invoke the matcher recursively: nesting is kept in a stack of immutable
`Frame` objects. Each new set occurrence starts with an empty mask. A repetition
iteration or selected set branch counts only if it consumes input.

`ConfigurationFrontier` compares paths only when state, position, and registers
agree. It preserves incomparable typed interpretations and accounts for `INVALID`
and `NOT_APPLICABLE`. Parameter readers can consume multiple tokens: paths with
different priority-vector lengths are not discarded prematurely before a shared
continuation.

The compiled structure is linear in the number of AST nodes. This does not promise
linear parsing time: ambiguous branches, different parameter assignments, and set
states can generate many configurations. There is no 512-route limit or execution
strategy switch.

Error suggestions run a separate bounded search over the automaton, with typo
scoring. Their budget does not limit command recognition. Rankings for complex
suggestions may differ from those of the old engine.

## Code organization

| File | Responsibility |
|---|---|
| `api.py` | Public line and configuration parser facades |
| `catalogs/` | Reading one flat/grouped document |
| `context/` | View indexes and a block stack based on prepared transitions |
| `automata/sources.py` | Source patterns, stable IDs, and grammar errors |
| `automata/compiler.py`, `automata/building.py` | AST compilation in a fresh workspace |
| `automata/model.py`, `automata/first_tokens.py` | Instructions and the entry index |
| `runtime/execution.py` | Configurations, stack, and choice, set, and repetition transition objects |
| `runtime/recognition.py`, `runtime/worklist.py` | One recognition run and its configuration queue |
| `runtime/matcher.py` | Combining recognition, candidate resolution, and diagnostics |
| `runtime/atoms.py`, `runtime/frontier.py` | Reading parameters and safely reducing work |
| `runtime/resolution.py`, `runtime/matches.py` | Public matches and ambiguity |
| `diagnostics/` | Structured errors, similarity, and bounded suggestion search |
| `patterns/`, `parameters/` | Independent copies of the grammar and parameter subsystem |

The top-level `api.py`, `results.py`, `errors.py`, `serialization.py`, and `__init__.py`
retain the package's public boundary. Internal implementation is split across
`automata/`, `runtime/`, and `diagnostics/`. The main imports are unchanged:
`from vrp_parser_automaton import CommandLineParser`.

Transition objects operate on a specific instruction and configuration. Mutable
queues, the compiler workspace, and candidate sets are scoped to a single run.
The automaton and execution states themselves are immutable.

## Manual checks

From the repository root, without installing the package:

```bash
python3.13 manual_automaton_test.py
python3.13 manual_automaton_test.py --case wide-set
python3.13 manual_automaton_test.py --case large-repeat
python3.13 manual_automaton_test.py --case optional-chain
python3.13 manual_automaton_test.py --patterns data/commands.json --line 'display clock'
```

The script prints the state count and full result JSON. Its first example is the
previously discussed `ip route-static` with typed parameters. Examples also include
intentionally invalid lines, such as a repeated `tag` or a repeated set branch.

| Example | States |
|---|---:|
| Complex `ip route-static` from the script | 56 |
| Optional set of 24 branches with parameters | 53 |
| Parameter repeated with `&<1-100000>` | 6 |
| 40 independent optional groups with a prefix and suffix | 123 |

Entire current catalog: 7,269 patterns, 45,222 states.

Tests in `tests/automaton/` cover the ported API contracts, parameters, diagnostics,
CLI, and catalog, along with automaton structure, independence from the original
package, wide sets, large repetitions, and the complex static route. Context,
catalogs, and identifier preservation are tested in `tests/test_automaton_context.py`.
Format matching is handled by a separate package,
[`vrp_format_matcher`](automaton-format-matcher.md). It uses the same AST compiler
and control transitions for general language comparison. Identical structures are
linked directly through their original AST nodes. Results include parameter
correspondences, statuses, and matching scopes; grouped catalogs also include
prepared view information. The matcher does not evaluate predicates.
