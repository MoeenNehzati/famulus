# Cross-Platform Lifecycle and macOS Native Acceptance Implementation Plan

> **Deferred pending approved post-adoption rebase — do not execute.** The
> umbrella package is frozen. Its proposed artifacts are not authorized by the
> unified migration and require fresh functional-predecessor dispositions when
> rebased.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or, with explicit delegation approval, `superpowers:subagent-driven-development`.

**Goal:** Prove clean plugin lifecycle behavior on Linux, macOS, and Windows, plus selected Google/recurring onboarding and a real temporary macOS LaunchAgent completion path from stable installed commands.

**Architecture:** A hermetic lifecycle suite exercises the real platform bootstrap/installer with temporary homes, read-only source/plugin trees, and fake external services on all three OS runners. A separate macOS native CI smoke installs one uniquely labeled LaunchAgent, waits for process and task completion, captures diagnostics, and removes only its own state.

**Tech Stack:** Linux/macOS/Windows GitHub Actions, uv, managed CPython 3.11, launchd/systemd/Task Scheduler adapters, pytest.

## Global constraints

- Consume the authoritative contracts listed in the umbrella [contract-ownership table](README.md#contract-ownership); this file owns only integrated lifecycle/native acceptance.
- Full lifecycle acceptance depends on completed installer-runtime, dispatcher, Google, recurring, and downstream subplans. The native LaunchAgent smoke depends only on installer/dispatcher/recurring and can run earlier.
- Hermetic tests never contact Google, open a browser, touch the real scheduler, or modify real host configuration.
- Native cleanup targets one unique label/path in `finally`; it never performs broad scheduler deletion.
- Skill documentation changes use `skill-maker`.

## Source feedback owned here

Item 9, plus integrated verification of the installer, Google, and recurring invariants enumerated below. Dispatcher validation/detail contracts retain their focused gate in the dispatcher subplan.

---

### Task 1: Add a three-platform plugin lifecycle and macOS LaunchAgent acceptance gate

**Files:**
- Create: `skills/install-assistant-tools/tests/test_macos_feedback_acceptance.py`
- Create: `skills/install-assistant-tools/tests/test_managed_install_lifecycle.py`
- Modify: `skills/recurring-tasks/tests/test_scheduler_live_smoke.py`
- Modify: `.github/workflows/python-tests.yml`
- Modify: `docs/testing.md`
- Modify through `skill-maker`: `skills/install-assistant-tools/SKILL.md`
- Modify through `skill-maker`: `skills/recurring-tasks/SKILL.md`

**Interfaces:**
- Consumes: the completed installer-runtime, dispatcher-contract, Google-onboarding, and recurring-reliability subplans.
- Produces: hermetic lifecycle acceptance on `ubuntu-latest`, `macos-latest`, and `windows-latest`, plus real task execution on `macos-latest`.

- [ ] **Step 1: Write the hermetic acceptance test**

Parameterize the hermetic lifecycle suite over Linux, macOS, and Windows using a temporary HOME and plugin-cache-like read-only repo copy. Seed `$CODEX_HOME` and `$CLAUDE_HOME` with sentinel files and snapshot both trees before installation. Request optional launchers `collab`, `coauthor`, and `tw` without explicitly selecting `assistant`. The suite must prove:

1. bootstrap installs or uses pinned `uv`;
2. managed CPython reports 3.11;
3. `dispatcher`, `invoke-skill`, mandatory `assistant`, `_agent_launch.py`, platform wrappers, and selected `collab`, `coauthor`, and `tw` all exist under the platform `user_bin` (`~/.local/bin` on POSIX; `%LOCALAPPDATA%\Famulus\bin` on Windows), even though `assistant` was not selected;
4. dispatcher and every Python child report the managed `sys.executable`;
5. worker directories are outside the plugin cache;
6. no launcher path contains Documents, Desktop, or Downloads;
7. each backend renders its native scheduler location/record using the stable uv/runtime resolver (with no release path) and installed bin PATH; the macOS case uses `~/Library/LaunchAgents`;
8. trigger acceptance alone does not satisfy the test;
9. the job finishes with process exit 0 and a satisfied task contract;
10. aggregate health exits 0 only after all checks pass;
11. the complete `$CODEX_HOME` and `$CLAUDE_HOME` snapshots are byte-for-byte and link-for-link unchanged by plugin-mode launcher installation;
12. completion output names all absolute shared-launcher paths and platform handoff: POSIX names the exact modified rc file, shell-quoted `source` command, and new-terminal alternative; Windows names the persistent PATH target plus immediate PowerShell/cmd assignments and new-terminal alternative;
13. blank mode input selects plugin mode after the script displays both modes and marks plugin mode recommended;
14. selecting Drive, Calendar, and Gmail causes one stubbed `authorize-services` invocation, passes one credential reference to three service-owned configuration interfaces, and performs no per-service authorization invocation; and
15. opting into recommended recurring automation causes one stubbed `recurring-tasks.machine.scripts-setup` invocation with the installed absolute bin/uv/resolver/pointer/config/state paths and selected `default_llm`, without an LLM handoff or a second prompt set.

Add these lifecycle invariants on every platform:

16. the activated source/venv lives under one versioned release and `current.json` points to that same release;
17. removing the original plugin/cache/checkout after activation does not break installed commands;
18. a successful update builds and smokes a second release before atomically switching the pointer and retains the previous release;
19. injected failure at source copy, venv creation, dependency install, command smoke, manifest write, and pointer replacement leaves the prior pointer bytes and commands usable;
20. a read-only activated release still permits recurring jobs and email triage because jobs, logs, run records, status, watermark, and triage logs live under config/state roots;
21. candidate dependencies match the current platform aggregate and exclude dependencies declared only for another OS;
22. v1 manifest/legacy-path migration installs and verifies replacements before removing only proven installer-owned old paths, and an interrupted migration is retry-safe;
23. default uninstall preserves shared releases and Google secrets while another owner exists, and `--purge` removes only unreferenced credential records through the secret store;
24. install metadata, bootstrap files, and stable resolver resources exist in the packaged plugin artifact, not only in the source checkout; and
25. generated shims and scheduler records contain no plugin-cache, checkout, or obsolete release path.

Use stub Claude/Codex executables and deterministic dispatcher machine interfaces, including synthetic combined-Google and recurring-setup results; do not open a browser, contact Google, store real credentials, or touch the user's real scheduler in the hermetic test. Feed the installer one scripted interactive answer stream; do not pre-ask or translate selections in test orchestration.

- [ ] **Step 2: Run locally and verify the new test exposes remaining gaps**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_managed_install_lifecycle.py skills/install-assistant-tools/tests/test_macos_feedback_acceptance.py`

Expected before all dependency subplans land: failures naming the first unmet invariant. Expected after those subplans: PASS.

- [ ] **Step 3: Extend the opt-in native launchd smoke**

On `macos-latest`, create one uniquely named temporary LaunchAgent, trigger it with `launchctl kickstart -k`, wait for completion, assert last exit status, validate the run record/task contract, and remove only that LaunchAgent in `finally` cleanup. Before bootstrapping the native plist, simulate a stale prior location in the isolated test home and assert reload addresses the loaded service by label rather than the obsolete path. Include its plist, launchctl output, latest run record, and latest delimited log block on failure.

- [ ] **Step 4: Update CI and documentation**

Keep the ordinary unit matrix on Ubuntu/macOS/Windows with Python 3.11 and add the managed-runtime lifecycle suite to all three runners now. The host test Python may launch pytest but must not satisfy the product bootstrap; contract assertions prove the bootstrap invokes only pinned uv/managed Python. Keep real scheduler smoke separate: launchd is required on macOS; backend rendering/contract tests remain mandatory everywhere, with narrowly scoped opt-in native systemd/Task Scheduler smoke where the CI runner permits it. Document exactly what hermetic tests prove and what each native smoke additionally proves.

Rewrite recurring-task documentation so it describes the backend-neutral source of truth and the systemd/launchd/Task Scheduler implementations rather than calling the whole system systemd-only.

- [ ] **Step 5: Run the full local validation ladder**

Run:

```bash
python3 -m pytest -q skills/install-assistant-tools/tests
python3 -m pytest -q skills/recurring-tasks/tests
python3 -m pytest -q skills/daily-plan/tests skills/list-manager/tests skills/email-client/tests skills/email-triage/tests
python3 -m pytest -q skills/skill-maker/tests/test_blueprint_tools.py tests/validate_blueprints.py tests/test_dispatcher_route_smoke.py
python3 validators/runner.py
bash .githooks/skill/check-blueprints
```

Expected: all commands exit 0. Then run `python3 scripts/run-python-tests.py --suite full --verbose`; any unrelated pre-existing failure must be reported separately and must not be hidden by skipping this plan's focused tests.

- [ ] **Step 6: Commit after review**

Commit acceptance tests and documentation with message `test: cover managed install lifecycle`.

---

## Local acceptance criteria

The umbrella [integrated acceptance](README.md#integrated-acceptance) remains authoritative. This subplan is complete when:

- the hermetic suite proves all twenty-five invariants in Task 1 on Linux, macOS, and Windows without network, browser, real scheduler, or real host-configuration effects;
- the opt-in native smoke proves that one real temporary LaunchAgent reaches process completion and satisfies the task-result contract;
- failure output preserves the exact plist, `launchctl` diagnostics, run record, and delimited log block needed to diagnose the run;
- cleanup removes only the unique test service and its temporary state; and
- the three-platform lifecycle jobs, macOS native acceptance job, package-content checks, and documented local validation ladder pass after their dependency subplans land.
