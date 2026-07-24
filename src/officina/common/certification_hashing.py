"""Canonical v4 node hashing and certification-basis derivation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import subprocess
from typing import Any, Iterable, Mapping, Sequence

import jsonschema
import yaml

from .atomic_files import AtomicWriteError, read_regular_file_bytes
from .blueprint_graph import (
    BlueprintNode,
    RepositoryBlueprintGraph,
)
from .git_provenance import capture_git_snapshot, git_file_provenance_batch


class CertificationHashError(ValueError):
    """Raised when a graph cannot be certified deterministically."""


CERTIFICATION_BASIS_MANIFEST = Path(
    "skills/skill-drift/references/certification-basis-roots.json"
)
CANONICAL_NODE_HASH_POLICY = Path(
    "references/certification/node-hash-policy.yaml"
)
CERTIFIER_NODE_ID = "skill-certifier"
CERTIFIER_INTERFACE_ID = "skill-certifier.interface.certify"
CERTIFIER_INTERFACE_VERSION = 1
CERTIFIER_CHECK_REGISTRY: Mapping[str, tuple[str, int]] = {
    "deterministic": ("v4-deterministic", 1),
    "route-smoke": ("route-smoke-dependencies", 1),
    "semantic-review": ("blueprint-accuracy", 1),
}


@dataclass(frozen=True)
class NodeHashState:
    """Canonical hash state for one v4 node."""

    node_hash: str | None = None
    input_manifest: tuple[dict[str, str], ...] = ()
    dependency_hashes: tuple[dict[str, Any], ...] = ()
    certification_basis_hash: str | None = None


def certification_target_postorder(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    requested: Sequence[str],
) -> tuple[str, ...]:
    """Order exact certification targets after every canonical dependency."""

    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise CertificationHashError(
                f"canonical certification dependency cycle at {node_id}"
            )
        if node_id not in graph.nodes:
            raise CertificationHashError(
                f"unknown exact certification target: {node_id}"
            )
        state = states.get(node_id)
        if not isinstance(state, NodeHashState):
            raise CertificationHashError(
                f"missing canonical certification state for {node_id}"
            )
        visiting.add(node_id)
        dependencies: list[tuple[str, str, int]] = []
        for dependency in state.dependency_hashes:
            relation = dependency.get("relation")
            target = dependency.get("target")
            version = dependency.get("version")
            if (
                not isinstance(relation, str)
                or not isinstance(target, str)
                or target not in graph.nodes
                or not isinstance(version, int)
                or isinstance(version, bool)
            ):
                raise CertificationHashError(
                    f"invalid canonical certification dependency for {node_id}"
                )
            dependencies.append((relation, target, version))
        for _relation, target, _version in sorted(dependencies):
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)
        ordered.append(node_id)

    for node_id in sorted(set(requested)):
        visit(node_id)
    return tuple(ordered)


@dataclass(frozen=True, order=True)
class RouteSmokeDependencyMapping:
    """One dynamically loaded route-smoke file resolved to existing authority."""

    path: str
    authority: str
    target_node_id: str | None


@dataclass(frozen=True, order=True)
class _ContractReference:
    locator: str
    path: Path
    digest: str


def _require_sha256_hash(node_id: str, field: str, value: object) -> str:
    if not isinstance(value, str):
        raise CertificationHashError(f"{node_id}: {field} must be a sha256 hash")
    prefix, separator, hexadecimal = value.partition(":")
    if (
        prefix != "sha256"
        or not separator
        or len(hexadecimal) != 64
        or any(character not in "0123456789abcdef" for character in hexadecimal)
    ):
        raise CertificationHashError(f"{node_id}: {field} must be a sha256 hash")
    return value


def _route_smoke_path(
    value: Path | str,
    repo_root: Path,
    *,
    allow_outside: bool,
) -> tuple[Path, str]:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    absolute = Path(os.path.abspath(candidate))
    try:
        relative = absolute.relative_to(repo_root).as_posix()
    except ValueError:
        if not allow_outside:
            raise CertificationHashError(
                f"unmapped route-smoke dependency outside repository: {absolute}"
            )
        relative = absolute.as_posix()
    try:
        metadata = absolute.lstat()
    except OSError as exc:
        raise CertificationHashError(
            f"unmapped route-smoke dependency {relative}: {exc}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise CertificationHashError(
            f"unmapped route-smoke dependency is not a regular file: {relative}"
        )
    return absolute, relative


def map_route_smoke_dependencies(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    *,
    source_node_id: str,
    loaded_paths: Iterable[Path | str],
    certification_basis_paths: Iterable[Path | str],
    repo_root: Path,
) -> tuple[RouteSmokeDependencyMapping, ...]:
    """Resolve every dynamic Python dependency to node input, edge, or basis."""

    root = Path(repo_root).resolve()
    source = graph.nodes.get(source_node_id)
    if source is None or source.node_type != "behavioral_source":
        raise CertificationHashError(
            f"route-smoke source must be a behavioral source: {source_node_id}"
        )
    manifest_paths: dict[str, set[str]] = {}
    validated_states: dict[str, NodeHashState] = {}
    for node_id in sorted(graph.nodes):
        state = states.get(node_id)
        if not isinstance(state, NodeHashState):
            raise CertificationHashError(
                f"{node_id}: route-smoke mapping requires canonical v4 node state"
            )
        _require_sha256_hash(node_id, "node_hash", state.node_hash)
        if not isinstance(state.input_manifest, tuple):
            raise CertificationHashError(
                f"{node_id}: route-smoke mapping found invalid input manifest"
            )
        if not isinstance(state.dependency_hashes, tuple):
            raise CertificationHashError(
                f"{node_id}: route-smoke mapping found invalid dependency hashes"
            )
        paths: set[str] = set()
        for entry in state.input_manifest:
            path = entry.get("path") if isinstance(entry, Mapping) else None
            if not isinstance(path, str):
                raise CertificationHashError(
                    f"{node_id}: route-smoke mapping found invalid input manifest"
                )
            paths.add(path)
        manifest_paths[node_id] = paths
        validated_states[node_id] = state

    reachable: set[str] = set()
    pending = [source_node_id]
    children: dict[str, set[str]] = {node_id: set() for node_id in graph.nodes}
    required_dependency_fields = {"relation", "target", "version", "node_hash"}
    for node_id, state in validated_states.items():
        for index, dependency in enumerate(state.dependency_hashes):
            if (
                not isinstance(dependency, Mapping)
                or set(dependency) != required_dependency_fields
            ):
                raise CertificationHashError(
                    f"{node_id}: invalid dependency hash at index {index}"
                )
            relation = dependency.get("relation")
            target_id = dependency.get("target")
            version = dependency.get("version")
            target_hash = dependency.get("node_hash")
            target = (
                graph.nodes.get(target_id) if isinstance(target_id, str) else None
            )
            target_state = (
                validated_states.get(target_id)
                if isinstance(target_id, str)
                else None
            )
            if (
                not isinstance(relation, str)
                or not relation
                or target is None
                or target_state is None
                or not isinstance(version, int)
                or isinstance(version, bool)
                or version != target.version
                or target_hash != target_state.node_hash
            ):
                raise CertificationHashError(
                    f"{node_id}: invalid dependency hash at index {index}"
                )
            children[node_id].add(target_id)
    while pending:
        current = pending.pop()
        for target_id in sorted(children.get(current, ())):
            if target_id not in reachable and target_id != source_node_id:
                reachable.add(target_id)
                pending.append(target_id)

    basis = {
        absolute: label
        for absolute, label in (
            _route_smoke_path(path, root, allow_outside=True)
            for path in certification_basis_paths
        )
    }
    mappings: dict[str, RouteSmokeDependencyMapping] = {}
    for absolute, relative in (
        _route_smoke_path(path, root, allow_outside=True) for path in loaded_paths
    ):
        try:
            absolute.relative_to(root)
        except ValueError:
            basis_label = basis.get(absolute)
            if basis_label is None:
                raise CertificationHashError(
                    f"unmapped route-smoke dependency outside repository: {absolute}"
                )
            mapping = RouteSmokeDependencyMapping(
                basis_label, "certification-basis", None
            )
            mappings[basis_label] = mapping
            continue
        if relative in manifest_paths[source_node_id]:
            mapping = RouteSmokeDependencyMapping(
                relative, "direct-input", source_node_id
            )
        else:
            direct_owner = graph.direct_file_owners.get(absolute)
            candidates = {
                target_id
                for target_id in reachable
                if relative in manifest_paths[target_id]
            }
            if direct_owner in candidates:
                candidates = {direct_owner}
            if len(candidates) == 1:
                mapping = RouteSmokeDependencyMapping(
                    relative,
                    "certification-dependency",
                    next(iter(candidates)),
                )
            elif candidates:
                raise CertificationHashError(
                    f"unmapped route-smoke dependency {relative}: ambiguous authority"
                )
            elif absolute in basis:
                mapping = RouteSmokeDependencyMapping(
                    relative, "certification-basis", None
                )
            else:
                detail = "no authority" if not candidates else "ambiguous authority"
                raise CertificationHashError(
                    f"unmapped route-smoke dependency {relative}: {detail}"
                )
        mappings[relative] = mapping
    return tuple(mappings[path] for path in sorted(mappings))


def route_smoke_trace_signature(
    mappings: Iterable[RouteSmokeDependencyMapping],
) -> tuple[tuple[str, str, str | None], ...]:
    """Return the stable projection compared by the certification audit."""

    return tuple(
        sorted(
            (mapping.path, mapping.authority, mapping.target_node_id)
            for mapping in mappings
        )
    )


_STABLE_CHECK_FIELDS = ("id", "version", "passed", "findings")


def _default_schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "references" / "blueprint"



def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def certification_basis_roots_path(repo_root: Path) -> Path:
    """Return the one repository-owned certification-basis manifest path."""

    return Path(repo_root).resolve() / CERTIFICATION_BASIS_MANIFEST


def _tracked_basis_paths_at_head(root: Path) -> tuple[PurePosixPath, ...]:
    """Return paths tracked at one captured HEAD, or none outside a Git root."""

    snapshot = capture_git_snapshot(root)
    if snapshot is None or snapshot.repo_root != root:
        return ()
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-tree",
                "-r",
                "-z",
                "--name-only",
                snapshot.commit,
            ],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise CertificationHashError(
            f"cannot enumerate certification basis at {snapshot.commit}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CertificationHashError(
            f"cannot enumerate certification basis at {snapshot.commit}: {detail}"
        )
    return tuple(
        PurePosixPath(os.fsdecode(raw_path))
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    )


def _basis_pattern_matches(path: PurePosixPath, pattern: PurePosixPath) -> bool:
    """Match pathlib-style path segments, with ``**`` matching zero or more."""

    path_parts = path.parts
    pattern_parts = pattern.parts

    @lru_cache(maxsize=None)
    def matches(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return matches(pattern_index + 1, path_index) or (
                path_index < len(path_parts)
                and matches(pattern_index, path_index + 1)
            )
        return (
            path_index < len(path_parts)
            and fnmatchcase(path_parts[path_index], part)
            and matches(pattern_index + 1, path_index + 1)
        )

    return matches(0, 0)


def resolve_certification_basis_paths(
    repo_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> tuple[Path, ...]:
    """Resolve the canonical manifest without accepting caller-selected inputs."""

    root = Path(repo_root).resolve()
    manifest = certification_basis_roots_path(root)
    try:
        raw = json.loads(
            read_regular_file_bytes(
                manifest,
                allowed_root=root,
                allow_non_atomic=allow_non_atomic,
            ).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CertificationHashError(
            f"cannot read certification basis manifest {manifest}: {exc}"
        ) from exc
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise CertificationHashError(
            f"{manifest}: certification basis manifest must contain a JSON string list"
        )
    tracked_at_head = _tracked_basis_paths_at_head(root)
    selected = {manifest}
    for pattern in raw:
        relative = Path(pattern)
        if relative.is_absolute() or ".." in relative.parts:
            raise CertificationHashError(
                f"certification basis root must stay under target package: {pattern}"
            )
        is_pattern = any(character in pattern for character in "*?[]")
        current_candidates = (
            sorted(root.glob(pattern), key=lambda path: path.as_posix())
            if is_pattern
            else [root / relative]
        )
        tracked_candidates = {
            root.joinpath(*path.parts)
            for path in tracked_at_head
            if _basis_pattern_matches(path, PurePosixPath(pattern))
        }
        matched_regular_file = False
        for path in sorted(
            {*current_candidates, *tracked_candidates},
            key=lambda candidate: candidate.as_posix(),
        ):
            try:
                read_regular_file_bytes(
                    path,
                    allowed_root=root,
                    allow_non_atomic=allow_non_atomic,
                )
            except OSError as exc:
                try:
                    path.lstat()
                except FileNotFoundError:
                    if path not in tracked_candidates:
                        continue
                    raise CertificationHashError(
                        f"tracked certification basis input is missing: {path}"
                    ) from exc
                except OSError:
                    pass
                raise CertificationHashError(
                    "certification basis input is not a confined regular file: "
                    f"{path}: {exc}"
                ) from exc
            selected.add(path)
            matched_regular_file = True
        if not matched_regular_file:
            raise CertificationHashError(
                f"certification basis pattern matched no files: {pattern}"
            )
    return tuple(sorted(selected, key=lambda path: path.as_posix()))


def compute_certification_basis_hash(
    repo_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> str:
    """Hash the one canonical v4 certification-basis manifest and its files."""

    root = Path(repo_root).resolve()
    entries: list[dict[str, str]] = []
    for path in resolve_certification_basis_paths(
        root,
        allow_non_atomic=allow_non_atomic,
    ):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise CertificationHashError(
                f"certification basis input is outside repository: {path}"
            ) from exc
        data = read_regular_file_bytes(
            path,
            allowed_root=root,
            allow_non_atomic=allow_non_atomic,
        )
        if path == root / CANONICAL_NODE_HASH_POLICY:
            try:
                data = _canonical_bytes(yaml.safe_load(data.decode("utf-8")))
            except (UnicodeError, yaml.YAMLError, TypeError, ValueError) as exc:
                raise CertificationHashError(
                    f"cannot canonicalize node hash policy: {path}"
                ) from exc
        entries.append({"path": relative, "digest": _hash_bytes(data)})
    if not entries:
        raise CertificationHashError("certification basis must contain at least one input")
    return _hash_value(entries)


def expected_certifier_checks() -> tuple[dict[str, object], ...]:
    """Return the exact passed records owned by the versioned certifier registry."""

    return normalize_node_checks(
        {
            "id": check_id,
            "version": version,
            "passed": True,
            "findings": [],
        }
        for check_id, version in CERTIFIER_CHECK_REGISTRY.values()
    )


def derive_certifier_identity(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    source_commit: str,
) -> dict[str, object]:
    """Derive the certifier identity from the current v4 graph and Git snapshot."""

    node = graph.nodes.get(CERTIFIER_NODE_ID)
    if node is None or node.node_type != "module":
        raise CertificationHashError("canonical certifier module is absent from the v4 graph")
    export = graph.exports.get(CERTIFIER_INTERFACE_ID)
    if (
        export is None
        or export.module_node_id != CERTIFIER_NODE_ID
        or export.version != CERTIFIER_INTERFACE_VERSION
    ):
        raise CertificationHashError(
            "canonical certifier interface is absent or has the wrong version"
        )
    state = states.get(CERTIFIER_NODE_ID)
    node_hash = state.node_hash if isinstance(state, NodeHashState) else None
    if (
        not isinstance(node_hash, str)
        or len(node_hash) != 71
        or not node_hash.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in node_hash[7:])
    ):
        raise CertificationHashError("canonical certifier node state is unavailable")
    if (
        not isinstance(source_commit, str)
        or len(source_commit) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in source_commit)
    ):
        raise CertificationHashError("canonical certifier source commit is unavailable")
    return {
        "interface": CERTIFIER_INTERFACE_ID,
        "version": CERTIFIER_INTERFACE_VERSION,
        "node_hash": node_hash,
        "source_commit": source_commit,
    }


def _hash_value(value: Any) -> str:
    return _hash_bytes(_canonical_bytes(value))


def _reference_candidates(value: object) -> tuple[tuple[str, str], ...]:
    found: set[tuple[str, str]] = set()

    def visit(current: object) -> None:
        if isinstance(current, Mapping):
            path = current.get("path")
            fragment = current.get("fragment")
            if isinstance(path, str) and isinstance(fragment, str) and fragment.startswith("#"):
                found.add((path, fragment))
            for key, child in current.items():
                if key == "contract_references":
                    continue
                if (
                    key in {"schema", "format"}
                    and isinstance(child, str)
                    and child.lower().endswith((".json", ".yaml", ".yml"))
                ):
                    found.add((child, "#"))
                visit(child)
        elif isinstance(current, list):
            for child in current:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def _validated_owned_input(owner_root: Path, path: Path) -> Path:
    """Resolve one regular file beneath its owner without following symlinks."""

    owner_absolute = Path(os.path.abspath(owner_root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(owner_absolute)
    except ValueError as exc:
        raise CertificationHashError(
            f"{path}: input must remain under {owner_root}"
        ) from exc
    current = owner_absolute
    try:
        for component in relative.parts:
            current = current / component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise CertificationHashError(
                    f"{path}: input contains a symlink component"
                )
        if not stat.S_ISREG(metadata.st_mode):
            raise CertificationHashError(f"{path}: input must be a regular file")
    except FileNotFoundError as exc:
        raise CertificationHashError(f"{path}: input does not exist") from exc
    try:
        path_absolute.resolve(strict=True).relative_to(
            owner_absolute.resolve(strict=True)
        )
    except ValueError as exc:
        raise CertificationHashError(f"{path}: input resolves outside its owner") from exc
    return path_absolute


def _resolve_reference_path(owner_root: Path, base: Path, locator: str) -> Path:
    relative = Path(locator)
    if relative.is_absolute():
        raise CertificationHashError(
            f"reference path {locator!r} must be relative to its locator base"
        )
    candidate = Path(os.path.abspath(base / relative))
    owner = Path(os.path.abspath(owner_root))
    try:
        candidate.relative_to(owner)
    except ValueError as exc:
        raise CertificationHashError(
            f"reference path {locator!r} escapes its confinement root"
        ) from exc
    return _validated_owned_input(owner, candidate)


def _parse_reference_document(path: Path, payload: bytes) -> object:
    try:
        text = payload.decode("utf-8")
        if path.suffix == ".json" or path.name.endswith(".schema.json"):
            return json.loads(text)
        return yaml.safe_load(text)
    except (UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise CertificationHashError(f"{path}: cannot parse referenced document: {exc}") from exc


def _validate_fragment(document: object, fragment: str, path: Path) -> None:
    if fragment in {"", "#"}:
        return
    if not fragment.startswith("#/"):
        raise CertificationHashError(f"{path}: unsupported reference fragment {fragment!r}")
    current = document
    try:
        for raw_part in fragment[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, Mapping):
                current = current[part]
            else:
                raise KeyError(part)
    except (KeyError, IndexError, ValueError) as exc:
        raise CertificationHashError(f"{path}: unresolved reference fragment {fragment!r}") from exc


def _recursive_contract_references(
    owner_root: Path,
    seeds: Iterable[tuple[str, str]],
) -> tuple[_ContractReference, ...]:
    """Resolve the complete confined file closure of authored contract locators."""

    return _recursive_contract_references_from_roots(
        (owner_root, owner_root, path, fragment)
        for path, fragment in seeds
    )


def _recursive_contract_references_from_roots(
    seeds: Iterable[tuple[Path, Path, str, str]],
) -> tuple[_ContractReference, ...]:
    """Resolve contract closures with an explicit confinement and locator base."""

    pending = list(seeds)
    entries: dict[str, _ContractReference] = {}
    parsed_paths: set[Path] = set()
    while pending:
        confined_root, base, locator_path, fragment = pending.pop(0)
        path = _resolve_reference_path(confined_root, base, locator_path)
        relative = path.relative_to(confined_root).as_posix()
        locator = f"{relative}{fragment}"
        payload = path.read_bytes()
        document = _parse_reference_document(path, payload)
        _validate_fragment(document, fragment, path)
        entries[locator] = _ContractReference(locator, path, _hash_bytes(payload))
        if path in parsed_paths:
            continue
        parsed_paths.add(path)
        for child_path, child_fragment in _reference_candidates(document):
            pending.append(
                (confined_root, path.parent, child_path, child_fragment)
            )
        if isinstance(document, (Mapping, list)):
            refs: list[str] = []

            def collect_refs(current: object) -> None:
                if isinstance(current, Mapping):
                    ref = current.get("$ref")
                    if isinstance(ref, str):
                        refs.append(ref)
                    for child in current.values():
                        collect_refs(child)
                elif isinstance(current, list):
                    for child in current:
                        collect_refs(child)

            collect_refs(document)
            for ref in sorted(set(refs)):
                path_text, separator, ref_fragment = ref.partition("#")
                if not path_text:
                    continue
                if "://" in path_text:
                    raise CertificationHashError(
                        f"{path}: external reference URI is unsupported: {ref}"
                    )
                pending.append((
                    confined_root,
                    path.parent,
                    path_text,
                    f"#{ref_fragment}" if separator else "#",
                ))
    return tuple(entries[locator] for locator in sorted(entries))


def normalize_node_checks(
    checks: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Project node checks to stable semantic fields in canonical order."""

    normalized = []
    identities: set[tuple[object, object]] = set()
    for check in checks:
        if not isinstance(check, Mapping):
            raise CertificationHashError("node check must be a mapping")
        try:
            item = {field: deepcopy(check[field]) for field in _STABLE_CHECK_FIELDS}
        except KeyError as exc:
            raise CertificationHashError(
                f"node check is missing stable field {exc.args[0]!r}"
            ) from exc
        if item["passed"] is not True:
            raise CertificationHashError("cannot certify failed node check")
        identity = (item["id"], item["version"])
        try:
            duplicate = identity in identities
        except TypeError as exc:
            raise CertificationHashError("node check identity must be scalar") from exc
        if duplicate:
            raise CertificationHashError(
                f"duplicate node check identity {identity[0]!r} version {identity[1]!r}"
            )
        identities.add(identity)
        normalized.append(item)
    try:
        return tuple(
            sorted(normalized, key=lambda item: (str(item["id"]), int(item["version"])))
        )
    except (TypeError, ValueError) as exc:
        raise CertificationHashError("node check version must be an integer") from exc



def load_node_hash_policy(
    policy_path: Path,
    *,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate the canonical ordered node-input policy."""

    path = Path(policy_path)
    selected_schema = (
        Path(schema_path)
        if schema_path is not None
        else _default_schema_root().parent / "certification" / "node-hash-policy.schema.json"
    )
    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema = json.loads(selected_schema.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise CertificationHashError(f"{path}: cannot load node hash policy: {exc}") from exc
    if not isinstance(policy, dict):
        raise CertificationHashError(f"{path}: node hash policy must be a mapping")
    errors = sorted(
        jsonschema.Draft7Validator(schema).iter_errors(policy),
        key=lambda error: (tuple(str(part) for part in error.path), error.message),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(f"[{part!r}]" for part in first.path)
        raise CertificationHashError(
            f"{path}: node hash policy schema error at {location}: {first.message}"
        )
    return policy


def _git_exclude_matches(
    repo_root: Path,
    relative_paths: Iterable[str],
    pattern: str,
) -> set[str]:
    """Return candidate files matched by one Git exclude pattern."""

    candidates = tuple(sorted(set(relative_paths)))
    if not candidates:
        return set()
    for relative in candidates:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise CertificationHashError(
                f"Git exclude candidate must be repository-relative: {relative}"
            )
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(Path(repo_root).resolve()),
                "--literal-pathspecs",
                "ls-files",
                "--cached",
                "--others",
                "--ignored",
                "-z",
                f"--exclude={pattern}",
                "--",
                *candidates,
            ],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise CertificationHashError(
            f"cannot apply Git exclude pattern {pattern!r}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CertificationHashError(
            f"cannot apply Git exclude pattern {pattern!r}: {detail}"
        )
    return {
        os.fsdecode(path)
        for path in result.stdout.split(b"\0")
        if path
    }


def _reserved_certification_output(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    name = path.name
    return (
        ".certificates" in path.parts
        or ".certificate-history" in path.parts
        or "pooled-blueprint-review" in name
    )


def _read_node_input(
    node: BlueprintNode,
    path: Path,
    repo_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> bytes:
    try:
        path.relative_to(repo_root)
        return read_regular_file_bytes(
            path,
            allowed_root=node.skill_root,
            allow_non_atomic=allow_non_atomic,
        )
    except (AtomicWriteError, BlueprintGraphError, OSError, ValueError) as exc:
        raise CertificationHashError(str(exc)) from exc


def _v4_node_input_manifests(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
    policy: Mapping[str, Any],
    *,
    allow_non_atomic: bool = False,
) -> tuple[
    dict[str, tuple[dict[str, str], ...]],
    dict[str, set[str]],
]:
    """Resolve policy-selected inputs and cross-owner contract dependencies."""

    root = Path(repo_root).resolve()
    owned_paths = {
        Path(os.path.abspath(path)): owner_id
        for path, owner_id in graph.direct_file_owners.items()
    }
    contract_dependencies: dict[str, set[str]] = {
        node_id: set() for node_id in graph.nodes
    }
    mandatory_contract_paths: dict[str, set[Path]] = {
        node_id: set() for node_id in graph.nodes
    }
    for node_id, node in sorted(graph.nodes.items()):
        references = list(_recursive_contract_references(
            node.skill_root, _reference_candidates(node.declaration)
        ))
        structured = node.declaration.get("contract_references", [])
        if not isinstance(structured, list):
            raise CertificationHashError(
                f"{node.blueprint_path}: contract_references must be a list"
            )
        structured_seeds: list[tuple[Path, Path, str, str]] = []
        for index, locator in enumerate(structured):
            if not isinstance(locator, Mapping):
                raise CertificationHashError(
                    f"{node.blueprint_path}: contract_references[{index}] must be a mapping"
                )
            base_name = locator.get("base")
            path = locator.get("path")
            fragment = locator.get("fragment", "#")
            if (
                base_name not in {"module-root", "repository-root"}
                or not isinstance(path, str)
                or not isinstance(fragment, str)
            ):
                raise CertificationHashError(
                    f"{node.blueprint_path}: invalid contract_references[{index}]"
                )
            confined = node.skill_root if base_name == "module-root" else root
            structured_seeds.append((confined, confined, path, fragment))
        references.extend(
            _recursive_contract_references_from_roots(structured_seeds)
        )
        for reference in references:
            canonical_reference = Path(os.path.abspath(reference.path))
            owner_id = owned_paths.get(canonical_reference)
            if owner_id is None:
                raise CertificationHashError(
                    f"{node.blueprint_path}: referenced contract "
                    f"{reference.locator!r} is not directly owned by a node"
                )
            mandatory_contract_paths[owner_id].add(canonical_reference)
            if owner_id != node_id:
                contract_dependencies[node_id].add(owner_id)

    mandatory_paths_by_node: dict[str, set[Path]] = {}
    all_paths = set(owned_paths)
    for node_id, node in sorted(graph.nodes.items()):
        mandatory_paths = {
            Path(os.path.abspath(node.blueprint_path)),
        }
        if node.gateway_path is not None:
            mandatory_paths.add(Path(os.path.abspath(node.gateway_path)))
        mandatory_paths.update(mandatory_contract_paths[node_id])
        for mandatory_path in mandatory_paths:
            try:
                mandatory_path.relative_to(root)
            except ValueError as exc:
                raise CertificationHashError(
                    f"{mandatory_path}: mandatory node input is outside the repository"
                ) from exc
        mandatory_paths_by_node[node_id] = mandatory_paths
        all_paths.update(mandatory_paths)

    relative_paths: dict[Path, str] = {}
    for path in sorted(all_paths):
        try:
            relative_paths[path] = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise CertificationHashError(
                f"{path}: cannot determine Git provenance"
            ) from exc
    try:
        provenance = git_file_provenance_batch(root, all_paths)
    except (ValueError, OSError) as exc:
        raise CertificationHashError(
            f"{root}: cannot determine Git provenance"
        ) from exc

    selected = {
        path: provenance[path] == "tracked"
        for path in owned_paths
    }
    final_actions: dict[Path, str] = {}
    raw_rules = policy.get("rules")
    if not isinstance(raw_rules, list):
        raise CertificationHashError("node hash policy rules must be a list")
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, Mapping):
            raise CertificationHashError(f"node hash policy rule {index} must be a mapping")
        action = rule.get("action")
        pattern = rule.get("pattern")
        if action not in {"include", "exclude"} or not isinstance(pattern, str):
            raise CertificationHashError(f"node hash policy rule {index} is invalid")
        matched_relatives = _git_exclude_matches(
            root,
            relative_paths.values(),
            pattern,
        )
        matches = [
            path for path, relative in relative_paths.items()
            if relative in matched_relatives
        ]
        owned_matches = [
            path for path in matches
            if path in owned_paths
        ]
        if (
            action == "include"
            and rule.get("require_match") is True
            and not owned_matches
        ):
            raise CertificationHashError(
                f"node hash policy include {pattern!r} requires at least one match"
            )
        for path in owned_matches:
            selected[path] = action == "include"
        for path in matches:
            final_actions[path] = action

    manifests: dict[str, tuple[dict[str, str], ...]] = {}
    for node_id, node in sorted(graph.nodes.items()):
        node_paths = {
            path
            for path, owner_id in owned_paths.items()
            if owner_id == node_id and selected[path]
        }
        for mandatory_path in mandatory_paths_by_node[node_id]:
            relative = relative_paths[mandatory_path]
            if final_actions.get(mandatory_path) == "exclude":
                raise CertificationHashError(
                    f"{node_id}: mandatory blueprint, gateway, or contract input "
                    f"cannot be excluded: {relative}"
                )
            node_paths.add(mandatory_path)

        entries: list[dict[str, str]] = []
        for path in sorted(node_paths):
            relative = relative_paths[path]
            if _reserved_certification_output(relative):
                raise CertificationHashError(
                    f"{relative}: reserved certification output cannot be a node input"
                )
            payload = _read_node_input(
                node,
                path,
                root,
                allow_non_atomic=allow_non_atomic,
            )
            entries.append(
                {
                    "path": relative,
                    "digest": _hash_bytes(payload),
                    "git_provenance": provenance[path],
                }
            )
        manifests[node_id] = tuple(entries)
    return manifests, contract_dependencies


def _compute_v4_node_hash_states(
    graph: RepositoryBlueprintGraph,
    *,
    repo_root: Path,
    policy_path: Path,
    certification_basis_hash: str,
    certification_basis_paths: Iterable[Path | str],
    allow_non_atomic: bool = False,
) -> dict[str, NodeHashState]:
    policy = load_node_hash_policy(policy_path)
    manifests, contract_dependencies = _v4_node_input_manifests(
        graph,
        repo_root,
        policy,
        allow_non_atomic=allow_non_atomic,
    )
    node_hashes = {
        node_id: _hash_value(
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "version": node.version,
                "input_manifest": manifests[node_id],
            }
        )
        for node_id, node in sorted(graph.nodes.items())
    }
    dependencies_by_node: dict[str, set[tuple[str, str, int | None]]] = {
        node_id: set() for node_id in graph.nodes
    }
    for edge in graph.certification_edges:
        dependencies_by_node[edge.source_node_id].add(
            (edge.relation, edge.target_node_id, edge.target_version)
        )
    for source_id, target_ids in contract_dependencies.items():
        for target_id in target_ids:
            dependencies_by_node[source_id].add(
                (
                    "references-cross-owner-contract",
                    target_id,
                    graph.nodes[target_id].version,
                )
            )

    visiting: list[str] = []
    visited: set[str] = set()

    def reject_dependency_cycle(node_id: str) -> None:
        if node_id in visiting:
            start = visiting.index(node_id)
            cycle = visiting[start:] + [node_id]
            raise CertificationHashError(
                "certification dependency cycle includes " + " -> ".join(cycle)
            )
        if node_id in visited:
            return
        visiting.append(node_id)
        for _relation, target_id, _version in sorted(
            dependencies_by_node[node_id],
            key=lambda item: (item[0], item[1], item[2] or 0),
        ):
            reject_dependency_cycle(target_id)
        visiting.pop()
        visited.add(node_id)

    for node_id in sorted(graph.nodes):
        reject_dependency_cycle(node_id)

    states: dict[str, NodeHashState] = {}
    for node_id in sorted(graph.nodes):
        dependency_hashes = tuple(
            {
                "relation": relation,
                "target": target_id,
                "version": version,
                "node_hash": node_hashes[target_id],
            }
            for relation, target_id, version in sorted(
                dependencies_by_node[node_id],
                key=lambda item: (item[0], item[1], item[2] or 0),
            )
        )
        states[node_id] = NodeHashState(
            node_hash=node_hashes[node_id],
            input_manifest=manifests[node_id],
            dependency_hashes=dependency_hashes,
            certification_basis_hash=certification_basis_hash,
        )
    return states


def compute_node_hash_states(
    graph: RepositoryBlueprintGraph,
    *,
    repo_root: Path,
    policy_path: Path,
    certification_basis_hash: str,
    certification_basis_paths: Iterable[Path | str] = (),
    allow_non_atomic: bool = False,
) -> dict[str, NodeHashState]:
    """Compute the canonical v4 node hashes and dependency hashes."""

    if not isinstance(graph, RepositoryBlueprintGraph) or any(
        node.declaration.get("schema_version") != 4
        for node in graph.nodes.values()
    ):
        raise CertificationHashError("node hashing accepts only an all-v4 repository graph")
    return _compute_v4_node_hash_states(
        graph,
        repo_root=repo_root,
        policy_path=policy_path,
        certification_basis_hash=certification_basis_hash,
        certification_basis_paths=certification_basis_paths,
        allow_non_atomic=allow_non_atomic,
    )
