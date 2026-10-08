# CloudEngine mock catalogs

Four documents following [specification v1](../../../docs/reference/command-catalog-format.md):

| File | Source | Layout | Records |
|---|---|---|---:|
| [device_flat.json](device_flat.json) | Device, synthetic export | Flat | 150 |
| [device_grouped.json](device_grouped.json) | Device, synthetic export | 13 views | 150 |
| [documentation_flat.json](documentation_flat.json) | Documentation | Flat | 151 |
| [documentation_grouped.json](documentation_grouped.json) | Documentation with a recovered hierarchy | 13 views | 151 |

Based on `huawei-cloudengine-9800-8800-6800-v300r024c00/cmd_corpus` from the
`cli-reference-corpus` project. Each record's `metadata` identifies the source JSON
file and zero-based format index in `CLIs`. Commands have no `id` field.

## Sample selection

- Seed: `20261006`. Of the 150 records, 136 were selected randomly across views.
- 12 commands were selected manually to test transitions, and two more are ACL
  references from IPv4/IPv6 GRPC server views. Transitions are checked separately
  against `FuncDef`.
- The sample covers System, BGP and two of its address families, GRPC and two server
  views, Basic/Advanced ACL, VLAN, Route-policy, OSPF, and IS-IS.
- `display` and `reset` are excluded. Formats with unrecognized grammar or
  unclassified parameters were skipped. Known additional entry commands outside
  the selected hierarchy were excluded from the random portion.
- Each record belongs to one allowed `ParentView` from its source page; the original
  name is preserved in documentation `metadata.parent_view`. Identical command text
  in different views is treated as separate contextual records.
- One `bfd all-interfaces enable` command from the RIP view was added to the
  documentation to populate the target view of the already selected `rip` command.
  All original 150 records are retained. The device catalogs still contain the
  original sample.

## Preserved and synthetic data

Documentation formats are taken from `CLIs` unchanged. `parameter_types` are derived
from `ParaDef` and selected name heuristics. All four types are represented:
`string`, `integer`, `ipv4-address`, and `ipv6-address`. This mock represents enums,
MAC addresses, prefixes, and composite AS numbers as strings, without their special
constraints.

Device formats are created by replacing parameters with typed declarations at their
AST positions. Numeric ranges and string lengths come from simple descriptions or
are replaced with mock bounds. Device view IDs are synthetic aliases.
This is **not an export from a real device**. Documentation parameter names and
branching grammar are preserved; corresponding pairs are intended for the `exact`
stage.

Mock `creates` examples were added for seven commands and `requires` examples for
four ACL references. Other semantics are omitted. These rules are test annotations,
not the output of a complete knowledge-extraction pipeline. Device files contain
neither transitions nor semantics.

Within each source, flat and grouped versions contain the same records in the same
traversal order; only `switch_to_view` is removed from flat documentation.
Documentation group membership is recovered independently of device groups.
Their contents, counts, and names are not required to match.

## Recovered hierarchy

[refresh_switches.py](refresh_switches.py) preserves the explicit target from
`FuncDef`. Parameter names, types, and ranges do not refine this target. The name is
lowercased and whitespace is normalized; the original sentence is retained in
`metadata.switch_to_view_source`.

The script then checks documentation examples: a prompt change after an entry
command is linked to the `ParentView` of the following command's page. The example's
last command must match one of that page's formats, and its `ParentView` must be
unique. The entry command is checked in a known source context. For example, an ACL
reference from gRPC does not inherit the system-level `acl` transition. Parameter
types do not participate in this check.

Examples supplied additional transitions for OSPF, IS-IS, RIP, and BGP IPv4. Their
`metadata.switch_to_view_source` contains `field: "Examples"`, the source file, an
example fragment, and the view name. There are **13 transitions** in total, all
**13 groups are reachable from `system view`**, and every target contains commands.
Ambiguous observations do not become arbitrary transitions; unreachable groups or
empty targets stop regeneration before JSON is written.

Groups are associated with target names as follows:

| Original `ParentView` | Catalog group |
|---|---|
| Basic ACL view, Advanced ACL view | `acl view` |
| GRPC server view | `grpc ipv4 server view` |
| BGP-IPv6 unicast address family view | `bgp ipv6 address family view` |
| Other selected views | The same name in lowercase |

`acl view` is a general group for the selected ACL formats. Entry commands retain
the target from `FuncDef`; Basic/Advanced distinctions remain in
`metadata.parent_view`. This mock does not model restrictions on individual rules
by ACL subtype. Original records, including identical formats from two ACL groups,
are retained separately to preserve their provenance and annotations.

At the system level, entry commands are available for ACL, BGP, gRPC, VLAN,
route-policy, OSPF, IS-IS, and RIP. BGP contains two address families; gRPC contains
IPv4 and IPv6 server views. This recovers the selected documentation mock; it does
not assume that the entire corpus hierarchy is complete. The remaining 138 records
omit the transition field; an unconfirmed `null` is not added.

Recalculate transitions for the same sample:

```bash
PYTHONPATH=src python3 data/mocks/cloudengine_150/refresh_switches.py --corpus /path/to/cmd_corpus
```

The script updates both documentation JSON files, retaining the current sample,
formats, parameter types, and mock `creates`/`requires`. Device files are neither read
nor changed. Rerunning produces the same result.

## Checks

Checks cover JSON, format and original view provenance, group reachability,
transition evidence, parameter type coverage, and equality of flat/grouped records.
Extraction tests also cover different names for one context, the shared ACL context,
and isolation of identical commands in different views. The current matcher confirmed
all 150 device → documentation pairs and 203 slot bindings, as well as 150
documentation → documentation correspondences. The runtime parser was checked on
12 real lines for the selected formats.

The matcher accepts both catalogs directly:
`FormatMatcher().compile_catalogs(device, documentation)`. In JSON, `source.view`
and `source.index` point to the original device or documentation record. For two
grouped catalogs, commands in `device.entry_view` search for documentation only in
`documentation.entry_view`. Search remains global for other views and flat catalogs;
their matches do not yet establish context correspondence. Each format retains all
full documentation correspondences, including intersections alongside exact matches.
There are no heuristic view scores; `source.view` identifies the original context
of each discovered pair. For grouped → grouped, `hierarchy` is added automatically
to the result JSON: view relations, documentation effects, and possible device target
views. Candidates are not selected by name or match count; even one candidate remains
`unresolved` unless the entry view pair establishes the target. Explicit input
`switch_to_view` values, if present, are retained separately. External code uses the
mapping after parsing; the runtime parser does not load it.

To recover the device hierarchy and obtain a separate parser input:

```bash
python3.13 manual_format_matcher_test.py \
  --patterns data/mocks/cloudengine_150/device_grouped.json \
  --documents data/mocks/cloudengine_150/documentation_grouped.json \
  --save-catalog /tmp/runtime_catalog.json --save /tmp/mapping.json --summary
```

This mode calls `prepare_catalogs()` and treats the documentation hierarchy as
prepared: an omitted documentation transition means context preservation, but the
command inventory may be incomplete. View recovery requires one device view to
cover the available documentation sample; additional device commands are allowed.
The prepared catalog remains `grouped`, and unresolved command effects receive
`"switch_to_view": {"status": "unresolved"}`. The summary reports the current view,
transition and pair counts. This is structural recovery without confirmation on
a real device; see the [recovery rules](../../../docs/reference/automaton-format-matcher.md).

## Running the parser on mocks

From the repository root, with Python 3.13+, no package installation required:

```bash
python3.13 manual_automaton_test.py \
  --patterns data/mocks/cloudengine_150/device_flat.json \
  --line 'bgp 65000' --line 'ipv4-family unicast'
```

Device mocks contain standard runtime parameter declarations. Transitions in
`device_grouped.json` have not yet been recovered; pass it with `--flat` for global
search. Full grouped parsing requires a separate prepared document with recovered
`switch_to_view` values. Documentation mocks are intended for the matcher and an
external semantic pipeline.

For your own file, replace `--line` with `--config config.txt`. In flat mode, a `#`
line requires its own format, which is absent from this sample. An example of one
prepared grouped document, the CLI, and the Python API are described in the
[parser guide](../../../docs/reference/automaton-parser.md).
