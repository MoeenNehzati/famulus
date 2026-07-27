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
(SKILL.md, first-party shared packages, and any generically-named script) free
of platform references. Validated blueprint files are descriptive graph
artifacts rather than module content; once the canonical graph validates them,
this text guard scans their owned files instead of their declaration metadata.
Frozen version-4 blueprint fixtures retain the line-level checks below.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.blueprint_graph import (  # noqa: E402
    BlueprintGraphError,
    load_repository_blueprint_graph,
)
from officina.common.blueprint_inventory import BlueprintInventoryError  # noqa: E402

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
    Path("references/skill-standards/skill-guidelines.standard.yaml"),
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
REQUIRES_BLUEPRINT_GRAPH = True
BLUEPRINT_GRAPH_OPTIONAL = True


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


def _validated_blueprint_paths(graph: object) -> frozenset[Path]:
    """Return blueprint files already validated by the canonical graph."""
    return frozenset(
        node.blueprint_path.resolve()
        for node in graph.nodes.values()
    )


def _canonical_blueprint_paths(repo_root: Path) -> frozenset[Path]:
    """Load blueprint files validated by the canonical version-5 graph."""

    schema_root = repo_root / "references" / "blueprint"
    try:
        graph = load_repository_blueprint_graph(
            repo_root,
            schema_root=schema_root,
        )
    except (
        BlueprintGraphError,
        BlueprintInventoryError,
        OSError,
        UnicodeError,
    ):
        return frozenset()
    return _validated_blueprint_paths(graph)


def _iter_files(repo_root: Path, *, excluded_blueprints: frozenset[Path]):
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if root.is_file():
            if root.resolve() not in excluded_blueprints:
                yield root
            continue
        if not root.is_dir():
            continue
        for child in root.rglob("*"):
            if not child.is_file():
                continue
            rel_parts = child.relative_to(repo_root).parts
            if any(part in _EXCLUDED_PARTS for part in rel_parts):
                continue
            rel_path = child.relative_to(repo_root)
            if any(rel_path == ep or ep in rel_path.parents for ep in _EXCLUDED_PATHS):
                continue
            if child.resolve() in excluded_blueprints:
                continue
            yield child


def _validate(
    repo_root: Path,
    excluded_blueprints: frozenset[Path],
) -> list[str]:
    """Return error strings for every platform-specific reference found in shared content."""
    repo_root = repo_root.resolve()
    errors: list[str] = []
    for path in _iter_files(repo_root, excluded_blueprints=excluded_blueprints):
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
                errors.append(f"{rel.as_posix()}:{lineno}: {line.strip()}")
    return errors


def validate_with_graph(repo_root: Path, graph: object) -> list[str]:
    return _validate(repo_root, _validated_blueprint_paths(graph))


def validate(repo_root: Path) -> list[str]:
    return _validate(repo_root, _canonical_blueprint_paths(repo_root))


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
