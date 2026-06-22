#!/usr/bin/env python3
"""Ensure shared skill content stays platform-neutral."""

from __future__ import annotations

import re
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
FORBIDDEN = re.compile(r"(\.claude|\.codex|Claude|Codex|claude|codex)")


def iter_files(path: Path):
    if path.is_file():
        yield path
        return
    if not path.exists():
        return
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        rel_parts = child.relative_to(REPO_ROOT).parts
        if any(part in EXCLUDED_PARTS for part in rel_parts):
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
