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
EXCLUDED_PATHS = {
    Path("skills/install-assistant-tools"),
    Path("skills/recurring-tasks"),
}
FORBIDDEN = re.compile(r"(\.claude|\.codex|Claude|Codex|claude|codex)")


def tracked_files() -> set[Path]:
    """Return the set of files tracked by git (relative to REPO_ROOT)."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {Path(line) for line in result.stdout.splitlines() if line}


def iter_files(path: Path, tracked: set[Path]) -> list[Path]:
    if path.is_file():
        rel = path.relative_to(REPO_ROOT)
        return [path] if rel in tracked else []
    if not path.exists():
        return []
    results = []
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        rel_path = child.relative_to(REPO_ROOT)
        if rel_path not in tracked:
            continue
        if any(rel_path == excluded or excluded in rel_path.parents for excluded in EXCLUDED_PATHS):
            continue
        results.append(child)
    return results


def main() -> int:
    tracked = tracked_files()
    errors: list[str] = []
    for root in CHECK_ROOTS:
        for path in iter_files(root, tracked):
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
