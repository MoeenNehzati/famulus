# Skill Blueprint Guide

## Authority model

Each skill has exactly one canonical graph root:

```text
skills/<skill>/blueprint.yaml
```

The root owns skill-level facts and points to interface blueprints. It does not
inline facts owned by those interfaces. Every subordinate node binds exactly
one regular file and stores its own contract in a hidden sidecar beside that
file.

Canonical blueprint types are:

- `skill`
- `llm-interface`
- `machine-interface`
- `behavior-source`

Generated pooled reviews and health records are not blueprint types and never
define graph edges.

Legacy monolithic roots remain accepted during migration through
`legacy-skill.schema.json`. New authoring uses schema version 2.

The dispatcher keeps that migration boundary explicit. On Windows, where the
POSIX descriptor primitives required for no-follow runtime snapshots are
unavailable, it may read one legacy monolithic root snapshot, select the
requested interface, and use the preexisting portable legacy invocation path
only after that captured snapshot passes `legacy-skill.schema.json` and the
selected interface declares `platform_support.windows: true`. Schema-version-2
roots, sidecars, and runtime bindings do not use this compatibility path;
dispatch fails closed when descriptor-safe primitives are unavailable. Other
hosts also fail closed if those primitives are unexpectedly unavailable.

## Schema family

`references/blueprint/schema.json` is the compatibility entry point. Concrete
authoring and validation rules live in:

- `skill.schema.json`
- `llm-interface.schema.json`
- `machine-interface.schema.json`
- `behavior-source.schema.json`
- `common.schema.json`
- `health.schema.json`
- `pooled-review.schema.json`

`schema-meta.json` defines the required per-field metadata protocol and maps
every `related_validation_rules` ID to its enforcing validator and tests. Each
public typed field states:

- whether it is required or optional;
- whether it participates in certified contract hashes;
- whether and how it appears in generated authoring templates;
- authoring guidance;
- the mechanical validation rules that enforce it.

The schema family is therefore the complete source for document shape,
authoring-template generation, contract-hash inclusion, and validator
traceability. Python validators add filesystem and graph checks that JSON
Schema cannot perform by itself.

`template.yaml` is the committed artifact-layout manifest, not a root blueprint
or a second authoring specification. Concrete authoring templates are generated
from each type schema. The schema hash covers every concrete schema, the
annotation protocol, the compatibility/annotated schema inputs, and this
manifest; a missing required input is an error rather than a smaller valid
schema family.

## File layout

For a single node bound to `foo.py`:

```text
foo.py
.foo.py.blueprint.yaml
.foo.py.health.json
```

If two interfaces bind the same file, all sidecars are qualified by local node
name:

```text
foo.py
.foo.py.first.blueprint.yaml
.foo.py.first.health.json
.foo.py.second.blueprint.yaml
.foo.py.second.health.json
```

The default LLM interface is ordinary, explicit, and file-backed:

```text
SKILL.md
.SKILL.md.blueprint.yaml
.SKILL.md.health.json
```

The skill root keeps its established unsuffixed names:

```text
blueprint.yaml
.last_audit.json
```

Directories cannot be blueprint bindings. A collection that needs graph
identity must expose a concrete dispatcher, manifest, index, or README file and
bind that file.

## Skill root

The root contains only skill-level metadata and version-pinned locators for its
interfaces:

```yaml
schema_version: 2
blueprint_type: skill
id: example-skill
category: development-assistant
role: automation
kind: tool
interfaces:
  - interface: example-skill.llm.default
    version: 1
    blueprint:
      base: skill-root
      path: .SKILL.md.blueprint.yaml
  - interface: example-skill.machine.run
    version: 1
    blueprint:
      base: skill-root
      path: _rtx/._runner.py.blueprint.yaml
```

Every root must declare an `llm.default` edge. The root ID must equal the skill
directory name. A root edge does not repeat the target interface's description,
binding, access policy, behavior sources, IO, ownership, or dependencies; those
facts belong to the target sidecar.

## LLM interfaces

An LLM interface owns its binding and behavior dependencies:

```yaml
schema_version: 2
blueprint_type: llm-interface
id: example-skill.llm.default
version: 1
description: Primary LLM-facing skill instructions.
binding:
  kind: instruction-file
  path: SKILL.md
allow_all_skills: true
allowed_callers: []
uses_interfaces:
  - interface: example-skill.machine.run
    version: 1
behavior_sources:
  - source: example-skill.source.policy
    version: 1
    blueprint:
      base: skill-root
      path: references/.policy.md.blueprint.yaml
    reason: Defines the policy used by this interface.
direct_io:
  reads: []
  writes: []
  network: []
owns_filesystem: []
```

The edge owns `reason`, because the reason explains the consumer's use. The
behavior-source node owns intrinsic facts about itself.

## Machine interfaces

Machine interfaces remain public under `skill.machine.name` regardless of
implementation language. Python files live under `_rtx`; command files live
under `_cx`.

Python binding:

```yaml
binding:
  kind: python-entrypoint
  path: _rtx/_runner.py
  symbol: Interface
  args_prefix: []
```

Command-file binding:

```yaml
binding:
  kind: command-file
  path: _cx/_refresh
  args_prefix: []
```

Command files are executed directly. Inline shell strings, direct Bash command
declarations, and `bash -c` bindings are invalid. `_cx` is intentionally an
opaque implementation namespace, not a public interface namespace.
Python and command bindings cannot contain parent traversal and must resolve
inside their owning skill's `_rtx` or `_cx` directory, including after symlink
resolution. Both are tracked files; command files must also be executable.
Hand-authored LLM text refers to the canonical machine interface, never the
opaque `_cx` path.

`platform_support` states where the machine interface itself is supported. A
runtime dependency's `platforms` map states only where that dependency applies,
so one portable interface may declare different service or binary dependencies
for different hosts. In contrast, every `uses_interfaces` target is required by
the caller: a machine interface cannot claim support on a platform where a
required interface is unsupported.

`patterns` constrain dispatcher argv forms. They do not enforce which bucket,
account, database, host, or subsystem the implementation can access. Scope
enforcement must come from the implementation or downstream service boundary.

`direct_io` is descriptive immediate IO and likewise does not prove subsystem
confinement. Its live subject data and declaration are excluded from certified
contract hashes. `owns_filesystem` identifies writer authority and permitted
readers; machine and LLM interfaces may both own files.

## Behavior sources

Behavior-source nodes bind files, never directories:

```yaml
schema_version: 2
blueprint_type: behavior-source
id: example-skill.source.policy
version: 1
description: Defines the policy used by interfaces.
binding:
  kind: file
  path: references/policy.md
content: config
format: markdown
uses_behavior_sources:
  - source: example-skill.source.rules
    version: 1
    blueprint:
      base: skill-root
      path: references/.rules.md.blueprint.yaml
    reason: Supplies the detailed rules indexed by this policy.
uses_interfaces:
  - interface: other-skill.machine.lookup
    version: 1
```

Behavior-source nodes may point to other behavior-source nodes and may declare
canonical interfaces they use. Both edge kinds are version-pinned and
recursively participate in health. LLM-interface and behavior-source bodies
must use canonical interface IDs instead of bare cross-skill names, and every
interface ID named in a body must appear in that node's own `uses_interfaces`.

Behavior-source visibility follows physical ownership. A skill may directly
reference behavior sources declared under its own `skills/<skill>/` directory.
Repository-owned sources under `references/` use `references.source.*` IDs and
are visible to every skill. A node cannot directly reference a behavior source
under another skill; that skill must expose the behavior through a declared
interface instead. The source namespace must match its physical location.

## Relationship validation

Repository validators check more than isolated YAML shape:

1. Root and node IDs are canonical and namespace-correct.
2. Locators resolve to reachable subordinate sidecars.
3. Every subordinate binding is an existing regular file.
4. Sidecar names match the bound file and shared-file qualifier rule.
5. Edge target type and pinned version match.
6. Cross-skill access control permits the consumer.
7. Machine interfaces use only machine interfaces.
8. LLM interfaces use same-skill machine interfaces or LLM interfaces.
9. Behavior-source edges target behavior sources; source interface edges
   target declared interfaces.
10. Duplicate edges and local or cross-skill cycles are rejected.
11. Health files, blueprint files, pooled reviews, and directories cannot be
    bound as content nodes.

Graph discovery starts only from `skills/<skill>/blueprint.yaml` and follows
declared locators and interface IDs. Validators do not scan pooled reviews or
health records for authority.
Targeted audit and drift load only that root's reachable closure. A malformed
unrelated skill cannot block the target, while a malformed reachable provider
does. A repository-root behavior-source locator resolves only under
`references/`; direct locators into another skill's local behavior sources are
rejected. Repository-owned sources retain one canonical binding, sidecar, and
health record across consumers.

## Health records

`skill-audit` certifies nodes bottom-up. Every record contains:

- subject ID, type, version, blueprint path, and binding path;
- raw `blueprint_file_hash`;
- schema-governed `blueprint_contract_hash`;
- bound-file and node-local hashes;
- direct dependency summaries;
- downstream artifact and downstream certified-health hashes;
- stable `certified_health_hash`;
- current schema and audit-policy hashes;
- normalized check evidence;
- certifier interface identity and version;
- `record_hash` and HMAC-SHA-256 authentication.

Stamping is node-local and commit-backed. Immediately before replacing one
node's record, the certifier verifies that the node's own authored blueprint,
bound file, and other local inputs exactly match the captured Git commit and
that HEAD has not changed. Already authenticated and current child records are
reused without rechecking those children's worktree state. A dirty node still
receives complete semantic audit results, but no stamp; the result states that
stamping requires committed local inputs.

Only stable check evidence (`id`, `version`, `passed`, and normalized findings)
participates in health. Volatile command output and timing do not churn stamps.
Generated records, key material, and pools are ignored local state and are
written with no-follow, atomic create-or-replace operations.

A parent dependency copies the expected child `certified_health_hash` computed
from live state and admitted check evidence. Drift authenticates a record
before validating its health schema or using any of its fields. Wrong subject,
type, certifier, dependency summaries, or failed/malformed checks make the node
unhealthy. Unauthenticated record content never influences a parent's expected
hash.

Canonical JSON uses UTF-8, sorted keys, compact separators, and no floating
point values. Hashes are SHA-256. Authentication uses HMAC-SHA-256 over the raw
record-hash bytes with domain `famulus-health-record-v1\0`. The 32-byte local key
lives at `skills/skill-audit/.health-authentication-key`, is ignored by Git, and
is created with POSIX mode `0600`. This protects against casual manual record
editing; stronger external or public-key trust can be added later.
Every health record depends on `skill-audit` as its certifier through the
explicit certifier identity and the policy hash. That policy hash includes the
certifier implementation and shared authentication, graph, schema-template,
health, and pooled-review machinery; it is a certification dependency, not a
graph edge that would create a cycle for `skill-audit` itself.
Certification refuses pre-existing symlinks at generated health or pooled-review
paths and opens outputs with no-follow semantics where the host supports them.

Changing a node's bound file, certified contract, schema policy, checks, or any
reachable child changes its expected certified health hash and propagates
upward. Raw blueprint formatting and schema-excluded metadata remain visible
through `blueprint_file_hash` without necessarily making the semantic
certification stale.

## Pooled review

After certification, tooling generates:

```text
.pooled-blueprint-review.yaml
.pooled-blueprint-review.health.json
```

The YAML expands root, node declarations, edges, and bounded health summaries
for human review. Its health depends on the canonical root's verified health:

- unhealthy root means unhealthy pool;
- healthy root plus changed or missing pool means unhealthy pool only;
- pool content or health never affects root health.

The checker validates the pool schema and requires byte-for-byte equality with
the canonical renderer over the exact authenticated records admitted by root
health. A valid HMAC alone cannot make arbitrary or noncanonical YAML healthy.

Deleting a pool must not change graph reconstruction or canonical drift status.

Health state is intentionally local in this phase. A portable append-only
certification ledger or externally held signatures remain deferred; neither is
required to reconstruct or validate the canonical blueprint graph.

## Migration

Legacy monolithic roots continue to validate and expand into virtual interface
nodes in memory. Migration moves one interface at a time into file-backed
sidecars without changing its canonical public ID or version unless the
caller-visible contract changes. Generated pools are never used as migration
input.
