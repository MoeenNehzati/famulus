"""Validate the top-level README's user-facing contract."""
from __future__ import annotations

from pathlib import Path


README = Path("README.md")
INTRO_SNIPPETS = (
    "Famulus is a cross-host assistant library",
    "Famulus is a personal research assistant",
)
REQUIRED_SNIPPETS = (
    "install the plugin",
    "https://moeennehzati.github.io/famulus/",
    "https://github.com/MoeenNehzati/famulus/issues",
    "Plan my day",
    "Wrap up today",
    "Build a math dependency graph",
    "docs/quickstarts/personal-assistance.md",
    "docs/quickstarts/research.md",
    "docs/quickstarts/development.md",
    "docs/quickstarts/automation.md",
    "docs/quickstarts/skill-development.md",
    "docs/domains/assistant-interaction.md",
    "docs/domains/assistant-operations.md",
    "docs/skills.md",
    "docs/contributors/README.md",
)
# Code-level entities belong in the installation guide and in skill
# documentation, not in the users' entry point: the README names public
# commands and skills, and routes anything deeper through `dispatcher`.
FORBIDDEN_SNIPPETS = (
    "The dispatcher is the only approved route",
    "_rtx",
    "_phase_entry.py",
    "_install_scaffold.py",
    "_config_bridge.py",
    "_agent_launchers.py",
    "docs/officina/blueprints.md",
    "docs/officina/skill-blueprints.md",
    "validators/` and `skills/skill-maker/validators/",
)


def validate(repo_root: Path) -> list[str]:
    """Report the top-level README's violations of its user-facing contract.

    Intent
    ------
    Keep the README an entry point for users rather than for contributors.

    Rationale
    ---------
    The README is the users' entry point, so contributor-facing detail is
    treated as a violation rather than as extra information.

    Pseudocode
    ----------
    - set path = repo_root joined with README
    - if neither path nor docs exists:
      - return an empty error list
    - if path is not a regular file:
      - return a single missing-README error
    - set text = README contents
    - set errors = empty error list
    - if no intro snippet appears in text:
      - set errors = errors plus a missing-introduction error
    - for snippet in REQUIRED_SNIPPETS:
      - set errors = errors plus a missing-snippet error when absent
    - for snippet in FORBIDDEN_SNIPPETS:
      - set errors = errors plus a forbidden-snippet error when present
    - return errors

    Wraps
    -----
    - none
    """

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
