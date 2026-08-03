# macOS Installer Runtime (v5 Rebase) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the still-open macOS installation feedback items owned by `docs/plans/osx_feedback_fix/01-installer-runtime.md` (items 1, 2, 3, 4, 8, 18, 19) using a mechanism that fits the currently-adopted v5 nested-module architecture, instead of the frozen 2026-07-24 design that contradicts the real `references/blueprint/runtime_dependencies.json` (already at schema v1, not the v2 the old plan proposed) and predates the mandatory v5 blueprint shape.

**Architecture:** A new `officina.install` v5 module owns a versioned managed-runtime build, an atomic `current.json` pointer, and a stable launcher resolver. It consumes the existing v1 `runtime_dependencies.json` as-is (extracting, not replacing, the parsing logic already correct in `_install_scaffold.py`). A new `famulus_paths.py` source on the existing `officina.common` module supplies platform-correct, non-Documents install paths, consumed by `install-assistant-tools`/`_rtx` to fix the Documents-path bug and the optional-`assistant`-launcher bug directly.

**Tech Stack:** Python 3.11+, `uv`, pytest, the repo's `skill-maker` blueprint tooling, JSON Schema (blueprint schema v5), TOML.

**Source of truth for staleness decisions:** This plan supersedes `docs/plans/osx_feedback_fix/01-installer-runtime.md` for feedback items 1, 2, 3, 4, 8, 18, 19 only. It does not touch items owned by subplans 02–06 of that package. Before starting, re-confirm the exact `skill-maker` sync/validate command and `skill-certifier` invocation from `skills/skill-maker/SKILL.md` and `docs/certification_and_drift.md` — this plan describes their purpose but was drafted without pinning their exact CLI surface.

---

## Task 0: Lock the dependency-manifest contract with a regression guard

**Files:**
- Modify: `skills/install-assistant-tools/tests/test_scaffold.py`

- [ ] **Step 1: Write the guard-rail test**

Add a test that fails loudly if a future change reintroduces a `by_platform`/schema-v2 shape instead of the real, live v1 manifest:

```python
import json
from pathlib import Path

def test_runtime_dependencies_manifest_is_still_schema_v1():
    manifest_path = Path(__file__).resolve().parents[3] / "references" / "blueprint" / "runtime_dependencies.json"
    payload = json.loads(manifest_path.read_text())
    assert payload["version"] == 1
    assert "skills" in payload
    # Spot check a known live entry keeps the documented shape.
    entry = payload["skills"]["install-assistant-tools"]["interfaces"]["scripts-install"]
    dep = entry["dependencies"][0]
    assert set(dep) >= {"kind", "name", "platforms"}
    assert set(dep["platforms"]) <= {"linux", "macos", "windows"}
```

Adjust the exact `skills`/`interfaces` key names to match whatever `references/blueprint/runtime_dependencies.json` actually contains for `install-assistant-tools` at implementation time — read the file first and use a real entry, don't guess the key.

- [ ] **Step 2: Run test to verify it passes against current state**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_scaffold.py -k manifest_is_still_schema_v1 -v`
Expected: PASS (this is a regression guard on already-true state, not a new feature).

- [ ] **Step 3: Commit**

```bash
git add skills/install-assistant-tools/tests/test_scaffold.py
git commit -m "test: guard runtime_dependencies.json against v2 schema drift"
```

---

## Task 1: `FamulusPaths` as a new source on the existing `officina.common` module

**Files:**
- Create: `src/officina/common/famulus_paths.py`
- Create: `src/officina/common/blueprints/famulus-paths.yaml`
- Modify: `src/officina/common/blueprint.yaml` (via `skill-maker`, not by hand)
- Test: `tests/test_officina_famulus_paths.py`

- [ ] **Step 1: Write the failing tests**

```python
import os
from pathlib import Path

import pytest

from officina.common.famulus_paths import resolve_famulus_paths


def test_macos_paths_avoid_documents(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    paths = resolve_famulus_paths(platform="darwin", home=tmp_path)
    assert "Documents" not in str(paths.user_bin)
    assert "Documents" not in str(paths.data_root)


def test_linux_paths_avoid_documents(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    paths = resolve_famulus_paths(platform="linux", home=tmp_path)
    assert "Documents" not in str(paths.user_bin)


def test_windows_requires_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError):
        resolve_famulus_paths(platform="win32", home=tmp_path)


def test_xdg_data_home_override_is_honored(monkeypatch, tmp_path):
    override = tmp_path / "custom-xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(override))
    paths = resolve_famulus_paths(platform="linux", home=tmp_path)
    assert str(paths.data_root).startswith(str(override))


def test_relative_home_is_rejected():
    with pytest.raises(ValueError):
        resolve_famulus_paths(platform="linux", home=Path("relative/home"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q tests/test_officina_famulus_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'officina.common.famulus_paths'`

- [ ] **Step 3: Implement `famulus_paths.py`**

```python
"""Platform-correct, non-Documents Famulus install and state path resolution."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FamulusPaths:
    data_root: Path
    config_root: Path
    state_root: Path
    runtime_root: Path
    releases_root: Path
    current_pointer: Path
    install_state_root: Path
    uv_bin: Path
    python_install_dir: Path
    worker_root: Path
    launcher_profile_root: Path
    recurring_config_root: Path
    recurring_state_root: Path
    email_triage_state_root: Path
    user_bin: Path


def resolve_famulus_paths(*, platform: str, home: Path) -> FamulusPaths:
    if not home.is_absolute():
        raise ValueError(f"home must be an absolute path, got {home!r}")

    if platform == "darwin":
        data_root = home / "Library" / "Application Support" / "Famulus"
        config_root = home / "Library" / "Application Support" / "Famulus" / "config"
        state_root = home / "Library" / "Application Support" / "Famulus" / "state"
        user_bin = home / ".local" / "bin"
    elif platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is required to resolve Famulus paths on Windows")
        base = Path(local_app_data) / "Famulus"
        data_root = base
        config_root = base / "config"
        state_root = base / "state"
        user_bin = base / "bin"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        data_root = Path(xdg_data) / "famulus" if xdg_data else home / ".local" / "share" / "famulus"
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        config_root = Path(xdg_config) / "famulus" if xdg_config else home / ".config" / "famulus"
        xdg_state = os.environ.get("XDG_STATE_HOME")
        state_root = Path(xdg_state) / "famulus" if xdg_state else home / ".local" / "state" / "famulus"
        user_bin = home / ".local" / "bin"

    runtime_root = data_root / "runtime"
    return FamulusPaths(
        data_root=data_root,
        config_root=config_root,
        state_root=state_root,
        runtime_root=runtime_root,
        releases_root=runtime_root / "releases",
        current_pointer=runtime_root / "current.json",
        install_state_root=state_root / "install",
        uv_bin=data_root / "tools" / "uv",
        python_install_dir=data_root / "python",
        worker_root=state_root / "workers",
        launcher_profile_root=data_root / "launcher-profiles",
        recurring_config_root=config_root / "recurring-tasks",
        recurring_state_root=state_root / "recurring-tasks",
        email_triage_state_root=state_root / "email-triage",
        user_bin=user_bin,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q tests/test_officina_famulus_paths.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Register the source on the `officina.common` blueprint through `skill-maker`**

Use `skill-maker` (per `skills/skill-maker/SKILL.md`) to:
1. Add `famulus_paths\.py` to `src/officina/common/blueprint.yaml`'s `content:` list.
2. Add a `common.source.famulus-paths` entry under `sources:` pointing at `blueprints/famulus-paths.yaml`.
3. Create `src/officina/common/blueprints/famulus-paths.yaml` as a `behavioral_source`, `schema_version: 5`, with one interface (e.g. `python-api`) whose contract documents `resolve_famulus_paths(platform, home) -> FamulusPaths`, `direct_io` declaring env reads (`XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `LOCALAPPDATA`) and no filesystem writes.
4. Export `common.interface.famulus-paths` with `access.allow_all_modules: false` and `allowed_callers` limited to `install-assistant-tools-rtx` (extend the allowlist in later tasks as other modules need it — do not grant broad access up front).

Do **not** hand-edit `src/officina/common/blueprint.yaml` directly — regenerate it through the `skill-maker` workflow so the generated contract blocks stay byte-identical to what the tooling produces.

- [ ] **Step 6: Validate the blueprint**

Run the repo's blueprint validation entrypoint (confirm exact command from `skills/skill-maker/SKILL.md`, e.g. `skill-maker validate` or `python3 -m validators.runner` — pin the real command before running) against `src/officina/common/`.
Expected: no schema errors, no orphaned exports.

- [ ] **Step 7: Commit**

```bash
git add src/officina/common/famulus_paths.py src/officina/common/blueprints/famulus-paths.yaml src/officina/common/blueprint.yaml tests/test_officina_famulus_paths.py
git commit -m "feat(officina.common): add FamulusPaths source, avoid Documents on all platforms"
```

---

## Task 2: Documents-path fix in `install-assistant-tools` (feedback items 4, 8)

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py:269,326`
- Modify: `skills/install-assistant-tools/_rtx/_agent_launchers.py:80,251`
- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py:468`
- Modify (via `skill-maker`): `skills/install-assistant-tools/_rtx/blueprints/rtx-install-scaffold.yaml`, `blueprints/rtx-agent-launchers.yaml`
- Test: `skills/install-assistant-tools/tests/test_scaffold.py`, `test_launchers.py`, `test_uninstall.py`

- [ ] **Step 1: Write failing tests**

In each of the three test files, add (adjust fixture/harness names to match what's already used in that file — read the file first):

```python
def test_default_bin_dir_is_not_under_documents(tmp_home):
    from officina.common.famulus_paths import resolve_famulus_paths
    expected = resolve_famulus_paths(platform=sys.platform, home=tmp_home).user_bin
    result = default_bin_dir(home=tmp_home)  # or the real call path in that module
    assert result == expected
    assert "Documents" not in str(result)
```

```python
def test_worker_root_in_plugin_mode_is_not_under_repo_workers(tmp_home):
    from officina.common.famulus_paths import resolve_famulus_paths
    expected = resolve_famulus_paths(platform=sys.platform, home=tmp_home).worker_root
    result = install_worker_dir(mode="plugin", home=tmp_home)  # match real signature
    assert result == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_scaffold.py skills/install-assistant-tools/tests/test_launchers.py skills/install-assistant-tools/tests/test_uninstall.py -k "not_under_documents or not_under_repo_workers" -v`
Expected: FAIL (current default still resolves to `home / "Documents" / "_rtx" / "bin"`)

- [ ] **Step 3: Replace the Documents default with `FamulusPaths`**

In `_install_scaffold.py`, `_agent_launchers.py`, `_install_uninstall.py`: replace every
```python
bin_dir = bin_dir or (home / "Documents" / "_rtx" / "bin")
```
with
```python
bin_dir = bin_dir or resolve_famulus_paths(platform=sys.platform, home=home).user_bin
```
and update any help/usage text (`_install_scaffold.py:326`) that documents the old default. In `_agent_launchers.py`, change `install_worker_dir` so plugin-mode installs use `resolve_famulus_paths(...).worker_root` and development-mode installs keep `repo_root / "workers"` (explicit live checkout, not a public/immutable-tree conflict).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_scaffold.py skills/install-assistant-tools/tests/test_launchers.py skills/install-assistant-tools/tests/test_uninstall.py -v`
Expected: PASS, full files, no regressions.

- [ ] **Step 5: Update blueprint contracts through `skill-maker`**

Update `direct_io`/usage descriptions in `blueprints/rtx-install-scaffold.yaml` and `blueprints/rtx-agent-launchers.yaml` so the documented default path matches the new behavior. Re-sync generated `SKILL.md` contract blocks.

- [ ] **Step 6: Commit**

```bash
git add skills/install-assistant-tools/_rtx/_install_scaffold.py skills/install-assistant-tools/_rtx/_agent_launchers.py skills/install-assistant-tools/_rtx/_install_uninstall.py skills/install-assistant-tools/_rtx/blueprints/rtx-install-scaffold.yaml skills/install-assistant-tools/_rtx/blueprints/rtx-agent-launchers.yaml skills/install-assistant-tools/tests/test_scaffold.py skills/install-assistant-tools/tests/test_launchers.py skills/install-assistant-tools/tests/test_uninstall.py
git commit -m "fix(install-assistant-tools): stop defaulting public bin/worker paths under Documents"
```

---

## Task 3: Guarantee `assistant` in the launcher closure (feedback item 18)

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_agent_launchers.py`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify (via `skill-maker`): `skills/install-assistant-tools/_rtx/blueprints/rtx-agent-launchers.yaml`
- Test: `skills/install-assistant-tools/tests/test_launchers.py`

- [ ] **Step 1: Write failing tests**

```python
def test_launcher_closure_always_includes_assistant():
    assert launcher_closure((), install_invoke_skill=True) == ("assistant",)


def test_launcher_closure_puts_assistant_first_no_duplicate():
    assert launcher_closure(("collab", "assistant"), install_invoke_skill=True) == ("assistant", "collab")


def test_install_with_no_agents_still_creates_assistant_launcher(tmp_bin_dir):
    run(agents=[], bin_dir=tmp_bin_dir, install_invoke_skill=True)
    assert (tmp_bin_dir / "assistant").exists() or (tmp_bin_dir / "assistant.bat").exists()
```

(Match `run`'s real parameter names/signature — read `_agent_launchers.py` before writing this step.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_launchers.py -k assistant -v`
Expected: FAIL — `launcher_closure` doesn't exist yet; current code only installs `assistant` when explicitly requested.

- [ ] **Step 3: Implement `launcher_closure` and wire it in**

```python
def launcher_closure(selected_agents: Sequence[str], *, install_invoke_skill: bool) -> tuple[str, ...]:
    agents = list(dict.fromkeys(selected_agents))  # de-dupe, preserve order
    if install_invoke_skill and "assistant" not in agents:
        agents.insert(0, "assistant")
    elif install_invoke_skill:
        agents.remove("assistant")
        agents.insert(0, "assistant")
    return tuple(agents)
```

Update `run()` in `_agent_launchers.py` to iterate `launcher_closure(agents, install_invoke_skill=install_invoke_skill)` instead of raw `agents` for the bin/worker/profile install loop. Update `_phase_entry.py`'s call site to pass `install_invoke_skill=True` unconditionally (not user-selectable — it is an `invoke-skill` implementation prerequisite per feedback item 18).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_launchers.py skills/install-assistant-tools/tests/test_agent_launch.py -v`
Expected: PASS

- [ ] **Step 5: Update blueprint contract**

Through `skill-maker`, update `blueprints/rtx-agent-launchers.yaml`'s `arguments.agents` description to state that `assistant` is always installed as an `invoke-skill` prerequisite regardless of the `--agents` selection.

- [ ] **Step 6: Commit**

```bash
git add skills/install-assistant-tools/_rtx/_agent_launchers.py skills/install-assistant-tools/_rtx/_phase_entry.py skills/install-assistant-tools/_rtx/blueprints/rtx-agent-launchers.yaml skills/install-assistant-tools/tests/test_launchers.py
git commit -m "fix(install-assistant-tools): always install assistant launcher for invoke-skill"
```

---

## Task 4: `officina.install` module scaffold — `install-info.toml` + `RuntimePointer`

**Files:**
- Create: `install-info.toml`
- Create: `src/officina/install/__init__.py`, `install_info.py`, `runtime_pointer.py`
- Create: `src/officina/install/blueprint.yaml`, `blueprints/install-info.yaml`, `blueprints/runtime-pointer.yaml`
- Test: `tests/test_officina_install_info.py`, `tests/test_officina_runtime_pointer.py`

- [ ] **Step 1: Write failing tests for `install-info.toml` parsing**

```python
import pytest
from officina.install.install_info import load_install_info, InstallInfoError

def test_load_install_info_parses_pinned_versions():
    info = load_install_info(Path("install-info.toml"))
    assert info.schema_version == 1
    assert info.uv_version == "0.11.29"
    assert info.managed_python == "3.11"

def test_load_install_info_rejects_unknown_schema_version(tmp_path):
    bad = tmp_path / "install-info.toml"
    bad.write_text("schema_version = 99\n")
    with pytest.raises(InstallInfoError):
        load_install_info(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q tests/test_officina_install_info.py -v`
Expected: FAIL — module and `install-info.toml` don't exist yet.

- [ ] **Step 3: Create `install-info.toml` and `install_info.py`**

```toml
schema_version = 1

[bootstrap]
uv_version = "0.11.29"

[managed_python]
preferred = "3.11"
supported = ">=3.11,<3.12"
```

```python
"""Parse and validate the pinned bootstrap/runtime versions in install-info.toml."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tomllib


class InstallInfoError(Exception):
    pass


@dataclass(frozen=True)
class InstallInfo:
    schema_version: int
    uv_version: str
    managed_python: str
    managed_python_supported: str


def load_install_info(path: Path) -> InstallInfo:
    data = tomllib.loads(path.read_text())
    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise InstallInfoError(f"unsupported install-info.toml schema_version: {schema_version!r}")
    try:
        return InstallInfo(
            schema_version=schema_version,
            uv_version=data["bootstrap"]["uv_version"],
            managed_python=data["managed_python"]["preferred"],
            managed_python_supported=data["managed_python"]["supported"],
        )
    except KeyError as exc:
        raise InstallInfoError(f"install-info.toml missing required key: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q tests/test_officina_install_info.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `RuntimePointer`**

```python
import json
from pathlib import Path

import pytest

from officina.install.runtime_pointer import (
    RuntimePointer,
    RuntimePointerError,
    activate_release,
    load_current_pointer,
)


def test_activate_release_writes_pointer_atomically(tmp_path):
    runtime_root = tmp_path / "runtime"
    releases_root = runtime_root / "releases"
    release_dir = releases_root / "2026-07-27T00-00-00Z-abc123"
    release_dir.mkdir(parents=True)
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"
    python_bin.write_text("#!/bin/sh\n")

    activate_release(runtime_root=runtime_root, release_dir=release_dir, python_bin=python_bin)

    pointer = load_current_pointer(runtime_root=runtime_root)
    assert pointer.release_id == "2026-07-27T00-00-00Z-abc123"
    assert pointer.python_bin == python_bin


def test_load_current_pointer_rejects_path_outside_runtime_root(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    outside = tmp_path / "outside" / "python"
    (runtime_root / "current.json").write_text(json.dumps({
        "schema_version": 1,
        "release_id": "x",
        "runtime_source": str(outside.parent),
        "python_bin": str(outside),
    }))
    with pytest.raises(RuntimePointerError):
        load_current_pointer(runtime_root=runtime_root)


def test_failed_activation_leaves_prior_pointer_untouched(tmp_path):
    runtime_root = tmp_path / "runtime"
    releases_root = runtime_root / "releases"
    good = releases_root / "good-release"
    (good / "venv" / "bin").mkdir(parents=True)
    (good / "venv" / "bin" / "python").write_text("#!/bin/sh\n")
    activate_release(runtime_root=runtime_root, release_dir=good, python_bin=good / "venv" / "bin" / "python")

    missing_python = releases_root / "bad-release" / "venv" / "bin" / "python"
    with pytest.raises(RuntimePointerError):
        activate_release(runtime_root=runtime_root, release_dir=missing_python.parents[1], python_bin=missing_python)

    pointer = load_current_pointer(runtime_root=runtime_root)
    assert pointer.release_id == "good-release"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python3 -m pytest -q tests/test_officina_runtime_pointer.py -v`
Expected: FAIL — module doesn't exist yet.

- [ ] **Step 7: Implement `runtime_pointer.py`**

```python
"""Atomic current.json pointer for the managed Famulus runtime."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class RuntimePointerError(Exception):
    pass


@dataclass(frozen=True)
class RuntimePointer:
    release_id: str
    runtime_source: Path
    python_bin: Path


def _pointer_path(runtime_root: Path) -> Path:
    return runtime_root / "current.json"


def load_current_pointer(*, runtime_root: Path) -> RuntimePointer:
    path = _pointer_path(runtime_root)
    if not path.exists():
        raise RuntimePointerError(f"no current.json at {path}")
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        raise RuntimePointerError(f"unsupported current.json schema_version: {payload.get('schema_version')!r}")
    python_bin = Path(payload["python_bin"])
    runtime_source = Path(payload["runtime_source"])
    if not python_bin.is_absolute() or not runtime_source.is_absolute():
        raise RuntimePointerError("current.json paths must be absolute")
    try:
        python_bin.relative_to(runtime_root)
    except ValueError as exc:
        raise RuntimePointerError("python_bin must live under runtime_root") from exc
    return RuntimePointer(release_id=payload["release_id"], runtime_source=runtime_source, python_bin=python_bin)


def activate_release(*, runtime_root: Path, release_dir: Path, python_bin: Path) -> RuntimePointer:
    if not python_bin.exists():
        raise RuntimePointerError(f"candidate python_bin does not exist: {python_bin}")
    runtime_root.mkdir(parents=True, exist_ok=True)
    pointer = RuntimePointer(release_id=release_dir.name, runtime_source=release_dir, python_bin=python_bin)
    payload = {
        "schema_version": 1,
        "release_id": pointer.release_id,
        "runtime_source": str(pointer.runtime_source),
        "python_bin": str(pointer.python_bin),
    }
    tmp_path = _pointer_path(runtime_root).with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, _pointer_path(runtime_root))
    return pointer
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest -q tests/test_officina_runtime_pointer.py -v`
Expected: PASS

- [ ] **Step 9: Scaffold the `officina.install` v5 module blueprint through `skill-maker`**

Create `src/officina/install/blueprint.yaml` (`schema_version: 5`, `node_type: module`, `authority.owns_filesystem` declaring `<famulus-data-root>/runtime` as `readwrite`), `blueprints/install-info.yaml` and `blueprints/runtime-pointer.yaml` as behavioral sources with `python-api` interfaces matching the functions above, and export `officina-install.interface.runtime-pointer` / `officina-install.interface.install-info` with `allowed_callers: [install-assistant-tools-rtx]`. Follow the exact structure already used by `src/officina/common/blueprint.yaml` as the template — don't invent new blueprint conventions.

- [ ] **Step 10: Validate blueprint**

Run the repo's blueprint validation entrypoint against `src/officina/install/`.
Expected: no schema errors.

- [ ] **Step 11: Commit**

```bash
git add install-info.toml src/officina/install/ tests/test_officina_install_info.py tests/test_officina_runtime_pointer.py
git commit -m "feat(officina.install): add install-info.toml parsing and atomic current.json pointer"
```

---

## Task 5: `managed_runtime.py` — one atomic dependency-install batch from the real v1 manifest (feedback items 2, 3)

**Files:**
- Create: `src/officina/install/managed_runtime.py`, `blueprints/managed-runtime.yaml`
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py` (delete per-package loop, import shared helper)
- Test: `tests/test_officina_managed_runtime.py`

- [ ] **Step 1: Capture today's `required_python_packages` output as a regression fixture**

Before deleting anything, run the current function against the real manifest and record its output, so the replacement can be diffed against it:

Run: `python3 -c "from _rtx._install_scaffold import required_python_packages; print(required_python_packages())"` (adjust import path to however the module is actually invoked) and save the printed list into the new test as the expected baseline.

- [ ] **Step 2: Write failing tests**

```python
import json
from pathlib import Path

import pytest

from officina.install.managed_runtime import declared_python_packages, build_candidate_release, ManagedRuntimeError


REAL_MANIFEST = Path("references/blueprint/runtime_dependencies.json")


def test_declared_python_packages_matches_today_baseline():
    packages = declared_python_packages(REAL_MANIFEST, platform="linux")
    # Replace with the exact tuple captured in Step 1.
    assert packages == (...)


def test_declared_python_packages_filters_by_platform(tmp_path):
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "skills": {
            "example": {
                "interfaces": {
                    "run": {
                        "dependencies": [
                            {"kind": "python", "name": "pywin32", "version": "1.0", "platforms": {"windows": True}},
                            {"kind": "python", "name": "pyyaml", "version": "6.0", "platforms": {"linux": True, "macos": True, "windows": True}},
                        ]
                    }
                }
            }
        },
    }))
    assert declared_python_packages(manifest, platform="linux") == ("pyyaml==6.0",)


def test_build_candidate_release_runs_one_batch_install(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a) or FakeCompletedProcess())
    build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        platform="linux",
        uv_bin=Path("/fake/uv"),
    )
    assert len(calls) == 1  # exactly one batch pip/uv install call


def test_build_candidate_release_failure_writes_no_pointer(monkeypatch, tmp_path):
    def fail(*a, **k):
        raise ManagedRuntimeError("simulated failure")
    monkeypatch.setattr("officina.install.managed_runtime._run_dependency_install", fail)
    runtime_root = tmp_path / "runtime"
    with pytest.raises(ManagedRuntimeError):
        build_candidate_release(runtime_root=runtime_root, manifest_path=REAL_MANIFEST, platform="linux", uv_bin=Path("/fake/uv"))
    assert not (runtime_root / "current.json").exists()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest -q tests/test_officina_managed_runtime.py -v`
Expected: FAIL — module doesn't exist yet.

- [ ] **Step 4: Implement `managed_runtime.py`**

```python
"""Build a versioned managed-runtime candidate release from the real v1
runtime_dependencies.json manifest, installing all Python dependencies in one
atomic batch instead of best-effort per-package."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class ManagedRuntimeError(Exception):
    pass


def declared_python_packages(manifest_path: Path, *, platform: str) -> tuple[str, ...]:
    payload = json.loads(manifest_path.read_text())
    if payload.get("version") != 1:
        raise ManagedRuntimeError(f"unsupported runtime_dependencies.json version: {payload.get('version')!r}")
    seen: dict[str, str] = {}
    for skill in payload.get("skills", {}).values():
        for interface in skill.get("interfaces", {}).values():
            for dep in interface.get("dependencies", []):
                if dep.get("kind") != "python":
                    continue
                if not dep.get("platforms", {}).get(platform):
                    continue
                name = dep["name"]
                key = name.casefold()
                seen.setdefault(key, f"{name}=={dep['version']}" if dep.get("version") else name)
    return tuple(sorted(seen.values(), key=str.casefold))


def _run_dependency_install(*, uv_bin: Path, python_bin: Path, packages: tuple[str, ...]) -> None:
    if not packages:
        return
    result = subprocess.run(
        [str(uv_bin), "pip", "install", "--python", str(python_bin), *packages],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(f"dependency install failed: {result.stderr}")


def build_candidate_release(*, runtime_root: Path, manifest_path: Path, platform: str, uv_bin: Path) -> Path:
    from officina.install.runtime_pointer import activate_release  # local import avoids cycle at module load

    packages = declared_python_packages(manifest_path, platform=platform)
    release_id = _new_release_id()
    release_dir = runtime_root / "releases" / release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    python_bin = release_dir / "venv" / "bin" / "python"

    _run_dependency_install(uv_bin=uv_bin, python_bin=python_bin, packages=packages)

    activate_release(runtime_root=runtime_root, release_dir=release_dir, python_bin=python_bin)
    return release_dir


def _new_release_id() -> str:
    import secrets
    import time
    timestamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    return f"{timestamp}-{secrets.token_hex(3)}"
```

Delete `install_python_packages`'s per-package `subprocess.run([sys.executable, "-m", "pip", "install", ...])` loop and `required_python_packages` in `_install_scaffold.py`; replace call sites with `officina.install.managed_runtime.declared_python_packages`/`build_candidate_release`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest -q tests/test_officina_managed_runtime.py skills/install-assistant-tools/tests/test_scaffold.py -v`
Expected: PASS; old per-package tests in `test_scaffold.py` that asserted WARN-and-continue behavior are removed/replaced, not left dangling.

- [ ] **Step 6: Blueprint updates through `skill-maker`**

Create `blueprints/managed-runtime.yaml` under `officina.install`, export `officina-install.interface.build-candidate-release` with `allowed_callers: [install-assistant-tools-rtx]`. Update `blueprints/rtx-install-scaffold.yaml` to remove the now-deleted per-package dependency-install contract and reference the new cross-module call via `uses_interfaces`.

- [ ] **Step 7: Commit**

```bash
git add src/officina/install/managed_runtime.py src/officina/install/blueprints/managed-runtime.yaml skills/install-assistant-tools/_rtx/_install_scaffold.py skills/install-assistant-tools/_rtx/blueprints/rtx-install-scaffold.yaml tests/test_officina_managed_runtime.py skills/install-assistant-tools/tests/test_scaffold.py
git commit -m "feat(officina.install): atomic one-batch dependency install from the real v1 manifest"
```

---

## Task 6: Stable-resolver launcher generation (feedback item 19)

> **Design correction (2026-07-28):** The first attempt at this task used a
> resolver script that imports `officina.install.runtime_pointer` at module
> level. That import runs under whatever Python the shim's `#!/usr/bin/env
> python3` shebang invokes — i.e. the user's ambient system Python — which is
> exactly the invocation this program forbids elsewhere ("Product bootstrap
> never invokes or mutates the user's ambient python or python3"). A resolver
> that needs `officina` importable before it can even find the managed
> interpreter defeats its own purpose. The corrected design below makes the
> resolver a **dependency-free, stdlib-only script** that never imports
> `officina`. It duplicates a minimal, read-only version of `runtime_pointer`'s
> containment check inline — `officina.install.runtime_pointer` remains the
> sole source of truth for *writing* `current.json` (at release-activation
> time, with the full adversarially-verified check); the resolver only *reads*
> it, using a deliberately narrow reimplementation. Task 6c below adds a
> cross-check test that fails loudly if the two implementations' behavior
> ever diverges on a shared table of test vectors, so the duplication doesn't
> silently rot.

**Files:**
- Create: `src/officina/install/launcher_entry.py` (a thin Python wrapper around the real resolver, used only for the shared test-vector cross-check — see Task 6c), `resolvers/launch.py` (the actual dependency-free resolver script deployed into every release, plain stdlib only), `blueprints/launcher-entry.yaml`
- Modify: `skills/install-assistant-tools/_rtx/_install_launcher/_linux_launcher.py`, `_windows_launcher.py`
- Test: `skills/install-assistant-tools/tests/test_install_launcher.py`, `tests/test_officina_launcher_entry.py`

- [ ] **Step 1: Write failing tests**

```python
def test_generated_dispatcher_does_not_embed_repo_root_or_sys_executable(tmp_repo_root):
    content = _unix_dispatcher_content(repo_root=tmp_repo_root)
    assert str(tmp_repo_root) not in content
    assert sys.executable not in content
    assert "resolvers/v1/launch.py" in content


def test_generated_launcher_content_has_no_legacy_vendor_paths(tmp_repo_root):
    content = _unix_dispatcher_content(repo_root=tmp_repo_root)
    for legacy_marker in ("openai-bundled", "release-2026-07"):
        assert legacy_marker not in content
```

(Match real function names/signatures in `_linux_launcher.py` — read the file first.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_install_launcher.py -v`
Expected: FAIL — current generated content embeds `repr(str(repo_root))` and `sys.executable`.

- [ ] **Step 3: Implement the dependency-free resolver script**

`src/officina/install/resolvers/launch.py` — this file is what actually gets deployed into `<runtime_root>/bootstrap/resolvers/v1/launch.py` (Task 7 wires the deployment); it must import nothing beyond the Python standard library, since it runs under ambient Python before any interpreter handoff:

```python
#!/usr/bin/env python3
"""Dependency-free launcher resolver: reads current.json and execs into the
active managed-runtime release's interpreter. This file MUST NOT import
officina or any third-party package — it runs under the user's ambient
Python, before control transfers to the managed interpreter. It duplicates a
minimal, read-only containment check from officina.install.runtime_pointer;
that module remains the sole source of truth for validating and WRITING
current.json. See tests/test_officina_launcher_entry.py for the cross-check
that keeps this copy honest against the real implementation."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


class ResolverError(Exception):
    pass


def _require_contained_or_trusted(path: Path, *, root: Path, trusted_roots: tuple[Path, ...], label: str) -> Path:
    if not path.is_absolute():
        raise ResolverError(f"{label} must be an absolute path: {path}")
    resolved_root = root.resolve()
    try:
        path.parent.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise ResolverError(f"{label} must live under runtime_root: {path}") from exc
    resolved_leaf = path.resolve()
    allowed_roots = (resolved_root, *(r.resolve() for r in trusted_roots))
    if not any(_is_relative_to(resolved_leaf, allowed) for allowed in allowed_roots):
        raise ResolverError(f"{label} resolves outside runtime_root and all trusted roots: {path} -> {resolved_leaf}")
    return resolved_leaf


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _load_current_pointer(runtime_root: Path, *, trusted_roots: tuple[Path, ...]) -> Path:
    pointer_path = runtime_root / "current.json"
    if not pointer_path.exists():
        raise ResolverError(f"no current.json at {pointer_path}")
    payload = json.loads(pointer_path.read_text())
    if payload.get("schema_version") != 1:
        raise ResolverError(f"unsupported current.json schema_version: {payload.get('schema_version')!r}")
    python_bin = Path(payload["python_bin"])
    runtime_source = Path(payload["runtime_source"])
    _require_contained_or_trusted(runtime_source, root=runtime_root, trusted_roots=(), label="runtime_source")
    return _require_contained_or_trusted(python_bin, root=runtime_root, trusted_roots=trusted_roots, label="python_bin")


def _trusted_interpreter_roots() -> tuple[Path, ...]:
    # Deployment writes this resolver alongside a sibling data file
    # (`trusted-roots.json`, a flat JSON list of absolute path strings)
    # populated at release-activation time from the same
    # managed_runtime._uv_python_install_dir() derivation officina.install
    # uses — this resolver reads that file rather than re-deriving trust
    # itself, since it must not shell out to `uv` or import anything.
    trust_file = Path(__file__).resolve().parent / "trusted-roots.json"
    if not trust_file.exists():
        return ()
    return tuple(Path(p) for p in json.loads(trust_file.read_text()))


def main(argv: list[str]) -> int:
    runtime_root = Path(argv[0]).resolve().parents[3]  # bootstrap/resolvers/v1/launch.py -> runtime_root
    try:
        python_bin = _load_current_pointer(runtime_root, trusted_roots=_trusted_interpreter_roots())
    except ResolverError as exc:
        print(f"famulus launcher: {exc}", file=sys.stderr)
        return 1
    os.execv(str(python_bin), [str(python_bin), *argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

Update `_unix_dispatcher_content`/`_unix_invoke_skill_content` in `_linux_launcher.py` (and the Windows equivalent) to generate shims that invoke `<runtime_root>/bootstrap/resolvers/v1/launch.py <original-args>` directly (the shim itself may still need a `#!/usr/bin/env python3`-equivalent shebang or `exec` line, but it must embed only this FIXED resolver path — never `repo_root` or `sys.executable`). The resolver script above is what actually runs under that shebang; it has no imports beyond stdlib, so no ambient-`officina`-importability problem exists.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_install_launcher.py skills/install-assistant-tools/tests/test_launchers.py -v`
Expected: PASS

- [ ] **Step 5: Re-check `_osx_launcher.py`'s disposition**

Read `_osx_launcher.py` again now that `_linux_launcher.py`'s content-generation no longer embeds platform-specific literal paths. If `OSXLauncherInstaller(LinuxLauncherInstaller)` with zero overrides is now correct (both are POSIX shells calling the same resolver), leave it as-is and add a one-line comment explaining why the empty subclass is intentional, not a stub. If `launchctl`/plist-specific behavior is still needed, that belongs to subplan 06 (macOS acceptance), not this task — do not scope-creep into LaunchAgent work here.

- [ ] **Step 6: Blueprint updates**

Create `blueprints/launcher-entry.yaml` under `officina.install` describing the resolver as a deployable artifact (not a Python-importable interface — it is deliberately import-free). Update `blueprints/rtx-install-launcher.yaml` (or equivalent) to reference the resolver path contract and note the `trusted-roots.json` sidecar file Task 7's deployment step must write alongside it.

- [ ] **Step 6c: Cross-check test — keep the duplicated containment logic honest**

**Test:** `tests/test_officina_launcher_entry.py`

Import BOTH `officina.install.runtime_pointer._require_contained_or_trusted` (the real, adversarially-verified implementation) and `officina.install.resolvers.launch._require_contained_or_trusted` (the stdlib-only duplicate — the resolver script itself is not normally imported as a module, but for this test only, load it via `importlib.util.spec_from_file_location` so the test can call its internals directly without giving production code any reason to import it as a package). Build a shared table of test vectors covering every case the earlier security review exercised: contained real path (accept), contained-but-symlink-to-trusted-root (accept), symlink-to-untrusted-target (reject), symlink chain (reject if ultimately untrusted), dangling symlink (reject), parent-directory symlink escape (reject), non-absolute path (reject). Run every vector through BOTH implementations and assert they agree on accept/reject for every case. This test is the safeguard against the two copies silently drifting apart; if it ever fails, that's a signal the resolver's copy needs to be updated to match a change in the real implementation (or vice versa — investigate which one is right before "fixing" the test).

- [ ] **Step 7: Commit**

```bash
git add src/officina/install/launcher_entry.py src/officina/install/resolvers/ src/officina/install/blueprints/launcher-entry.yaml skills/install-assistant-tools/_rtx/_install_launcher/ skills/install-assistant-tools/tests/test_install_launcher.py tests/test_officina_launcher_entry.py
git commit -m "fix(install-assistant-tools): generate launchers via a dependency-free stable resolver, not embedded repo/interpreter paths"
```

**Known interim state after this task:** until Task 7 lands (deployment of the resolver + `trusted-roots.json` sidecar into an activated release), no `current.json` exists in production and the generated shims will fail cleanly with a "no current.json" resolver error rather than silently doing the old (buggy) thing. This is an accepted, temporary regression window scoped to land alongside Task 7 in the same rollout — call this out explicitly in the Task 6 commit/PR description so it isn't mistaken for an unrelated bug if observed between the two commits landing.

---

## Task 7: Wire `_phase_entry.py` to the managed runtime (feedback item 1)

> **Naming correction (2026-07-28):** Tasks 4-5 landed with module id `install`
> (not `officina-install` — this repo's blueprint graph enforces module id ==
> directory basename), and Task 5's export is named to match its `install.interface.*`
> sibling convention. Verify the exact current interface/export name in
> `src/officina/install/blueprint.yaml` before writing `uses_interfaces` below
> rather than trusting the `officina-install.interface.build-candidate-release`
> name this section originally used.
>
> **Scope addition (2026-07-28):** Task 6's corrected design (dependency-free
> resolver at `src/officina/install/resolvers/launch.py`, trust sidecar at
> `trusted-roots.json`) explicitly deferred **deploying** those two files into
> an activated release to this task — that deployment step was missing from
> the step list below and is added as Step 3b. Without it, `dispatcher`/
> `invoke-skill` remain non-functional after install even once this task's
> other wiring lands.

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify (via `skill-maker`): `skills/install-assistant-tools/_rtx/blueprints/rtx-phase-entry.yaml` (add `uses_interfaces` for the real `install.interface.*` export Task 5 created — check its exact name in `src/officina/install/blueprint.yaml` first)
- Test: `skills/install-assistant-tools/tests/test_install.py`

- [ ] **Step 1: Write failing test**

```python
def test_phase_entry_builds_candidate_before_scaffold(monkeypatch, tmp_home):
    calls = []
    monkeypatch.setattr(
        "officina.install.managed_runtime.build_candidate_release",
        lambda **kwargs: calls.append(kwargs) or (tmp_home / "runtime" / "releases" / "fake"),
    )
    run_install(home=tmp_home, mode="plugin")
    assert calls, "expected build_candidate_release to be called before scaffold"


def test_phase_entry_failed_candidate_leaves_prior_pointer_and_returns_nonzero(monkeypatch, tmp_home):
    def fail(**kwargs):
        raise ManagedRuntimeError("simulated")
    monkeypatch.setattr("officina.install.managed_runtime.build_candidate_release", fail)
    exit_code = run_install(home=tmp_home, mode="plugin")
    assert exit_code != 0
    assert not (tmp_home / "runtime" / "current.json").exists()
```

(Match `_phase_entry.py`'s real entry-point function name/signature.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_install.py -v`
Expected: FAIL — `_phase_entry.py` doesn't call `build_candidate_release` yet.

- [ ] **Step 3: Wire the call in**

In `_phase_entry.py`, before invoking `scaffold.run`, call `officina.install.managed_runtime.build_candidate_release(...)`; on `ManagedRuntimeError`, return a nonzero typed failure without calling scaffold at all.

- [ ] **Step 3b: Deploy the resolver and trust sidecar into the activated release (closes the Task 6 gap)**

**Files:**
- Modify: `src/officina/install/managed_runtime.py` (`build_candidate_release`, or wherever activation finalizes a release)
- Test: `skills/install-assistant-tools/tests/test_install.py`

After a candidate release is built and validated but as part of the same atomic activation `build_candidate_release`/`activate_release` performs, copy `src/officina/install/resolvers/launch.py` to `<runtime_root>/bootstrap/resolvers/v1/launch.py`, and write `trusted-roots.json` alongside it containing the same trusted-interpreter-roots list `build_candidate_release` already derives via `managed_runtime.uv_python_install_dir()` (per Task 5) — as a flat JSON list of absolute path strings, matching the shape `resolvers/launch.py`'s `_trusted_interpreter_roots()` already expects (verify the exact expected shape by reading that function before writing this step).

Write a test that, after a full `build_candidate_release` + this deployment step, actually invokes the generated `dispatcher` shim (or the resolver directly, matching the shim's exact invocation form) via `subprocess.run` with a clean environment (no `PYTHONPATH` injected — reuse the pattern from `tests/test_officina_launcher_entry.py`'s existing clean-env end-to-end tests) and asserts it succeeds without any `ModuleNotFoundError`. This is the test that finally proves `dispatcher --help` works again after a real plugin-mode install — update the three tests that were changed to assert the "no resolver deployed yet" failure mode in Task 6 (`test_claude_install.py`, `test_codex_install.py`, `test_e2e_lifecycle.py::test_launchers_executable_after_install`) back to asserting success, now that deployment actually happens.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_install.py skills/install-assistant-tools/tests/test_e2e_lifecycle.py skills/install-assistant-tools/tests/test_claude_install.py skills/install-assistant-tools/tests/test_codex_install.py -v`
Expected: PASS, no regressions in the existing e2e lifecycle suite, and the three tests reverted in Step 3b now assert success again.

- [ ] **Step 5: Blueprint update**

Add `uses_interfaces` to `blueprints/rtx-phase-entry.yaml` referencing the real `install.interface.*` export name (check `src/officina/install/blueprint.yaml` for its current exact name — do not assume `build-candidate-release` or the `officina-install` module id, both are stale from before Tasks 4-5 landed); extend that export's `allowed_callers` in `src/officina/install/blueprint.yaml` if not already covering `install-assistant-tools`.

- [ ] **Step 5c: Remove scaffold's own redundant, ambient-python-violating install path**

**Bug found during Task 7 execution (2026-07-28):** `_install_scaffold.py`'s `install_python_packages` was never actually fixed by Task 5 — it still runs `subprocess.run([sys.executable, "-m", "pip", "install", *packages, ...])`, i.e. it still shells out to **ambient Python**, the exact violation feedback items 2/3 exist to eliminate. Task 5 only fixed how the package list is *parsed* (`declared_python_packages`), not *where* it installs to. Now that Step 3/3b wire `build_candidate_release` (which correctly provisions a managed venv and installs the same package set there) to run before `scaffold.run()`, `install_python_packages` is pure redundant work on top of an already-known-bad path.

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py` (delete `install_python_packages`, `pip_install_timeout_seconds`, and their call site in `run()`)
- Test: `skills/install-assistant-tools/tests/test_scaffold.py` (delete/update tests that exercised `install_python_packages` directly; add a test confirming `run()` no longer calls `subprocess.run` with `sys.executable` at all)

Delete `install_python_packages` and its call site in `scaffold.run()` (currently appended to the `workflows`/`LauncherInstallResult` list built there). Scaffold's job becomes: assume dependencies are already installed into the managed release venv by the `build_candidate_release` call `_phase_entry.py` now makes before invoking scaffold at all — it does not need its own install step. If scaffold needs to *verify* the managed release exists/is healthy before proceeding (rather than blindly trusting `_phase_entry.py`'s call order), add a lightweight existence check against `FamulusPaths.current_pointer`, not a second install.

Run: `python3 -m pytest -q -o pythonpath=src skills/install-assistant-tools/tests/test_scaffold.py skills/install-assistant-tools/tests/test_install.py skills/install-assistant-tools/tests/test_e2e_lifecycle.py -v`
Expected: PASS. Confirm via `grep -rn sys.executable skills/install-assistant-tools/_rtx/_install_scaffold.py` that the ambient-python invocation is gone.

- [ ] **Step 6: Commit**

```bash
git add skills/install-assistant-tools/_rtx/_phase_entry.py skills/install-assistant-tools/_rtx/blueprints/rtx-phase-entry.yaml skills/install-assistant-tools/_rtx/_install_scaffold.py skills/install-assistant-tools/tests/test_install.py skills/install-assistant-tools/tests/test_scaffold.py
git commit -m "feat(install-assistant-tools): require a verified managed-runtime candidate before scaffold runs; remove scaffold's redundant ambient-python install path"
```

---

## Task 8: Full-module blueprint sync, validation, and certification (final gate)

**Files:** none new — this task runs tooling across everything touched in Tasks 1–7.

- [ ] **Step 1: Run blueprint validation across all touched modules**

Run the repo's real validation entrypoint (confirm exact command from `skills/skill-maker/SKILL.md`) against `src/officina/common/`, `src/officina/install/`, `skills/install-assistant-tools/`.
Expected: zero schema errors, zero orphaned exports, zero dangling `uses_interfaces`.

- [ ] **Step 2: Run certification**

Run `skill-certifier` (confirm exact invocation from `docs/certification_and_drift.md`) for `officina-common`, `officina-install`, `install-assistant-tools`, `install-assistant-tools-rtx`.
Expected: fresh, current certificates for all four nodes.

- [ ] **Step 3: Run the full focused test suite for this plan's scope**

Run: `python3 -m pytest -q tests/test_officina_famulus_paths.py tests/test_officina_install_info.py tests/test_officina_runtime_pointer.py tests/test_officina_managed_runtime.py skills/install-assistant-tools/tests/ -v`
Expected: all PASS.

- [ ] **Step 4: Run the repo's full precommit/CI-equivalent suite**

Run whatever `scripts/run-python-tests.py --suite full` (or the repo's documented full-suite command) is, to catch cross-module regressions outside this plan's direct scope.
Expected: PASS, or any failures triaged as pre-existing/unrelated before proceeding.

- [ ] **Step 5: Commit any generated/regenerated artifacts from validation and certification**

```bash
git status
git add <any regenerated SKILL.md / certificate files>
git commit -m "chore: sync blueprints and certify officina.install / install-assistant-tools after v5 rebase"
```

---

## Task 9: `uv` bootstrap (blocks field deployment, not this plan's other tasks)

> **Added 2026-07-28.** Task 7's hard-abort-before-scaffold semantics (a
> `ManagedRuntimeError` — including "`uv` binary not found" — now stops the
> entire install, not just the managed-runtime piece) means every fresh
> machine without `uv` already on `PATH` will hard-fail immediately after
> Tasks 1-8 land. The original frozen v4 plan (`docs/plans/osx_feedback_fix/01-installer-runtime.md`)
> had a pinned-`uv`-download bootstrap step in its own Task 1; this rebase's
> Task 1 dropped it (scope was narrowed to `FamulusPaths` only) and nothing
> since has replaced it. This task is **not required to close out Tasks 1-8**
> — dev/CI environments already have `uv` installed — but must land before
> this installer is considered field-ready for a machine that has never had
> `uv` on it.

**Files:**
- Create: `src/officina/install/uv_bootstrap.py`, `blueprints/uv-bootstrap.yaml`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py` (call the bootstrap before `_build_managed_runtime_candidate`, only when `paths.uv_bin` doesn't already exist)
- Test: `tests/test_officina_uv_bootstrap.py`

- [ ] **Step 1: Write failing tests**

Cover: downloads the pinned `uv` version from `install-info.toml`'s `bootstrap.uv_version` for the current platform (mock the network call — do not hit a real URL in tests except one explicit, opt-in, skip-guarded real-network integration test mirroring the pattern `tests/test_officina_launcher_entry.py` already used for real-`uv` end-to-end tests); verifies a checksum/signature if the upstream release provides one (research what `uv`'s real release artifacts offer before committing to a verification mechanism — don't invent one without confirming what's actually available); installs to `FamulusPaths.uv_bin`'s parent directory atomically (temp + rename, reusing `officina.common.atomic_files` per the established convention from Tasks 4-5); is a no-op if the pinned version is already present and its version string matches; never invokes ambient `python`/`python3` to perform the download (use `urllib.request` from the officina.install module's own process, which is fine — the constraint is about not invoking ambient *python as the install mechanism*, not about officina's own code never running under whatever Python launched the installer in the first place).

- [ ] **Step 2-6: Standard TDD cycle** — write failing tests, implement, pass, add blueprint, commit.

- [ ] **Step 7: Wire into `_phase_entry.py`**

Before `_build_managed_runtime_candidate()` (Task 7's Step 3), check `paths.uv_bin.exists()`; if not, call the new bootstrap. On bootstrap failure, same hard-abort-with-typed-failure semantics as `ManagedRuntimeError` already has.

- [ ] **Step 8: Update the "Known limitation" framing**

Once this task lands, revisit Task 7's Step 3b tests and any documentation that currently says "requires `uv` already present" and confirm they still make sense (they may now be redundant with this task's own coverage, or may need to explicitly test the bootstrap-then-build sequence together).

---

## Dependency order summary

```
Task 0 (manifest regression guard)
Task 1 (FamulusPaths) ──┬──> Task 2 (Documents-path fix)      [shippable early]
                        │
                        ├──> Task 4 (officina.install scaffold + pointer)
                        │        └──> Task 5 (managed_runtime, real-manifest dependency sync)
                        │                 └──> Task 7 (_phase_entry wiring, removes scaffold's redundant install path)
                        │                          └──> Task 9 (uv bootstrap — blocks field deployment, not Tasks 1-8)
                        │
                        └──> Task 6 (stable-resolver launchers, needs current.json from Task 4)

Task 3 (assistant closure) ── independent, only needs Task 2 landed first for a clean diff
Task 8 (blueprint sync + certification) ── final gate for Tasks 1-7
```

## Explicitly out of scope for this plan

- Manifest v2 (`install-manifest.json` ownership/rollback model) — proceeds close to the original 2026-07-24 design since it never conflicted with the real `runtime_dependencies.json`; tracked as a separate follow-up plan rather than folded in here, to keep this plan's diff reviewable. Rebased plan: [docs/manifest-v2-rebase.md](../../manifest-v2-rebase.md) (not yet implemented as of this writing).
- Native macOS LaunchAgent smoke testing — owned by subplan 06 (`docs/plans/osx_feedback_fix/06-macos-acceptance.md`), which already has real coverage in `skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py`; do not duplicate it here.
- Dispatcher structured-failure contracts, Google onboarding, recurring reliability, downstream workflow repairs — owned by subplans 02–05 of the umbrella, each needing their own (lighter) v5 rebase before implementation.
