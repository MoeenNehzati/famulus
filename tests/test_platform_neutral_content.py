#!/usr/bin/env python3
"""Ensure shared skill content stays platform-neutral."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_ROOTS = [
    REPO_ROOT / "skills",
    REPO_ROOT / "references",
    REPO_ROOT / "agents",
    REPO_ROOT / "CLAUDE.md",
]
EXCLUDED_PARTS = {
    "tests",
    ".git",
    ".claude-plugin",
    ".codex-plugin",
}
EXCLUDED_PATHS = {
    Path("skills/install-assistant-tools"),
}
FORBIDDEN = re.compile(r"(\.claude|\.codex|Claude|Codex|claude|codex)")


def git_tracked_files(directory: Path) -> list[Path]:
    """Return files tracked by git under directory (respects .gitignore)."""
    rel = directory.relative_to(REPO_ROOT)
    result = subprocess.run(
        ["git", "ls-files", str(rel)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line]


def iter_files(path: Path):
    if path.is_file():
        yield path
        return
    if not path.exists():
        return
    for child in git_tracked_files(path):
        if not child.is_file():
            continue
        rel_parts = child.relative_to(REPO_ROOT).parts
        if any(part in EXCLUDED_PARTS for part in rel_parts):
            continue
        rel_path = child.relative_to(REPO_ROOT)
        if any(rel_path == excluded or excluded in rel_path.parents for excluded in EXCLUDED_PATHS):
            continue
        yield child


def main() -> int:
    errors: list[str] = []
    for root in CHECK_ROOTS:
        for path in iter_files(root):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if FORBIDDEN.search(line):
                    rel = path.relative_to(REPO_ROOT)
                    errors.append(f"{rel}:{lineno}: {line.strip()}")

    if errors:
        print("Platform-specific references found in shared content:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
