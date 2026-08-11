# Recurring tasks: production parity

**Date:** 2026-08-11
**Status:** Approved design, not yet implemented
**Revision:** 2 — rewritten after two adversarial audits invalidated most of revision 1.

## Problem

`recurring-tasks` has been repaired on five consecutive days (Aug 5, 8, 9, 10, 11).
The individual fixes were correct. They kept failing to hold because they treated
symptoms of one structural fault:

> Every recurring failure has been in a code path that only production takes.
> The test suite exercises a different mode of the same code.

Instances:

- `_run_record.py:176` calls `sys.platform`; the file never imports `sys`
  (imports at `:27-34`). Reachable only for `job_name == "email-triage"` with
  `EMAIL_TRIAGE_STATE_DIR` unset — exactly production. `tests/test_job_executor.py:310`
  covers email-triage with the variable *set*; `:328` covers the unset case for a
  *different* job. The cell production occupies is the only untested one.
  Consequence: every email-triage run since Aug 9 has done its work and then died
  before writing its outcome record.
- `ce4bf1b` "stop the health check depending on who runs it" — 12 false alarms
  across 4 days, from the checker re-deriving expectations from ambient `PATH`.
- `dispatcher … scripts-healthcheck` fails with `ImportError: gateway mutated
  sys.path`, from an unguarded insert at `_healthcheck_probe.py:16-17`.

## What revision 1 got wrong

Recorded because the errors are instructive, and because two of them were
mine rather than the audits':

1. **The route-smoke conformance test was green on the bug it was written to
   catch.** Revision 1 mandated `sys.executable -P -m officina.dispatcher.cli`.
   `-P` does not strip `PYTHONPATH` (that is `-E`), the suite requires
   `PYTHONPATH=src` because officina is not pip-installed, and the insert at
   `_healthcheck_probe.py:16` is conditional on `SRC_DIR not in sys.path` — so
   the mutation never fires and the test passes. Verified: that exact command
   prints `route-smoke ok` while the installed shim reproduces the ImportError.
2. **The dispatcher cutover was premised on a falsehood.** The pin-target
   decision was framed around interface-id instability. Units reference no
   interface id at all: `_linux_backend.py:228` renders a file path, and
   `_linux_registration_check.py:42-43` states that `command:` is deliberately
   not checked because the executor reads it live from `jobs.yaml`, so it
   *cannot* go stale. The Aug 9 v6 rename could not have touched the units.
3. **`pyflakes` catches the flagship bug in 0.3 seconds** and no gate in
   revision 1 caught it at all. No linter runs anywhere — not in
   `.githooks/pre-commit`, not in CI.
4. **officina ships as a pinned release snapshot.** `configuration.schema.json`
   is loaded from `resources.files("officina.common")` (`configured_schema.py:596-598`),
   i.e. from whichever officina is imported — the frozen release for the
   dispatcher. `recurringJob` is `additionalProperties: false` with six keys.
   Adding any `jobs.yaml` field makes `load_jobs` raise for every consumer until
   a release is cut. The release already differs from the working tree.

Revision 1 proposed nine steps, four gates, and edits across three skills and
three platform backends. It would have caught one of the six observed bugs.

## Principle

Test the path production takes, by taking it. Where a fact was verified by hand
during design, convert the verification into a test rather than a sentence.

## Plan

Seven items, ordered. Each leaves the system working.

**1. `import sys` in `_run_record.py`, plus the missing test cell** —
email-triage with `EMAIL_TRIAGE_STATE_DIR` unset. One line, one test. Unblocks
triage immediately; everything else can land slowly.

**2. pyflakes F821 as a repo check.** One validator, one line in
`requirements-ci.txt`, 3 existing findings to clear. Cheapest item here and it
catches the highest-impact observed bug.

**3. Delete the unguarded inserts, land the AST rule, one commit.**
Remove `_healthcheck_probe.py:16-17` and `_job_executor.py:21-26` — verified
unnecessary: the release interpreter has `officina` on site-packages, and
script-dir auto-add covers sibling imports. Add a rule to
`validators/skill/boundaries.py` flagging module-scope `sys.path` mutation not
lexically guarded on `__package__`, in dispatcher-reachable gateway files.
Repo-wide violations after this commit: zero.

**Scope limit, load-bearing:** this removes `sys.path` inserts *only*. The
`if __package__: … else: …` dual import in `_job_executor.py:40-49` **stays** —
systemd invokes it as a script (`ExecStart=… launch.py …/_job_executor.py …`),
where `__package__ == ""` and the `else` branch is what runs. Converting it to
unconditional relative imports would break all three jobs.

**4. Production-invocation tests.** The item revision 1's own thesis called for
and none of its gates provided. Spawn `launch.py _job_executor.py --jobs-file
<tmp> --job <fake>` as a subprocess under cron/systemd conditions — `cwd=/`, no
`XDG_RUNTIME_DIR`, no `DBUS_SESSION_BUS_ADDRESS`, minimal `PATH` — and assert a
run record is written. Same shape for `_healthcheck_probe.py` invoked as the
cron line invokes it. These two tests catch the `sys` NameError, the entire
`ce4bf1b` ambient-environment class, and any future preamble breakage.

**5. Sync calls `install_healthcheck_cron`**, so a stale cron line self-repairs.
Wrap `subprocess.run(["crontab", "-l"])` (`_setup_runner.py:57-64`) for
`FileNotFoundError` first — unwrapped, a cron-less Linux host would crash every
sync and take unit syncing with it. No backup ceremony: `:182` short-circuits
without writing when the rendered line is identical, so write frequency does not
actually rise.

**6. Reader-side state-dir fix.** Delete `_JOB_STATE_DIR_ENV_OVERRIDE` from
`_run_record.py` and give `_resolve_job_state_dir` / `read_inner_status` an
injected `paths: FamulusPaths | None = None` argument. ~20 lines, one skill, no
writers touched, no on-disk migration. The production branch becomes exercisable
in tests without an env var, which is the actual root cause. Reader and writer
agree in production because the variable is unset there.

**7. Unattended, in two commits.**
(a) Add `--unattended` to both launcher generators (`_linux_launcher.py:76-110`
and `_windows_launcher.py:90-115` — separate code paths), reinstall on every
host, verify `invoke-skill --unattended <skill>` runs. (b) Only then have the
executor pass the flag. Order is not optional: the installed shim rejects a
second argument with `SystemExit(2)`, so passing the flag first hard-fails every
agent job.

Agent-driven jobs are identified by `command` starting with `invoke-skill` — not
by a new `jobs.yaml` field, which the closed schema forbids without a release.

Instead of a third `blocked` outcome: have `read_inner_status` return the status
payload's `reason` alongside `result`, and include it in the existing healthcheck
message. `daily-plan: inner status 'blocked' — no OAuth client installed` for
~5 lines, versus threading a third state through `evaluate_success_contract`,
`JobRunRecord`, `_job_control.py:162`, `_healthcheck_probe.py:220`, and every
`latest.json` already on disk.

## Cut, and why

- **Dispatcher cutover (export `execute-job`, three backends emit dispatcher
  commands).** Premised on interface-id churn that cannot affect units. Would
  *introduce* the id dependency they lack, change every job's child-process cwd
  to `_rtx` (`dispatcher/core.py:246`; units have no `WorkingDirectory=` today,
  so jobs run from `$HOME`), and emit a critical-urgency desktop notification
  every 4 hours until a sync ran — reproduced: `FAIL: email-triage: service unit
  stale`, rc=1, which the cron line turns into `notify-send --urgency=critical`.
  Alarm fatigue is the failure mode this design exists to kill.
- **Registration integrity beyond `_linux_registration_check.py`.** Catches zero
  observed bugs; its payoff was conditional on the cutover.
- **`blocked` as a third outcome.** Superseded by the reason string.
- **State-dir unification across skills.** Seven `default_state_dir`
  implementations in email-triage (revision 1 said four), plus daily-plan's
  hardcoded `STATE_DIR`, plus a new `FamulusPaths` field requiring a release,
  plus an on-disk migration with no rollback — to prevent one NameError.
- **Route-smoke conformance test.** Only worth writing if it runs under an
  interpreter with officina in site-packages *and* asserts `src` is absent from
  the child's `sys.path`. Otherwise it silently degrades to a no-op, which is
  worse than no test. Deferred, not forbidden.

## Out of scope

- **daily-plan's missing Google OAuth client and unreachable storage
  interface.** Real, and why it fails today, but a product decision. After item
  7 its failures read as `inner status 'blocked' — no OAuth client` rather than
  a generic contract failure.
- **email-triage's policy for deadline-less emails.** Ten actionable emails are
  parked because the list schema requires a deadline and the job correctly
  refused to invent one.
- **macOS/Windows independent CHECK.** Neither exists;
  `install_healthcheck_cron` is gated behind `sys.platform.startswith("linux")`
  (`_setup_runner.py:224`) while SKILL.md:108 asserts invariant 4
  unconditionally. Correcting the doc is in scope; implementing two schedulers
  for platforms this user does not run is not.

## Honest limits

- Items 1–4 would have caught 2 of the 6 observed failures (`_run_record`
  NameError, `ce4bf1b` ambient-environment). `bd8a649` (invalid OnCalendar),
  `cf19a5d` (freshness thresholds) and `6bcbfcd` (daily-plan false green) were
  each caught by an ordinary unit test landed with the fix. That is the correct
  and cheapest answer; this design does not try to generalize them.
- "Make the class unrepresentable" was an overclaim in revision 1. Items 2–4
  make the loader and ambient-environment classes fail at commit time. They do
  not make every production-only path impossible.
