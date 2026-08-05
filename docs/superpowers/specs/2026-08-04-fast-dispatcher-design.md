# Direct-Blueprint Dispatcher Design

**Date:** 2026-08-04
**Status:** Implemented and independently audited

## 1. Goal

Make dispatcher a small, deterministic router and authorization checker.
Every invocation must resolve only the requested caller, target, namespace
boundaries, and process binding. Its cost must depend on module nesting depth,
not repository size.

Dispatcher has exactly two responsibilities:

1. decide whether the declared caller may invoke the requested exported
   interface; and
2. compile and launch that interface's declared process binding.

Dispatcher does not repair, synchronize, index, certify, scan, or perform
repository-wide validation. It performs only the route-local structural checks
needed to reject malformed relevant state. Certification currentness remains
warning-only.

## 2. Design summary

The design replaces runtime graph and snapshot lookup with direct blueprint
lookup:

```text
repository root
  + root-level officina.toml
  + canonical dotted module ID
  + registered child segments
  + configured module lookup roots
  = exact blueprint path and ancestry
```

For example:

```text
module ID:     list-manager._rtx
module root:   <repo>/skills/list-manager/_rtx
interface ID:  list-manager._rtx.interface.read-list
```

The parent blueprint registers `_rtx` as a child and namespace-exports the
selected child interfaces. The child owns the public interface, its access
policy, its source binding, and its contract. There are no facades or
parent-owned aliases.

One dispatch reads only the blueprints on the caller and target ancestry paths,
the terminal target blueprint, and the exact behavioral-source blueprint.

## 3. Governing invariants

The following rules define the architecture:

1. **Paths define identity.** A canonical module ID is its module-root-relative
   directory path joined with dots.
2. **Repository configuration defines roots.** Dispatcher receives one exact
   `officina.toml`; the file's directory is the repository root and its
   `modules.roots` values are the only runtime lookup roots.
3. **Registration declares inventory.** Every parent blueprint lists every
   direct child; dispatcher never lists directories to discover children.
4. **Namespace export routes outward.** A registered child is externally
   reachable only through the required parent namespace exports.
5. **The child owns the interface.** Parents do not copy, rename, or facade a
   child's interface.
6. **Authorization remains hop-local.** Each accepted namespace owner becomes
   the caller evaluated at the next hop.
7. **Relevant defects fail closed.** Unrelated repository state is not read and
   cannot delay or deny the request.
8. **Certification is advisory.** It can warn but cannot authorize, deny,
   repair, or trigger expensive work.

## 4. Repository configuration and module lookup roots

### 4.1 Root configuration

Every dispatchable repository contains one user-adjustable `officina.toml` at
its root:

```toml
schema_version = 1

[modules]
roots = [
  "skills",
  "src/officina",
]
```

`officina.toml` is authored configuration, not a generated manifest. It may
grow to hold other user-adjustable repository settings, but version 1 contains
only `schema_version` and `modules.roots`. Unknown keys fail strict validation
until a later schema version defines them.

The absolute `officina.toml` path is an explicit dispatcher bootstrap input.
Its parent directory is the repository root. Dispatcher does not inspect the
`AI` environment variable, search the current directory, walk parent
directories, or infer a repository root from unrelated process state.

The managed runtime pointer gains one trusted `repository_config` field holding
the absolute path of this file. Activation validates that the file and every
configured root exist and are confined beneath its parent repository. The
stable resolver reads the pointer and injects the path as a fixed dispatcher
argument; users and target interfaces cannot override it. A development
launcher injects the absolute path belonging to its checkout. Programmatic and
nested dispatch preserve the same resolved path in runtime context rather than
rediscovering it. The managed runtime release remains the Python/dependency
environment; it need not duplicate the repository's module trees.

One `officina.common.repository_configuration` module owns this contract. It
parses TOML through the existing `officina.common.toml_io` boundary and returns
one typed configuration value. Its dispatcher path performs only the small v1
field/type/path checks. Offline validation applies the central configuration
JSON Schema to the same parsed mapping. Parity tests prove both paths accept
and reject the same v1 documents; no second configuration semantics is
permitted.

### 4.2 Dispatchable module roots

Dispatcher resolves `modules.roots` relative to the directory containing
`officina.toml`. In this repository the configured roots are:

```text
<repo>/skills
<repo>/src/officina
```

These configured paths are the only runtime blueprint lookup roots. The first
contains skill modules; the second contains first-party Officina modules such
as `common`, `install`, and `wakeup`. Another repository may use paths such as
`skills` and `officina` without changing dispatcher code.

Each configured root must be a unique, nonempty, repository-relative path with
no `..` segment. Its target must be an existing non-symlink directory, every
path component must remain confined beneath the repository root, and no path
component may be a symlink. Missing, duplicate, absolute, escaping, malformed,
or unsupported configuration fails closed.

For a top-level module ID, dispatcher checks the corresponding
`<lookup-root>/<module-segment>/blueprint.yaml` under each configured root. Exactly
one regular, confined blueprint must match. Zero matches is missing state;
multiple matches is an identity collision. Both fail closed.

Python import roots and blueprint lookup roots are related but distinct. The
runtime continues to make installed `officina` code importable through its
Python environment. Blueprint authorization uses only the paths declared in
`officina.toml`.

Repositories may contain non-runtime blueprint nodes elsewhere, including
reference and standards nodes. Path-derived identity applies only to modules
under configured dispatchable roots. Offline nodes retain their authored
globally unique IDs and marker-based inventory rules; they are not dispatcher
targets. Offline validation checks collisions across both identity domains.
Adding a directory of offline nodes to `modules.roots` is a schema migration,
not an automatic way to make those nodes dispatchable.

## 5. Canonical module identity

### 5.1 Dotted path identity

A module ID is the directory path beneath its dispatchable module root with
path separators replaced by dots:

```text
<repo>/skills/list-manager/blueprint.yaml
    -> list-manager

<repo>/skills/list-manager/_rtx/blueprint.yaml
    -> list-manager._rtx

<repo>/src/officina/common/blueprint.yaml
    -> common
```

Every segment must equal its directory name exactly. The permitted segment
grammar retains ordinary lowercase hyphenated names and the standardized
`_rtx` child segment. Empty segments, `.` and `..`, absolute paths, separators
inside segments, and reserved `interface` and `source` segments are invalid.

The schema parses exported interfaces at the reserved `.interface.` marker and
source interfaces at `.source.`. Everything before that marker is the complete
canonical module ID.

### 5.2 Identity validation

Runtime validates every blueprint it reads against its expected identity. A
blueprint at `list-manager/_rtx/blueprint.yaml` must declare
`id: list-manager._rtx`. A mismatch fails the invocation.

Repository-wide validation additionally proves global uniqueness across the
configured runtime roots and all offline blueprint nodes.

## 6. Explicit child registration

Child registration remains required because a module blueprint must completely
describe its direct module inventory. The registration contains only the local
child segment:

```yaml
id: list-manager

children:
  _rtx: {}
```

Given parent ID `list-manager`, parent root `skills/list-manager`, and child key
`_rtx`, the derived facts are:

```text
child ID:    list-manager._rtx
child root:  skills/list-manager/_rtx
blueprint:   skills/list-manager/_rtx/blueprint.yaml
```

The current child locator object is removed. Its `base` and `path` repeat facts
that the parent ID, parent root, and child segment already determine.

Dispatcher follows registration keys; it never calls `ls`, walks a directory,
or searches for markers. Repository-wide validation performs the complementary
strict checks:

- every registered child blueprint exists and declares the derived ID;
- every physically nested module blueprint is registered by its direct parent;
- one child has exactly one parent;
- no registration cycle or unsafe segment exists; and
- nearest physical containment agrees with registration.

Registration alone makes the child addressable inside the registered subtree.
It does not expose the child namespace outside that subtree.

## 7. Namespace-only child exports

### 7.1 No facades

The `facade_interface` export form is removed. Every exported interface is
owned by exactly one module and binds directly to one source interface:

```yaml
exports:
  list-manager._rtx.interface.read-list:
    access:
      allow_all_modules: false
      allowed_callers:
      - list-manager
    source_interface: list-manager._rtx.source.yaml-store.interface.read-list
```

There is no second parent-owned name, duplicated access policy, facade version
pin, facade caller transition, or facade relationship.

Parent modules may retain their own direct source exports, such as instruction
or gateway interfaces they actually implement. Only facade aliases disappear.

### 7.2 Namespace exports

A parent exposes a registered child's namespace through `namespace_exports`:

```yaml
namespace_exports:
  _rtx:
    version: 1
    access:
      allow_all_modules: true
      allowed_callers: []
    surface:
      only:
        list-manager._rtx.interface.read-list: 1
```

The namespace key must name a registered direct-child segment. `surface.only`
is required and contains exact descendant interface IDs and versions. It must
be nonempty. The `surface.all` form is removed: a new child export must never
become reachable merely because a blueprint changed while certification is
advisory.

Optional interface-specific route filters remain supported. Namespace and
interface-specific filters may narrow child authority but cannot expose a
private source interface or bypass the terminal child export.

Dispatcher checks only whether the requested interface and version occur in
the explicit `only` mapping. Offline authoring tools may propose or refresh
that mapping, but only an authored blueprint edit changes the runtime surface.

### 7.3 Discovery and presentation

Top-level skill modules remain host-discoverable. Their authored and generated
interface views include interfaces reachable through registered,
namespace-exported descendants, using the descendants' canonical IDs.

Tools can enumerate a module's declared topology and exported surface by
reading `children`, `namespace_exports`, and the referenced child blueprints.
They do not need filesystem discovery.

A non-discoverable child may be invoked through its canonical interface ID only
when the complete required namespace route exists. Host discovery of the child
itself is neither required nor implied.

Host calls continue to assert one eligible, discoverable top-level skill as the
caller. Nested runtime calls assert the canonical ID of the immediate calling
module. Namespace-exported children may be targets without becoming eligible
host caller identities.

## 8. Authorization semantics

The redesign preserves current caller, ancestry, namespace, and sibling
standards. Removing facades removes only facade-specific evaluation.

### 8.1 Access predicate

For current immediate caller module `x` and an access policy owned by module
`y`, the policy admits `x` exactly when:

```text
x == y
or access.allow_all_modules
or ancestry(x) intersects resolved(access.allowed_callers)
```

An empty, non-public allowlist is private to the policy owner. Naming a module
admits that module and its registered descendants; it does not admit its parent
or siblings.

### 8.2 Canonical and relative caller references

Caller allowlists continue to accept:

- one canonical dotted module ID; or
- one leading-dot reference resolved from the policy owner.

The current relative-reference meaning is preserved:

```text
._rtx     -> the owner's registered `_rtx` child
..parser  -> the owner's registered sibling `parser`
```

Because identity and registration are deterministic, dispatcher resolves these
references by manipulating the owner's ID segments and verifying the relevant
registration chain. It does not need a repository graph.

### 8.3 Crossed namespace gates

Dispatcher derives caller and target ancestry from their canonical IDs and
verified registration chains. Their longest common prefix is the lowest common
ancestor.

Only target-side namespace boundaries below that ancestor are crossed. This
preserves current behavior:

- a parent or sibling already inside a registered subtree does not cross that
  subtree's outward namespace gate;
- a caller in another branch crosses target-side gates below the common
  ancestor; and
- an unrelated caller crosses the complete target ancestry.

### 8.4 Hop-local caller identity

Authorization remains hop-local. Dispatcher starts with the declared immediate
caller. For each crossed namespace gate, from outermost to innermost:

1. evaluate the gate and any interface-specific filter against the current
   caller;
2. deny immediately if either filter rejects; and
3. on acceptance, make the route owner the immediate caller for the next hop.

The terminal child export evaluates the final hop-local caller. This is the
existing delegated-boundary rule and is required by current authorization
tests. Consequently a child export is an authority ceiling over its immediate
caller, not over the original caller: an admitted namespace owner may delegate
to the next hop. Tests must preserve that distinction explicitly.

Sibling behavior follows automatically. A direct sibling call crosses no
common-parent outward gate; the terminal target can admit the exact sibling or
their common ancestor, since the caller's ancestry contains that ancestor.

Source identity and `uses_interfaces` remain static validation and
certification facts. They do not independently grant runtime permission.

## 9. Direct dispatcher algorithm

For one invocation, dispatcher performs the following bounded sequence:

1. receive and strictly load one absolute `officina.toml` path;
2. derive the repository root and configured module roots from that file;
3. parse the target interface into canonical target module ID and interface
   name;
4. resolve the declared caller's top-level blueprint under the configured module
   roots;
5. follow and verify caller child registrations to establish caller ancestry;
6. resolve and verify the target's top-level blueprint and child registration
   chain;
7. calculate the caller/target lowest common ancestor;
8. read and evaluate only the crossed target-side namespace declarations;
9. find the exact exported target interface in the terminal module blueprint;
10. evaluate the terminal export access policy;
11. follow the module's exact `sources` entry to the behavioral-source
    blueprint;
12. verify the requested source interface and version;
13. compile caller arguments against the source's declared process binding;
14. emit only bounded advisory certification diagnostics; and
15. launch the gateway through the runtime provider.

The number of probes and reads is bounded by the configured-root count, caller
depth, target depth, registration paths needed by evaluated relative caller
references, and one selected source. Shared ancestry blueprints are read once
per invocation. Repository size and the number of unrelated interfaces do not
affect runtime work.

Relevant YAML is decoded and checked against a small dispatcher-owned set of
required field, type, identity, access, and binding invariants. Dispatcher does
not load the repository JSON schemas or call the repository validator. The
offline validator remains authoritative for complete schema conformance.
The managed production runtime uses PyYAML's LibYAML-backed safe loader and
activation rejects a runtime that lacks it; offline tooling may use the pure
Python safe loader. This is a runtime packaging requirement, not a routing
manifest.

## 10. Runtime safety and execution

Every opened blueprint, source file, and package path must be a regular,
non-symlinked path confined beneath its derived module root. Identity/path
collisions, path escapes, missing files, and unsupported binding forms fail
closed.

Dispatcher opens only the selected gateway and module-local paths needed to
launch it. It does not recursively snapshot or hash the target Python package.
Python gateways run with a lazy confined importer rooted at the selected
module. For each import actually requested, the importer opens path components
without following symlinks, requires regular files, proves confinement beneath
the module root, and executes the bytes it opened. It supports packages and
relative imports inside that module under a collision-free synthetic package
name derived from the canonical module ID. Standard-library modules, declared
third-party distributions, and first-party `officina` packages load only from
the managed runtime's pinned environment. That environment contains Officina's
pinned core dependencies plus the module dependencies derived from
`runtime_dependencies.json`. Repository modules outside the selected module
are rejected. Cross-module behavior continues to require dispatcher rather
than direct private imports. Import safety is route-local and lazy; it does not
require an eager package walk.

After gateway launch, stdout, stderr, and exit status pass through unchanged.

## 11. Runtime state that is removed

The invocation path must not use or maintain:

- dispatch manifests;
- activation snapshots or generations;
- routing-snapshot builders or pointers;
- route catalogs or route caches;
- blueprint synchronizers;
- repository inventory;
- repository-wide graph construction;
- automatic repair or rebuilding;
- Git inspection;
- node hashing;
- certification derivation;
- network access; or
- runtime routing writes.

Installation installs the dispatcher runtime and declared dependencies. It
does not generate or activate routing state. Its managed runtime pointer records
the absolute path of the activated repository's validated `officina.toml` and
makes that path available to the stable launcher. The
`runtime_dependencies.json` dependency manifest remains installation input,
not dispatcher routing state. Its generator must aggregate dependencies from
every executable interface owned by a top-level skill or any of its registered
descendants, whether or not a namespace route exposes that interface. The
manifest advances to v2 and keys entries by canonical interface ID so equal
local interface names in different descendants cannot overwrite each other.
The installer consumer migrates with it. Tests must prove a private or
child-only dependency is retained after its parent facade is removed.

The dispatcher itself is a separate first-party runtime artifact, not an
interface dependency. Candidate construction builds a versioned Officina wheel
from the selected source revision and installs it, together with pinned core
dependencies such as PyYAML/LibYAML, before installing v2 manifest-derived
module dependencies. Activation verifies the wheel identity, source revision,
and importability in the clean candidate environment, then updates the managed
runtime pointer. A candidate that cannot execute `officina.dispatcher.cli`
never activates.

There is no warm-up route. First and repeated invocations use the same direct
algorithm, with only ordinary operating-system file caching differing between
runs.

## 12. Offline validation, certification, and visualization

Repository-wide work remains available outside dispatcher:

- strict schema and topology validation;
- complete graph construction;
- certification and currentness derivation;
- generated interface projection and documentation;
- route-smoke validation; and
- repository visualization.

These tools derive topology from canonical identities plus explicit child
registration. They may scan the repository and help author explicit `only`
surfaces because they are explicit offline operations, but they do not create
a second runtime routing authority.

Dispatcher shares schema-level parsing and the authorization predicate where
that reuse remains small and side-effect free. It must not call an offline
entry point or inherit graph-building behavior through a shared helper.

Certification data is advisory during dispatch. Runtime context may provide an
already-verified, in-memory status view produced outside dispatcher.
Dispatcher consults that view only for the caller ancestry, crossed target
routes, terminal module, and implementing source: work is proportional to
route depth. It never reads certificate logs, checks Git, recomputes hashes,
follows certification dependencies, or waits for certification repair. If no
view is supplied, or any relevant node is absent, dispatcher reports
`certification-status-unavailable`; missing, stale, expired, malformed,
unknown, or unavailable status is warning-only.

## 13. Failure behavior

Dispatcher returns a stable structured error before launch for:

- missing or ambiguous repository/module roots;
- missing, malformed, unsupported, or unsafe `officina.toml`;
- invalid canonical module or interface syntax;
- missing top-level module;
- missing child registration;
- missing or malformed relevant blueprint;
- blueprint identity/path mismatch;
- unsafe, escaping, symlinked, or non-regular relevant paths;
- missing required namespace export;
- namespace surface or version mismatch;
- access denial at a named namespace or terminal boundary;
- unknown or private target interface;
- missing or inconsistent source binding;
- requested interface-version mismatch;
- invalid caller arguments; or
- unsupported process binding.

The error identifies the relevant authored blueprint condition. Dispatcher does
not suggest that it repaired, rebuilt, synchronized, or activated anything.
Unrelated malformed modules are not read and therefore do not affect the call.

## 14. Blueprint schema changes

This is a breaking schema revision and requires a new schema version. The new
module schema must:

1. allow canonical dotted hierarchical module IDs;
2. reserve `.interface.` and `.source.` as ID delimiters;
3. define child keys as direct local module segments;
4. replace child locator values with empty registration records;
5. require explicit `children` and `namespace_exports`, including empty
   mappings;
6. remove the `facade_interface` export form;
7. require every export to contain `source_interface` and `access`;
8. preserve exact and leading-dot caller references under the new identity
   grammar; and
9. require explicit nonempty `only` surfaces and remove `all`;
10. preserve route access and interface-specific namespace filters.

Schema metadata, templates, authored standards, validators, and examples must
change together. Frozen historical schemas and fixtures remain immutable.

The central repository-configuration schema must add the strict version 1
`officina.toml` shape. Runtime performs an equivalent small TOML structural
check without loading JSON Schema; offline validation checks the document
against the central schema.

## 15. Repository migration

The migration is a clean cutover. Old facade addresses are not runtime aliases.

For each `_rtx` child:

```text
<skill>-rtx                       -> <skill>._rtx
<skill>-rtx.interface.<name>      -> <skill>._rtx.interface.<name>
<skill>-rtx.source.<source>       -> <skill>._rtx.source.<source>
```

The `_rtx/` directories and their Python package names remain unchanged.

The migration must:

- add root-level `officina.toml` with `modules.roots = ["skills",
  "src/officina"]`;
- extend the managed runtime pointer, stable resolver, CLI, development
  launcher, and nested runtime context to carry the exact validated
  `officina.toml` path;
- build and verify a pinned first-party Officina wheel plus core dependencies
  in each managed runtime candidate before pointer activation;
- remove dispatcher repository-root discovery through `AI`, cwd, and parent
  searches;
- rewrite every child module, source, and interface ID;
- replace parent `children` locators with direct child registrations;
- replace every parent facade with the corresponding namespace-exported child
  interface;
- preserve parent direct source exports that the parent genuinely implements;
- rewrite `allowed_callers`, relative references where necessary,
  `uses_interfaces`, dependencies, and nested dispatch declarations;
- update generated `SKILL.md` interface blocks and other consumer projections;
- update installed scripts, recurring jobs, hooks, documentation, tests, and
  user-facing command examples from every removed facade ID to its child ID;
- update process-binding and runtime metadata carrying module IDs;
- migrate the runtime-dependency generator and installer to manifest v2 so all
  registered-descendant dependencies remain installed without a facade and
  canonical interface IDs cannot collide;
- replace unrestricted package-path insertion with the lazy confined importer;
- rewrite module-local flat absolute imports such as `from _run_record` and
  `from _fs_links` as package-relative imports, remove runtime `sys.path`
  mutation, and reject its return in mechanical checks;
- remove facade relations from graphing, certification, and visualization;
- remove dispatcher snapshot, catalog, synchronization, and repair paths;
- remove routing-snapshot activation from installation;
- update architecture documentation and examples;
- update certification-basis coverage for the new dispatcher and schema; and
- treat pre-migration certificates as stale advisory state pending
  recertification.

Before code migration, a committed, machine-checked cutover inventory must list
every old facade ID, its canonical child ID, and every repository consumer of
the old ID. Completeness is proved by rejecting any live old ID after rewrite.
The inventory is offline migration evidence, not runtime routing or diagnostic
state. There are no compatibility aliases; an old address returns the same
plain `unknown-interface` error as any other unknown address.

The cutover updates the normative authority set together: `docs/architecture.md`,
`docs/skill-blueprints.md`, the live blueprint schema and schema metadata under
`references/blueprint/`, `references/node-standards/module.standard.yaml`, the
blueprint graph and interface-projection consumers, the runtime-dependency
generator, and `src/officina/common/certification_hashing.py`'s versioned check
registry plus certification-basis roots. Related assurance mappings and tests
must refer to the new check versions. Frozen migration schemas, standards
fixtures, and historical certificates remain byte-for-byte historical.

Dispatcher accepts only the new live schema version. The full migrated
repository is validated before the new runtime pointer is activated. If a
relevant route later contains an old or mixed-version blueprint, dispatcher
rejects that route; it does not translate or repair it.

The existing snapshot-based fast-dispatcher implementation is superseded by
this design and must be removed or rewritten rather than extended.

## 16. Performance contract

Dispatcher resolution cost must be independent of total repository size.

The invocation path is required to have:

- one top-level root probe per configured module root;
- one small `toml_io` configuration read;
- blueprint reads proportional only to configured-root probes, caller depth,
  target depth, and evaluated relative-reference registration paths;
- one selected behavioral-source blueprint read;
- no repository walk, glob, recursive package traversal, or unrelated schema
  load;
- no routing writes, locks, rebuilds, or activation checks;
- no Git or network operations; and
- no certification computation.

Initial targets on the reference Linux host are:

- warm in-process resolution median below 50 milliseconds;
- fresh-process dry-run median below 100 milliseconds;
- fresh-process dry-run p95 below 150 milliseconds; and
- no first-route penalty beyond ordinary filesystem-cache variation.

These bounds reflect the cost of decoding the authored YAML directly,
including the current roughly 1,900-line list-manager source blueprint. A
future tighter target would require a smaller authored binding format; it must
not be achieved by reintroducing generated routing state.

Measurements cover repository-root selection, relevant blueprint reads,
authorization, and process-binding compilation. They exclude gateway execution
and external-service latency.

Performance tests must also assert operation counts and repository-size
independence so faster hardware cannot hide an accidental scan.

## 17. Verification requirements

### 17.1 Identity and registration

Tests must cover configured-root lookup, root collisions, dotted identity
parsing, directory/ID mismatch, registered children, missing children,
unregistered nested modules, unsafe segments, and relative caller resolution.

Configuration tests must cover the valid repository document, explicit launcher
propagation, alternate configured roots, unknown schema versions and keys,
missing or duplicate roots, absolute and escaping paths, symlink traversal, and
proof that cwd and `AI` do not influence resolution.

Installer tests must launch `officina.dispatcher.cli` from a clean candidate
environment with no source-checkout `PYTHONPATH`, and must reject activation
when the pinned Officina wheel, source revision, core parser, or repository
configuration is missing or inconsistent.

### 17.2 Authorization equivalence

The direct resolver must preserve current non-facade authorization behavior
for:

- self, public, exact-caller, and ancestor admission;
- parent, child, descendant, and sibling calls;
- cross-branch and unrelated callers;
- lowest-common-ancestor gate selection;
- explicit `only` and interface-specific namespace filters;
- hop-local namespace caller replacement;
- private targets and immediate-caller child authority ceilings, including
  namespace-owner delegation;
- direct host restrictions; and
- interface-version and process-binding selection.

Facade declarations and old facade interface IDs must fail under the new
schema rather than silently changing meaning.

### 17.3 Runtime isolation

Tests must replace repository inventory, graph building, route catalogs,
snapshot loading, Git, certification derivation, writes, and network access with
hard failures, then prove fresh and repeated dispatcher invocations still
resolve and execute.

Filesystem tracing must confirm that dispatcher opens only the expected
configured-root candidates, relevant ancestry blueprints, terminal
module/source inputs, and runtime files. It must not traverse unrelated module
or package trees.

Gateway tests must exercise package and relative imports within the selected
module, reject symlink and path-escape imports, reject imports from another
module, reject runtime `sys.path` mutation, and prove imported files are opened
lazily rather than recursively.

### 17.4 Performance

Fresh-process benchmarks must exercise both a top-level direct interface and a
namespace-exported `_rtx` interface. Synthetic repositories with increasing
numbers of unrelated modules must not increase relevant read counts or
materially change resolution time.

### 17.5 Repository migration

The migrated repository must contain:

- no `facade_interface` declarations;
- no live `<skill>-rtx` module IDs;
- no old parent alias where the implementation is child-owned;
- no dispatch snapshot or route-catalog dependency;
- one valid root-level `officina.toml` and no ambient repository-root fallback;
- complete child registration and namespace surfaces;
- preserved private and child-only entries in v2
  `runtime_dependencies.json`; and
- passing strict schemas, standards, projections, route-smoke tests, and full
  repository validation.

## 18. Non-goals

- repairing, synchronizing, or rewriting blueprints during dispatch;
- locating repositories from cwd, parent searches, or ambient `AI` state;
- repository-wide validation during dispatch;
- preserving old facade interface IDs as compatibility aliases;
- changing current hop-local caller, ancestry, sibling, or namespace policy;
- using source identity or `uses_interfaces` as runtime permission;
- deriving certification or drift state during dispatch;
- process isolation against hostile same-user code; and
- optimizing gateway execution or external-service latency.
