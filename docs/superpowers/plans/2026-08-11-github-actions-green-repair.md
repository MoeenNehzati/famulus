# GitHub Actions Green Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Documentation Pages and all three Python GitHub Actions matrix jobs pass reliably on `master`.

**Architecture:** Establish one deterministic CI dependency input, repair the platform boundary bugs exposed by Windows and macOS, and keep native installer/scheduler tests meaningful through isolated test setup and run-specific evidence. Repository settings are changed only to activate the already-committed Pages workflow.

**Tech Stack:** GitHub Actions, Python 3.11, pytest/pytest-xdist, jsonschema, pathlib/URI handling, keyring, Windows Task Scheduler, MkDocs/GitHub Pages.

## Global Constraints

- Preserve the Ubuntu, macOS, and Windows matrix and its current two pytest workers.
- Do not suppress native keyring or scheduler failures.
- Do not migrate the complete configured-schema subsystem.
- Do not touch `docs/reports/2026-08-11-skill-description-invocation-audit.md`.
- Stage only files owned by this repair.

---

### Task 1: Deterministic CI environment and Pages activation

**Files:**
- Create: `requirements-ci.txt`
- Modify: `.github/workflows/python-tests.yml`
- Create: `tests/test_ci_workflow_contract.py`

**Interfaces:**
- Consumes: Python 3.11 and the existing `repo_checks.py --suite full` workflow entrypoint.
- Produces: `requirements-ci.txt`, the only Python dependency input used by the Python Tests workflow.

- [ ] **Step 1: Add failing workflow contract tests**

Add tests which load `.github/workflows/python-tests.yml` as text and assert that the install step uses `python3 -m pip install -r requirements-ci.txt`, and which parse `requirements-ci.txt` to require exact entries for `pytest`, `pytest-xdist`, `PyYAML`, `jsonschema`, `keyring`, `cryptography`, and `lark`.

```python
def test_python_ci_installs_the_locked_ci_requirements() -> None:
    workflow = (REPO_ROOT / ".github/workflows/python-tests.yml").read_text()
    assert "python3 -m pip install -r requirements-ci.txt" in workflow


def test_ci_requirements_cover_every_imported_test_dependency() -> None:
    requirements = (REPO_ROOT / "requirements-ci.txt").read_text().splitlines()
    names = {line.split("==", 1)[0].lower() for line in requirements if line}
    assert names == {
        "pytest", "pytest-xdist", "pyyaml", "jsonschema",
        "keyring", "cryptography", "lark",
    }
```

- [ ] **Step 2: Verify the workflow tests fail for the missing file and old install command**

Run: `python3 -m pytest -q tests/test_ci_workflow_contract.py`

Expected: FAIL because `requirements-ci.txt` does not exist and the workflow contains an inline floating install list.

- [ ] **Step 3: Add the verified dependency lock and switch the workflow**

Create:

```text
pytest==8.3.4
pytest-xdist==3.8.0
PyYAML==6.0.2
jsonschema==4.23.0
keyring==25.6.0
cryptography==44.0.1
lark==1.3.1
```

Replace the inline workflow install command with:

```yaml
run: python3 -m pip install -r requirements-ci.txt
```

- [ ] **Step 4: Verify the workflow contract tests pass**

Run: `python3 -m pytest -q tests/test_ci_workflow_contract.py`

Expected: PASS.

- [ ] **Step 5: Enable the existing Pages workflow in repository settings**

First query `gh api repos/MoeenNehzati/famulus/pages`. If it returns 404, create the site with `POST /repos/MoeenNehzati/famulus/pages` and `build_type=workflow`; otherwise update it with `PUT /repos/MoeenNehzati/famulus/pages` and the same build type. Re-query and require `build_type` to equal `workflow`.

---

### Task 2: Cross-platform schema and test contracts

**Files:**
- Modify: `src/officina/common/configured_schema.py`
- Modify: `tests/test_configured_schema.py`
- Modify: `tests/test_officina_launcher_entry.py`
- Modify: `tests/test_repository_validator_checks.py`
- Modify: `skills/recurring-tasks/_rtx/tests/test_schedule_backend.py`
- Modify: `skills/recurring-tasks/_rtx/tests/test_setup_runner.py`

**Interfaces:**
- Consumes: local `file:` URI strings and `allowed_schema_root` paths.
- Produces: `_decoded_file_uri_path(uri_path: str, *, platform: str) -> str`, whose Windows result removes the URI-only leading slash before a drive letter.

- [ ] **Step 1: Add the failing Windows file-URI decoding regression**

Add a unit test that calls the new helper with `/D:/a/famulus/common.schema.json` and `platform="win32"`, expecting `D:/a/famulus/common.schema.json`; add a POSIX case expecting the leading slash to remain.

```python
def test_file_uri_path_decoding_preserves_windows_drive_root() -> None:
    assert configured_schema_module._decoded_file_uri_path(
        "/D:/a/famulus/common.schema.json", platform="win32"
    ) == "D:/a/famulus/common.schema.json"
```

- [ ] **Step 2: Verify the new regression fails because the helper is absent**

Run: `python3 -m pytest -q tests/test_configured_schema.py -k file_uri_path_decoding`

Expected: FAIL with `AttributeError` for `_decoded_file_uri_path`.

- [ ] **Step 3: Implement platform-correct file URI decoding**

Add a helper that URL-decodes the path and removes exactly one leading slash only for a Windows drive-root form matching `^/[A-Za-z]:/`. Use it in `_local_reference_path` before constructing `Path`; leave relative references unchanged.

```python
def _decoded_file_uri_path(uri_path: str, *, platform: str = sys.platform) -> str:
    decoded = unquote(uri_path)
    if platform == "win32" and re.match(r"^/[A-Za-z]:/", decoded):
        return decoded[1:]
    return decoded
```

- [ ] **Step 4: Verify configured-schema coverage**

Run: `python3 -m pytest -q tests/test_configured_schema.py tests/test_blueprint_catalog_schema.py`

Expected: PASS.

- [ ] **Step 5: Correct platform assumptions in existing tests**

Make these bounded test changes:

- derive the launcher test's `runtime_root` from `resolve_famulus_paths(platform=sys.platform, home=home).runtime_root`;
- skip the arbitrary-byte filename test unless `sys.platform.startswith("linux")`;
- monkeypatch `setup_runner.sys.platform` to `"linux"` in the cron orchestration test;
- assert the generated Task Scheduler `/TR` value is at most 261 characters, while retaining the old inline-command overage assertion.

- [ ] **Step 6: Verify all corrected platform contract tests locally**

Run: `python3 -m pytest -q tests/test_officina_launcher_entry.py::test_deployed_stable_launcher_runs_an_installed_dispatcher_without_pythonpath tests/test_repository_validator_checks.py::test_staged_path_transport_preserves_non_utf8_filename_bytes skills/recurring-tasks/_rtx/tests/test_schedule_backend.py::test_windows_wrapper_tr_value_stays_well_under_261_char_limit skills/recurring-tasks/_rtx/tests/test_setup_runner.py::test_run_setup_uses_python_runtimes_and_scheduler_backend`

Expected: PASS on Linux, with only the explicitly non-Linux test skipped where applicable.

---

### Task 3: Native integration isolation, verification, and delivery

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/tests/install_test_utils.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_install_test_utils.py`
- Modify: `skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py`
- Modify: `docs/testing.md`

**Interfaces:**
- Consumes: a per-test temporary directory and subprocess environment.
- Produces: `configure_isolated_test_keyring(env: dict[str, str], tmp_root: Path) -> None`, selecting a file-backed backend located entirely below `tmp_root`; and scheduler-smoke evidence tied to a run record created after the Task Scheduler trigger.

- [ ] **Step 1: Add a failing isolated-keyring helper test**

Test that configuring an environment creates an importable backend module below `tmp_root`, sets `PYTHON_KEYRING_BACKEND`, sets the backend data path below `tmp_root`, and permits store/lookup/delete across two Python subprocesses.

- [ ] **Step 2: Verify the helper test fails because isolation is not configured**

Run: `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests/test_install_test_utils.py::test_python_test_env_provides_an_isolated_persistent_keyring`

Expected: FAIL because `configure_isolated_test_keyring` does not exist.

- [ ] **Step 3: Implement and apply the test-only backend**

Have `python_test_env` call `configure_isolated_test_keyring`. The generated backend subclasses `keyring.backend.KeyringBackend`, persists only a JSON mapping below the test temporary root, and implements `get_password`, `set_password`, and `delete_password`. Production `secret_store.py` remains unchanged.

- [ ] **Step 4: Verify the install acceptance tests**

Run: `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests/test_claude_install.py skills/install-assistant-tools/_rtx/tests/test_claude_github_install.py`

Expected: PASS or capability skips unrelated to keyring; no `NoKeyringError` or signing-material failure.

- [ ] **Step 5: Make the Windows scheduler smoke prove a new scheduled run**

After preflight, remove the preflight marker and job log directory. Record the trigger time, call `schtasks /Run`, then poll both the marker and `<log_dir>/<job>/latest.json`; accept success only when the run record's `started_at` is at or after the trigger and the marker exists. On timeout include `schtasks /Query`, wrapper text, `scheduler.log`, `run.log`, and any run record.

- [ ] **Step 6: Verify scheduler unit coverage locally**

Run: `python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py skills/recurring-tasks/_rtx/tests/test_schedule_backend.py skills/recurring-tasks/_rtx/tests/test_job_executor.py`

Expected: unit tests PASS; live smoke skips on unsupported local scheduler environments.

- [ ] **Step 7: Document and run repository verification**

Document `requirements-ci.txt` as the GitHub dependency lock in `docs/testing.md`. Run:

```text
python3 repo_checks.py --suite precommit
python3 repo_checks.py --suite full --verbose --sequential
git diff --check
```

Expected: both suites PASS locally, subject only to explicitly capability-gated native smokes.

- [ ] **Step 8: Commit, push, and monitor**

Stage only plan-owned files, commit without bypassing hooks, push `master`, and monitor both workflows for the exact pushed SHA. If hosted native diagnostics expose another root cause, add one failing regression and repeat the smallest TDD cycle before a follow-up commit.
