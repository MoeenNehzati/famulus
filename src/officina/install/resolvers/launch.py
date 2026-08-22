#!/usr/bin/env python3
"""Read current.json and exec into the active managed-runtime interpreter.

This file MUST NOT import ``officina`` or any third-party package: it is
deployed standalone to ``<runtime_root>/bootstrap/resolvers/v1/launch.py``
and runs under whatever Python a generated shim's ``#!/usr/bin/env python3``
shebang finds -- the user's ambient system Python -- before control ever
transfers to the managed interpreter. An import of ``officina`` at this
point would be exactly the ambient-python invocation this program forbids
elsewhere, and would fail outright on any host where ``officina`` isn't
installed into the ambient Python's site-packages.

It therefore duplicates a minimal, read-only containment check from
``officina.install.runtime_pointer``. That module remains the sole source of
truth for validating and WRITING current.json (at release-activation time,
with the full adversarially-verified check, including symlink-chain and
parent-directory-escape handling); this copy only needs to read it safely
enough to refuse an untrusted ``python_bin`` before exec-ing into it. See
``tests/test_officina_launcher_entry.py`` for the cross-check test that
fails loudly if this copy's behavior ever diverges from the real
implementation on a shared table of adversarial test vectors.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


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
    "codex_home",
    "claude_home",
}
_RUNTIME_ROOT_MODULES = frozenset(
    {
        "officina.launchers.agent",
        "officina.recurring.control",
        "officina.recurring.executor",
        "officina.recurring.healthcheck",
    }
)
_ACTIVE_GENERATION_KEYS = {"schema_version", "generation"}


class ResolverError(Exception):
    """Raised when current.json cannot be read or validated safely."""


def _active_generation_source() -> Path | None:
    """Return the complete immutable generation selected beside this file."""
    fixed_dir = Path(__file__).resolve().parent
    active_file = fixed_dir / "active.json"
    if not active_file.exists():
        return None
    try:
        payload = json.loads(active_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResolverError(f"could not read active resolver generation: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _ACTIVE_GENERATION_KEYS:
        raise ResolverError("active resolver generation has invalid fields")
    if payload.get("schema_version") != 1:
        raise ResolverError("active resolver generation has unsupported schema")
    generation = payload.get("generation")
    if (
        not isinstance(generation, str)
        or len(generation) != 64
        or any(character not in "0123456789abcdef" for character in generation)
    ):
        raise ResolverError("active resolver generation has invalid identity")
    generations_root = fixed_dir.parent / "generations"
    generation_dir = generations_root / generation
    source = generation_dir / "launch.py"
    trust_file = generation_dir / "trusted-roots.json"
    try:
        if generation_dir.resolve(strict=True).parent != generations_root.resolve(strict=True):
            raise ResolverError("active resolver generation escapes its generation root")
    except OSError as exc:
        raise ResolverError(f"active resolver generation is incomplete: {exc}") from exc
    if not source.is_file() or not trust_file.is_file():
        raise ResolverError("active resolver generation is incomplete")
    return source


def _run_active_generation(argv: list[str]) -> int | None:
    """Execute the selected generation while preserving the fixed argv path."""
    source = _active_generation_source()
    if source is None:
        return None
    namespace: dict[str, object] = {
        "__file__": str(source),
        "__name__": "_famulus_active_resolver",
    }
    try:
        code = compile(source.read_bytes(), str(source), "exec")
        exec(code, namespace)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ResolverError(f"could not load active resolver generation: {exc}") from exc
    generation_main = namespace.get("main")
    if not callable(generation_main):
        raise ResolverError("active resolver generation has no callable main")
    return generation_main(argv)


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _require_contained_or_trusted(
    path: Path, *, root: Path, trusted_roots: tuple[Path, ...], label: str
) -> Path:
    """Validate ``path`` against ``root``/``trusted_roots`` and return the
    validated *entry* path (not its resolved target), or raise if it escapes
    ``root`` and every entry in ``trusted_roots``.

    ``path`` itself (its parent directory chain) must physically live under
    ``root``, without dereferencing the leaf. If the leaf is a symlink, its
    fully resolved target must additionally land under ``root`` or one of
    ``trusted_roots`` -- never unconditionally accepted, since that would let
    anyone able to write under ``root`` point ``path`` at an arbitrary
    binary. This mirrors
    ``officina.install.runtime_pointer._require_contained_or_trusted``; keep
    both in sync (the cross-check test enforces this).
    """
    if not path.is_absolute():
        raise ResolverError(f"{label} must be an absolute path: {path}")
    resolved_root = root.resolve()
    try:
        path.parent.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise ResolverError(f"{label} must live under runtime_root: {path}") from exc
    resolved_leaf = path.resolve()
    allowed_roots = (resolved_root, *(r.resolve() for r in trusted_roots))
    if not any(_is_relative_to(resolved_leaf, allowed) for allowed in allowed_roots):
        raise ResolverError(
            f"{label} resolves outside runtime_root and all trusted roots: {path} -> {resolved_leaf}"
        )
    # Preserve the validated venv entry path for exec. python_bin is
    # typically a venv's bin/python symlink into uv's shared interpreter
    # store; execing the *resolved* base-interpreter target directly starts
    # a bare interpreter that never finds venv/pyvenv.cfg (pyvenv.cfg
    # discovery walks up from the invoked path's own directory, which for
    # the resolved target is the shared store, not the venv), so it silently
    # loses the venv's site-packages -- including officina itself. Exec-ing
    # the unresolved entry path instead keeps the venv association intact;
    # trust validation above still fully resolves the symlink chain first,
    # so this is not a security regression, only a change in what gets exec'd.
    return path


def _require_repository_config(path: Path) -> Path:
    """Validate the activation-vetted config path without importing Officina."""

    if not path.is_absolute():
        raise ResolverError(f"repository_config must be an absolute path: {path}")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ResolverError(f"repository_config contains a symlink: {current}")
    if not path.is_file():
        raise ResolverError(f"repository_config is not a regular file: {path}")
    return path


def _require_absolute_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ResolverError(f"{label} must be an absolute path: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ResolverError(f"{label} must be an existing directory: {path}") from exc
    if not resolved.is_dir():
        raise ResolverError(f"{label} must be an existing directory: {path}")
    return resolved


def _validate_schema3(
    payload: dict[str, object], *, runtime_root: Path, runtime_source: Path
) -> None:
    if set(payload) != _SCHEMA3_KEYS:
        raise ResolverError(
            "schema-3 current.json must contain exactly the seven pointer fields"
        )
    release_id = payload.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise ResolverError("current.json release_id must be a non-empty string")
    resolved_source = runtime_source.resolve()
    if (
        resolved_source.parent != (runtime_root / "releases").resolve()
        or resolved_source.name != release_id
    ):
        raise ResolverError("schema-3 release identity does not match runtime_root")
    try:
        context_path = Path(payload["installation_context"])
        launcher_resources = Path(payload["launcher_resources"])
    except TypeError as exc:
        raise ResolverError(f"invalid schema-3 pointer path: {exc}") from exc
    validated_context = _require_contained_or_trusted(
        context_path,
        root=resolved_source,
        trusted_roots=(),
        label="installation_context",
    )
    if validated_context != resolved_source / "installation-context.json":
        raise ResolverError("installation_context has the wrong candidate address")
    try:
        context = json.loads(validated_context.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ResolverError(f"cannot read installation_context: {exc}") from exc
    if not isinstance(context, dict) or set(context) != _CONTEXT_KEYS:
        raise ResolverError("installation_context has an invalid exact schema")
    if context.get("schema_version") != 1 or context.get("release_id") != release_id:
        raise ResolverError("installation_context release identity does not match pointer")
    mode = context.get("mode")
    if mode == "standard":
        if context.get("installation_id") != "standard" or context.get("development_root") is not None:
            raise ResolverError("invalid standard installation_context")
        expected_resources = resolved_source / "launcher-resources"
    elif mode == "development":
        source_root = context.get("source_root")
        development_root = context.get("development_root")
        if not isinstance(source_root, str) or source_root != development_root:
            raise ResolverError("invalid development installation_context")
        expected_resources = Path(source_root)
    else:
        raise ResolverError("invalid installation_context mode")
    if _require_absolute_directory(
        launcher_resources, label="launcher_resources"
    ) != expected_resources.resolve(strict=False):
        raise ResolverError("launcher_resources does not match installation_context")


def _load_current_pointer(
    runtime_root: Path, *, trusted_roots: tuple[Path, ...]
) -> Path | tuple[Path, Path]:
    """Read current.json beneath ``runtime_root`` and return its validated
    ``python_bin`` entry path (unresolved -- see
    ``_require_contained_or_trusted``)."""
    pointer_path = runtime_root / "current.json"
    if not pointer_path.exists():
        raise ResolverError(f"no current.json at {pointer_path}")
    try:
        payload = json.loads(pointer_path.read_text())
    except (OSError, UnicodeError, ValueError) as exc:
        raise ResolverError(f"cannot read current.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResolverError("current.json must contain a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2, 3}:
        raise ResolverError(
            f"unsupported current.json schema_version: {payload.get('schema_version')!r}"
        )
    try:
        python_bin = Path(payload["python_bin"])
        runtime_source = Path(payload["runtime_source"])
    except (KeyError, TypeError) as exc:
        raise ResolverError(f"current.json missing required key: {exc}") from exc
    # runtime_source is never allowed to resolve outside runtime_root -- only
    # python_bin may land in a trusted interpreter store.
    _require_contained_or_trusted(runtime_source, root=runtime_root, trusted_roots=(), label="runtime_source")
    _require_contained_or_trusted(
        python_bin, root=runtime_root, trusted_roots=trusted_roots, label="python_bin"
    )
    repository_config = None
    if schema_version in {2, 3}:
        try:
            repository_config = _require_repository_config(
                Path(payload["repository_config"])
            )
        except (KeyError, TypeError) as exc:
            raise ResolverError(f"current.json missing required key: {exc}") from exc
    if schema_version == 3:
        _validate_schema3(
            payload, runtime_root=runtime_root, runtime_source=runtime_source
        )
    if repository_config is None:
        return python_bin
    return python_bin, repository_config


def _trusted_interpreter_roots() -> tuple[Path, ...]:
    """Return the trusted-interpreter-store allowlist for this resolver.

    Deployment writes this resolver alongside a sibling data file
    (``trusted-roots.json``, a flat JSON list of absolute path strings)
    populated at release-activation time from the same
    ``managed_runtime._uv_python_install_dir()`` derivation
    ``officina.install`` uses elsewhere. This resolver reads that file
    instead of re-deriving trust itself, since it must not shell out to
    ``uv`` or import anything beyond the standard library.
    """
    trust_file = Path(__file__).resolve().parent / "trusted-roots.json"
    if not trust_file.exists():
        return ()
    try:
        entries = json.loads(trust_file.read_text())
    except (OSError, ValueError):
        return ()
    if not isinstance(entries, list):
        return ()
    return tuple(Path(entry) for entry in entries if isinstance(entry, str))


def main(argv: list[str]) -> int:
    """Resolve the active release from ``current.json`` and exec into it.

    ``argv[0]`` is this file's own invocation path, expected to be
    ``<runtime_root>/bootstrap/resolvers/v1/launch.py``. ``argv[1:]`` is
    forwarded unchanged as the new process's argv.
    """
    try:
        delegated = _run_active_generation(argv)
        if delegated is not None:
            return delegated
        runtime_root = Path(argv[0]).resolve().parents[3]
        loaded_pointer = _load_current_pointer(
            runtime_root, trusted_roots=_trusted_interpreter_roots()
        )
        if isinstance(loaded_pointer, tuple):
            python_bin, repository_config = loaded_pointer
        else:
            python_bin = loaded_pointer
            repository_config = None
    except ResolverError as exc:
        print(f"famulus launcher: {exc}", file=sys.stderr)
        return 1

    forwarded = list(argv[1:])
    if repository_config is not None and forwarded[:2] == [
        "-m",
        "officina.dispatcher.cli",
    ]:
        if "--repository-config" in forwarded:
            print(
                "famulus launcher: repository_config cannot be overridden",
                file=sys.stderr,
            )
            return 1
        forwarded[2:2] = ["--repository-config", str(repository_config)]
    if len(forwarded) >= 2 and forwarded[0] == "-m" and forwarded[1] in _RUNTIME_ROOT_MODULES:
        if any(
            argument == "--runtime-root" or argument.startswith("--runtime-root=")
            for argument in forwarded[2:]
        ):
            print(
                "famulus launcher: runtime_root cannot be overridden",
                file=sys.stderr,
            )
            return 1
        forwarded[2:2] = ["--runtime-root", str(runtime_root.resolve())]
    os.execv(str(python_bin), [str(python_bin), *forwarded])
    return 1  # pragma: no cover - os.execv never returns on success


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


__all__ = ["main", "ResolverError"]
