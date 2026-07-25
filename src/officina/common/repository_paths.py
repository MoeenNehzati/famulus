"""Repository-relative path conversion without resolving descendants."""
from __future__ import annotations

import os
from pathlib import Path


class RepositoryPathError(ValueError):
    """Raised when a path cannot be expressed beneath a required root."""


def equivalent_root_relative_path(path: Path, root: Path) -> Path:
    """Return ``path`` relative to a lexically or physically equivalent root."""

    candidate = Path(os.path.abspath(path))
    boundary = Path(os.path.abspath(root))
    try:
        return candidate.relative_to(boundary)
    except ValueError:
        for ancestor in (candidate, *candidate.parents):
            try:
                if ancestor.samefile(boundary):
                    return candidate.relative_to(ancestor)
            except OSError:
                continue
    raise RepositoryPathError(f"{path}: path is outside root {root}")


def repository_relative_path(path: Path, repo_root: Path) -> Path:
    """Return a repository-relative path, rooting relative inputs at the repo."""

    root = Path(os.path.abspath(repo_root))
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return equivalent_root_relative_path(candidate, root)
    except RepositoryPathError as exc:
        raise RepositoryPathError(
            f"{path}: path is outside repository {repo_root}"
        ) from exc


def repository_relative_posix(path: Path, repo_root: Path) -> str:
    """Return the repository-relative path serialized with POSIX separators."""

    return repository_relative_path(path, repo_root).as_posix()
