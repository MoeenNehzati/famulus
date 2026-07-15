# Skill Audit And Drift

This document describes the first-pass audit/drift system for local skills.

The goal is split across two skills:

- `skill-audit` audits an exact selected closure and, when each node is
  commit-ready, writes authenticated node-local health records.
- `skill-drift` reports whether installed skills still match their last local
  audit record.

`skill-drift` is a mechanical flagger, not a certifier. For schema-version-2
skills it authenticates every reachable node health record, recomputes the live
artifact graph, and propagates unhealthy state to the canonical root. Legacy
monolithic skills retain the earlier flat-hash comparison during migration.
Targeted checks load only the selected root's reachable closure, so malformed
unrelated skills do not block it; malformed reachable dependencies do.

## Highest-Priority Design Rule

`skill-drift` must remain a blueprint-driven mechanical drift checker. It
trusts the blueprint that was accepted at certification time and hashes only the
artifact surfaces declared by that blueprint plus explicit audit-policy
modules. It must not infer semantic dependencies from prose, Markdown links,
inline-code paths, or LLM-readable references.

`skill-audit` is the certifier that does the thinking. Its job is to review the
skill, use LLM judgment where deterministic checks are insufficient, decide
whether the blueprint exactly represents the skill's instruction and runtime
surface, and require blueprint updates before writing a fresh audit record.

The invariant is:

```text
drift trusts the certified blueprint; audit certifies that the blueprint is trustworthy.
```

If skill text starts telling the LLM to look at a new file, the skill text hash
changes and `skill-drift` marks the record stale. `skill-audit` must then either
add that file to the blueprint-declared contract or reject certification.

## Drift Behavior

`skill-drift` exports a status interface:

```bash
dispatcher --caller-skill skill-drift skill-drift.machine.drift-status status [target ...] [--json] [--with-test-validate]
```

It also exports a hash-computation interface:

```bash
dispatcher --caller-skill skill-drift skill-drift.machine.compute-hashes compute-hashes [target ...] [--json]
```

Targets may be plain installed skill names or exact skill root paths. With skill
names, the checker reports matching installed copies wherever they are found in
installed skill roots. With exact paths, it reports only that skill root. With
no targets, `status` reports every discovered installed skill and
`compute-hashes` returns every observed blueprint-backed skill.

The default output is a Markdown table. Each non-JSON run writes the same table
to:

```text
skills/skill-drift/_build/<YYYY-MM-DD_HH-MM-SS>.md
```

`_build/` is gitignored. `--json` keeps machine-readable output on stdout and
does not write a Markdown report.

`compute-hashes` does not read `.last_audit.json` and does not write a Markdown
report. It returns the current `skill`, `policy`, and `interfaces` hashes for
blueprint-backed skills. Missing `blueprint.yaml` is a command failure for
`compute-hashes`, because certifier skills need hashes they can write into a new
audit record.

`status` is intentionally more tolerant than `compute-hashes`: external or
plugin skills that have `SKILL.md` but no local `blueprint.yaml` are reported as
`audit-stale` with a `hash-unavailable` concern rather than aborting the whole
status report.

The checker reports one row per discovered skill with the source, skill name,
derived audit status, audit record path, and concerns. Current concern classes
include:

- `missing-record`;
- `corrupt-record`;
- `unsupported-schema`;
- `skill-mismatch`;
- `missing-hash`;
- `changed-hash`;
- `extra-recorded-hash`;
- `hash-unavailable`.

Typed graph concerns additionally include missing or invalid authentication
keys, missing node records, authentication failures, changed artifacts,
unhealthy downstream nodes, and stale pooled review artifacts. Pooled concerns
do not change canonical root status.

The optional `--with-test-validate` flag adds a health signal by running repo
validators and target skill tests when those check surfaces are available. This
does not change the audit status. When requested, `overall_status` uses:

```text
needs-attention = audit-stale OR health-failed
```

Callers must keep audit drift and current health failures separate.

## Relationship To Skill Audit

The intended split is:

- `skill-audit`: decide whether a skill is fit to certify, then write the audit
  record;
- `skill-drift`: later detect whether anything relevant changed after that
  record was written.

`skill-audit`:

- runs the blueprint sync check;
- runs validators;
- runs the configured precommit Python test suite;
- performs deterministic semantic checks on the target skill;
- computes current hashes through `skill-drift`;
- for typed skills, writes authenticated health for every reachable node and a
  generated pooled review with its own health record;
- for legacy skills, writes the compatibility `.last_audit.json` record;
- verifies the written record by asking `skill-drift` for post-write status;
- rolls back the record if post-write verification fails.

The compatibility record written for a legacy skill contains:

- skill name;
- timestamp;
- audit policy hash;
- current git commit when available;
- mechanical and semantic check evidence;
- current skill and interface hashes from `skill-drift`;
- record digest computed over the canonical record contents, excluding the
  digest field itself.

Example shape:

```json
{
  "skill": "skill-name",
  "timestamp": "2026-07-11T16:10:00-04:00",
  "audit_policy_hash": "sha256:...",
  "checks": {
    "mechanical": [
      {"name": "validators", "passed": true},
      {"name": "tests", "passed": true}
    ],
    "semantic": {"passed": true, "findings": []}
  },
  "hashes": {
    "skill": "sha256:...",
    "interfaces": {
      "llm.default": "sha256:...",
      "machine.some-interface": "sha256:..."
    }
  },
  "record_digest": "sha256:..."
}
```

The semantic checks currently include:

- declared behavior sources exist;
- machine invocation entrypoints exist;
- hand-authored `SKILL.md` does not contain direct execution logic outside
  declared interfaces;
- some implicit directory references are represented by declared behavior
  sources.

These checks are intentionally only a first pass. They do not yet prove full
semantic exactness.

For a legacy skill, `audit-current` means the target still matches a
digest-protected record under the current audit policy. Editing a readable
check status or other trust-relevant field by hand makes the record digest
mismatch unless the digest is deliberately regenerated. Typed skills use the
authenticated recursive model below.

## Target Artifact Health Model

The schema family, graph loader, validators, dispatcher compatibility layer,
audit writer, and drift reader implement this model. Typed and legacy roots are
accepted concurrently; new authoring uses schema version 2.

### Recursive audit-state currentness

Audit state is evaluated recursively in DFS postorder. A node is `current` only
when its blueprint and binding match its recorded hashes, every source node is
current, and no source has a `state_unchanged_since` later than the node's. It is
`stale` when any of those conditions fails.

```text
CHECK(root):
    visiting := empty set
    current := empty map
    return IS_CURRENT(root)


IS_CURRENT(X):
    if X is in visiting:
        return error("dependency cycle")

    if current contains X:
        return current[X]

    add X to visiting
    all_sources_current := true

    for each source Y of X:
        if IS_CURRENT(Y) is false:
            all_sources_current := false

    current[X] :=
        blueprint_is_current(X)
        and binding_is_current(X)
        and all_sources_current
        and, for every source Y of X,
            Y.state_unchanged_since <= X.state_unchanged_since

    remove X from visiting
    return current[X]
```

`blueprint_is_current` and `binding_is_current` compare the exact current file
bytes with the hashes in X's audit record. Because source edges are part of the
blueprint, adding, removing, or changing an edge makes the blueprint stale.
`state_unchanged_since` changes only when a new audit record admits a different
blueprint, binding, or source state; merely checking an unchanged node does not
change it.

The `visiting` set is the active recursion path and exists only to detect
dependency cycles. It is not part of the currentness rule and may be omitted if
acyclicity has already been established. The `current` map memoizes completed
nodes so a shared source is evaluated once. This procedure reads existing audit
records; it does not itself approve a stale node or write a replacement record.

The audit system certifies the reachable artifact graph one node at a time,
not only a skill-level summary. A skill audit starts from the canonical skill
`blueprint.yaml`, follows declared interface and behavior-source dependencies, and
writes a separate health record for every reachable node:

- the skill summary;
- each LLM interface;
- each machine interface;
- each declared behavior-source node.

Canonical blueprints remain the source of graph structure. The skill root owns
skill facts and points to interface sidecars; each interface or behavior-source
sidecar owns its node-local facts and points to its direct neighbors. No node
repeats a neighbor's intrinsic information. Starting from the one skill root,
the canonical root plus reachable sidecars must be sufficient to reconstruct
the graph. Health records are auxiliary certification state and cannot add
nodes or edges.

This does not require migrating every skill before the system is useful.
Legacy monolithic blueprints expand to virtual interface nodes in memory, while
typed roots and sidecars provide full node-local contracts. Shared files under
`references/` can remain ordinary files until a typed consumer introduces an
explicit file-backed behavior-source node.
If several skills depend on the same shared behavior source, the audit graph
identifies that source by a canonical artifact ID and reuses the same
behavior-source health record instead of duplicating source certification inside
each consuming interface record.
For a repository-root locator under `skills/<owner>/`, binding paths resolve
against that owner. This gives the shared node one canonical ID, bound file,
blueprint sidecar, and health sidecar regardless of which consumer reaches it.

The intended ownership rule is:

```text
interfaces own interface health; behavior sources own behavior-source health;
skill records summarize the reachable certified graph.
```

Here "own" is informal audit-design language, not the formal blueprint
`owns_filesystem` field. It means that node-local information belongs with the
node it describes. A behavior source may carry or sit beside metadata about its
own content hash, source-level dependencies, source-level checks, and certified
health hash. That local information can simplify certification because the blueprint
does not have to inline all interactions between behavior sources. The blueprint
draws on the behavior-source record; it does not lose authority over which
behavior sources participate in the skill graph.

An interface health record contains its structured blueprint contract hash and
the artifact and certified-health hashes of direct interface and behavior-source
dependencies. A behavior-source health record contains the canonical source ID,
bound-file hash, contract hash, dependency hashes, audit-policy hash, schema
hash, check evidence, record hash, and HMAC. A skill summary
records the root interface and the reachable graph digest rather than
embedding every descendant hash as flat skill-owned state.

When a behavior source is healthy, an interface or skill summary may copy the
behavior source's content hash, dependency summary, and certified health hash rather
than re-deriving the entire source-local certification inline. This is a
memoization and readability convenience, not a weakening of the blueprint
contract.

This model keeps `skill-drift` mechanical. Drift recomputes node hashes,
checks each node against its health record, and propagates stale status upward
through declared graph edges. It does not decide whether a Markdown reference,
inline path, or prose instruction should have been a behavior source. That
decision remains part of `skill-audit` certification.

Health records are derived certification artifacts. They do not require
editing the referenced source file, because putting a source's own hash inside
itself creates self-referential hashing problems. They live beside the source
as hidden sidecars under the companion-file convention below.

The companion-file convention should be uniform across skills, interfaces, and
behavior sources. Abstractly, each auditable node has:

- an authored `X` file that declares the node-local contract or audit metadata;
- a generated `Y` file that records the node's last certified health.

For the skill node, these are the unsuffixed skill-level files:

```text
blueprint.yaml
.last_audit.json
```

For a node whose relevant content file is `z`, the hidden colocated files are:

```text
.z.blueprint.yaml
.z.health.json
```

This preserves the existing per-skill blueprint pattern while keeping node
metadata out of ordinary directory listings: the skill owns `blueprint.yaml`
and `.last_audit.json`; a behavior source or interface content file owns
`.<content>.blueprint.yaml` and `.<content>.health.json`. If multiple nodes bind
one file, each sidecar adds its local node name before the suffix. The hidden
`.z.blueprint.yaml` file must remain subordinate to the skill blueprint. It may
declare source-local dependencies, checks, or metadata that would be awkward to
inline at the skill level, but the skill `blueprint.yaml` must still identify
that `z` participates in the graph and that `.z.blueprint.yaml` is the local
metadata file to consult.

Generated aggregate descriptions should stay out of canonical blueprints.
After `skill-audit` writes or refreshes the node health records for a skill, it
generates a pooled blueprint for human review. The pooled blueprint
is an assembled view that starts from the skill `blueprint.yaml`, follows the
declared graph, reads downstream hidden blueprint and health sidecars
files, and presents the expanded interface descriptions, behavior-source
descriptions, dependency summaries, and health summaries in one place.

The pooled blueprint is not authoritative input. Validators and drift checks
must be able to ignore it and reconstruct the same graph from the canonical
skill blueprint plus declared node-local files. Its purpose is review:
after an audit, the user should be able to inspect one generated artifact and
see the effective graph and the certified downstream content that the skill is
drawing on.

The generated review files are `.pooled-blueprint-review.yaml` and
`.pooled-blueprint-review.health.json`. Pool health depends on verified root
health, but canonical root health never depends on either pooled file.
The pool must validate against `pooled-review.schema.json` and exactly equal the
canonical rendering of the graph and the same authenticated records admitted by
root health. Pool and health files are ignored local state.

Health records use SHA-256 hashes and HMAC-SHA-256 authentication with canonical
compact sorted-key UTF-8 JSON and no floating-point values. The stable
`certified_health_hash` excludes timestamps and authentication, while
`record_hash` covers the generated record payload. HMAC authenticates the raw
record-hash bytes under `famulus-health-record-v1\0`. The 32-byte local key is
stored at `skills/skill-audit/.health-authentication-key`, ignored by Git, and
created with POSIX mode `0600`.
Each record also names `skill-audit.machine.certify@1`. The policy hash covers
the skill-audit certifier and the shared authentication, atomic-file,
Git-provenance, graph, schema-template, health, and pooled-review code. This
makes `skill-audit` a certification-policy dependency of every record without
adding a recursive artifact-graph edge.

Drift authenticates a health record before schema validation or use of any
recorded check evidence. An authenticated record is still stale if it violates
`health.schema.json`, names the wrong subject/type/certifier, contains a failed
check, or disagrees with recomputed dependencies. Unauthenticated or malformed
record fields never contribute to expected parent hashes.

Certification is node-local and commit-backed. A node is stamp-worthy only if
its own authored inputs match the captured commit immediately before its atomic
record replacement and HEAD remains unchanged. Child nodes are processed first;
an already authenticated and current child stamp is reused without following
that child's worktree status again. Dirty nodes still receive semantic audit
results, but no stamp, with an explicit commit-required reason. Stable check
evidence excludes volatile stdout, stderr, timing, and invocation noise.

Generated records, pools, and key material are written with no-follow atomic
operations. Their local HMAC protects against casual hand editing. A portable
ledger, external key custody, and public-key signatures remain deferred.

## Drift Inputs

For each installed skill, the checker reads:

- the local audit record: `.last_audit.json`;
- the skill blueprint, if present;
- for typed roots, every reachable authored node sidecar, bound file, and
  generated node health record;
- for legacy roots, files discovered through the compatibility skill and
  interface dependency explorer;
- shared policy files that define the skill audit rules.

Typed drift also reads the existing local HMAC key. It never creates that key.
It verifies the canonical root independently of the generated pooled review;
pool freshness is a separate review-artifact concern.

The policy hash covers the normative audit standard and the audit/drift
implementation, not every executable check used by the current gate. Tests,
validators, and hooks are health gates: `skill-audit` runs them before writing
a record, and `skill-drift --with-test-validate` can report their current
failures, but their source files are not policy-hash inputs by default.

The intended policy surface includes:

- `skill-audit` implementation, blueprint, and skill instructions;
- `skill-drift` implementation, blueprint, skill instructions, and references;
- shared skill guidelines;
- blueprint guide, template, schema, and generated runtime dependency metadata;
- reference docs and plans that define audit semantics.

Tests, validators, hooks, and test-runner configuration can still change health
outcomes. They should be exercised directly, not treated as part of the
certified policy hash.

For legacy blueprint-backed skills, compatibility interface hashes include:

- a canonical JSON entry for the structured interface blueprint declaration;
- file-backed LLM binding files such as `SKILL.md`;
- declared `behavior_sources` on LLM interfaces;
- declared `invocation.behavior_sources` on machine interfaces;
- Python machine-interface invocation entrypoints and dependencies loaded by
  route-smoke tracing;
- declared dispatch dependencies discovered by the Python interface resolver;
- interface hashes declared in `uses_interfaces`, recursively.

For typed nodes, the same exclusion is declared field-by-field in the concrete
schema's `x-famulus.audit_hash` metadata. `direct_io` is not hash input, either
as live subject data or as declaration metadata. It describes operational data read or written during an invocation,
such as inboxes, calendars, user documents, stdout, remote files, and API
responses. `skill-drift` must not hash the live operational data named by
`direct_io`, and `direct_io` declaration edits should not by themselves stale an
audit record.

Legacy compatibility sidecars such as `depends_on_skills` and
`permissions.json` are not drift inputs. Dependency and suggested-permission
metadata are represented by `blueprint.yaml` and generated repo-level manifests
such as `references/blueprint/runtime_dependencies.json`.

`uses_interfaces` is the interface-level dependency declaration. There is no
skill-level `depends_on`; each interface says which version-pinned interfaces it
actually uses or orchestrates. This is especially important for LLM interfaces, where the
prompt surface can route work through machine interfaces without code-level
dispatch declarations.

For Python machine interfaces there are two related surfaces:

- `DispatchCall` declarations in code, which are executable route menus;
- `uses_interfaces` declarations in the blueprint, which are hash/audit
  dependencies.

Those surfaces should be validated against each other. Today the hash layer can
trace `DispatchCall` dependencies and can hash `uses_interfaces`, but the system
does not yet enforce that both declarations agree.

Missing audit records, corrupt records, unsupported schemas, skill mismatches,
hash changes, and unavailable hash inputs all produce `audit-stale`.

External skills that have `SKILL.md` but no `blueprint.yaml` are still reported.
They receive a `hash-unavailable` concern instead of aborting the whole run.

## Installed Skill Discovery

Installed skill roots are discovered through host-specific source adapters. The
generic checker consumes only a neutral installed-source aggregate.

Those adapters live under:

```text
skills/skill-drift/_rtx/_skill_sources/
```

The generic checker imports only:

```python
observed_skill_sources()
```

This mirrors the repository's platform-boundary convention: host-specific path
logic lives in host-named files, while shared files remain host-neutral.

## What This Skill Does Not Do

`skill-drift` does not write or refresh `.last_audit.json`. Writing audit records
belongs to `skill-audit`. The only file writes performed here are local Markdown
report artifacts under `_build/`.

`skill-drift` also does not diagnose whether a change is good or bad. It only
reports whether the current observed hash state matches the last accepted audit
record.

`skill-audit` should not be treated as a complete semantic certifier yet. Its
current deterministic checks are useful gates, but they do not replace a full
review of whether the blueprint exactly represents behavior.

## Current Skill-Audit Gaps

The current certifier is useful, but still incomplete:

- It does not yet prove that every blueprint declaration is used by actual
  behavior, so excess declarations can pass.
- It does not yet prove that every behavior-relevant file root, state path,
  runtime dependency, permission, command surface, or interface call is declared.
- Its implicit-reference scan is regex-based and narrow. It can miss implicit
  dependencies and can conservatively flag prose that needs human review.
- It does not currently validate `uses_interfaces` against all code-level
  `DispatchCall` declarations.
- It runs a global mechanical gate through `scripts/run-python-tests.py`. The
  precommit suite discovers all skill test directories by default and excludes
  only known heavy or special suites, currently `skills/install-assistant-tools/tests`.
- It requires each stamped node's own inputs to match the captured Git commit.
  This deliberately does not recheck already certified downstream worktrees
  when a parent reuses their authenticated current records.
- Its write target is dynamic across selected skill roots. The current
  `owns_filesystem` vocabulary does not express target-parameterized ownership;
  a future schema extension could represent that more exactly.

## Current Skill-Drift Gaps

The current reader/checker is useful, but still incomplete as a stale detector:

- Typed records use a local HMAC key, which protects against casual manual
  edits but not an actor who can read the key and rewrite both record and MAC.
  Stronger trust would require external key custody or public-key signatures.
- `uses_interfaces` currently resolves targets under the repository root used
  for hashing. Exact installed skill roots outside the normal repo layout need
  continued testing, especially if a target depends on another installed copy.
- Markdown/prose reference discovery is deliberately not part of `skill-drift`;
  `skill-audit` must detect instruction-visible file references and ensure the
  blueprint declares the relevant roots before certification.
- Python dependency tracing depends on `route_smoke` importing behavior-relevant
  lazy modules without side effects.
- Declared dispatch menus may over-include dependencies when a class-level menu
  is shared by multiple machine interfaces.
- Status mode intentionally reports non-blueprint external/plugin skills as
  stale, while hash mode fails for explicit non-blueprint targets. That split is
  useful but should remain documented for callers.
- Health state is local and ignored by Git; there is no portable certification
  ledger yet.

## Settled Design Choices

The current baseline has these design choices implemented:

- `skill-drift` is blueprint-first and does not parse prose, Markdown links,
  inline-code paths, or broad LLM-readable references for dependencies.
- The policy hash covers normative audit semantics and audit/drift
  implementation, while tests, validators, hooks, and test-runner configuration
  remain executed health gates.
- Interface hashes include canonical structured blueprint metadata plus declared
  file roots, traced Python dependencies, and recursive `uses_interfaces`
  targets, excluding `direct_io`.
- `direct_io` is operational IO metadata only. Neither its declaration nor the
  live operational data it names is content-hashed.

## Current Design Findings

The current implementation is a first-pass audit/drift system. The core
architecture is sound, but these gaps remain before `.last_audit.json` should be
treated as a strong certification artifact:

- **Record trust:** typed `audit-current` requires authenticated node records,
  current policy and schema hashes, matching local and recursive graph hashes,
  and passed normalized checks. Legacy records retain digest self-consistency
  until migrated.
- **Interface-use validation:** machine-interface `DispatchCall` declarations
  should be mirrored in `uses_interfaces` and validated. This belongs in the
  validator/audit layer, not the drift hash contract: `skill-drift` should trust
  the blueprint while a mechanical validator independently route-smokes Python
  machine interfaces, extracts direct `DispatchCall` targets, and compares them
  with direct `uses_interfaces` declarations.
- **Semantic exactness:** `skill-audit` currently checks only a deterministic
  subset of blueprint exactness. It should eventually prove no missing or excess
  behavior sources, interface calls, runtime dependencies, permissions, state
  paths, and execution surfaces.
- **Test coverage surface:** the repository's Python test runner now discovers
  skill test directories for the precommit gate. The exclusion list should stay
  narrow and deliberate so new skill tests are included automatically.

## Remaining Fix Order

1. Add a validator that checks direct machine-interface dependency agreement:
   route-smoke each Python machine interface, collect its direct `DispatchCall`
   targets, and require the same direct machine interfaces in blueprint
   `uses_interfaces`. Keep recursive dependency hashing in `skill-drift`; keep
   dependency correctness in validators/`skill-audit`.
2. Expand `skill-audit` semantic exactness checks from first-pass heuristics to
   explicit missing/excess checks for behavior sources, permissions, runtime
   dependencies, state paths, and interface calls.
3. Consider external key custody or public-key signatures if local HMAC is not
   enough protection against intentional edits by an actor with repository
   access.
