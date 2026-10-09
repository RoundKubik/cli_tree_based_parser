# Offline matching: device → documentation

For **each device format**, `vrp_format_matcher` finds suitable documentation
formats and parameter correspondences. The result is saved as ordinary JSON.
The matcher uses `parameter_types`, does not evaluate predicates, and does not read
`requires`/`creates`. The calling code handles their subsequent processing.

## Default workflow: system and non-system scopes

Use the existing grouped device and documentation catalogs directly. Their JSON
format does not change. Each catalog's `entry_view` identifies its system group.
Commands in that group match only the other catalog's system group. Every remaining
group is searched together, excluding system. No shared scopes or cross-scope
fallback are used. `switch_to_view` and `shared_views` are ignored in this mode.

```python
import json
from pathlib import Path
from vrp_format_matcher import FormatMatcher

device = json.loads(Path("device_grouped.json").read_text())
documentation = json.loads(Path("documentation_grouped.json").read_text())
mapping = FormatMatcher().compile_catalogs(device, documentation)
Path("mapping.json").write_text(
    json.dumps(mapping.to_dict(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
```

The parser then consumes **the same original device catalog**, without a recovery
step. `prepare_catalogs()` is also supported: it returns an unchanged copy of the
target catalog plus the same mapping. It does not recover transitions or emit a
`hierarchy` section by default. Formats, record ordering, IDs and slots are preserved.
Flat or mixed inputs retain global matching; documentation-to-documentation works too.

```bash
PYTHONPATH=src python3.13 -m vrp_format_matcher \
  --patterns device_grouped.json --documents documentation_grouped.json \
  --save mapping.json --summary
```

All four matching cases and parameter type checks remain in use within the selected
scope. [Semantic lookup](semantic-lookup.md) explains how to retrieve `creates` and
`requires` after parsing, including alternative matches and partial bindings.

## Compute and save

```python
import json
from pathlib import Path

from vrp_format_matcher import FormatMatcher

formats = ["vlan { INTEGER<1-4096> [ to INTEGER<1-4096> ] } &<1-10>"]
documents = [
    {
        "id": "vlan-range",
        "format": "vlan { <first> [ to <last> ] } &<1-10>",
        "parameter_types": [
            {"parameter_name": "first", "parameter_type": "integer"},
            {"parameter_name": "last", "parameter_type": "integer"},
        ],
    }
]
result = FormatMatcher().compile_formats(formats, documents)
Path("mapping.json").write_text(
    json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
```

If a `CommandLineParser` already exists, call
`FormatMatcher().compile(parser, documents)`. This uses the parser's type registry.
To compare two corpora with named `<placeholder>` parameters, use
`compile_formats(..., target_syntax="document")`: literals such as `YYYY-MM-DD`
will then not be interpreted as device types.

## Flat and grouped catalogs

`compile_catalogs(device_catalog, documentation_catalog)` accepts two objects
following [specification v1](command-catalog-format.md). Each may be flat or grouped.
The target catalog's syntax is selected by `source`, so documentation-to-documentation
comparisons are also supported.

```python
device = json.loads(Path("device_grouped.json").read_text(encoding="utf-8"))
documentation = json.loads(
    Path("documentation_grouped.json").read_text(encoding="utf-8")
)
result = FormatMatcher().compile_catalogs(device, documentation)
data = result.to_dict()
```

Matching considers structure, type compatibility, and the two search scopes.
If both catalogs have `type="grouped"`, a command from `device.entry_view` searches
only within `documentation.entry_view`. These groups may have
different names; the initial pair is defined by `entry_view`, not names such as
`system`/`System view` or the order of groups in JSON.

The restriction applies before pair comparison, including prefix fallback.
For example, consider device format `acl INTEGER<2000-2999>` in the entry view:

```text
documentation, System view:      acl [ number ] <acl-number>
documentation, GRPC server view: acl <acl-number>
```

With `documentation.entry_view="System view"`, the intersection with the first record
is selected. The exact gRPC match does not participate in the search. If nothing is
found in these scopes, the search does not expand to other concrete views.

Other device views search all non-system documentation groups together. There is
no fallback into the documentation entry view. Search is global if at least one
catalog is flat. `matched` confirms a format relationship, not a concrete view or
semantic interpretation. Hierarchy recovery is available only through explicit
`FormatMatcher(context_mode="hierarchy")`.

The search scope is part of the grouping key for identical target formats: global
search results are not reused for entry-view commands. ASTs and programs are still
reused. A record's membership in the entry pair is visible through `source.view`
and `catalogs.*.entry_view`.

A `source` is added to `devices[pattern_id]` and `documents[document_id]`:

```json
{"view": "System view", "index": 2}
```

This addresses `catalog["views"]["System view"][2]`. For a flat catalog, `view` is
`null`, and `index` points to `catalog["commands"][index]`. The index is zero-based
and refers to the original array, not the list of matches. Identical strings from
different views and duplicates within one group have separate internal IDs and
locations. Input v1 records do not contain `id`.

```python
pair = result.pairs[0]
location = data["documents"][pair.document_id]["source"]
records = (
    documentation["commands"]
    if location["view"] is None
    else documentation["views"][location["view"]]
)
source_command = records[location["index"]]
# Original creates/requires, parameter_types, and switch_to_view are available here.
```

The same locations are available in Python through the `view` and `index` fields of
`result.device_catalog.entries[pattern_id]` and
`result.documentation_catalog.entries[document_id]`. Catalog headers, including
vendor/device/model_type, are stored once in `data["catalogs"]["device"]` and
`data["catalogs"]["documentation"]`. Original records with semantics are not duplicated
in the result. Resolving these locations requires the same input catalogs with the
same record order.

All device records, including unmatched ones, retain their locations. As before,
the JSON `documents` section contains only records that participate in the result.
The legacy `compile()` and `compile_formats()` methods retain their existing JSON schema.

## View relations and documentation transitions

With `FormatMatcher(context_mode="hierarchy")` and two grouped catalogs,
`compile_catalogs()` prepares
`result.hierarchy` and adds a `hierarchy` section to JSON. Documentation →
documentation is also supported. For flat or mixed inputs, `hierarchy` is `None`
in Python and absent from JSON.

```python
result = FormatMatcher(context_mode="hierarchy").compile_catalogs(device, documentation)
hierarchy = result.hierarchy
assert hierarchy is not None
evidence = hierarchy.evidence

for link in evidence.command_links:
    print(link.device.view, link.documentation.view)
    print(link.pair.pattern_id, link.pair.document_id)
    print(link.transition.kind, link.transition.target_view)
    # link.pair is the original PreparedPair, including bindings and automaton.

for link in evidence.view_links:
    print(link.device_view, link.documentation_view)
    # link.commands references the corresponding command_links.

for doc_view, target in hierarchy.targets.items():
    print(doc_view, target.status, target.device_view)
    print([link.device_view for link in target.candidates])
```

A separate `HierarchyAnalysis(device, documentation, result).collect()` remains
available in `vrp_format_matcher.hierarchy`. Its `.resolve()` method returns the same
`PreparedHierarchy` as the automatic `compile_catalogs()` stage.

`command_links` contains all `exact` and `reordered` pairs and completed intersections
of full commands. Commands without parameters are retained. `prefix`, unfinished
intersections, and absent matches do not create relations. If the intersection is
ready but language-relation refinement exhausts its limit, a pair with status
`matched` is still used.

`transition.kind` reflects only the documentation record:

| `switch_to_view` | `kind` | `target_view` |
|---|---|---|
| String | `switch` | Original documentation view key |
| `null` | `stay` | `None` |
| `{"status": "unresolved"}` | `unknown` | `None` |
| Field omitted | `unknown` | `None` |

A transition is associated with the applicability scope of **a specific pair**.
For `intersection`, this means the accepted complete paths of `link.pair.automaton`,
with original slot IDs and repetition coordinates. A structural pair applies to the
entire matched structure. Collection does not evaluate predicates or apply
transitions to runtime commands.

View names are used only as local references. `view_links` groups command evidence
without weights, rankings, or selection of a single view. Multiple transition
targets, as well as `stay` and `unknown` from different documentation records, are
preserved separately. A target may be an empty group. An absent relation between
groups does not prove incompatibility.

The original catalogs with the same formats, types, and record order are required.
The collector checks headers, locations, and the format strings being used. ASTs and
automata are not rebuilt; bindings and graphs are not copied.

## Resolving transition targets

This section describes the optional hierarchy mode of `compile_catalogs()`. `prepare_catalogs()`
additionally resolves targets through full coverage and known transitions;
`hierarchy.resolved_views` shows the final correspondences.

The collector indexes `view_links` by documentation view once. For each `switch`
target, it retains every discovered relation to a device view. These are positive
observations, not an exhaustive list of possible targets. Names are not compared
across catalogs, and match counts are not used for selection.

A target has two possible states:

- `resolved`: the target documentation view equals `documentation.entry_view`;
  the established entry pair provides `device.entry_view`;
- `unresolved`: any other documentation view. Zero, one, or multiple discovered
  candidates are preserved without automatic selection. An empty documentation
  group also remains unresolved unless it is the entry view.

The status applies only to the **target**, not to the validity of transferring the
entire command. Even with a known target, the original pair, its applicability scope,
and source context correspondence remain relevant. The result has no global
"hierarchy fully recovered" flag: the discovered alternatives do not prove that.

If a target catalog record already specifies a string or `null` `switch_to_view`,
it is retained in `hierarchy.declared_transitions[pattern_id]`: a string is the
original device view; `None` means explicit context preservation. An absent key
means there is no established declaration; an `unresolved` marker is not a known
transition either. These data are retained even for commands without documentation
matches, including target catalogs with `source="documentation"`. Documentation
alternatives do not overwrite them and remain visible as separate records.

## Hierarchy representation in JSON

```text
hierarchy
  entry_views: {device, documentation}
  view_links[]
    device_view
    documentation_view
    mappings: {pattern_id: [mapping_index, ...]}
  targets
    <documentation view>
      status: resolved | unresolved
      candidates: [indices into hierarchy.view_links]
      device_view: established target; only when resolved
  transitions
    <pattern_id>[]
      mapping_index
      kind: switch | stay | unknown
      target: key in hierarchy.targets; only for switch
  declared_transitions: {pattern_id: device_view | null}  # If specified in the input.
  resolved_views: {device_view: documentation_view}  # After prepare_catalogs().
```

`mapping_index` points to `devices[pattern_id].mappings[mapping_index]` in the same
JSON. This record provides `document_id`, bindings, and `automaton_id`. Both formats'
`source.view` and `source.index` are already in the main tables. References are valid
for the saved result with its mapping order unchanged.

Targets and view evidence are stored once, even when many entry commands reference
them. `hierarchy` contains no copies of formats, bindings, slot IDs, or graphs.
To serialize it, use `result.to_dict()`, not `dataclasses.asdict(result)`.

Example of reading a transition's applicability scope:

```python
data = result.to_dict()
for pattern_id, effects in data["hierarchy"]["transitions"].items():
    for effect in effects:
        pair = data["devices"][pattern_id]["mappings"][effect["mapping_index"]]
        scope = data["automata"].get(pair.get("automaton_id"))
        if effect["kind"] == "switch":
            target = data["hierarchy"]["targets"][effect["target"]]
            # target.status == unresolved: the device view has not been established.
```

`stay` and `unknown` are not merged. Different effects from multiple documentation
records retain their own `mapping_index`. Commands with `prefix`, unfinished
comparison, or no documentation may have no entry in `transitions`; an absent entry
does not mean `stay`. Remaining unknown comparisons are visible in the main
`devices.*.mappings` lists.

This JSON is intended for external postprocessing. The runtime parser accepts one
format file; for grouped input, all transitions must already be prepared in that
file. The parser has no `mapping=` argument. After parsing, external code connects
`pattern_id`, `slot_id`, and repetition coordinates to this mapping.
See [parser input](automaton-parser.md#input-document).

## Parameter type compatibility

The mapping is defined in `comparison/parameter_types.py`:

| Device declaration | Documentation type |
|---|---|
| `INTEGER<min-max>` | `integer` |
| `STRING<min-max>` | `string` |
| `TEXT<min-max>` | `string` |
| `X.X.X.X` | `ipv4-address` |
| `X:X::X:X` | `ipv6-address` |
| `X:X::X:X/M` | `ipv6-prefix` |
| `HEX<min-max>` | `hex` |
| `H-H-H` | `mac` |
| `PASSWORDEX<min-max>` | `passwordex` |
| `YYYY/MM/DD`, `YYYY-MM-DD` | `date-slash`, `date-iso` |
| `MM-DD`, `MM-DD-YYYY` | `month-day`, `date-us` |
| `YYYY/MM/DD,HH:MM:SS` | `datetime-slash` |
| `HH:MM:SS`, `<hh:mm>` | `time-seconds`, `time` |

If both types are known and differ, the parameters are not linked. Numeric ranges
and string lengths are not compared. Documentation `text` normalizes to `string`.
`ipv6-prefix` and `ipv6-address` remain different categories.
An unmapped device type, missing annotation or explicit documentation `unknown`
allows matching without a type check; this does not establish semantic compatibility.
Formats containing such parameters cannot prove view coverage or whole-format
transitions. They remain in the result with their available bindings. An unknown
competitor blocks selection of another view as the unique target.

`ENUM{...}` currently falls into this unmapped category: a nonempty enumeration
can bind to a documented `string`, with its original `enum` slot type retained.
Its choices are checked by the runtime parser, not compared by the matcher.
Consequently that binding alone cannot prove hierarchy coverage. An empty
`ENUM{}` is an invalid declaration.

`parameter_types` is optional in documentation commands for both v1 catalogs and
`compile()`/`compile_formats()` inputs. A supplied array may be empty or cover only
some names: missing annotations mean unknown types. Known types still filter their
own parameter correspondences. Unsupported types, malformed entries, duplicates,
and references to absent parameters raise `FormatError`.
Documentation-to-documentation comparison uses available annotations from both
sides. Input records are not rewritten to insert `unknown` entries.
`compare(doc, device)` accepts only strings and does not use external annotations.

The check applies during structural matching and automaton traversal. If structure
matches but types do not, search continues with branch reordering and then
intersection. The intersection retains bindings only from productive compatible
paths. During prefix search, an incompatible parameter stops the shared trace.

`slot_id`, original declarations, and device `type_id` are preserved. For example,
`TEXT` is compared as `string`, but its result `type_id` remains `text`.
In documentation slots, `type_id` contains the annotation from `parameter_types`,
or `null` when it was not supplied. `null` and `unknown` both mean no known type.
Identical strings with different annotations are not merged in caches.

After `prepare_catalogs()` in hierarchy mode, checked entries in `hierarchy.view_links` also expose
`coverage`: `covered` means that the device view covers the local documentation
sample, `partial` means that it does not, and `unknown` means that types or analysis
limits prevented a conclusion. These are not confidence scores. Multiple covered
views remain alternatives. A candidate may have no completed bindings when its
comparison exceeded a limit. `compile_catalogs()` does not run these proofs and
therefore omits `coverage`.

## Result schema

```text
devices
  <device pattern_id>
    device_format
    status: matched | partial | unmatched | unknown
    stage: exact | reordered | intersection | prefix | mixed | null
    slots
      <slot_id>: name, declaration, type_id, repeat_ids
    mappings[]
      document_id
      status
      stage: exact | reordered | intersection | prefix
      binding_mode: structural | path_dependent | prefix_dependent | unavailable
      bindings[]
        device:   device slot_id
        document: document slot_id
      automaton_id: reference to the applicability graph, if needed
documents
  <document_id>
    document_format
    slots
      <slot_id>: name, declaration, type_id, repeat_ids
automata
  <automaton_id>
    start, final, edges
    # Edges contain target, label and, for parameters,
    # document/device: {slot_id, iterations}
```

All device formats appear in `devices`, including those without documentation
matches. All discovered full correspondences and duplicates are preserved separately.
A device status of `matched` means at least one correspondence exists; other
candidates may be `unknown`. The `mappings` list includes unfinished comparisons
with this status so that an absent result is not mistaken for a proven absence of
a match. If confirmed pairs come from different stages, the device has `stage="mixed"`.
Each pair specifies its own stage. Unknown pairs do not change the summary stage.
A complete match without parameters is retained with `bindings: []`; this is not
an error.

`result.pairs` is a convenient flat Python view of the same pairs, exposed as a
computed property. The Python API retains complete pair objects; for JSON, call
**`result.to_dict()`**, not `dataclasses.asdict(result)`. It stores slot descriptions
once per source and identical graphs once per result. Graph identity includes
transitions, `slot_id`, and repetition coordinates; names and declarations come from
the corresponding pair's slot tables. Each mapping in JSON does not duplicate the
device format or its ID. `stage="exact"` replaces the redundant
`structurally_identical` flag. Diagnostic fields `common_example`,
`document_only_example`, `device_only_example`, `common_prefix`, and `reason` are
absent. There is no `evaluate` method or artifact reader. All data are returned in
memory; saving uses one ordinary `json.dumps`, without intermediate files.
Old result files must be recalculated or left to their previous consumer.

A pair's status describes the relation of the **documentation language to the
device language**:

This is an abstraction over keywords and parameters with the type check described
above, without checking ranges, real values, or semantics. With unknown types,
`equivalent` means structural compatibility without a proven type conflict.
During automaton traversal, an unknown type accepts any category.

| Status | Meaning |
|---|---|
| `equivalent` | The structural languages are equal |
| `document_subset` | The document describes part of the device language |
| `device_subset` | The device language is included in the documentation language |
| `overlap` | There are shared complete commands and differences on both sides |
| `matched` | The complete intersection and bindings are built; the exact relation was not proved within budget |
| `unknown` | Pair analysis is unfinished; no bindings are returned for this result |
| `prefix_match` | Shared incomplete traces were found; bindings apply only to their beginnings |
| `prefix_only` | Only a shared prefix exists |
| `disjoint` | There are no shared commands or nonempty common prefixes |

Normal compilation excludes proven mismatches. The last two statuses are available
through a separate `compare(doc, device)` call, which diagnoses the relation between
complete languages. In Python, syntactic identity is recorded in
`structurally_identical`; in JSON, it corresponds to `stage="exact"`.
Normalizing alternative order and redundant mandatory groups can produce
`equivalent` with `structurally_identical=False`.

## Linking JSON to parser results

`vrp_parser_automaton.ParameterValue` now has:

- `slot_id`: the original parameter position **in the format**, such as `p:7`;
- `iterations`: zero-based occurrence coordinates within `&<m-n>`, from the outer
  repetition to the inner one, such as `(("r:5", 1),)`.

`span` still locates the value **in the actual command line**. This is a different
coordinate system. Reordering `[]*`/`{}*` branches changes value order, but not their
`slot_id` values. A set itself does not repeat one slot: each branch can be selected
once. `iterations` arise from `&<m-n>`.

The linking key is **`(PatternMatch.pattern_id, ParameterValue.slot_id)`**.
Do not use a value's ordinal position, type name, or declaration alone:
`INTEGER<1-4096>` may appear multiple times in one format.

Example of applying a **structural** correspondence without predicates:

```python
from collections import defaultdict
from vrp_parser_automaton import CommandLineParser, ParsedCommand

mapping = json.loads(Path("mapping.json").read_text(encoding="utf-8"))
parser = CommandLineParser({"commands": formats})
parsed = parser.parse("vlan 10 to 20 30")
assert isinstance(parsed, ParsedCommand)

for match in parsed.matches:
    device = mapping["devices"][match.pattern_id]
    values_by_slot = defaultdict(list)
    for value in match.parameters:
        values_by_slot[value.slot_id].append(value)

    for pair in device["mappings"]:
        if pair["binding_mode"] != "structural":
            continue  # path_dependent requires a scope check; see below.
        document = mapping["documents"][pair["document_id"]]
        for binding in pair["bindings"]:
            slot = document["slots"][binding["document"]]
            for value in values_by_slot[binding["device"]]:
                print(
                    pair["document_id"],
                    slot["name"],
                    value.normalized,
                    value.iterations,
                )
```

The result is `first=10` and `last=20` in iteration 0, and `first=30` in iteration 1.
`bindings` lists pairs of source slots. `repeat_ids` in the `slots` tables lists a
slot's enclosing repetitions. For a structural correspondence, the device and
documentation lists describe the same nesting levels pairwise: iteration numbers
can be transferred to the documentation's repetition identifiers.

Device formats must be the same original strings during offline compilation and
runtime: changing whitespace changes `pattern_id` and slot positions. A format ID
contains a hash of the string and its duplicate number within the catalog. Reordering
different formats does not change IDs. For identical duplicates, preserve their
multiplicity in the catalog.

The parser preserves alternative assignments of one value to different source
slots. For example, `c [ INTEGER<1-100> ] [ INTEGER<1-100> ]` with line `c 1` produces
`ambiguous` with two `slot_id` alternatives. Choosing only `primary_match` in this
case means selecting one interpretation yourself.

## Partial and path-dependent correspondences

With `binding_mode="structural"`, identical AST structures are linked directly,
checking type compatibility but not names or ranges. Reordered alternatives and
redundant mandatory groups do not require command expansion. When branches are
reordered, types can distinguish alternatives with identical shapes. Within the
same shape and type, occurrence order is preserved; a different semantic order
cannot be recovered from these data.

With `binding_mode="path_dependent"`, `bindings` lists **possible** correspondences.
Their applicability is determined by complete graph paths, not just slot presence.
In JSON, the graph is available as `mapping["automata"][pair["automaton_id"]]`;
in Python, it remains `pair.automaton`.
For example:

```text
device: c INTEGER<1-100> [ to INTEGER<1-100> ]
doc:    c { <single> | <first> to <last> }
```

The first device slot corresponds to `single` for `c 1` and to `first` for `c 1 to 2`.
Likewise, documentation format `c { a <a> | b <b> }` does not apply to `c a 1 b 2`,
even if the device allows both branches through `{}*`. Parameter presence alone
does not distinguish these cases.

The intersection graph is already calculated offline; formats need not be matched
again. Your postprocessing can convert it into suitable constraints.
To use it directly:

1. Obtain structural tokens from the actual command: treat each captured parameter,
   using its `span`, as one `P` (including values containing several words), and all
   remaining words as literals `K:<word in ASCII lowercase>`.
2. Traverse the prepared `edges` from `start`: `label=null` is an ε-transition,
   `K:...` is a literal, and `P` is a parameter. For `P`, check `device.slot_id` and
   `device.iterations` against the specific `PatternMatch`.
3. Collect `document` bindings only along paths that consumed the entire command
   and reached `final`. Look up names by `document.slot_id` in
   `mapping["documents"][pair["document_id"]]["slots"]`.
   Do not mix alternative documentation interpretations.

Edges retain coordinates of specific repetitions; the summary `bindings` list
references slots without a specific iteration. The graph contains only productive
paths. `tests/test_format_matcher_result.py` checks reconstruction of all original
bindings and transitions from compact JSON; applicability to actual parses is tested
in `tests/test_format_matcher_runtime_slots.py`.

## Finding all correspondences

For each device format, the index selects documentation formats that may intersect.
Each pair independently goes through the following checks:

| Pair `stage` | Condition |
|---|---|
| `exact` | Identical ASTs with the same branch order and compatible types; names and ranges are ignored |
| `reordered` | Matching structures after normalizing branch order and redundant mandatory wrappers |
| `intersection` | Shared complete commands and bindings along their paths |

The first successful check completes analysis of **that pair**, while the search
for other documents continues. For example, `acl INTEGER<2000-2999>` retains both
the exact correspondence `acl <number>` and the intersection with
`acl [ number ] <number>`, if both documents belong to the allowed search scope.
Commands without parameters are handled in the same way: absent bindings do not
invalidate a complete match. Matches are retained in documentation order, without
weights or ranking.

If no full correspondence is found, `prefix` searches for common beginnings of
incomplete traces with at least one binding before divergence. Keyword-only prefixes
do not create mappings. There are no manual `best`/`all` modes.

Indexes are built **over the documentation**. Shared complete commands are filtered
using a prefix tree and necessary conditions on required/possible AST symbols.
This pruning is conservative: if prefix analysis exhausts its budget, the candidate
set expands and potential matches are retained. For incomplete traces, a separate
tree of paths up to the first parameter is built when needed. `undo vlan <id>` and
`undo interface <name>` are rejected at the differing keywords, before constructing
the automaton product. There is no exhaustive enumeration of catalog pairs.

Automata are not built for `exact` and `reordered`. For other pairs, the intersection
with bindings is built first, followed by refinement of the complete-language
relation. Exhausting the refinement budget preserves prepared bindings with status
`matched`. ASTs and programs are reused. Identical pairs of strings and type
annotations are calculated once, but all source IDs are preserved. The comparison
cache is scoped to one device format within one pass; failed comparisons do not
accumulate across the entire corpus. Duplicate device formats are processed together
when their types and search scopes agree.

`MappingLimits` bounds instructions, configurations, and operations in the general
algorithm. Different sets can still generate many subsets. `unknown` is retained
as an unfinished check, including when another document already matched exactly.
If no full pair has been proved, prefix fallback remains available; it does not turn
an unknown full result into a proven absence of a match.

The guarantees apply to the chosen format representation: known types are checked
by category, ranges are ignored, and unknown types are treated as compatible.
A format match alone does not establish semantic or view correspondence.
Syntactically identical alternatives are matched by occurrence order, as before.

`on_progress(event)` reports `stage="matching"` during full-match search and
`stage="prefix"` for the remaining device formats. `devices_done` and `devices_total`
refer to the current pass; `pairs_prepared` counts pairs in completed results.
These progress stages differ from an individual pair's `stage` in JSON.

Search retains more correspondences than the previous approach of stopping after
the first successful stage for a device format. Consequently, result size and
execution time can increase for catalogs with many intersecting formats. JSON slots
and graphs are still shared across records without losing bindings.

## Applying incomplete traces

`stage="prefix"` gives the device status `partial`, the pair status `prefix_match`,
and `binding_mode="prefix_dependent"`. This is not a correspondence for an entire
command. For example, `c <first> doc <tail>` and
`c INTEGER<1-100> device INTEGER<1-100>` link only `first` to the first device
parameter. After divergence at `doc`/`device`, traversal does not resume even if
subsequent words match.

The graph referenced by `automaton_id` stores nonempty common prefixes, including
short and long repetition variants. For a concrete parse, applicable paths match the
command's beginning in literals, `slot_id`, and repetition coordinates. Unlike
`path_dependent`, this does not require consuming the entire command. To select the
longest common beginning, retain the furthest boundary reached along that path
rather than stopping at the first accepting state. No bindings exist beyond that
boundary. Your postprocessing decides whether predicates apply to such a partial
correspondence.

## Command-line interface

```bash
PYTHONPATH=src python3.13 -m vrp_format_matcher \
  --patterns device.json --documents docs.json --save mapping.json --summary

PYTHONPATH=src python3.13 -m vrp_format_matcher \
  --patterns data/mocks/cloudengine_150/device_grouped.json \
  --documents data/mocks/cloudengine_150/documentation_grouped.json \
  --save mapping.json --summary
```

Both files may be v1 flat or grouped catalogs; the CLI calls `compile_catalogs`.
`--summary` hides individual correspondence details but keeps the counts.
Legacy inputs are also supported: `device.json` is an object with a `commands`
array of strings, and `docs.json` is an array of `{"format": "..."}` records
with optional IDs. You cannot mix legacy inputs with v1 catalogs. `--target-syntax`
is used only for legacy inputs; v1 catalogs determine syntax through `source`.
Passes are selected automatically; there are no `best`/`all` switches.
Both `--patterns` and `--documents` are required. `--save` writes indented JSON.
The CLI exits with `0` after a completed run, including unmatched formats, and `2`
for invalid arguments, input, or file errors. Progress goes to stderr; the summary
and optional correspondence details go to stdout. After installation, the same
arguments are accepted by `vrp-format-matcher`.

```bash
python3.13 scripts/benchmark_format_matcher.py --corpus /path/to/cmd_corpus --skip-invalid --report /tmp/report.json
```

The benchmark excludes `display`, matches the corpus against itself, and checks
every slot of each source format. Duplicates are retained. Invalid formats are
reported explicitly; without `--skip-invalid`, the run stops. Corpus reading and
benchmark reporting remain in the standalone benchmark utility.

Code is split by responsibility: `preparation/compiler.py` is the public API;
`preparation/indexes.py` contains indexes; `preparation/pipeline.py` searches for all
full pairs and performs prefix fallback; `preparation/pairs.py` analyzes one pair;
`preparation/programs.py` handles programs and node correspondences. The package has
no predicates, artifact readers, or alternative search modes.

## Optional hierarchy recovery

With explicit `context_mode="hierarchy"`, use `prepare_catalogs(device, documentation)`.
The second input is documentation **with a prepared hierarchy**. In this method,
an omitted documentation `switch_to_view` means context preservation. Known gaps,
including parameter-dependent targets without a single transition, must be marked
explicitly with `"switch_to_view": {"status": "unresolved"}`.
In the original grouped device input, an omitted field still means unknown.
For documentation → documentation, both inputs are treated as prepared hierarchies.
The reference may contain only a sample of commands: prepared transition annotations
do not require a complete command inventory.

```python
prepared = FormatMatcher(context_mode="hierarchy").prepare_catalogs(device, documentation)
Path("runtime_catalog.json").write_text(
    json.dumps(prepared.catalog, ensure_ascii=False),
    encoding="utf-8",
)
Path("mapping.json").write_text(
    json.dumps(prepared.mapping.to_dict(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(prepared.catalog["type"], len(prepared.unresolved))
```

`prepared.catalog` is the runtime parser's only input. Your external code uses the
mapping and original documentation after configuration parsing. `prepared.unresolved`
contains the `pattern_id` values of commands whose single effect on context cannot
yet be established. These can include ordinary commands if view preservation could
not be confirmed.

Processing order:

1. Match formats and check parameter type categories.
2. Require a device view to cover every local format in the documentation sample.
   Extra device commands are allowed. Existing `equivalent` and `document_subset`
   proofs are used first; otherwise check inclusion in the union of suitable device
   formats **from the same view**. Prefix matches do not prove full coverage.
   Explicit `shared_views` and copies of their typed formats are excluded from view
   identification. The entry pair is an anchor, not a competing candidate.
3. Select a target only when exactly one device view covers that sample and no
   competitor has unknown coverage. Several documentation groups may refer to the
   same device group; all their mappings are retained. Empty or shared-only samples
   supply no identity. View names, weights and match counts are not used. Missing
   coverage retains the existing partial evidence without promoting a target.
4. Propagate explicitly known transitions from the entry view pair. They distinguish
   identical command sets in documentation → documentation and when device transitions
   are already known. Conflicting paths are not selected by traversal order. Relations
   established through known transitions take precedence over similarity of complete
   group contents.
5. Transfer a transition only when the **entire device format** is covered, all
   applicable full pairs have one documented effect, and the target is unambiguous.
   Coverage may be collective: several documentation records with the same effect
   can cover the entire format together. A conflict, even with a partial or still
   unresolved match, prevents a format-wide `switch_to_view`. When a device scope
   has several documentation references, each must establish a whole-format effect
   and those effects must resolve to the same runtime destination. Missing evidence
   in one reference does not imply a stay.
6. Restrict the final pairs to established views. Full pairs and their bindings are
   reused; if restriction removes a full match, run prefix fallback within the
   allowed view.

For example, `c { a | b } <id>` is covered by `c a <first>` and `c b <second>` when
parameter types agree. Both original pairs, their bindings, and their applicability
scopes are preserved. For transition transfer the collective check must prove that
no device commands remain uncovered: a missing branch, optional omission or `{}*`
permutation prevents assigning an effect to the entire device format. Such extra
device routes do not prevent identification of the containing view from a smaller
documentation sample.

The union does not copy graphs or enumerate routes: traversal keeps the source
automaton ID and its current states with repetition registers. States from different
formats are not merged. ASTs and compiled programs are reused; languages with
identical typed structure share a check result. Binding calculation and the four
existing matching cases for individual formats remain unchanged.

Inclusion checks are bounded by `comparison_states` and `analysis_steps`. Exhausting
a limit means unknown, not uncovered; such a competitor cannot falsely make another
view unique. Unknown parameter categories do not prove equality either. Proven
coverage can still be used if additional unresolved pairs introduce no different
context effect.

This recovers a hierarchy using the agreed structural criterion; it does not confirm
device behavior. Numeric ranges and string lengths are still not compared.
In JSON, `hierarchy.resolved_views` contains device-view → documentation-view
relations with a single reference. When several documentation groups map to one
device view, `hierarchy.targets` retains each resolved target and `view_links`
retains the corresponding pair references. No documentation group is selected by
order. Unresolved views retain their positive candidates.

For example, a sample containing `server enable` and an IPv4 `source-ip` format
can identify an IPv4 server scope even when the device has additional commands.
Removing `source-ip` may leave both IPv4 and IPv6 scopes possible. Resolving one
scope never assigns another by elimination. If syntax differs between releases,
even one-sided full coverage can fail; partial matches remain available, but are
not silently discarded to manufacture uniqueness.

The optional grouped-catalog field `"shared_views": ["common-scope-id"]` declares
shared scopes explicitly. Names are local references and have no built-in meaning.
Shared formats remain available to scoped matching and effect checks; declarations
do not add or reorder command records. This field does not implement parser global
inheritance: a runtime producer must still place commands in their usable views,
as the documentation recovery pipeline does. Without the field, scopes are ordinary.

The prepared grouped catalog preserves **original device view IDs**, groups, and
known transitions. Documentation names are not copied into the device catalog.
An unknown effect is marked directly on the command record:

```json
{"format": "interface STRING<1-64>", "switch_to_view": {"status": "unresolved"}}
```

The parser with `context_mode="hierarchy"` uses the established hierarchy. Only the child block after such
a command is parsed without view restrictions; leaving it restores the known parent
context. For manual refinement, replace the marker with a device view ID or `null`.
Documentation alternatives are in `hierarchy.transitions[pattern_id]` and reference
the original pairs and their bindings; target candidates are in `hierarchy.targets`.
They do not need to be duplicated in every runtime catalog record.

If different parts of a device format correspond to documentation commands with
different transitions, no single `switch_to_view` is assigned: the record stays
`unresolved`, and all pairs and their applicability scopes are preserved. Splitting
documentation into formats with constant targets helps retain these differences,
but does not itself create a conditional transition for a shared device format.
Execution of rules based on parameter values is not implemented yet. Name
normalization cannot recover a missing target-selection rule.

Formats, record order, `pattern_id`, `slot_id`, and bindings are preserved.
`source.view/index` address the original catalogs; these positions also stay the
same in the prepared catalog. The input dictionaries are not modified.

A flat target catalog is supported and remains flat. Recovering a grouped device
catalog requires grouped documentation.

```bash
PYTHONPATH=src python3.13 -m vrp_format_matcher \
  --context-mode hierarchy \
  --patterns device_grouped.json --documents documentation_grouped.json \
  --save-catalog runtime_catalog.json --save mapping.json --summary
```

Without `--save-catalog`, the CLI calls `compile_catalogs()`:
matching and candidate collection without runtime catalog preparation.
