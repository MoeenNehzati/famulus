# Skill Drift

`skill-drift` reports whether installed skills match their last local audit
record. It is a mechanical flagger, not a certifier: it compares recorded hashes
with freshly computed hashes and reports `audit-current` or `audit-stale`.

## Current Behavior

The exported status interface is:

```bash
dispatcher --caller-skill skill-drift skill-drift.machine.drift-status status [skill-name ...] [--json]
```

The exported hash-computation interface is:

```bash
dispatcher --caller-skill skill-drift skill-drift.machine.compute-hashes compute-hashes [skill-name ...] [--json]
```

With explicit skill names, the checker reports those skills wherever they are
found in installed skill roots. With no skill names, it reports every discovered
installed skill.

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

## What Gets Checked

For each installed skill, the checker reads:

- the local audit record: `.last_audit.json`;
- the skill blueprint, if present;
- files discovered through the skill and interface dependency explorer;
- shared policy files that define the skill audit rules.

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
belongs to a future writer/certifier skill. The only file writes performed here
are local Markdown report artifacts under `_build/`.
