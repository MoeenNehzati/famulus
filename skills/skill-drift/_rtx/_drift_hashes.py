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

from officina.blueprint_search import BlueprintSearchError, load_blueprint_record, strip_selected_paths

HASH_PREFIX = "sha256:"
DEFAULT_EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store", ".last_audit.json"}
DEFAULT_EXCLUDE_SUFFIXES = {".pyc"}
NON_HASHABLE_BLUEPRINT_SELECTORS = ("**.direct_io",)
CANONICAL_INTERFACE_RE = re.compile(
    r"^(?P<skill>[a-z0-9]+(?:-[a-z0-9]+)+)\."
    r"(?P<namespace>machine|llm)\."
    r"(?P<interface>[a-z0-9]+(?:-[a-z0-9]+)*)$"
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


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic JSON bytes for structured blueprint metadata."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


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

        invocation = interface_spec.get("invocation")
        if isinstance(invocation, dict) and invocation.get("kind") == "python_machine_interface":
            entrypoint = invocation.get("entrypoint")
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

        return sorted(result)


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
    """Collect behavior-shaping roots from one blueprint interface."""

    roots: list[str] = []
    if include_binding:
        roots.extend(local_binding_roots(interface_spec))
    roots.extend(behavior_source_roots(interface_spec))
    return dedupe_preserving_order(roots)


def behavior_source_roots(interface_spec: dict[str, Any]) -> list[str]:
    """Return files declared as behavior sources on an interface or its invocation."""

    roots: list[str] = []
    for container in (interface_spec, interface_spec.get("invocation")):
        if not isinstance(container, dict):
            continue
        value = container.get("behavior_sources", [])
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    roots.append(entry["path"])
    return roots


def hash_interface(
    skill_dir: Path,
    repo_root: Path,
    interface_spec: dict[str, Any],
    *,
    _seen_interfaces: frozenset[str] = frozenset(),
) -> str:
    """Hash one machine or LLM interface from its declared roots."""

    return digest_entries(
        interface_entries(
            skill_dir,
            repo_root,
            interface_spec,
            _seen_interfaces=_seen_interfaces,
        )
    )


def interface_entries(
    skill_dir: Path,
    repo_root: Path,
    interface_spec: dict[str, Any],
    *,
    _seen_interfaces: frozenset[str] = frozenset(),
) -> list[HashEntry]:
    """Collect hash entries for one machine or LLM interface."""

    entries = [interface_metadata_entry(interface_spec)]
    entries.extend(collect_declared_root_entries(skill_dir, repo_root, interface_roots(interface_spec)))
    entries.extend(python_runtime_dependency_entries(skill_dir, repo_root, interface_spec))
    entries.extend(used_interface_hash_entries(skill_dir, repo_root, interface_spec, _seen_interfaces))
    return dedupe_entries(entries)


def interface_metadata_entry(interface_spec: dict[str, Any]) -> HashEntry:
    """Return the canonical structured blueprint declaration for an interface."""

    return HashEntry(
        "blueprint-interface",
        "json",
        canonical_json_bytes(strip_selected_paths(interface_spec, NON_HASHABLE_BLUEPRINT_SELECTORS)),
    )


def used_interface_hash_entries(
    skill_dir: Path,
    repo_root: Path,
    interface_spec: dict[str, Any],
    seen_interfaces: frozenset[str] = frozenset(),
) -> list[HashEntry]:
    """Return hash entries for interfaces declared in uses_interfaces."""

    entries: list[HashEntry] = []
    for canonical_name in used_interface_names(interface_spec):
        if canonical_name in seen_interfaces:
            raise HashRootError(f"uses_interfaces cycle includes {canonical_name}")
        target_skill, namespace, target_interface_name = parse_canonical_interface(canonical_name)
        target_skill_dir = repo_root / "skills" / target_skill
        target_blueprint = load_blueprint(target_skill_dir, repo_root)
        target_spec = interface_spec_by_name(target_blueprint, target_skill, namespace, target_interface_name)
        target_hash = hash_interface(
            target_skill_dir,
            repo_root,
            target_spec,
            _seen_interfaces=seen_interfaces | {canonical_name},
        )
        entries.append(HashEntry(f"used-interface:{canonical_name}", "interface-hash", target_hash.encode("utf-8")))
    return entries


def used_interface_names(interface_spec: dict[str, Any]) -> list[str]:
    """Return declared canonical interfaces used by an interface."""

    value = interface_spec.get("uses_interfaces", [])
    if not isinstance(value, list):
        raise HashRootError("uses_interfaces must be a list")
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            canonical_name = item
        elif isinstance(item, dict) and isinstance(item.get("interface"), str):
            canonical_name = item["interface"]
        else:
            raise HashRootError("uses_interfaces entries must be strings or mappings with `interface`")
        parse_canonical_interface(canonical_name)
        result.append(canonical_name)
    return dedupe_preserving_order(result)


def parse_canonical_interface(canonical_name: str) -> tuple[str, str, str]:
    match = CANONICAL_INTERFACE_RE.match(canonical_name)
    if not match:
        raise HashRootError(f"uses_interfaces entry must be canonical interface: {canonical_name}")
    return match.group("skill"), match.group("namespace"), match.group("interface")


def load_blueprint(skill_dir: Path, repo_root: Path) -> dict[str, Any]:
    path = skill_dir / "blueprint.yaml"
    if not path.is_file():
        raise HashRootError(f"{skill_dir.name}: missing blueprint.yaml")
    try:
        return load_blueprint_record(path, repo_root=repo_root, skill=skill_dir.name).data
    except BlueprintSearchError as exc:
        raise HashRootError(str(exc)) from exc


def interface_spec_by_name(
    blueprint: dict[str, Any],
    skill_name: str,
    namespace: str,
    interface_name: str,
) -> dict[str, Any]:
    interfaces = blueprint.get("interfaces")
    if not isinstance(interfaces, dict):
        raise HashRootError(f"{skill_name}: missing interfaces")
    namespace_specs = interfaces.get(namespace)
    if not isinstance(namespace_specs, dict):
        raise HashRootError(f"{skill_name}: missing {namespace} interfaces")
    spec = namespace_specs.get(interface_name)
    if not isinstance(spec, dict):
        raise HashRootError(f"{skill_name}.{namespace}.{interface_name} is not defined")
    return spec


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
    return dedupe_preserving_order(roots)


def hash_skill(skill_dir: Path, repo_root: Path, blueprint: dict[str, Any]) -> str:
    """Hash a skill from all blueprint-declared interface roots."""

    entries = [
        HashEntry(
            "blueprint",
            "json",
            canonical_json_bytes(strip_selected_paths(blueprint, NON_HASHABLE_BLUEPRINT_SELECTORS)),
        )
    ]
    entries.extend(entries_for_dependency_files(DependencyExplorer(repo_root).explore_skill(skill_dir, blueprint), repo_root))
    for interface_name, interface_spec in iter_blueprint_interface_specs(blueprint):
        interface_hash = hash_interface(skill_dir, repo_root, interface_spec)
        entries.append(HashEntry(f"interface:{interface_name}", "interface-hash", interface_hash.encode("utf-8")))
    return digest_entries(entries)


def iter_blueprint_interface_specs(blueprint: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    interfaces = blueprint.get("interfaces")
    if not isinstance(interfaces, dict):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for namespace in ("llm", "machine"):
        entries = interfaces.get(namespace)
        if not isinstance(entries, dict):
            continue
        for name, spec in sorted(entries.items()):
            if isinstance(spec, dict):
                result.append((f"{namespace}.{name}", spec))
    return result


def python_runtime_dependency_entries(
    skill_dir: Path, repo_root: Path, interface_spec: dict[str, Any]
) -> list[HashEntry]:
    """Collect modules loaded by a clean PythonMachineInterface route-smoke run."""

    invocation = interface_spec.get("invocation")
    if not isinstance(invocation, dict) or invocation.get("kind") != "python_machine_interface":
        return []
    entrypoint = invocation.get("entrypoint")
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
