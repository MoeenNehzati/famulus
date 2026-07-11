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

REPO_ROOT = Path(__file__).resolve().parents[3]


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def github_owner_repo(repo_root: Path = REPO_ROOT) -> str:
    """`owner/repo` shorthand, read from the plugin manifest's `repository` URL."""
    repository = read_json(repo_root / ".claude-plugin" / "plugin.json")["repository"]
    return urlparse(repository).path.strip("/")


def expected_skills(repo_root: Path = REPO_ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "skills"],
        cwd=repo_root,
        capture_output=True,
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
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
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
