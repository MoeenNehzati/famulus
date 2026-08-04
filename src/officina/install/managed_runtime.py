"""Build a versioned managed-runtime candidate release from the real v1
runtime_dependencies.json manifest, installing all declared Python
dependencies in one atomic batch instead of ambient, best-effort per-package
pip calls.

`build_candidate_release` is called from `_phase_entry.py`, ahead of
`scaffold.run`, so a real managed-runtime release (and the dependency-free
launcher resolver deployed alongside it -- see `_deploy_resolver`) exists
before any launcher shim that execs into it is generated. `_install_scaffold.py`
separately consumes `declared_python_packages` for its own ambient,
ahead-of-managed-runtime ecosystem ambient package installs.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

from officina.common import atomic_files
from officina.install.dispatch_snapshot_builder import build_dispatch_snapshot
from officina.install.runtime_pointer import RuntimePointer, activate_release

_VERSION_OPERATOR_RE = re.compile(r"^(==|>=|<=|!=|~=|>|<)")

_DEFAULT_DEPENDENCY_INSTALL_TIMEOUT_SECONDS = 600


def _dependency_install_timeout_seconds() -> float:
    raw = os.environ.get("FAMULUS_MANAGED_RUNTIME_INSTALL_TIMEOUT_SECONDS", "")
    if not raw:
        return _DEFAULT_DEPENDENCY_INSTALL_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_DEPENDENCY_INSTALL_TIMEOUT_SECONDS


# The real, dependency-free resolver source deployed standalone to
# <runtime_root>/bootstrap/resolvers/v1/launch.py -- see that file's
# docstring for why it must never be imported here (it must stay
# stdlib-only and runnable under the user's ambient Python).
_RESOLVER_SOURCE = Path(__file__).resolve().parent / "resolvers" / "launch.py"


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


def _run_uv(argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run a ``uv`` subprocess, converting an unusable ``uv_bin`` (not yet
    bootstrapped, not executable, etc.) or a hung/too-slow call into a
    ``ManagedRuntimeError`` instead of letting ``OSError`` /
    ``subprocess.TimeoutExpired`` propagate as an unhandled crash or an
    indefinite hang.

    ``build_candidate_release`` is a real production call site as of Task 7
    (`_phase_entry.py` calls it ahead of every non-dry-run install); on a
    freshly provisioned machine the machine-local managed uv binary at
    ``officina.common.famulus_paths.resolve_famulus_paths(...).uv_bin`` may
    not exist yet (its bootstrap is separately scoped), and that must surface
    as the same clean, typed failure as any other managed-runtime error --
    not a raw traceback out of ``subprocess.run``, and not an unbounded wait
    (e.g. a large or many-dependency manifest download stalling on a slow or
    unavailable network).
    """
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout,
        )
    except OSError as exc:
        raise ManagedRuntimeError(f"could not run uv ({argv[0]}): {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ManagedRuntimeError(f"uv command timed out after {timeout}s: {' '.join(argv)}") from exc


def _create_release_venv(*, uv_bin: Path, venv_dir: Path, python_version: str) -> None:
    """Provision the release's managed interpreter with ``uv venv``.

    ``uv pip install --python <path>`` requires an existing virtual
    environment (or system interpreter) at that path; it does not create
    one. This must run before any dependency install against ``venv_dir``.
    """
    result = _run_uv([str(uv_bin), "venv", "--python", python_version, str(venv_dir)])
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
    result = _run_uv(
        [str(uv_bin), "pip", "install", "--python", str(python_bin), *packages],
        timeout=_dependency_install_timeout_seconds(),
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"dependency install failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def _venv_python_bin(venv_dir: Path, *, platform: str) -> Path:
    """Return the interpreter path a real ``uv venv --python ... venv_dir``
    creates for ``platform``.

    ``uv venv``'s interpreter layout is not uniform across every host this
    installer targets. This must branch on the ``platform`` value passed
    through from ``build_candidate_release`` -- not the platform this
    installer process happens to be running on -- because a single
    hardcoded layout here silently produces a python_bin that never exists
    on one of the targets, which later surfaces as an unrelated (and,
    before this fix, uncaught) ``RuntimePointerError`` out of
    ``activate_release`` instead of a clear failure at the actual point of
    the mistake. See ``_install_scaffold.py``'s ``_platform_name()`` for
    the canonical set of values this parameter takes.
    """
    if platform == "windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


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
    result = _run_uv([str(uv_bin), "python", "dir"])
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


def _deploy_resolver(*, runtime_root: Path, trusted_interpreter_roots: tuple[Path, ...]) -> None:
    """Deploy the dependency-free resolver and its trust sidecar to the
    fixed path every generated launcher shim execs into
    (``<runtime_root>/bootstrap/resolvers/v1/launch.py``), alongside a
    ``trusted-roots.json`` sidecar (a flat JSON list of absolute path
    strings) that resolver's ``_trusted_interpreter_roots()`` reads.

    Idempotent: overwrites any prior deployment with the current resolver
    source and trust list, so it is safe to call on every successful
    activation. Called from ``build_candidate_release`` *before*
    ``activate_release`` writes current.json (not after): a deployment
    failure here must not leave a release activated with a missing or
    broken resolver. Any ``OSError`` (missing source, permissions, disk
    full, a concurrent-install race on the resolver directory) is converted
    to ``ManagedRuntimeError`` so callers get the same clean, typed failure
    as every other managed-runtime error, not a raw traceback.
    """
    try:
        resolver_dir = runtime_root / "bootstrap" / "resolvers" / "v1"
        resolver_dir.mkdir(parents=True, exist_ok=True)
        resolver_path = resolver_dir / "launch.py"
        resolver_bytes = _RESOLVER_SOURCE.read_bytes()
        # atomic_replace_bytes (not shutil.copy2): resolver_path is the
        # fixed path every generated launcher shim and every scheduled
        # recurring-tasks job execs into, and build_candidate_release runs
        # again on every install-then-update flow against the same
        # runtime_root -- a plain copy2 here could race a job that is
        # mid-exec into this exact file, handing it a torn read. mode=0o755
        # makes the file executable as part of the same atomic write, so no
        # separate chmod is needed afterward.
        atomic_files.atomic_replace_bytes(
            resolver_path, resolver_bytes, allowed_root=resolver_dir, mode=0o755
        )
        trust_file = resolver_dir / "trusted-roots.json"
        trust_file.write_text(
            json.dumps([str(root) for root in trusted_interpreter_roots]),
            encoding="utf-8",
        )
    except OSError as exc:
        # atomic_files.AtomicWriteError is itself an OSError subclass, so
        # this also covers a confined-write rejection (symlink resolver
        # directory, non-regular destination, etc.), not just plain I/O
        # failures.
        raise ManagedRuntimeError(f"could not deploy the launcher resolver: {exc}") from exc


def build_candidate_release(
    *,
    runtime_root: Path,
    manifest_path: Path,
    platform: str,
    uv_bin: Path,
    python_version: str,
    repo_root: Path | None = None,
) -> RuntimePointer:
    """Create a new release directory, provision its managed interpreter,
    install its declared Python dependencies in a single atomic batch, and
    activate it.

    ``python_version`` should be the pinned ``managed_python.preferred``
    value from install-info.toml (see officina.install.install_info); it is
    passed straight through to ``uv venv --python``.

    On any failure (bad manifest, failed venv creation, failed batch
    install, or failed resolver deployment), no release is activated:
    current.json is left untouched and no new pointer is written. The
    dependency-free launcher resolver and its trust sidecar are deployed
    *before* activation for exactly this reason: a deployment failure must
    prevent activation, not follow it.
    """
    packages = declared_python_packages(manifest_path, platform=platform)
    release_id = _new_release_id()
    release_dir = runtime_root / "releases" / release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = release_dir / "venv"
    python_bin = _venv_python_bin(venv_dir, platform=platform)

    _create_release_venv(uv_bin=uv_bin, venv_dir=venv_dir, python_version=python_version)
    _run_dependency_install(uv_bin=uv_bin, python_bin=python_bin, packages=packages)

    if repo_root is not None:
        try:
            build_dispatch_snapshot(
                Path(repo_root).resolve(),
                snapshot_root=runtime_root.parent / "dispatcher" / "snapshots",
            )
        except Exception as exc:
            raise ManagedRuntimeError(
                f"dispatcher snapshot build failed: {exc}"
            ) from exc

    trusted_interpreter_roots = (_uv_python_install_dir(uv_bin),)
    _deploy_resolver(runtime_root=runtime_root, trusted_interpreter_roots=trusted_interpreter_roots)
    return activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
        trusted_interpreter_roots=trusted_interpreter_roots,
    )


# Public alias: whatever deploys officina.install.resolvers.launch (the
# dependency-free resolver -- see its docstring for why it cannot import this
# module itself) needs this same uv-managed Python install dir to populate
# the resolver's trusted-roots.json sidecar at release-activation time, so
# the resolver accepts the very symlinked python_bin that build_candidate_release
# just activated, without re-implementing uv's "where do you keep
# interpreters" lookup a second time.
uv_python_install_dir = _uv_python_install_dir

__all__ = [
    "ManagedRuntimeError",
    "build_candidate_release",
    "declared_python_packages",
    "uv_python_install_dir",
]
