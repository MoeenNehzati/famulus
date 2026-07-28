#!/usr/bin/env python3
"""Stable launcher entry point: reads current.json and execs into the
active managed-runtime release's interpreter. Generated launcher shims
invoke this file by a fixed path, never by embedding a release-specific
interpreter or repo path.

This module's source is deployed immutably at
``<runtime_root>/bootstrap/resolvers/v1/launch.py`` (a stable location
under the machine-local Famulus data root, not tied to any particular repo
checkout or release). Generated public shims (``dispatcher``,
``invoke-skill``, ...) only ever need to know that one fixed path -- never
the currently active release's interpreter or source checkout, both of
which change every time a new release is activated via
``officina.install.runtime_pointer.activate_release``. A future pointer
protocol installs a new versioned resolver alongside this one and only
retires it once nothing references it; this file is never edited in place
once deployed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from officina.install.managed_runtime import ManagedRuntimeError, uv_python_install_dir
from officina.install.runtime_pointer import RuntimePointerError, load_current_pointer


def _trusted_interpreter_roots(runtime_root: Path) -> tuple[Path, ...]:
    """Return uv's managed-Python install dir as a trusted symlink target.

    A real ``uv venv``-created ``python_bin`` is a symlink into uv's own
    interpreter store, which lives outside ``runtime_root`` (see
    ``managed_runtime.build_candidate_release``, which derives this exact
    root via ``uv_python_install_dir`` before activating a release).
    ``load_current_pointer`` must trust that same root, or it would reject
    the very pointer this resolver exists to launch. ``runtime_root``'s
    parent is the shared Famulus data root that also owns ``tools/uv``
    (see ``officina.common.famulus_paths.resolve_famulus_paths``), so the
    uv binary location is derived structurally rather than by re-resolving
    platform/home at launch time.
    """
    uv_bin = runtime_root.parent / "tools" / "uv"
    try:
        return (uv_python_install_dir(uv_bin),)
    except (ManagedRuntimeError, OSError):
        return ()


def main(argv: list[str]) -> int:
    """Resolve the active release from ``current.json`` and exec into it.

    ``argv[0]`` is this file's own invocation path, expected to be
    ``<runtime_root>/bootstrap/resolvers/v1/launch.py``. ``argv[1:]`` is
    forwarded unchanged as the new process's argv -- generated shims pass
    ``-m <module> <user-args>`` so the active release's interpreter runs
    the intended first-party entry point exactly as
    ``<python> -m <module> <user-args>`` would.
    """
    runtime_root = Path(argv[0]).resolve().parents[3]
    try:
        pointer = load_current_pointer(
            runtime_root=runtime_root,
            trusted_interpreter_roots=_trusted_interpreter_roots(runtime_root),
        )
    except RuntimePointerError as exc:
        print(f"famulus launcher: {exc}", file=sys.stderr)
        return 1

    python_bin = str(pointer.python_bin)
    src_dir = str(pointer.runtime_source / "src")
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{src_dir}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else src_dir
    )

    os.execve(python_bin, [python_bin, *argv[1:]], env)
    return 1  # pragma: no cover - os.execve never returns on success


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


__all__ = ["main"]
