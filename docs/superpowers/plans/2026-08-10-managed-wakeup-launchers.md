# Managed Wakeup Launchers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install `llm-wakeup` and `lw` as public cross-platform managed-runtime launchers beside `dispatcher`.

**Architecture:** Extend the existing platform launcher bundles with one required wakeup capability. Linux/macOS and Windows generate two equivalent shims that invoke the stable resolver with `-m officina.wakeup.cli`; existing scaffold, PATH, manifest, and uninstall machinery owns their lifecycle.

**Tech Stack:** Python 3.11+, Windows batch files, pytest, Officina managed-runtime resolver

## Global Constraints

- Both public names must use the same stable managed-runtime resolver contract as `dispatcher`.
- Linux and macOS install extensionless executable `llm-wakeup` and `lw` shims.
- Windows installs `llm-wakeup.bat` and `lw.bat` shims.
- Generated shims must not embed a repository checkout, active release path, or release-specific interpreter.
- The wakeup bundle is a required scaffold capability.
- Native scheduler installation and wakeup policy configuration are out of scope.
- Preserve all pre-existing staged and unstaged changes, especially the installer manifest/uninstall test edits.
- Do not commit without explicit user authorization.

---

### Task 1: Platform Wakeup Launcher Bundle

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/tests/test_install_launcher.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_launcher/_base_launcher.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_launcher/_linux_launcher.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_launcher/_windows_launcher.py`

**Interfaces:**
- Consumes: the fixed resolver path returned by each platform module's `_resolver_path(home=...)`.
- Produces: `install_wakeup_launcher(bin_dir, dry_run, manifest=None, *, home=None) -> LauncherInstallResult` on Linux/macOS and Windows launcher installers.
- Produces: shared module-shim content helpers used by both dispatcher and wakeup launchers.

- [x] **Step 1: Write failing platform tests**

Add tests that call `install_wakeup_launcher` for Linux, macOS, and Windows. Assert the exact command filenames, resolver markers, `officina.wakeup.cli`, argument forwarding, executable bits on POSIX, and absence of repo/interpreter embedding. Example Linux assertions:

```python
wakeup = installer.install_wakeup_launcher(bin_dir, dry_run=False, home=home)
assert wakeup.status == "installed"
for command in ("llm-wakeup", "lw"):
    content = (bin_dir / command).read_text(encoding="utf-8")
    assert "'officina.wakeup.cli'" in content
    assert "os.execv(RESOLVER" in content
    assert (bin_dir / command).stat().st_mode & 0o111
```

Windows assertions use `llm-wakeup.bat` and `lw.bat`, require `-m officina.wakeup.cli %*`, and assert no extensionless counterparts exist.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m pytest -q \
  -o 'pythonpath=src skills/install-assistant-tools/_rtx skills/install-assistant-tools/_rtx/tests' \
  skills/install-assistant-tools/_rtx/tests/test_install_launcher.py
```

Expected: FAIL because `install_wakeup_launcher` does not exist.

- [x] **Step 3: Add the minimal shared launcher implementation**

Define wakeup workflow labels in `_base_launcher.py`. Refactor dispatcher content generation into a private module-launcher helper while preserving `_unix_dispatcher_content` and `_windows_dispatcher_content` as compatibility wrappers for existing tests.

Linux/macOS bundle shape:

```python
LauncherBundleSpec(
    name="llm-wakeup",
    workflows=WAKEUP_WORKFLOWS,
    files=[
        LauncherFileSpec(
            destination=bin_dir / command,
            mode="generate",
            content=_unix_module_content("officina.wakeup.cli", home=home),
            executable=True,
        )
        for command in ("llm-wakeup", "lw")
    ],
)
```

Windows uses the same command tuple with `.bat` destinations and a shared batch module helper. Its helper must reuse `_resolve_python_interpreter()` and `_resolver_path()` exactly as dispatcher does.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run the same focused pytest command. Expected: all launcher tests pass.

---

### Task 2: Scaffold and Lifecycle Integration

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/tests/test_scaffold.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- Verify without editing unless necessary: `skills/install-assistant-tools/_rtx/tests/test_uninstall.py`
- Verify without editing unless necessary: `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py`

**Interfaces:**
- Consumes: `install_wakeup_launcher(...)` from Task 1.
- Produces: an always-installed required `llm-wakeup` scaffold capability containing both public commands.

- [x] **Step 1: Write failing scaffold tests**

Extend Linux and Windows scaffold tests to assert both wakeup command files. Extend dry-run reporting to require `WOULD-INSTALL: llm-wakeup` and wakeup workflow text. Extend the dry-run no-write test to assert neither wakeup file exists.

- [x] **Step 2: Run the focused scaffold tests and verify RED**

Run:

```bash
python3 -m pytest -q \
  -o 'pythonpath=src skills/install-assistant-tools/_rtx skills/install-assistant-tools/_rtx/tests' \
  skills/install-assistant-tools/_rtx/tests/test_scaffold.py
```

Expected: FAIL because scaffold does not call the wakeup bundle installer.

- [x] **Step 3: Add the wakeup capability to scaffold**

Insert the wakeup result beside dispatcher and invoke-skill:

```python
capability_results = [
    launcher_installer.install_dispatcher_launcher(
        repo_root, bin_dir, dry_run, manifest, home=home
    ),
    launcher_installer.install_wakeup_launcher(
        bin_dir, dry_run, manifest, home=home
    ),
    launcher_installer.install_invoke_skill_launcher(
        bin_dir, dry_run, manifest
    ),
]
```

Update scaffold module prose and missing-release advisory to name the added commands.

- [x] **Step 4: Run scaffold tests and verify GREEN**

Run the focused scaffold tests. Expected: all pass.

- [x] **Step 5: Verify manifest-driven removal and lifecycle behavior**

Run:

```bash
python3 -m pytest -q \
  -o 'pythonpath=src skills/install-assistant-tools/_rtx skills/install-assistant-tools/_rtx/tests' \
  skills/install-assistant-tools/_rtx/tests/test_install_manifest.py \
  skills/install-assistant-tools/_rtx/tests/test_uninstall.py \
  skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py
```

Expected: all non-platform-skipped tests pass; the existing uninstall fixture leaves its managed bin directory empty after removing dispatcher, wakeup commands, and agent launchers.

---

### Task 3: User-Facing Installation Documentation

**Files:**
- Modify: `docs/officina/installation.md`
- Verify: `src/officina/wakeup/CLAUDE-CODEX-README.md`

**Interfaces:**
- Consumes: the completed scaffold behavior from Task 2.
- Produces: installation documentation that identifies `llm-wakeup` and `lw` as universal managed launchers.

- [x] **Step 1: Update the installation guide**

Add `llm-wakeup` and `lw` to the universal scaffold inventory. State that both use the active managed Officina release through the stable resolver and are installed on Linux, macOS, and Windows (`.bat` on Windows).

- [x] **Step 2: Check documentation consistency**

Confirm `CLAUDE-CODEX-README.md` still accurately states that the installer creates both commands. Run:

```bash
git diff --check -- docs/officina/installation.md src/officina/wakeup/CLAUDE-CODEX-README.md
```

Expected: exit 0.

---

### Task 4: Final Verification

**Files:**
- Verify all files changed by Tasks 1-3.

**Interfaces:**
- Consumes: completed platform, scaffold, lifecycle, and documentation changes.
- Produces: fresh evidence that the feature works and unrelated dirty files remain untouched.

- [x] **Step 1: Run the installer test slice**

```bash
python3 -m pytest -q \
  -o 'pythonpath=src skills/install-assistant-tools/_rtx skills/install-assistant-tools/_rtx/tests' \
  skills/install-assistant-tools/_rtx/tests/test_install_launcher.py \
  skills/install-assistant-tools/_rtx/tests/test_scaffold.py \
  skills/install-assistant-tools/_rtx/tests/test_install_manifest.py \
  skills/install-assistant-tools/_rtx/tests/test_uninstall.py \
  skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py
```

- [x] **Step 2: Run wakeup tests**

```bash
python3 -m pytest -q src/officina/wakeup/tests
```

- [x] **Step 3: Inspect the exact diff and whitespace**

```bash
git diff --check -- \
  skills/install-assistant-tools/_rtx/_install_launcher/_base_launcher.py \
  skills/install-assistant-tools/_rtx/_install_launcher/_linux_launcher.py \
  skills/install-assistant-tools/_rtx/_install_launcher/_windows_launcher.py \
  skills/install-assistant-tools/_rtx/_install_scaffold.py \
  skills/install-assistant-tools/_rtx/tests/test_install_launcher.py \
  skills/install-assistant-tools/_rtx/tests/test_scaffold.py \
  docs/officina/installation.md \
  docs/superpowers/specs/2026-08-10-managed-wakeup-launchers-design.md \
  docs/superpowers/plans/2026-08-10-managed-wakeup-launchers.md
```

Review `git diff` and `git status --short`; do not stage, commit, restore, or alter unrelated files.
