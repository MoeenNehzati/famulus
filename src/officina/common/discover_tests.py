"""Shared test-discovery helpers for repository tooling."""

from __future__ import annotations

from pathlib import Path


def _resolve_repo_root(candidate: Path | str | None = None) -> Path:
    """Locate repo root from a likely working directory."""
    root = Path.cwd().resolve() if candidate is None else Path(candidate).resolve()
    for path in (root, *root.parents):
        if (path / "src" / "officina").exists():
            return path
    return root


def is_test_module(path: Path | str, repo_root: Path | str | None = None) -> bool:
    """Return true when ``path`` belongs to one canonical test tree."""
    candidate = Path(path).resolve()
    root = _resolve_repo_root(candidate if repo_root is None else repo_root)
    try:
        parts = candidate.relative_to(root).parts
    except ValueError:
        return False
    return (
        parts[:1] == ("tests",)
        or parts[:2] == ("hooks", "tests")
        or parts[:4] == ("src", "officina", "wakeup", "tests")
        or (
            len(parts) >= 3
            and parts[0] == "skills"
            and parts[2] == "tests"
        )
        or (
            len(parts) >= 4
            and parts[0] == "skills"
            and parts[2:4] == ("_rtx", "tests")
        )
    )
