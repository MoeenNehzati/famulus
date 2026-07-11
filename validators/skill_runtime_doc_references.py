"""Reject private runtime implementation references in skill-facing Markdown."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from validators.skill_runtime_files import (
    ALLOWED_RTX_SUFFIXES,
    EXEMPT_RTX_DIRNAMES,
    EXEMPT_RTX_FILENAMES,
    RTX_DIR_NAME,
)
from validators.skill_md_body import hand_authored_skill_body

_EXCLUDED_PARTS = {"tests", "assets", ".system"}
_WORD = r"A-Za-z0-9_"
_SUFFIX_ALT = "|".join(re.escape(s) for s in sorted(ALLOWED_RTX_SUFFIXES))
_OLD_RUNTIME_PATH_RE = re.compile(
    rf"(?<!/)scripts/[\w.-]+(?:{_SUFFIX_ALT})(?![{_WORD}])",
    re.IGNORECASE,
)

def _iter_skill_markdown(repo_root: Path):
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for path in sorted(skills_root.glob("*")):
        if not path.is_dir() or path.name == ".system":
            continue
        for md_path in sorted(path.rglob("*.md")):
            rel_path = md_path.relative_to(repo_root)
            if any(part in _EXCLUDED_PARTS for part in rel_path.parts):
                continue
            yield md_path, rel_path


def _runtime_stems_for_skill(skill_dir: Path) -> list[str]:
    rtx_dir = skill_dir / RTX_DIR_NAME
    if not rtx_dir.is_dir():
        return []
    stems: set[str] = set()
    for path in sorted(rtx_dir.rglob("*")):
        if path.is_dir():
            if path.name in EXEMPT_RTX_DIRNAMES:
                continue
            stems.add(path.name)
            continue
        if not path.is_file() or path.suffix not in ALLOWED_RTX_SUFFIXES:
            continue
        if path.name in EXEMPT_RTX_FILENAMES:
            continue
        stems.add(path.stem)
    return sorted(stems)


def _stem_patterns(stem: str) -> list[re.Pattern[str]]:
    public_stem = stem.lstrip("_")
    words = [word for word in public_stem.split("_") if word]
    if len(words) < 2:
        return []
    underscore = re.escape(public_stem)
    private_underscore = re.escape(stem)
    spaced = r"\s+".join(re.escape(word) for word in words)
    hyphenated = "-".join(re.escape(word) for word in words)
    return [
        re.compile(rf"(?<![{_WORD}]){private_underscore}(?![{_WORD}])", re.IGNORECASE),
        re.compile(rf"(?<![{_WORD}]){underscore}(?![{_WORD}])", re.IGNORECASE),
        re.compile(rf"(?<![{_WORD}]){spaced}(?![{_WORD}])", re.IGNORECASE),
        re.compile(rf"(?<![{_WORD}]){hyphenated}(?![{_WORD}])", re.IGNORECASE),
    ]


def _public_markdown_text(path: Path, text: str) -> str:
    """Return hand-authored public text for runtime-leak scanning."""
    if path.name != "SKILL.md":
        return text
    return hand_authored_skill_body(text)


def _suffix_patterns_for_stem(stem: str) -> list[re.Pattern[str]]:
    public_stem = re.escape(stem.lstrip("_"))
    private_stem = re.escape(stem)
    return [
        re.compile(rf"(?<![{_WORD}]){private_stem}(?:{_SUFFIX_ALT})(?![{_WORD}])", re.IGNORECASE),
        re.compile(rf"(?<![{_WORD}]){public_stem}(?:{_SUFFIX_ALT})(?![{_WORD}])", re.IGNORECASE),
    ]


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    stems_by_skill = {
        skill_dir.name: _runtime_stems_for_skill(skill_dir)
        for skill_dir in sorted(skills_root.iterdir())
        if skill_dir.is_dir() and skill_dir.name != ".system"
    }

    for path, rel_path in _iter_skill_markdown(repo_root):
        skill_name = rel_path.parts[1]
        stem_patterns = [
            (stem, pattern)
            for stem in stems_by_skill.get(skill_name, [])
            for pattern in _stem_patterns(stem)
        ]
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = _public_markdown_text(path, text).splitlines()
        for lineno, line in enumerate(lines, start=1):
            if RTX_DIR_NAME in line:
                errors.append(f"{rel_path}:{lineno}: skill-facing Markdown must not mention `{RTX_DIR_NAME}`")
            old_path = _OLD_RUNTIME_PATH_RE.search(line)
            if old_path:
                errors.append(
                    f"{rel_path}:{lineno}: skill-facing Markdown must not mention old runtime path "
                    f"`{old_path.group(0)}`"
                )
            for stem in stems_by_skill.get(skill_name, []):
                for suffix_pattern in _suffix_patterns_for_stem(stem):
                    suffix_match = suffix_pattern.search(line)
                    if suffix_match:
                        errors.append(
                            f"{rel_path}:{lineno}: skill-facing Markdown must not mention runtime file "
                            f"`{suffix_match.group(0)}`"
                        )
                        break
            for stem, pattern in stem_patterns:
                if pattern.search(line):
                    errors.append(
                        f"{rel_path}:{lineno}: skill-facing Markdown must not mention private runtime "
                        f"name `{stem}`"
                    )
                    break

    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Skill runtime Markdown reference violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
