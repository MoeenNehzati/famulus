# Recurring Reliability (v5 Rebase) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix feedback items 7, 10, 11, 13, 14, 15, 16, 17, 20, 22 from `docs/plans/osx_feedback_fix/README.md` — scheduler backend portability, truthful health reporting, and run-outcome contracts for `skills/recurring-tasks/` — superseding `docs/plans/osx_feedback_fix/04-recurring-reliability.md`. All ten items are still open; none were resolved by the recent notifier/test-backfill commits (`c3d40f7`, `a058cbb`).

**Architecture:** Extend `ScheduleContext` with the stable-resolver/config/state fields the installer-runtime rebase now provides (via `officina.install`/`FamulusPaths`, see `docs/superpowers/plans/2026-07-27-osx-installer-runtime-v5-rebase.md`), add a `JobRunRecord`/`evaluate_success_contract` layer that separates "the scheduler triggered the process" from "the job actually succeeded," and fix `_healthcheck_probe.py` so it can never report success while `problems > 0`. Rewrite `SKILL.md` to document all three real backends instead of only systemd.

**Tech Stack:** Python 3.11+, pytest, launchd/systemd/Windows Task Scheduler backends already in `_rtx/_schedule_backend/`.

**Path note:** Every file this subplan touches already lives under `skills/recurring-tasks/_rtx/` and `skills/recurring-tasks/_rtx/tests/` (confirmed current tree — the frozen 2026-07-24 plan cited some paths without the `_rtx/` prefix; that staleness is corrected in this plan, no other rebase is needed for file locations).

---

## Task 1: Backend-owned scheduler context (feedback items 7, 17) — depends on installer-runtime rebase Task 1/4

**Files:**
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_base_backend.py` (currently `ScheduleContext` at lines 29-35 has only `skill_dir, jobs_file, log_dir, unit_dir, live`)
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_osx_backend.py` (line 93 currently embeds `sys.executable` directly in `ProgramArguments` — release-specific interpreter, not a stable resolver)
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_linux_backend.py`, `_windows_backend.py` (equivalent interpreter-resolution fix)
- Modify: `skills/recurring-tasks/_rtx/jobs.yaml` (remove hardcoded `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` from both jobs — confirmed present today on both the `email-triage` and `daily-plan` entries)
- Test: `skills/recurring-tasks/_rtx/tests/test_schedule_backend.py`

- [ ] **Step 1: Write failing tests for the extended `ScheduleContext`**

```python
def test_schedule_context_carries_stable_resolver_path(tmp_path):
    from officina.install.runtime_pointer import RuntimePointer
    ctx = ScheduleContext(
        skill_dir=tmp_path, jobs_file=tmp_path / "jobs.yaml",
        log_dir=tmp_path / "logs", unit_dir=tmp_path / "units", live=False,
        runtime_resolver=tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py",
        config_root=tmp_path / "config", state_root=tmp_path / "state",
        assistant_default="codex",
    )
    assert ctx.runtime_resolver.name == "launch.py"


def test_osx_backend_program_arguments_use_stable_resolver_not_sys_executable(tmp_path):
    ctx = ScheduleContext(..., runtime_resolver=tmp_path / "resolvers" / "v1" / "launch.py")
    args = build_program_arguments(ctx, job)
    assert sys.executable not in args
    assert str(ctx.runtime_resolver) in args
```

(Match the real `ScheduleContext` dataclass fields already present and the actual function that builds launchd `ProgramArguments` — read `_base_backend.py` and `_osx_backend.py` first and extend rather than replace their existing signatures where reasonable.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_schedule_backend.py -v`
Expected: FAIL — new `ScheduleContext` fields don't exist yet.

- [ ] **Step 3: Extend `ScheduleContext` and wire the stable resolver into each backend**

Add `runtime_resolver: Path`, `config_root: Path`, `state_root: Path`, `assistant_default: str` fields to `ScheduleContext` in `_base_backend.py`. In `_osx_backend.py`, `_linux_backend.py`, `_windows_backend.py`, replace direct `sys.executable` embedding with `str(ctx.runtime_resolver)` (the same stable-resolver mechanism introduced in the installer-runtime rebase's Task 6 — reuse it, don't reinvent a second resolver).

- [ ] **Step 4: Remove hardcoded DBUS address from `jobs.yaml`**

```yaml
jobs:
- name: email-triage
  description: Triage new emails into todo and triage lists
  command: ASSISTANT_DEFAULT=codex invoke-skill email-triage
  schedule: 0 3 * * *
  enabled: true
- name: daily-plan
  description: Generate or show today's daily plan, including potential-actions triage
  command: invoke-skill daily-plan
  schedule: 0 7 * * *
  enabled: true
```

(Schedule change from `0 * * * *` to once-daily times addresses Task 4/item 16 below — see Task 4 for the full fix including the underlying platform-gate bug; this step only removes the DBUS hardcode. `DBUS_SESSION_BUS_ADDRESS` resolution becomes the Linux backend's job at process-launch time — read `_linux_backend.py` to confirm where session-bus discovery should move to, e.g. resolving `/run/user/$(id -u)/bus` at runtime instead of a hardcoded UID.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_schedule_backend.py skills/recurring-tasks/_rtx/tests/test_sync_units.py -v`
Expected: PASS

- [ ] **Step 6: Blueprint updates through `skill-maker`**

Update `skills/recurring-tasks/_rtx/blueprints/rtx-schedule-backend-init.yaml` and `jobs-config.yaml` contracts to reflect the new `ScheduleContext` fields and the DBUS-address removal.

- [ ] **Step 7: Commit**

```bash
git add skills/recurring-tasks/_rtx/_schedule_backend/ skills/recurring-tasks/_rtx/jobs.yaml skills/recurring-tasks/_rtx/blueprints/ skills/recurring-tasks/_rtx/tests/test_schedule_backend.py
git commit -m "fix(recurring-tasks): backend-owned scheduler context, drop hardcoded DBUS UID"
```

---

## Task 2: Truthful healthcheck exit code (feedback items 10, 13)

**Files:**
- Modify: `skills/recurring-tasks/_rtx/_healthcheck_probe.py` (`main()` at line 158; confirmed today it `return 0` unconditionally at both the load-failure except branch, line 173, and at the end of the function regardless of `problems`, line 210)
- Test: `skills/recurring-tasks/_rtx/tests/test_healthcheck.py`

- [ ] **Step 1: Write failing tests**

```python
def test_main_returns_nonzero_when_problems_found(monkeypatch, tmp_jobs_with_failure):
    exit_code = main([str(tmp_jobs_with_failure)])
    assert exit_code != 0


def test_main_returns_zero_when_no_problems(monkeypatch, tmp_jobs_all_healthy):
    exit_code = main([str(tmp_jobs_all_healthy)])
    assert exit_code == 0


def test_main_returns_nonzero_on_load_failure(monkeypatch, tmp_path):
    broken_jobs = tmp_path / "jobs.yaml"
    broken_jobs.write_text("not: valid: yaml: [")
    exit_code = main([str(broken_jobs)])
    assert exit_code != 0
```

(Match the real `main()` signature and existing test fixtures already present in `test_healthcheck.py` — extend that file's existing fixture style rather than inventing new ones.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_healthcheck.py -v`
Expected: FAIL — `main()` currently always returns 0 (confirmed at `_healthcheck_probe.py:173` and `:210`).

- [ ] **Step 3: Fix the exit code**

At `_healthcheck_probe.py:173` (the load-failure except branch), change `return 0` to `return 1`. At line 210 (function end), change the unconditional `return 0` to `return 1 if problems > 0 else 0`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_healthcheck.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/recurring-tasks/_rtx/_healthcheck_probe.py skills/recurring-tasks/_rtx/tests/test_healthcheck.py
git commit -m "fix(recurring-tasks): healthcheck exits nonzero when problems are found"
```

---

## Task 3: Run-record / success-contract separation (feedback items 14, 15, 20, 22)

**Files:**
- Create: `skills/recurring-tasks/_rtx/_run_record.py`
- Create: `skills/recurring-tasks/_rtx/blueprints/rtx-run-record.yaml`
- Modify: `skills/recurring-tasks/_rtx/_job_control.py` (`test_job()` at lines 97-104 currently reports launchd `kickstart`/systemd `start --wait` return code as "Test passed" — trigger/process conflation)
- Modify: `skills/recurring-tasks/_rtx/_job_executor.py` (currently writes only raw stdout/stderr to `run.log`, no run-boundary markers or `latest.json`)
- Modify: `skills/recurring-tasks/_rtx/jobs.yaml` (add a `success:` contract block per job — item 22)
- Test: `skills/recurring-tasks/_rtx/tests/test_job_executor.py`, `test_manage_job.py`

- [ ] **Step 1: Write failing tests for `JobRunRecord`**

```python
import json
from pathlib import Path

from _run_record import JobRunRecord, write_run_record, evaluate_success_contract


def test_write_run_record_creates_run_boundary_markers(tmp_path):
    record = JobRunRecord(job_name="email-triage", started_at="2026-07-27T00:00:00Z",
                           finished_at="2026-07-27T00:00:05Z", process_exit_code=0,
                           inner_status="ok", success=True)
    write_run_record(log_dir=tmp_path, record=record)
    latest = json.loads((tmp_path / "email-triage" / "latest.json").read_text())
    assert latest["success"] is True
    assert latest["started_at"] == "2026-07-27T00:00:00Z"


def test_evaluate_success_contract_fails_when_inner_status_is_error_despite_zero_exit():
    contract = {"require_inner_status": "ok"}
    result = evaluate_success_contract(process_exit_code=0, inner_status="error", contract=contract)
    assert result.success is False


def test_evaluate_success_contract_passes_when_no_contract_declared():
    result = evaluate_success_contract(process_exit_code=0, inner_status=None, contract={})
    assert result.success is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_job_executor.py -k run_record -v`
Expected: FAIL — `_run_record.py` doesn't exist yet.

- [ ] **Step 3: Implement `_run_record.py`**

```python
"""Separates 'the scheduler triggered a process' from 'the job actually
succeeded' by recording process exit code and inner task status together,
evaluated against each job's declared success contract."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class SuccessEvaluation:
    success: bool
    reason: str = ""


@dataclass(frozen=True)
class JobRunRecord:
    job_name: str
    started_at: str
    finished_at: str
    process_exit_code: int
    inner_status: str | None
    success: bool


def evaluate_success_contract(*, process_exit_code: int, inner_status: str | None, contract: dict) -> SuccessEvaluation:
    if process_exit_code != 0:
        return SuccessEvaluation(success=False, reason=f"process exit code {process_exit_code}")
    required = contract.get("require_inner_status")
    if required is not None and inner_status != required:
        return SuccessEvaluation(success=False, reason=f"inner status {inner_status!r} != required {required!r}")
    return SuccessEvaluation(success=True)


def write_run_record(*, log_dir: Path, record: JobRunRecord) -> None:
    job_dir = log_dir / record.job_name
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(record)
    tmp_path = job_dir / "latest.json.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, job_dir / "latest.json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_job_executor.py -k run_record -v`
Expected: PASS

- [ ] **Step 5: Wire into `_job_executor.py` (fixes items 15, 20)**

After a job's process completes, parse its inner status (however the executed skill already signals success/failure — check for an existing exit-code or status-file convention in `_job_executor.py` before inventing a new one), build a `JobRunRecord`, evaluate it against the job's `success:` contract (read from `jobs.yaml`), and call `write_run_record`. Add explicit `--- RUN START ---`/`--- RUN END (success=<bool>) ---` boundary markers to `run.log` around each execution.

- [ ] **Step 6: Fix `test_job()` trigger/process conflation (item 14)**

In `_job_control.py`, `test_job()` (lines 97-104) currently reports the OS scheduler's kickstart/start acceptance as "Test passed." Change it to trigger the job, wait for the resulting `latest.json` run record (with a bounded timeout), and report pass/fail based on `JobRunRecord.success`, not the trigger call's own return code.

- [ ] **Step 7: Add `success:` contracts to `jobs.yaml` (item 22)**

```yaml
jobs:
- name: email-triage
  description: Triage new emails into todo and triage lists
  command: ASSISTANT_DEFAULT=codex invoke-skill email-triage
  schedule: 0 3 * * *
  enabled: true
  success:
    require_inner_status: ok
- name: daily-plan
  description: Generate or show today's daily plan, including potential-actions triage
  command: invoke-skill daily-plan
  schedule: 0 7 * * *
  enabled: true
  success:
    require_inner_status: ok
```

- [ ] **Step 8: Run full recurring-tasks suite**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/ -v`
Expected: PASS, no regressions.

- [ ] **Step 9: Blueprint updates through `skill-maker`**

Update `blueprints/rtx-job-executor.yaml`, `rtx-job-control.yaml`, `jobs-config.yaml` contracts for the new run-record writes and `success:` schema field.

- [ ] **Step 10: Commit**

```bash
git add skills/recurring-tasks/_rtx/_run_record.py skills/recurring-tasks/_rtx/blueprints/rtx-run-record.yaml skills/recurring-tasks/_rtx/_job_control.py skills/recurring-tasks/_rtx/_job_executor.py skills/recurring-tasks/_rtx/jobs.yaml skills/recurring-tasks/_rtx/blueprints/rtx-job-executor.yaml skills/recurring-tasks/_rtx/blueprints/rtx-job-control.yaml skills/recurring-tasks/_rtx/tests/
git commit -m "feat(recurring-tasks): separate trigger acceptance from real task success via run records"
```

---

## Task 4: Daily-plan macOS platform gate + schedule frequency (feedback items 16, part of 7)

**Files:**
- Modify: `skills/daily-plan/_rtx/blueprints/rtx-plan-orchestrate.yaml:247-250`, `rtx-plan-storage.yaml:276-279` (both currently declare `platforms: {linux: true, macos: false, windows: false}` for PyYAML/rclone dependencies)
- (schedule frequency already corrected in Task 1 Step 4's `jobs.yaml` rewrite — this task only fixes the platform-gate root cause)
- Test: whatever test currently asserts `rtx-plan-orchestrate.yaml`'s platform map (check `skills/daily-plan/_rtx/tests/` for an existing blueprint-contract test to extend; if none exists, add one)

- [ ] **Step 1: Write a failing test**

```python
def test_daily_plan_dependencies_are_available_on_macos():
    import yaml
    from pathlib import Path
    payload = yaml.safe_load(Path("skills/daily-plan/_rtx/blueprints/rtx-plan-orchestrate.yaml").read_text())
    for dep in payload.get("runtime_dependencies", []):
        assert dep["platforms"].get("macos") is not False, f"{dep['name']} still gated off macOS"
```

(Adjust the exact YAML traversal to match the real structure of `runtime_dependencies` in this file — read it first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q skills/daily-plan/_rtx/tests/ -k macos -v`
Expected: FAIL — `platforms.macos` is currently `false`.

- [ ] **Step 3: Fix the platform declarations**

In both `rtx-plan-orchestrate.yaml` and `rtx-plan-storage.yaml`, change each dependency's `platforms.macos` (and `platforms.windows`, if also incorrectly `false` and the dependency is genuinely cross-platform — verify PyYAML and rclone both ship macOS/Windows wheels/binaries before flipping the flag) from `false` to `true`. Per the v5 schema (`references/blueprint/common.schema.json:365`), this is expressed per-dependency, not as a single top-level module flag — do not add a top-level `platforms:` key to the module blueprint.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q skills/daily-plan/_rtx/tests/ -k macos -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/daily-plan/_rtx/blueprints/rtx-plan-orchestrate.yaml skills/daily-plan/_rtx/blueprints/rtx-plan-storage.yaml
git commit -m "fix(daily-plan): stop gating PyYAML/rclone deps off macOS/Windows"
```

---

## Task 5: Rewrite `SKILL.md` to document all three backends (feedback item 11)

**Files:**
- Modify: `skills/recurring-tasks/SKILL.md`

- [ ] **Step 1: Read the current doc and each backend's real behavior**

Confirm `SKILL.md`'s current wording (audit found it "entirely systemd-worded" — re-verify at implementation time since Tasks 1-4 may have already required doc edits) and read `_linux_backend.py`, `_osx_backend.py`, `_windows_backend.py` for their real setup/teardown/log-location behavior.

- [ ] **Step 2: Rewrite the scheduling section**

Replace systemd-only language with a per-platform section (Linux: systemd user timers; macOS: launchd LaunchAgents; Windows: Task Scheduler), each describing environment inheritance, log locations, and how to inspect job status — matching what the code actually does post-Tasks 1-4, not the pre-rebase behavior.

- [ ] **Step 3: Commit**

```bash
git add skills/recurring-tasks/SKILL.md
git commit -m "docs(recurring-tasks): document macOS/Windows backends, not just systemd"
```

---

## Dependency order summary

```
Task 1 (backend-owned context, needs officina.install stable resolver from the installer-runtime rebase)
Task 2 (healthcheck exit code) ── independent, can land first
Task 4 (daily-plan platform gate) ── independent, can land any time
Task 3 (run records) ── depends on Task 1's ScheduleContext fields for resolver-aware execution
Task 5 (SKILL.md rewrite) ── last, documents the end state of Tasks 1-4
```

## Explicitly out of scope

- The desktop notifier and healthcheck test backfill already landed in `c3d40f7`/`a058cbb` — do not duplicate that work.
- Google-onboarding-driven recurring setup (installer subplan 03's Task 2 in the original umbrella) — tracked separately once subplan 03's rebase is finalized.
