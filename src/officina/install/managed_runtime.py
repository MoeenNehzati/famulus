"""Build and atomically activate a versioned managed-runtime candidate.

The blueprint-derived dependency manifest is accepted only with its matching
generated, exact, hash-checked core lock. Candidate construction uses the
pinned managed Python patch, installs the lock with hash enforcement, and
installs the locally built Officina wheel without resolving dependencies.

`build_candidate_release` is called from `_phase_entry.py`, ahead of
`scaffold.run`, so a real managed-runtime release (and the dependency-free
launcher resolver deployed alongside it -- see `_deploy_resolver`) exists
before any launcher shim that execs into it is generated. `_install_scaffold.py`
separately consumes `declared_python_packages` for host capability reporting
and legacy ambient-package handling.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import officina.common.atomic_files as atomic_files
import officina.common.toml_io as toml_io
from officina.git.provenance import run_git
from officina.install import runtime_lock
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


def _selected_modules(
    manifest_path: Path, *, optional_module_ids: tuple[str, ...] = ()
) -> tuple[str, ...]:
    try:
        return runtime_lock.selected_runtime_module_ids(
            manifest_path, optional_module_ids=optional_module_ids
        )
    except runtime_lock.RuntimeLockError as exc:
        raise ManagedRuntimeError(str(exc)) from exc


def _iter_declared_dependencies(
    manifest_path: Path, *, platform: str, optional_module_ids: tuple[str, ...] = ()
):
    """Yield ``(name, version)`` for every python-package dependency declared
    for ``platform`` in a supported runtime_dependencies.json manifest,
    before dedup/spec-building. Shared by ``declared_python_packages`` and
    ``optional_python_packages`` so both read the manifest the same way.
    """
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("version") not in {1, 2}:
        raise ManagedRuntimeError(
            f"unsupported runtime_dependencies.json version: {payload.get('version')!r}"
        )
    skills = payload.get("skills", {})
    if not isinstance(skills, dict):
        raise ManagedRuntimeError("runtime_dependencies.json 'skills' must be an object")

    selected = set(_selected_modules(manifest_path, optional_module_ids=optional_module_ids))
    for module_id, skill in skills.items():
        if module_id not in selected:
            continue
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
                yield name, dependency.get("version")


def declared_python_packages(
    manifest_path: Path, *, platform: str, selected_module_ids: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Return the deduplicated, sorted pip install specs for every
    python-package dependency declared for ``platform`` in a supported
    runtime_dependencies.json manifest.

    The first version constraint seen for a given package name (case-folded)
    wins; later, less-specific ("any") duplicates for the same package are
    ignored.

    With no selected optional module IDs this is the core dependency set.
    Optional dependencies are added only through the owning module IDs.
    """
    seen: dict[str, str] = {}
    for name, version in _iter_declared_dependencies(
        manifest_path, platform=platform, optional_module_ids=selected_module_ids
    ):
        key = name.casefold()
        seen.setdefault(key, _package_spec(name, version))

    return tuple(sorted(seen.values(), key=str.casefold))


def optional_runtime_modules(manifest_path: Path, *, platform: str) -> tuple[dict[str, object], ...]:
    """Describe each optional module's package delta over the core selection."""
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagedRuntimeError(f"could not read runtime dependency manifest: {exc}") from exc
    skills = payload.get("skills")
    if not isinstance(skills, dict):
        raise ManagedRuntimeError("runtime_dependencies.json 'skills' must be an object")
    core = set(declared_python_packages(manifest_path, platform=platform))
    result: list[dict[str, object]] = []
    for module_id, module in sorted(skills.items()):
        if not isinstance(module, dict) or module.get("installation_tier") != "optional":
            continue
        packages = set(declared_python_packages(
            manifest_path, platform=platform, selected_module_ids=(module_id,)
        ))
        result.append({"id": module_id, "packages": tuple(sorted(packages - core, key=str.casefold))})
    return tuple(result)


@dataclass(frozen=True)
class PackageSizeEstimate:
    """Package-index artifact size or an explicit unavailable estimate."""

    package: str
    bytes: int | None


def _package_name_from_spec(spec: str) -> str:
    return re.split(r"(?:===|==|!=|~=|>=|<=|>|<|@)", spec.split("[", 1)[0], maxsplit=1)[0]


def load_cached_package_index_metadata(
    packages: tuple[str, ...], *, cache_dir: Path | None = None
) -> dict[str, object]:
    """Load per-package index records from the installer metadata cache.

    The cache contains one JSON response per normalized package name.  This
    function deliberately does not perform network I/O: the optional prompt
    remains usable offline, and callers get an explicit unavailable estimate
    for records that are absent or malformed.
    """
    root = cache_dir or Path(
        os.environ.get(
            "FAMULUS_PACKAGE_INDEX_CACHE",
            Path.home() / ".cache" / "famulus" / "package-index",
        )
    )
    metadata: dict[str, object] = {}
    for spec in packages:
        name = _package_name_from_spec(spec)
        try:
            payload = json.loads((root / f"{name.casefold()}.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            metadata[name.casefold()] = payload
    return metadata


def package_size_estimates(
    packages: tuple[str, ...], *, package_index_metadata: dict[str, object] | None = None
) -> tuple[PackageSizeEstimate, ...]:
    """Read wheel/sdist sizes from the package-index metadata cache boundary.

    The caller supplies already-cached package-index JSON.  A missing record,
    malformed metadata, or an index response without a usable wheel/sdist
    size is represented by ``bytes=None`` rather than an invented estimate.
    """
    metadata = package_index_metadata or {}
    estimates: list[PackageSizeEstimate] = []
    for package in packages:
        name = _package_name_from_spec(package)
        record = metadata.get(name.casefold())
        urls = record.get("urls") if isinstance(record, dict) else None
        sizes = [url.get("size") for url in urls or [] if isinstance(url, dict)
                 and url.get("packagetype") in {"bdist_wheel", "sdist"}
                 and isinstance(url.get("size"), int) and url["size"] >= 0]
        estimates.append(PackageSizeEstimate(package=name, bytes=max(sizes) if sizes else None))
    return tuple(estimates)


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
    result = _run_uv(
        [
            str(uv_bin),
            "venv",
            "--managed-python",
            "--python",
            python_version,
            str(venv_dir),
        ]
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"venv creation failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def _run_dependency_install(
    *, uv_bin: Path, python_bin: Path, packages: tuple[str, ...], no_deps: bool = False
) -> None:
    """Install the supplied local artifact(s) in one atomic uv batch call.

    Fails fast: any non-zero exit raises ManagedRuntimeError, unlike the
    previous per-package WARN-and-continue loop.
    """
    if not packages:
        return
    argv = [str(uv_bin), "pip", "install", "--python", str(python_bin)]
    if no_deps:
        argv.append("--no-deps")
    result = _run_uv(
        [*argv, *packages],
        timeout=_dependency_install_timeout_seconds(),
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"dependency install failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def _run_locked_dependency_install(
    *, uv_bin: Path, python_bin: Path, lock_path: Path
) -> None:
    """Install the already-validated release lock with hash enforcement."""
    result = _run_uv(
        [
            str(uv_bin),
            "pip",
            "install",
            "--python",
            str(python_bin),
            "--require-hashes",
            "-r",
            str(lock_path),
        ],
        timeout=_dependency_install_timeout_seconds(),
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"locked dependency install failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


def _packaged_source_revision(repo_root: Path) -> str:
    """Fingerprint the source inputs used to build the Officina wheel.

    Plugin managers install a copied source tree without its ``.git``
    directory.  In that environment there is no commit ID to record, so the
    managed-runtime artifact records a stable 160-bit prefix of a SHA-256
    digest instead.  The digest covers the build configuration and every
    non-generated regular file beneath the configured ``officina`` package,
    with repository-relative POSIX paths separating otherwise-identical
    bytes. Python bytecode caches are excluded because importing the copied
    plugin may create them before the wheel build and their bytes are
    machine-specific, not source identity.

    Symlinks and special files are rejected rather than followed: a packaged
    runtime must be reproducible from self-contained, regular wheel inputs.
    The wheel's separate SHA-256 remains the authoritative identity of the
    exact built artifact.
    """
    pyproject_access = toml_io.open(repo_root, "pyproject.toml")
    pyproject = pyproject_access.path
    source_root = repo_root / "src" / "officina"
    if pyproject.is_symlink() or not pyproject.is_file():
        raise ManagedRuntimeError(
            f"could not identify Officina packaged source: not a regular file: {pyproject}"
        )
    if source_root.is_symlink() or not source_root.is_dir():
        raise ManagedRuntimeError(
            f"could not identify Officina packaged source: not a directory: {source_root}"
        )
    with pyproject_access as stream:
        pyproject_bytes = stream.read().encode("utf-8")

    source_files: list[Path] = []
    for path in source_root.rglob("*"):
        if path.is_symlink():
            raise ManagedRuntimeError(
                f"could not identify Officina packaged source: symlink is forbidden: {path}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise ManagedRuntimeError(
                f"could not identify Officina packaged source: not a regular file: {path}"
            )
        relative_to_source = path.relative_to(source_root)
        if "__pycache__" in relative_to_source.parts or path.suffix in {
            ".pyc",
            ".pyo",
        }:
            continue
        source_files.append(path)
    if not source_files:
        raise ManagedRuntimeError(
            f"could not identify Officina packaged source: no package files under {source_root}"
        )

    digest = hashlib.sha256()
    for path in (pyproject, *sorted(source_files)):
        relative_path = path.relative_to(repo_root).as_posix().encode("utf-8")
        content = pyproject_bytes if path == pyproject else path.read_bytes()
        content_digest = hashlib.sha256(content).digest()
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(content_digest)
    return digest.hexdigest()[:40]


def _source_revision(repo_root: Path) -> str:
    """Return the Git commit or a deterministic packaged-source fingerprint."""
    try:
        result = run_git(
            repo_root,
            "rev-parse",
            "--show-toplevel",
            "HEAD",
            check=False,
            timeout=30,
        )
        output_lines = result.stdout.decode("utf-8", errors="strict").splitlines()
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return _packaged_source_revision(repo_root)
    if result.returncode == 0 and len(output_lines) == 2:
        reported_root, revision = output_lines
        try:
            owns_source = Path(reported_root).resolve() == repo_root.resolve()
        except OSError:
            owns_source = False
        if owns_source and re.fullmatch(r"[0-9a-f]{40}", revision):
            return revision
    return _packaged_source_revision(repo_root)


def _build_officina_wheel(
    *, uv_bin: Path, python_bin: Path, repo_root: Path, artifact_dir: Path
) -> tuple[Path, str, str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result = _run_uv(
        [
            str(uv_bin), "build", "--wheel", "--no-build-isolation",
            "--python", str(python_bin), "--out-dir", str(artifact_dir),
            str(repo_root),
        ],
        timeout=_dependency_install_timeout_seconds(),
    )
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"Officina wheel build failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    wheels = tuple(artifact_dir.glob("famulus_officina-*.whl"))
    if len(wheels) != 1:
        raise ManagedRuntimeError(
            f"Officina wheel build produced {len(wheels)} matching artifacts; expected 1"
        )
    wheel = wheels[0]
    return wheel, hashlib.sha256(wheel.read_bytes()).hexdigest(), _source_revision(repo_root)


def _run_candidate_probe(argv: list[str]) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=env,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagedRuntimeError(f"candidate runtime probe failed to run: {exc}") from exc
    if result.returncode != 0:
        raise ManagedRuntimeError(
            f"candidate runtime probe failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def _validate_candidate_runtime(*, python_bin: Path) -> None:
    _run_candidate_probe(
        [
            str(python_bin), "-I", "-c",
            "import yaml; assert yaml.CSafeLoader; import officina.dispatcher.cli",
        ]
    )
    _run_candidate_probe(
        [str(python_bin), "-I", "-m", "officina.dispatcher.cli", "--help"]
    )


def _candidate_python_identity(*, python_bin: Path) -> dict[str, object]:
    """Read the candidate interpreter identity without ambient Python state."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    program = (
        "import json,platform,sys;"
        "print(json.dumps({'implementation':sys.implementation.name,"
        "'version':platform.python_version(),'build':platform.python_build(),"
        "'compiler':platform.python_compiler(),'platform':platform.platform(),"
        "'cache_tag':sys.implementation.cache_tag,'executable':sys.executable},sort_keys=True))"
    )
    try:
        result = subprocess.run(
            [str(python_bin), "-I", "-c", program],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=env,
            timeout=30,
        )
        identity = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ManagedRuntimeError(f"could not identify candidate Python: {exc}") from exc
    string_keys = ("implementation", "version", "compiler", "platform", "cache_tag", "executable")
    build = identity.get("build") if isinstance(identity, dict) else None
    if (
        not isinstance(identity, dict)
        or any(not isinstance(identity.get(key), str) for key in string_keys)
        or not isinstance(build, list)
        or len(build) != 2
        or any(not isinstance(value, str) for value in build)
    ):
        detail = result.stderr.strip() if result.returncode != 0 else "invalid identity output"
        raise ManagedRuntimeError(f"could not identify candidate Python: {detail}")
    return {key: identity[key] for key in (*string_keys, "build")}


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


def _venv_site_packages(venv_dir: Path, *, platform: str, python_version: str) -> Path:
    """Return the site-packages directory a real ``uv venv --python ... venv_dir``
    creates for ``platform``, mirroring ``_venv_python_bin``'s platform branch.
    """
    if platform == "windows":
        return venv_dir / "Lib" / "site-packages"
    version_parts = python_version.split(".")
    if len(version_parts) < 2 or not all(part.isdigit() for part in version_parts[:2]):
        raise ManagedRuntimeError(f"invalid managed Python version: {python_version!r}")
    abi_version = ".".join(version_parts[:2])
    return venv_dir / "lib" / f"python{abi_version}" / "site-packages"


def _install_officina_self(*, repo_root: Path, venv_dir: Path, platform: str, python_version: str) -> None:
    """Copy the running ``officina`` package itself into the release venv.

    The batch dependency install above only installs what
    ``runtime_dependencies.json`` declares -- third-party packages officina's
    own code needs at runtime. ``officina`` is not, and should not be, one of
    those entries (it isn't published anywhere the batch installer could
    fetch it from); without this step every release venv would provision
    successfully and then fail with ``ModuleNotFoundError: No module named
    'officina'`` the moment ``dispatcher`` (or anything else) tries to exec
    into it, since ``dispatcher`` always runs as ``-m officina.dispatcher.cli``
    against the release's own interpreter.

    A plain directory copy (not ``uv pip install <repo_root>``) is used
    deliberately: officina ships non-.py package data (blueprint.yaml files,
    certification records, schemas) that a wheel build would need explicit
    packaging config to carry, and a copy needs none of that. Each release
    gets its own independent snapshot, not a live link back to the source
    tree, matching how the declared third-party dependencies are pinned
    per-release rather than shared.
    """
    source = repo_root / "src" / "officina"
    if not source.is_dir():
        raise ManagedRuntimeError(f"officina source not found at {source}")
    destination = _venv_site_packages(venv_dir, platform=platform, python_version=python_version) / "officina"
    try:
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__"))
    except OSError as exc:
        raise ManagedRuntimeError(f"could not install officina into the release venv: {exc}") from exc


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
    lock_input_path: Path,
    lock_path: Path,
    platform: str,
    uv_bin: Path,
    uv_version: str,
    python_version: str,
    repo_root: Path | None = None,
    optional_module_ids: tuple[str, ...] = (),
) -> RuntimePointer:
    """Create a new release directory, provision its managed interpreter,
    install its locked Python dependencies, install Officina, verify the
    candidate when a repository is explicit, and activate the release.

    ``python_version`` should be the pinned ``managed_python.preferred``
    value from install-info.toml (see officina.install.install_info); it is
    passed straight through to ``uv venv --python``.

    Production callers pass ``repo_root`` explicitly. That path is validated,
    built as a wheel, installed, probed in isolation, and recorded by wheel
    digest plus Git revision or copied-source fingerprint. The omitted-root
    compatibility path retains the target branch's direct package-copy
    behavior for low-level callers.

    Core-only candidates use the checked-in universal lock.  A requested
    optional module selection is compiled into a release-local hash-checked
    lock, leaving the checked-in core lock untouched.

    On any failure (bad manifest, failed venv creation, failed batch
    install, failed Officina build or validation, failed self-install, or
    failed resolver deployment), no release is activated: current.json is
    left untouched and no new pointer is written. The dependency-free launcher
    resolver and its trust sidecar are deployed *before* activation for exactly
    this reason: a deployment failure must prevent activation, not follow it.
    """
    selected_module_ids = tuple(sorted(set(optional_module_ids)))
    selected_lock_input_path = lock_input_path
    selected_lock_path = lock_path
    try:
        runtime_lock.selected_runtime_module_ids(
            manifest_path, optional_module_ids=selected_module_ids
        )
        if not selected_module_ids:
            lock_metadata = runtime_lock.validate_runtime_lock(
                manifest_path=manifest_path,
                input_path=lock_input_path,
                lock_path=lock_path,
                expected_uv_version=uv_version,
                expected_python_version=python_version,
            )
    except runtime_lock.RuntimeLockError as exc:
        raise ManagedRuntimeError(f"invalid runtime lock or optional module selection: {exc}") from exc

    explicit_repo_root = repo_root is not None
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    repo_root = Path(repo_root).resolve()
    repository_config = repo_root / toml_io.repository_config_filename()
    release_id = _new_release_id()
    release_dir = runtime_root / "releases" / release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = release_dir / "venv"
    python_bin = _venv_python_bin(venv_dir, platform=platform)

    try:
        if selected_module_ids:
            selected_lock_input_path = release_dir / "runtime-lock" / "requirements-selected.in"
            selected_lock_path = release_dir / "runtime-lock" / "requirements-selected.lock"
            lock_metadata = runtime_lock.generate_runtime_lock(
                manifest_path=manifest_path,
                input_path=selected_lock_input_path,
                lock_path=selected_lock_path,
                uv_bin=uv_bin,
                expected_uv_version=uv_version,
                python_version=python_version,
                selected_module_ids=selected_module_ids,
            )
    except runtime_lock.RuntimeLockError as exc:
        raise ManagedRuntimeError(f"invalid runtime lock: {exc}") from exc

    _create_release_venv(uv_bin=uv_bin, venv_dir=venv_dir, python_version=python_version)
    _run_locked_dependency_install(
        uv_bin=uv_bin,
        python_bin=python_bin,
        lock_path=selected_lock_path,
    )
    if explicit_repo_root:
        try:
            from officina.configuration.repository import load_repository_configuration

            load_repository_configuration(repository_config)
        except Exception as exc:
            raise ManagedRuntimeError(f"invalid repository configuration: {exc}") from exc
        wheel, wheel_sha256, source_revision = _build_officina_wheel(
            uv_bin=uv_bin,
            python_bin=python_bin,
            repo_root=repo_root,
            artifact_dir=release_dir / "artifacts",
        )
        _run_dependency_install(
            uv_bin=uv_bin,
            python_bin=python_bin,
            packages=(str(wheel),),
            no_deps=True,
        )
        _validate_candidate_runtime(python_bin=python_bin)
        python_identity = _candidate_python_identity(python_bin=python_bin)
        if python_identity["version"] != python_version:
            raise ManagedRuntimeError(
                "candidate Python version mismatch: "
                f"expected {python_version}, got {python_identity['version']}"
            )
        atomic_files.atomic_replace_bytes(
            release_dir / "artifact.json",
            json.dumps(
                {
                    "schema_version": 3,
                    "wheel": wheel.name,
                    "wheel_sha256": wheel_sha256,
                    "source_revision": source_revision,
                    "runtime_lock": {
                        "path": str(selected_lock_path),
                        "sha256": lock_metadata.lock_sha256,
                        "input_sha256": lock_metadata.input_sha256,
                        "uv_version": lock_metadata.uv_version,
                    },
                    "selected_module_ids": list(selected_module_ids),
                    "python": python_identity,
                },
                indent=2,
            ).encode("utf-8"),
            allowed_root=release_dir,
            mode=0o600,
        )
    else:
        _install_officina_self(
            repo_root=repo_root,
            venv_dir=venv_dir,
            platform=platform,
            python_version=python_version,
        )

    trusted_interpreter_roots = (_uv_python_install_dir(uv_bin),)
    _deploy_resolver(runtime_root=runtime_root, trusted_interpreter_roots=trusted_interpreter_roots)
    return activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
        repository_config=repository_config,
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
