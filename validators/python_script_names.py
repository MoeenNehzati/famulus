"""Validate importable Python script filenames for skill runtime modules.

Dispatcher machine interfaces execute Python runtimes as modules, so skill
runtime files under ``scripts/`` must have importable module names. Use
underscores, not hyphens, in Python filenames.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SKIP_PARTS = {"tests", "validators", "__pycache__", ".git", ".claude-plugin", ".codex-plugin", "logs"}


def _tracked_files(repo_root: Path) -> set[Path] | None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return {repo_root / path for path in result.stdout.splitlines()}


def _iter_skill_script_python_files(repo_root: Path):
    tracked = _tracked_files(repo_root)
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for path in skills_root.glob("*/scripts/*.py"):
        if tracked is not None and path not in tracked:
            continue
        rel_path = path.relative_to(repo_root)
        if len(rel_path.parts) >= 2 and rel_path.parts[1] == ".system":
            continue
        if any(part in _SKIP_PARTS for part in rel_path.parts):
            continue
        yield path


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path in _iter_skill_script_python_files(repo_root):
        if "-" in path.stem:
            rel_path = path.relative_to(repo_root)
            suggested = path.name.replace("-", "_")
            errors.append(
                f"{rel_path}: Python script filenames under skills/*/scripts must use "
                f"underscores for importable module names; rename to `{suggested}`"
            )
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Python script filename violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
