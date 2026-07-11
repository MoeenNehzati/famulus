"""Hash helpers for skill health records.

This first version intentionally hashes declared roots only. Directory roots are
expanded recursively, but file roots are not parsed for transitive dependencies
such as Python imports or Markdown links. Health-record status computation and
transitive reference expansion will build on these functions in later slices.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


HASH_PREFIX = "sha256:"
DEFAULT_EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store", ".health.json"}
DEFAULT_EXCLUDE_SUFFIXES = {".pyc"}
DIRECT_FIELDS = ("directly_reads", "directly_executes", "directly_writes")


class HashRootError(ValueError):
    """Raised when a declared hash root is not safe to resolve."""


@dataclass(frozen=True, order=True)
class HashEntry:
    """A deterministic filesystem entry used as hash input."""

    label: str
    kind: str
    data: bytes


def digest_entries(entries: Iterable[HashEntry]) -> str:
    """Return a stable sha256 digest for already-collected hash entries."""

    hasher = hashlib.sha256()
    for entry in sorted(entries):
        hasher.update(entry.kind.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(entry.label.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(entry.data)
        hasher.update(b"\0")
    return f"{HASH_PREFIX}{hasher.hexdigest()}"


def resolve_declared_root(skill_dir: Path, repo_root: Path, declared_root: str) -> tuple[Path, str]:
    """Resolve a blueprint-declared root without allowing path traversal."""

    if not isinstance(declared_root, str) or not declared_root:
        raise HashRootError("hash root must be a non-empty string")
    if os.path.isabs(declared_root):
        raise HashRootError(f"hash root must be relative: {declared_root}")

    if declared_root.startswith("$repo/"):
        base = repo_root.resolve()
        relative = Path(declared_root[len("$repo/") :])
    else:
        base = skill_dir.resolve()
        relative = Path(declared_root)

    if any(part == ".." for part in relative.parts):
        raise HashRootError(f"hash root must not contain '..': {declared_root}")

    path = (base / relative).resolve(strict=False)
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise HashRootError(f"hash root escapes its base: {declared_root}") from exc
    return path, path_label(path, repo_root)


def path_label(path: Path, repo_root: Path) -> str:
    """Return the repo-relative label used in hash entries."""

    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def is_excluded(path: Path) -> bool:
    """Return whether a path is excluded from skill content hashing."""

    return (
        any(part in DEFAULT_EXCLUDE_NAMES for part in path.parts)
        or path.name in DEFAULT_EXCLUDE_NAMES
        or path.suffix in DEFAULT_EXCLUDE_SUFFIXES
    )


def entries_for_path(path: Path, repo_root: Path, label: str | None = None) -> Iterator[HashEntry]:
    """Yield deterministic entries for a file, directory, symlink, or missing root."""

    entry_label = label if label is not None else path_label(path, repo_root)
    if not path.exists() and not path.is_symlink():
        yield HashEntry(entry_label, "missing", b"")
        return
    if is_excluded(Path(entry_label)):
        return
    if path.is_symlink():
        yield HashEntry(entry_label, "symlink", os.readlink(path).encode("utf-8"))
        return
    if path.is_file():
        yield HashEntry(entry_label, "file", path.read_bytes())
        return
    if path.is_dir():
        children = sorted(path.rglob("*"), key=lambda child: path_label(child, repo_root))
        for child in children:
            child_label = path_label(child, repo_root)
            if is_excluded(Path(child_label)):
                continue
            if child.is_symlink():
                yield HashEntry(child_label, "symlink", os.readlink(child).encode("utf-8"))
            elif child.is_file():
                yield HashEntry(child_label, "file", child.read_bytes())
        return
    yield HashEntry(entry_label, "special", b"")


def hash_declared_roots(skill_dir: Path, repo_root: Path, declared_roots: Iterable[str]) -> str:
    """Hash all files under blueprint-declared roots."""

    entries: list[HashEntry] = []
    for declared_root in declared_roots:
        path, label = resolve_declared_root(skill_dir, repo_root, declared_root)
        entries.extend(entries_for_path(path, repo_root, label))
    return digest_entries(entries)


def local_binding_roots(interface_spec: dict[str, Any]) -> list[str]:
    """Return local files implied by an LLM interface binding."""

    binding = interface_spec.get("binding")
    if isinstance(binding, dict):
        kind = binding.get("kind")
        path = binding.get("path")
        if kind in {"skill_file", "markdown_file"} and isinstance(path, str):
            return [path]
    legacy_file = interface_spec.get("file")
    if isinstance(legacy_file, str):
        return [legacy_file]
    return []


def interface_roots(interface_spec: dict[str, Any], *, include_binding: bool = True) -> list[str]:
    """Collect direct and binding roots from one blueprint interface."""

    roots: list[str] = []
    if include_binding:
        roots.extend(local_binding_roots(interface_spec))
    for field in DIRECT_FIELDS:
        value = interface_spec.get(field, [])
        if isinstance(value, list):
            roots.extend(root for root in value if isinstance(root, str))
    return dedupe_preserving_order(roots)


def hash_interface(skill_dir: Path, repo_root: Path, interface_spec: dict[str, Any]) -> str:
    """Hash one machine or LLM interface from its declared roots."""

    return hash_declared_roots(skill_dir, repo_root, interface_roots(interface_spec))


def skill_roots(blueprint: dict[str, Any]) -> list[str]:
    """Collect declared roots for every interface in a blueprint."""

    roots: list[str] = []
    interfaces = blueprint.get("interfaces")
    if not isinstance(interfaces, dict):
        return roots
    for namespace in ("llm", "machine"):
        entries = interfaces.get(namespace)
        if not isinstance(entries, dict):
            continue
        for spec in entries.values():
            if isinstance(spec, dict):
                roots.extend(interface_roots(spec, include_binding=(namespace == "llm")))
    roots.extend(["depends_on_skills", "permissions.json"])
    return dedupe_preserving_order(roots)


def hash_skill(skill_dir: Path, repo_root: Path, blueprint: dict[str, Any]) -> str:
    """Hash a skill from all blueprint-declared interface roots."""

    return hash_declared_roots(skill_dir, repo_root, skill_roots(blueprint))


def dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
