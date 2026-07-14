"""Validate the top-level README's user-facing contract."""
from __future__ import annotations

from pathlib import Path


README = Path("README.md")
INTRO_SNIPPETS = (
    "Famulus is a cross-host assistant library",
    "Famulus is a cross-llm skills library",
)
REQUIRED_SNIPPETS = (
    "Recommended: plugin install",
    "docs/installation.md",
    "Plan my day",
    "Wrap up today",
    "Build a math dependency graph",
    "docs/user/general.md",
    "docs/user/research.md",
    "docs/user/system.md",
    "docs/skills.md",
    "docs/contributors/README.md",
)
FORBIDDEN_SNIPPETS = (
    "The dispatcher is the only approved route",
    "docs/skill-blueprints.md",
    "validators/` and `skills/skill-maker/validators/",
)


def validate(repo_root: Path) -> list[str]:
    path = repo_root / README
    if not path.exists() and not (repo_root / "docs").exists():
        return []
    if not path.is_file():
        return [f"{README}: missing"]
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if not any(snippet in text for snippet in INTRO_SNIPPETS):
        errors.append(
            f"{README}: missing required README introduction "
            f"({ ' or '.join(f'`{snippet}`' for snippet in INTRO_SNIPPETS) })"
        )
    for snippet in REQUIRED_SNIPPETS:
        if snippet not in text:
            errors.append(f"{README}: missing required README content `{snippet}`")
    for snippet in FORBIDDEN_SNIPPETS:
        if snippet in text:
            errors.append(f"{README}: still contains contributor-only content `{snippet}`")
    return errors
