# GitHub Actions Green Repair Design

## Goal

Make the `master` GitHub Actions workflows consistently green on Ubuntu,
macOS, and Windows while preserving meaningful portability, installer, and
native scheduler coverage.

## Observed Failure Boundary

The current and previous `master` revisions both fail, so the merge commit is
not the common cause. The failures divide into five independent boundaries:

- Documentation deployment cannot configure a Pages site because the
  repository is not enabled for Actions-based Pages deployment.
- The Python workflow installs an incomplete, floating dependency set: `lark`
  is absent and newer `pytest`/`jsonschema` releases differ from the locally
  verified environment.
- Windows converts `file:///D:/...` schema references through POSIX-shaped URI
  paths and consequently rejects in-root files as escaping the schema root.
- Several tests encode Linux-specific filesystem, launcher, cron, or path
  length assumptions while running on macOS.
- Headless Ubuntu has no native keyring for installer signing-material tests,
  and the Windows native scheduler smoke reports task success without proving
  that a new scheduled run produced its marker.

## Chosen Approach

Use a deterministic CI dependency contract, repair real cross-platform path
handling, correct tests whose asserted contract is platform-independent, and
retain native integration tests with stronger isolated test setup and run
evidence.

Two narrower alternatives are rejected:

- Pin-only repair would make the dependency failures disappear but leave the
  Windows URI bug and incorrect macOS assumptions in place.
- Migrating all schema resolution immediately to jsonschema's replacement
  registry API would be broader than the failing-CI objective. The existing
  compatibility resolver will instead be version-bounded until that migration
  is designed separately.

## Implementation Slices

### 1. Deterministic workflow and Pages setup

Add one CI requirements file containing every test dependency, including
`lark`, at versions verified by the repository test suite. Make the Python
workflow install that file. Enable GitHub Pages with the Actions build source;
do not weaken the Pages workflow or make documentation failures non-fatal.

### 2. Portable contracts

Add a failing Windows-file-URI regression test, then decode local `file:` URIs
with platform-correct semantics before applying schema-root containment.
Correct macOS tests to derive the managed runtime path from `FamulusPaths`, run
the cron orchestration assertion under an explicit Linux platform, restrict
arbitrary-byte filename coverage to platforms that support it, and assert the
documented 261-character Task Scheduler limit rather than an incidental
200-character threshold.

### 3. Native integration isolation

Give Ubuntu installer subprocesses an isolated test-only credential backend;
production secret-store selection and rejection rules remain unchanged. Make
the Windows scheduler smoke distinguish the preflight run from the scheduled
run by observing a fresh run record or marker and include scheduler/bootstrap
evidence on timeout. Do not skip or downgrade either integration check merely
to obtain green status.

## Testing and Delivery

For each product-code change, add or adjust the smallest regression test first
and observe the expected failure before implementation. Run focused slices,
then the repository precommit and full suites. Stage only files from this
repair, leaving `docs/reports/2026-08-11-skill-description-invocation-audit.md`
untouched. Commit, push, and monitor the exact pushed SHA until Documentation
Pages and every Python matrix job are green; if a native hosted-runner failure
remains, use its new diagnostics for a further focused repair.

## Non-goals

- Do not reduce the OS matrix or increase pytest worker count during repair.
- Do not suppress native keyring or scheduler failures.
- Do not migrate the complete configured-schema subsystem.
- Do not modify the unrelated untracked report.
