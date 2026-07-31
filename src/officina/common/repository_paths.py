"""Repository-relative path conversion without resolving descendants."""
from __future__ import annotations

import os
from importlib.util import find_spec
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


def resolve_python_source_path(path: Path) -> Path | None:
    """Resolve one module-like path to a concrete Python source file."""
    if path.is_file():
        if path.suffix != ".py":
            return None
        return path
    if path.is_dir():
        init_path = path / "__init__.py"
        if init_path.is_file():
            return init_path
    return None


def resolve_logical_module_path(
    logical_module: str,
    *,
    repo_roots: tuple[Path, ...] | None = None,
) -> Path | None:
    """Resolve a logical module identifier to a Python source file path."""
    normalized = (logical_module or "").strip()
    if not normalized:
        return None

    logical = normalized.replace("/", ".").replace("\\", ".")
    if logical.endswith(".py"):
        logical = logical[:-3]

    parts = tuple(part for part in logical.split(".") if part)
    if not parts:
        return None
    if any(not part.isidentifier() for part in parts):
        return None

    roots = tuple(repo_roots) if repo_roots is not None else _default_repository_roots()
    if not roots:
        return None

    module_parts = Path(*parts)
    for root in roots:
        package_candidate = root / "src" / module_parts
        resolved = resolve_python_source_path(package_candidate)
        if resolved is not None:
            return resolved

        module_candidate = root / Path(*parts).with_suffix(".py")
        resolved = resolve_python_source_path(module_candidate)
        if resolved is not None:
            return resolved

        direct_package = root / module_parts
        resolved = resolve_python_source_path(direct_package)
        if resolved is not None:
            return resolved

        direct_module = root / Path(*parts).with_suffix(".py")
        resolved = resolve_python_source_path(direct_module)
        if resolved is not None:
            return resolved

    spec = find_spec(logical)
    if spec is None or spec.origin is None or spec.origin == "built-in":
        return None

    candidate = Path(spec.origin)
    if candidate.suffix == ".py" and candidate.is_file():
        return candidate

    if candidate.is_dir():
        init_path = candidate / "__init__.py"
        if init_path.is_file():
            return init_path

    for location in spec.submodule_search_locations or ():
        init_path = Path(location) / "__init__.py"
        if init_path.is_file():
            return init_path
    return None


def _default_repository_roots() -> tuple[Path, ...]:
    """Best-effort root candidates for logical repository module lookup."""
    root = Path(__file__).resolve().parents[3]
    roots: set[Path] = {root}
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / "src").is_dir() and any(
            child.is_dir() for child in (candidate / "src").iterdir()
        ):
            roots.add(candidate)
        if candidate.parent == candidate:
            break
    return tuple(sorted(roots))
