"""Shared test-discovery helpers for repository tooling."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable


BASE_TEST_DIRS: tuple[str, ...] = (
    "tests",
    "hooks/tests",
)

_SKILL_TEST_GLOBS: tuple[str, ...] = (
    "*/tests",
    "*/_rtx/tests",
)


def _is_under_path(child: Path, parent: Path) -> bool:
    """Check without relying on ``Path.is_relative_to`` availability."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _resolve_repo_root(candidate: Path | str | None = None) -> Path:
    """Locate repo root from a likely working directory."""
    root = Path.cwd().resolve() if candidate is None else Path(candidate).resolve()
    for path in (root, *root.parents):
        if (path / "src" / "officina").exists():
            return path
    return root


def _string_paths(paths: Iterable[Path], *, relative_to_root: Path) -> tuple[str, ...]:
    return tuple(
        path.relative_to(relative_to_root).as_posix()
        for path in paths
    )


@lru_cache(maxsize=8)
def discover_skill_test_dirs(
    repo_root: Path | str | None = None,
) -> tuple[Path, ...]:
    """Discover skill-local test directories under ``skills/*/tests`` and ``_* /_rtx/tests``."""
    root = _resolve_repo_root(repo_root)
    skills_root = root / "skills"
    if not skills_root.exists():
        return tuple()

    found: list[Path] = []
    for pattern in _SKILL_TEST_GLOBS:
        for path in sorted(skills_root.glob(pattern)):
            if path.is_dir():
                found.append(path)
    return tuple(sorted(found))


@lru_cache(maxsize=8)
def discover_repository_test_dirs(
    repo_root: Path | str | None = None,
    *,
    return_relative: bool = False,
) -> tuple[str, ...] | tuple[Path, ...]:
    """Return all discovery roots used by repository-level Python test runs."""
    root = _resolve_repo_root(repo_root)
    discovered = [root / entry for entry in BASE_TEST_DIRS]
    discovered.extend(discover_skill_test_dirs(root))

    filtered = [path for path in discovered if path.exists()]
    if return_relative:
        return _string_paths(filtered, relative_to_root=root)
    return tuple(filtered)


def is_test_module(path: Path | str, repo_root: Path | str | None = None) -> bool:
    """Return true when ``path`` is inside a repository test directory."""
    candidate = Path(path).resolve()
    root = _resolve_repo_root(candidate if repo_root is None else repo_root)
    test_dirs = discover_repository_test_dirs(root)
    return any(_is_under_path(candidate, test_dir) for test_dir in test_dirs)
