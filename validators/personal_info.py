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
# and the public GitHub Pages domain are intentionally linked from user docs.
_ALLOWED_PATTERNS = (
    re.compile(r"https?://moeennehzati\.github\.io/\S*", re.IGNORECASE),
    re.compile(r"moeennehzati\.github\.io", re.IGNORECASE),
    re.compile(r"MoeenNehzati", re.IGNORECASE),
)


def _scrub(text: str) -> str:
    """Remove allowed public identifiers before token scanning.

    Intent
    ------
    Prevent documented public handles and domains from producing personal-token findings.

    Rationale
    ---------
    Allowed identifiers may contain the same substrings as forbidden private information.

    Pseudocode
    ----------
    - for pattern in allowed public identifiers:
      - set text = text with pattern removed
    - return text

    Wraps
    -----
    - none
    """
    for pattern in _ALLOWED_PATTERNS:
        text = pattern.sub("", text)
    return text


def _find_disallowed_token(text: str) -> re.Match[str] | None:
    """Return the first non-allowed personal token in text.

    Intent
    ------
    Skip allow-pattern substitutions when text contains no forbidden-token candidate.

    Rationale
    ---------
    Most scanned paths and lines are clean, while allowed identifiers require rechecking.

    Pseudocode
    ----------
    - if text has no token candidate:
      - return none
    - return first token remaining after allowed identifiers are removed

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._scrub:
      why:
        computes: "Removes allowed public identifiers before the definitive search."
    """
    if _PATTERN.search(text) is None:
        return None
    return _PATTERN.search(_scrub(text))

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
    """Return ordered path and line findings for personal tokens.

    Intent
    ------
    Scan every non-exempt repository file path and decodable content line.

    Rationale
    ---------
    Scanned repository content must not expose private identifying names or home paths.

    Pseudocode
    ----------
    - for path in sorted repository files:
      - if relative path has a disallowed token:
        - set findings = findings plus path finding
      - for line in decodable content:
        - if line has a disallowed token:
          - set findings = findings plus line finding
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._find_disallowed_token:
      why:
        computes: "Tests repository-relative paths before local file scanning continues."

    InstantiationsFromRepo
    ----------------------
    ._find_disallowed_token:
      why:
        constructs: "Builds each content-line token match used in a finding."
    """
    errors: list[str] = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        if rel in _ALLOWED_PATHS:
            continue
        if _find_disallowed_token(str(rel)):
            errors.append(f"{rel}: file path contains a personal-info token")
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; gitleaks/other checks cover these
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _find_disallowed_token(line)
            if match:
                errors.append(
                    f"{rel}:{lineno}: contains personal-info token "
                    f"'{match.group(0)}'"
                )
    return errors
