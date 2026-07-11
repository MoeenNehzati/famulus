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

## Drift Behavior

`skill-drift` exports a status interface:

```bash
dispatcher --caller-skill skill-drift skill-drift.machine.drift-status status [target ...] [--json]
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

- schema version;
- skill name;
- timestamp;
- writer identifier;
- current git commit when available;
- mechanical and semantic check evidence;
- current skill, policy, and interface hashes from `skill-drift`.

The semantic checks currently include:

- declared direct roots exist;
- machine runtime entrypoints exist;
- hand-authored `SKILL.md` does not contain direct execution logic outside
  declared interfaces;
- some implicit directory references are represented by declared roots.

These checks are intentionally only a first pass. They do not yet prove full
semantic exactness.

The intended record trust model is stricter than the current reader. In the end,
`audit-current` should mean "this target still matches a record written by the
current certifier under the current audit policy", not merely "the hash values in
some JSON file happen to match".

## Drift Inputs

For each installed skill, the checker reads:

- the local audit record: `.last_audit.json`;
- the skill blueprint, if present;
- files discovered through the skill and interface dependency explorer;
- shared policy files that define the skill audit rules.

The current policy hash is a first pass. It should be broadened so changes to
the audit standard stale old records. The intended policy surface includes at
least:

- `skill-audit` implementation, blueprint, skill instructions, and tests;
- `skill-drift` implementation, blueprint, skill instructions, references, and
  tests;
- shared skill guidelines;
- blueprint guide, template, schema, and generated runtime dependency metadata;
- validators and hooks that enforce skill/blueprint/audit semantics;
- test runner configuration used by the mechanical gate;
- reference docs and plans that define audit semantics.

For blueprint-backed skills, interface hashes include:

- binding files such as `SKILL.md`;
- declared `directly_reads`, `directly_executes`, and `directly_writes` roots;
- Markdown references that resolve to existing files;
- Python runtime dependencies loaded by route-smoke tracing;
- declared dispatch dependencies discovered by the Python interface resolver;
- machine-interface hashes declared in `uses_interfaces`, recursively.

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

The current writer is useful, but still incomplete as a certifier:

- It does not yet prove that every blueprint declaration is used by actual
  behavior, so excess declarations can pass.
- It does not yet prove that every behavior-relevant file root, state path,
  runtime dependency, permission, command surface, or interface call is declared.
- Its implicit-reference scan is regex-based and narrow. It can miss implicit
  dependencies and can conservatively flag prose that needs human review.
- It does not currently validate `uses_interfaces` against all code-level
  `DispatchCall` declarations.
- It runs a global mechanical gate, but that gate depends on the explicit test
  list in `scripts/run-python-tests.py`; skill tests omitted from that list are
  not run.
- It certifies the current filesystem state, not a clean git tree. The git
  commit recorded in `.last_audit.json` is evidence, not the sole source of
  truth.
- Its write target is dynamic, so the blueprint uses a broad write declaration
  over the skills tree. A future blueprint syntax for dynamic target writes
  would express this more exactly.

## Current Skill-Drift Gaps

The current reader/checker is useful, but still incomplete as a stale detector:

- It currently accepts matching hash records without requiring the expected
  `skill-audit` writer or check evidence.
- Interface hashes do not yet include all structured blueprint metadata. Changes
  to patterns, access control, runtime dependency declarations, runtime argument
  prefixes, or descriptions may not stale the record unless they also alter a
  hashed file or dependency entry.
- `uses_interfaces` currently resolves targets under the repository root used
  for hashing. Exact installed skill roots outside the normal repo layout need
  continued testing, especially if a target depends on another installed copy.
- Markdown reference discovery is pattern-based, not a full parser. Missing
  references are ignored by the explorer.
- Python dependency tracing depends on `route_smoke` importing behavior-relevant
  lazy modules without side effects.
- Declared dispatch menus may over-include dependencies when a class-level menu
  is shared by multiple machine interfaces.
- Status mode intentionally reports non-blueprint external/plugin skills as
  stale, while hash mode fails for explicit non-blueprint targets. That split is
  useful but should remain documented for callers.

## Current Design Findings

The current implementation is a first-pass audit/drift system. The core
architecture is sound, but these gaps remain before `.last_audit.json` should be
treated as a strong certification artifact:

- **Record trust:** `audit-current` should mean "current under the current
  certifier", not merely "hashes match". Drift should require the expected
  `skill-audit` writer and a valid checks payload, likely with a schema bump.
- **Policy hash breadth:** the policy hash should cover the whole audit
  standard. That includes `skill-audit`, `skill-drift`, shared skill
  guidelines, blueprint guide/template/schema, generated dependency metadata,
  relevant validators, tests, hooks, reference docs, and any other files that
  define audit semantics.
- **Blueprint metadata hashing:** interface hashes should include canonical
  structured blueprint metadata such as patterns, access control, runtime
  binding, runtime dependencies, direct roots, and `uses_interfaces`, not just
  files reached from those declarations.
- **Interface-use validation:** machine-interface `DispatchCall` declarations
  should either be mirrored in `uses_interfaces` and validated, or the hash
  layer should derive equivalent target-interface hashes from dispatch tracing.
- **Semantic exactness:** `skill-audit` currently checks only a deterministic
  subset of blueprint exactness. It should eventually prove no missing or excess
  file roots, interface calls, runtime dependencies, permissions, state paths,
  and execution surfaces.
- **Test coverage surface:** the repository's Python test runner uses an
  explicit directory list. Skill tests are not automatically discovered unless
  their directory appears in that list, so `skills/skill-drift/tests` should be
  added to the mechanical gate.

## Recommended Fix Order

1. Strengthen audit-record trust: bump the record schema and require the current
   `skill-audit` writer plus valid check evidence before reporting
   `audit-current`.
2. Broaden the policy hash to include the full audit standard, especially
   `skill-audit`, shared skill guidelines, blueprint guide/template/schema,
   generated metadata, validators, hooks, tests, and audit reference docs.
3. Add canonical structured blueprint metadata entries to interface hashes.
4. Validate `uses_interfaces` against machine-interface `DispatchCall`
   declarations, or derive equivalent used-interface hash entries from dispatch
   tracing.
5. Add `skills/skill-drift/tests` to the mechanical test suite used by
   `skill-audit`.
6. Expand `skill-audit` semantic exactness checks from first-pass heuristics to
   explicit missing/excess checks for roots, permissions, runtime dependencies,
   state paths, and interface calls.
