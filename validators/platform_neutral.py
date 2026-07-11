"""Validate that shared content contains no platform-specific references.

A file whose own filename names a host (case-insensitive substring match,
e.g. ``codex_parser.py`` or ``claude_parser.py``) is allowed to mention that
host's forbidden terms -- the filename itself is the visible signal that
this one file is intentionally host-specific, while every other shared file
must stay generic. Both the filename check and the content check are
case-insensitive (``CLAUDE_HOME`` is treated the same as ``claude_home`` or
``Claude Code``). ``__init__.py`` is always exempt too: it is the
conventional aggregation seam that statically imports the host-specific
files and re-exports a generic collection (e.g. ``parsers = [...]``) for
everything else to consume without naming any host itself.

This lets a skill hold real per-host logic without a blanket per-skill
exemption: put host-specific parts in a file named after that host (plus
the __init__.py that wires them together), keep everything else (SKILL.md,
blueprint.yaml, and any generically-named script) free of host references.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_FORBIDDEN_TERMS: dict[str, re.Pattern[str]] = {
    "claude": re.compile(r"(\.claude|claude)", re.IGNORECASE),
    "codex": re.compile(r"(\.codex|codex)", re.IGNORECASE),
}

_ALWAYS_EXEMPT_FILENAMES = {"__init__.py"}

_CHECK_ROOTS = ["skills", "references", "agents", "CLAUDE.md"]
_EXCLUDED_PARTS = {"tests", "validators", ".git", ".claude-plugin", ".codex-plugin"}
_EXCLUDED_PATHS = {
    Path("skills/install-assistant-tools"),
    Path("skills/recurring-tasks"),
}


def _forbidden_pattern_for(path: Path) -> re.Pattern[str] | None:
    """Forbidden-term pattern for this file, exempting any host named in
    the file's own filename, and exempting __init__.py unconditionally.
    Returns None if nothing is left to forbid for this file."""
    name_lower = path.name.lower()
    if name_lower in _ALWAYS_EXEMPT_FILENAMES:
        return None
    active = [p for host, p in _FORBIDDEN_TERMS.items() if host not in name_lower]
    if not active:
        return None
    return re.compile("|".join(p.pattern for p in active), re.IGNORECASE)


def _tracked_files(repo_root: Path) -> set[Path] | None:
    """Return files tracked by git, or None if not in a git repo (no filter applied)."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
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
        pattern = _forbidden_pattern_for(path)
        if pattern is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
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
