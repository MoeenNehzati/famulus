"""Dependency exploration and hash helpers for skill drift reports.

This module discovers relevant files for skills, interfaces, and files, then
hashes the discovered files deterministically for audit-record comparison.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

HASH_PREFIX = "sha256:"
DEFAULT_EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store", ".last_audit.json"}
DEFAULT_EXCLUDE_SUFFIXES = {".pyc"}
DIRECT_FIELDS = ("directly_reads", "directly_executes", "directly_writes")
MARKDOWN_SUFFIXES = {".md", ".markdown"}
MARKDOWN_REFERENCE_RE = re.compile(
    r"@?[A-Za-z0-9_.-]+(?:[/\\][A-Za-z0-9_.-]+)+|@?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+"
)


class HashRootError(ValueError):
    """Raised when a declared hash root is not safe to resolve."""


@dataclass(frozen=True, order=True)
class HashEntry:
    """A deterministic filesystem entry used as hash input."""

    label: str
    kind: str
    data: bytes


@dataclass(frozen=True, order=True)
class DependencyFile:
    """One existing file discovered as relevant to a skill or interface."""

    label: str
    path: Path
    reason: str


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


class DependencyExplorer:
    """Discover relevant files for skills, interfaces, and files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()

    def explore_file(
        self,
        path: Path,
        *,
        base_dir: Path | None = None,
        reason: str = "file",
    ) -> list[DependencyFile]:
        """Return the transitive existing files relevant to one file or directory."""

        base = (base_dir or path.parent).resolve(strict=False)
        return self._explore_paths([(path.resolve(strict=False), base, reason)])

    def explore_interface(self, skill_dir: Path, interface_spec: dict[str, Any]) -> list[DependencyFile]:
        """Return the transitive existing files relevant to one blueprint interface."""

        skill_root = skill_dir.resolve()
        seeds: list[tuple[Path, Path, str]] = []
        for root in interface_roots(interface_spec):
            path, _label = resolve_declared_root(skill_root, self.repo_root, root)
            seeds.append((path, skill_root, f"interface root {root}"))

        runtime = interface_spec.get("runtime")
        if isinstance(runtime, dict) and runtime.get("kind") == "python_machine_interface":
            entrypoint = runtime.get("entrypoint")
            if isinstance(entrypoint, str):
                for path in explore_python_runtime_dependency_files(skill_root, self.repo_root, entrypoint):
                    seeds.append((path.resolve(strict=False), path.parent.resolve(strict=False), "python dependency"))

        return self._explore_paths(seeds)

    def explore_skill(self, skill_dir: Path, blueprint: dict[str, Any]) -> list[DependencyFile]:
        """Return the transitive existing files relevant to a whole skill."""

        skill_root = skill_dir.resolve()
        files: list[DependencyFile] = []
        interfaces = blueprint.get("interfaces")
        if isinstance(interfaces, dict):
            for namespace in ("llm", "machine"):
                entries = interfaces.get(namespace)
                if not isinstance(entries, dict):
                    continue
                for spec in entries.values():
                    if isinstance(spec, dict):
                        files.extend(self.explore_interface(skill_root, spec))

        for compatibility_file in ("depends_on_skills", "permissions.json"):
            files.extend(
                self.explore_file(
                    skill_root / compatibility_file,
                    base_dir=skill_root,
                    reason=f"compatibility file {compatibility_file}",
                )
            )
        return dedupe_dependency_files(files)

    def _explore_paths(self, seeds: Iterable[tuple[Path, Path, str]]) -> list[DependencyFile]:
        queue: deque[tuple[Path, Path, str]] = deque(seeds)
        seen: set[str] = set()
        result: list[DependencyFile] = []

        while queue:
            path, base_dir, reason = queue.popleft()
            label = path_label(path, self.repo_root)
            if label in seen or is_excluded(Path(label)):
                continue
            seen.add(label)

            if not path.exists() and not path.is_symlink():
                continue
            if path.is_dir():
                children = sorted(path.rglob("*"), key=lambda child: path_label(child, self.repo_root))
                for child in children:
                    child_label = path_label(child, self.repo_root)
                    if is_excluded(Path(child_label)):
                        continue
                    if child.is_file() or child.is_symlink():
                        queue.append((child.resolve(strict=False), base_dir, f"{reason} directory child"))
                continue
            if not path.is_file() and not path.is_symlink():
                continue

            result.append(DependencyFile(label=label, path=path, reason=reason))
            if path.suffix.lower() in MARKDOWN_SUFFIXES and path.is_file():
                for referenced in self._markdown_references(path, base_dir):
                    queue.append((referenced, base_dir, f"markdown reference from {label}"))

        return sorted(result)

    def _markdown_references(self, path: Path, base_dir: Path) -> list[Path]:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return []

        references: list[Path] = []
        seen: set[Path] = set()
        for match in MARKDOWN_REFERENCE_RE.finditer(text):
            token = match.group(0).lstrip("@").replace("\\", "/")
            token = token.strip(".,;:)]}\"'")
            if not token or token.startswith(("http://", "https://")) or os.path.isabs(token):
                continue
            candidate = (base_dir / token).resolve(strict=False)
            try:
                candidate.relative_to(self.repo_root)
            except ValueError:
                continue
            if (candidate.exists() or candidate.is_symlink()) and candidate not in seen:
                references.append(candidate)
                seen.add(candidate)
        return references


def hash_declared_roots(skill_dir: Path, repo_root: Path, declared_roots: Iterable[str]) -> str:
    """Hash all files under blueprint-declared roots."""

    return digest_entries(collect_declared_root_entries(skill_dir, repo_root, declared_roots))


def collect_declared_root_entries(
    skill_dir: Path, repo_root: Path, declared_roots: Iterable[str]
) -> list[HashEntry]:
    """Collect hash entries under blueprint-declared roots."""

    entries: list[HashEntry] = []
    explorer = DependencyExplorer(repo_root)
    for declared_root in declared_roots:
        path, label = resolve_declared_root(skill_dir, repo_root, declared_root)
        if not path.exists() and not path.is_symlink():
            entries.extend(entries_for_path(path, repo_root, label))
            continue
        for dependency in explorer.explore_file(path, base_dir=path.parent, reason=f"declared root {declared_root}"):
            entries.extend(entries_for_path(dependency.path, repo_root, dependency.label))
    return entries


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

    return digest_entries(interface_entries(skill_dir, repo_root, interface_spec))


def interface_entries(skill_dir: Path, repo_root: Path, interface_spec: dict[str, Any]) -> list[HashEntry]:
    """Collect hash entries for one machine or LLM interface."""

    return entries_for_dependency_files(DependencyExplorer(repo_root).explore_interface(skill_dir, interface_spec), repo_root)


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

    entries = entries_for_dependency_files(DependencyExplorer(repo_root).explore_skill(skill_dir, blueprint), repo_root)
    return digest_entries(entries)


def python_runtime_dependency_entries(
    skill_dir: Path, repo_root: Path, interface_spec: dict[str, Any]
) -> list[HashEntry]:
    """Collect modules loaded by a clean PythonMachineInterface route-smoke run."""

    runtime = interface_spec.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("kind") != "python_machine_interface":
        return []
    entrypoint = runtime.get("entrypoint")
    if not isinstance(entrypoint, str):
        return []

    entries: list[HashEntry] = []
    for path in explore_python_runtime_dependency_files(skill_dir, repo_root, entrypoint):
        entries.extend(entries_for_path(path, repo_root))
    return dedupe_entries(entries)


def entries_for_dependency_files(files: Iterable[DependencyFile], repo_root: Path) -> list[HashEntry]:
    entries: list[HashEntry] = []
    for dependency in files:
        entries.extend(entries_for_path(dependency.path, repo_root, dependency.label))
    return dedupe_entries(entries)


def explore_python_runtime_dependency_files(skill_dir: Path, repo_root: Path, entrypoint: str) -> list[Path]:
    """Recursively discover local/officina files that can affect an interface."""

    trace_code = r"""
import contextlib
import io
import json
import os
import sys
from pathlib import Path

skill_dir = Path(sys.argv[1]).resolve()
src_root = Path(sys.argv[2]).resolve()
entrypoint = sys.argv[3]
repo_root = Path(sys.argv[4]).resolve()
officina_root = src_root / "officina"
skills_root = repo_root / "skills"
sys.path.insert(0, str(src_root))

from officina.runtime.python_machine_interface import DispatchDependencyResolver
from officina.runtime.python_machine_interface_runner import load_interface, run_python_machine_interface

def is_under(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

paths = []

def collect_loaded_paths():
    for module in sys.modules.values():
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        path = Path(module_file).resolve()
        if path.suffix not in {".py", ".pyi"}:
            continue
        if is_under(path, skill_dir) or is_under(path, skills_root) or is_under(path, officina_root):
            paths.append(path.as_posix())

os.chdir(skill_dir)
with contextlib.redirect_stdout(io.StringIO()):
    interface = load_interface(entrypoint)
    run_python_machine_interface(interface, ["--route-smoke"])
collect_loaded_paths()

resolver = DispatchDependencyResolver(repo_root=repo_root)
with contextlib.redirect_stdout(io.StringIO()):
    dependencies = resolver.collect(interface)
    for dependency in dependencies:
        for token in dependency.resolved.command:
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = dependency.resolved.cwd / candidate
            candidate = candidate.resolve()
            if candidate.exists() and is_under(candidate, skills_root):
                paths.append(candidate.as_posix())
        target_interface = resolver.load_python_interface(
            dependency.resolved.target_skill,
            dependency.resolved.script_interface,
        )
        if target_interface is not None:
            previous_cwd = Path.cwd()
            try:
                os.chdir(dependency.resolved.cwd)
                run_python_machine_interface(target_interface, ["--route-smoke"])
            finally:
                os.chdir(previous_cwd)
            collect_loaded_paths()

print(json.dumps(sorted(set(paths))))
"""
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC_ROOT) if not current_pythonpath else f"{SRC_ROOT}{os.pathsep}{current_pythonpath}"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            trace_code,
            str(skill_dir),
            str(SRC_ROOT),
            entrypoint,
            str(repo_root),
        ],
        cwd=skill_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise HashRootError(
            f"route-smoke dependency trace failed for {entrypoint}: "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return [Path(path) for path in json.loads(result.stdout)]


def dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def dedupe_entries(entries: Iterable[HashEntry]) -> list[HashEntry]:
    seen: set[tuple[str, str]] = set()
    result: list[HashEntry] = []
    for entry in entries:
        key = (entry.label, entry.kind)
        if key not in seen:
            result.append(entry)
            seen.add(key)
    return result


def dedupe_dependency_files(files: Iterable[DependencyFile]) -> list[DependencyFile]:
    seen: set[str] = set()
    result: list[DependencyFile] = []
    for file in sorted(files):
        if file.label not in seen:
            result.append(file)
            seen.add(file.label)
    return result
