"""Validate that shared content contains no platform-specific references."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN = re.compile(r"(\.claude|\.codex|Claude|Codex|claude|codex)")

_CHECK_ROOTS = ["skills", "references", "agents", "CLAUDE.md"]
_EXCLUDED_PARTS = {"tests", ".git", ".claude-plugin", ".codex-plugin"}
_EXCLUDED_PATHS = {
    Path("skills/install-assistant-tools"),
    Path("skills/recurring-tasks"),
}


def _tracked_files(repo_root: Path) -> set[Path] | None:
    """Return files tracked by git, or None if not in a git repo (no filter applied)."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return {repo_root / p for p in result.stdout.splitlines()}


def _iter_files(repo_root: Path):
    tracked = _tracked_files(repo_root)
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if root.is_file():
            if tracked is None or root in tracked:
                yield root
            continue
        if not root.is_dir():
            continue
        for child in root.rglob("*"):
            if not child.is_file():
                continue
            if tracked is not None and child not in tracked:
                continue
            rel_parts = child.relative_to(repo_root).parts
            if any(part in _EXCLUDED_PARTS for part in rel_parts):
                continue
            rel_path = child.relative_to(repo_root)
            if any(rel_path == ep or ep in rel_path.parents for ep in _EXCLUDED_PATHS):
                continue
            yield child


def validate(repo_root: Path) -> list[str]:
    """Return error strings for every platform-specific reference found in shared content."""
    errors: list[str] = []
    for path in _iter_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN.search(line):
                rel = path.relative_to(repo_root)
                errors.append(f"{rel}:{lineno}: {line.strip()}")
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Platform-specific references found in shared content:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
