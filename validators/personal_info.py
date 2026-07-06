"""Validate that no tracked file contains personal identifying tokens.

Blocks any occurrence (case-insensitive, substring) of the owner's name
tokens. Since only tracked files are pushed, the check covers exactly the
content that could become public. Home-directory paths like /home/<name>
are intentionally caught: tracked files must never embed them.

This module excludes itself (it necessarily contains the tokens).
"""
from __future__ import annotations

import re
from pathlib import Path

_TOKENS = ("seyed", "moeen", "nehzati")
_PATTERN = re.compile("|".join(_TOKENS), re.IGNORECASE)

# Public identifiers that are allowed to appear anywhere: the GitHub handle
# is the repo's public address and appears in install/clone commands.
_ALLOWED_IDENTIFIERS = ("MoeenNehzati",)


def _scrub(text: str) -> str:
    """Remove allowed public identifiers before scanning for tokens."""
    for ident in _ALLOWED_IDENTIFIERS:
        text = text.replace(ident, "")
    return text

# Files allowed to contain the tokens:
# - this validator and its tests (necessarily contain them)
# - plugin manifests, where the owner deliberately signs as author
_ALLOWED_PATHS = {
    Path("validators/personal_info.py"),
    Path("tests/validate_personal_info.py"),
    Path(".claude-plugin/plugin.json"),
    Path(".claude-plugin/marketplace.json"),
    Path(".codex-plugin/plugin.json"),
}


def validate(repo_root: Path) -> list[str]:
    """Return an error per line containing a personal-info token."""
    errors: list[str] = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        if rel in _ALLOWED_PATHS:
            continue
        if _PATTERN.search(_scrub(str(rel))):
            errors.append(f"{rel}: file path contains a personal-info token")
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; gitleaks/other checks cover these
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _PATTERN.search(_scrub(line))
            if match:
                errors.append(
                    f"{rel}:{lineno}: contains personal-info token "
                    f"'{match.group(0)}'"
                )
    return errors
