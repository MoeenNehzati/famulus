"""Validate that shared content contains no platform-specific references.

A file whose own filename names a host or operating system (case-insensitive
substring match, e.g. ``codex_parser.py`` or ``windows.py``) is allowed to
mention that platform's forbidden terms -- the filename itself is the visible
signal that this one file is intentionally platform-specific, while every
other shared file must stay generic. Filename checks are case-insensitive, and
content checks use each platform group's own pattern policy (for example,
``CLAUDE_HOME`` is treated the same as ``claude_home`` or ``Claude Code``).
``__init__.py`` is always exempt too: it is the conventional aggregation seam
that statically imports platform-specific files and re-exports a generic
collection (e.g. ``parsers = [...]``) for everything else to consume without
naming any platform itself.

This lets a module hold real per-platform logic without a blanket per-skill
exemption: put platform-specific parts in a file named after that platform
(plus the __init__.py that wires them together), keep everything else
(SKILL.md, blueprint.yaml, first-party shared packages, and any
generically-named script) free of platform references.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_PLATFORM_GROUPS: dict[str, tuple[set[str], re.Pattern[str]]] = {
    "claude": ({"claude"}, re.compile(r"(?i:(\.claude|claude))")),
    "codex": ({"codex"}, re.compile(r"(?i:(\.codex|codex))")),
    "linux": ({"linux"}, re.compile(r"(?i:\b(linux)\b)")),
    "osx": ({"osx", "macos", "darwin"}, re.compile(r"(?i:\b(osx|macos|darwin)\b)")),
    "windows": ({"windows", "win32"}, re.compile(r"\b(Windows|win32)\b")),
}

_ALWAYS_EXEMPT_FILENAMES = {"__init__.py"}

_CHECK_ROOTS = ["skills", "references", "agents", "CLAUDE.md", "src/officina"]
_EXCLUDED_PARTS = {"tests", "validators", ".git", ".claude-plugin", ".codex-plugin"}
_EXCLUDED_PATHS = {
    Path("references/skill-standards/skill-guidelines.md"),
    Path("skills/install-assistant-tools"),
    Path("skills/latex-workshop"),
    Path("skills/recurring-tasks"),
}
_PLATFORM_METADATA_TOOLING_PATHS = {
    Path("skills/skill-maker/_rtx/_blueprint_syncer.py"),
}
_HOST_PATTERN = re.compile(r"(?i:(\.claude|claude|\.codex|codex))")
_PLATFORM_METADATA_LINE_RE = re.compile(
    r"^\s*(?:#\s*)?[\"']?(?:linux|macos|windows)[\"']?\s*:\s*(?:true|false|\{)"
)


def _is_allowed_platform_metadata_line(rel_path: Path, line: str) -> bool:
    """Allow explicit OS support metadata without weakening host-name checks."""
    if _HOST_PATTERN.search(line):
        return False
    if rel_path.name.endswith("blueprint.yaml") and _PLATFORM_METADATA_LINE_RE.search(line):
        return True
    if rel_path == Path("references/blueprint/runtime_dependencies.json"):
        return _PLATFORM_METADATA_LINE_RE.search(line) is not None
    if rel_path.parts[:2] == ("references", "blueprint"):
        return True
    if rel_path in _PLATFORM_METADATA_TOOLING_PATHS:
        return True
    return False


def _forbidden_pattern_for(path: Path) -> re.Pattern[str] | None:
    """Forbidden-term pattern for this file, exempting any host named in
    the file's own filename, and exempting __init__.py unconditionally.
    Returns None if nothing is left to forbid for this file."""
    name_lower = path.name.lower()
    if name_lower in _ALWAYS_EXEMPT_FILENAMES:
        return None
    active = [
        pattern
        for aliases, pattern in _PLATFORM_GROUPS.values()
        if not any(alias in name_lower for alias in aliases)
    ]
    if not active:
        return None
    return re.compile("|".join(p.pattern for p in active))


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
            rel = path.relative_to(repo_root)
            if _is_allowed_platform_metadata_line(rel, line):
                continue
            if pattern.search(line):
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
