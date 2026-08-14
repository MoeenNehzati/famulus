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
    configure_isolated_test_keyring(env, tmp_root)
    if extra:
        env.update(extra)
    return env


def configure_isolated_test_keyring(env: dict[str, str], tmp_root: Path) -> None:
    """Select a persistent, test-only keyring contained by ``tmp_root``.

    Installer acceptance tests launch several real Python subprocesses. A
    process-local fake cannot carry signing material between them, while a
    hosted Linux runner normally has no desktop credential service. Generate
    a tiny keyring backend beside the test's other temporary state so those
    subprocesses exercise the real ``keyring`` API without reading or writing
    the developer's host credential store.
    """
    module_root = tmp_root / "keyring-backend"
    module_root.mkdir(parents=True, exist_ok=True)
    module_path = module_root / "famulus_test_keyring.py"
    module_path.write_text(
        '''from __future__ import annotations

import json
import os
from pathlib import Path

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class IsolatedFileKeyring(KeyringBackend):
    """Minimal file-backed keyring used only by installer acceptance tests."""

    priority = 1

    @property
    def _path(self) -> Path:
        return Path(os.environ["FAMULUS_TEST_KEYRING_PATH"])

    def _read(self) -> dict[str, str]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _write(self, values: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _key(service: str, username: str) -> str:
        return json.dumps([service, username], separators=(",", ":"))

    def get_password(self, service: str, username: str) -> str | None:
        return self._read().get(self._key(service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        values = self._read()
        values[self._key(service, username)] = password
        self._write(values)

    def delete_password(self, service: str, username: str) -> None:
        values = self._read()
        key = self._key(service, username)
        if key not in values:
            raise PasswordDeleteError("credential does not exist")
        del values[key]
        self._write(values)
''',
        encoding="utf-8",
    )
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(module_root) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    env["PYTHON_KEYRING_BACKEND"] = (
        "famulus_test_keyring.IsolatedFileKeyring"
    )
    env["FAMULUS_TEST_KEYRING_PATH"] = str(tmp_root / "keyring" / "secrets.json")


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
    """Pinned release-contract `uv` on PATH, or None if unavailable.

    An older host uv can be installed yet lack the managed Python patch pinned
    by this release. Callers skip unless PATH resolves the exact bootstrap
    version; installed-plugin tests exercise bootstrap of that version
    separately.
    """
    uv_bin = shutil.which("uv")
    if uv_bin is None:
        return None
    result = subprocess.run(
        [uv_bin, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if result.returncode != 0 or result.stdout.split()[:2] != ["uv", "0.11.29"]:
        return None
    return uv_bin


def build_minimal_managed_runtime_release(
    *, home: Path, tmp_root: Path, repo_root: Path | None = None
) -> None:
    """Build and activate a real managed-runtime candidate release under
    ``home``, including Officina and the stable dependency-free resolver.

    When ``repo_root`` is provided, this exercises the verified wheel and
    copied-source provenance path. Otherwise the low-level compatibility path
    installs the package snapshot containing this helper.

    Callers must guard on managed_runtime_uv_bin() first and skip if it is
    None.
    """
    from officina.common.famulus_paths import resolve_famulus_paths
    from officina.install.install_info import load_install_info
    from officina.install.managed_runtime import build_candidate_release

    uv_bin = managed_runtime_uv_bin()
    assert uv_bin is not None, "build_minimal_managed_runtime_release requires a real uv binary"

    paths = resolve_famulus_paths(platform=sys.platform, home=home)
    source_root = repo_root or REPO_ROOT
    info = load_install_info(source_root)
    manifest = source_root / "references" / "blueprint" / "runtime_dependencies.json"
    platform_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    build_candidate_release(
        runtime_root=paths.runtime_root,
        manifest_path=manifest,
        lock_input_path=source_root / "references" / "runtime" / "requirements-core.in",
        lock_path=source_root / "references" / "runtime" / "requirements-core.lock",
        platform=platform_name,
        uv_bin=Path(uv_bin),
        uv_version=info.uv_version,
        python_version=info.managed_python,
        repo_root=repo_root,
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
