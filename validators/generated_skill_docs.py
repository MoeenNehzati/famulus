"""Validate the generated full skill index."""
from __future__ import annotations

from pathlib import Path

from docs_tooling.catalog import SKILL_INDEX_PATH
from docs_tooling.render import render_skill_index


def validate(repo_root: Path) -> list[str]:
    path = repo_root / SKILL_INDEX_PATH
    if not path.exists() and not (repo_root / "skills").exists():
        return []
    if not path.is_file():
        return [f"{SKILL_INDEX_PATH}: missing"]
    actual = path.read_text(encoding="utf-8")
    expected = render_skill_index(repo_root)
    if actual != expected:
        return [f"{SKILL_INDEX_PATH}: stale or manually edited; run python3 scripts/generate-doc-artifacts.py"]
    return []
