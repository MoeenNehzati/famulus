#!/usr/bin/env python3
"""Dependency-free launcher resolver: reads current.json and execs into the
active managed-runtime release's interpreter.

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
import hashlib
import os
import sys
from pathlib import Path


class ResolverError(Exception):
    """Raised when current.json cannot be read or validated safely."""


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


def _require_bundle_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ResolverError("resolver_bundle_id must be a lowercase SHA-256 digest")
    return value


def _require_entry_parent(path: Path, *, root: Path, label: str) -> None:
    """Validate an entry's parent without following its final symlink."""
    if not path.is_absolute():
        raise ResolverError(f"{label} must be an absolute path: {path}")
    try:
        path.parent.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ResolverError(f"{label} must live under runtime_root: {path}") from exc


def _load_current_pointer(
    runtime_root: Path,
    *,
    trusted_roots: tuple[Path, ...],
    defer_v3_interpreter_validation: bool = False,
) -> Path | tuple[Path, Path] | tuple[Path, Path, str]:
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
    if schema_version == 3 and defer_v3_interpreter_validation:
        _require_entry_parent(python_bin, root=runtime_root, label="python_bin")
    else:
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
        try:
            bundle_id = _require_bundle_id(payload["resolver_bundle_id"])
        except KeyError as exc:
            raise ResolverError(f"current.json missing required key: {exc}") from exc
        assert repository_config is not None
        return python_bin, repository_config, bundle_id
    if repository_config is None:
        return python_bin
    return python_bin, repository_config


def _trusted_interpreter_roots(resolver_path: Path | None = None) -> tuple[Path, ...]:
    """Return the trusted-interpreter-store allowlist for this resolver.

    Deployment writes this resolver alongside a sibling data file
    (``trusted-roots.json``, a flat JSON list of absolute path strings)
    populated at release-activation time from the same
    ``managed_runtime._uv_python_install_dir()`` derivation
    ``officina.install`` uses elsewhere. This resolver reads that file
    instead of re-deriving trust itself, since it must not shell out to
    ``uv`` or import anything beyond the standard library.
    """
    trust_file = (resolver_path or Path(__file__)).absolute().parent / "trusted-roots.json"
    if not trust_file.exists():
        return ()
    try:
        entries = json.loads(trust_file.read_text())
    except (OSError, ValueError):
        return ()
    if not isinstance(entries, list):
        return ()
    return tuple(Path(entry) for entry in entries if isinstance(entry, str))


def _validate_resolver_bundle(runtime_root: Path, bundle_id: str) -> Path:
    """Validate a content-addressed bundle using only standard-library code."""
    bundle_id = _require_bundle_id(bundle_id)
    resolvers_dir = runtime_root / "resolvers"
    bundles_dir = resolvers_dir / "bundles"
    for label, directory in (
        ("resolvers", resolvers_dir),
        ("bundles", bundles_dir),
    ):
        if directory.is_symlink() or not directory.is_dir():
            raise ResolverError(f"resolver bundle {label} ancestor is missing or unsafe")
    bundle_dir = bundles_dir / bundle_id
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise ResolverError(f"resolver bundle is missing or unsafe: {bundle_id}")
    files: dict[str, bytes] = {}
    try:
        for name in ("launch.py", "trusted-roots.json", "manifest.json"):
            path = bundle_dir / name
            if path.is_symlink() or not path.is_file():
                raise ResolverError(f"resolver bundle file is missing or unsafe: {name}")
            files[name] = path.read_bytes()
        if hashlib.sha256(files["manifest.json"]).hexdigest() != bundle_id:
            raise ResolverError("resolver bundle manifest digest does not match its id")
        manifest = json.loads(files["manifest.json"])
        expected = {
            "files": {
                "launch.py": hashlib.sha256(files["launch.py"]).hexdigest(),
                "trusted-roots.json": hashlib.sha256(files["trusted-roots.json"]).hexdigest(),
            },
            "schema_version": 1,
        }
        if manifest != expected:
            raise ResolverError("resolver bundle file digest validation failed")
        roots = json.loads(files["trusted-roots.json"])
        if not isinstance(roots, list) or any(
            not isinstance(root, str) or not Path(root).is_absolute() for root in roots
        ):
            raise ResolverError("resolver bundle trust data is invalid")
    except ResolverError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ResolverError(f"cannot validate resolver bundle: {exc}") from exc
    return bundle_dir


def main(argv: list[str]) -> int:
    """Resolve the active release from ``current.json`` and exec into it.

    ``argv[0]`` is this file's own invocation path, expected to be
    ``<runtime_root>/bootstrap/resolvers/v1/launch.py``. ``argv[1:]`` is
    forwarded unchanged as the new process's argv.
    """
    invoked_path = Path(argv[0]).absolute()
    runtime_root = invoked_path.parents[3]
    running_from_bundle = invoked_path.parent.parent.name == "bundles"
    try:
        loaded_pointer = _load_current_pointer(
            runtime_root,
            trusted_roots=_trusted_interpreter_roots(invoked_path),
            defer_v3_interpreter_validation=not running_from_bundle,
        )
        if isinstance(loaded_pointer, tuple) and len(loaded_pointer) == 3:
            python_bin, repository_config, bundle_id = loaded_pointer
            bundle_dir = _validate_resolver_bundle(runtime_root, bundle_id)
            bundle_launch = bundle_dir / "launch.py"
            if not running_from_bundle:
                os.execv(
                    sys.executable,
                    [sys.executable, str(bundle_launch), *argv[1:]],
                )
                return 1
            if invoked_path != bundle_launch:
                raise ResolverError(
                    "active resolver bundle does not match the invoked bundle"
                )
            # Re-load now that the selected bundle's own trust sidecar is known.
            loaded_pointer = _load_current_pointer(
                runtime_root,
                trusted_roots=_trusted_interpreter_roots(bundle_launch),
            )
            python_bin, repository_config, _bundle_id = loaded_pointer
            if _bundle_id != bundle_id:
                raise ResolverError(
                    "current resolver bundle changed during launch; retry from bootstrap"
                )
        elif isinstance(loaded_pointer, tuple):
            python_bin, repository_config = loaded_pointer
        else:
            if running_from_bundle:
                raise ResolverError("legacy pointer cannot select a resolver bundle")
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
    os.execv(str(python_bin), [str(python_bin), *forwarded])
    return 1  # pragma: no cover - os.execv never returns on success


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


__all__ = ["main", "ResolverError"]
