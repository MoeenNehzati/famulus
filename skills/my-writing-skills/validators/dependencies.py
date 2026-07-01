"""Validate SKILL.md dependency blocks, depends_on_skills sidecars, and parent-path rules."""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Skill names that are allowed to reference ../../.githooks/ in addition to ../references and ../../tools
_EXTENDED_PARENT_EXCEPTIONS = {"update-skill-guidelines"}

# Pattern: a relative path segment that goes upward (../.. or higher)
_PARENT_PATH_RE = re.compile(r"(?:^|(?<=[^A-Za-z0-9_./~\-]))(?:\.\.?/)*\.\./")
_ALLOWED_DIRS_BASE = re.compile(
    r"(?:^|(?<=[^A-Za-z0-9_./~\-]))(?:\.\.?/)*\.\./(?:references|tools)(?:/|[ \t`'\"]|$)"
)
_ALLOWED_DIRS_EXTENDED = re.compile(
    r"(?:^|(?<=[^A-Za-z0-9_./~\-]))(?:\.\.?/)*\.\./(?:references|tools|\.githooks)(?:/|[ \t`'\"]|$)"
)

_DEPRECATED_MARKERS_RE = re.compile(r"^(Sub-skills to invoke:|Depends on:)", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

# Matches "Dependencies: none" or "Dependencies:" followed by a list
_DEPS_NONE_RE = re.compile(r"^Dependencies:\s*none\s*$", re.MULTILINE)
_DEPS_BLOCK_START_RE = re.compile(r"^Dependencies:\s*$", re.MULTILINE)


def _load_sidecar(path: Path) -> list[str]:
    """Parse depends_on_skills: strip comments, blank lines, sort."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = re.sub(r"\s*#.*$", "", raw).strip()
        if stripped:
            lines.append(stripped)
    return sorted(lines)


def _extract_dep_block(text: str) -> list[str] | None:
    """Return sorted deps from Dependencies block, or None if block is absent."""
    if _DEPS_NONE_RE.search(text):
        return []

    m = _DEPS_BLOCK_START_RE.search(text)
    if not m:
        return None  # missing block

    deps: list[str] = []
    pos = m.end()
    # Skip the newline immediately following "Dependencies:\n"
    lines = text[pos:].splitlines()
    if lines and lines[0].strip() == "":
        lines = lines[1:]
    for line in lines:
        if re.match(r"^\s*-\s*", line):
            dep = re.sub(r"^\s*-\s*", "", line).strip()
            if dep:
                deps.append(dep)
        elif line.strip() == "":
            break
        else:
            break
    return sorted(deps)


def _strip_frontmatter_and_deps(text: str) -> str:
    """Return SKILL.md text without frontmatter and without the Dependencies block."""
    # Strip frontmatter
    fm_match = _FRONTMATTER_RE.match(text)
    if fm_match:
        text = text[fm_match.end():]

    # Strip Dependencies block
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^Dependencies:\s*none\s*$", line):
            i += 1
            continue
        if re.match(r"^Dependencies:\s*$", line):
            i += 1
            # skip list items and trailing blank
            while i < len(lines):
                l = lines[i]
                if re.match(r"^\s*-\s*", l):
                    i += 1
                elif l.strip() == "":
                    i += 1
                    break
                else:
                    break
            continue
        result.append(line)
        i += 1
    return "".join(result)


def _word_boundary_mentions(text: str, skill_name: str) -> bool:
    """Return True if skill_name appears as a whole token in text."""
    escaped = re.escape(skill_name)
    pattern = re.compile(
        r"(?:^|(?<=[^A-Za-z0-9:_\-]))" + escaped + r"(?=[^A-Za-z0-9:_\-]|$)"
    )
    return bool(pattern.search(text))


def _validate_parent_paths(skill_file: Path, skill_name: str) -> list[str]:
    errors: list[str] = []
    allowed = _ALLOWED_DIRS_EXTENDED if skill_name in _EXTENDED_PARENT_EXCEPTIONS else _ALLOWED_DIRS_BASE
    for lineno, line in enumerate(skill_file.read_text(encoding="utf-8").splitlines(), start=1):
        if _PARENT_PATH_RE.search(line) and not allowed.search(line):
            errors.append(
                f"{skill_file}:{lineno}: parent paths in SKILL.md may only point to "
                f"../references or ../../tools: {line.strip()}"
            )
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    # Collect all known skill names: local directories + names from all depends_on_skills files
    skill_name_set: set[str] = {p.name for p in skills_root.iterdir() if p.is_dir()}
    for sidecar in skills_root.glob("*/depends_on_skills"):
        skill_name_set.update(_load_sidecar(sidecar))
    skill_names = sorted(skill_name_set)

    skill_files = sorted(skills_root.glob("*/SKILL.md"))

    # Pass 1: parent-path checks
    for skill_file in skill_files:
        skill_name = skill_file.parent.name
        errors.extend(_validate_parent_paths(skill_file, skill_name))

    # Pass 2: dependency checks
    for skill_file in skill_files:
        skill_dir = skill_file.parent
        skill_name = skill_dir.name
        dependency_file = skill_dir / "depends_on_skills"

        text = skill_file.read_text(encoding="utf-8")

        # Check for deprecated markers
        for m in _DEPRECATED_MARKERS_RE.finditer(text):
            lineno = text[: m.start()].count("\n") + 1
            errors.append(
                f"{skill_file}:{lineno}: {m.group(0)} — "
                f"use the Dependencies block plus depends_on_skills"
            )

        # Extract Dependencies block
        skill_deps = _extract_dep_block(text)
        if skill_deps is None:
            errors.append(f"{skill_file}: missing Dependencies block")

        if not dependency_file.exists():
            errors.append(f"{skill_file}: missing {dependency_file}")
            continue

        sidecar_deps = _load_sidecar(dependency_file)

        if skill_deps is not None and skill_deps != sidecar_deps:
            errors.append(
                f"{skill_file}: Dependencies block does not match {dependency_file}"
            )

        # Extract exact skill-name mentions in body
        body = _strip_frontmatter_and_deps(text)
        other_skills = sorted(s for s in skill_names if s != skill_name)
        body_mentions = sorted(s for s in other_skills if _word_boundary_mentions(body, s))

        if body_mentions != sidecar_deps:
            errors.append(
                f"{skill_file}: exact skill-name mentions in SKILL.md body "
                f"do not match {dependency_file}"
            )

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid skill dependencies.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
