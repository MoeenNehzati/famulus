# Development Mode: Live Officina Python

## Goal

After one development-mode installation, commands launched through `dispatcher`
in the normal managed environment must import Officina from the selected
worktree. Ordinary edits under
`src/officina` must therefore take effect without another installation.
Standard/plugin installations must remain immutable wheel snapshots.

This fixes the observed mixed state: development mode exposed live skills and
blueprints, while the managed runtime still executed an older copied wheel. A
live blueprint could consequently compile `release --force` even though the
runtime parser rejected `--force`.

## Minimal solution

1. In `skills/install-assistant-tools/_rtx/_phase_entry.py`, pass
   `editable=dev_mode` through `_build_managed_runtime_candidate()` to
   `managed_runtime.build_candidate_release()`.

2. In `src/officina/install/managed_runtime.py`, add an `editable: bool = False`
   keyword to `build_candidate_release()` and `_run_dependency_install()`.
   For the explicit-repository branch:

   - keep the existing wheel build, digest, and candidate metadata so the
     release artifact contract and build validation do not need redesign;
   - when `editable` is false, install that wheel exactly as today;
   - when `editable` is true, instead run the same dependency-install path with
     `--no-deps --no-build-isolation --editable <repo_root>`; editable mode
     accepts exactly one install target;
   - reject `editable=True` when no explicit `repo_root` was supplied, before
     creating a release directory;
   - record `source_mode` as `"editable"` or `"wheel"` in `artifact.json`, so
     the retained wheel is not mistaken for the active source; editable mode
     also records the absolute `editable_source_root`. The existing
     `source_revision` remains the revision at build/install time.

3. Keep the existing isolated candidate validation and activation sequence.
   In editable mode, additionally verify that the imported `officina` package
   resolves beneath `<repo_root>/src/officina`. Both probes must pass before
   `current.json` changes.

4. Update the governed contract, not only prose:

   - add the optional `editable` argument and its explicit-`repo_root`
     precondition to
     `src/officina/install/blueprints/managed-runtime.yaml`;
   - propagate the normal source/interface version changes to its phase-entry
     and scaffold consumers, then regenerate the owning blueprints;
   - update the affected docstrings plus only the currently contradictory
     user-facing text in `skills/install-assistant-tools/SKILL.md`,
     `docs/officina/installation.md`, and
     `docs/dependency-and-bootstrap-audit.md`.

   These surfaces must say that development mode uses the selected worktree as
   live Python source while standard/plugin mode installs the built wheel.

## Tests

- One parameterized phase-entry test: development mode forwards
  `editable=True`; plugin mode forwards `editable=False`.
- Managed-runtime unit tests: extend the existing wheel-command assertion and
  add one editable-command assertion for
  `--no-deps --no-build-isolation --editable <repo_root>`.
- Behavioral test: build an editable candidate from a temporary checkout,
  change an import-observable source value after activation, and verify the
  unchanged managed interpreter observes the new value and imports from the
  recorded source root.
- Run the focused phase-entry and managed-runtime test files.

## Acceptance criteria

- One development-mode reinstall is required to create the editable runtime.
- After that reinstall, an Officina Python edit is visible to a new
  `dispatcher` process without reinstalling, provided the caller has not
  deliberately overridden Python imports through ambient variables.
- Standard/plugin mode still imports the installed wheel and does not observe
  later worktree edits.
- Candidate failure still leaves the prior runtime pointer active.

## Exclusions

Do not change the dispatcher launcher, resolver, runtime pointer format,
development skill symlinks, Rutter, VoyageDispenser, or math-dependency-graph.
Do not add `PYTHONPATH`, source copying, file watching, automatic reinstall, or
a second development-runtime mechanism. Dependency or interpreter changes may
still require reinstalling; ordinary Python source edits may not.

Hardening launchers against an explicitly supplied ambient `PYTHONPATH` is a
separate policy change and is not part of this stale-runtime fix.
