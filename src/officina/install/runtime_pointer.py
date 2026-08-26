"""Validate, read, and atomically activate managed-runtime pointers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from officina.common.atomic_files import atomic_replace_bytes
from officina.configuration.repository import (
    RepositoryConfigurationError,
    load_repository_configuration,
)


class RuntimePointerError(Exception):
    """Raised when the runtime pointer cannot be read or activated safely."""


@dataclass(frozen=True)
class RuntimePointer:
    release_id: str
    runtime_source: Path
    python_bin: Path
    repository_config: Path | None = None
    launcher_resources: Path | None = None
    installation_context: Path | None = None


@dataclass(frozen=True)
class InstalledContextRecord:
    schema_version: Literal[1, 2]
    release_id: str
    mode: Literal["standard", "development"]
    installation_id: str
    source_root: Path
    development_root: Path | None
    selected_home: Path | None
    codex_home: Path
    claude_home: Path


_SCHEMA3_KEYS = {
    "schema_version",
    "release_id",
    "runtime_source",
    "python_bin",
    "repository_config",
    "launcher_resources",
    "installation_context",
}
_CONTEXT_KEYS = {
    "schema_version",
    "release_id",
    "mode",
    "installation_id",
    "source_root",
    "development_root",
    "selected_home",
    "codex_home",
    "claude_home",
}
_CONTEXT_V1_KEYS = _CONTEXT_KEYS - {"selected_home"}
_DEVELOPMENT_ID = re.compile(r"dev-[0-9a-f]{32}\Z")


def _pointer_path(runtime_root: Path) -> Path:
    return runtime_root / "current.json"


def _require_contained(path: Path, *, root: Path, label: str) -> Path:
    """Return ``path`` resolved, or raise if it is not absolute and beneath ``root``.

    Fully resolves ``path``, including its leaf component: if the leaf is a
    symlink, its resolved target must itself live under ``root``. This is
    the strict, secure-by-default check — callers who need to accept a leaf
    symlink pointing at a specific trusted location outside ``root`` (e.g. a
    real ``uv``-managed interpreter symlink) must use
    ``_require_contained_or_trusted`` instead and pass that location
    explicitly; silently skipping leaf resolution here would let anyone who
    can write under ``root`` point ``python_bin`` at an arbitrary binary.
    """
    if not path.is_absolute():
        raise RuntimePointerError(f"{label} must be an absolute path: {path}")
    resolved_root = root.resolve()
    try:
        resolved = path.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimePointerError(f"{label} must live under runtime_root: {path}") from exc
    return resolved_root / resolved


def _require_absolute_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise RuntimePointerError(f"{label} must be an absolute path: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimePointerError(f"{label} must be an existing directory: {path}") from exc
    if not resolved.is_dir():
        raise RuntimePointerError(f"{label} must be an existing directory: {path}")
    return resolved


def load_installed_context_record(path: Path) -> InstalledContextRecord:
    """Load the exact immutable context-record schema selected by schema 3."""
    if not path.is_absolute() or not path.is_file():
        raise RuntimePointerError(
            f"installation_context must be an absolute regular file: {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimePointerError(f"cannot read installation_context: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimePointerError("installation_context must contain an exact supported schema")
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2}:
        raise RuntimePointerError("unsupported installation_context schema_version")
    expected_keys = _CONTEXT_V1_KEYS if schema_version == 1 else _CONTEXT_KEYS
    missing = expected_keys - set(payload)
    if missing:
        raise RuntimePointerError(
            f"installation_context is missing required field: {sorted(missing)[0]}"
        )
    if set(payload) != expected_keys:
        raise RuntimePointerError("installation_context must contain an exact supported schema")
    mode = payload.get("mode")
    release_id = payload.get("release_id")
    installation_id = payload.get("installation_id")
    if not isinstance(release_id, str) or not release_id:
        raise RuntimePointerError("installation_context release_id must be non-empty")
    if mode == "standard":
        if installation_id != "standard" or payload.get("development_root") is not None:
            raise RuntimePointerError("invalid standard installation_context identity")
        development_root = None
    elif mode == "development":
        if not isinstance(installation_id, str) or not _DEVELOPMENT_ID.fullmatch(
            installation_id
        ):
            raise RuntimePointerError("invalid development installation_context identity")
        raw_development_root = payload.get("development_root")
        if not isinstance(raw_development_root, str):
            raise RuntimePointerError("development installation_context needs development_root")
        development_root = _require_absolute_directory(
            Path(raw_development_root), label="development_root"
        )
    else:
        raise RuntimePointerError(f"invalid installation_context mode: {mode!r}")
    paths: dict[str, Path] = {}
    path_keys = ("source_root", "codex_home", "claude_home")
    if schema_version == 2:
        path_keys += ("selected_home",)
    for key in path_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise RuntimePointerError(f"installation_context {key} must be absolute")
        paths[key] = Path(value).resolve(strict=False)
    if mode == "development" and development_root != paths["source_root"]:
        raise RuntimePointerError(
            "development installation_context source_root must equal development_root"
        )
    return InstalledContextRecord(
        schema_version=schema_version,
        release_id=release_id,
        mode=mode,
        installation_id=installation_id,
        source_root=paths["source_root"],
        development_root=development_root,
        selected_home=paths.get("selected_home"),
        codex_home=paths["codex_home"],
        claude_home=paths["claude_home"],
    )


def load_deployed_resolver_trusted_roots(*, runtime_root: Path) -> tuple[Path, ...]:
    """Load the trusted interpreter roots selected by the fixed resolver."""
    resolver_root = runtime_root / "bootstrap" / "resolvers"
    fixed_root = resolver_root / "v1"
    active_path = fixed_root / "active.json"
    trust_path = fixed_root / "trusted-roots.json"
    if active_path.exists():
        try:
            active = json.loads(active_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimePointerError(
                f"cannot read active resolver generation: {exc}"
            ) from exc
        if not isinstance(active, dict) or set(active) != {
            "schema_version",
            "generation",
        }:
            raise RuntimePointerError("active resolver generation has invalid fields")
        generation = active.get("generation")
        if active.get("schema_version") != 1:
            raise RuntimePointerError("active resolver generation has unsupported schema")
        if not isinstance(generation, str) or re.fullmatch(r"[0-9a-f]{64}", generation) is None:
            raise RuntimePointerError("active resolver generation has invalid identity")
        generation_root = resolver_root / "generations" / generation
        try:
            resolved_generation = generation_root.resolve(strict=True)
            resolved_generations = (resolver_root / "generations").resolve(strict=True)
        except OSError as exc:
            raise RuntimePointerError(
                f"active resolver generation is incomplete: {exc}"
            ) from exc
        if resolved_generation.parent != resolved_generations:
            raise RuntimePointerError("active resolver generation escapes its root")
        if not (resolved_generation / "launch.py").is_file() or not (
            resolved_generation / "trusted-roots.json"
        ).is_file():
            raise RuntimePointerError("active resolver generation is incomplete")
        trust_path = resolved_generation / "trusted-roots.json"
    if not trust_path.exists():
        return ()
    try:
        entries = json.loads(trust_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimePointerError(f"cannot read resolver trusted roots: {exc}") from exc
    if not isinstance(entries, list) or not all(
        isinstance(entry, str) and bool(entry) and Path(entry).is_absolute()
        for entry in entries
    ):
        raise RuntimePointerError("resolver trusted roots must be absolute path strings")
    return tuple(Path(entry) for entry in entries)


def _validate_schema3_fields(
    *,
    runtime_root: Path,
    runtime_source: Path,
    release_id: str,
    launcher_resources: Path,
    installation_context: Path,
) -> tuple[Path, Path]:
    resolved_source = _require_contained(
        runtime_source, root=runtime_root, label="runtime_source"
    )
    expected_parent = (runtime_root / "releases").resolve(strict=False)
    if resolved_source.parent != expected_parent or resolved_source.name != release_id:
        raise RuntimePointerError(
            "schema-3 release_id/runtime_source identity does not match runtime_root/releases"
        )
    resolved_context = _require_contained(
        installation_context, root=resolved_source, label="installation_context"
    )
    if resolved_context != resolved_source / "installation-context.json":
        raise RuntimePointerError(
            "installation_context must be the candidate's installation-context.json"
        )
    record = load_installed_context_record(resolved_context)
    if record.release_id != release_id:
        raise RuntimePointerError(
            "installation_context release_id does not match pointer release_id"
        )
    resolved_resources = _require_absolute_directory(
        launcher_resources, label="launcher_resources"
    )
    expected_resources = (
        resolved_source / "launcher-resources"
        if record.mode == "standard"
        else record.source_root
    )
    if resolved_resources != expected_resources.resolve(strict=False):
        raise RuntimePointerError(
            f"launcher_resources does not match the {record.mode} context address"
        )
    return resolved_resources, resolved_context


def _require_contained_or_trusted(
    path: Path,
    *,
    root: Path,
    trusted_roots: tuple[Path, ...],
    label: str,
) -> Path:
    """Like ``_require_contained``, but also accepts a fully-resolved
    ``path`` that lives under one of ``trusted_roots`` instead of ``root``.

    Used only for ``python_bin``: the symlink file itself must still live
    under ``root`` (checked via its parent directory, without dereferencing
    the leaf), but a real ``uv``-managed venv's ``bin/python`` is itself a
    symlink pointing at uv's own interpreter store, which lives outside
    ``root``. Rather than skipping leaf validation entirely (which would let
    anyone who can write under ``root`` point ``python_bin`` at an arbitrary
    binary), the resolved leaf target is checked against an explicit
    allowlist of trusted roots supplied by the caller.

    This logic is intentionally duplicated (not imported) in
    ``officina.install.resolvers.launch._require_contained_or_trusted``: that
    module is a dependency-free, stdlib-only script deployed to run under the
    user's ambient Python before any interpreter handoff, so it cannot import
    this module (see its docstring for why). If you change the containment
    behavior here, update that copy too and rerun
    ``tests/test_officina_launcher_entry.py``'s
    ``test_resolver_containment_check_matches_real_runtime_pointer_implementation``
    cross-check, which fails loudly if the two implementations disagree on
    any of its adversarial test vectors.
    """
    if not path.is_absolute():
        raise RuntimePointerError(f"{label} must be an absolute path: {path}")
    resolved_root = root.resolve()
    resolved_parent = path.parent.resolve()
    try:
        relative_parent = resolved_parent.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimePointerError(f"{label} must live under runtime_root: {path}") from exc
    contained_path = resolved_root / relative_parent / path.name

    resolved_leaf = path.resolve()
    allowed_leaf_roots = (resolved_root, *(trusted_root.resolve() for trusted_root in trusted_roots))
    if not any(
        resolved_leaf == allowed or allowed in resolved_leaf.parents
        for allowed in allowed_leaf_roots
    ):
        raise RuntimePointerError(
            f"{label} resolves outside runtime_root and outside all trusted "
            f"interpreter roots: {path} -> {resolved_leaf}"
        )
    return contained_path


def decode_current_pointer(
    payload: object,
    *,
    runtime_root: Path,
    trusted_interpreter_roots: tuple[Path, ...] = (),
) -> RuntimePointer:
    if not isinstance(payload, dict):
        raise RuntimePointerError("current.json must contain a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2, 3}:
        raise RuntimePointerError(
            f"unsupported current.json schema_version: {payload.get('schema_version')!r}"
        )
    try:
        python_bin = Path(payload["python_bin"])
        runtime_source = Path(payload["runtime_source"])
        release_id = payload["release_id"]
    except KeyError as exc:
        raise RuntimePointerError(f"current.json missing required key: {exc}") from exc
    _require_contained_or_trusted(
        python_bin, root=runtime_root, trusted_roots=trusted_interpreter_roots, label="python_bin"
    )
    _require_contained(runtime_source, root=runtime_root, label="runtime_source")
    if not isinstance(release_id, str) or not release_id:
        raise RuntimePointerError("current.json release_id must be a non-empty string")
    repository_config = None
    if schema_version in {2, 3}:
        try:
            repository_config = Path(payload["repository_config"])
            validated = load_repository_configuration(repository_config)
        except KeyError as exc:
            raise RuntimePointerError(
                f"current.json missing required key: {exc}"
            ) from exc
        except (TypeError, RepositoryConfigurationError) as exc:
            raise RuntimePointerError(
                f"invalid repository_config: {exc}"
            ) from exc
        repository_config = validated.config_path
    launcher_resources = None
    installation_context = None
    if schema_version == 3:
        if set(payload) != _SCHEMA3_KEYS:
            raise RuntimePointerError(
                "schema-3 current.json must contain exactly the seven pointer fields"
            )
        try:
            launcher_resources, installation_context = _validate_schema3_fields(
                runtime_root=runtime_root,
                runtime_source=runtime_source,
                release_id=release_id,
                launcher_resources=Path(payload["launcher_resources"]),
                installation_context=Path(payload["installation_context"]),
            )
        except (KeyError, TypeError) as exc:
            raise RuntimePointerError(
                f"current.json missing required key: {exc}"
            ) from exc
    return RuntimePointer(
        release_id=release_id,
        runtime_source=runtime_source,
        python_bin=python_bin,
        repository_config=repository_config,
        launcher_resources=launcher_resources,
        installation_context=installation_context,
    )


def load_current_pointer(
    *, runtime_root: Path, trusted_interpreter_roots: tuple[Path, ...] = ()
) -> RuntimePointer:
    """Load and validate the current release pointer beneath ``runtime_root``.

    ``python_bin`` may resolve (through a symlink) to a location under one of
    ``trusted_interpreter_roots`` (e.g. uv's managed-Python store) instead of
    ``runtime_root``; by default no such roots are trusted, so a symlinked
    ``python_bin`` must resolve under ``runtime_root`` itself.
    """
    path = _pointer_path(runtime_root)
    if not path.exists():
        raise RuntimePointerError(f"no current.json at {path}")
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimePointerError(f"cannot read current.json: {exc}") from exc
    return decode_current_pointer(
        payload,
        runtime_root=runtime_root,
        trusted_interpreter_roots=trusted_interpreter_roots,
    )


def activate_release(
    *,
    runtime_root: Path,
    release_dir: Path,
    python_bin: Path,
    repository_config: Path | None = None,
    launcher_resources: Path | None = None,
    installation_context: Path | None = None,
    trusted_interpreter_roots: tuple[Path, ...] = (),
) -> RuntimePointer:
    """Atomically point current.json at ``release_dir``'s ``python_bin``.

    Validates that release_dir and python_bin are absolute paths beneath
    runtime_root and that python_bin exists before writing anything, then
    durably replaces current.json through the shared confined atomic-write
    primitive (fsync of file and parent directory, symlink rejection) so a
    failed or interrupted activation never leaves current.json missing or
    partially written; any prior pointer is left untouched on failure.

    ``python_bin`` itself must physically live under ``runtime_root`` (its
    parent directory chain is checked without dereferencing symlinks), but if
    it is a symlink, its resolved target must additionally be under
    ``runtime_root`` or one of ``trusted_interpreter_roots`` — never
    unconditionally accepted, since that would let anyone able to write under
    runtime_root point python_bin at an arbitrary binary.
    """
    _require_contained(release_dir, root=runtime_root, label="release_dir")
    _require_contained_or_trusted(
        python_bin, root=runtime_root, trusted_roots=trusted_interpreter_roots, label="python_bin"
    )
    if not python_bin.exists():
        raise RuntimePointerError(f"candidate python_bin does not exist: {python_bin}")
    runtime_root.mkdir(parents=True, exist_ok=True)
    if repository_config is not None:
        try:
            validated_config = load_repository_configuration(repository_config)
        except RepositoryConfigurationError as exc:
            raise RuntimePointerError(f"invalid repository_config: {exc}") from exc
        repository_config = validated_config.config_path
    schema3_requested = launcher_resources is not None or installation_context is not None
    if schema3_requested:
        if (
            repository_config is None
            or launcher_resources is None
            or installation_context is None
        ):
            raise RuntimePointerError(
                "schema-3 activation requires repository_config, launcher_resources, "
                "and installation_context"
            )
        launcher_resources, installation_context = _validate_schema3_fields(
            runtime_root=runtime_root,
            runtime_source=release_dir,
            release_id=release_dir.name,
            launcher_resources=launcher_resources,
            installation_context=installation_context,
        )
    pointer = RuntimePointer(
        release_id=release_dir.name,
        runtime_source=release_dir,
        python_bin=python_bin,
        repository_config=repository_config,
        launcher_resources=launcher_resources,
        installation_context=installation_context,
    )
    payload = {
        "schema_version": 3 if schema3_requested else (2 if repository_config is not None else 1),
        "release_id": pointer.release_id,
        "runtime_source": str(pointer.runtime_source),
        "python_bin": str(pointer.python_bin),
    }
    if repository_config is not None:
        payload["repository_config"] = str(repository_config)
    if schema3_requested:
        payload["launcher_resources"] = str(launcher_resources)
        payload["installation_context"] = str(installation_context)
    atomic_replace_bytes(
        _pointer_path(runtime_root),
        json.dumps(payload, indent=2).encode("utf-8"),
        allowed_root=runtime_root,
        mode=0o600,
    )
    return pointer


__all__ = [
    "RuntimePointer",
    "RuntimePointerError",
    "InstalledContextRecord",
    "activate_release",
    "decode_current_pointer",
    "load_installed_context_record",
    "load_deployed_resolver_trusted_roots",
    "load_current_pointer",
]
