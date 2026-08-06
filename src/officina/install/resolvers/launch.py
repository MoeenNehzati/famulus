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


def _load_current_pointer(runtime_root: Path, *, trusted_roots: tuple[Path, ...]) -> Path:
    """Read current.json beneath ``runtime_root`` and return its validated
    ``python_bin`` entry path (unresolved -- see
    ``_require_contained_or_trusted``)."""
    pointer_path = runtime_root / "current.json"
    if not pointer_path.exists():
        raise ResolverError(f"no current.json at {pointer_path}")
    payload = json.loads(pointer_path.read_text())
    if payload.get("schema_version") != 1:
        raise ResolverError(
            f"unsupported current.json schema_version: {payload.get('schema_version')!r}"
        )
    try:
        python_bin = Path(payload["python_bin"])
        runtime_source = Path(payload["runtime_source"])
    except KeyError as exc:
        raise ResolverError(f"current.json missing required key: {exc}") from exc
    # runtime_source is never allowed to resolve outside runtime_root -- only
    # python_bin may land in a trusted interpreter store.
    _require_contained_or_trusted(runtime_source, root=runtime_root, trusted_roots=(), label="runtime_source")
    return _require_contained_or_trusted(
        python_bin, root=runtime_root, trusted_roots=trusted_roots, label="python_bin"
    )


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
    runtime_root = Path(argv[0]).resolve().parents[3]
    try:
        python_bin = _load_current_pointer(runtime_root, trusted_roots=_trusted_interpreter_roots())
    except ResolverError as exc:
        print(f"famulus launcher: {exc}", file=sys.stderr)
        return 1

    os.execv(str(python_bin), [str(python_bin), *argv[1:]])
    return 1  # pragma: no cover - os.execv never returns on success


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


__all__ = ["main", "ResolverError"]
