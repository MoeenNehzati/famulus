# Inventory, Graph, Dispatcher, and Injection Design

## Strict blueprint inventory

Add `src/officina/common/blueprint_inventory.py` as the reusable filesystem
inventory boundary:

```python
@dataclass(frozen=True)
class BlueprintDocument:
    path: Path
    owner_root: Path
    declaration: Mapping[str, JsonValue]


def iter_blueprints(
    repo_root: Path,
    *,
    skip_parse_errors: bool = False,
) -> Iterator[BlueprintDocument]: ...
```

`JsonValue` permits null, booleans, numbers, strings, lists, and string-keyed
mappings. Discovery includes exactly `skills/<skill>/blueprint.yaml`, regular
files matching `skills/<skill>/**/.*.blueprint.yaml`, and regular files matching
`references/**/.*.blueprint.yaml`, whether or not they are reachable from a
skill root. Target v3 documents are normalized for graph, dispatch, and
injection. Older declarations may be returned only as raw migration records;
they cannot define target behavior or satisfy a Phase 1-4 gate. Traversal does
not follow directory symlinks; selected files are opened no-follow and must remain within
their lexical owner root. Skill documents use `skills/<skill>` as owner root;
reference documents use the repository root. `.git`, caches, `tmp/`, `logs/`,
and generated health/certificate directories are excluded. Ordering is
repository-relative lexical order.

The loader rejects unreadable files, duplicate YAML keys, custom tags,
non-string mapping keys, nonmapping document roots, and YAML-native values that
cannot cross the JSON Schema boundary. In strict mode it buffers the inventory,
raises one path-specific aggregate exception if any parse fails, and yields
nothing before the complete inventory succeeds. Diagnostic mode emits one
deterministic `BlueprintParseWarning` per skipped path and yields valid
documents; graph construction and autoinjection may not use diagnostic mode
(`INV-001`, `INV-002`).

Schema, ID, layout, reachability, ownership, and relationship errors occur
after parsing and are never skippable parse errors. Existing graph-loading APIs
delegate parsing/discovery to this module rather than maintaining another YAML
implementation.

## Normalized repository model

Graph normalization produces:

- one `BlueprintNode` per skill, LLM interface, machine module, and behavior
  source;
- one `MachineInterfaceExport` per nested machine interface, keyed by canonical
  public ID and pointing to its owning module;
- authored node dependency edges for certification;
- interface-scoped execution edges and helper edges for authorization and
  injection.

The normalized model uses distinct records:

- `BlueprintNode` for certifiable skill, LLM-interface, machine-module, and
  behavior-source nodes;
- `MachineInterfaceExport` for public nested IDs and versions;
- `ExportDependencyEdge(source_export, target_interface, target_version)` for
  interface execution authority;
- `HelperEdge(source_export, local_helper_id, target_interface, binding)` for
  bounded caller assistance;
- `ModuleCertificationEdge(source_module, target_node)` derived from authored
  module/export dependencies for certificate ordering.

A module's certification dependencies are the union of its module-level and
all nested interface-level direct node targets. This union is used only for
hash/certificate dependency ordering. It does not give one export runtime
authority over a sibling's tools. Runtime authorization always uses the exact
interface-scoped direct union from `DEP-002`.

An export-to-export dependency resolves the target export version first, then
projects to the target's owning module for certification. A same-module target
is retained in the runtime export graph but omitted from the module certificate
edge set to avoid a false node self-cycle. Runtime cycle detection expands
module-shared edges onto every export and checks export IDs, so same-module
recursion and helper cycles are still rejected.

Graph validation rejects duplicate module IDs, duplicate public export IDs,
invalid canonical namespaces, missing targets, version mismatches, cycles,
forbidden edge types, unauthorized callers, content overlap, ownership overlap,
and orphaned authored sidecars. Public IDs remain `skill.machine.name`; module
IDs use `skill.machine-module.name`.

## Dispatcher resolution

Dispatcher resolves a public export ID to:

1. its owning module;
2. the module gateway and content boundary;
3. interface-local authorization;
4. the compiled fixed/public invocation binding;
5. the exact effective direct tool set;
6. current structural index status and certification gate.

Module IDs cannot be invoked. Dispatcher rejects invalid blueprints before
gateway loading and rechecks inexpensive security-critical rules rather than
trusting generated state. It emits positionals and named arguments according to
`BND-005`, supplies module fixed arguments, prevents caller override, and keeps
execution independent of caller cwd.

There is no dispatcher development bypass. Focused tests may import or invoke a
private gateway through test-only runtime helpers, but that route is not a
public contract and is never injected.

## Consumer-local selection

Each LLM interface is a separate consumer. The default consumer is the inline
root LLM interface bound to `SKILL.md`; named LLM consumers are sidecars bound to
their own `llm_interfaces/*.md` files.

LLM interfaces remain graph nodes with their own version, description,
behavior sources, immediate `direct_io`, ownership, and direct
`uses_interfaces`. This redesign changes where their dependency projections are
written; it does not collapse named LLM interfaces into the skill root or remove
their existing contract responsibilities.

For consumer `C`:

1. load the complete strict inventory;
2. validate and normalize the repository graph;
3. read only `C.uses_interfaces` as direct grants;
4. for each machine grant, resolve one nested export and its owning module;
5. for each LLM grant, resolve the named LLM interface: retain a same-skill
   relative instruction gateway, but for a cross-skill target retain its
   canonical ID/version/description plus
   `route: {kind: provider-skill, skill: <provider-skill-id>}` without exposing
   its filesystem gateway;
6. require current certification for every target node/export;
7. expand only the bounded acyclic helper closure of a selected machine export,
   recursively following helper edges but never ordinary tool dependencies;
8. apply the exact projection field table and embed retained definitions;
9. omit gateway/content, sibling exports, implementation dependencies,
   ownership internals not relevant to calling, and ordinary transitive tools;
10. place one deterministic generated block in `C`'s gateway file;
11. remove stale blocks from consumers with no selected interfaces.

Selected YAML is processed, not copied verbatim: schema validation,
normalization/default application, reference resolution, caller-specific
selection, and field elision occur before serialization. It retains canonical
field names and does not translate the contract to prose or a second interface
format (`INJ-001` through `INJ-003`).

### Exact projection contract

`references/blueprint/interface-projection.schema.json` defines the generated
document. Selection is closed and deterministic:

| Source | Generated treatment |
|---|---|
| machine export `id`, `version`, `description` | always retain under canonical `interfaces.<local-name>` nesting |
| interface `invocation_binding.fixed` | retain normalized typed entries so the caller knows what dispatcher supplies |
| `contract` | retain every normalized field and explicit schema default; never summarize or elide a contract branch |
| `direct_io` | retain the selected export's three normalized lists so output, effect, and verification references resolve |
| bounded `helpers` | retain only selected-export helpers, including target ID/version, binding, result, route, empty/freshness/failure semantics |
| retained schema/format references | resolve under the provider owner root, replace the locator with `{definition_ref: <key>}`, and embed the validation-equivalent reachable fragment in top-level `definitions` |
| same-skill LLM target | retain ID/version/description and relative instruction gateway |
| cross-skill LLM target | retain ID/version/description and canonical provider-skill route, never a path |
| module gateway/content/conformance/platform/dependencies, access control, ownership, siblings, and unreferenced definitions | always omit |

Definition keys are assigned by canonical provider skill/path/fragment order.
Each definition records `source_skill`, normalized provider-relative `path`,
`fragment`, content digest, and `value`; generated consumers resolve only
`definition_ref`, so moving generated Markdown cannot change resolution.
`value` retains the selected fragment, every transitively reachable definition,
all validation/applicator keywords, and caller-relevant annotations `title`,
`description`, `default`, `examples`, `deprecated`, `readOnly`, and `writeOnly`;
unreachable definitions and other annotations are pruned in canonical key order.
Recursive `$ref` values enter the same table, cycles use definition
keys, and an escaping/unresolved reference blocks projection. Each export's
standalone normalized projection is limited to 12,288 UTF-8 bytes and is checked
during certification. A consumer's combined block is independently limited to
16,384 bytes and is checked only during projection/injection. Either overflow
requires narrowing the export or direct grant set; neither is silently
truncated or replaced by an unresolved reference. This is a
self-contained canonical projection, not a prose renderer. Identical selected
normalized contracts produce byte-identical YAML.

An interface's effective tool union may be included as non-granting metadata
when required to explain a bounded helper. It is never recursively expanded
into callable prompt tools. Cross-skill LLM grants resolve through canonical IDs
and provider blueprints, never provider filesystem paths.
`route.kind: provider-skill` instructs the model/runtime to delegate to the
named provider skill and request the target LLM interface ID; it is not a
machine dispatcher call or filesystem include.

Consumer-local selection is a prompt-context boundary. Dispatcher authorization
remains skill-wide through `--caller-skill`; the generated helper binding is not
a capability token. An enforced fixed helper operation must be its own exported
simple interface (`INJ-004`).

The generated block uses the exact markers
`<!-- BEGIN BLUEPRINT USED INTERFACES -->` and
`<!-- END BLUEPRINT USED INTERFACES -->`. In `SKILL.md` it appears immediately
after the root contract block; in a named LLM gateway it appears before the
authored body. Generation plans all target contents before writing, detects
conflicting generated blocks, writes atomically, and is idempotent. Phase 5
migration inventories every live exposed interface from gateways, focused
tests, behavior sources, and skill content, using older declarations only as
hints. It records one disposition:
`add-direct-edge`, `keep-uninjected`, or `retire`. No export disappears silently.

## SessionStart vocabulary

One session-wide context block is derived from the union of constructs present
in all selected fragments. It defines only those constructs, using compact
terminal notation such as `<x>`, `[<x>]`, `<x>...`, `[<x>...]`, `[--flag]`, and
`<a|b>`. The glossary identifies these as required, optional, one-or-more,
zero-or-more, valueless switch, and alternatives respectively.

The dispatcher portion explains verified global options once:

- required caller identity;
- always-available `--dry-run`, which resolves and prints the compiled
  invocation without executing the gateway or reading stdin;
- `--stdin` only when at least one selected interface accepts stdin;
- any future global option only after checking the live dispatcher.

When a cross-skill LLM route is selected, the vocabulary also defines
`route: {kind: provider-skill, skill: <id>}` as delegation to that skill for the
named LLM interface. It is omitted otherwise.

It also includes this invariant:

> Within interface arguments, supply all positional values first in increasing
> position order. Then supply named options and switches in any order, keeping
> each option immediately followed by its values. Dispatcher adds
> interface-fixed arguments; do not supply them.

The dispatcher core and notation glossary always fit within 500 characters.
Optional vocabulary entries are individually budgeted and ordered by descending
use count then canonical name. Entries that do not fit remain self-describing
in the selected YAML and are omitted from the hook; no valid projection fails
solely because optional glossary text would exceed the budget. The combined
context remains within 750 characters. Host-specific hooks render
the same semantic payload and retain their output-schema tests. Interface
effects, retry advice, temp/log details, and other per-interface facts remain in
the selected YAML, not the session hook (`HOOK-001`).

## Migration grouping

Live Python interfaces are grouped by normalized gateway identity: owner skill,
gateway path, symbol, and args prefix. The migration tool proposes
`skill.machine-module.<gateway-stem>` and a module sidecar path derived from the
gateway. A checked-in migration map resolves collisions or intentional splits;
public interface IDs do not change. The emitted declarations are v3 modules.
Existing v2 or prototype declarations are non-authoritative hints and do not
override live behavior.

Legacy path roots are normalized during the same migration. An unprefixed path
becomes `relative_to: skill-root`. `$repo/...` and `$home/...` become declared
repository and home/config filesystem resource references. Persistent
`$tmp/...` becomes a declared temporary resource; purely ephemeral skill-private
temp paths use the private runtime namespace and may qualify for `IO-003`.
Absolute paths do not survive as literals: they must resolve through a declared
resource reference or migration fails (`MIG-003`).

For `_check_drift_state.py`, migration derives the implemented operations from
the gateway and focused tests, then creates one v3 module with separate exports
for selected-skill hashing, exact-root hashing, and all-observed-skill hashing.
Each fixes machine-readable output and its implementation operation token rather than
exposing mode arguments. The historical shared-gateway validation failure then
becomes invalid and must disappear.
