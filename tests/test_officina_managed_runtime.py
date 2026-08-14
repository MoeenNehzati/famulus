from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
import hashlib
from pathlib import Path

import pytest

from officina.common import atomic_files
from officina.install.managed_runtime import (
    ManagedRuntimeError,
    _source_revision,
    _venv_python_bin,
    _venv_site_packages,
    build_candidate_release as _build_candidate_release,
    declared_python_packages,
    optional_python_packages,
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


def test_declared_python_packages_matches_today_baseline():
    packages = declared_python_packages(REAL_MANIFEST, platform="linux")
    assert packages == (
        "bibtexparser",
        "cryptography>=44.0.1",
        "dateparser",
        "jsonschema>=4",
        "keyring",
        "marker-pdf",
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


def test_declared_python_packages_include_optional_false_excludes_marker_pdf():
    """marker-pdf pulls in the full torch/transformers/CUDA stack (several
    GB) for pdf-to-markdown's OCR models alone -- the "core" set (what a
    fresh install gets by default) excludes it; every other real declared
    package stays."""
    core = declared_python_packages(REAL_MANIFEST, platform="linux", include_optional=False)
    assert "marker-pdf" not in core
    assert core == (
        "bibtexparser",
        "cryptography>=44.0.1",
        "dateparser",
        "jsonschema>=4",
        "keyring",
        "PyYAML>=6",
        "rich",
    )


def test_declared_python_packages_include_optional_true_is_the_default():
    assert declared_python_packages(REAL_MANIFEST, platform="linux") == declared_python_packages(
        REAL_MANIFEST, platform="linux", include_optional=True
    )


def test_optional_python_packages_returns_just_the_deferred_set():
    assert optional_python_packages(REAL_MANIFEST, platform="linux") == ("marker-pdf",)


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


def test_build_candidate_release_include_optional_dependencies_false_excludes_marker_pdf(monkeypatch, tmp_path):
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
        include_optional_dependencies=False,
    )

    pip_call = calls[1]
    assert "marker-pdf" not in pip_call
    assert "--require-hashes" in pip_call
    assert "marker-pdf" not in Path(pip_call[-1]).read_text(encoding="utf-8")


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


def test_deploy_resolver_writes_through_atomic_replace_bytes_not_plain_copy(monkeypatch, tmp_path):
    """Regression test for a real bug: _deploy_resolver used to write the
    resolver with plain `shutil.copy2`, which is not atomic. The resolver
    at `<runtime_root>/bootstrap/resolvers/v1/launch.py` is a fixed path
    every generated launcher shim and every scheduled recurring-tasks job
    execs into, and build_candidate_release genuinely runs a second time
    against the same runtime_root during a normal install-then-update flow
    -- a non-atomic overwrite risks a torn read by a job executing the file
    at that exact moment. This asserts the real deployment goes through
    officina.common.atomic_files.atomic_replace_bytes (like its sibling
    uv_bootstrap.py/runtime_pointer.py writes) instead of shutil.copy2, and
    that the deployed file is both correct and executable.
    """
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

    assert len(atomic_calls) == 1
    resolver_path = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    args, kwargs = atomic_calls[0]
    assert args[0] == resolver_path
    assert kwargs["mode"] == 0o755
    assert resolver_path.exists()
    assert resolver_path.read_bytes() == (
        Path(__file__).resolve().parents[1]
        / "src" / "officina" / "install" / "resolvers" / "launch.py"
    ).read_bytes()
    assert os.access(resolver_path, os.X_OK)


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
    assert metadata["schema_version"] == 2
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
