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

REPO_ROOT = Path(__file__).resolve().parents[3]


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def expected_skills(repo_root: Path = REPO_ROOT) -> list[str]:
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
    shutil.copytree(repo_root, destination, symlinks=False)


def launcher_path(bin_dir: Path, agent: str) -> Path:
    if os.name == "nt":
        return bin_dir / f"{agent}.bat"
    return bin_dir / agent


def codex_env(home: Path, codex_home: Path, tmp_root: Path) -> dict[str, str]:
    return python_test_env(
        tmp_root,
        {
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
        },
    )


def claude_env(home: Path, claude_home: Path, tmp_root: Path) -> dict[str, str]:
    return python_test_env(
        tmp_root,
        {
            "HOME": str(home),
            "CLAUDE_HOME": str(claude_home),
        },
    )
