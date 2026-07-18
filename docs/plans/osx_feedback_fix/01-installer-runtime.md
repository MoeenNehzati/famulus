# Installer Runtime and Launchers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or, with explicit delegation approval, `superpowers:subagent-driven-development`. Complete tasks in order and stop at each review gate.

**Goal:** Provide a dependency-light cross-platform bootstrap, atomic versioned managed-runtime activation, a script-owned installer wizard, and stable plugin/developer launcher behavior.

**Architecture:** Thin shell/PowerShell wrappers install pinned uv and transfer control to a candidate managed release. The candidate contains the complete installed source payload and venv, is fully smoke-tested, and becomes active only by atomic replacement of `current.json`. Stable shims resolve the active source/interpreter from that pointer. One typed selection model drives the installer; mutable config/state is external to releases, and developer integration is explicit and isolated.

**Tech Stack:** POSIX shell, PowerShell, uv 0.11.29, CPython 3.11, Python stdlib, pytest, GitHub Actions.

## Global constraints

- Inherit only program-wide constraints and sequencing from the [umbrella](README.md). This subplan is authoritative for runtime paths/activation, `InstallSelections`, `InstallResult`, and lifecycle ownership.
- Plugin mode is default/recommended; developer mode requires an explicit checkout.
- The LLM launches the checked-in bootstrap but does not collect installer answers.
- Public commands live under `~/.local/bin` on macOS/Linux and `%LOCALAPPDATA%\Famulus\bin` on Windows.
- Plugin-mode installed payloads live under the Famulus data root; mutable config and operational state live under their distinct Famulus config/state roots, never a release, skill, cache, or user-content directory.
- First install and update always build a new release. They never install dependencies into or rewrite the active release in place.
- Plugin mode leaves complete `$CODEX_HOME` and `$CLAUDE_HOME` trees unchanged.
- Skill documentation/contract changes use `skill-maker`.
- Development test commands may use the repo/CI Python; product bootstrap may not.

## Source feedback owned here

Items 1-4, 8, 18, and 19 in the umbrella traceability table.

---

### Task 1: Add canonical install metadata, the pinned `uv` bootstrap, and the managed-runtime model

**Files:**
- Create: `install-info.toml`
- Create: `skills/install-assistant-tools/bootstrap/install-info.toml` as the packaged byte-identical copy
- Create through `skill-maker`: `skills/install-assistant-tools/bootstrap/runtime_dependencies.json` as the generated packaged dependency manifest
- Create: `skills/install-assistant-tools/bootstrap/bootstrap.sh`
- Create: `skills/install-assistant-tools/bootstrap/bootstrap.ps1`
- Create: `src/officina/install/__init__.py`
- Create: `src/officina/install/install_info.py`
- Create: `src/officina/install/managed_runtime.py`
- Create: `src/officina/install/runtime_pointer.py`
- Create: `src/officina/install/launcher_entry.py`
- Create: `src/officina/common/famulus_paths.py`
- Modify through `skill-maker`: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `skills/skill-maker/tests/test_blueprint_tools.py`
- Create: `tests/test_officina_install_info.py`
- Create: `tests/test_officina_managed_runtime.py`
- Create: `tests/test_officina_famulus_paths.py`
- Create: `skills/install-assistant-tools/tests/test_bootstrap_contract.py`
- Modify: `README.md`
- Modify: `docs/installation.md`
- Modify through `skill-maker`: `skills/install-assistant-tools/SKILL.md`

**Interfaces:**
- Produces: `InstallInfo` with typed bootstrap, managed-Python, and `uv` sections.
- Produces: `load_install_info(path: Path) -> InstallInfo`; the developer caller supplies root-level `install-info.toml`, while the plugin bootstrap supplies the copy shipped beside its launcher payload. Task 1 does not depend on the Task 2 `InstallMode` enum.
- Produces: dependency-light shared `FamulusPaths(home: Path, platform: str, env: Mapping[str, str])` with platform data/config/state roots, `runtime_root`, `releases_root`, `current_pointer`, `install_state_root`, `uv_bin`, `python_install_dir`, `worker_root`, `launcher_profile_root`, recurring/email-triage config/state roots, and `user_bin`.
- Produces: `famulus_paths(home: Path, platform: str = sys.platform, env: Mapping[str, str] | None = None) -> FamulusPaths`; installer code may retain `ManagedRuntimePaths` as an alias during migration.
- Produces: `RuntimePointer(schema_version, release_id, runtime_source, python_bin)` plus strict load/validate/atomic-activate helpers and a single-writer activation lock/journal.
- Produces: `declared_python_packages(manifest_path: Path, platform: str) -> tuple[str, ...]`; developer mode supplies the root generated manifest and plugin mode supplies its packaged byte-identical copy.
- Produces: `build_candidate_release(*, repo_root: Path, paths: ManagedRuntimePaths, release_id: str, uv_executable: Path, platform: str, dry_run: bool = False) -> RuntimePointer` and `activate_candidate(paths, pointer) -> None`.
- Produces: `main(argv: Sequence[str] | None = None) -> int` with a `bootstrap --repo-root PATH [--dry-run]` command used by both platform wrappers.
- Consumes: `InstallInfo.managed_python.preferred`, `InstallInfo.managed_python.supported`, and `InstallInfo.uv.version` rather than independent Python/uv constants.
- Consumes: Astral's versioned standalone installers at `https://astral.sh/uv/<uv.version>/install.sh` and `install.ps1`.

- [ ] **Step 1: Write the failing install-information contract tests**

Create `tests/test_officina_install_info.py` with an exact policy assertion:

```python
from pathlib import Path

from officina.install.install_info import load_install_info


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_install_info_separates_bootstrap_from_managed_python() -> None:
    info = load_install_info(REPO_ROOT / "install-info.toml")
    assert info.schema_version == 1
    assert info.bootstrap.system_python_required is False
    assert info.bootstrap.network_required is True
    assert info.bootstrap.administrator_required is False
    assert info.bootstrap.supported_platforms == ("linux", "macos", "windows")
    assert info.bootstrap.posix_shell == "sh"
    assert info.bootstrap.posix_downloaders == ("curl", "wget")
    assert info.bootstrap.windows_shell == "powershell"
    assert info.managed_python.preferred == "3.11"
    assert info.managed_python.supported == ">=3.11,<3.12"
    assert info.managed_python.preference == "only-managed"
    assert info.uv.version == "0.11.29"
```

Run: `python3 -m pytest -q tests/test_officina_install_info.py`

Expected: collection fails because `officina.install.install_info` does not exist.

- [ ] **Step 2: Write failing path and command-construction tests**

Add tests that require distinct data/config/state roots outside user-content directories, a versioned release root, and a stable pointer on macOS/Linux. Windows uses distinct `data`, `config`, `state`, and `bin` children under `LOCALAPPDATA\Famulus`:

```python
from pathlib import Path

from officina.common.famulus_paths import famulus_paths


def test_macos_runtime_paths_avoid_documents() -> None:
    paths = famulus_paths(Path("/Users/tester"), "darwin", {})
    assert paths.user_bin == Path("/Users/tester/.local/bin")
    assert paths.data_root == Path("/Users/tester/.local/share/famulus")
    assert paths.config_root == Path("/Users/tester/.config/famulus")
    assert paths.state_root == Path("/Users/tester/.local/state/famulus")
    assert paths.runtime_root == paths.data_root / "runtime"
    assert paths.releases_root == paths.runtime_root / "releases"
    assert paths.current_pointer == paths.runtime_root / "current.json"
    assert paths.install_state_root == paths.state_root / "install"
    assert paths.uv_bin == paths.runtime_root / "bootstrap/bin/uv"
    assert paths.python_install_dir == paths.runtime_root / "python"
    assert paths.worker_root == paths.state_root / "workers"
    assert paths.launcher_profile_root == paths.data_root / "launcher-profiles"
    assert paths.recurring_config_root == paths.config_root / "recurring-tasks"
    assert paths.recurring_state_root == paths.state_root / "recurring-tasks"
    assert paths.email_triage_state_root == paths.state_root / "email-triage"
    assert "Documents" not in str(paths)


def test_windows_runtime_paths_are_user_scoped(monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    paths = famulus_paths(Path(r"C:\Users\tester"), "win32", dict(os.environ))
    assert paths.user_bin == Path(r"C:\Users\tester\AppData\Local\Famulus\bin")
    assert paths.uv_bin == paths.runtime_root / "bootstrap/bin/uv.exe"
    assert paths.python_install_dir == paths.runtime_root / "python"
    assert paths.data_root == Path(r"C:\Users\tester\AppData\Local\Famulus\data")
    assert paths.config_root == Path(r"C:\Users\tester\AppData\Local\Famulus\config")
    assert paths.state_root == Path(r"C:\Users\tester\AppData\Local\Famulus\state")
    assert paths.worker_root == paths.state_root / "workers"
    assert paths.launcher_profile_root == paths.data_root / "launcher-profiles"
```

Add pointer tests for missing/unknown schemas, non-absolute paths, traversal or paths outside `runtime_root`, mismatched release IDs, missing source/interpreter, truncated JSON, concurrent updater exclusion, interrupted activation-journal recovery, and atomic replacement. Add lifecycle tests proving a failed candidate build or smoke leaves the prior pointer bytes unchanged, successful activation switches both source and interpreter together, and the immediately previous release remains available.

In `test_bootstrap_contract.py`, load root `install-info.toml` and assert its packaged copy is byte-identical; assert the packaged dependency manifest equals the generated root manifest. Build the same allow-list used by the actual plugin/package assembly and prove both metadata files, both platform bootstraps, and the stable launcher resolver are included. Assert both bootstrap files mirror `uv.version` and `managed_python.preferred`, contain the derived pinned installer URL, set `UV_UNMANAGED_INSTALL`, set `UV_PYTHON_INSTALL_DIR` to the Famulus-owned Python directory, and transfer control to a shared managed-runtime entrypoint rather than running `_phase_entry.py` under the ambient interpreter. The scripts must not invoke bare `python`, `python3`, or `py` before `uv` has installed/resolved the preferred interpreter.

- [ ] **Step 3: Run the focused tests and verify RED**

Run: `python3 -m pytest -q tests/test_officina_install_info.py tests/test_officina_famulus_paths.py tests/test_officina_managed_runtime.py skills/install-assistant-tools/tests/test_bootstrap_contract.py`

Expected: collection fails because the install-info/runtime modules and bootstrap files do not exist.

- [ ] **Step 4: Write the canonical install-information file and parser**

Create root-level `install-info.toml` with exactly these policy fields:

```toml
schema_version = 1

[bootstrap]
system_python_required = false
network_required = true
administrator_required = false
supported_platforms = ["linux", "macos", "windows"]

[bootstrap.posix]
shell = "sh"
downloaders = ["curl", "wget"]

[bootstrap.windows]
shell = "powershell"

[managed_python]
preferred = "3.11"
supported = ">=3.11,<3.12"
preference = "only-managed"

[uv]
version = "0.11.29"
```

Parse it into these public immutable types:

```python
@dataclass(frozen=True)
class BootstrapInfo:
    system_python_required: bool
    network_required: bool
    administrator_required: bool
    supported_platforms: tuple[str, ...]
    posix_shell: str
    posix_downloaders: tuple[str, ...]
    windows_shell: str


@dataclass(frozen=True)
class ManagedPythonInfo:
    preferred: str
    supported: str
    preference: str


@dataclass(frozen=True)
class UvInfo:
    version: str


@dataclass(frozen=True)
class InstallInfo:
    schema_version: int
    bootstrap: BootstrapInfo
    managed_python: ManagedPythonInfo
    uv: UvInfo
```

Implement `load_install_info(path: Path)` with stdlib `tomllib` under the managed Python. Developer mode passes `<repo-root>/install-info.toml`; plugin mode passes the packaged bootstrap copy and therefore does not depend on a host checkout. Reject unknown `schema_version` values, missing/incorrectly typed fields, and a managed-Python preference other than `only-managed`. Root remains canonical; the package/build contract fails on copy drift. Runtime packages remain generated in `references/blueprint/runtime_dependencies.json`; plugin assembly copies that exact artifact into the bootstrap payload and fails on drift rather than maintaining a handwritten list.

- [ ] **Step 5: Implement the path model, platform-filtered dependency manifest, and atomic pointer**

Create the shared path model in `officina.common` so installer, Google, recurring, and email-triage code use one implementation. It must accept an explicit environment mapping for tests, honor XDG overrides on POSIX, require `LOCALAPPDATA` on Windows, normalize supported platform names, and reject relative roots:

```python
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


def declared_python_packages(manifest: Path, platform: str) -> tuple[str, ...]:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    packages = payload.get("by_platform", {}).get(platform, {}).get("python-package", [])
    if not isinstance(packages, list) or not all(isinstance(item, str) and item for item in packages):
        raise ManagedRuntimeError(f"invalid Python dependency manifest: {manifest}")
    return tuple(sorted(set(packages), key=str.lower))
```

Update `generated_runtime_dependencies_manifest` through `skill-maker` to emit schema version 2 with `by_platform.linux`, `.macos`, and `.windows` aggregates. Each `python-package` aggregate is a sorted list of pip requirement strings: `name` when the blueprint version is `any`, otherwise `name<specifier>` (for example `PyYAML>=6`). `all` remains reporting-only and must not drive installation. The same sync transaction writes byte-identical JSON to the canonical root path and packaged bootstrap path; `--check` fails when either differs. Add generator tests for a dependency supported on only one platform, multiple declarations of the same package, and one-sided packaged-copy drift. Normalize identical requirements; if distinct constraints cannot be represented safely as one combined requirement, fail generation instead of silently choosing one.

`build_candidate_release` creates a new `releases/<release-id>` directory and never writes the active release. It copies the allow-listed complete first-party payload needed by dispatcher, launchers, hooks, schemas/references, profiles, and skills into `source/`; it never copies mutable state, credentials, caches, virtual environments, or VCS metadata. Reject source symlinks that escape the payload root and verify all required packaged files before continuing. Then create `venv/`, load `InstallInfo`, set `UV_PYTHON_INSTALL_DIR=<python_install_dir>` for every uv subprocess, and execute these logical operations with `subprocess.run(..., check=True)`:

1. `uv python install <managed_python.preferred>`;
2. `uv venv --managed-python --python <managed_python.preferred> <candidate>/venv` for every candidate release;
3. `uv pip install --python <candidate-python> <platform-filtered declared packages>`;
4. verify the candidate interpreter version, import all required core modules, run dispatcher route/dry-run checks from the copied source, and smoke the generated `dispatcher`, `invoke-skill`, and mandatory `assistant` closure against the candidate.

Only after all four operations and prospective-manifest validation succeed may `activate_candidate` take the per-install lock and create an activation journal containing checksums/backups of the old pointer/manifest and the new intended records. It then writes same-directory temporary files, flushes them, replaces the manifest, and replaces `current.json` as the commit point. A caught failure restores both old files before returning. On startup, every lifecycle command resolves an unfinished journal before reading active state: absence of the committed new pointer rolls back both records; presence of the exact committed pointer completes manifest cleanup. Thus installed commands always use either the old or new complete release, never a mixed source/interpreter. Retain at least the immediately previous release in manifest `previous_release`. Garbage collection may remove only older releases absent from active/previous fields and every resource owner set.

Dry-run must print the same operations without creating directories or downloading artifacts.

- [ ] **Step 6: Implement the two thin platform bootstraps**

The POSIX bootstrap must:

```sh
UV_VERSION="0.11.29"
PYTHON_VERSION="3.11"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/famulus"
RUNTIME_ROOT="$DATA_ROOT/runtime"
UV_BIN="$RUNTIME_ROOT/bootstrap/bin/uv"
UV_PYTHON_INSTALL_DIR="$DATA_ROOT/python"
INSTALL_URL="https://astral.sh/uv/$UV_VERSION/install.sh"
```

These two values mirror `install-info.toml` because no TOML-capable Python exists yet. The contract test makes drift a failure; do not add an ad hoc TOML parser to either wrapper.

The POSIX wrapper derives `REPO_ROOT` from the checked-in bootstrap location, exports `UV_PYTHON_INSTALL_DIR`, checks `test -x "$UV_BIN"`, and, when missing, downloads the versioned installer with `curl` or `wget` and executes it with `UV_UNMANAGED_INSTALL="$RUNTIME_ROOT/bootstrap/bin"`. It then invokes the shared stdlib-only entrypoint with this logical command:

```text
PYTHONPATH=<repo-root>/src <uv-bin> run --managed-python --python 3.11 --no-project -- python -m officina.install.managed_runtime bootstrap --repo-root <repo-root>
```

The PowerShell bootstrap uses the same mirrored values, sets `$env:UV_PYTHON_INSTALL_DIR` to `$env:LOCALAPPDATA\Famulus\data\python`, installs uv under `$env:LOCALAPPDATA\Famulus\data\runtime\bootstrap\bin`, supplies `<repo-root>/src` only to the child process, and invokes the same module entrypoint. Once started, the POSIX wrapper emits a named error when neither downloader is available; both wrappers exit nonzero on download, candidate creation, dependency installation, smoke verification, or pointer activation failure. Shell/PowerShell availability is a pre-invocation host prerequisite and must be stated in the installation preflight. Neither wrapper probes or executes the system Python, and every uv environment/run command uses `--managed-python` so a compatible ambient Python is not silently selected.

- [ ] **Step 7: Document the explicit first-install route and both Python contracts**

Update `README.md`, `docs/installation.md`, and the skill to say that first installation uses the bootstrap because dispatcher does not exist yet. State that later operations use the installed absolute dispatcher: update requires the absolute newly delivered plugin/source root, repair uses the active release, and uninstall/purge use manifest v2. Document the exact dispatcher commands and that downloading/delivering a new plugin payload is outside the update interface's mutation scope. Replace the inaccurate `Python 3.6+` claim with two explicit statements:

1. no system Python is required for bootstrap;
2. Famulus installs preferred CPython 3.11 and supports the managed range `>=3.11,<3.12`.

Link `install-info.toml` as the canonical source and document the actual non-Python prerequisites: supported OS/architecture, POSIX shell plus `curl`/`wget` or PowerShell, TLS/network access, and writable/executable user directories. Include the ordered installation pipeline and exact default destinations from this plan, emphasizing that `dispatcher` and `invoke-skill` go in the stable user command directory rather than Documents or the plugin cache.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_officina_install_info.py tests/test_officina_famulus_paths.py tests/test_officina_managed_runtime.py skills/install-assistant-tools/tests/test_bootstrap_contract.py`

Expected: all tests pass without network access by mocking subprocess/download calls.

- [ ] **Step 9: Commit the bootstrap boundary after review**

```bash
git add install-info.toml \
  skills/install-assistant-tools/bootstrap/install-info.toml \
  skills/install-assistant-tools/bootstrap/runtime_dependencies.json \
  README.md \
  docs/installation.md \
  skills/install-assistant-tools/bootstrap/bootstrap.sh \
  skills/install-assistant-tools/bootstrap/bootstrap.ps1 \
  skills/install-assistant-tools/SKILL.md \
  skills/install-assistant-tools/tests/test_bootstrap_contract.py \
  src/officina/install/__init__.py \
  src/officina/common/famulus_paths.py \
  src/officina/install/install_info.py \
  src/officina/install/managed_runtime.py \
  tests/test_officina_install_info.py \
  tests/test_officina_famulus_paths.py \
  tests/test_officina_managed_runtime.py
git commit -m "feat: add managed Python bootstrap"
```

---

### Task 2: Move installation decisions from the LLM into a script-owned wizard

**Files:**
- Create: `src/officina/install/install_options.py`
- Create: `tests/test_officina_install_options.py`
- Modify: `skills/install-assistant-tools/bootstrap/bootstrap.sh`
- Modify: `skills/install-assistant-tools/bootstrap/bootstrap.ps1`
- Modify: `src/officina/install/managed_runtime.py`
- Modify through `skill-maker`: `skills/install-assistant-tools/blueprint.yaml`
- Modify through `skill-maker`: `skills/install-assistant-tools/SKILL.md`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/tests/test_bootstrap_contract.py`
- Modify: `skills/install-assistant-tools/tests/test_install.py`
- Regenerate: `references/blueprint/runtime_dependencies.json`
- Modify: `docs/installation.md`

**Interfaces:**
- Produces: `InstallMode(StrEnum)` with `PLUGIN = "plugin"` and `DEVELOPER = "developer"`.
- Produces: `RecurringSetup(StrEnum)` with `SKIP = "skip"` and `RECOMMENDED = "recommended"`.
- Produces: immutable `InstallSelections(mode, repo_path, agents, default_llm, google_services, gmail_nickname, recurring_setup)`.
- Produces: `CapabilityStatus(StrEnum)` with `COMPLETE`, `PARTIAL`, `SKIPPED`, `BLOCKED`, and `FAILED`, plus immutable `OnboardingCapabilityResult(name, status, code, message, retry_command)` for optional post-install phases.
- Produces: immutable `InstallResult(schema_version, core_install, google_onboarding, recurring_automation, retry_commands)` with `as_payload()`, `render_text()`, and `exit_code()` implementing the exact umbrella mapping `0/1/2/3`.
- Produces: `write_install_result(result: InstallResult, *, home: Path) -> Path`, atomically writing `~/.local/state/assistant-tools/latest-install.json` without secrets.
- Produces: `InstallCancelled(Exception)` as the local control-flow signal raised only when the user declines the final confirmation; `_phase_entry` converts it to an `InstallResult` without starting scaffold or optional phases.
- Produces: `collect_install_selections(namespace: argparse.Namespace, *, input_fn: Callable[[str], str], output: TextIO) -> InstallSelections`.
- Produces: `render_install_summary(selections: InstallSelections) -> str` with no credentials, tokens, or environment values.
- Changes: canonical installer arguments are `--mode {plugin,developer}`, `--repo-path DIR`, `--agents LIST`, `--default-llm {claude,codex}`, repeated `--google-service {drive,calendar,gmail}`, `--gmail-nickname NAME`, `--recurring {skip,recommended}`, `--non-interactive`, and `--yes`.
- Preserves: `--dev-mode` and `--no-dev-mode` as deprecated compatibility aliases for one release; conflicting old/new mode flags are rejected.

- [ ] **Step 1: Write failing selection-model tests**

Create `tests/test_officina_install_options.py` with local deterministic helpers and exact construction/validation cases:

```python
def answers(*values: str):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def namespace(**overrides):
    defaults = {
        "interactive": True,
        "mode": None,
        "repo_path": None,
        "agents": None,
        "default_llm": None,
        "google_service": [],
        "gmail_nickname": None,
        "recurring": None,
        "non_interactive": False,
        "yes": False,
        "dev_mode": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)
```

Then add:

```python
def test_blank_mode_selects_recommended_plugin_mode() -> None:
    selections = collect_install_selections(
        namespace(interactive=True),
        input_fn=answers("", "", "", "", "n", ""),
        output=io.StringIO(),
    )
    assert selections.mode is InstallMode.PLUGIN
    assert selections.repo_path is None


def test_developer_mode_requires_explicit_checkout() -> None:
    selections = collect_install_selections(
        namespace(interactive=True),
        input_fn=answers("2", "/work/famulus", "", "", "", "n", ""),
        output=io.StringIO(),
    )
    assert selections.mode is InstallMode.DEVELOPER
    assert selections.repo_path == Path("/work/famulus")
```

The output assertion must include this exact mode presentation:

```text
Choose an installation mode:

1. Plugin mode (recommended)
   Stable installation for normal use. Updates are managed through the plugin.

2. Developer mode
   Uses a live repository checkout so skill, hook, and launcher edits take effect immediately.

Selection [1]:
```

Add cases proving invalid mode input reprompts inside the script; developer mode alone asks for a checkout path; plugin mode never asks for one; blank optional selections produce no optional agents or Google services; recurring setup requires explicit `recommended`; duplicates and unknown agents/services are rejected; and the summary clearly labels plugin mode as recommended.

Add result tests for every exit mapping: all requested complete/skipped gives `0`; core failure gives `1`; invalid selection gives `2`; and core complete with any explicitly requested optional status in `PARTIAL`, `BLOCKED`, or `FAILED` gives `3`. Assert human and JSON renderings derive from the same object and contain no environment values or secrets.

Run: `python3 -m pytest -q tests/test_officina_install_options.py`

Expected: collection fails because `officina.install.install_options` does not exist.

- [ ] **Step 2: Write failing non-interactive parity tests**

Construct an argparse namespace equivalent to:

```text
--non-interactive --mode developer --repo-path /work/famulus \
--agents collab,coauthor --default-llm codex \
--google-service drive --google-service calendar --recurring recommended --yes
```

Assert it produces the same `InstallSelections` as the equivalent interactive answers without calling `input_fn`. Add exact default assertions for an otherwise empty non-interactive invocation:

```python
assert selections == InstallSelections(
    mode=InstallMode.PLUGIN,
    repo_path=None,
    agents=(),
    default_llm="claude",
    google_services=(),
    gmail_nickname=None,
    recurring_setup=RecurringSetup.SKIP,
)
```

Require non-interactive mode to reject developer mode without `--repo-path`, `--repo-path` with plugin mode, `--gmail-nickname` without the Gmail service, `--yes` without `--non-interactive`, duplicate service flags, and conflicting `--mode`/legacy mode flags. When Gmail is selected without an explicit nickname, use the stable default `gmail`. No validation error may fall back to an LLM question.

Run: `python3 -m pytest -q tests/test_officina_install_options.py -k non_interactive`

Expected: failures because the typed selection model and canonical flags do not exist.

- [ ] **Step 3: Implement the selection model and local wizard**

Create `install_options.py` with the mode/recurring/status enums, frozen selection/result dataclasses, normalization functions, exact prompt text, local validation loops, and summary renderer. The interactive question order is fixed:

1. installation mode, with plugin first and selected by blank input;
2. developer checkout path only when developer mode was explicitly selected;
3. optional agent launchers;
4. default launcher backend;
5. optional Google services;
6. Gmail nickname, defaulting to `gmail`, only when Gmail was selected;
7. optional recommended recurring automation;
8. one complete summary and `Continue with this installation? [Y/n]`.

Cancellation returns a named `InstallCancelled` result before scaffold, developer integration, launchers, OAuth, or recurring setup runs. The already downloaded Famulus bootstrap/runtime may remain and must be identified as reusable state; cancellation must not call uninstall or delete anything.

Both interactive and non-interactive routes pass through the same normalization and cross-field validation. `--yes` suppresses only the final confirmation, not validation. Do not inspect the filesystem to infer developer mode.

- [ ] **Step 4: Route bootstrap arguments into the managed installer unchanged**

Make both thin bootstrap wrappers accept and forward installer arguments after their own bootstrap options. They continue to mirror only the pinned uv/Python values required before managed Python exists; they must not duplicate the installation menu or parse mode, agent, backend, Google, or recurring selections.

Extend `test_bootstrap_contract.py` to pass sentinel installer arguments containing spaces and assert the POSIX and PowerShell wrappers preserve argument boundaries when transferring control to the managed installer. Reject shell-string concatenation or reparsing.

- [ ] **Step 5: Replace `_phase_entry` prompts with `InstallSelections`**

At the start of `_phase_entry.run`, collect one `InstallSelections` object and pass its fields to later phases. Remove the standalone yes/no developer question, repository-path prompt, agent prompt, default-backend prompt, and any LLM-facing instruction to collect those values first. Plugin mode derives its immutable plugin root from the installed package; developer mode consumes only the explicitly selected `repo_path`.

The phase entrypoint must print the complete plan summary before optional writes and atomically write/render `InstallResult` after execution. The Google and recurring subplans consume `google_services` and `recurring_setup`; until those subplans land, explicitly requested selections are recorded as `BLOCKED` with safe retry commands and produce exit `3`, rather than asking the LLM to perform them.

Add orchestration tests proving the LLM-equivalent caller can invoke one bootstrap/install command with no pre-collected answers; the script owns every prompt. Assert cancellation stops before scaffold and that invalid input is handled entirely within the process.

- [ ] **Step 6: Change the installer skill contract from conversational setup to script launch**

Use `skill-maker` to change `install-assistant-tools.machine.scripts-install` to the canonical flag surface above and regenerate the injected contract. Rewrite the owner-facing skill workflow so it requires the LLM to do only this:

1. explain that checked-in bootstrap code will install a private runtime and run an interactive installer;
2. ask permission to launch that bootstrap;
3. invoke it without separately asking mode, launcher, backend, remote-service, or recurring questions;
4. relay the script's final capability report or structured retry route.

Delete the current LLM-owned “Ask the mode question” and conversational “Phase 2” instructions. Document that deterministic recovery stays in scripts; the LLM may interpret a structured failure or propose a code change only when the script reports an unsupported condition.

- [ ] **Step 7: Run the script-owned wizard slice**

Run:

```text
python3 -m pytest -q \
  tests/test_officina_install_options.py \
  skills/install-assistant-tools/tests/test_bootstrap_contract.py \
  skills/install-assistant-tools/tests/test_install.py \
  tests/validate_blueprints.py
```

Expected: all pass; tests provide all input in-process, open no browser, touch no real home directory, and prove plugin mode is both recommended and selected by default.

- [ ] **Step 8: Commit after review**

Stage only the typed option model, installer/contract, bootstrap forwarding, tests, generated artifact, and documentation listed in this task. Commit with message `feat: move installation choices into scripts`.

---

### Task 3: Execute the script-owned installation plan inside the managed environment

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/_rtx/_agent_launchers.py`
- Modify: `skills/install-assistant-tools/bin/_agent_launch.py`
- Modify: `skills/install-assistant-tools/tests/test_scaffold.py`
- Modify: `skills/install-assistant-tools/tests/test_install.py`
- Modify: `skills/install-assistant-tools/tests/test_launchers.py`
- Modify: `skills/install-assistant-tools/tests/test_agent_launch.py`
- Modify: `skills/install-assistant-tools/tests/test_e2e_lifecycle.py`
- Modify: `script_dispatcher/pyproject.toml`
- Modify: `.github/workflows/python-tests.yml`

**Interfaces:**
- Consumes: `managed_runtime_paths`, `build_candidate_release`, `activate_candidate`, and the active `RuntimePointer` from Task 1, plus `InstallSelections` from Task 2.
- Produces: `_phase_entry.run(..., selections: InstallSelections | None = None, runtime_python: Path | None = None, worker_root: Path | None = None, launcher_profile_root: Path | None = None) -> int`.
- Produces: `_install_scaffold.run(..., runtime_python: Path | None = None, sync_dependencies: bool = True) -> int`.
- Produces: `launcher_closure(selected_agents: Sequence[str], *, install_invoke_skill: bool = True) -> tuple[str, ...]`; when `install_invoke_skill` is true, `assistant` is present exactly once and ordered before optional agents.
- Produces: `verify_installed_commands(command_paths: Sequence[Path], *, platform: str = sys.platform) -> None`; it invokes each installed command by absolute path using the platform-native process form.
- Produces: `render_shell_handoff(*, platform: str, bin_dir: Path, shell_rc: Path | None, command_paths: Sequence[Path]) -> str`; it reports installed paths and exact current-shell/new-terminal instructions without claiming to mutate the parent process.

- [ ] **Step 1: Replace ambient-interpreter assertions with managed-interpreter assertions**

Update scaffold tests to pass a fake active pointer and require that generated `dispatcher` and `invoke-skill` launchers contain only the stable uv/bootstrap-resolver paths, not `sys.executable`, the candidate interpreter, source checkout, release ID, or plugin-cache path:

```python
runtime_python = tmp_path / "runtime" / "releases" / "r1" / "venv" / "bin" / "python"
runtime_python.parent.mkdir(parents=True)
runtime_python.write_text("stub", encoding="utf-8")

status = scaffold.run(
    repo_root=repo_root,
    home=tmp_path,
    bin_dir=bin_dir,
    shell_rc=rc_file,
    runtime_python=runtime_python,
)
launcher = (bin_dir / "dispatcher").read_text(encoding="utf-8")
assert str(paths.current_pointer) not in launcher  # the stable resolver owns pointer parsing
assert str(paths.uv_bin) in launcher
assert str(paths.runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py") in launcher
assert str(runtime_python) not in launcher
assert "r1" not in launcher
```

Add a test that `sync_dependencies=False` performs no package-install subprocess calls while still installing launchers.

Add default-placement assertions for the complete dispatcher floor:

```python
home = tmp_path / "home"
paths = managed_runtime_paths(home, "darwin")
status = scaffold.run(
    repo_root=repo_root,
    home=home,
    bin_dir=None,
    shell_rc=shell_rc,
    runtime_python=runtime_python,
)
assert status == 0
assert (paths.user_bin / "dispatcher").is_file()
assert (paths.user_bin / "invoke-skill").is_file()
assert (paths.user_bin / "assistant").is_file()
assert (paths.user_bin / "_agent_launch.py").is_file()
```

Use `.bat` suffixes for the Windows public commands and assert every required Windows wrapper is present. Assert neither resolved path contains `Documents`, `Desktop`, `Downloads`, or the plugin-cache root. Pass an empty optional-agent selection and require `launcher_closure((), install_invoke_skill=True) == ("assistant",)`.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_scaffold.py skills/install-assistant-tools/tests/test_install.py`

Expected: failures because the new keyword arguments are unsupported and launchers still embed `sys.executable`.

- [ ] **Step 3: Replace per-package ambient `pip` calls**

Delete the loop that runs `[sys.executable, "-m", "pip", ...]`. Build one new candidate release and report one consolidated result. The function must either return a fully verified `RuntimePointer` or raise a typed `ManagedRuntimeError`; required dependency or smoke failure stops Phase 1 rather than logging warnings and continuing. Never mutate the active release.

Expose `--no-dependency-sync` for targeted repairs. It skips only dependency synchronization; it does not skip interpreter verification or launcher installation.

- [ ] **Step 4: Thread the managed interpreter through the phase orchestrator**

At the start of `_phase_entry.run`, resolve the candidate pointer when `runtime_python` is absent and consume the validated `InstallSelections` from Task 2. Refuse to continue if `sys.executable` does not resolve to the candidate interpreter; print the bootstrap command instead of importing more runtime modules under an unsupported interpreter. Install and verify all candidate-owned files before activation; pass the prospective manifest into the journaled activation transaction only after the complete core smoke passes.

Pass the candidate interpreter to candidate smoke checks and pass stable managed paths to launcher installation. Before returning from scaffold, install the full mandatory command closure from `launcher_closure((), install_invoke_skill=True)` into the same effective bin directory as `dispatcher` and `invoke-skill`; a missing `assistant`, `_agent_launch.py`, or platform wrapper is a scaffold failure. Pass plugin-mode `worker_root` and `launcher_profile_root` from `managed_runtime_paths`; retain `repo_root / "workers"` and live `$CODEX_HOME` profiles in development mode.

- [ ] **Step 5: Write failing cross-platform shell-handoff tests**

Before the Python-floor change, add failing shell-handoff tests to `skills/install-assistant-tools/tests/test_scaffold.py`. Cover macOS and Linux independently even though they share POSIX rendering, and cover both native Windows shells:

```python
@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_posix_handoff_names_absolute_commands_and_rc(platform, tmp_path):
    bin_dir = tmp_path / ".local" / "bin"
    rc_file = tmp_path / (".zshrc" if platform == "darwin" else ".bashrc")
    commands = (bin_dir / "dispatcher", bin_dir / "invoke-skill")

    message = scaffold.render_shell_handoff(
        platform=platform,
        bin_dir=bin_dir,
        shell_rc=rc_file,
        command_paths=commands,
    )

    assert str(commands[0]) in message
    assert str(commands[1]) in message
    assert f"source {shlex.quote(str(rc_file))}" in message
    assert "open a new terminal" in message.lower()


def test_windows_handoff_covers_powershell_and_cmd(tmp_path):
    bin_dir = Path(r"C:\Users\tester\AppData\Local\Famulus\bin")
    commands = (bin_dir / "dispatcher.bat", bin_dir / "invoke-skill.bat")

    message = scaffold.render_shell_handoff(
        platform="win32",
        bin_dir=bin_dir,
        shell_rc=None,
        command_paths=commands,
    )

    assert str(commands[0]) in message
    assert "$env:Path" in message
    assert 'set "PATH=' in message
    assert "open a new terminal" in message.lower()
```

Add subprocess tests proving `verify_installed_commands` invokes POSIX launchers directly as `[absolute_path, "--help"]` and Windows batch launchers as `["cmd.exe", "/d", "/c", absolute_path, "--help"]`. A failed absolute-path smoke test must make scaffold fail even if the launcher file exists. Bare `dispatcher` or `invoke-skill` resolution in the installer's unchanged process environment is not an installation-success criterion.

- [ ] **Step 6: Run the shell-handoff tests and verify RED**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_scaffold.py -k 'handoff or verify_installed_commands'`

Expected: failures because `render_shell_handoff` and `verify_installed_commands` do not exist and scaffold does not yet perform absolute-path smoke tests.

- [ ] **Step 7: Implement the cross-platform current-shell handoff**

After launcher creation, call `verify_installed_commands` before reporting success. Then call `render_shell_handoff` and print its result:

- macOS and Linux name the absolute `dispatcher` and `invoke-skill` paths, the exact rc file changed, `source <shell-quoted-rc-path>` for immediate use, and the alternative of opening a new terminal;
- Windows names the absolute `.bat` paths and explains that the persistent user `PATH` applies to new processes. Print an immediate PowerShell assignment (`$env:Path = '<bin-dir>;' + $env:Path`), an immediate `cmd.exe` assignment (`set "PATH=<bin-dir>;%PATH%"`), and the shell-independent alternative of opening a new terminal;
- every platform explicitly states that the already-running parent shell is not modified by the installer;
- `--dry-run` prints the planned handoff but does not execute the smoke tests.

Quote POSIX paths with `shlex.quote`. Quote PowerShell and `cmd.exe` path values with platform-specific helpers rather than interpolating an unescaped user-controlled path. Do not launch a replacement shell, source the user's rc file in a subprocess, or modify the user's current process on the installer's behalf.

- [ ] **Step 8: Align declared and tested Python floors**

Set `script_dispatcher/pyproject.toml` to the exact `install-info.toml` managed support range:

```toml
requires-python = ">=3.11,<3.12"
```

Keep the main CI interpreter at 3.11. Add a bootstrap-contract job that starts from the host environment but verifies the installed dispatcher reports Python 3.11 from the managed environment.
Extend `tests/test_officina_install_info.py` to parse `script_dispatcher/pyproject.toml` and assert its `project.requires-python` equals `InstallInfo.managed_python.supported`.

- [ ] **Step 9: Run the installer slice**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_scaffold.py skills/install-assistant-tools/tests/test_install.py skills/install-assistant-tools/tests/test_launchers.py skills/install-assistant-tools/tests/test_agent_launch.py skills/install-assistant-tools/tests/test_e2e_lifecycle.py`

Expected: all tests pass; no test writes into the developer's real home or package environment.

- [ ] **Step 10: Commit after review**

```bash
git add skills/install-assistant-tools/_rtx/_install_scaffold.py \
  skills/install-assistant-tools/_rtx/_phase_entry.py \
  skills/install-assistant-tools/_rtx/_agent_launchers.py \
  skills/install-assistant-tools/bin/_agent_launch.py \
  skills/install-assistant-tools/tests/test_scaffold.py \
  skills/install-assistant-tools/tests/test_install.py \
  skills/install-assistant-tools/tests/test_launchers.py \
  skills/install-assistant-tools/tests/test_agent_launch.py \
  skills/install-assistant-tools/tests/test_e2e_lifecycle.py \
  script_dispatcher/pyproject.toml .github/workflows/python-tests.yml
git commit -m "feat: run installer in managed Python"
```

---

### Task 4: Stabilize entrypoints in three independent review slices

**Files:**
- Create: `src/officina/launcher/__init__.py`
- Create: `src/officina/launcher/codex_profile.py`
- Create: `tests/test_officina_codex_profile.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_launcher/_base_launcher.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_launcher/_linux_launcher.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_launcher/_windows_launcher.py`
- Modify: `skills/install-assistant-tools/_rtx/_agent_launchers.py`
- Modify: `skills/install-assistant-tools/_rtx/_config_bridge.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py`
- Modify: `skills/install-assistant-tools/_rtx/_state_record.py`
- Create: `skills/install-assistant-tools/_rtx/_update_runtime.py`
- Create: `skills/install-assistant-tools/_rtx/_repair_install.py`
- Modify: `skills/install-assistant-tools/bin/_agent_launch.py`
- Modify: `hooks/inject_dispatcher_context.py`
- Modify: `hooks/hooks.json`
- Modify: `llmhooks/lib/cross_host.py`
- Modify: `skills/install-assistant-tools/tests/test_install_launcher.py`
- Modify: `skills/install-assistant-tools/tests/test_launchers.py`
- Modify: `skills/install-assistant-tools/tests/test_agent_launch.py`
- Modify: `skills/install-assistant-tools/tests/test_dev_link.py`
- Modify: `skills/install-assistant-tools/tests/test_dev_link_hooks.py`
- Modify: `skills/install-assistant-tools/tests/test_uninstall.py`
- Create: `skills/install-assistant-tools/tests/test_update_runtime.py`
- Create: `skills/install-assistant-tools/tests/test_repair_install.py`
- Modify: `hooks/tests/test_inject_dispatcher_context.py`
- Modify through `skill-maker`: `skills/install-assistant-tools/blueprint.yaml`
- Regenerate through `skill-maker`: `skills/install-assistant-tools/SKILL.md`

**Interfaces:**
- Consumes: `ManagedRuntimePaths` from Task 1.
- Consumes: `launcher_closure` and the mandatory universal command closure from Task 3.
- Changes: `install_dispatcher_launcher(..., paths: ManagedRuntimePaths, ...)` and `install_invoke_skill_launcher(..., paths: ManagedRuntimePaths, ...)` on every platform installer; generated shims invoke the stable bootstrap resolver and never embed an active-release path.
- Changes: `install_binding(host, script_path, python_executable) -> InstallBinding`.
- Produces: `codex_config_overrides(profile_path: Path, *, model_instructions_file: Path) -> tuple[str, ...]`; each result is one TOML `key=value` assignment suitable for a Codex `--config` argument.
- Produces: `build_codex_command(agent: str, forwarded_args: Sequence[str], *, plugin_mode: bool, launcher_profile_path: Path | None = None, model_instructions_file: Path | None = None) -> list[str]`.
- Changes: `_agent_launchers.run(..., plugin_mode: bool = False, worker_root: Path | None = None, launcher_profile_root: Path | None = None, paths: FamulusPaths | None = None) -> None`; stable resolver paths replace release-specific interpreter embedding.
- Produces: `install-assistant-tools.machine.update --source-root ABSOLUTE-PATH [--dry-run]`, which builds/activates a new release from a newly delivered plugin/source payload.
- Produces: `install-assistant-tools.machine.repair [--component launchers|hooks|schedulers|all] [--dry-run]`, which repairs stable integration from the active release without dependency reinstall or pointer change.
- Produces: `install-assistant-tools.machine.uninstall [--purge] [--dry-run]`, which applies manifest-v2 ownership cleanup.

**Execution rule:** Treat slices 4A-4C as separate red/green/review gates. Add or enable only the current slice's assertions, run its focused command, and commit only its file subset before continuing. Files shared by two slices are staged by exact diff hunk.

#### Slice 4A: Bind generated entrypoints and hooks to managed Python

- [ ] **Step 1: Resolve any overlapping hook edits before implementation**

Before touching either hook file, inspect the current diff. If another change overlaps, build on it when the intent is clear; otherwise stop for the user to commit or hand it off. This is an implementation gate, not authorization to reset or stash those files.

- [ ] **Step 2: Write the failing Slice 4A entrypoint tests**

Require all generated launchers to use the stable resolver, which reads and validates `current.json` before re-executing the active managed interpreter:

```python
runtime_python = Path("/Users/tester/.local/share/famulus/runtime/releases/r1/venv/bin/python")
dispatcher = installer.install_dispatcher_launcher(
    repo_root, bin_dir, dry_run=False, paths=paths
)
content = dispatcher.path.read_text(encoding="utf-8")
assert str(paths.uv_bin) in content
assert str(paths.runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py") in content
assert str(runtime_python) not in content
assert "/usr/bin/env python3" not in content.splitlines()[0]
```

Require generated Claude/Codex hook bindings to use the stable bootstrap resolver rather than an active release path. Add a static plugin-hook test proving the tiny shim can start without system Python and re-exec the active managed interpreter before importing `llmhooks`.

Add a launcher regression test that searches installed configuration and command arguments for obsolete vendor/cache roots such as `openai-bundled/famulus`, the bootstrap checkout, and old release IDs. Plugin-mode discovery must derive from `RuntimePointer.runtime_source`; development mode may use only the repo root recorded by the installer.

Prepare, but do not enable until Slice 4B, the mode-boundary assertions below. Snapshot the complete `$CODEX_HOME` and `$CLAUDE_HOME` trees, including file bytes and symlink targets, before plugin-mode launcher installation:

```python
def snapshot_tree(root: Path) -> dict[Path, tuple[str, bytes | str]]:
    snapshot: dict[Path, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        else:
            snapshot[relative] = ("dir", b"")
    return snapshot


codex_before = snapshot_tree(codex_home)
claude_before = snapshot_tree(claude_home)
```

Plugin mode must copy the selected profile into `paths.launcher_profile_root`, leave both complete host snapshots unchanged, and construct Codex arguments from the copied TOML without `--profile`:

```python
_agent_launchers.run(
    plugin_mode=True,
    agents=("assistant",),
    codex_home=codex_home,
    claude_home=claude_home,
    launcher_profile_root=paths.launcher_profile_root,
)

assert (paths.launcher_profile_root / "assistant.config.toml").is_file()
assert snapshot_tree(codex_home) == codex_before
assert snapshot_tree(claude_home) == claude_before

command = build_codex_command(
    "assistant",
    [],
    plugin_mode=True,
    launcher_profile_path=paths.launcher_profile_root / "assistant.config.toml",
    model_instructions_file=installed_agent_root / "assistant.md",
)
assert "--profile" not in command
assert "--config" in command
assert any("model_instructions_file=" in arg for arg in command)
```

Add a launcher-closure test proving that an empty optional selection still installs the delegated command:

```python
assert launcher_closure((), install_invoke_skill=True) == ("assistant",)
assert launcher_closure(("collab", "assistant"), install_invoke_skill=True) == (
    "assistant", "collab"
)
```

Run the installer with no optional agents and assert `dispatcher`, `invoke-skill`, `assistant`, `_agent_launch.py`, and the platform wrapper all share the effective bin directory. Then execute `invoke-skill --help` with that directory as the complete test PATH; it must not fail with `Command not found: assistant`.

Development mode must keep the live profile under `$CODEX_HOME` and select it by name:

```python
_agent_launchers.run(
    plugin_mode=False,
    agents=("assistant",),
    codex_home=codex_home,
    repo_root=repo_root,
)

assert (codex_home / "assistant.config.toml").exists()
assert build_codex_command("assistant", [], plugin_mode=False)[:3] == [
    "codex", "--profile", "assistant"
]
```

Add `tests/test_officina_codex_profile.py` cases for strings, booleans, arrays, nested tables, and a quoted dotted-key segment such as `[notice.model_migrations]` with key `"gpt-5.4"`. The flattened assignment must preserve that segment as `notice.model_migrations."gpt-5.4"`, not reinterpret the period as another table boundary.

- [ ] **Step 3: Run the Slice 4A tests and verify RED**

Run: `python3 -m pytest -q tests/test_officina_codex_profile.py skills/install-assistant-tools/tests/test_install_launcher.py skills/install-assistant-tools/tests/test_agent_launch.py skills/install-assistant-tools/tests/test_dev_link_hooks.py hooks/tests/test_inject_dispatcher_context.py`

Expected: failures because launcher installers capture `sys.executable`, hook bindings hardcode `python3`, plugin hook JSON invokes the full hook directly, and profile flattening is absent.

- [ ] **Step 4: Make interpreter binding explicit**

Remove launcher-generation reads of ambient `sys.executable`. Install the first resolver immutably as `runtime_root/bootstrap/resolvers/v1/launch.py`; never edit a resolver version in place. Public POSIX and Windows shims invoke it with the logical form `<uv> run --managed-python --python 3.11 --no-project -- python <resolver> <logical-command> [args...]`, with `UV_PYTHON_INSTALL_DIR` set to the stable Famulus Python directory. The resolver strictly loads `current.json`, accepts only an allow-listed logical command, constructs a cwd-independent entrypoint from the active source, replaces ambient `PYTHONPATH` with the release's required first-party roots, and uses `os.execve` to start that release's interpreter. Other environment entries pass through unchanged and are never logged. A future pointer protocol installs a new versioned resolver first, proves it reads both old/new pointer schemas, and only then atomically replaces individual public shims; old resolver versions remain until no manifest owner references them. Dispatcher continues using `sys.executable` internally, which guarantees every dispatcher-launched Python machine interface uses the active managed environment. Test invocation from unrelated working directories plus POSIX shell, PowerShell, and `cmd.exe` argument forwarding, including spaces and metacharacters.

The static plugin hook remains a pre-dispatcher exception. Keep it dependency-light and route it through the same stable resolver/current pointer before importing `llmhooks`. If no valid active pointer exists, emit the current dispatcher-missing guidance and exit successfully. Remove any generated or fallback lookup rooted in a stale vendor-specific plugin cache; development fallback may use only the repo root recorded by the installer.

Implement `codex_config_overrides` with `tomllib` from the managed Python. Recursively flatten nested mappings, quote any non-bare TOML key segment, serialize scalar and array values as valid TOML literals, and replace the source profile's `model_instructions_file` with the absolute installed instruction path supplied by the caller. Reject unsupported values before starting Codex. The launcher expands the returned assignments into repeated `--config`, `<assignment>` pairs and then appends user-supplied Codex arguments.

- [ ] **Step 5: Pass the Slice 4A review gate**

Re-run the Step 3 command and require GREEN. Review and commit only the managed-interpreter, hook-shim, stale-cache lookup, and TOML-serialization hunks with message `feat: bind Famulus entrypoints to managed Python`.

#### Slice 4B: Stabilize public commands and plugin/developer profiles

- [ ] **Step 1: Enable the Slice 4B mode and command-closure tests**

Enable the prepared complete-host snapshots, plugin/developer profile assertions, launcher-closure assertions, stable-bin assertions, and denied-host-target parameterization. Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_launchers.py skills/install-assistant-tools/tests/test_agent_launch.py skills/install-assistant-tools/tests/test_dev_link.py skills/install-assistant-tools/tests/test_dev_link_hooks.py`

Expected: failures because plugin mode mutates host configuration homes, an empty optional selection omits `assistant`, and stable launcher-owned profile paths are not yet enforced.

- [ ] **Step 2: Move plugin-mode workers and public commands**

Implement these defaults:

```python
if plugin_mode:
    worker_root = paths.worker_root
else:
    worker_root = repo_root / "workers"

bin_dir = explicit_bin_dir or paths.user_bin
```

Preserve Task 3's universal closure and add any optional selected launchers into the same effective bin directory. Normalize the requested agent list through `launcher_closure`; `assistant` remains an implementation prerequisite of `invoke-skill`, not an optional user selection. Re-running launcher installation must repair any missing closure member, and the phase must fail rather than report `invoke-skill` usable when a required member is absent or outside the effective PATH.

The defaults are exact: macOS/Linux installs public commands under `~/.local/bin`; Windows installs them under `%LOCALAPPDATA%\Famulus\bin`. Apply an explicit `--bin-dir` consistently to launcher creation, PATH wiring, hook bindings, scheduler command generation, manifest records, and uninstall. Never derive a public launcher destination from the checkout, plugin cache, current working directory, Documents, Desktop, or Downloads.

- [ ] **Step 3: Split plugin and development profile installation**

In plugin mode, copy each selected agent TOML and any Claude launcher resource into `launcher_profile_root`, record the copies as launcher-owned state, and perform no launcher-related write under either `$CODEX_HOME` or `$CLAUDE_HOME`. Generate the launcher with its stable launcher-resource paths and installed agent-instruction path. At runtime it loads the TOML and invokes Codex with repeated `--config` overrides; for Claude it builds the agent definition inline from launcher-owned resources. It never sets `CODEX_HOME` or `CLAUDE_HOME`, because those variables also control user-owned authentication, sessions, logs, skills, and base configuration.

In development mode, preserve live-checkout behavior: install or link `$CODEX_HOME/<agent>.config.toml` according to the existing conflict policy, keep its instruction path live against the selected checkout, and invoke `codex --profile <agent>`. Do not install the Codex profile TOML into `$CLAUDE_HOME`; Claude continues receiving its agent definition through its existing launcher-owned mechanism.

Treat a `PermissionError` from any development host-integration target as a failure of only that phase. This includes Codex profiles/config/hooks, Claude settings/hooks, host-directory links, and shell integration owned by the development bridge. Report the exact target and operation, mark the phase rerunnable, print the targeted development-integration retry command, and explicitly say to authorize the normal-user retry rather than use `sudo`. The completed managed runtime, dispatcher floor, and plugin-owned launcher resources remain valid.

Add parameterized regressions that deny one target at a time under `$CODEX_HOME`, `$CLAUDE_HOME`, and the development hook/config destinations. Each case must return nonzero, identify only the denied target, preserve already completed non-host installation state, print the same targeted normal-user retry route, and contain no `sudo`, administrator, or root-elevation suggestion.

- [ ] **Step 4: Pass the Slice 4B review gate**

Re-run the Step 1 command and require GREEN. Commit only stable-bin, command-closure, launcher-resource, plugin/developer separation, and permission-reporting hunks with message `feat: separate plugin and developer launch state`.

#### Slice 4C: Migrate manifest v2 and make uninstall reference-safe

- [ ] **Step 1: Write failing ownership and uninstall tests**

Add lifecycle cases for v1-to-v2 migration, unknown legacy entries, each ownership kind, two installations sharing one active/previous release, default credential preservation, explicit `--purge`, interrupted migration, failed update rollback, and user-owned host configuration. Require uninstall to remove only the selected installation's ownership and retain every resource still referenced by another owner.

Add dispatcher contract tests for update, repair, and uninstall. Update rejects a relative/missing source, a source without byte-matching packaged install/dependency metadata, or a source resolving inside the active release. Repair reads only the active pointer/manifest and cannot change either. All three recover an unfinished activation journal before acting, support `--dry-run` without writes, and serialize concurrent lifecycle operations through the same lock.

- [ ] **Step 2: Implement manifest v2, legacy migration, and ownership-aware uninstall**

Use this normalized shape (additional typed metadata may be added, but ownership cannot be implicit):

```json
{
  "schema_version": 2,
  "active_release": "r2",
  "previous_release": "r1",
  "installations": {
    "plugin": {"mode": "plugin", "resources": ["runtime:r2", "launcher:dispatcher"]}
  },
  "resources": {
    "runtime:r2": {
      "kind": "runtime_release",
      "path": "/absolute/famulus/runtime/releases/r2",
      "owners": ["plugin"]
    }
  }
}
```

Record uv/bootstrap assets, current/previous releases, `current.json`, generated launchers, launcher profiles, worker/config/state roots, scheduler registrations, legacy-path replacements, and credential references as separate resources. A credential resource stores only a secret-store reference and is `purge_only`; never serialize secret values.

Manifest validation requires exact bidirectional references: every installation resource ID exists and names that installation in `owners`, every owner names an installation that references the resource, active/previous release IDs resolve to runtime resources, non-secret filesystem paths are absolute/canonical and unique, and purge-only resources cannot contain filesystem deletion targets. Reject the whole manifest on any mismatch.

Load v1 without mutating it, map every known entry to a typed v2 resource owned by `legacy-install`, preserve unknown entries in a lossless legacy section, validate references and paths, then atomically write v2. Migration of Documents launchers/PATH entries, plugin-cache workers/state, mutable `skills/recurring-tasks/{jobs.yaml,logs}` and `skills/email-triage/{state,triage.log}` data, and obsolete plist/systemd/Task Scheduler entries is install-new/copy-state, verify-new, remove-old. Service-owned importers validate copied mutable data before new scheduling is enabled. Remove an old path only when v1 proves installer ownership; preserve ambiguous/user-owned paths in place, report them, and never delete a checkout file merely because it resembles legacy state.

Uninstall removes the selected installation from each resource's `owners`, deletes only ownerless installer-owned resources, and never follows paths outside validated Famulus roots. It first disables/removes owned scheduler registrations, then public shims/hooks, then unreferenced config/state/profile resources; only after no executable entrypoint remains may a final-install uninstall remove `current.json`, active/previous releases, and uv/bootstrap assets. Preserve credentials unless `--purge` is selected; purge calls the secret-store API and removes the registry reference only when no other owner remains. Never remove user-owned Codex configuration merely because a plugin-mode launcher profile was removed. Keep the active pointer valid throughout executable cleanup, and refuse to delete the active or previous release while referenced.

- [ ] **Step 3: Run focused ownership and lifecycle tests**

Run: `python3 -m pytest -q skills/install-assistant-tools/tests/test_uninstall.py skills/install-assistant-tools/tests/test_e2e_lifecycle.py`

Expected: all pass, including lossless v1 migration, interrupted-write recovery, failed-update rollback, reference preservation, purge boundaries, and exact ownership cleanup.

- [ ] **Step 4: Commit after review**

Stage only manifest/state/uninstall hunks and their tests. Commit with message `feat: track launcher ownership for uninstall`.

---
