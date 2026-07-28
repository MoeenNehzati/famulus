"""Build a versioned managed-runtime candidate release from the real v1
runtime_dependencies.json manifest, installing all declared Python
dependencies in one atomic batch instead of ambient, best-effort per-package
pip calls.

Not yet wired to a production caller: `build_candidate_release` has no
production call site yet. `_install_scaffold.py` only consumes
`declared_python_packages` (the atomic-batch dependency-install fix); wiring
`build_candidate_release` into `_phase_entry.py` ahead of `scaffold.run` is a
separately planned, later task with its own test plan and blueprint
updates — not folded in here to avoid preempting that task's scope.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import time
from pathlib import Path

from officina.install.runtime_pointer import RuntimePointer, activate_release

_VERSION_OPERATOR_RE = re.compile(r"^(==|>=|<=|!=|~=|>|<)")


class ManagedRuntimeError(Exception):
    """Raised when the runtime dependency manifest is unreadable/unsupported
    or the batch dependency install fails."""


def _package_spec(name: str, version: str | None) -> str:
    if not version or version == "any":
        return name
    if _VERSION_OPERATOR_RE.match(version):
        return f"{name}{version}"
    return f"{name}=={version}"


def declared_python_packages(manifest_path: Path, *, platform: str) -> tuple[str, ...]:
    """Return the deduplicated, sorted pip install specs for every
    python-package dependency declared for ``platform`` in the real v1
    runtime_dependencies.json manifest.

    The first version constraint seen for a given package name (case-folded)
    wins; later, less-specific ("any") duplicates for the same package are
    ignored.
    """
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ManagedRuntimeError(
            f"unsupported runtime_dependencies.json version: {payload.get('version')!r}"
        )
    skills = payload.get("skills", {})
    if not isinstance(skills, dict):
        raise ManagedRuntimeError("runtime_dependencies.json 'skills' must be an object")

    seen: dict[str, str] = {}
    for skill in skills.values():
        interfaces = skill.get("interfaces", {}) if isinstance(skill, dict) else {}
        if not isinstance(interfaces, dict):
            continue
        for interface in interfaces.values():
            dependencies = interface.get("dependencies", []) if isinstance(interface, dict) else []
            if not isinstance(dependencies, list):
                continue
            for dependency in dependencies:
                if not isinstance(dependency, dict) or dependency.get("kind") != "python-package":
                    continue
                platforms = dependency.get("platforms", {})
                if not isinstance(platforms, dict) or not platforms.get(platform):
                    continue
                name = dependency.get("name")
                if not isinstance(name, str) or not name:
                    continue
                key = name.casefold()
                seen.setdefault(key, _package_spec(name, dependency.get("version")))

    return tuple(sorted(seen.values(), key=str.casefold))


def _create_release_venv(*, uv_bin: Path, venv_dir: Path, python_version: str) -> None:
    """Provision the release's managed interpreter with ``uv venv``.

    ``uv pip install --python <path>`` requires an existing virtual
    environment (or system interpreter) at that path; it does not create
    one. This must run before any dependency install against ``venv_dir``.
    """
    result = subprocess.run(
        [str(uv_bin), "venv", "--python", python_version, str(venv_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"venv creation failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def _run_dependency_install(*, uv_bin: Path, python_bin: Path, packages: tuple[str, ...]) -> None:
    """Install every declared package in one atomic uv batch call.

    Fails fast: any non-zero exit raises ManagedRuntimeError, unlike the
    previous per-package WARN-and-continue loop.
    """
    if not packages:
        return
    result = subprocess.run(
        [str(uv_bin), "pip", "install", "--python", str(python_bin), *packages],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"dependency install failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def _new_release_id() -> str:
    timestamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    return f"{timestamp}-{secrets.token_hex(3)}"


def _uv_python_install_dir(uv_bin: Path) -> Path:
    """Ask uv where it stores managed Python interpreters.

    A real ``uv venv``-created ``bin/python`` is a symlink into this
    directory, which lives outside runtime_root. activate_release must trust
    this specific, uv-reported location for a symlinked python_bin to
    resolve into — not an arbitrary target — so the trusted root is derived
    from uv itself rather than assumed.
    """
    result = subprocess.run(
        [str(uv_bin), "python", "dir"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"could not determine uv's Python install directory (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    stdout = result.stdout.strip()
    if not stdout:
        # An empty path would resolve to the process's cwd, silently
        # widening the trusted-interpreter allowlist to wherever this
        # process happens to be running from — refuse instead.
        raise ManagedRuntimeError("uv reported an empty Python install directory")
    return Path(stdout)


def build_candidate_release(
    *,
    runtime_root: Path,
    manifest_path: Path,
    platform: str,
    uv_bin: Path,
    python_version: str,
) -> RuntimePointer:
    """Create a new release directory, provision its managed interpreter,
    install its declared Python dependencies in a single atomic batch, and
    activate it.

    ``python_version`` should be the pinned ``managed_python.preferred``
    value from install-info.toml (see officina.install.install_info); it is
    passed straight through to ``uv venv --python``.

    On any failure (bad manifest, failed venv creation, failed batch
    install), no release is activated: current.json is left untouched and no
    new pointer is written.
    """
    packages = declared_python_packages(manifest_path, platform=platform)
    release_id = _new_release_id()
    release_dir = runtime_root / "releases" / release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = release_dir / "venv"
    python_bin = venv_dir / "bin" / "python"

    _create_release_venv(uv_bin=uv_bin, venv_dir=venv_dir, python_version=python_version)
    _run_dependency_install(uv_bin=uv_bin, python_bin=python_bin, packages=packages)

    trusted_interpreter_roots = (_uv_python_install_dir(uv_bin),)
    return activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
        trusted_interpreter_roots=trusted_interpreter_roots,
    )


# Public alias: officina.install.launcher_entry needs this same uv-managed
# Python install dir to derive the trusted_interpreter_roots that let it
# accept the very symlinked python_bin that build_candidate_release just
# activated, without re-implementing uv's "where do you keep interpreters"
# lookup a second time.
uv_python_install_dir = _uv_python_install_dir

__all__ = [
    "ManagedRuntimeError",
    "build_candidate_release",
    "declared_python_packages",
    "uv_python_install_dir",
]
