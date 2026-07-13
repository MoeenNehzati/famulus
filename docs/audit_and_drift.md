# Skill Audit And Drift

This document describes the first-pass audit/drift system for local skills.

The goal is split across two skills:

- `skill-audit` certifies a skill after checks pass and writes
  `.last_audit.json`.
- `skill-drift` reports whether installed skills still match their last local
  audit record.

`skill-drift` is a mechanical flagger, not a certifier: it compares recorded
hashes with freshly computed hashes and reports `audit-current` or
`audit-stale`.

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

`skill-audit` currently:

- runs the blueprint sync check;
- runs validators;
- runs the configured precommit Python test suite;
- performs deterministic semantic checks on the target skill;
- computes current hashes through `skill-drift`;
- writes `.last_audit.json`;
- verifies the written record by asking `skill-drift` for post-write status;
- rolls back the record if post-write verification fails.

The audit record written by `skill-audit` currently contains:

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

`audit-current` means the target still matches a digest-protected record under
the current audit policy. Editing a readable check status or other
trust-relevant field by hand makes the record digest mismatch unless the digest
is deliberately regenerated.

## Target Artifact Health Model

The audit system should eventually certify the whole reachable artifact graph,
not only a skill-level summary. A skill audit starts from the skill's default
LLM interface, follows declared interface and behavior-source dependencies, and
writes a separate health record for every reachable node:

- the skill summary;
- each LLM interface;
- each machine interface;
- each declared `behavior_sources` entry;
- shared audit-policy nodes.

The blueprint remains the source of graph structure. The information in
`blueprint.yaml` must be sufficient to reconstruct the skill's audit graph:
which interfaces exist, which interfaces depend on which other interfaces, and
which behavior sources each interface draws on. Per-node health records and
local sidecar metadata must not become a second graph-definition language.
They are auxiliary certification state for graph nodes already reachable from
the blueprint.

This does not require migrating shared files before the system is useful.
`skill-audit` and `skill-drift` can traverse the current blueprint declarations
and materialize health records for the artifact nodes they find. Shared files
under `references/` can therefore remain ordinary behavior sources at first.
If several skills depend on the same shared behavior source, the audit graph
should identify that source by a canonical artifact id and reuse the same
behavior-source health record instead of duplicating source certification inside
each consuming interface record.

The intended ownership rule is:

```text
interfaces own interface health; behavior sources own behavior-source health;
skill records summarize the reachable certified graph.
```

Here "own" is informal audit-design language, not the formal blueprint
`owns_filesystem` field. It means that node-local information belongs with the
node it describes. A behavior source may carry or sit beside metadata about its
own content hash, source-level dependencies, source-level checks, and record
digest. That local information can simplify certification because the blueprint
does not have to inline all interactions between behavior sources. The blueprint
draws on the behavior-source record; it does not lose authority over which
behavior sources participate in the skill graph.

An interface health record should contain the interface hash, its structured
blueprint declaration, the hashes and record digests of direct interface
dependencies, and the hashes and record digests of direct behavior-source
dependencies. A behavior-source health record should contain the canonical
source id, content hash, dependency hashes if any are declared or mechanically
known, audit-policy hash, check evidence, and record digest. A skill summary
should then record the root interface and the reachable graph digest rather than
embedding every descendant hash as flat skill-owned state.

When a behavior source is healthy, an interface or skill summary may copy the
behavior source's content hash, dependency summary, and record digest rather
than re-deriving the entire source-local certification inline. This is a
memoization and readability convenience, not a weakening of the blueprint
contract.

This model keeps `skill-drift` mechanical. Drift recomputes node hashes,
checks each node against its health record, and propagates stale status upward
through declared graph edges. It does not decide whether a Markdown reference,
inline path, or prose instruction should have been a behavior source. That
decision remains part of `skill-audit` certification.

Health records should be treated as derived certification artifacts. They must
not require editing the referenced source file, because putting a source's own
hash inside itself creates self-referential hashing problems. If colocated files
are later desirable, they should live beside the source as sidecars or in a
central audit-record directory keyed by canonical artifact id.

The companion-file convention should be uniform across skills, interfaces, and
behavior sources. Abstractly, each auditable node has:

- an authored `X` file that declares the node-local contract or audit metadata;
- a generated `Y` file that records the node's last certified health.

For the skill node, these are the unsuffixed skill-level files:

```text
blueprint.yaml
.last_audit.json
```

For a node whose relevant content file is `z`, the colocated files should be:

```text
z.blueprint.yaml
z.last_audit.json
```

This preserves the existing per-skill blueprint pattern while making the
recursive convention obvious: the skill owns `blueprint.yaml` and
`.last_audit.json`; a behavior source or interface content file owns
`<content>.blueprint.yaml` and `<content>.last_audit.json`. The suffixed
`z.blueprint.yaml` file must remain subordinate to the skill blueprint. It may
declare source-local dependencies, checks, or metadata that would be awkward to
inline at the skill level, but the skill `blueprint.yaml` must still identify
that `z` participates in the graph and that `z.blueprint.yaml` is the local
metadata file to consult.

Generated aggregate descriptions should stay out of canonical blueprints.
After `skill-audit` writes or refreshes the node health records for a skill, it
should also generate a pooled blueprint for human review. The pooled blueprint
is an assembled view that starts from the skill `blueprint.yaml`, follows the
declared graph, reads downstream `*.blueprint.yaml` and `*.last_audit.json`
files, and presents the expanded interface descriptions, behavior-source
descriptions, dependency summaries, and health summaries in one place.

The pooled blueprint is not authoritative input. Validators and drift checks
must be able to ignore it and reconstruct the same graph from the canonical
skill blueprint plus declared node-local files. Its purpose is review:
after an audit, the user should be able to inspect one generated artifact and
see the effective graph and the certified downstream content that the skill is
drawing on.

## Drift Inputs

For each installed skill, the checker reads:

- the local audit record: `.last_audit.json`;
- the skill blueprint, if present;
- files discovered through the skill and interface dependency explorer;
- shared policy files that define the skill audit rules.

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

For blueprint-backed skills, interface hashes include:

- a canonical JSON entry for the structured interface blueprint declaration;
- file-backed LLM binding files such as `SKILL.md`;
- declared `behavior_sources` on LLM interfaces;
- declared `invocation.behavior_sources` on machine interfaces;
- Python machine-interface invocation entrypoints and dependencies loaded by
  route-smoke tracing;
- declared dispatch dependencies discovered by the Python interface resolver;
- interface hashes declared in `uses_interfaces`, recursively.

`direct_io` is not hash input, either as live subject data or as declaration
metadata. It describes operational data read or written during an invocation,
such as inboxes, calendars, user documents, stdout, remote files, and API
responses. `skill-drift` must not hash the live operational data named by
`direct_io`, and `direct_io` declaration edits should not by themselves stale an
audit record.

Legacy compatibility sidecars such as `depends_on_skills` and
`permissions.json` are not drift inputs. Dependency and suggested-permission
metadata are represented by `blueprint.yaml` and generated repo-level manifests
such as `references/blueprint/runtime_dependencies.json`.

`uses_interfaces` is an interface-level dependency declaration. Skill-level
`depends_on` authorizes which dependency interfaces a skill may use;
`uses_interfaces` says which machine interfaces a particular interface actually
uses or orchestrates. This is especially important for LLM interfaces, where the
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
- It certifies the current filesystem state, not a clean git tree. The git
  commit recorded in `.last_audit.json` is evidence, not the sole source of
  truth.
- Its write target is dynamic, so the blueprint uses a broad write declaration
  over the skills tree. A future blueprint syntax for dynamic target writes
  would express this more exactly.

## Current Skill-Drift Gaps

The current reader/checker is useful, but still incomplete as a stale detector:

- It rejects records whose digest does not match their canonical contents, but
  this is an integrity check rather than a cryptographic trust boundary.
  Strong tamper resistance would require a future signature scheme.
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

- **Record trust:** `audit-current` now requires a matching record digest,
  current audit policy hash, current skill/interface hashes, and readable check
  evidence whose gates passed. Future hardening could add local signatures if
  digest self-consistency is not strong enough.
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
- **Per-node health records:** skill-level audit records are too coarse for the
  intended dependency graph. A skill audit should create or refresh health
  records for every reachable interface and behavior source, so shared behavior
  sources can be certified once and reused by multiple consuming interfaces.
- **Test coverage surface:** the repository's Python test runner now discovers
  skill test directories for the precommit gate. The exclusion list should stay
  narrow and deliberate so new skill tests are included automatically.

## Recommended Fix Order

1. Do a schema-design pass before implementation. Review the current
   `references/blueprint/schema.json`, `references/blueprint/guide.md`, and
   this document together, then define how skill blueprints, node-local
   `*.blueprint.yaml` files, node-local `*.last_audit.json` files, and generated
   pooled blueprints relate. If Superpowers is available, use its planning and
   skill-design workflows for this pass before touching code.
2. Extend the blueprint schema family, not just the skill schema. Keep
   `skills/<skill>/blueprint.yaml` as the canonical graph root, and add an
   explicit node-local artifact type for `z.blueprint.yaml` files so behavior
   sources and interface content files do not have to pretend to be skills.
3. Extend skill-blueprint `behavior_sources` and LLM binding declarations so
   they can point to subordinate node-local blueprint and health files while
   remaining sufficient to reconstruct the graph without reading generated
   pooled blueprints.
4. Define the health-record layout for skill summaries, interfaces, and
   behavior sources. The first version can use current blueprint declarations
   directly; do not require moving shared reference files before this works.
5. Teach `skill-audit` to traverse a skill's reachable interface and
   behavior-source graph and write per-node health records. A skill audit should
   certify every interface and behavior source reachable from the audited skill,
   then generate a pooled blueprint for user review.
6. Teach `skill-drift` to read those per-node health records, recompute node
   hashes, and propagate stale status upward through declared dependencies.
   Drift must ignore generated pooled blueprints as authoritative input.
7. Add a validator that checks direct machine-interface dependency agreement:
   route-smoke each Python machine interface, collect its direct `DispatchCall`
   targets, and require the same direct machine interfaces in blueprint
   `uses_interfaces`. Keep recursive dependency hashing in `skill-drift`; keep
   dependency correctness in validators/`skill-audit`.
8. Expand `skill-audit` semantic exactness checks from first-pass heuristics to
   explicit missing/excess checks for behavior sources, permissions, runtime
   dependencies, state paths, and interface calls.
9. Consider signed audit records if digest self-consistency is not enough
   protection against intentional local edits.
