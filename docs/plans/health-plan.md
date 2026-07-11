# Skill Drift And Audit Plan

Status: current as of 2026-07-11.

This plan supersedes the older `skill-health` design. The current naming is:

- skill: `skill-drift`
- audit record: `.last_audit.json`
- positive status: `audit-current`
- negative status: `audit-stale`

The current implementation is a first-pass drift checker. It raises mechanical
flags when installed skill files no longer match a local audit record. It does
not diagnose whether the changes are good or bad, and it does not certify
skills.

## Current Implementation

`skill-drift` exposes:

```bash
dispatcher --caller-skill skill-drift skill-drift.machine.drift-status status [target ...] [--json]
dispatcher --caller-skill skill-drift skill-drift.machine.compute-hashes compute-hashes [target ...] [--json]
```

Behavior:

- targets may be installed skill names or exact skill root paths;
- with skill names, report those installed skills wherever they are found;
- with exact paths, report only the skill rooted at that path;
- with no skill names, report every discovered installed skill;
- default output is a Markdown table;
- default output is also saved to
  `skills/skill-drift/_build/<YYYY-MM-DD_HH-MM-SS>.md`;
- `_build/` is gitignored;
- `--json` emits machine-readable output on stdout and skips Markdown report
  writing.

`compute-hashes` is for certifier/writer skills. It computes the current
`skill`, `policy`, and `interfaces` hashes without reading `.last_audit.json`
and without writing a Markdown report. It fails if a target skill lacks
`blueprint.yaml`. With no targets, it returns every observed blueprint-backed
skill. With explicit targets, names are searched through the observed installed
skill roots and path-like targets are resolved as exact skill roots.

The checker reports one row per discovered skill with:

- source;
- skill name;
- derived audit status;
- audit record path;
- concerns.

The current concern classes include:

- `missing-record`;
- `corrupt-record`;
- `unsupported-schema`;
- `skill-mismatch`;
- `missing-hash`;
- `changed-hash`;
- `extra-recorded-hash`;
- `hash-unavailable`.

External/plugin skills that contain `SKILL.md` but do not have this repository's
`blueprint.yaml` convention are reported as `audit-stale` with
`hash-unavailable`; they do not abort the whole run.

## Installed Skill Discovery

`skill-drift` no longer assumes the source checkout's `skills/` directory is the
runtime universe. It discovers installed skill roots through host-specific source
adapters under:

```text
skills/skill-drift/_rtx/_skill_sources/
```

The generic checker imports only the neutral aggregate:

```python
observed_skill_sources()
```

This follows the repository's platform-neutrality rule: host-specific path logic
lives in host-named modules, while generic runtime files remain host-neutral.

## Hash Inputs

For Famulus-style skills with `blueprint.yaml`, the checker computes:

- a skill hash from all declared LLM and machine interface dependencies;
- interface hashes for each declared LLM and machine interface;
- a policy hash over the files that define the audit standard.

The dependency explorer recursively includes:

- declared `directly_reads`, `directly_executes`, and `directly_writes`;
- declared `uses_interfaces`, whose target machine-interface hashes are included
  recursively in the declaring interface hash;
- LLM binding files such as `SKILL.md`;
- Markdown address-like references that resolve to existing files;
- Python runtime imports loaded by `route_smoke`;
- relevant shared `officina` imports;
- package `__init__.py` files;
- declared `DispatchCall` targets, including nested dispatch chains;
- blueprint metadata and generated repo-level manifests such as
  `references/blueprint/runtime_dependencies.json`.

See `skills/skill-drift/references/dependency-explorer.md` for the detailed
recursive matching rules and known limitations.

## Audit Record Format

The current reader expects:

```json
{
  "schema_version": 1,
  "skill": "skill-name",
  "recorded_at": "2026-07-11T16:10:00-04:00",
  "writer": "skill-doctor@first-pass",
  "hashes": {
    "skill": "sha256:...",
    "policy": "sha256:...",
    "interfaces": {
      "llm.default": "sha256:...",
      "machine.some-interface": "sha256:..."
    }
  }
}
```

`.last_audit.json` is local state and must stay gitignored:

```gitignore
skills/*/.last_audit.json
```

Reason: audit records describe machine-local checked state. They are not source
artifacts.

## Current Boundaries

`skill-drift` is not a certifier. It only compares recorded hashes with current
hashes and reports drift concerns.

It writes only rendered Markdown reports under `_build/`. It never writes or
refreshes `.last_audit.json`.

The audit record writer should be a separate skill, probably `skill-doctor`.
That future skill should own semantic review, test execution, validator
execution, and writing a fresh `.last_audit.json` only after the target skill has
actually been checked.

## Skill Audit Design

The certifier/writer skill is called `skill-audit`.

`skill-audit` exposes:

```bash
dispatcher --caller-skill skill-audit skill-audit.machine.certify certify [target ...] [--json]
```

Targets follow the same convention as `skill-drift`: omit targets for every
observed blueprint-backed skill, pass a plain skill name to search installed
skill roots, or pass an exact skill root path to certify that one installed
copy.

`skill-audit` has two gates:

1. Mechanical checks:
   - blueprint sync check;
   - validators;
   - tests.
2. Semantic blueprint exactness:
   - every blueprint entry is correct;
   - no behavior-relevant file, command, dependency, permission, state path, or
     interface call is missing from the blueprint;
   - no declared file, command, dependency, permission, state path, or interface
     call is excess.
   - `SKILL.md` contains only user interaction, decision flow, and interface
     orchestration; executable behavior is encapsulated behind declared
     interfaces.

Implicit references count. If skill instructions, docs, docstrings, runtime
code, or tests describe behavior without a direct path, `skill-audit` must still
trace the implied dependency and require the blueprint to declare it. For
example, wording such as "look under this directory for executables" means the
skill depends on executables under that directory; the blueprint should declare
that directory or the relevant executable set. Likewise, prose that names a
script family, helper module, generated artifact, config file, state directory,
or external command surface should be treated as a candidate dependency even
when the exact path is not written inline.

The semantic rule is exactness, not minimal path syntax. A direct file path is
only one way a dependency can be expressed. If the skill's behavior would change
when an implicitly referenced file or executable changes, then omitting it from
the blueprint is a miss; declaring files or permissions that the skill does not
actually use is excess.

`SKILL.md` is not an execution surface. It should not tell an assistant to run
commands, scripts, runtime files, or implementation paths directly. It may route
work through declared interfaces. Generated blueprint interface documentation is
exempt from this prose check because it is derived from the blueprint itself.

## Next Steps

1. Expand `skill-audit`'s deterministic semantic exactness checks beyond the
   current first pass.
2. Decide whether non-Famulus external/plugin skills should be excluded from the
   default all-skill report or kept with `hash-unavailable` concerns.
3. Tighten the known dependency-explorer limitations listed in
   `references/dependency-explorer.md`.
4. Add a report option if callers need the Markdown path without printing the
   full table to stdout.
5. Add a migration command only when the writer/certifier exists.
