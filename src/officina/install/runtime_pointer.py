"""Atomic current.json pointer for the managed Famulus runtime."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from officina.common.atomic_files import atomic_replace_bytes
from officina.common.repository_configuration import (
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
    resolver_bundle_id: str | None = None


_RESOLVER_BUNDLE_ID_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_resolver_bundle_id(value: object) -> str:
    """Return one canonical SHA-256 bundle identifier or fail closed."""
    if not isinstance(value, str) or _RESOLVER_BUNDLE_ID_RE.fullmatch(value) is None:
        raise RuntimePointerError(
            "resolver_bundle_id must be a 64-character lowercase SHA-256 digest"
        )
    return value


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
    if not isinstance(release_id, str) or not release_id:
        raise RuntimePointerError("current.json release_id must be a non-empty string")
    _require_contained_or_trusted(
        python_bin, root=runtime_root, trusted_roots=trusted_interpreter_roots, label="python_bin"
    )
    _require_contained(runtime_source, root=runtime_root, label="runtime_source")
    if runtime_source.name != release_id:
        raise RuntimePointerError(
            "current.json release_id must match runtime_source directory name"
        )
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
    resolver_bundle_id = None
    if schema_version == 3:
        try:
            resolver_bundle_id = _require_resolver_bundle_id(
                payload["resolver_bundle_id"]
            )
        except KeyError as exc:
            raise RuntimePointerError(
                f"current.json missing required key: {exc}"
            ) from exc
    return RuntimePointer(
        release_id=release_id,
        runtime_source=runtime_source,
        python_bin=python_bin,
        repository_config=repository_config,
        resolver_bundle_id=resolver_bundle_id,
    )


def activate_release(
    *,
    runtime_root: Path,
    release_dir: Path,
    python_bin: Path,
    repository_config: Path | None = None,
    trusted_interpreter_roots: tuple[Path, ...] = (),
    resolver_bundle_id: str | None = None,
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
    if resolver_bundle_id is None:
        raise RuntimePointerError(
            "resolver_bundle_id is required for every new runtime pointer"
        )
    resolver_bundle_id = _require_resolver_bundle_id(resolver_bundle_id)
    if repository_config is None:
        raise RuntimePointerError(
            "schema v3 activation requires a validated repository_config"
        )
    pointer = RuntimePointer(
        release_id=release_dir.name,
        runtime_source=release_dir,
        python_bin=python_bin,
        repository_config=repository_config,
        resolver_bundle_id=resolver_bundle_id,
    )
    payload = {
        "schema_version": 3,
        "release_id": pointer.release_id,
        "runtime_source": str(pointer.runtime_source),
        "python_bin": str(pointer.python_bin),
    }
    if repository_config is not None:
        payload["repository_config"] = str(repository_config)
    payload["resolver_bundle_id"] = resolver_bundle_id
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
    "activate_release",
    "load_current_pointer",
]
