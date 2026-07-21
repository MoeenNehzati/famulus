# Cross-Platform Recurring Reliability Implementation Plan

> **Deferred pending version-4 adoption and rebase — do not execute.** The
> umbrella package is frozen. Its proposed artifacts are not authorized by the
> unified migration and require fresh functional-predecessor dispositions when
> rebased.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or, with explicit delegation approval, `superpowers:subagent-driven-development`.

**Goal:** Make recurring setup backend-native and prove trigger, process, and requested task success separately on macOS, Linux, and Windows.

**Architecture:** The package ships immutable backend-neutral job defaults; the mutable `jobs.yaml` is installed under the Famulus config root. Scheduler backends own native paths, environments, reload, status, testing, and health scheduling. Every run writes an atomic result under the Famulus state root; installer onboarding calls recurring setup through dispatcher.

**Tech Stack:** Python 3.11, launchd, systemd user services, Windows Task Scheduler, dispatcher, pytest.

## Global constraints

- Inherit program-wide constraints and sequencing from the [umbrella](README.md). Consume runtime/result contracts from [installer Tasks 1-3](01-installer-runtime.md) and dispatcher failures from [dispatcher Task 2](02-dispatcher-contracts.md); this subplan owns recurring task-success contracts.
- macOS uses `~/Library/LaunchAgents`; Linux uses systemd user units; Windows uses Task Scheduler.
- Scheduled commands use the stable Famulus resolver/bin and obtain the managed interpreter from `current.json` at run time; native definitions never embed a release-specific interpreter.
- Scheduler configuration, logs, run records, and task-status artifacts never live in a skill, plugin cache, or activated release.
- Trigger acceptance, process completion, and task success are distinct states.
- Health returns nonzero for any scheduler, environment, process, task-contract, or freshness failure.
- Recurring setup is explicit opt-in and dispatcher-only from the installer.
- Skill/blueprint changes use `skill-maker`.

## Source feedback owned here

Items 7, 10, 11, 13-17, 20, and 22 in the umbrella traceability table.

---

### Task 1: Make scheduler defaults and environments backend-owned

**Files:**
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_base_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_linux_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_osx_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_windows_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_unit_writer.py`
- Modify: `skills/recurring-tasks/_rtx/_job_control.py`
- Modify: `skills/recurring-tasks/_rtx/_job_executor.py`
- Modify: `skills/recurring-tasks/_rtx/_live_probe.py`
- Modify: `skills/recurring-tasks/_rtx/_healthcheck_probe.py`
- Modify: `skills/recurring-tasks/_rtx/_ensure_agent_env.py`
- Modify: `skills/recurring-tasks/_rtx/_setup_runner.py`
- Modify: `skills/recurring-tasks/jobs.yaml`
- Modify: `skills/recurring-tasks/tests/test_schedule_backend.py`
- Modify: `skills/recurring-tasks/tests/test_sync_units.py`
- Modify: `skills/recurring-tasks/tests/test_setup_runner.py`
- Modify: `skills/recurring-tasks/tests/test_manage_job.py`
- Modify: `skills/recurring-tasks/tests/test_healthcheck.py`
- Modify: `skills/recurring-tasks/tests/test_scheduler_live_smoke.py`
- Modify through `skill-maker`: `skills/recurring-tasks/blueprint.yaml`
- Regenerate: `skills/recurring-tasks/SKILL.md`
- Regenerate: `references/blueprint/runtime_dependencies.json`

**Interfaces:**
- Changes: `ScheduleContext` gains `bin_dir: Path`, `uv_bin: Path`, `runtime_resolver: Path`, `runtime_pointer: Path`, `jobs_file: Path`, `state_root: Path`, `agent_command_template: str`, and `assistant_default: str`.
- Produces: one canonical context factory used by `_setup_runner`, `_unit_writer`, `_job_control`, `_job_executor`, live smoke, and health probes; no call site constructs a partial context or uses module-level skill-directory defaults.
- Changes: `recurring-tasks.machine.scripts-setup --bin-dir PATH --uv-bin PATH --runtime-resolver PATH --runtime-pointer PATH --jobs-file PATH --state-root PATH --assistant-default {claude,codex}` and the corresponding `_setup_runner` parser; all arguments are required.
- Produces: a validated `ScheduleContext` from those arguments; native backend selection and `agent_command_template="invoke-skill {skill}"` remain recurring-task-owned.
- Changes: `sync_units(..., unit_dir: Path | None, ...)`; `None` means the selected backend chooses its native directory.
- Changes: `ScheduleBackend.reload(label: str, unit_path: Path) -> None`; the macOS implementation unloads the service target by label and bootstraps the new plist path, so reload does not depend on finding the previous plist file.
- Produces: launchd `EnvironmentVariables` containing a managed PATH, `AI_AGENT_COMMAND_TEMPLATE`, `ASSISTANT_DEFAULT`, `FAMULUS_RUNTIME_POINTER`, `FAMULUS_RECURRING_CONFIG`, `FAMULUS_RECURRING_STATE`, `EMAIL_TRIAGE_STATE_DIR`, and `EMAIL_TRIAGE_LOG_FILE`; systemd and Task Scheduler receive equivalent explicit values in their native form.

- [ ] **Step 1: Add failing macOS context and plist tests**

Require a no-override sync to write to `~/Library/LaunchAgents`, and require generated plist content to contain:

```python
active_python = load_runtime_pointer(context.runtime_pointer).python_bin
assert plist["ProgramArguments"][:2] == [str(context.uv_bin), "run"]
assert str(context.runtime_resolver) in plist["ProgramArguments"]
assert str(context.runtime_pointer) not in plist["ProgramArguments"]  # resolver owns it
assert str(active_python) not in plist["ProgramArguments"]
assert plist["EnvironmentVariables"]["AI_AGENT_COMMAND_TEMPLATE"] == "invoke-skill {skill}"
assert plist["EnvironmentVariables"]["ASSISTANT_DEFAULT"] == "codex"
assert plist["EnvironmentVariables"]["PATH"].split(os.pathsep)[0] == str(context.bin_dir)
```

Add equivalent backend-specific assertions for systemd and Task Scheduler without forcing one backend's directory/environment convention onto another.

Add parser/contract tests requiring absolute bin, uv, resolver, pointer, jobs, and state paths plus `--assistant-default claude|codex`. Reject missing or relative paths, unsupported defaults, paths outside their validated Famulus roots, and caller-supplied scheduler-backend or command-template values. Assert the parsed values reach `ScheduleContext` unchanged and no path falls back to ambient `PATH`, `sys.executable`, `python3`, or an active-release interpreter.

Add constructor/call-site tests for `_unit_writer`, `_job_control`, `_job_executor`, `_live_probe`, `_healthcheck_probe`, and scheduler live smoke. Each must obtain the same absolute `jobs_file` and `state_root` from the canonical factory. Run the existing manage-job, health, and live-smoke suites in the RED/GREEN gate so making new context fields required cannot silently break an older constructor.

Add a macOS migration regression with an obsolete plist at `~/.config/systemd/user/ai-daily-plan.plist` and the replacement at `~/Library/LaunchAgents/ai-daily-plan.plist`. Mock `launchctl` and require this logical sequence:

```python
assert reload_commands == [
    ["launchctl", "bootout", f"gui/{uid}/ai-daily-plan"],
    ["launchctl", "bootstrap", f"gui/{uid}", str(new_plist)],
]
```

The first command may report that the label is not currently loaded; that is an idempotent absence, not a reason to skip bootstrapping the new plist. Assert that neither reload command uses the obsolete plist path and that the separate status query is `["launchctl", "print", f"gui/{uid}/ai-daily-plan"]`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest -q skills/recurring-tasks/tests/test_schedule_backend.py skills/recurring-tasks/tests/test_sync_units.py skills/recurring-tasks/tests/test_setup_runner.py`

Expected: the required setup flags/context wiring are absent, macOS receives the Linux default unit directory, and plist environment variables are absent.

- [ ] **Step 3: Pass `None` for native directory selection**

In `_unit_writer.main`, keep `unit_dir=None` when the caller did not provide `--unit-dir`. Only explicit test overrides become `Path` values. Delete the module-level assumption that `DEFAULT_UNIT_DIR` is valid for every backend.

Implement macOS reload against the service target `gui/<uid>/<label>`, followed by `launchctl bootstrap gui/<uid> <new-plist>`. Do not require the old plist path for unload or migration. Preserve explicit `--unit-dir` only as a test/advanced override; it must not change the stable service label.

- [ ] **Step 4: Generate complete native environments**

Build scheduler commands from the stable uv/bootstrap resolver plus user-command/config/state paths supplied during recurring setup. The resolver validates `current.json` immediately before every run and re-executes that release's managed interpreter. A normal release update therefore needs no scheduler rewrite; repair is required only when stable bootstrap/bin locations change. Remove Linux-only `DBUS_SESSION_BUS_ADDRESS` assignments from `jobs.yaml`; backend code owns backend-specific environment.

Implement the required setup flags and validation in `_setup_runner`, construct `ScheduleContext` with the exact validated values, and declare the machine-interface arguments through `skill-maker`. The command template remains an internal recurring constant and the backend remains platform-derived.

Change the defaults to:

```yaml
- name: email-triage
  command: invoke-skill email-triage
  schedule: "0 * * * *"
- name: daily-plan
  command: dispatcher --caller-skill daily-plan daily-plan.machine.orchestrate
  schedule: "0 7 * * *"
```

The daily-plan schedule is deliberately daily at 07:00 local time; email triage remains hourly.

Treat the packaged `skills/recurring-tasks/jobs.yaml` as immutable defaults only. On first setup, create `<config-root>/recurring-tasks/jobs.yaml`. On update, merge newly shipped job names/fields without overwriting an existing user's enabled state, schedule, assistant choice, or other explicit override; removed defaults are reported rather than silently deleting user configuration. All control, executor, probe, and backend reads use the installed config path. All logs and records use `<state-root>/recurring-tasks`.

- [ ] **Step 5: Make healthcheck scheduling native**

Extend the backend protocol with `sync_healthcheck(context: ScheduleContext, cadence_minutes: int) -> None`. Use systemd on Linux, launchd on macOS, and Task Scheduler on Windows. Each entry invokes the stable resolver, which re-executes the active managed interpreter; no entry embeds `python3` or a release path, and no platform mixes native jobs with an unrelated cron backend.

- [ ] **Step 6: Run scheduler tests**

Run: `python3 -m pytest -q skills/recurring-tasks/tests/test_schedule_backend.py skills/recurring-tasks/tests/test_sync_units.py skills/recurring-tasks/tests/test_setup_runner.py skills/recurring-tasks/tests/test_scheduler_live_smoke.py`

Expected: unit tests pass everywhere; live smoke remains opt-in outside CI.

- [ ] **Step 7: Commit after review**

Stage the scheduler backend/default/environment changes, setup parser/contract, generated artifacts, and tests. Commit with message `fix: make scheduler configuration backend owned`.

---

### Task 2: Move recurring-automation onboarding into installer-owned orchestration

**Files:**
- Modify through `skill-maker`: `skills/install-assistant-tools/blueprint.yaml`
- Modify through `skill-maker`: `skills/install-assistant-tools/SKILL.md`
- Create: `skills/install-assistant-tools/_rtx/_recurring_onboarding.py`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Create: `skills/install-assistant-tools/tests/test_recurring_onboarding.py`
- Modify: `skills/install-assistant-tools/tests/test_install.py`
- Modify through `skill-maker`: `skills/recurring-tasks/blueprint.yaml`
- Modify through `skill-maker`: `skills/recurring-tasks/SKILL.md`
- Regenerate: `references/blueprint/runtime_dependencies.json`
- Modify: `docs/installation.md`

**Interfaces:**
- Consumes: `InstallSelections.recurring_setup` and `InstallSelections.default_llm`, the completed installer's explicit stable bin/uv/resolver/pointer paths, recurring config/state paths, and the recurring setup interface completed in Task 1.
- Produces: `run_recurring_onboarding(selection: RecurringSetup, *, dispatcher_path: Path, bin_dir: Path, uv_bin: Path, runtime_resolver: Path, runtime_pointer: Path, jobs_file: Path, state_root: Path, assistant_default: Literal["claude", "codex"], dry_run: bool = False) -> OnboardingCapabilityResult`.
- Changes: `install-assistant-tools` declares dispatcher use of `recurring-tasks.machine.scripts-setup`; `recurring-tasks` explicitly permits the installer caller.
- Preserves: recurring setup remains an explicit opt-in and optional failure never rolls back or misreports the completed core installation.

- [ ] **Step 1: Write failing orchestration tests**

Create `test_recurring_onboarding.py` with a fake absolute dispatcher and assert `RecurringSetup.RECOMMENDED` invokes exactly:

```text
<absolute-dispatcher> --caller-skill install-assistant-tools recurring-tasks.machine.scripts-setup --bin-dir <absolute-bin> --uv-bin <absolute-uv> --runtime-resolver <absolute-resolver> --runtime-pointer <absolute-pointer> --jobs-file <absolute-jobs-file> --state-root <absolute-state-root> --assistant-default codex
```

Assert `RecurringSetup.SKIP` invokes nothing. Neither route may call `input()`, import recurring-task runtime code, inspect `jobs.yaml` directly, or ask an LLM to continue setup.

Parameterize `claude` and `codex` and assert the exact selected value reaches the recurring interface. Reject relative paths, a runtime outside the completed managed environment, an unsupported default, and any caller-supplied scheduler backend or command template. The dispatcher retry command must preserve the validated absolute paths and selected default without consulting ambient `PATH`, `sys.executable`, or an undeclared install-record reader.

Add cases for dispatcher rejection, unsupported scheduler backend, missing account capability, dry-run, and setup failure. Each failure returns a named optional capability result with `status`, `code`, `message`, and `retry_command`; it must not alter the already recorded core-install status.

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_recurring_onboarding.py`

Expected: collection fails because `_recurring_onboarding.py` does not exist and the cross-skill use is undeclared.

- [ ] **Step 2: Implement dispatcher-only recurring onboarding**

Create `_recurring_onboarding.py` as a thin subprocess orchestrator. It receives the already validated enum, explicit completed-install paths, and selected default LLM; invokes the installed dispatcher by absolute path with the declared flags; and parses only the recurring setup exit status/structured result. It does not reproduce scheduler logic, job definitions, prerequisite discovery, or platform-specific commands.

Task 1's recurring setup interface validates every absolute runtime/config/state value again, loads the pointer once to prove the active runtime is healthy, constructs `ScheduleContext` through the canonical factory using only stable paths, and lets the selected native backend choose its own unit directory. No setup path falls back to ambient `PATH`, `python3`, a release-specific path, skill-directory mutable files, or a default LLM that differs from the installer selection.

When recurring setup reports a missing prerequisite, retain that structured result in the final capability report and print the exact retry command. Do not convert it into a conversational LLM handoff. `--dry-run` renders the planned dispatcher invocation without executing it.

- [ ] **Step 3: Integrate recurring setup into the same installer run**

After core installation and selected Google onboarding, `_phase_entry.py` validates the active pointer and passes `InstallSelections.recurring_setup`, `InstallSelections.default_llm`, and the exact stable `user_bin`, `uv_bin`, resolver, pointer, installed `jobs_file`, and recurring `state_root` to `run_recurring_onboarding`. It then emits one final report separating:

```text
core_install: complete
google_onboarding: complete|partial|skipped|failed
recurring_automation: complete|skipped|blocked|failed
```

The process does not print “ask your assistant to set up recurring automation.” If the user skipped recurring automation, the report gives the non-interactive installer retry flag and direct dispatcher retry command without prompting again.

- [ ] **Step 4: Replace the LLM-owned Phase 2 contract**

Use `skill-maker` to declare the cross-skill interface use and regenerate both affected contracts. Remove prose that says remote/recurring setup is deliberately conversational or “not this skill's job to script.” Replace it with the script-first rule: the installer owns selection and orchestration, while service/scheduler skills own their machine operations behind dispatcher interfaces.

The LLM-facing installer workflow ends after launching the bootstrap and relaying its final report. It must not ask a second set of Google or recurring questions after the process exits.

- [ ] **Step 5: Run the focused installer/onboarding contract tests**

Run:

```text
python3 -m pytest -q \
  tests/test_officina_install_options.py \
  skills/install-assistant-tools/tests/test_install.py \
  skills/install-assistant-tools/tests/test_google_onboarding.py \
  skills/install-assistant-tools/tests/test_recurring_onboarding.py \
  skills/recurring-tasks/tests/test_setup_runner.py \
  tests/validate_blueprints.py
```

Expected: all pass; one installer invocation owns every deterministic choice and no test expects post-install LLM questioning.

- [ ] **Step 6: Commit after review**

Stage only the recurring-onboarding orchestration, changed contracts, generated artifact, tests, and documentation listed by this task. Commit with message `feat: script recurring setup during installation`.

---

### Task 3: Separate trigger, process, and task success in recurring automation

**Files:**
- Create: `skills/recurring-tasks/_rtx/_run_record.py`
- Create: `skills/recurring-tasks/tests/test_run_record.py`
- Modify: `skills/recurring-tasks/_rtx/_job_executor.py`
- Modify: `skills/recurring-tasks/_rtx/_job_control.py`
- Modify: `skills/recurring-tasks/_rtx/_healthcheck_probe.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_base_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_osx_backend.py`
- Modify: `skills/recurring-tasks/jobs.yaml`
- Modify: `skills/recurring-tasks/tests/test_job_executor.py`
- Modify: `skills/recurring-tasks/tests/test_healthcheck.py`
- Modify: `skills/recurring-tasks/tests/test_manage_job.py`

**Interfaces:**
- Produces: `JobRunRecord(run_id, job_name, started_at, finished_at, process_exit_code, task_status, task_detail)` serialized atomically to `<recurring-state-root>/logs/<job>/latest.json`.
- Produces: `evaluate_success_contract(job: Mapping[str, object], record: JobRunRecord, now: datetime) -> tuple[bool, str]`.
- Changes: `ScheduleBackend.test(...) -> TriggerResult`; trigger acceptance is not named or reported as test success.

- [ ] **Step 1: Write failing run-record and false-positive tests**

Cover all three levels explicitly:

```python
def test_trigger_accepted_is_not_job_success(fake_backend) -> None:
    fake_backend.trigger_result = TriggerResult(accepted=True, detail="kickstart accepted")
    result = test_job("daily-plan", timeout_seconds=1)
    assert result.success is False
    assert result.phase == "timeout"


def test_process_zero_with_failed_task_contract_is_failure(tmp_path) -> None:
    record = JobRunRecord(process_exit_code=0, task_status="failed", task_detail="dispatcher rejected macos")
    ok, reason = evaluate_success_contract(job, record, now)
    assert not ok
    assert "dispatcher rejected macos" in reason
```

Add a healthcheck CLI test requiring process exit code 1 whenever any enabled job fails.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest -q skills/recurring-tasks/tests/test_run_record.py skills/recurring-tasks/tests/test_job_executor.py skills/recurring-tasks/tests/test_manage_job.py skills/recurring-tasks/tests/test_healthcheck.py`

Expected: run-record module is absent, `scripts-test` returns immediately after kickstart, and healthcheck returns 0 on detected failures.

- [ ] **Step 3: Add per-run log and result records**

Before launching the command, `_job_executor` appends:

```text
=== run <uuid> started <ISO-8601> ===
```

After completion it appends the process and task result plus:

```text
=== run <uuid> finished <ISO-8601> ===
```

Write `latest.json` atomically. Historical log lines remain append-only, but every diagnostic can identify the latest run unambiguously.

- [ ] **Step 4: Add task success contracts**

Use deterministic process exit for direct dispatcher jobs. For LLM-driven email triage, add this `jobs.yaml` contract:

```yaml
success:
  kind: json-status
  path: ${EMAIL_TRIAGE_STATE_DIR}/status.json
  result_field: result
  success_value: ok
  timestamp_field: watermark_advanced_at
```

Resolve the environment placeholder before validation and reject a relative/missing state root. The task succeeds only when the status timestamp falls between the run's start and finish. An outer LLM process exit of 0 cannot override a missing or failed task contract.

- [ ] **Step 5: Make `scripts-test` wait for completion**

After scheduler trigger acceptance, poll the backend/run record until a new run ID finishes or the configurable timeout expires. Output one of `TRIGGERED`, `PROCESS-FAILED`, `TASK-FAILED`, `SUCCEEDED`, or `TIMED-OUT`; return nonzero for every result except `SUCCEEDED`.

- [ ] **Step 6: Strengthen aggregate health**

Health requires scheduler manager availability, job registration, resolvable executables/environment, a successful latest process, a satisfied task contract, and cadence-appropriate freshness. Fix the notifier path to `_assistant_desktop_notify.py`. Return 1 when any failure is detected and 2 for invalid invocation/configuration.

- [ ] **Step 7: Run focused tests**

Run: `python3 -m pytest -q skills/recurring-tasks/tests/test_run_record.py skills/recurring-tasks/tests/test_job_executor.py skills/recurring-tasks/tests/test_manage_job.py skills/recurring-tasks/tests/test_healthcheck.py`

Expected: all pass, including false-positive regressions.

- [ ] **Step 8: Commit after review**

Commit with message `fix: verify recurring task outcomes`.

---

### Task 4: Make daily-plan's macOS route contract truthful

**Files:**
- Modify through `skill-maker`: `skills/daily-plan/blueprint.yaml`
- Modify: `skills/daily-plan/tests/test_dispatch_contract.py`
- Modify: `skills/daily-plan/tests/test_plan_runtime.py`
- Modify: `tests/test_dispatcher_route_smoke.py`
- Regenerate: affected `SKILL.md` contract blocks and `references/blueprint/runtime_dependencies.json`

**Interfaces:**
- Changes: `daily-plan.machine.orchestrate` and its verified Python/rclone dependencies declare `macos: true`.

- [ ] **Step 1: Add a macOS route-closure test**

Build a route test that resolves `daily-plan.machine.orchestrate` for `macos` and recursively checks every declared dispatch/dependency edge required by that route. Assert no edge rejects macOS.

- [ ] **Step 2: Run tests and verify the route test RED**

Run: `python3 -m pytest -q skills/daily-plan/tests/test_dispatch_contract.py tests/test_dispatcher_route_smoke.py`

Expected: macOS route closure fails on the current false platform metadata.

- [ ] **Step 3: Correct platform declarations through `skill-maker`**

In `skills/daily-plan/blueprint.yaml`, change only the daily-plan interfaces and their in-file PyYAML/rclone dependency declarations whose implementations/binaries are verified on macOS. Regenerate contracts and dependency artifacts, then run `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check`.

- [ ] **Step 4: Run the direct route and focused suites**

Run: `python3 -m pytest -q skills/daily-plan/tests/test_dispatch_contract.py skills/daily-plan/tests/test_plan_runtime.py tests/test_dispatcher_route_smoke.py`

Expected: all pass on Linux; the generated route metadata explicitly permits macOS.

- [ ] **Step 5: Commit after review**

Commit with message `fix: declare daily plan macOS support`.

---
