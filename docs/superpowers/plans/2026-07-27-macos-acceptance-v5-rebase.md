# Cross-Platform and macOS Integrated Acceptance (v5 Rebase) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the integrated three-platform install/update/rollback/uninstall lifecycle described in `docs/plans/osx_feedback_fix/README.md`'s "Integrated acceptance" section, superseding `docs/plans/osx_feedback_fix/06-macos-acceptance.md`. Feedback item 9 (native macOS LaunchAgent completion path) is **already substantially implemented** — this plan extends it rather than duplicating it.

**Architecture:** A new `tests/test_install_lifecycle.py` (or equivalent, exact location TBD at implementation time) drives the managed-runtime install/update/rollback/uninstall/purge invariants against the `officina.install` module built in the installer-runtime rebase, on a temporary home directory, across the existing 3-OS CI matrix. The native macOS LaunchAgent smoke stays where it already lives (`skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py::_macos_smoke`) — this plan only closes its one real gap (stale-prior-location reload-by-label) rather than rewriting it.

**Tech Stack:** Python 3.11+, pytest, GitHub Actions, launchd/systemd/Windows Task Scheduler.

**Hard dependency:** This entire plan is gated on the installer-runtime, dispatcher-contracts, google-onboarding, recurring-reliability, and downstream-workflows rebases landing first — its acceptance invariants only make sense once `officina.install`'s `current.json`/versioned-release/rollback mechanism actually exists. Do not start Task 1 until `docs/superpowers/plans/2026-07-27-osx-installer-runtime-v5-rebase.md` is implemented through at least Task 7.

---

## Task 0: Confirm what already exists (verification only, no code change)

**Files:** none — read-only verification.

- [ ] **Step 1: Confirm the existing 3-OS CI matrix**

Run: `grep -n "matrix\|runs-on" .github/workflows/python-tests.yml`
Expected: a matrix already runs `ubuntu-latest`, `macos-latest`, `windows-latest`, each executing the full test suite — confirmed present today. This plan extends that matrix's job list, it does not create a new one.

- [ ] **Step 2: Confirm the existing native LaunchAgent smoke**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py -v --collect-only`
Expected: `test_live_scheduler_fires_and_cleans_up` exists and dispatches to `_macos_smoke()` on macOS, which already does `launchctl bootstrap` → `launchctl kickstart -k` → wait-for-marker → `launchctl bootout` cleanup with existence assertions (`skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py:213-247`). This plan's Task 3 extends this test — it does not create a parallel one.

- [ ] **Step 3: No commit for this task** — proceed to Task 1.

---

## Task 1: Managed-runtime lifecycle invariants (install/update/rollback/uninstall/purge)

**Files:**
- Create: `tests/test_install_lifecycle.py`

- [ ] **Step 1: Write failing tests for first install**

```python
import os
import sys
from pathlib import Path

import pytest


def test_first_install_creates_a_release_and_activates_it(tmp_path):
    from officina.install.managed_runtime import build_candidate_release
    from officina.install.runtime_pointer import load_current_pointer
    runtime_root = tmp_path / "runtime"
    release_dir = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=Path("references/blueprint/runtime_dependencies.json"),
        platform=sys.platform,
        uv_bin=Path(os.environ.get("UV_BIN", "uv")),
    )
    pointer = load_current_pointer(runtime_root=runtime_root)
    assert pointer.runtime_source == release_dir


def test_first_install_requires_no_system_python(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("sys.executable", "/should/never/be/used/python3")
    # Assert build_candidate_release never shells out with sys.executable —
    # only with the managed interpreter path it creates itself.
    ...
```

- [ ] **Step 2: Run tests to verify they fail or pass against the real Task 4-7 artifacts**

Run: `python3 -m pytest -q tests/test_install_lifecycle.py -k first_install -v`
Expected: these should PASS once the installer-runtime rebase's Tasks 4, 5, 7 have landed (this task doesn't add new production code, it adds an integration-level test over already-built pieces) — if it fails, the installer-runtime rebase isn't actually complete yet; stop and finish that first rather than adding acceptance-layer workarounds.

- [ ] **Step 3: Write failing tests for update/rollback**

```python
def test_successful_update_activates_new_release_without_touching_old(tmp_path):
    from officina.install.managed_runtime import build_candidate_release
    from officina.install.runtime_pointer import load_current_pointer
    runtime_root = tmp_path / "runtime"
    first = build_candidate_release(runtime_root=runtime_root, manifest_path=MANIFEST, platform=sys.platform, uv_bin=UV_BIN)
    second = build_candidate_release(runtime_root=runtime_root, manifest_path=MANIFEST, platform=sys.platform, uv_bin=UV_BIN)
    pointer = load_current_pointer(runtime_root=runtime_root)
    assert pointer.runtime_source == second
    assert first.exists()  # previous release retained for rollback


def test_failed_update_leaves_prior_pointer_and_release_usable(monkeypatch, tmp_path):
    from officina.install.managed_runtime import build_candidate_release, ManagedRuntimeError
    from officina.install.runtime_pointer import load_current_pointer
    runtime_root = tmp_path / "runtime"
    good = build_candidate_release(runtime_root=runtime_root, manifest_path=MANIFEST, platform=sys.platform, uv_bin=UV_BIN)

    monkeypatch.setattr("officina.install.managed_runtime._run_dependency_install",
                         lambda **k: (_ for _ in ()).throw(ManagedRuntimeError("simulated")))
    with pytest.raises(ManagedRuntimeError):
        build_candidate_release(runtime_root=runtime_root, manifest_path=MANIFEST, platform=sys.platform, uv_bin=UV_BIN)

    pointer = load_current_pointer(runtime_root=runtime_root)
    assert pointer.runtime_source == good
    assert good.exists()


def test_rollback_reactivates_previous_release(tmp_path):
    from officina.install.managed_runtime import build_candidate_release
    from officina.install.runtime_pointer import activate_release, load_current_pointer
    runtime_root = tmp_path / "runtime"
    first = build_candidate_release(runtime_root=runtime_root, manifest_path=MANIFEST, platform=sys.platform, uv_bin=UV_BIN)
    build_candidate_release(runtime_root=runtime_root, manifest_path=MANIFEST, platform=sys.platform, uv_bin=UV_BIN)
    activate_release(runtime_root=runtime_root, release_dir=first, python_bin=first / "venv" / "bin" / "python")
    assert load_current_pointer(runtime_root=runtime_root).runtime_source == first
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q tests/test_install_lifecycle.py -v`
Expected: PASS (these exercise real installer-runtime-rebase code, not new production logic).

- [ ] **Step 5: Write failing tests for uninstall/purge**

```python
def test_default_uninstall_removes_only_this_installations_resources(tmp_path):
    # Requires manifest-v2 (Task 8 of the installer-runtime rebase, "explicitly out of scope"
    # for the rebase itself but needed here for a real ownership-aware uninstall test).
    # If manifest-v2 hasn't landed yet, this test should be skipped with a clear reason,
    # not faked — mark it `@pytest.mark.skip(reason="requires manifest-v2, tracked separately")`
    # until that follow-up plan lands.
    ...


def test_purge_removes_config_and_state_roots(tmp_path):
    ...
```

- [ ] **Step 6: Run and confirm skip reasoning is explicit**

Run: `python3 -m pytest -q tests/test_install_lifecycle.py -v`
Expected: uninstall/purge tests SKIP with an explicit reason if manifest-v2 isn't landed yet; everything else PASSES. Do not fake ownership-aware uninstall behavior just to make this task look complete — that would produce a false acceptance signal.

- [ ] **Step 7: Commit**

```bash
git add tests/test_install_lifecycle.py
git commit -m "test: integrated managed-runtime lifecycle acceptance (install/update/rollback)"
```

---

## Task 2: v1-manifest and legacy-path migration acceptance

**Files:**
- Create: `tests/test_legacy_migration.py`

- [ ] **Step 1: Write failing test for legacy manifest migration**

```python
def test_v1_manifest_migrates_to_v2_losslessly(tmp_path):
    from skills.install_assistant_tools._rtx._state_record import load_manifest, MANIFEST_VERSION
    legacy_manifest = tmp_path / "install-manifest.json"
    legacy_manifest.write_text(json.dumps({
        "manifest_version": 1,
        "entries": [{"kind": "launcher", "path": str(tmp_path / "bin" / "dispatcher")}],
    }))
    migrated = load_manifest(legacy_manifest)
    assert migrated.manifest_version == MANIFEST_VERSION
    # Every legacy entry must have a corresponding record post-migration:
    assert any(str(tmp_path / "bin" / "dispatcher") in str(r) for r in migrated.all_paths())
```

(This depends on manifest-v2 landing — the installer-runtime rebase explicitly scoped it out as a separate follow-up. If that follow-up hasn't landed by the time this task runs, mark this test `@pytest.mark.skip` with a clear reason rather than blocking this whole acceptance plan on it.)

- [ ] **Step 2: Run test**

Run: `python3 -m pytest -q tests/test_legacy_migration.py -v`
Expected: PASS once manifest-v2 lands, or an explicit SKIP with reason until then.

- [ ] **Step 3: Commit**

```bash
git add tests/test_legacy_migration.py
git commit -m "test: v1-manifest to v2 migration acceptance"
```

---

## Task 3: Close the one real gap in the native macOS LaunchAgent smoke (feedback item 9)

**Files:**
- Modify: `skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py` (`_macos_smoke()`, currently lines 213-247)

**Do not duplicate the existing smoke.** The audit confirmed `_macos_smoke()` already does real `launchctl bootstrap`/`kickstart`/wait-for-marker/`bootout` cleanup with existence assertions. The only gap identified: it doesn't simulate the "stale prior location, reload-by-label" case (a LaunchAgent plist left over from a previous release pointing at a now-removed path, then a new install needing to detect and replace it by label rather than erroring).

- [ ] **Step 1: Write a failing test for the stale-prior-location case**

```python
def test_macos_smoke_replaces_stale_prior_location_by_label():
    if sys.platform != "darwin":
        pytest.skip("macOS-only")
    _macos_smoke_with_stale_prior_plist()
```

Add `_macos_smoke_with_stale_prior_plist()` alongside the existing `_macos_smoke()` in the same test file: write a plist at the job's label pointing at a nonexistent old release path, bootstrap it (expect it to already be loaded/stale), then run the real install/activation flow and assert `launchctl print <target>/<label>` now reports the **new** program path, not the stale one, before running the existing wait-for-marker/cleanup sequence.

- [ ] **Step 2: Run test to verify it fails**

Run: `FAMULUS_RUN_SCHEDULER_SMOKE=1 python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py -k stale_prior_location -v` (macOS runner only)
Expected: FAIL — no reload-by-label handling exists yet.

- [ ] **Step 3: Implement reload-by-label handling in the macOS schedule backend**

In `skills/recurring-tasks/_rtx/_schedule_backend/_osx_backend.py`, before `launchctl bootstrap`, check whether the label is already loaded (`launchctl print <target>/<label>`); if so, `launchctl bootout` the stale entry first, then bootstrap the new plist — this is the same "current.json"-driven reload semantics the installer-runtime rebase's Task 6 established for shell launchers, applied here to LaunchAgent plists.

- [ ] **Step 4: Run test to verify it passes**

Run: `FAMULUS_RUN_SCHEDULER_SMOKE=1 python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py -v` (macOS runner only)
Expected: PASS, including the pre-existing `test_live_scheduler_fires_and_cleans_up` (no regression).

- [ ] **Step 5: Commit**

```bash
git add skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py skills/recurring-tasks/_rtx/_schedule_backend/_osx_backend.py
git commit -m "fix(recurring-tasks): macOS LaunchAgent reload-by-label when a stale prior entry is loaded"
```

---

## Task 4: Wire the new lifecycle tests into the existing CI matrix

**Files:**
- Modify: `.github/workflows/python-tests.yml`

**Do not create a new matrix job.** The 3-OS matrix (`ubuntu-latest`, `macos-latest`, `windows-latest`) already exists and already runs `python3 scripts/run-python-tests.py --suite full --verbose`. This task only ensures `tests/test_install_lifecycle.py` and `tests/test_legacy_migration.py` are included in that existing full-suite run (confirm they are picked up automatically by whatever discovery `run-python-tests.py --suite full` already uses — do not add a redundant explicit step unless discovery misses them).

- [ ] **Step 1: Confirm discovery picks up the new tests**

Run: `python3 scripts/run-python-tests.py --suite full --verbose 2>&1 | grep -c "test_install_lifecycle\|test_legacy_migration"`
Expected: nonzero — both files are discovered without any workflow change.

- [ ] **Step 2: If discovery misses them, and only then, add explicit inclusion**

Only if Step 1 shows zero matches, modify `.github/workflows/python-tests.yml`'s test-selection step to explicitly include the new files. Otherwise, skip this step — no workflow change is needed.

- [ ] **Step 3: Commit (only if Step 2 was needed)**

```bash
git add .github/workflows/python-tests.yml
git commit -m "ci: include managed-runtime lifecycle acceptance tests in the existing 3-OS matrix"
```

---

## Dependency order summary

```
Task 0 (verify existing CI matrix + LaunchAgent smoke) ── no dependency, do first
Task 1 (lifecycle invariants) ── depends on installer-runtime rebase Tasks 4,5,7
Task 2 (manifest migration) ── depends on the separate manifest-v2 follow-up (may SKIP until then)
Task 3 (LaunchAgent reload-by-label) ── independent of Tasks 1-2, only needs the installer-runtime rebase's Task 6 stable-resolver convention
Task 4 (CI wiring) ── last, after Tasks 1-3 exist
```

## Explicitly out of scope / already done, do not duplicate

- The 3-OS CI matrix itself — already exists in `.github/workflows/python-tests.yml`.
- The core native macOS LaunchAgent bootstrap/kickstart/cleanup smoke — already exists in `test_scheduler_live_smoke.py::_macos_smoke`; Task 3 extends it with one missing case, it does not rewrite it.
- Dev-mode install/uninstall/launcher-survival invariants — already covered by `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py` (commit `25354d7`); this plan's Task 1 covers the plugin-mode managed-runtime model that test does not touch, not a duplicate of it.
