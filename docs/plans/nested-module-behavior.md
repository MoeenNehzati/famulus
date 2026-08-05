# Nested modules: version 5 design and cutover

Status: implemented in the canonical v5 cutover and merged to `master`.

Closure: the implementation plan was closed by `9da6a38` and the follow-up
certification-currentness correction was committed as `a1dcb5a`. Future changes
should edit the canonical architecture, blueprint, and certification documents
directly rather than extending this cutover design.

This document defines only the nested-module delta from the adopted
architecture. Unchanged node, interface, hashing, certificate, validator, and
testing behavior remains governed by:

- `docs/architecture.md`;
- `docs/skill-blueprints.md`;
- `docs/certification_and_drift.md`;
- `references/blueprint/README.md`; and
- `TESTING.md`.

The implementation should be explainable through five rules:

- Containment chooses ownership; declarations choose authority.
- Hash what you own; certify what you depend on.
- Registration reveals internally; namespace export routes outward.
- Facades preserve names, not permissions.
- Resolve once; enforce everywhere.

## 1. Decision summary

### 1.1 Explicit topology

A nested module exists only when its direct physical parent registers its
canonical `blueprint.yaml`. The registration tree must agree with nearest
physical containment. Duplicate parents, missing or unregistered nested
markers, inconsistent locators, cycles, ignored paths, symlink aliases, and
nested source-control repositories fail closed.

Registration documents containment and makes the child namespace addressable
inside the parent's registered subtree. It does not expose the namespace
outside that subtree and does not grant interface authority.

### 1.2 Ownership and hashing do not change meaning

Ownership first selects the deepest registered module containing a file, then
the matching behavioral source within that module. Parent ownership prunes
registered child roots.

Each node hashes only its directly owned inputs under the existing node-hash
policy. A parent hashes its registration and routing declarations, but not
child bytes or the child's `node_hash`. Physical containment and inactive
registration alone create no certification dependency.

### 1.3 Child exports remain the authority ceiling

A child source interface is private unless the child module exports it. No
ancestor can expose a private child interface.

The child export's existing `access` declaration is the maximum caller set.
Namespace filters, interface-specific route filters, and facade filters
intersect with that set; none can widen it. Granting a module access does not
grant its descendants access.

Every cross-module calling source still declares the exact interface ID and
version in `uses_interfaces`. Imports and source dependencies confer no
invocation authority.

### 1.4 Namespace exports route descendant identities

A parent may namespace-export one registered direct child. The declaration:

- pins the direct child's version;
- carries an allow-all, exact, or empty caller filter; and
- selects either every routed descendant export (`all`) or exact descendant
  interface IDs and versions (`only`).

Each boundary crossed from outside requires its own namespace export. For a
target module `T`, a strict ancestor `P`, and the direct child `Q` of `P`
containing `T`, the `P -> Q` route is required exactly when the caller is
outside `P`'s subtree.

Consequently, a parent or sibling may address an authorized child directly
without the common parent's outward filter: both are already inside that
registered subtree. A caller in another branch crosses the lower target-side
boundaries below the lowest common ancestor. An unrelated caller crosses the
complete target ancestry.

Routes preserve the descendant-owned interface ID. They do not flatten
namespaces, create aliases, copy contracts, or change the authenticated target.
An `all` route is a reviewed materialized surface, not an unrecorded wildcard.

### 1.5 Facades preserve parent APIs

A facade export lets a skill parent retain an existing parent-owned interface
ID while targeting one exact export and version of its direct `_rtx` child.
The facade derives the child contract, process binding, and effective version;
it cannot copy or replace them.

The immediate asserted caller is preserved across the facade. The caller must
pass both the parent facade policy and the child export policy. The existing
self exception is evaluated at each export owner: a parent is self at its
facade, but not at the child. The child must explicitly admit the parent when a
parent-owned source calls through the facade.

A facade request selects the child export without namespace-exporting the
`_rtx` namespace. Direct requests to the child ID remain separate and follow
ordinary registration and namespace-routing rules.

### 1.6 Caller references are exact

Caller lists accept:

- a bare globally unique module ID; or
- a leading-dot path relative to the module owning the declaration.

Relative paths use Python-style levels. The first dot starts at the owner;
additional leading dots move through registered parents; the remaining
dot-separated suffix descends through registered local child segments.
Examples: `._rtx` is a skill's code child and `..parser` is the owner's
sibling `parser`.

A relative reference must have a nonempty suffix, may not traverse above the
registration root, and resolves to one exact global module ID through the
certified registration tree. It is not a glob or a relationship-derived caller
set. A topology change requires revalidation and recertification rather than
silently retargeting the reference.

### 1.7 One resolver owns authorization

The canonical graph provides one pure resolver for direct exports, namespace
routes, and facades. Its immutable result contains the immediate caller,
requested and terminal interfaces, implementing source, caller/target
ancestry, lowest common ancestor, crossed namespace gates, resolved relative
callers, effective filters, decision and diagnostic, derived certification
relations, and required certificate set.

Graph validation, interface projection, dispatcher admission, route-smoke
tracing, and certificate currentness consume this result. They do not
reimplement caller resolution, ancestry traversal, facade traversal, or access
intersection.

The public dispatcher still receives an asserted caller identity; repository
validation proves declarations, not process authentication. This remains a
cooperative same-user boundary, not process isolation.

## 2. Repository skill specialization

Every repository-managed skill has exactly one direct non-discoverable code
child rooted at `_rtx/`.

A repository-managed skill is an accepted top-level module directly below a
configured repository `skills/` root with `SKILL.md`, matching root basename
and module ID, and `discovery.mechanism: skill`. Partial combinations are
invalid. Host-supplied `.system` skills remain outside this rule.

The code child has:

- global ID `<skill-id>-rtx`;
- local registration segment `_rtx`;
- marker `_rtx/blueprint.yaml`;
- module-root-relative gateway `__init__.py`, physically
  `_rtx/__init__.py`;
- no `discovery`; and
- no nested modules below `_rtx` in version 5.

This is the sole module-ID/root-basename exception.

| Skill parent owns | `_rtx` child owns |
|---|---|
| `SKILL.md`, host discovery, instruction gateway and instruction-only assets | Executable behavior and machine interfaces |
| Parent interface IDs and facade declarations | Code-owned sources, contracts, exports, and runtime dependencies |
| Documentation used only by the instruction layer | Runtime schemas, templates, executable assets, tests, and fixtures |
| Parent-local authority, hash, and certificate | Runtime authority, code state, hash, and certificate |

Instruction-only skills receive the same empty code boundary with
`_rtx/blueprint.yaml` and `__init__.py`. Behavioral decomposition inside
`_rtx` continues to use behavioral sources rather than more modules.

Repository validators, hooks, schemas that establish the module mechanism,
certification-policy inputs, and similar bootstrap files are repository
infrastructure rather than skill runtime.

## 3. Authored version 5 delta

Version 5 is a breaking blueprint change. A live repository is entirely v4 or
entirely v5; the canonical graph rejects mixed versions.

Every v5 module declares:

- `children`: a mapping from direct-child global ID to a canonical
  `base: module-root` `blueprint.yaml` locator; and
- `namespace_exports`: a mapping from registered direct-child ID to its exact
  version, route filter, `all` or `only` surface, and optional exact-interface
  filters.

Empty mappings are explicit. Every repository-managed skill registers exactly
one `_rtx` child.

Each `exports` value is a closed choice:

- a source export contains `source_interface` and `access`; or
- a facade export contains one exact `facade_interface` target/version and
  `access`.

Schema validation rejects mixed forms. Graph validation proves registration,
ownership, target visibility, versions, attenuation, private-target rejection,
and cycle freedom.

The canonical v5 schema family includes the typed schemas, selector, common
definitions, metadata, annotated authoring entry point
`schema.annotated-draft.json`, template, examples, pooled-review and interface
projection schemas, certificate schema, and `references/blueprint/README.md`.
New fields carry the required `x-famulus` metadata and catalog entries. Every
canonical authoring entry point must select and instruct only v5.

The graph relation matrix adds:

- topology: `contains-module`;
- routing: `routes-child-namespace` and `routes-terminal-module`; and
- facades: `facades-child-export` and `facades-implementing-source`.

`contains-module` alone is not a dependency. Every active namespace surface,
including `all`, materializes its exact resolved interface set and terminal
exporting-module hashes. This makes every reviewing ancestor suspect when a
routed descendant surface or access policy changes.

## 4. Implementation delta by existing owner

### 4.1 Inventory, graph, and projection

Extend `officina.common.blueprint_inventory` rather than adding a second
walker:

1. use its existing bounded, ignore-aware, symlink-safe marker collection;
2. identify top-level modules;
3. follow authored registrations recursively; and
4. reject every nested module marker not consumed exactly once.

Rename root-specific internal fields such as `owner_root` and `skill_root` to
`module_root`. Record parents, children, local segments, ancestry, deepest
ownership, and registration proofs in the canonical graph.

Graph construction indexes nodes, topology, interfaces, and exports before
resolving relationships. Projection and blueprint search use graph IDs and
ancestry rather than root basenames.

### 4.2 Dispatcher and Python runtime

Internal APIs and traces use `caller_module_id` and `target_module_id`;
`target_module_id` is always the graph node ID, never the `_rtx` basename.
The host-facing `--caller-skill` spelling may remain for discoverable parent
calls.

Caller attribution selects the deepest registered module containing the
calling source. Parent instruction code calls as `<skill-id>`; moved code calls
as `<skill-id>-rtx`.

Gateways resolve relative to the terminal source's `module_root`. The existing
Python provider retains its descriptor, snapshot, confinement, tracing, and
fallback mechanisms, but its binding carries three distinct values:

- physical module root and root-relative source path;
- collision-free logical package identity derived from global module ID; and
- logical entrypoint path/module name.

The runner imports the logical entrypoint directly. `module.__file__` and the
compile filename remain the separately validated physical source path.
Logical-package caches and route-smoke cycle keys are isolated by global module
and interface IDs so different `_rtx` packages cannot alias.

### 4.3 Certification and drift

Existing local-hash, append-only history, dependency-first ordering, and
currentness rules remain unchanged except for these v5 deltas:

- authorization requires current certificates for the terminal exporter,
  implementing source, every active route or facade owner, and every parent
  proving the consulted caller/target topology or relative reference;
- active routes record the direct-child dependency and each materialized
  terminal-module dependency;
- facades record the child export and implementing source dependencies;
- code-child logs live under `_rtx/.certificates/`; and
- pooled review, target selection, and drift use global module IDs.

The certificate reader accepts closed payload-v1 histories and payload-v2
entries. The certifier emits only v2; currentness under v5 requires the final
active entry to be v2.

The v5 check registry is exact:

- `deterministic`: `("v5-deterministic", 1)`;
- `route-smoke`: `("route-smoke-dependencies", 2)`; and
- `semantic-review`: `("blueprint-accuracy", 2)`.

Frozen v1 registry identities remain available only for historical validation.

Move the certification-basis manifest to repository bootstrap infrastructure
at `references/certification/certification-basis-roots.json`. Update the
constant and patterns for `validators/skill/**/*.py`, the shared resolver, and
every new or relocated enforcement input. The existing coverage test must
prove that validator files themselves, not only their imports, are
basis-covered.

The certifier route exception remains limited to the exact certifier
caller/interface/target. Issuance uses the existing
`certification_target_postorder` rooted at `skill-certifier`,
`skill-certifier._rtx`, and its implementing sources, including transitive and
external dependencies. The existing read-only skill-maker synchronization
fallback is adapted through its v5 facade.

Verification keys and the secret namespace remain stable parent-level
certification-bootstrap infrastructure. They preserve every historical public
key and grant `skill-certifier._rtx` the sole supported mutation authority.
Per-node certificate logs remain reserved certification outputs rather than
ordinary module content or runtime state.

### 4.4 Standards, validators, and generated tooling

The canonical skill standard becomes `standard_version: 2.0.0`, `revision: 1`.
It replaces, rather than coexists with, live v4 requirements. Frozen historical
standards fixtures remain unchanged and their fidelity tests no longer bind
old statements to live-v2 nodes.

One graph-preflight validator owns topology and authorization errors.
Relationship and interface-ID validators consume its validated graph rather
than independently rebuilding or re-reporting it.

Move `skills/skill-maker/validators/` to repository bootstrap infrastructure
at `validators/skill/`. Rename caller-specific validators and tests from
`dispatch_caller_skill` to `dispatch_caller_module`. Existing hooks,
`validators/runner.py`, staged-index isolation, pre-commit, CI, and
certification remain the execution path; no nested-module hook is added.

Update existing validators only where their present scope is wrong:

- runtime-file checks distinguish executable child files from child
  blueprints, schemas, assets, state, tests, and certificates;
- authority, portability, dependency, and runtime-document checks recurse
  through registered children; and
- instruction prose, taxonomy, and discovery checks remain parent-only.

The existing generator creates parent and child blueprints together. The
syncer renders `SKILL.md` blocks only for discoverable parents, derives facade
contracts, and includes child runtime dependencies without advertising
`_rtx` as a skill. Catalogs and generated user documentation remain
parent-only. Extend `scripts/run-python-tests.py` to include both
`skills/*/tests` and `skills/*/_rtx/tests`; do not introduce a new test
inventory mechanism.

Update the adopted architecture, blueprint, certification/drift, search,
schema-authoring, contributor, skill-maker, and skill-certifier documentation
at cutover.

## 5. Atomic migration and cutover

V5 development uses noncanonical schema and staged-checkout loader entry
points while the live canonical loader remains v4. The converter owns an
immutable private v4 schema and inventory parser.

Reuse the existing migration engine's deterministic dry run, reviewed
manifest, atomic write, rollback, and idempotence patterns. The nested-module
converter must:

1. upgrade every live module and source to v5, adding explicit empty topology
   fields to unchanged non-skill modules;
2. create each skill's `_rtx` child and move code-owned sources, runtime
   assets, authority, state, tests, and fixtures;
3. rebase every module-root-relative gateway, content, locator, binding,
   contract, schema, permission, authority, test, and fixture path;
4. rewrite internal absolute `_rtx` imports to relative imports and reject
   unresolved imports;
5. rename moved source and interface identities into the
   `<skill-id>-rtx` namespace and rewrite exact relationships;
6. convert parent machine exports to facades and apply the access migration
   below;
7. relocate repository validators and update all canonical v5 authoring,
   certification-basis, check-registry, generated-view, and documentation
   artifacts;
8. apply deterministic node-version and dependency-pin rewrites;
9. validate and disposition v1 certificate histories; and
10. emit a complete manifest of moves, identity/version/pin/path/access/import
    rewrites, history dispositions, generated artifacts, and one disposition
    for every committed input file.

Source ownership wins during file classification. Module-owned executable
files and files named by exact moved-runtime path evidence move to the code
child. Parent gateways and recognized instruction-only assets remain with the
parent. Any other module-owned file is reported as `unclassified_files` and
aborts planning; the converter never guesses from an unfamiliar basename or
extension.

### 5.1 Access migration

Allow-all remains allow-all. For each exact permitted skill caller `X`, retain
`X` and add `X-rtx` when a source moving into that child declares the exact
use. A moved source that formerly relied on its module's self exception
likewise adds its new child ID where required. This produces migrated policy
`A'`.

The parent facade and child ceiling both use `A'`. For every restricted
facade, the child ceiling also adds the exact parent. This preserves the
parent export's existing self capability across the facade; it does not grant
the parent's descendants. Retained parent-owned sources may therefore keep
calling through the facade, while parent-only, child-only, and mixed callers
remain distinguishable.

### 5.2 Node and interface versions

The converter maintains separate node-version and interface-version maps.

For nodes:

- a new child, moved source, or renamed identity starts at version 1;
- an existing parent module increments once because its registered boundary
  and export representation change;
- any other existing module increments once when its export access or another
  boundary declaration changes;
- any retained source increments once if its gateway, interface contract,
  dependencies, authority, content ownership, or declared uses change; and
- an unchanged non-skill node whose only change is schema-v5 empty topology
  fields retains its node version.

Node versions rewrite module/source dependencies and certificate subjects.

For interfaces, moving ownership or changing the qualified interface ID does
not reset the contract version. Preserve its existing version when the
contract is semantically unchanged; increment it only when the reviewed
contract changes. Interface versions rewrite `uses_interfaces`, facade targets,
namespace surface pins, and generated interface views.

### 5.3 Certificate history disposition

Before moving any history, the frozen v1 reader and retained keys validate its
canonical encoding, signatures, complete `previous_entry_hash` chain, and
historical subject. Any failure aborts migration.

- Stable nodes outside the certifier bootstrap issuance closure may append v2
  to valid v1 histories.
- Renamed or relocated histories are copied byte-for-byte beneath immutable
  `references/certification-history/v4-cutover/` evidence using their original
  repository-relative log path, excluded from active discovery, and restart as
  v2 under the new identity.
- After deriving the exact v5 certifier postorder, every active v1 log for a
  closure node or its mapped v4 predecessor is archived. The active closure
  therefore starts as the empty valid prefix and is rebuilt
  dependency-first.

The converter manifest records every disposition and complete-file hash.
Because active logs are ignored Git state, every archived ignored log also
produces a hash-bound `remove-active-at-authorized-cutover` state operation.
Candidate construction remains source-read-only; the authorized cutover
consumes these explicit non-Git operations after installing the byte-identical
archive.

### 5.4 Cutover

Before cutover, validate a complete migrated staged checkout through the v5
loader. One commit then:

- migrates every live node;
- switches the canonical schemas and loader to v5;
- installs the new standard, validator layout, certification basis, and check
  registry; and
- regenerates canonical views and manifests.

The repository must contain zero live v4 nodes or v4 authoring entry points.
Certify that clean commit in canonical dependency-first order and verify
post-write currentness. No long-lived mixed-version compatibility path is
supported.

## 6. Acceptance matrix

| Area | Required evidence |
|---|---|
| Topology and ownership | Registered nesting succeeds; unregistered, duplicate-parent, cyclic, symlinked, ignored, and wrong-nearest-parent nesting fails; parent content and authority cannot absorb a child |
| Authorization | Self, parent, sibling, cross-branch, descendant, and unrelated callers behave correctly for exact and relative IDs; every crossed outward route is required; private targets and widening filters fail |
| Facades and surfaces | Original caller is preserved; parent and child checks both apply; direct child access bypasses only the facade filter; `all` materializes terminal surfaces and invalidates every reviewing ancestor |
| Runtime | Parent and child caller IDs are distinct; two `_rtx` packages execute in one trace without cache collision; descriptor and snapshot paths preserve physical `__file__`, relative imports, and resource lookup |
| Certification | Route/facade/topology proof sets are exact; v1/v2 history rules, v5 check registry, basis coverage, bootstrap postorder, corrupt-history failure, key stability, and repository-wide recertification are verified |
| Migration | Dry run equals write manifest; rollback and idempotence hold; access, node/interface version, pin, path, import, and history rewrites are complete; every canonical authoring surface is v5 |
| Tooling | One graph preflight, staged runner isolation, parent-only discovery/catalogs, child-aware validators, generated facade views, and both parent/child test roots are covered |

Representative end-to-end fixtures are:

- `get-weather._rtx`: small code child;
- `list-manager._rtx`: multiple facades and outgoing dependencies;
- `recurring-tasks._rtx`: multiple sources with internal imports; and
- `skill-certifier._rtx`: bootstrap, history, and authority boundaries.

## 7. Repository application

| Decision | Application |
|---|---|
| Apply in v5 | All 35 repository-managed skills: migrate 19 existing `_rtx` roots and create 16 minimal empty code children; exclude host `.system` skills |
| Review later | `common -> certification`; `common -> blueprint-runtime` |
| Conditional only | Combined `officina -> execution`; registering existing `common` under an `officina` parent; `officina -> migration`; a `references` parent |
| Do not promote | Schema/source/package micro-splits, runtime subpackages, bootstrap infrastructure, compatibility wrappers, tests, docs, generated/build output, worker repositories, or independently discoverable skills under umbrella parents |

Existing `_rtx` roots:

`connect-google`, `regenerate-blueprints`, `install-assistant-tools`,
`pdf-to-markdown`, `skill-maker`, `recurring-tasks`, `daily-plan`,
`initialize-tdd`, `find-handoff-candidates`, `list-manager`,
`skill-certifier`, `get-weather`, `skill-drift`, `math-dependency-graph`,
`email-triage`, `email-client`, `bib-audit`, `cloud-files`, and `g-calendar`.

Skills receiving minimal empty children:

`git-workflow`, `update-standards`, `loose-mode`, `prepare-handoff`,
`proof-audit`, `latex-workshop`, `technical-flow-review`, `notation-review`,
`tool-applicability`, `formal-prose-review`, `fix-bisync`, `wrap-up`,
`refactor-skills`, `make-tex-docstring`, `hook-maker`, and `tight-mode`.

The deferred candidates remain deferred because existing behavioral-source
boundaries already provide most of their useful encapsulation. Promote one
only when it needs an independently addressable namespace or authority policy,
not merely because its files form a recognizable package.
