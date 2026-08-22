from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
import tomllib
import hashlib
from pathlib import Path

import pytest

import officina.common.atomic_files as atomic_files
import officina.install.managed_runtime as managed_runtime_module
from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.context import InstallationContext
from officina.install.managed_runtime import (
    ManagedRuntimeError,
    _deploy_resolver,
    _source_revision,
    _venv_python_bin,
    _venv_site_packages,
    build_candidate_release as _build_candidate_release,
    declared_python_packages,
    deployed_resolver_trusted_roots,
    optional_runtime_modules,
    package_size_estimates,
    load_cached_package_index_metadata,
)
from officina.install.runtime_lock import render_runtime_requirements
from test_support.git_repository import GitTestRepository
from test_support.uv_subprocess import FakeCompletedProcess, fake_uv_subprocess_run

REPO_ROOT = Path(__file__).resolve().parents[1]

REAL_MANIFEST = Path(__file__).resolve().parents[1] / "references" / "blueprint" / "runtime_dependencies.json"
UV_BIN = shutil.which("uv")
# A real Path's str() renders with the host's native separators regardless
# of the `platform=` string passed to build_candidate_release (that
# parameter only selects *logical* branching inside the function, e.g.
# venv layout -- it never changes how Python itself stringifies a Path
# object on this interpreter). So every assertion below must compare
# against str(FAKE_UV_BIN), not a hardcoded POSIX-style literal, to stay
# correct on a real Windows test host.
FAKE_UV_BIN = Path("/fake/uv")
PINNED_UV_VERSION = "0.11.29"
PINNED_PYTHON_VERSION = "3.11.15"
LEGACY_RESOLVER_SOURCE = (
    REPO_ROOT / "tests" / "fixtures" / "officina" / "legacy_resolver_v1_launch.py"
)


def _pinned_uv_is_available() -> bool:
    if UV_BIN is None:
        return False
    result = subprocess.run(
        [UV_BIN, "--version"], capture_output=True, text=True, encoding="utf-8", errors="strict"
    )
    return result.returncode == 0 and result.stdout.split()[:2] == ["uv", PINNED_UV_VERSION]


PINNED_UV_AVAILABLE = _pinned_uv_is_available()


def _write_test_runtime_lock(tmp_path: Path, manifest_path: Path) -> tuple[Path, Path]:
    """Write a structurally valid lock bound to ``manifest_path``.

    Resolution correctness is covered by the checked-in generated lock and
    generator tests; managed-runtime tests only need a small offline fixture
    that exercises validation and command wiring.
    """
    input_path = tmp_path / "requirements-core.in"
    lock_path = tmp_path / "requirements-core.lock"
    rendered = render_runtime_requirements(manifest_path)
    input_path.write_text(rendered, encoding="utf-8")
    input_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    lock_body = f"rich==13.9.4 --hash=sha256:{'a' * 64}\n"
    lock_content_sha256 = hashlib.sha256(lock_body.encode("utf-8")).hexdigest()
    lock_path.write_text(
        "# famulus-runtime-lock-schema: 1\n"
        f"# input-sha256: {input_sha256}\n"
        f"# uv-version: {PINNED_UV_VERSION}\n"
        f"# python-version: {PINNED_PYTHON_VERSION}\n"
        f"# lock-content-sha256: {lock_content_sha256}\n"
        "#\n"
        f"{lock_body}",
        encoding="utf-8",
    )
    return input_path, lock_path


def build_candidate_release(**kwargs):
    """Supply the release-lock contract to legacy scenarios in this module."""
    if "lock_input_path" not in kwargs:
        manifest_path = Path(kwargs["manifest_path"])
        if manifest_path == REAL_MANIFEST:
            runtime_refs = REPO_ROOT / "references" / "runtime"
            kwargs["lock_input_path"] = runtime_refs / "requirements-core.in"
            kwargs["lock_path"] = runtime_refs / "requirements-core.lock"
        else:
            kwargs["lock_input_path"], kwargs["lock_path"] = _write_test_runtime_lock(
                manifest_path.parent, manifest_path
            )
    kwargs.setdefault("uv_version", PINNED_UV_VERSION)
    if kwargs.get("python_version") == "3.11":
        kwargs["python_version"] = PINNED_PYTHON_VERSION
    return _build_candidate_release(**kwargs)


def test_release_venv_uses_exact_managed_python(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store"),
    )
    input_path, lock_path = _write_test_runtime_lock(tmp_path, REAL_MANIFEST)

    build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        lock_input_path=input_path,
        lock_path=lock_path,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        uv_version=PINNED_UV_VERSION,
        python_version=PINNED_PYTHON_VERSION,
    )

    assert calls[0][1:] == [
        "venv", "--managed-python", "--python", PINNED_PYTHON_VERSION,
        calls[0][-1],
    ]


def test_release_installs_validated_lock_with_hashes(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store"),
    )
    input_path, lock_path = _write_test_runtime_lock(tmp_path, REAL_MANIFEST)

    build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        lock_input_path=input_path,
        lock_path=lock_path,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        uv_version=PINNED_UV_VERSION,
        python_version=PINNED_PYTHON_VERSION,
    )

    lock_install = next(call for call in calls if "--require-hashes" in call)
    assert lock_install[-3:] == ["--require-hashes", "-r", str(lock_path)]


def test_release_rejects_stale_lock_before_creating_release(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store"),
    )
    input_path, lock_path = _write_test_runtime_lock(tmp_path, REAL_MANIFEST)
    input_path.write_text("stale\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"

    with pytest.raises(ManagedRuntimeError, match="stale"):
        build_candidate_release(
            runtime_root=runtime_root,
            manifest_path=REAL_MANIFEST,
            lock_input_path=input_path,
            lock_path=lock_path,
            platform="linux",
            uv_bin=FAKE_UV_BIN,
            uv_version=PINNED_UV_VERSION,
            python_version=PINNED_PYTHON_VERSION,
        )

    assert calls == []
    assert not (runtime_root / "releases").exists()


def test_wheel_metadata_preserves_non_python_runtime_assets() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        configuration = tomllib.load(stream)

    setuptools = configuration["tool"]["setuptools"]
    assert setuptools["package-data"]["officina"] == ["**/*"]
    assert set(setuptools["exclude-package-data"]["officina"]) == {
        "**/__pycache__/*",
        "**/*.pyc",
        "**/*.pyo",
    }


def test_source_revision_falls_back_to_packaged_source_fingerprint(
    monkeypatch,
    tmp_path,
):
    repo_root = tmp_path / "packaged-plugin"
    source_root = repo_root / "src" / "officina"
    source_root.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='example'\n")
    module = source_root / "runtime.py"
    module.write_text("VALUE = 1\n")

    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 128, b"", b"fatal: not a git repository"
        ),
    )

    first = _source_revision(repo_root)
    assert len(first) == 40
    assert set(first) <= set("0123456789abcdef")
    assert _source_revision(repo_root) == first

    (repo_root / "README.md").write_text("not wheel input\n")
    assert _source_revision(repo_root) == first

    cache_file = source_root / "__pycache__" / "runtime.cpython-311.pyc"
    cache_file.parent.mkdir()
    cache_file.write_bytes(b"generated bytecode")
    assert _source_revision(repo_root) == first

    module.write_text("VALUE = 2\n")
    assert _source_revision(repo_root) != first


def test_source_revision_ignores_parent_repository_and_ambient_git_routing(
    monkeypatch,
    tmp_path,
):
    parent_repo = tmp_path / "unrelated-repository"
    git = GitTestRepository.create(parent_repo)
    (parent_repo / "tracked.txt").write_text("parent repository\n")
    git.git("add", "tracked.txt")
    git.git("commit", "--quiet", "-m", "initial")
    parent_revision = git.git("rev-parse", "HEAD").stdout.decode("ascii").strip()

    packaged_root = parent_repo / "plugin-copy"
    source_root = packaged_root / "src" / "officina"
    source_root.mkdir(parents=True)
    (packaged_root / "pyproject.toml").write_text("[project]\nname='example'\n")
    module = source_root / "runtime.py"
    module.write_text("VALUE = 1\n")

    ambient = GitTestRepository.create(tmp_path / "ambient-repository")
    (ambient.root / "tracked.txt").write_text("ambient repository\n")
    ambient.git("add", "tracked.txt")
    ambient.git("commit", "--quiet", "-m", "ambient")
    monkeypatch.setenv("GIT_DIR", str(ambient.root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(packaged_root))

    first = _source_revision(packaged_root)
    assert first != parent_revision
    module.write_text("VALUE = 2\n")
    assert _source_revision(packaged_root) != first


@pytest.mark.parametrize(
    "relative_symlink",
    (Path("linked.py"), Path("__pycache__") / "linked.pyc"),
)
def test_packaged_source_revision_rejects_symlinks_even_in_bytecode_paths(
    monkeypatch,
    tmp_path,
    relative_symlink,
):
    repo_root = tmp_path / "packaged-plugin"
    source_root = repo_root / "src" / "officina"
    source_root.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='example'\n")
    (source_root / "runtime.py").write_text("VALUE = 1\n")
    target = tmp_path / "outside.py"
    target.write_text("outside\n")
    link = source_root / relative_symlink
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=some Windows runners deny symlink creation; alternate=Linux and macOS exercise both ordinary and bytecode-path symlink rejection
        pytest.skip(f"symlink creation is unavailable: {exc}")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 128, b"", b"fatal: not a git repository"
        ),
    )

    with pytest.raises(ManagedRuntimeError, match="symlink is forbidden"):
        _source_revision(repo_root)


def test_declared_python_packages_defaults_to_core_module_selection():
    packages = declared_python_packages(REAL_MANIFEST, platform="linux")
    assert packages == (
        "bibtexparser",
        "cryptography>=44.0.1",
        "dateparser",
        "jsonschema>=4",
        "keyring",
        "pyflakes==3.2.0",
        "pytest-xdist==3.8.0",
        "pytest==8.3.4",
        "PyYAML>=6",
        "rich",
    )


def test_declared_python_packages_filters_by_platform(tmp_path):
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "skills": {
            "example": {
                "interfaces": {
                    "run": {
                        "dependencies": [
                            {"kind": "python-package", "name": "pywin32", "version": "1.0", "platforms": {"windows": True}},
                            {"kind": "python-package", "name": "pyyaml", "version": "6.0", "platforms": {"linux": True, "macos": True, "windows": True}},
                        ]
                    }
                }
            }
        },
    }))
    assert declared_python_packages(manifest, platform="linux") == ("pyyaml==6.0",)


def test_declared_python_packages_rejects_unsupported_schema_version(tmp_path):
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({"version": 3, "skills": {}}))
    with pytest.raises(ManagedRuntimeError):
        declared_python_packages(manifest, platform="linux")


def test_selected_optional_module_adds_its_platform_dependency_without_package_policy():
    packages = declared_python_packages(
        REAL_MANIFEST, platform="linux", selected_module_ids=("pdf-to-markdown",)
    )
    assert "marker-pdf" in packages


def test_optional_runtime_modules_describes_pdf_to_markdown():
    modules = optional_runtime_modules(REAL_MANIFEST, platform="linux")
    assert modules == ({"id": "pdf-to-markdown", "packages": ("marker-pdf",)},)


def test_package_size_estimates_use_wheel_or_sdist_metadata_or_report_unavailable():
    estimates = package_size_estimates(
        ("known", "unknown"),
        package_index_metadata={
            "known": {
                "urls": [
                    {"packagetype": "bdist_wheel", "size": 17},
                    {"packagetype": "sdist", "size": 23},
                ]
            }
        },
    )

    assert [(estimate.package, estimate.bytes) for estimate in estimates] == [
        ("known", 23),
        ("unknown", None),
    ]


def test_load_cached_package_index_metadata_is_offline_and_normalizes_specs(tmp_path):
    (tmp_path / "known.json").write_text('{"urls": []}', encoding="utf-8")

    assert load_cached_package_index_metadata(
        ("Known[extra]>=1", "missing"), cache_dir=tmp_path
    ) == {"known": {"urls": []}}


def test_venv_python_bin_posix_is_bin_python():
    venv_dir = Path("/fake/release/venv")
    for platform_name in ("linux", "macos"):
        assert _venv_python_bin(venv_dir, platform=platform_name) == venv_dir / "bin" / "python"


def test_venv_python_bin_windows_is_scripts_python_exe():
    venv_dir = Path("/fake/release/venv")
    assert _venv_python_bin(venv_dir, platform="windows") == venv_dir / "Scripts" / "python.exe"


def test_venv_site_packages_uses_major_minor_for_exact_patch():
    venv_dir = Path("/fake/release/venv")
    assert _venv_site_packages(
        venv_dir, platform="linux", python_version=PINNED_PYTHON_VERSION
    ) == venv_dir / "lib" / "python3.11" / "site-packages"


def test_build_candidate_release_on_windows_uses_scripts_python_exe(monkeypatch, tmp_path):
    """Regression test for a real bug: build_candidate_release used to
    hardcode `venv_dir / "bin" / "python"` with no platform branch, so on
    Windows (where `uv venv` creates `Scripts\\python.exe`) python_bin never
    existed. That made runtime_pointer.activate_release raise
    RuntimePointerError -- not a ManagedRuntimeError -- which
    _phase_entry.py's `except ManagedRuntimeError` didn't catch, crashing
    the installer. This exercises build_candidate_release with
    platform="windows" against a fake uv that lays out the venv the real
    Windows uv would, and asserts the batch pip-install call and the
    activated pointer both use the Windows interpreter path.
    """
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store", windows=True),
    )

    pointer = build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        platform="windows",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
    )

    assert pointer.python_bin == pointer.runtime_source / "venv" / "Scripts" / "python.exe"
    assert pointer.python_bin.exists()
    pip_call = calls[1]
    assert pip_call[:4] == [str(FAKE_UV_BIN), "pip", "install", "--python"]
    assert pip_call[4] == str(pointer.python_bin)


def test_build_candidate_release_creates_venv_then_one_batch_pip_install(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run", fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store")
    )

    pointer = build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
    )

    assert len(calls) == 3  # uv venv, one batch pip-install call (not per-package), uv python dir
    venv_call, pip_call, python_dir_call = calls
    assert venv_call == [
        str(FAKE_UV_BIN), "venv", "--managed-python", "--python",
        PINNED_PYTHON_VERSION, str(pointer.runtime_source / "venv"),
    ]
    assert pip_call[:4] == [str(FAKE_UV_BIN), "pip", "install", "--python"]
    assert pip_call[4] == str(pointer.python_bin)
    assert pip_call[-3:] == [
        "--require-hashes", "-r",
        str(REPO_ROOT / "references" / "runtime" / "requirements-core.lock"),
    ]
    assert python_dir_call == [str(FAKE_UV_BIN), "python", "dir"]
    assert pointer.python_bin.exists()


def test_build_candidate_release_core_lock_does_not_include_optional_module(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run", fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store")
    )

    build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
    )

    pip_call = calls[1]
    assert "marker-pdf" not in pip_call
    assert "--require-hashes" in pip_call
    assert "marker-pdf" not in Path(pip_call[-1]).read_text(encoding="utf-8")


def test_optional_candidate_generates_hash_checked_selection_lock_and_records_it(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run", fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store")
    )
    generated: dict[str, object] = {}

    def generate(**kwargs):
        generated.update(kwargs)
        lock_path = kwargs["lock_path"]
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("marker-pdf==1 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
        from officina.install.runtime_lock import RuntimeLockMetadata
        return RuntimeLockMetadata("i" * 64, PINNED_UV_VERSION, PINNED_PYTHON_VERSION, "l" * 64)

    monkeypatch.setattr("officina.install.managed_runtime.runtime_lock.generate_runtime_lock", generate)
    pointer = build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
        repo_root=REPO_ROOT,
        optional_module_ids=("pdf-to-markdown",),
    )

    assert generated["selected_module_ids"] == ("pdf-to-markdown",)
    lock_install = next(call for call in calls if "--require-hashes" in call)
    assert lock_install[-1].endswith("requirements-selected.lock")
    artifact = json.loads((pointer.runtime_source / "artifact.json").read_text(encoding="utf-8"))
    assert artifact["selected_module_ids"] == ["pdf-to-markdown"]
    assert artifact["runtime_lock"]["input_sha256"] == "i" * 64
    assert artifact["runtime_lock"]["sha256"] == "l" * 64


def test_build_candidate_release_provisions_venv_before_installing_packages(monkeypatch, tmp_path):
    """Dedicated sanity check for the uv-venv step's exact arguments and
    ordering, independent of the batch-install call-count assertion above."""
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run", fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store")
    )

    build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=REAL_MANIFEST,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
    )

    assert calls[0][:5] == [
        str(FAKE_UV_BIN), "venv", "--managed-python", "--python",
        PINNED_PYTHON_VERSION,
    ]
    assert calls[1][0] == str(FAKE_UV_BIN)


def test_build_candidate_release_failure_writes_no_pointer(monkeypatch, tmp_path):
    def fail(*a, **k):
        raise ManagedRuntimeError("simulated failure")

    monkeypatch.setattr("officina.install.managed_runtime._run_dependency_install", fail)
    monkeypatch.setattr(
        "officina.install.managed_runtime._create_release_venv",
        lambda **kwargs: None,
    )
    runtime_root = tmp_path / "runtime"
    with pytest.raises(ManagedRuntimeError):
        build_candidate_release(
            runtime_root=runtime_root,
            manifest_path=REAL_MANIFEST,
            platform="linux",
            uv_bin=FAKE_UV_BIN,
            python_version="3.11",
        )
    assert not (runtime_root / "current.json").exists()


def test_build_candidate_release_venv_failure_writes_no_pointer(monkeypatch, tmp_path):
    def fail(**kwargs):
        raise ManagedRuntimeError("simulated venv creation failure")

    monkeypatch.setattr("officina.install.managed_runtime._create_release_venv", fail)
    runtime_root = tmp_path / "runtime"
    with pytest.raises(ManagedRuntimeError):
        build_candidate_release(
            runtime_root=runtime_root,
            manifest_path=REAL_MANIFEST,
            platform="linux",
            uv_bin=FAKE_UV_BIN,
            python_version="3.11",
        )
    assert not (runtime_root / "current.json").exists()


def test_build_candidate_release_resolver_deploy_failure_writes_no_pointer(monkeypatch, tmp_path):
    """A failed resolver deployment (e.g. disk full, permissions, a
    concurrent-install race) must behave exactly like every other
    build_candidate_release failure: no release is activated (current.json
    stays untouched) and the raised exception is a typed ManagedRuntimeError
    the installer's `except ManagedRuntimeError` can catch cleanly -- not a
    raw OSError propagating as an unhandled crash. This also guards against
    regressing back to deploying the resolver *after* activate_release,
    which would let a deployment failure leave a release activated with a
    missing/broken resolver.
    """
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run", fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store")
    )
    monkeypatch.setattr(
        "officina.install.managed_runtime.atomic_files.atomic_replace_bytes",
        lambda *a, **k: (_ for _ in ()).throw(OSError("simulated disk full")),
    )
    runtime_root = tmp_path / "runtime"

    with pytest.raises(ManagedRuntimeError):
        build_candidate_release(
            runtime_root=runtime_root,
            manifest_path=REAL_MANIFEST,
            platform="linux",
            uv_bin=FAKE_UV_BIN,
            python_version="3.11",
        )

    assert not (runtime_root / "current.json").exists()
    # activate_release must never have run either: no release directory was
    # promoted, so no bootstrap/resolvers path exists beyond what
    # _deploy_resolver itself half-created before failing.
    assert not (runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py").exists()


def test_deploy_resolver_atomically_selects_a_complete_immutable_generation(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run", fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store")
    )
    atomic_calls: list = []
    real_atomic_replace_bytes = atomic_files.atomic_replace_bytes

    def spying_atomic_replace_bytes(*args, **kwargs):
        atomic_calls.append((args, kwargs))
        return real_atomic_replace_bytes(*args, **kwargs)

    monkeypatch.setattr(
        "officina.install.managed_runtime.atomic_files.atomic_replace_bytes",
        spying_atomic_replace_bytes,
    )
    runtime_root = tmp_path / "runtime"

    build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=REAL_MANIFEST,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
    )

    resolver_dir = runtime_root / "bootstrap" / "resolvers" / "v1"
    assert len(atomic_calls) == 4
    deployed = {Path(args[0]).name: kwargs["mode"] for args, kwargs in atomic_calls}
    resolver_path = resolver_dir / "launch.py"
    assert deployed == {
        "launch.py": 0o755,
        "trusted-roots.json": 0o644,
        "active.json": 0o644,
    }
    assert resolver_path.exists()
    assert resolver_path.read_bytes() == (
        Path(__file__).resolve().parents[1]
        / "src" / "officina" / "install" / "resolvers" / "launch.py"
    ).read_bytes()
    active = json.loads((resolver_dir / "active.json").read_text(encoding="utf-8"))
    assert active["schema_version"] == 1
    generation = (
        runtime_root / "bootstrap" / "resolvers" / "generations" / active["generation"]
    )
    assert generation.is_dir()
    assert (generation / "launch.py").read_bytes() == resolver_path.read_bytes()
    assert json.loads((generation / "trusted-roots.json").read_text(encoding="utf-8"))
    assert os.access(resolver_path, os.X_OK)


def test_public_trusted_root_reader_matches_a_valid_deployed_generation(tmp_path):
    runtime_root = tmp_path / "runtime"
    trusted_root = tmp_path / "trusted-python"

    _deploy_resolver(
        runtime_root=runtime_root,
        trusted_interpreter_roots=(trusted_root,),
    )

    assert deployed_resolver_trusted_roots(runtime_root=runtime_root) == (
        trusted_root.resolve(strict=False),
    )


def test_public_trusted_root_reader_rejects_generation_symlink_escape(tmp_path):
    runtime_root = tmp_path / "runtime"
    fixed = runtime_root / "bootstrap" / "resolvers" / "v1"
    generations = runtime_root / "bootstrap" / "resolvers" / "generations"
    generation_id = "a" * 64
    outside = tmp_path / "outside-generation"
    fixed.mkdir(parents=True)
    generations.mkdir(parents=True)
    outside.mkdir()
    (outside / "launch.py").write_text("# complete\n", encoding="utf-8")
    (outside / "trusted-roots.json").write_text(
        json.dumps([str(tmp_path / "attacker-root")]), encoding="utf-8"
    )
    try:
        (generations / generation_id).symlink_to(outside, target_is_directory=True)
    except OSError:
        # famulus-skip: category=capability-unavailable; reason=escape regression requires a real directory symlink; alternate=wrong-schema and malformed-selector tests exercise the same strict public rejection boundary without symlinks
        pytest.skip("directory symlinks are unavailable")
    (fixed / "active.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation_id}),
        encoding="utf-8",
    )
    shutil.copy2(
        REPO_ROOT / "src" / "officina" / "install" / "resolvers" / "launch.py",
        fixed / "launch.py",
    )
    resolver_namespace = runpy.run_path(
        str(fixed / "launch.py"), run_name="_famulus_parity_resolver"
    )
    resolver_error = resolver_namespace["ResolverError"]

    with pytest.raises(resolver_error, match="escapes its generation root"):
        resolver_namespace["_active_generation_source"]()

    with pytest.raises(ManagedRuntimeError, match="escapes its generation root"):
        deployed_resolver_trusted_roots(runtime_root=runtime_root)


@pytest.mark.parametrize(
    ("active_text", "error_fragment"),
    [
        ("{", "could not read active resolver generation"),
        (
            json.dumps({"schema_version": 2, "generation": "a" * 64}),
            "unsupported schema",
        ),
    ],
)
def test_public_trusted_root_reader_rejects_invalid_active_selector(
    tmp_path, active_text, error_fragment
):
    runtime_root = tmp_path / "runtime"
    fixed = runtime_root / "bootstrap" / "resolvers" / "v1"
    generation = (
        runtime_root / "bootstrap" / "resolvers" / "generations" / ("a" * 64)
    )
    fixed.mkdir(parents=True)
    generation.mkdir(parents=True)
    (generation / "launch.py").write_text("# complete\n", encoding="utf-8")
    (generation / "trusted-roots.json").write_text("[]", encoding="utf-8")
    (fixed / "active.json").write_text(active_text, encoding="utf-8")

    with pytest.raises(ManagedRuntimeError, match=error_fragment):
        deployed_resolver_trusted_roots(runtime_root=runtime_root)


# famulus-skip: category=platform-contract; reason=POSIX directory fsync is the durability primitive exercised here; alternate=the managed-runtime product suite and legacy-migration interruption test exercise the shared publication order on every host
@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync contract")
def test_generation_directory_publication_fsyncs_parent_after_rename(
    monkeypatch, tmp_path
):
    publisher = getattr(managed_runtime_module, "_durably_publish_generation", None)
    assert publisher is not None, "generation publication lacks a durability boundary"
    generations_dir = tmp_path / "generations"
    generations_dir.mkdir()
    temporary = generations_dir / ".candidate.tmp"
    temporary.mkdir()
    (temporary / "launch.py").write_text("complete\n", encoding="utf-8")
    generation = generations_dir / ("a" * 64)
    events: list[str] = []
    directory_descriptors: set[int] = set()
    real_open = os.open
    real_replace = os.replace
    real_fsync = os.fsync
    real_close = os.close

    def recording_open(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        if Path(path) == generations_dir:
            directory_descriptors.add(descriptor)
        return descriptor

    def recording_replace(source, destination, *args, **kwargs):
        result = real_replace(source, destination, *args, **kwargs)
        if Path(destination) == generation:
            events.append("generation-rename")
        return result

    def recording_fsync(descriptor):
        if descriptor in directory_descriptors:
            events.append("generations-directory-fsync")
        return real_fsync(descriptor)

    def recording_close(descriptor):
        try:
            return real_close(descriptor)
        finally:
            directory_descriptors.discard(descriptor)

    monkeypatch.setattr(managed_runtime_module.os, "open", recording_open)
    monkeypatch.setattr(managed_runtime_module.os, "replace", recording_replace)
    monkeypatch.setattr(managed_runtime_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(managed_runtime_module.os, "close", recording_close)

    publisher(temporary, generation, generations_dir)

    assert events == ["generation-rename", "generations-directory-fsync"]
    assert generation.is_dir()


def test_durable_generation_publication_completes_before_selector_replacement(
    monkeypatch, tmp_path
):
    publisher = getattr(managed_runtime_module, "_durably_publish_generation", None)
    assert publisher is not None, "generation publication lacks a durability boundary"
    events: list[str] = []

    def recording_publisher(temporary, generation, generations_dir):
        publisher(temporary, generation, generations_dir)
        events.append("durable-generation")

    real_atomic_replace_bytes = atomic_files.atomic_replace_bytes

    def recording_atomic_replace(path, *args, **kwargs):
        if Path(path).name == "active.json":
            events.append("selector")
        return real_atomic_replace_bytes(path, *args, **kwargs)

    monkeypatch.setattr(
        managed_runtime_module, "_durably_publish_generation", recording_publisher
    )
    monkeypatch.setattr(
        managed_runtime_module.atomic_files,
        "atomic_replace_bytes",
        recording_atomic_replace,
    )

    _deploy_resolver(
        runtime_root=tmp_path / "runtime",
        trusted_interpreter_roots=(tmp_path / "trusted-python",),
    )

    assert events == ["durable-generation", "selector"]


# famulus-skip: category=platform-contract; reason=POSIX retry durability is established by parent-directory fsync; alternate=the shared legacy-migration interruption test exercises retry-safe selector ordering on every host
@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync retry contract")
def test_retry_resyncs_visible_generation_before_selector_replacement(
    monkeypatch, tmp_path
):
    runtime_root = tmp_path / "runtime"
    generations_dir = runtime_root / "bootstrap" / "resolvers" / "generations"
    fixed_dir = runtime_root / "bootstrap" / "resolvers" / "v1"
    real_publisher = managed_runtime_module._durably_publish_generation

    def fail_after_rename(temporary, generation, parent):
        os.replace(temporary, generation)
        raise OSError("simulated parent fsync failure")

    monkeypatch.setattr(
        managed_runtime_module, "_durably_publish_generation", fail_after_rename
    )
    with pytest.raises(ManagedRuntimeError, match="simulated parent fsync failure"):
        _deploy_resolver(
            runtime_root=runtime_root,
            trusted_interpreter_roots=(tmp_path / "trusted-python",),
        )
    assert not (fixed_dir / "active.json").exists()
    assert any(path.is_dir() for path in generations_dir.iterdir())
    monkeypatch.setattr(
        managed_runtime_module, "_durably_publish_generation", real_publisher
    )

    events: list[str] = []
    directory_descriptors: set[int] = set()
    real_open = os.open
    real_fsync = os.fsync
    real_close = os.close
    real_atomic_replace_bytes = atomic_files.atomic_replace_bytes

    def recording_open(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        if Path(path) == generations_dir:
            directory_descriptors.add(descriptor)
        return descriptor

    def recording_fsync(descriptor):
        if descriptor in directory_descriptors:
            events.append("generations-directory-fsync")
        return real_fsync(descriptor)

    def recording_close(descriptor):
        try:
            return real_close(descriptor)
        finally:
            directory_descriptors.discard(descriptor)

    def recording_atomic_replace(path, *args, **kwargs):
        if Path(path).name == "active.json":
            events.append("selector")
        return real_atomic_replace_bytes(path, *args, **kwargs)

    monkeypatch.setattr(managed_runtime_module.os, "open", recording_open)
    monkeypatch.setattr(managed_runtime_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(managed_runtime_module.os, "close", recording_close)
    monkeypatch.setattr(
        managed_runtime_module.atomic_files,
        "atomic_replace_bytes",
        recording_atomic_replace,
    )

    _deploy_resolver(
        runtime_root=runtime_root,
        trusted_interpreter_roots=(tmp_path / "trusted-python",),
    )

    assert events == ["generations-directory-fsync", "selector"]


@pytest.mark.parametrize("interrupted_target", ("active.json", "launch.py"))
def test_interrupted_resolver_upgrade_preserves_working_old_bundle_and_pointer(
    monkeypatch, tmp_path, interrupted_target
):
    runtime_root = tmp_path / "runtime"
    release_dir = runtime_root / "releases" / "old-release"
    python_bin = (
        release_dir / "venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else release_dir / "venv" / "bin" / "python"
    )
    python_bin.parent.mkdir(parents=True)
    python_bin.symlink_to(Path(sys.executable).resolve())
    runtime_root.mkdir(exist_ok=True)
    (runtime_root / "current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_id": release_dir.name,
                "runtime_source": str(release_dir),
                "python_bin": str(python_bin),
            }
        ),
        encoding="utf-8",
    )
    resolver_dir = runtime_root / "bootstrap" / "resolvers" / "v1"
    resolver_dir.mkdir(parents=True)
    resolver = resolver_dir / "launch.py"
    shutil.copy2(LEGACY_RESOLVER_SOURCE, resolver)
    resolver.chmod(0o755)
    trusted_root = Path(sys.executable).resolve().parent
    legacy_trust = json.dumps([str(trusted_root)]).encode("utf-8")
    (resolver_dir / "trusted-roots.json").write_bytes(legacy_trust)
    active_path = resolver_dir / "active.json"
    assert not active_path.exists()
    old_pointer = (runtime_root / "current.json").read_bytes()
    legacy_resolver = resolver.read_bytes()
    resolver_command = (
        [sys.executable, str(resolver)] if os.name == "nt" else [str(resolver)]
    )
    before = subprocess.run(
        [*resolver_command, "-c", "print('old-bundle-works')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode == 0, before.stderr
    assert before.stdout == "old-bundle-works\n"

    real_atomic_replace_bytes = atomic_files.atomic_replace_bytes

    def interrupt_publication(path, *args, **kwargs):
        if Path(path) == resolver_dir / interrupted_target:
            raise OSError("simulated resolver publication interruption")
        return real_atomic_replace_bytes(path, *args, **kwargs)

    monkeypatch.setattr(
        "officina.install.managed_runtime.atomic_files.atomic_replace_bytes",
        interrupt_publication,
    )
    with pytest.raises(ManagedRuntimeError, match="simulated resolver publication interruption"):
        _deploy_resolver(
            runtime_root=runtime_root,
            trusted_interpreter_roots=(tmp_path / "new-python-store",),
        )

    if interrupted_target == "active.json":
        assert not active_path.exists()
    else:
        assert active_path.exists()
    assert (runtime_root / "current.json").read_bytes() == old_pointer
    assert resolver.read_bytes() == legacy_resolver
    assert (resolver_dir / "trusted-roots.json").read_bytes() == legacy_trust
    after = subprocess.run(
        [*resolver_command, "-c", "print('old-bundle-still-works')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert after.returncode == 0, after.stderr
    assert after.stdout == "old-bundle-still-works\n"


def test_repo_candidate_installs_verified_officina_wheel_before_activation(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "skills").mkdir()
    (repo_root / "src" / "officina").mkdir(parents=True)
    (repo_root / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills", "src/officina"]\n',
        encoding="utf-8",
    )
    manifest = repo_root / "runtime_dependencies.json"
    manifest.write_text('{"version": 2, "skills": {}}', encoding="utf-8")
    calls: list = []
    call_kwargs: list[dict] = []
    fake_run = fake_uv_subprocess_run(
        calls, trusted_python_dir=tmp_path / "uv-python-store"
    )

    def recording_run(cmd, **kwargs):
        call_kwargs.append(dict(kwargs))
        return fake_run(cmd, **kwargs)

    monkeypatch.setattr("subprocess.run", recording_run)
    runtime_root = tmp_path / "runtime"

    pointer = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
        repo_root=repo_root,
    )

    build_index = next(i for i, call in enumerate(calls) if call[1] == "build")
    install_indices = [i for i, call in enumerate(calls) if call[1:3] == ["pip", "install"]]
    probe_indices = [i for i, call in enumerate(calls) if len(call) > 1 and call[1] == "-I"]
    assert install_indices[0] < build_index < install_indices[1] < min(probe_indices)
    assert "--require-hashes" in calls[install_indices[0]]
    assert any(value.endswith(".whl") for value in calls[install_indices[1]])
    assert "--no-deps" in calls[install_indices[1]]
    for index in probe_indices:
        assert "PYTHONPATH" not in call_kwargs[index]["env"]
        assert call_kwargs[index]["env"]["PYTHONNOUSERSITE"] == "1"
    metadata = json.loads((pointer.runtime_source / "artifact.json").read_text())
    assert metadata["wheel"] == "famulus_officina-0.1.0-py3-none-any.whl"
    assert len(metadata["wheel_sha256"]) == 64
    assert metadata["source_revision"] == "a" * 40
    assert metadata["schema_version"] == 3
    assert len(metadata["runtime_lock"]["sha256"]) == 64
    assert metadata["runtime_lock"]["uv_version"] == PINNED_UV_VERSION
    assert metadata["python"]["version"] == PINNED_PYTHON_VERSION
    assert metadata["python"]["implementation"] == "cpython"
    assert metadata["python"]["build"] == ["main", "Aug 13 2026 00:00:00"]
    assert metadata["python"]["compiler"] == "GCC 13.3.0"
    assert pointer.repository_config == (repo_root / "officina.toml").resolve()


def test_invalid_repository_config_preserves_prior_pointer(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store"),
    )
    runtime_root = tmp_path / "runtime"
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text('{"version": 2, "skills": {}}', encoding="utf-8")
    prior = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
    )
    prior_pointer = (runtime_root / "current.json").read_bytes()
    bad_repo = tmp_path / "bad-repo"
    bad_repo.mkdir()
    (bad_repo / "officina.toml").write_text("schema_version = 999\n", encoding="utf-8")

    with pytest.raises(ManagedRuntimeError, match="repository configuration"):
        build_candidate_release(
            runtime_root=runtime_root,
            manifest_path=manifest,
            platform="linux",
            uv_bin=FAKE_UV_BIN,
            python_version="3.11",
            repo_root=bad_repo,
        )

    assert (runtime_root / "current.json").read_bytes() == prior_pointer
    assert prior.python_bin.exists()


def _launcher_context(
    tmp_path: Path, repo_root: Path, *, mode: str
) -> InstallationContext:
    home = tmp_path / "home"
    if mode == "standard":
        paths = resolve_famulus_paths(platform="linux", home=home, environ={})
        return InstallationContext(
            mode="standard",
            source_root=repo_root,
            development_root=None,
            paths=paths,
            codex_home=home / ".codex",
            claude_home=home / ".claude",
            installation_id="standard",
        )
    isolated_home = repo_root / ".famulus" / "home"
    paths = resolve_famulus_paths(
        platform="linux",
        home=isolated_home,
        environ={
            "XDG_DATA_HOME": str(isolated_home / ".local" / "share"),
            "XDG_CONFIG_HOME": str(isolated_home / ".config"),
            "XDG_STATE_HOME": str(isolated_home / ".local" / "state"),
        },
    )
    return InstallationContext(
        mode="development",
        source_root=repo_root,
        development_root=repo_root,
        paths=paths,
        codex_home=repo_root / ".famulus" / "homes" / "codex",
        claude_home=repo_root / ".famulus" / "homes" / "claude",
        installation_id="dev-0123456789abcdef0123456789abcdef",
    )


def _launcher_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "skills").mkdir(parents=True)
    (repo / "src" / "officina").mkdir(parents=True)
    (repo / "src" / "officina" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "agents").mkdir()
    (repo / "agents" / "assistant.md").write_text("original\n", encoding="utf-8")
    (repo / "profiles").mkdir()
    (repo / "profiles" / "assistant_claude_setting.json").write_text("{}\n")
    (repo / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills", "src/officina"]\n',
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n',
        encoding="utf-8",
    )
    return repo


def test_standard_candidate_copies_immutable_launcher_resources(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store"),
    )
    repo = _launcher_repo(tmp_path)
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text('{"version": 2, "skills": {}}')
    context = _launcher_context(tmp_path, repo, mode="standard")

    pointer = build_candidate_release(
        runtime_root=context.paths.runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
        repo_root=repo,
        installation_context=context,
    )
    assert pointer.launcher_resources == pointer.runtime_source / "launcher-resources"
    assert (pointer.launcher_resources / "agents" / "assistant.md").read_text() == "original\n"
    (repo / "agents" / "assistant.md").write_text("changed\n")
    assert (pointer.launcher_resources / "agents" / "assistant.md").read_text() == "original\n"


def test_development_candidate_points_to_exact_live_launcher_resources(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store"),
    )
    repo = _launcher_repo(tmp_path)
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text('{"version": 2, "skills": {}}')
    context = _launcher_context(tmp_path, repo, mode="development")

    pointer = build_candidate_release(
        runtime_root=context.paths.runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
        repo_root=repo,
        installation_context=context,
    )

    assert pointer.launcher_resources == repo.resolve()
    record = json.loads(pointer.installation_context.read_text())
    assert record["release_id"] == pointer.release_id
    assert record["installation_id"] == context.installation_id


def test_context_publication_failure_preserves_prior_pointer(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(
        "subprocess.run",
        fake_uv_subprocess_run(calls, trusted_python_dir=tmp_path / "uv-python-store"),
    )
    repo = _launcher_repo(tmp_path)
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text('{"version": 2, "skills": {}}')
    context = _launcher_context(tmp_path, repo, mode="standard")
    prior = build_candidate_release(
        runtime_root=context.paths.runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=FAKE_UV_BIN,
        python_version="3.11",
    )
    before = context.paths.current_pointer.read_bytes()
    monkeypatch.setattr(
        "officina.install.managed_runtime._publish_installation_context",
        lambda **_kwargs: (_ for _ in ()).throw(ManagedRuntimeError("interrupted")),
    )

    with pytest.raises(ManagedRuntimeError, match="interrupted"):
        build_candidate_release(
            runtime_root=context.paths.runtime_root,
            manifest_path=manifest,
            platform="linux",
            uv_bin=FAKE_UV_BIN,
            python_version="3.11",
            repo_root=repo,
            installation_context=context,
        )

    assert context.paths.current_pointer.read_bytes() == before
    assert prior.release_id == json.loads(before)["release_id"]


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover call shapes and ordering without uv installed
@pytest.mark.skipif(not PINNED_UV_AVAILABLE, reason="pinned uv is not on PATH")
def test_build_candidate_release_end_to_end_with_real_uv(tmp_path):
    """Integration test against the real uv binary (no mocking): proves the
    venv-creation + batch-install + officina-self-install + activation flow
    actually works, not just that mocked call shapes look right."""
    manifest = REAL_MANIFEST
    runtime_root = tmp_path / "runtime"

    pointer = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=Path(UV_BIN),
        python_version=PINNED_PYTHON_VERSION,
    )

    assert pointer.python_bin.exists()
    assert (runtime_root / "current.json").exists()
    site_packages_has_rich = any(
        p.name.startswith("rich") for p in (pointer.runtime_source / "venv").rglob("rich*")
    )
    assert site_packages_has_rich

    site_packages = _venv_site_packages(pointer.runtime_source / "venv", platform="linux", python_version="3.11")
    assert (site_packages / "officina" / "__init__.py").is_file()
    # Clean env (no inherited PYTHONPATH from whatever ran this test itself)
    # so this actually proves the release venv resolves `officina` from its
    # own copied site-packages, not a source tree the test runner happens to
    # have on its own PYTHONPATH.
    result = subprocess.run(
        [str(pointer.python_bin), "-c", "import officina; print(officina.__file__)"],
        capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(site_packages / "officina" / "__init__.py")


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked artifact/probe tests above cover ordering and rollback
@pytest.mark.skipif(not PINNED_UV_AVAILABLE, reason="pinned uv is not on PATH")
def test_repo_candidate_real_uv_runs_installed_dispatcher_through_stable_launcher(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    manifest = REAL_MANIFEST

    pointer = build_candidate_release(
        runtime_root=tmp_path / "runtime",
        manifest_path=manifest,
        platform="linux",
        uv_bin=Path(UV_BIN),
        python_version=PINNED_PYTHON_VERSION,
        repo_root=repo_root,
    )

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    resolver = tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py"
    result = subprocess.run(
        [str(resolver), "-m", "officina.dispatcher.cli", "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "Invoke a skill machine interface" in result.stdout
    metadata = json.loads((pointer.runtime_source / "artifact.json").read_text())
    assert len(metadata["source_revision"]) == 40
    assert set(metadata["source_revision"]) <= set("0123456789abcdef")
