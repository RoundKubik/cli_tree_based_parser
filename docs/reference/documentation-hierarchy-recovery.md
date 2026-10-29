# Recovering documentation view transitions

`vrp_documentation_hierarchy` is a standalone module that fills parent commands'
`switch_to_view` arrays using documented examples of child commands. It uses the
existing public syntax parser. It does not depend on the device format matcher,
change the parser/runtime, or construct a separate global state graph.

## Run

From the repository root, with Python 3.13+:

```bash
PYTHONPATH=src python3 -m vrp_documentation_hierarchy \
  --catalog data/generated/cloudengine_terra_complete/documentation_without_switches.json \
  --corpus ../cli-reference-corpus/output/huawei-cloudengine-9800-8800-6800-v300r024c00/cmd_corpus \
  --output data/generated/cloudengine_terra_complete/documentation_recovered_from_examples.json
```

The CLI writes the enriched document and, by default,
`documentation_recovered_from_examples.report.json`. Use `--report` to choose
another report path. The example input has no pre-existing `switch_to_view`
annotations, so the run measures recovery from source examples alone.
The input, output, and report paths must differ. Exit code 0 means recovery
completed, including unresolved observations; 2 means invalid input or an I/O error.

The library API returns a copy and leaves the caller's document unchanged:

```python
from vrp_documentation_hierarchy import recover_hierarchy

result = recover_hierarchy(document, corpus_directory)
enriched_document = result.catalog
report = result.report
```

## Input

The document must have `source: "documentation"`, `type: "grouped"`, an
`entry_view` referencing a `views` group, and command records with:

```json
{
  "format": "interface { <interface-name> | <interface-type> <interface-number> }",
  "documentation_source": {
    "file": "2.2.1.23_interface.json",
    "format_index": 0
  }
}
```

`file` is relative to the corpus directory. `format_index` is a zero-based integer
in that page's `CLIs`; the source format must equal the catalog format. Copies in
different views retain the same source identity and consistent syntax annotations.
Optional `parameter_types` are passed through to the existing syntax parser.

Views containing copies of a source format define its parent-view set. Original
record ordering, fields, and source references are preserved. An explicit
`global_view` or legacy `shared_views` declares globally available groups; group
names alone do not make commands global.

Source pages provide `CLIs` and `Examples` (an array of arrays of strings).
An empty/missing `Examples` array is permitted. This version consumes `Examples`
directly; it does not re-extract examples from Markdown, PDFs, or `ExtraInfo`.

## Recovery

1. Compile each source format once. Retain all syntax matches, then restrict them
   to the source views available at that position.
2. Parse local example blocks. Anchor child occurrences to the source page owning
   the example, keeping identically spelled commands from other pages separate.
3. Locate the entry command of each child's immediate enclosing block.
4. Resolve that entry's source scope from the example root and existing ancestor
   `switch_to_view` annotations. Multiple matching ancestor formats contribute the
   union of their known targets; missing branches can be discovered by later passes.
5. Add all catalog parent views of the anchored child to EVERY matching parent
   format's `switch_to_view` array within that source scope. For example, if
   `acl number 2999` matches five formats, all five receive the child's views.
   Global children supply no target-view evidence.
6. Apply the collected additions together at the end of each pass. Newly available
   scopes are used on the next iteration. Repeat until annotations stop changing.
   Expanding the source scope can expose more matching parents; they receive the
   same observations on subsequent passes, without discarding earlier candidates.

For example, `bgp 100` followed by `ipv6-family unicast` first supplies a target
for `bgp`. A separate example with a command nested under `ipv6-family unicast`
supplies the family command's own targets on the next pass, after its source scope
has become available. There is no requirement to know that target beforehand.

The arrays represent sets and are sorted and deduplicated. For example, an
interface entry can acquire several interface views without selecting an exact
interface type, sub-interface, or L2/L3 state. Numeric parameter values are not
interpreted as view-selection rules.

Existing valid string targets are converted to singleton arrays. Unknown legacy
target names are recorded in `seed_issues` and become `{"status": "unresolved"}`.
Input arrays must reference existing, non-global groups. Existing `null`, missing,
and unresolved annotations supply no child-context targets; recovery may replace
them when a source example supplies evidence. An empty array is not an inferred
stay transition. Existing valid targets are retained when examples add more views.

## Example structure and report

Huawei interactive examples support `<HOST>` and `[~HOST-context]` prompts,
`system-view`, `quit`, `return`, and hostname changes through `sysname`.
`~`/`*` changes do not open blocks. Context suffixes remain opaque. Initial
`[HUAWEI]` is the standard Huawei system prompt; other hostnames are learned from
user prompts or `system-view`. Unknown initial subcontexts remain unresolved.
Standalone prompts are accepted. A contradictory prompt after an exit or hostname
change causes the WHOLE example to be skipped and reported, including facts from
its earlier lines. Other examples on that same documentation page are still
processed independently. Unknown starting contexts are kept for diagnostics and
do not by themselves invalidate an entire example. Examples without prompts use
indentation, with unindented commands anchored at `entry_view`.

The report contains:

- `changes`: old/new annotations and original command locations;
- `evidence`: source page, zero-based example index, one-based parent/child line
  numbers, commands, source identities, contributed target views, the number of
  matching parent formats, and the first recovery pass for that attribution;
- `unresolved`: unknown ancestor transitions, unknown source contexts, and commands
  that did not match any parent format in the available scope;
- `structure_issues`: unknown starting contexts and inconsistent prompt sequences;
- `skipped_examples`: contradictory examples excluded in full;
- `seed_issues`: legacy target names absent from the catalog;
- `summary`: processing and recovery counts, including skipped examples and
  observations applied to multiple matching parent formats.

The result is a partial documentation annotation. Real configurations are not used
as training evidence. Executing these multi-view transitions in a runtime parser,
and applying them to device/documentation mappings, are separate integrations.
The existing parser's legacy hierarchy mode still expects a single target; this
module does not extend that mode.
