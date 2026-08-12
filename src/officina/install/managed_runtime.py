"""Build a versioned managed-runtime candidate release from the supported
runtime_dependencies.json manifest, installing all declared Python
dependencies in one atomic batch instead of ambient, best-effort per-package
pip calls.

``prepare_candidate_release`` builds and validates an immutable candidate and
resolver bundle without changing ``current.json``.  The transaction owner may
then complete its other pre-commit checks and call
``activate_prepared_release`` at the pointer commit boundary.
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

from officina.common import atomic_files, toml_io
from officina.common.git_provenance import run_git
from officina.install.runtime_pointer import (
    RuntimePointer,
    RuntimePointerError,
    activate_release,
    load_current_pointer,
)

_VERSION_OPERATOR_RE = re.compile(r"^(==|>=|<=|!=|~=|>|<)")

_DEFAULT_DEPENDENCY_INSTALL_TIMEOUT_SECONDS = 600
_CORE_RUNTIME_PACKAGES = ("setuptools==80.9.0", "PyYAML==6.0.2")


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
_RELEASE_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-[0-9a-f]{6}$")
_BUNDLE_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ManagedRuntimeError(Exception):
    """Raised when the runtime dependency manifest is unreadable/unsupported
    or the batch dependency install fails."""


@dataclass(frozen=True)
class PreparedRelease:
    """Validated but inactive runtime state owned by the installer transaction."""

    release_id: str
    release_dir: Path
    python_bin: Path
    repository_config: Path
    trusted_interpreter_roots: tuple[Path, ...]
    resolver_bundle_id: str


def _package_spec(name: str, version: str | None) -> str:
    if not version or version == "any":
        return name
    if _VERSION_OPERATOR_RE.match(version):
        return f"{name}{version}"
    return f"{name}=={version}"


# Packages declared in runtime_dependencies.json that are large and needed by
# only a single feature skill, not by officina/dispatcher or core skill
# functionality generally. marker-pdf (pdf-to-markdown's OCR models) pulls in
# the full torch/transformers/CUDA stack -- several GB -- for a capability
# most installs never touch. This is a pure installer-policy decision (what
# to install by default), not a property of the manifest itself, so it's kept
# here rather than as a new field on the schema-validated manifest.
_OPTIONAL_HEAVY_PACKAGE_NAMES = frozenset({"marker-pdf"})


def _iter_declared_dependencies(manifest_path: Path, *, platform: str):
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
                yield name, dependency.get("version")


def declared_python_packages(
    manifest_path: Path, *, platform: str, include_optional: bool = True
) -> tuple[str, ...]:
    """Return the deduplicated, sorted pip install specs for every
    python-package dependency declared for ``platform`` in a supported
    runtime_dependencies.json manifest.

    The first version constraint seen for a given package name (case-folded)
    wins; later, less-specific ("any") duplicates for the same package are
    ignored.

    ``include_optional=False`` excludes ``_OPTIONAL_HEAVY_PACKAGE_NAMES``
    (see that constant) -- the "core" set installed by default; pass
    ``include_optional=True`` (the default, matching historical behavior) to
    get the full set instead.
    """
    seen: dict[str, str] = {}
    for name, version in _iter_declared_dependencies(manifest_path, platform=platform):
        key = name.casefold()
        if not include_optional and key in _OPTIONAL_HEAVY_PACKAGE_NAMES:
            continue
        seen.setdefault(key, _package_spec(name, version))

    return tuple(sorted(seen.values(), key=str.casefold))


def optional_python_packages(manifest_path: Path, *, platform: str) -> tuple[str, ...]:
    """Return just the declared pip install specs considered optional/heavy
    (see ``_OPTIONAL_HEAVY_PACKAGE_NAMES``) -- the packages the "core" set
    excludes. Lets a caller tell the user what's being deferred before
    deciding whether to include them.
    """
    full = set(declared_python_packages(manifest_path, platform=platform, include_optional=True))
    core = set(declared_python_packages(manifest_path, platform=platform, include_optional=False))
    return tuple(sorted(full - core, key=str.casefold))


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
    return venv_dir / "lib" / f"python{python_version}" / "site-packages"


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


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    """Durably record a directory-entry mutation where the host supports it."""
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_exact_release_candidate(releases_root: Path, release_id: str) -> None:
    """Remove only the freshly allocated immediate child and sync its parent."""
    if _RELEASE_ID_RE.fullmatch(release_id) is None:
        raise ManagedRuntimeError(f"refusing to remove invalid release id: {release_id!r}")
    release_dir = releases_root / release_id
    if release_dir.parent.absolute() != releases_root.absolute():
        raise ManagedRuntimeError("refusing to remove a non-child release candidate")
    if release_dir.is_symlink():
        release_dir.unlink()
    elif release_dir.exists():
        if not release_dir.is_dir():
            raise ManagedRuntimeError(
                f"refusing to remove non-directory release candidate: {release_dir}"
            )
        shutil.rmtree(release_dir)
    _fsync_directory(releases_root)


def _resolver_bundle_material(
    trusted_interpreter_roots: tuple[Path, ...],
) -> tuple[bytes, bytes, bytes, str]:
    resolver_bytes = _RESOLVER_SOURCE.read_bytes()
    roots: list[str] = []
    for root in trusted_interpreter_roots:
        if not root.is_absolute():
            raise ManagedRuntimeError(f"trusted interpreter root must be absolute: {root}")
        rendered = str(root.resolve())
        if rendered not in roots:
            roots.append(rendered)
    trust_bytes = _json_bytes(roots)
    manifest_bytes = _json_bytes(
        {
            "schema_version": 1,
            "files": {
                "launch.py": hashlib.sha256(resolver_bytes).hexdigest(),
                "trusted-roots.json": hashlib.sha256(trust_bytes).hexdigest(),
            },
        }
    )
    return resolver_bytes, trust_bytes, manifest_bytes, hashlib.sha256(manifest_bytes).hexdigest()


def _validate_resolver_bundle(*, runtime_root: Path, resolver_bundle_id: str) -> Path:
    """Validate the named immutable resolver bundle and return its directory."""
    if _BUNDLE_ID_RE.fullmatch(resolver_bundle_id) is None:
        raise ManagedRuntimeError("invalid resolver bundle identifier")
    bundle_dir = runtime_root / "resolvers" / "bundles" / resolver_bundle_id
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise ManagedRuntimeError(f"resolver bundle is missing or unsafe: {resolver_bundle_id}")
    try:
        files = {
            name: atomic_files.read_regular_file_bytes(
                bundle_dir / name, allowed_root=bundle_dir
            )
            for name in ("launch.py", "trusted-roots.json", "manifest.json")
        }
        if hashlib.sha256(files["manifest.json"]).hexdigest() != resolver_bundle_id:
            raise ManagedRuntimeError("resolver bundle manifest digest does not match its id")
        manifest = json.loads(files["manifest.json"])
        if manifest != {
            "files": {
                "launch.py": hashlib.sha256(files["launch.py"]).hexdigest(),
                "trusted-roots.json": hashlib.sha256(files["trusted-roots.json"]).hexdigest(),
            },
            "schema_version": 1,
        }:
            raise ManagedRuntimeError("resolver bundle file digest validation failed")
        roots = json.loads(files["trusted-roots.json"])
        if not isinstance(roots, list) or any(
            not isinstance(root, str) or not Path(root).is_absolute() for root in roots
        ):
            raise ManagedRuntimeError("resolver bundle trust data is invalid")
    except ManagedRuntimeError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ManagedRuntimeError(f"could not validate resolver bundle: {exc}") from exc
    return bundle_dir


def _publish_resolver_bundle(
    *, runtime_root: Path, trusted_interpreter_roots: tuple[Path, ...]
) -> str:
    """Publish resolver code and trust data once under their manifest digest."""
    resolver_bytes, trust_bytes, manifest_bytes, bundle_id = _resolver_bundle_material(
        trusted_interpreter_roots
    )
    bundles_root = runtime_root / "resolvers" / "bundles"
    staging_root = runtime_root / "resolvers" / "staging"
    try:
        atomic_files.ensure_secure_directory(bundles_root)
        atomic_files.ensure_secure_directory(staging_root)
        bundle_dir = bundles_root / bundle_id
        if bundle_dir.exists():
            _validate_resolver_bundle(
                runtime_root=runtime_root, resolver_bundle_id=bundle_id
            )
            return bundle_id
        stage_dir = staging_root / secrets.token_hex(16)
        stage_dir.mkdir(mode=0o700)
        try:
            for name, data, mode in (
                ("launch.py", resolver_bytes, 0o555),
                ("trusted-roots.json", trust_bytes, 0o444),
                ("manifest.json", manifest_bytes, 0o444),
            ):
                atomic_files.atomic_create_bytes(
                    stage_dir / name, data, allowed_root=stage_dir, mode=mode
                )
            _fsync_directory(stage_dir)
            os.replace(stage_dir, bundle_dir)
            _fsync_directory(bundles_root)
        finally:
            if stage_dir.exists():
                shutil.rmtree(stage_dir)
        _validate_resolver_bundle(
            runtime_root=runtime_root, resolver_bundle_id=bundle_id
        )
        return bundle_id
    except ManagedRuntimeError:
        raise
    except OSError as exc:
        raise ManagedRuntimeError(f"could not publish resolver bundle: {exc}") from exc


def _deploy_resolver(
    *, runtime_root: Path, trusted_interpreter_roots: tuple[Path, ...]
) -> None:
    """Deploy the fixed bootstrap, retaining legacy v1/v2 trust compatibility."""
    try:
        resolver_dir = runtime_root / "bootstrap" / "resolvers" / "v1"
        atomic_files.ensure_secure_directory(resolver_dir)
        atomic_files.atomic_replace_bytes(
            resolver_dir / "launch.py",
            _RESOLVER_SOURCE.read_bytes(),
            allowed_root=resolver_dir,
            mode=0o755,
        )
        atomic_files.atomic_replace_bytes(
            resolver_dir / "trusted-roots.json",
            _json_bytes([str(root.resolve()) for root in trusted_interpreter_roots]),
            allowed_root=resolver_dir,
            mode=0o600,
        )
    except OSError as exc:
        raise ManagedRuntimeError(f"could not deploy the launcher resolver: {exc}") from exc


def _prepare_candidate_release(
    *,
    runtime_root: Path,
    manifest_path: Path,
    platform: str,
    uv_bin: Path,
    python_version: str,
    repo_root: Path,
    include_optional_dependencies: bool = True,
    build_wheel: bool,
) -> PreparedRelease:
    """Build and validate one candidate without mutating ``current.json``.

    ``python_version`` should be the pinned ``managed_python.preferred``
    value from install-info.toml (see officina.install.install_info); it is
    passed straight through to ``uv venv --python``.

    Every exception after allocation removes only the exact new release child
    and durably records that removal.  Published content-addressed resolver
    bundles may remain cached; older releases are never pruned here.
    """
    repo_root = Path(repo_root).resolve()
    repository_config = repo_root / toml_io.repository_config_filename()
    packages = declared_python_packages(
        manifest_path, platform=platform, include_optional=include_optional_dependencies
    )
    release_id = _new_release_id()
    release_dir = runtime_root / "releases" / release_id
    releases_root = runtime_root / "releases"
    atomic_files.ensure_secure_directory(releases_root)
    allocated = False
    try:
        release_dir.mkdir(mode=0o700)
        allocated = True
        venv_dir = release_dir / "venv"
        python_bin = _venv_python_bin(venv_dir, platform=platform)

        _create_release_venv(uv_bin=uv_bin, venv_dir=venv_dir, python_version=python_version)
        if build_wheel:
            from officina.common.repository_configuration import load_repository_configuration

            try:
                load_repository_configuration(repository_config)
            except Exception as exc:
                raise ManagedRuntimeError(f"invalid repository configuration: {exc}") from exc
            _run_dependency_install(
                uv_bin=uv_bin, python_bin=python_bin, packages=_CORE_RUNTIME_PACKAGES
            )
            wheel, wheel_sha256, source_revision = _build_officina_wheel(
                uv_bin=uv_bin,
                python_bin=python_bin,
                repo_root=repo_root,
                artifact_dir=release_dir / "artifacts",
            )
            _run_dependency_install(
                uv_bin=uv_bin, python_bin=python_bin, packages=(str(wheel),)
            )
            module_packages = tuple(
                package for package in packages
                if not re.match(r"(?i)^(?:pyyaml|setuptools)(?:[<>=!~].*)?$", package)
            )
            _run_dependency_install(
                uv_bin=uv_bin, python_bin=python_bin, packages=module_packages
            )
            _validate_candidate_runtime(python_bin=python_bin)
            atomic_files.atomic_replace_bytes(
                release_dir / "artifact.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "wheel": wheel.name,
                        "wheel_sha256": wheel_sha256,
                        "source_revision": source_revision,
                    },
                    indent=2,
                ).encode("utf-8"),
                allowed_root=release_dir,
                mode=0o600,
            )
        else:
            module_packages = tuple(
                package for package in packages
                if not re.match(r"(?i)^(?:pyyaml|setuptools)(?:[<>=!~].*)?$", package)
            )
            _run_dependency_install(
                uv_bin=uv_bin,
                python_bin=python_bin,
                packages=(*_CORE_RUNTIME_PACKAGES, *module_packages),
            )
            _install_officina_self(
                repo_root=repo_root,
                venv_dir=venv_dir,
                platform=platform,
                python_version=python_version,
            )

        trusted_interpreter_roots = (_uv_python_install_dir(uv_bin),)
        bundle_id = _publish_resolver_bundle(
            runtime_root=runtime_root,
            trusted_interpreter_roots=trusted_interpreter_roots,
        )
        _deploy_resolver(
            runtime_root=runtime_root,
            trusted_interpreter_roots=trusted_interpreter_roots,
        )
        _validate_resolver_bundle(
            runtime_root=runtime_root, resolver_bundle_id=bundle_id
        )
        return PreparedRelease(
            release_id=release_id,
            release_dir=release_dir,
            python_bin=python_bin,
            repository_config=repository_config,
            trusted_interpreter_roots=trusted_interpreter_roots,
            resolver_bundle_id=bundle_id,
        )
    except BaseException:
        if allocated:
            _remove_exact_release_candidate(releases_root, release_id)
        raise


def prepare_candidate_release(
    *,
    runtime_root: Path,
    manifest_path: Path,
    platform: str,
    uv_bin: Path,
    python_version: str,
    repo_root: Path,
    include_optional_dependencies: bool,
) -> PreparedRelease:
    """Build a wheel-backed, validated candidate without activating it."""
    return _prepare_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest_path,
        platform=platform,
        uv_bin=uv_bin,
        python_version=python_version,
        repo_root=repo_root,
        include_optional_dependencies=include_optional_dependencies,
        build_wheel=True,
    )


def _validate_prepared_release(runtime_root: Path, prepared: PreparedRelease) -> None:
    if prepared.release_dir.parent.absolute() != (runtime_root / "releases").absolute():
        raise ManagedRuntimeError("prepared release is not an immediate release child")
    if prepared.release_dir.name != prepared.release_id or not prepared.release_dir.is_dir():
        raise ManagedRuntimeError("prepared release identity does not match its directory")
    if not prepared.python_bin.exists():
        raise ManagedRuntimeError("prepared release interpreter is missing")
    artifact_path = prepared.release_dir / "artifact.json"
    if artifact_path.exists():
        try:
            artifact = json.loads(
                atomic_files.read_regular_file_bytes(
                    artifact_path, allowed_root=prepared.release_dir
                )
            )
            wheel = prepared.release_dir / "artifacts" / artifact["wheel"]
            wheel_bytes = atomic_files.read_regular_file_bytes(
                wheel, allowed_root=prepared.release_dir
            )
            if hashlib.sha256(wheel_bytes).hexdigest() != artifact["wheel_sha256"]:
                raise ManagedRuntimeError("prepared release wheel digest is invalid")
            _validate_candidate_runtime(python_bin=prepared.python_bin)
        except ManagedRuntimeError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise ManagedRuntimeError(f"prepared release artifact is invalid: {exc}") from exc
    _validate_resolver_bundle(
        runtime_root=runtime_root,
        resolver_bundle_id=prepared.resolver_bundle_id,
    )


def activate_prepared_release(
    runtime_root: Path, prepared: PreparedRelease
) -> RuntimePointer:
    """Revalidate a prepared release and atomically activate pointer schema v3."""
    _validate_prepared_release(runtime_root, prepared)
    try:
        return activate_release(
            runtime_root=runtime_root,
            release_dir=prepared.release_dir,
            python_bin=prepared.python_bin,
            repository_config=prepared.repository_config,
            trusted_interpreter_roots=prepared.trusted_interpreter_roots,
            resolver_bundle_id=prepared.resolver_bundle_id,
        )
    except RuntimePointerError as exc:
        raise ManagedRuntimeError(f"prepared release pointer validation failed: {exc}") from exc


def _bundle_trusted_roots(runtime_root: Path, bundle_id: str) -> tuple[Path, ...]:
    bundle = _validate_resolver_bundle(
        runtime_root=runtime_root, resolver_bundle_id=bundle_id
    )
    payload = json.loads((bundle / "trusted-roots.json").read_text(encoding="utf-8"))
    return tuple(Path(entry) for entry in payload)


def prune_old_releases(runtime_root: Path) -> tuple[Path, ...]:
    """Retain the active and newest prior exact release; remove older children."""
    try:
        raw_pointer = json.loads(
            atomic_files.read_regular_file_bytes(
                runtime_root / "current.json", allowed_root=runtime_root
            )
        )
        trusted_roots: tuple[Path, ...] = ()
        if raw_pointer.get("schema_version") == 3:
            trusted_roots = _bundle_trusted_roots(
                runtime_root, raw_pointer["resolver_bundle_id"]
            )
        pointer = load_current_pointer(
            runtime_root=runtime_root,
            trusted_interpreter_roots=trusted_roots,
        )
        releases_root = runtime_root / "releases"
        exact = sorted(
            path for path in releases_root.iterdir()
            if _RELEASE_ID_RE.fullmatch(path.name) is not None
            and path.is_dir()
            and not path.is_symlink()
        )
        previous = next(
            (path for path in reversed(exact) if path.name != pointer.release_id),
            None,
        )
        retained = {pointer.release_id}
        if previous is not None:
            retained.add(previous.name)
        removed: list[Path] = []
        for path in exact:
            if path.name in retained:
                continue
            _remove_exact_release_candidate(releases_root, path.name)
            removed.append(path)
        return tuple(removed)
    except ManagedRuntimeError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RuntimePointerError) as exc:
        raise ManagedRuntimeError(f"could not prune managed runtime releases: {exc}") from exc


def build_candidate_release(
    *,
    runtime_root: Path,
    manifest_path: Path,
    platform: str,
    uv_bin: Path,
    python_version: str,
    repo_root: Path | None = None,
    include_optional_dependencies: bool = True,
) -> RuntimePointer:
    """Compatibility API: prepare and immediately activate one candidate."""
    explicit_repo_root = repo_root is not None
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    prepared = _prepare_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest_path,
        platform=platform,
        uv_bin=uv_bin,
        python_version=python_version,
        repo_root=repo_root,
        include_optional_dependencies=include_optional_dependencies,
        build_wheel=explicit_repo_root,
    )
    return activate_prepared_release(runtime_root, prepared)


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
    "PreparedRelease",
    "activate_prepared_release",
    "build_candidate_release",
    "declared_python_packages",
    "prepare_candidate_release",
    "prune_old_releases",
    "uv_python_install_dir",
]
