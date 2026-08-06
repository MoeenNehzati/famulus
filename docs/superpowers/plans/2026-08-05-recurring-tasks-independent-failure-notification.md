# Recurring-Tasks Independent Failure Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the four-hour cron health check independent of systemd while making the cron registration itself show a desktop popup whenever the check cannot launch or exits nonzero.

**Architecture:** The managed cron line invokes the health checker through the installer's stable runtime resolver, redirects checker output to the skill-owned health-check log, and directly invokes `/usr/bin/notify-send` with the user D-Bus environment after any nonzero result. The health checker becomes notification-free and validates Linux unit contents against current job configuration so stale generated services are reported explicitly.

**Tech Stack:** Python 3.11+, cron, systemd user units, `notify-send`, pytest, version-5 skill blueprints.

## Global Constraints

- Scope is limited to `recurring-tasks`; unrelated cron jobs and dirty repository files remain untouched.
- Cron remains the independent Linux sentinel; do not replace it with systemd.
- Preserve the existing `0 */4 * * *` cadence.
- Notify on every failed cron invocation; do not deduplicate.
- The cron fallback must catch missing launcher, routing, interpreter, checker, and unhealthy-result failures.
- Persistent logs remain under the owning skill directory.
- Do not add a watchdog for cron itself.
- Tests must isolate crontab, filesystem, subprocess, and scheduler boundaries.
- Do not commit or push without separate authorization.

---

### Task 1: Make cron own the generic failure popup

**Files:**
- Modify: `skills/recurring-tasks/_rtx/_setup_runner.py`
- Test: `skills/recurring-tasks/_rtx/tests/test_setup_runner.py`

**Interfaces:**
- Consumes: the installer's managed runtime resolver and the private health-check entrypoint.
- Produces: `render_healthcheck_cron(*, runtime_resolver: Path, healthcheck: Path, log_file: Path, uid: int) -> str` and `install_healthcheck_cron(*, skill_root: Path, runtime_resolver: Path, healthcheck: Path, uid: int, migrate_cron: bool = False) -> None`.

- [ ] **Step 1: Replace setup-runner cron tests with contract-focused failing tests**

Add assertions that the rendered managed line:

```python
line = setup_runner.render_healthcheck_cron(
    runtime_resolver=tmp_path / "runtime" / "launch.py",
    healthcheck=tmp_path / "skill" / "_rtx" / "_healthcheck_probe.py",
    log_file=tmp_path / "logs" / "healthcheck" / "run.log",
    uid=1000,
)
assert line.startswith("0 */4 * * * ")
assert "recurring-tasks.interface.scripts-healthcheck" in line
assert "skills/recurring-tasks" not in line
assert "XDG_RUNTIME_DIR=/run/user/1000" in line
assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in line
assert "/usr/bin/notify-send" in line
assert setup_runner.CRON_MARKER in line
```

Add separate tests proving that installation replaces an existing managed line, preserves unrelated lines exactly, and rewrites the same desired content on repeated setup without duplication.

- [ ] **Step 2: Run the focused tests and confirm the new contract fails**

Run:

```bash
pytest skills/recurring-tasks/_rtx/tests/test_setup_runner.py -q
```

Expected: FAIL because `render_healthcheck_cron` and the new installation signature do not exist.

- [ ] **Step 3: Implement deterministic cron rendering and replacement**

Use `shlex.quote` for every absolute path and notification string. The rendered shell semantics must be:

```text
RECURRING_TASKS_HEALTHCHECK_CRON=1 <runtime-resolver> <healthcheck-entrypoint> >> <log> 2>&1 || XDG_RUNTIME_DIR=/run/user/<uid> DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus /usr/bin/notify-send --urgency=critical "Recurring tasks need attention" "The recurring-tasks health check failed. See its health-check log."
```

Remove every existing line carrying `# ai-recurring-healthcheck`, append exactly one freshly rendered line, and preserve every unrelated line in original order. Create `<skill-root>/logs/healthcheck` before writing the crontab. On non-Linux hosts, skip the independent cron installation with an explicit status line.

- [ ] **Step 4: Run setup-runner tests**

Run the Step 2 command. Expected: PASS.

---

### Task 2: Make the checker pure and keep one copy of each log line

**Files:**
- Modify: `skills/recurring-tasks/_rtx/_healthcheck_probe.py`
- Test: `skills/recurring-tasks/_rtx/tests/test_healthcheck.py`

**Interfaces:**
- Consumes: `RECURRING_TASKS_HEALTHCHECK_CRON=1` from the managed cron line.
- Produces: a checker that prints diagnostics and returns `0` or `1` without sending desktop notifications; manual invocations append to the skill-owned log, while cron invocations rely on stdout redirection to avoid duplicate log lines.

- [ ] **Step 1: Write failing tests for notification-free execution**

Delete tests for `notify_desktop`. Add tests proving:

```python
with mock.patch.dict(mod.os.environ, {"RECURRING_TASKS_HEALTHCHECK_CRON": "1"}):
    mod.log("one line")
assert not mod.HEALTHCHECK_LOG.exists()

with mock.patch.dict(mod.os.environ, {}, clear=True):
    mod.log("one line")
assert mod.HEALTHCHECK_LOG.read_text().count("one line") == 1
```

Update `main()` tests to assert only exit status and report content; no notification collaborator may be called on success, unhealthy state, or configuration-load failure.

- [ ] **Step 2: Run health-check tests and confirm failure**

Run:

```bash
pytest skills/recurring-tasks/_rtx/tests/test_healthcheck.py -q
```

Expected: FAIL because the checker still owns desktop notifications and always appends its log directly.

- [ ] **Step 3: Remove notification behavior and add cron-aware logging**

Remove `NOTIFY_SCRIPT`, `notify_desktop`, and their subprocess dependency. Set `HEALTHCHECK_LOG` to the top-level skill-owned `logs/healthcheck/run.log`. Implement `log()` so it always prints, but appends directly only when `RECURRING_TASKS_HEALTHCHECK_CRON` is absent. Preserve the existing zero/nonzero exit contract and full failure diagnostics.

- [ ] **Step 4: Run health-check tests**

Run the Step 2 command. Expected: PASS.

---

### Task 3: Detect stale Linux scheduler registrations directly

**Files:**
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_base_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_linux_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_osx_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_windows_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_healthcheck_probe.py`
- Test: `skills/recurring-tasks/_rtx/tests/test_schedule_backend.py`
- Test: `skills/recurring-tasks/_rtx/tests/test_healthcheck.py`

**Interfaces:**
- Produces: `ScheduleBackend.check_job_configuration(job: ScheduleJob, context: ScheduleContext) -> str | None`.
- Consumes: existing `service_content`, `timer_content`, `cron_to_systemd_calendar`, `ScheduleJob.from_mapping`, and `ScheduleContext`.

- [ ] **Step 1: Write failing Linux drift tests**

Create a temporary Linux unit directory and assert:

```python
reason = LinuxScheduleBackend().check_job_configuration(job, context)
assert reason == "my-job: service unit missing"
```

After writing current expected service and timer content, assert `None`; after replacing the jobs-file path or `OnCalendar`, assert a descriptive `service unit stale` or `timer unit stale` reason.

Add a health-check test proving configuration drift is checked before run-log freshness and returned as the job's failure reason.

- [ ] **Step 2: Run focused backend and health-check tests and confirm failure**

Run:

```bash
pytest skills/recurring-tasks/_rtx/tests/test_schedule_backend.py skills/recurring-tasks/_rtx/tests/test_healthcheck.py -q
```

Expected: FAIL because the backend contract has no configuration check.

- [ ] **Step 3: Implement the backend contract**

Add the method to the protocol. The Linux implementation compares both generated unit files byte-for-byte with `service_content(...)` and `timer_content(...)` built from the live `ScheduleJob` and `ScheduleContext`. Return a precise reason for missing, unreadable, or stale files. The macOS and Windows backends return `None` because independent cron drift validation is Linux-scoped by the approved design.

Update `check_job()` to construct `ScheduleJob.from_mapping(job)`, construct the current `ScheduleContext`, and return configuration drift before inspecting run logs or active state.

- [ ] **Step 4: Run focused backend and health-check tests**

Run the Step 2 command. Expected: PASS.

---

### Task 4: Align contracts, regenerate derived documentation, and repair the live installation

**Files:**
- Modify: `skills/recurring-tasks/_rtx/blueprints/rtx-setup-runner.yaml`
- Modify: `skills/recurring-tasks/_rtx/blueprints/rtx-healthcheck-probe.yaml`
- Modify: `skills/recurring-tasks/_rtx/blueprints/rtx-schedule-backend-init.yaml`
- Modify: `skills/recurring-tasks/SKILL.md` only outside generated blocks
- Regenerate: generated `SKILL.md` contract blocks and runtime-dependency manifest through `skill-maker.interface.sync-blueprints`

**Interfaces:**
- Consumes: the tested behavior from Tasks 1-3.
- Produces: blueprint contracts that describe cron-owned notification, pure checker output, skill-owned logs, and Linux configuration-drift checks.

- [ ] **Step 1: Update canonical blueprints and authored prose**

Remove desktop-notification effects from the health-check interface. Describe the checker as returning nonzero on failure and appending its report. Update setup's crontab write contract to include the managed fallback notification and stable runtime-resolver invocation. Keep operating-system names only in structured platform metadata, not generic blueprint prose. Update the authored skill body to explain that setup installs an independent periodic sentinel and that failures surface as popups from the scheduler registration.

- [ ] **Step 2: Regenerate and check derived blueprint documentation**

Run:

```bash
dispatcher --caller-skill skill-maker skill-maker.interface.sync-blueprints
dispatcher --caller-skill skill-maker skill-maker.interface.sync-blueprints --check
```

Expected: both commands exit `0`.

- [ ] **Step 3: Run the recurring-tasks regression suite**

Run:

```bash
pytest skills/recurring-tasks/_rtx/tests -q
```

Expected: PASS.

- [ ] **Step 4: Install the repaired live scheduler state**

Invoke `recurring-tasks.interface.scripts-setup --migrate-cron`. Verify the managed crontab line uses the installed runtime resolver and contains the direct notification fallback. Verify generated systemd services reference the current jobs file and their timers show 3:00 AM and 7:00 AM schedules.

- [ ] **Step 5: Run live jobs and health checks**

Invoke `recurring-tasks.interface.scripts-test email-triage`, `recurring-tasks.interface.scripts-test daily-plan`, and `recurring-tasks.interface.scripts-healthcheck`. Expected: fresh structured run records for both jobs and a zero health-check exit.

- [ ] **Step 6: Exercise the cron failure notification path**

Run the installed cron-compatible fallback once with a deliberately failing checker command, preserving the real crontab. Expected: one visible `Recurring tasks need attention` popup. Then run the healthy installed command and confirm it exits zero without a popup.

- [ ] **Step 7: Inspect the exact scoped diff**

Confirm only the design, plan, recurring-tasks implementation, tests, blueprints, and regenerated recurring-tasks artifacts changed. Leave all pre-existing standards/refactoring work untouched.
