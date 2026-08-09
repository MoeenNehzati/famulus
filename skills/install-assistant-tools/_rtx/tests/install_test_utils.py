"""Shared helpers for cross-platform installation tests.

These helpers keep the install tests explicit and assertion-heavy rather than
just command-success checks. They centralize temporary-environment creation,
CLI invocation, expected-skill discovery, and symlink capability checks.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from test_support.git_repository import GitTestRepository

REPO_ROOT = Path(__file__).resolve().parents[4]

DISPATCHER_CONTEXT_MARKERS = [
    "## Skill dispatcher",
    "treat `scripts/` as private",
    "dispatcher --caller-skill <skill> [--dry-run] <interface-id> <arguments>",
    "Dry-run prints compiled argv without gateway execution or stdin reads.",
    "Dispatcher adds fixed arguments; do not supply them.",
]


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_default_bin_dir_matches_famulus_paths(default_bin_dir, home: Path) -> None:
    """Assert a module's `default_bin_dir(home=...)` matches FamulusPaths.user_bin.

    Shared by test_scaffold.py, test_launchers.py, and test_uninstall.py,
    each of which re-exports its own `default_bin_dir` (imported from
    `_fs_links`) into its own module namespace — the per-module call still
    confirms that re-export, this just centralizes the assertion itself.
    """
    from officina.common.famulus_paths import resolve_famulus_paths

    expected = resolve_famulus_paths(platform=sys.platform, home=home).user_bin
    result = default_bin_dir(home=home)

    assert result == expected
    assert "Documents" not in str(result)


def github_owner_repo(repo_root: Path = REPO_ROOT) -> str:
    """`owner/repo` shorthand, read from the plugin manifest's `repository` URL."""
    repository = read_json(repo_root / ".claude-plugin" / "plugin.json")["repository"]
    return urlparse(repository).path.strip("/")


def expected_skills(repo_root: Path = REPO_ROOT) -> list[str]:
    result = GitTestRepository(repo_root).git(
        "ls-files",
        "-z",
        "--",
        "skills",
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        skill_names: set[str] = set()
        for rel in result.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
            if not rel:
                continue
            parts = Path(rel).parts
            if (
                len(parts) == 3
                and parts[0] == "skills"
                and parts[2] == "SKILL.md"
                and (repo_root / rel).is_file()
            ):
                skill_names.add(parts[1])
        return sorted(skill_names)

    return sorted(
        skill_dir.name
        for skill_dir in (repo_root / "skills").iterdir()
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file()
    )


def run_command(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    # Windows: npm installs CLIs as .cmd shims, which CreateProcess won't
    # find from a bare name — resolve through PATH explicitly.
    resolved = shutil.which(cmd[0], path=env.get("PATH") if env is not None else None)
    if resolved is not None:
        cmd = [resolved, *cmd[1:]]
    result = subprocess.run(
        cmd,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        joined = " ".join(cmd)
        raise AssertionError(
            f"Command failed with exit code {result.returncode}: {joined}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def python_test_env(tmp_root: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPYCACHEPREFIX"] = str(tmp_root / "pycache")
    if extra:
        env.update(extra)
    return env


def can_create_symlink() -> bool:
    if not hasattr(os, "symlink"):
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="symlink-check-") as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            dst = tmp_path / "dst"
            src.write_text("ok", encoding="utf-8")
            dst.symlink_to(src)
            return dst.is_symlink() and dst.resolve() == src.resolve()
    except OSError:
        return False


def copy_repo_tree(destination: Path, repo_root: Path = REPO_ROOT) -> None:
    """Copy only git-tracked content, so tests see exactly what a fresh
    checkout (e.g. CI) sees — not untracked runtime artifacts (workers/,
    generated env.sh, logs) that happen to exist in a local working tree."""
    result = GitTestRepository(repo_root).git("ls-files", "-z")
    for rel in result.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
        if not rel:
            continue
        src = repo_root / rel
        if not src.is_file():
            continue
        dst = destination / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        shutil.copymode(src, dst)


def launcher_path(bin_dir: Path, agent: str) -> Path:
    if os.name == "nt":
        return bin_dir / f"{agent}.bat"
    return bin_dir / agent


def install_minimum_scaffold(
    installed_path: Path,
    tmp_root: Path,
    *,
    home: Path,
    env: dict[str, str],
) -> Path:
    """Run the packaged scaffold installer and return its generated bin dir."""
    bin_dir = tmp_root / "minimum-scaffold-bin"
    cmd = [
        sys.executable,
        str(installed_path / "skills" / "install-assistant-tools" / "_rtx" / "_install_scaffold.py"),
        "--repo-root",
        str(installed_path),
        "--home",
        str(home),
        "--bin-dir",
        str(bin_dir),
    ]
    if os.name != "nt":
        cmd.extend(["--shell-rc", str(tmp_root / "minimum-scaffold.bashrc")])
    run_command(cmd, env=env)
    return bin_dir


def deploy_managed_uv(home: Path) -> Path | None:
    """Copy the real system uv to the exact machine-local location
    ``officina.common.famulus_paths.resolve_famulus_paths`` assumes for the
    managed uv binary (``<data_root>/tools/uv``), simulating the separately
    scoped uv-bootstrap step a real installer would already have run before
    ``_phase_entry.py`` ever calls ``build_candidate_release``.

    Returns None (deploying nothing) if no real uv is available on this
    machine; callers should skip rather than fail in that case.
    """
    from officina.common.famulus_paths import resolve_famulus_paths

    system_uv = managed_runtime_uv_bin()
    if system_uv is None:
        return None
    paths = resolve_famulus_paths(platform=sys.platform, home=home)
    paths.uv_bin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(system_uv, paths.uv_bin)
    paths.uv_bin.chmod(0o755)
    return paths.uv_bin


def managed_uv_python_install_dir() -> str | None:
    """This machine's real, already-populated uv Python install directory
    (e.g. ``~/.local/share/uv/python``), or None if uv is unavailable.

    Tests that run ``_phase_entry.py`` (and therefore build_candidate_release)
    as a real subprocess with an isolated, sandboxed ``HOME`` must still point
    ``uv venv --python <version>`` at this real, pre-populated interpreter
    store via ``UV_PYTHON_INSTALL_DIR`` -- otherwise uv resolves its Python
    install dir under the isolated HOME and attempts a fresh network download
    of the pinned Python version on every test run, which is slow at best and
    hangs/times out in network-restricted sandboxes at worst.
    """
    system_uv = managed_runtime_uv_bin()
    if system_uv is None:
        return None
    from officina.install.managed_runtime import uv_python_install_dir

    return str(uv_python_install_dir(Path(system_uv)))


def managed_runtime_uv_bin() -> str | None:
    """Real `uv` binary on this machine's PATH, or None if unavailable.

    Callers should skip (not fail) when this is None -- building a real
    managed-runtime release needs a real uv, same as the mocked-vs-real-uv
    split used throughout tests/test_officina_managed_runtime.py and
    tests/test_officina_launcher_entry.py.
    """
    return shutil.which("uv")


def build_minimal_managed_runtime_release(*, home: Path, tmp_root: Path) -> None:
    """Build and activate a real managed-runtime candidate release (empty
    dependency manifest -- no third-party packages needed) under ``home``,
    deploying the dependency-free launcher resolver and its trust sidecar as
    a side effect of build_candidate_release (see officina.install.
    managed_runtime._deploy_resolver). This makes a generated dispatcher
    shim's resolver hop succeed; the release venv still has no `officina`
    package installed (that's a separate, deliberate scope decision -- see
    _install_scaffold.install_python_packages's docstring), so `dispatcher
    --help` still fails, but with a ModuleNotFoundError raised by the release
    interpreter after control has already transferred there, never a
    resolver-side "no such file" or containment error.

    Callers must guard on managed_runtime_uv_bin() first and skip if it is
    None.
    """
    from officina.common.famulus_paths import resolve_famulus_paths
    from officina.install.managed_runtime import build_candidate_release

    uv_bin = managed_runtime_uv_bin()
    assert uv_bin is not None, "build_minimal_managed_runtime_release requires a real uv binary"

    paths = resolve_famulus_paths(platform=sys.platform, home=home)
    manifest = tmp_root / "managed-runtime-empty-manifest.json"
    manifest.write_text(json.dumps({"version": 2, "skills": {}}), encoding="utf-8")
    platform_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    build_candidate_release(
        runtime_root=paths.runtime_root,
        manifest_path=manifest,
        platform=platform_name,
        uv_bin=Path(uv_bin),
        python_version="3.11",
    )


def prepend_path(env: dict[str, str], path: Path) -> dict[str, str]:
    updated = dict(env)
    updated["PATH"] = str(path) + os.pathsep + updated.get("PATH", "")
    return updated


def contains_dispatcher_context(payload: object) -> bool:
    text = json.dumps(payload)
    return all(marker in text for marker in DISPATCHER_CONTEXT_MARKERS)


def _home_env(home: Path) -> dict[str, str]:
    env = {"HOME": str(home)}
    if os.name == "nt":
        # Windows tools resolve the home dir via USERPROFILE, not HOME
        env["USERPROFILE"] = str(home)
    return env


def codex_env(home: Path, codex_home: Path, tmp_root: Path) -> dict[str, str]:
    return python_test_env(
        tmp_root,
        {**_home_env(home), "CODEX_HOME": str(codex_home)},
    )


def claude_env(home: Path, claude_home: Path, tmp_root: Path) -> dict[str, str]:
    return python_test_env(
        tmp_root,
        {**_home_env(home), "CLAUDE_HOME": str(claude_home)},
    )
