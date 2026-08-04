"""Immutable, atomically activated route data for bounded host dispatch."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
from typing import Any, Mapping
import zlib

from officina.common.atomic_files import (
    AtomicWriteError,
    atomic_replace_bytes,
    read_regular_file_bytes,
)
from officina.common.blueprint_graph import (
    BlueprintDiagnostic,
    DispatchBlueprintGraph,
)
from officina.common.certification_types import CertificationDecision
from officina.dispatcher.catalog import (
    CatalogRoute,
    decode_repository_graph,
    encode_repository_graph,
)
from officina.dispatcher.errors import (
    DispatcherSnapshotError,
    UnauthorizedCallerError,
)


FORMAT_VERSION = 1
AUTHORIZATION_SEMANTICS_VERSION = 1
_REPAIR_MODULE = "officina.install.dispatch_snapshot_builder"


@dataclass(frozen=True)
class SnapshotRoute:
    """One exact caller/target route and its advisory certification state."""

    graph: DispatchBlueprintGraph | None
    certification: CertificationDecision | None
    denial: str | None = None


def _repair_message(repo_root: Path) -> str:
    return (
        "Run the snapshot builder directly: "
        f"python -m {_REPAIR_MODULE} --repo-root {repo_root}"
    )


def _error(
    code: str,
    message: str,
    *,
    repo_root: Path,
    route: CatalogRoute | None = None,
) -> DispatcherSnapshotError:
    return DispatcherSnapshotError(
        f"{message}. {_repair_message(repo_root)}",
        code=code,
        caller_module_id=route.caller_module_id if route is not None else "",
        target_module_id=(
            route.target_interface_id.split(".interface.", 1)[0]
            if route is not None
            else ""
        ),
    )


def selected_snapshot_root(snapshot_root: Path | None = None) -> Path:
    """Return the host-neutral dispatcher snapshot data root."""

    if snapshot_root is not None:
        return Path(snapshot_root).resolve()
    configured = os.environ.get("XDG_DATA_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "share"
    return (base / "famulus" / "dispatcher" / "snapshots").resolve()


def repository_snapshot_root(
    repo_root: Path,
    *,
    snapshot_root: Path | None = None,
) -> Path:
    root = Path(repo_root).resolve()
    repo_key = hashlib.sha256(root.as_posix().encode("utf-8")).hexdigest()[:24]
    return selected_snapshot_root(snapshot_root) / repo_key


def _route_key(route: CatalogRoute) -> str:
    identity = json.dumps(
        [route.caller_module_id, route.target_interface_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _confined_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise ValueError("snapshot path must be a string")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("snapshot path must be a confined relative path")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        raise ValueError("snapshot path escapes its repository snapshot root")
    return resolved


def _read_json(path: Path, *, allowed_root: Path) -> dict[str, Any]:
    raw = read_regular_file_bytes(path, allowed_root=allowed_root, allow_non_atomic=True)
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("snapshot JSON root must be an object")
    return decoded


def _encode_diagnostic(item: BlueprintDiagnostic, repo_root: Path) -> dict[str, Any]:
    path: str | None = None
    if item.path is not None:
        resolved = item.path.resolve()
        if resolved != repo_root and not resolved.is_relative_to(repo_root):
            raise ValueError(f"diagnostic path escapes repository: {item.path}")
        path = resolved.relative_to(repo_root).as_posix()
    return {"code": item.code, "message": item.message, "path": path}


def _decode_diagnostic(payload: object, repo_root: Path) -> BlueprintDiagnostic:
    if not isinstance(payload, dict) or set(payload) != {"code", "message", "path"}:
        raise ValueError("malformed snapshot diagnostic")
    code = payload["code"]
    message = payload["message"]
    relative = payload["path"]
    if not isinstance(code, str) or not code or not isinstance(message, str) or not message:
        raise ValueError("malformed snapshot diagnostic")
    path = None if relative is None else _confined_path(repo_root, relative)
    return BlueprintDiagnostic(code=code, message=message, path=path)


def _encode_route(route: CatalogRoute, value: SnapshotRoute, repo_root: Path) -> bytes:
    certification = value.certification
    graph = value.graph
    return _json_bytes(
        {
            "format_version": FORMAT_VERSION,
            "caller_module_id": route.caller_module_id,
            "target_interface_id": route.target_interface_id,
            "graph": (
                None if graph is None else encode_repository_graph(graph.graph)
            ),
            "diagnostics": [
                _encode_diagnostic(item, repo_root)
                for item in (() if graph is None else graph.diagnostics)
            ],
            "denial": value.denial,
            "certification": (
                None
                if certification is None
                else {
                    "certified": certification.certified,
                    "code": certification.code,
                    "message": certification.message,
                }
            ),
        }
    )


def _decode_route(
    payload: dict[str, Any],
    *,
    repo_root: Path,
    route: CatalogRoute,
) -> SnapshotRoute:
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported route record version")
    if payload.get("caller_module_id") != route.caller_module_id or payload.get(
        "target_interface_id"
    ) != route.target_interface_id:
        raise ValueError("route record identity mismatch")
    raw_graph = payload.get("graph")
    raw_diagnostics = payload.get("diagnostics")
    denial = payload.get("denial")
    if denial is not None and (not isinstance(denial, str) or not denial):
        raise ValueError("malformed route denial")
    if raw_graph is None:
        if denial is None or raw_diagnostics != []:
            raise ValueError("denied route requires one diagnostic and no graph")
        graph = None
    elif not isinstance(raw_graph, dict) or not isinstance(raw_diagnostics, list):
        raise ValueError("malformed route graph")
    else:
        if denial is not None:
            raise ValueError("authorized route cannot carry a denial")
        graph = DispatchBlueprintGraph(
            decode_repository_graph(raw_graph, repo_root=repo_root),
            tuple(_decode_diagnostic(item, repo_root) for item in raw_diagnostics),
        )
    raw_certification = payload.get("certification")
    certification = None
    if raw_certification is not None:
        if not isinstance(raw_certification, dict) or set(raw_certification) != {
            "certified",
            "code",
            "message",
        }:
            raise ValueError("malformed route certification status")
        certification = CertificationDecision(
            raw_certification["certified"],
            raw_certification["code"],
            raw_certification["message"],
        )
    return SnapshotRoute(
        graph=graph,
        certification=certification,
        denial=denial,
    )


def _load_manifest(
    repo_root: Path,
    *,
    snapshot_root: Path | None,
    route: CatalogRoute | None,
) -> tuple[Path, dict[str, Any]]:
    repository_root = repository_snapshot_root(repo_root, snapshot_root=snapshot_root)
    pointer_path = repository_root / "current.json"
    if not pointer_path.exists():
        raise _error(
            "dispatcher.snapshot_missing",
            "active dispatcher snapshot is missing",
            repo_root=repo_root,
            route=route,
        )
    try:
        pointer = _read_json(pointer_path, allowed_root=repository_root)
        if pointer.get("format_version") != FORMAT_VERSION:
            raise _error(
                "dispatcher.snapshot_unsupported",
                "active dispatcher snapshot pointer version is unsupported",
                repo_root=repo_root,
                route=route,
            )
        manifest_path = _confined_path(repository_root, pointer.get("manifest"))
        manifest = _read_json(manifest_path, allowed_root=repository_root)
    except DispatcherSnapshotError:
        raise
    except (AtomicWriteError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _error(
            "dispatcher.snapshot_malformed",
            "active dispatcher snapshot is malformed",
            repo_root=repo_root,
            route=route,
        ) from exc
    if manifest.get("format_version") != FORMAT_VERSION or manifest.get(
        "authorization_semantics_version"
    ) != AUTHORIZATION_SEMANTICS_VERSION:
        raise _error(
            "dispatcher.snapshot_unsupported",
            "active dispatcher snapshot semantics are unsupported",
            repo_root=repo_root,
            route=route,
        )
    if manifest.get("repo_root") != repo_root.resolve().as_posix() or not isinstance(
        manifest.get("routes"), dict
    ):
        raise _error(
            "dispatcher.snapshot_malformed",
            "active dispatcher snapshot manifest is malformed",
            repo_root=repo_root,
            route=route,
        )
    return repository_root, manifest


def load_snapshot_route(
    repo_root: Path,
    route: CatalogRoute,
    *,
    snapshot_root: Path | None = None,
) -> SnapshotRoute:
    """Load one exact route without inspecting repository source state."""

    root = Path(repo_root).resolve()
    repository_root, manifest = _load_manifest(
        root,
        snapshot_root=snapshot_root,
        route=route,
    )
    entry = manifest["routes"].get(_route_key(route))
    if not isinstance(entry, dict):
        raise _error(
            "dispatcher.snapshot_route_missing",
            f"active dispatcher snapshot has no route for {route.caller_module_id} -> {route.target_interface_id}",
            repo_root=root,
            route=route,
        )
    if set(entry) == {"caller_module_id", "target_interface_id", "denial"}:
        if (
            entry["caller_module_id"] != route.caller_module_id
            or entry["target_interface_id"] != route.target_interface_id
            or not isinstance(entry["denial"], str)
            or not entry["denial"]
        ):
            raise _error(
                "dispatcher.snapshot_malformed",
                "active dispatcher denial record is malformed",
                repo_root=root,
                route=route,
            )
        raise UnauthorizedCallerError(
            caller_module_id=route.caller_module_id,
            target_module_id=route.target_interface_id.split(".interface.", 1)[0],
            interface_id=route.target_interface_id,
            diagnostic=entry["denial"],
        )
    if set(entry) != {"path", "sha256"}:
        raise _error(
            "dispatcher.snapshot_malformed",
            "active dispatcher route index entry is malformed",
            repo_root=root,
            route=route,
        )
    try:
        record_path = _confined_path(repository_root, entry["path"])
        stored = read_regular_file_bytes(
            record_path,
            allowed_root=repository_root,
            allow_non_atomic=True,
        )
        if not isinstance(entry["sha256"], str) or hashlib.sha256(stored).hexdigest() != entry["sha256"]:
            raise ValueError("route record digest mismatch")
        raw = zlib.decompress(stored)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("route record root must be an object")
        loaded = _decode_route(payload, repo_root=root, route=route)
    except (
        AtomicWriteError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        zlib.error,
    ) as exc:
        raise _error(
            "dispatcher.snapshot_malformed",
            "active dispatcher route record is malformed",
            repo_root=root,
            route=route,
        ) from exc
    if loaded.denial is not None:
        raise UnauthorizedCallerError(
            caller_module_id=route.caller_module_id,
            target_module_id=route.target_interface_id.split(".interface.", 1)[0],
            interface_id=route.target_interface_id,
            diagnostic=loaded.denial,
        )
    return loaded


def activate_snapshot(
    repo_root: Path,
    routes: Mapping[CatalogRoute, SnapshotRoute],
    *,
    snapshot_root: Path | None = None,
) -> Path:
    """Write, reload-check, and atomically activate one complete generation."""

    root = Path(repo_root).resolve()
    repository_root = repository_snapshot_root(root, snapshot_root=snapshot_root)
    generation_id = secrets.token_hex(16)
    generation_root = repository_root / "generations" / generation_id
    routes_root = generation_root / "routes"
    routes_root.mkdir(parents=True, exist_ok=False)
    manifest_routes: dict[str, dict[str, str]] = {}
    for route, value in sorted(
        routes.items(),
        key=lambda item: (item[0].caller_module_id, item[0].target_interface_id),
    ):
        key = _route_key(route)
        if value.denial is not None:
            manifest_routes[key] = {
                "caller_module_id": route.caller_module_id,
                "target_interface_id": route.target_interface_id,
                "denial": value.denial,
            }
            continue
        raw = _encode_route(route, value, root)
        stored = zlib.compress(raw, level=1)
        relative = f"generations/{generation_id}/routes/{key}.json.z"
        path = repository_root / relative
        path.write_bytes(stored)
        path.chmod(0o600)
        manifest_routes[key] = {
            "path": relative,
            "sha256": hashlib.sha256(stored).hexdigest(),
        }
    manifest_relative = f"generations/{generation_id}/manifest.json"
    manifest = {
        "format_version": FORMAT_VERSION,
        "authorization_semantics_version": AUTHORIZATION_SEMANTICS_VERSION,
        "repo_root": root.as_posix(),
        "generation_id": generation_id,
        "routes": manifest_routes,
    }
    atomic_replace_bytes(
        repository_root / manifest_relative,
        _json_bytes(manifest),
        allowed_root=repository_root,
        mode=0o600,
    )
    for route in routes:
        candidate = _load_route_from_manifest(
            root,
            route,
            repository_root=repository_root,
            manifest=manifest,
        )
        if candidate != routes[route]:
            raise ValueError(f"snapshot reload mismatch for {route}")
    atomic_replace_bytes(
        repository_root / "current.json",
        _json_bytes({"format_version": FORMAT_VERSION, "manifest": manifest_relative}),
        allowed_root=repository_root,
        mode=0o600,
    )
    _cleanup_generations(repository_root, active_generation_id=generation_id)
    return generation_root


def _cleanup_generations(
    repository_root: Path,
    *,
    active_generation_id: str,
) -> None:
    """Best-effort retention of the active and newest prior generation."""

    generations_root = repository_root / "generations"
    candidates: list[tuple[int, str, Path]] = []
    for path in generations_root.iterdir():
        if path.name == active_generation_id or path.is_symlink() or not path.is_dir():
            continue
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        candidates.append((modified, path.name, path))
    candidates.sort(reverse=True)
    for _modified, _name, path in candidates[1:]:
        try:
            shutil.rmtree(path)
        except OSError:
            continue


def _load_route_from_manifest(
    repo_root: Path,
    route: CatalogRoute,
    *,
    repository_root: Path,
    manifest: Mapping[str, Any],
) -> SnapshotRoute:
    entry = manifest["routes"][_route_key(route)]
    if "denial" in entry:
        return SnapshotRoute(
            graph=None,
            certification=None,
            denial=entry["denial"],
        )
    stored = read_regular_file_bytes(
        _confined_path(repository_root, entry["path"]),
        allowed_root=repository_root,
        allow_non_atomic=True,
    )
    if hashlib.sha256(stored).hexdigest() != entry["sha256"]:
        raise ValueError("route record digest mismatch")
    raw = zlib.decompress(stored)
    payload = json.loads(raw.decode("utf-8"))
    return _decode_route(payload, repo_root=repo_root, route=route)


__all__ = [
    "AUTHORIZATION_SEMANTICS_VERSION",
    "FORMAT_VERSION",
    "SnapshotRoute",
    "activate_snapshot",
    "load_snapshot_route",
    "repository_snapshot_root",
    "selected_snapshot_root",
]
