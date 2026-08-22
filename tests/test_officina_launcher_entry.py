from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

import officina.install.launcher_entry as launcher_entry
import officina.install.runtime_pointer as runtime_pointer
from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.launcher_entry import ResolverError, main
from officina.install.managed_runtime import build_candidate_release, uv_python_install_dir
from officina.install.runtime_pointer import RuntimePointerError, activate_release

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = SRC_DIR.parent
RESOLVER_SOURCE = SRC_DIR / "officina" / "install" / "resolvers" / "launch.py"
RESOLVER_SUPPORT_SOURCES = ()
REAL_MANIFEST = Path(__file__).resolve().parents[1] / "references" / "blueprint" / "runtime_dependencies.json"
RUNTIME_LOCK_INPUT = REPO_ROOT / "references" / "runtime" / "requirements-core.in"
RUNTIME_LOCK = REPO_ROOT / "references" / "runtime" / "requirements-core.lock"
PINNED_UV_VERSION = "0.11.29"
PINNED_PYTHON_VERSION = "3.11.15"
UV_BIN = shutil.which("uv")


def _pinned_uv_is_available() -> bool:
    if UV_BIN is None:
        return False
    result = subprocess.run(
        [UV_BIN, "--version"], capture_output=True, text=True, encoding="utf-8", errors="strict"
    )
    return result.returncode == 0 and result.stdout.split()[:2] == ["uv", PINNED_UV_VERSION]


PINNED_UV_AVAILABLE = _pinned_uv_is_available()


def _resolver_argv(runtime_root: Path, *extra: str) -> list[str]:
    resolver_path = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    return [str(resolver_path), *extra]


def _manifest_without_heavy_ml_deps(tmp_path: Path) -> Path:
    """Write a copy of the real dependency manifest with `marker-pdf`
    (which pulls in `transformers` and a large model-weight download chain)
    dropped, keeping every other real declared dependency.

    Tests that exec into the real officina package need its actual
    transitive imports (e.g. jsonschema, PyYAML -- see officina/common's
    schema/blueprint modules) satisfied, so an empty stub manifest isn't
    enough; but installing the real manifest verbatim risks filling local
    disk on a constrained dev machine and is slow. This keeps the real
    dependency set (so it stays correct if officina's own imports change)
    while excluding the one entry that's disproportionately heavy.
    """
    data = json.loads(REAL_MANIFEST.read_text())
    for skill in data.get("skills", {}).values():
        for interface in skill.get("interfaces", {}).values():
            interface["dependencies"] = [
                dep for dep in interface.get("dependencies", [])
                if not (dep.get("kind") == "python-package" and dep.get("name") == "marker-pdf")
            ]
    manifest_path = tmp_path / "runtime_dependencies.no-marker-pdf.json"
    manifest_path.write_text(json.dumps(data))
    return manifest_path


def _deploy_managed_uv(runtime_root: Path) -> Path:
    """Copy the real system uv to ``<data_root>/tools/uv``, the exact
    location ``officina.common.famulus_paths.resolve_famulus_paths`` assumes
    for the machine-local managed uv binary, so ``build_candidate_release``
    builds the release the same way a real deployment would.
    """
    tools_dir = runtime_root.parent / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    managed_uv = tools_dir / "uv"
    shutil.copy2(UV_BIN, managed_uv)
    managed_uv.chmod(0o755)
    return managed_uv


def _deploy_resolver(runtime_root: Path, *, trusted_roots: tuple[Path, ...] = ()) -> Path:
    """Copy the real, dependency-free resolvers/launch.py source to its
    fixed deployment path, and (if given) write the trusted-roots.json
    sidecar a real deployment would populate at release-activation time from
    managed_runtime._uv_python_install_dir()."""
    resolver_path = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    resolver_path.parent.mkdir(parents=True, exist_ok=True)
    for source in (RESOLVER_SOURCE, *RESOLVER_SUPPORT_SOURCES):
        shutil.copy2(source, resolver_path.parent / source.name)
    resolver_path.chmod(0o755)
    if trusted_roots:
        trust_file = resolver_path.parent / "trusted-roots.json"
        trust_file.write_text(json.dumps([str(root) for root in trusted_roots]))
    return resolver_path


# ── main()/pointer-resolution behavior ───────────────────────────────────────


def test_main_rejects_missing_current_json(tmp_path, capsys):
    runtime_root = tmp_path / "runtime"
    exit_code = main(_resolver_argv(runtime_root))
    assert exit_code == 1
    assert "famulus launcher" in capsys.readouterr().err


@pytest.mark.parametrize("payload", ["{", "[]", "null"])
def test_main_rejects_malformed_current_json_without_traceback(
    tmp_path, capsys, payload
):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "current.json").write_text(payload)

    exit_code = main(_resolver_argv(runtime_root))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("famulus launcher: ")
    assert "Traceback" not in captured.err


def test_main_execs_into_pointer_python_bin(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    release_dir = runtime_root / "releases" / "good-release"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"
    python_bin.write_text("#!/bin/sh\n")
    activate_release(runtime_root=runtime_root, release_dir=release_dir, python_bin=python_bin)

    recorded = {}

    def fake_execv(path, argv):
        recorded["path"] = path
        recorded["argv"] = argv

    monkeypatch.setattr("os.execv", fake_execv)
    exit_code = main(_resolver_argv(runtime_root, "-c", "print(1)"))

    assert recorded["path"] == str(python_bin)
    assert recorded["argv"] == [str(python_bin), "-c", "print(1)"]
    assert exit_code == 1  # unreachable in a real exec; fake stand-in returns None -> main returns 1


def test_main_preserves_validated_venv_python_symlink_path(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    release_dir = runtime_root / "releases" / "good-release"
    real_python = release_dir / "interpreter" / "python3"
    real_python.parent.mkdir(parents=True)
    real_python.write_text("#!/bin/sh\n")
    python_bin = release_dir / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.symlink_to(real_python)
    activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
    )
    recorded = {}

    def fake_execv(path, argv):
        recorded["path"] = path
        recorded["argv"] = argv

    monkeypatch.setattr("os.execv", fake_execv)
    main(_resolver_argv(runtime_root, "-c", "print(1)"))

    assert recorded["path"] == str(python_bin)
    assert recorded["argv"][0] == str(python_bin)


def test_production_dispatcher_uses_pointer_config_without_legacy_roots_or_cwd(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "runtime"
    release_dir = runtime_root / "releases" / "good-release"
    python_bin = release_dir / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\n")
    repository = tmp_path / "repository"
    (repository / "skills").mkdir(parents=True)
    config = repository / "officina.toml"
    config.write_text('schema_version = 1\n[modules]\nroots = ["skills"]\n')
    activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
        repository_config=config,
    )
    recorded = {}
    unrelated = tmp_path / "unrelated cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    monkeypatch.delenv("AI", raising=False)
    monkeypatch.delenv("FAMULUS_REPO_ROOT", raising=False)

    def fake_execv(path, argv):
        recorded["path"] = path
        recorded["argv"] = argv

    monkeypatch.setattr("os.execv", fake_execv)
    main(_resolver_argv(runtime_root, "-m", "officina.dispatcher.cli", "--dry-run"))

    assert recorded["argv"] == [
        str(python_bin),
        "-m",
        "officina.dispatcher.cli",
        "--repository-config",
        str(config),
        "--dry-run",
    ]


def _write_schema3_pointer(runtime_root: Path) -> Path:
    release_dir = runtime_root / "releases" / "release-3"
    python_bin = release_dir / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    repository = runtime_root.parent / "repository"
    (repository / "skills").mkdir(parents=True)
    repository_config = repository / "officina.toml"
    repository_config.write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n', encoding="utf-8"
    )
    launcher_resources = release_dir / "launcher-resources"
    launcher_resources.mkdir()
    context = release_dir / "installation-context.json"
    context.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_id": release_dir.name,
                "mode": "standard",
                "installation_id": "standard",
                "source_root": str(repository),
                "development_root": None,
                "codex_home": str(runtime_root.parent / "home" / ".codex"),
                "claude_home": str(runtime_root.parent / "home" / ".claude"),
            }
        ),
        encoding="utf-8",
    )
    activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
        repository_config=repository_config,
        launcher_resources=launcher_resources,
        installation_context=context,
    )
    return python_bin


def test_main_injects_validated_runtime_root_for_managed_agent(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    python_bin = _write_schema3_pointer(runtime_root)
    recorded = {}

    def fake_execv(path, argv):
        recorded["path"] = path
        recorded["argv"] = argv

    monkeypatch.setattr("os.execv", fake_execv)
    main(
        _resolver_argv(
            runtime_root,
            "-m",
            "officina.launchers.agent",
            "--agent",
            "assistant",
        )
    )

    assert recorded["path"] == str(python_bin)
    assert recorded["argv"] == [
        str(python_bin),
        "-m",
        "officina.launchers.agent",
        "--runtime-root",
        str(runtime_root.resolve()),
        "--agent",
        "assistant",
    ]


@pytest.mark.parametrize(
    "module,arguments",
    [
        ("officina.recurring.control", ["status", "--descriptor", "/tmp/schedule.json"]),
        ("officina.recurring.executor", ["--descriptor", "/tmp/schedule.json", "--job", "demo", "--log-root", "/tmp/logs"]),
        ("officina.recurring.healthcheck", ["--descriptor", "/tmp/schedule.json", "--log-root", "/tmp/logs"]),
    ],
)
def test_main_injects_runtime_root_for_managed_recurring_modules(
    tmp_path, monkeypatch, module, arguments
):
    runtime_root = tmp_path / "runtime"
    python_bin = _write_schema3_pointer(runtime_root)
    recorded = {}

    def fake_execv(path, argv):
        recorded["path"] = path
        recorded["argv"] = argv

    monkeypatch.setattr("os.execv", fake_execv)
    main(_resolver_argv(runtime_root, "-m", module, *arguments))

    assert recorded["path"] == str(python_bin)
    assert recorded["argv"] == [
        str(python_bin),
        "-m",
        module,
        "--runtime-root",
        str(runtime_root.resolve()),
        *arguments,
    ]


@pytest.mark.parametrize(
    "module",
    [
        "officina.recurring.control",
        "officina.recurring.executor",
        "officina.recurring.healthcheck",
    ],
)
def test_main_rejects_runtime_root_override_for_managed_recurring_modules(
    tmp_path, capsys, module
):
    runtime_root = tmp_path / "runtime"
    _write_schema3_pointer(runtime_root)

    exit_code = main(
        _resolver_argv(runtime_root, "-m", module, "--runtime-root", "/attacker")
    )

    assert exit_code == 1
    assert "runtime_root cannot be overridden" in capsys.readouterr().err


@pytest.mark.parametrize(
    "override", [["--runtime-root", "/tmp/evil"], ["--runtime-root=/tmp/evil"]]
)
def test_main_rejects_managed_module_runtime_root_override(
    tmp_path, monkeypatch, capsys, override
):
    runtime_root = tmp_path / "runtime"
    _write_schema3_pointer(runtime_root)
    monkeypatch.setattr(
        "os.execv", lambda *_args: pytest.fail("resolver must reject before exec")
    )

    exit_code = main(
        _resolver_argv(
            runtime_root,
            "-m",
            "officina.launchers.agent",
            *override,
        )
    )

    assert exit_code == 1
    assert "runtime_root cannot be overridden" in capsys.readouterr().err


# famulus-skip: category=platform-contract; reason=the deployed stable launcher is a POSIX executable; alternate=Windows launcher contract tests cover native batch launchers
@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher execution")
def test_deployed_stable_launcher_runs_an_installed_dispatcher_without_pythonpath(
    tmp_path,
):
    sys.path.insert(
        0,
        str(REPO_ROOT / "skills" / "install-assistant-tools" / "_rtx"),
    )
    from _install_launcher._linux_launcher import _unix_dispatcher_content

    home = tmp_path / "home"
    runtime_root = resolve_famulus_paths(
        platform=sys.platform,
        home=home,
        environ={},
    ).runtime_root
    release_dir = runtime_root / "releases" / "installed"
    environment = release_dir / "venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
    python_bin = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    purelib_result = subprocess.run(
        [
            str(python_bin),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    installed_package = Path(purelib_result.stdout.strip()) / "officina"
    shutil.copytree(SRC_DIR / "officina", installed_package)
    config = REPO_ROOT / "officina.toml"
    activate_release(
        runtime_root=runtime_root,
        release_dir=release_dir,
        python_bin=python_bin,
        repository_config=config,
    )
    _deploy_resolver(runtime_root)
    dispatcher = tmp_path / "bin" / "dispatcher"
    dispatcher.parent.mkdir()
    dispatcher.write_text(_unix_dispatcher_content(repo_root=REPO_ROOT, home=home))
    dispatcher.chmod(0o755)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [
            str(dispatcher),
            "--caller-skill",
            "daily-plan",
            "--dry-run",
            "daily-plan._rtx.interface.render-plan",
            "--route-smoke",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["caller_module_id"] == "daily-plan"
    assert payload["target_module_id"] == "daily-plan._rtx"
    assert payload["command"] == ["--route-smoke"]
    assert completed.stderr == ""

    executed = subprocess.run(
        [
            str(dispatcher),
            "--caller-skill",
            "daily-plan",
            "daily-plan._rtx.interface.render-plan",
            "--route-smoke",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert executed.returncode == 0, executed.stderr
    assert executed.stdout == "route-smoke ok\n"
    assert "certification-status-unavailable" in executed.stderr


def test_trusted_interpreter_roots_reads_sidecar_file(tmp_path, monkeypatch):
    """The dependency-free resolver must not shell out to uv or import
    anything: trust comes from a plain JSON sidecar file placed next to the
    deployed resolver at release-activation time."""
    monkeypatch.setitem(
        launcher_entry._trusted_interpreter_roots.__globals__,
        "__file__",
        str(tmp_path / "resolvers" / "launch.py"),
    )
    resolver_dir = tmp_path / "resolvers"
    resolver_dir.mkdir()
    trust_file = resolver_dir / "trusted-roots.json"
    trust_file.write_text(json.dumps([str(tmp_path / "uv-python-store")]))

    assert launcher_entry._trusted_interpreter_roots() == (tmp_path / "uv-python-store",)


def test_trusted_interpreter_roots_returns_empty_when_sidecar_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(
        launcher_entry._trusted_interpreter_roots.__globals__,
        "__file__",
        str(tmp_path / "resolvers" / "launch.py"),
    )
    assert launcher_entry._trusted_interpreter_roots() == ()


def test_main_rejects_untrusted_symlinked_python_bin(tmp_path):
    """Without a trusted-roots.json sidecar, a symlinked python_bin pointing
    outside runtime_root must be rejected, not silently launched."""
    runtime_root = tmp_path / "runtime"
    release_dir = runtime_root / "releases" / "evil-release"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    python_bin = release_dir / "venv" / "bin" / "python"
    attacker_binary = tmp_path / "attacker-controlled-binary"
    attacker_binary.write_text("#!/bin/sh\necho pwned\n")
    python_bin.symlink_to(attacker_binary)
    (runtime_root / "current.json").write_text(json.dumps({
        "schema_version": 1,
        "release_id": "evil-release",
        "runtime_source": str(release_dir),
        "python_bin": str(python_bin),
    }))
    # No trusted-roots.json sidecar next to the dev-tree resolver source, so
    # _trusted_interpreter_roots() resolves to () and the symlink escape must
    # still be rejected.
    exit_code = main(_resolver_argv(runtime_root))
    assert exit_code == 1


def test_launcher_entry_never_imports_officina():
    """Guard against a regression back to the original bug: the resolver
    module actually deployed (resolvers/launch.py) must not import
    `officina` or anything beyond the standard library, since it runs under
    the user's ambient Python before any interpreter handoff happens."""
    import ast

    stdlib_or_bundle = {
        "__future__",
        "json",
        "os",
        "pathlib",
        "sys",
    }
    for source in (RESOLVER_SOURCE, *RESOLVER_SUPPORT_SOURCES):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".")[0]
                    assert top_level in stdlib_or_bundle, f"non-bundle import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                assert node.module is not None
                top_level = node.module.split(".")[0]
                assert top_level in stdlib_or_bundle, f"non-bundle import: {node.module}"


# ── Task 6c: cross-check the duplicated containment logic against the real
#    officina.install.runtime_pointer implementation ─────────────────────────


def _vector_contained_real_path(tmp_path: Path):
    root = tmp_path / "runtime"
    release = root / "releases" / "good"
    (release / "venv" / "bin").mkdir(parents=True)
    python_bin = release / "venv" / "bin" / "python"
    python_bin.write_text("#!/bin/sh\n")
    return python_bin, root, (), True


def _vector_symlink_to_trusted_root(tmp_path: Path):
    root = tmp_path / "runtime"
    release = root / "releases" / "good"
    (release / "venv" / "bin").mkdir(parents=True)
    python_bin = release / "venv" / "bin" / "python"
    trusted_store = tmp_path / "uv-python-store" / "cpython-3.11" / "bin"
    trusted_store.mkdir(parents=True)
    real_interpreter = trusted_store / "python3.11"
    real_interpreter.write_text("#!/bin/sh\n")
    python_bin.symlink_to(real_interpreter)
    return python_bin, root, (tmp_path / "uv-python-store",), True


def _vector_symlink_to_untrusted_target(tmp_path: Path):
    root = tmp_path / "runtime"
    release = root / "releases" / "evil"
    (release / "venv" / "bin").mkdir(parents=True)
    python_bin = release / "venv" / "bin" / "python"
    attacker_binary = tmp_path / "attacker-controlled-binary"
    attacker_binary.write_text("#!/bin/sh\n")
    python_bin.symlink_to(attacker_binary)
    unrelated_trusted = tmp_path / "unrelated-trusted-store"
    unrelated_trusted.mkdir()
    return python_bin, root, (unrelated_trusted,), False


def _vector_symlink_chain_to_untrusted_target(tmp_path: Path):
    root = tmp_path / "runtime"
    release = root / "releases" / "evil"
    (release / "venv" / "bin").mkdir(parents=True)
    python_bin = release / "venv" / "bin" / "python"
    intermediate_link = tmp_path / "intermediate-link"
    final_attacker_target = tmp_path / "final-attacker-target"
    final_attacker_target.write_text("#!/bin/sh\n")
    intermediate_link.symlink_to(final_attacker_target)
    python_bin.symlink_to(intermediate_link)
    return python_bin, root, (), False


def _vector_dangling_symlink(tmp_path: Path):
    root = tmp_path / "runtime"
    release = root / "releases" / "dangling"
    (release / "venv" / "bin").mkdir(parents=True)
    python_bin = release / "venv" / "bin" / "python"
    python_bin.symlink_to(tmp_path / "never-created-target")
    return python_bin, root, (), False


def _vector_parent_directory_symlink_escape(tmp_path: Path):
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    outside_release = tmp_path / "outside-release"
    (outside_release / "venv" / "bin").mkdir(parents=True)
    (outside_release / "venv" / "bin" / "python").write_text("#!/bin/sh\n")
    escape_link = root / "releases" / "escaped"
    escape_link.symlink_to(outside_release)
    python_bin = escape_link / "venv" / "bin" / "python"
    return python_bin, root, (), False


def _vector_non_absolute_path(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    return Path("relative") / "python", root, (), False


CROSS_CHECK_VECTORS = [
    ("contained real path", _vector_contained_real_path),
    ("symlink to trusted root", _vector_symlink_to_trusted_root),
    ("symlink to untrusted target", _vector_symlink_to_untrusted_target),
    ("symlink chain to untrusted target", _vector_symlink_chain_to_untrusted_target),
    ("dangling symlink", _vector_dangling_symlink),
    ("parent-directory symlink escape", _vector_parent_directory_symlink_escape),
    ("non-absolute path", _vector_non_absolute_path),
]


@pytest.mark.parametrize("name,build_vector", CROSS_CHECK_VECTORS, ids=[v[0] for v in CROSS_CHECK_VECTORS])
def test_resolver_containment_check_matches_real_runtime_pointer_implementation(
    name, build_vector, tmp_path
):
    """officina.install.resolvers.launch duplicates a minimal, read-only
    containment check so it never needs to import officina.install.
    runtime_pointer (see that module's docstring for why). This test runs a
    shared table of adversarial vectors -- the same categories of attack
    Task 4/5's security review established -- through BOTH implementations
    and asserts they agree, so the duplication can't silently drift.
    """
    path, root, trusted_roots, expect_accept = build_vector(tmp_path)

    real_error = None
    real_result = None
    try:
        real_result = runtime_pointer._require_contained_or_trusted(
            path, root=root, trusted_roots=trusted_roots, label="python_bin"
        )
    except RuntimePointerError as exc:
        real_error = str(exc)

    resolver_error = None
    resolver_result = None
    try:
        resolver_result = launcher_entry._require_contained_or_trusted(
            path, root=root, trusted_roots=trusted_roots, label="python_bin"
        )
    except ResolverError as exc:
        resolver_error = str(exc)

    assert (real_error is None) == expect_accept, f"{name}: real impl accept/reject mismatch with expectation"
    assert (resolver_error is None) == expect_accept, f"{name}: resolver impl accept/reject mismatch with expectation"
    assert (real_error is None) == (resolver_error is None), (
        f"{name}: real and resolver implementations disagree on accept/reject "
        f"(real_error={real_error!r}, resolver_error={resolver_error!r})"
    )
    if expect_accept:
        # Both implementations now return the same shape on acceptance: the
        # validated *entry* path (root-normalized, not leaf-resolved), never
        # the resolved leaf -- execing (or storing a pointer to) the resolved
        # base-interpreter target would bypass the venv's own pyvenv.cfg and
        # site-packages entirely. Trust validation still fully resolves the
        # symlink chain internally; only the returned/exec'd path differs.
        assert real_result is not None
        assert resolver_result == real_result
        assert resolver_result == path


# ── Real end-to-end smoke tests ──────────────────────────────────────────────

# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover main()'s pointer-resolution and trusted-roots logic without uv installed
@pytest.mark.skipif(not PINNED_UV_AVAILABLE, reason="pinned uv is not on PATH")
def test_resolver_end_to_end_execs_into_real_uv_managed_interpreter_with_clean_env(tmp_path):
    """Full integration smoke test, run with a CLEAN environment (no
    PYTHONPATH injection): build a real uv-managed release, deploy the
    actual dependency-free resolvers/launch.py source at its fixed resolver
    path plus the trusted-roots.json sidecar a real deployment would write,
    and spawn it as a real subprocess exactly as a generated dispatcher shim
    would (`os.execv` on the file directly, relying on its shebang). Because
    the resolver has zero non-stdlib imports, it needs no PYTHONPATH help to
    run under the ambient system Python -- that is the whole point of the
    corrected design, and this test's clean env is the proof.
    """
    runtime_root = tmp_path / "runtime"
    manifest = REAL_MANIFEST

    managed_uv = _deploy_managed_uv(runtime_root)
    pointer = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        lock_input_path=RUNTIME_LOCK_INPUT,
        lock_path=RUNTIME_LOCK,
        platform="linux",
        uv_bin=managed_uv,
        uv_version=PINNED_UV_VERSION,
        python_version=PINNED_PYTHON_VERSION,
    )

    trusted_roots = (uv_python_install_dir(managed_uv),)
    _deploy_resolver(runtime_root, trusted_roots=trusted_roots)
    resolver_path = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"

    # Deliberately no PYTHONPATH (or any other officina-specific env var) --
    # a genuinely clean subprocess environment.
    result = subprocess.run(
        [str(resolver_path), "-c", "import sys; print(sys.executable)"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    # The resolver execs into the venv's own entry path (its bin/python
    # symlink), not the fully resolved base-interpreter target -- resolving
    # it would bypass venv/pyvenv.cfg discovery and silently lose the venv's
    # site-packages. sys.executable in the launched process reports that
    # unresolved entry path.
    assert result.stdout.strip() == str(pointer.python_bin)


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover main()'s pointer-resolution and trusted-roots logic without uv installed
@pytest.mark.skipif(not PINNED_UV_AVAILABLE, reason="pinned uv is not on PATH")
def test_build_candidate_release_auto_deploys_resolver_with_clean_env(tmp_path):
    """Task 7 (Step 3b) closes the Task 6 gap: build_candidate_release itself
    must deploy the resolver and its trust sidecar as part of activation, not
    leave that to a separate manual deployment step. Unlike
    test_resolver_end_to_end_execs_into_real_uv_managed_interpreter_with_clean_env
    above, this test deliberately does NOT call _deploy_resolver -- proving
    the deployment happens automatically -- and invokes the resolver
    directly rather than the full dispatcher shim (covered separately by
    test_generated_dispatcher_shim_reaches_the_real_release_interpreter_with_clean_env
    below) with a clean environment.
    """
    runtime_root = tmp_path / "runtime"
    manifest = REAL_MANIFEST

    managed_uv = _deploy_managed_uv(runtime_root)
    pointer = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        lock_input_path=RUNTIME_LOCK_INPUT,
        lock_path=RUNTIME_LOCK,
        platform="linux",
        uv_bin=managed_uv,
        uv_version=PINNED_UV_VERSION,
        python_version=PINNED_PYTHON_VERSION,
    )

    resolver_path = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    trust_file = resolver_path.parent / "trusted-roots.json"
    assert resolver_path.is_file(), "build_candidate_release did not deploy the resolver"
    assert trust_file.is_file(), "build_candidate_release did not deploy the trust sidecar"
    trusted_entries = json.loads(trust_file.read_text())
    assert trusted_entries == [str(uv_python_install_dir(managed_uv))]

    # Deliberately no PYTHONPATH (or any other officina-specific env var) --
    # a genuinely clean subprocess environment, exactly as a generated
    # dispatcher shim's exec into this resolver would see.
    result = subprocess.run(
        [str(resolver_path), "-c", "import sys; print(sys.executable)"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert result.stdout.strip() == str(pointer.python_bin)


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover main()'s pointer-resolution and trusted-roots logic without uv installed
@pytest.mark.skipif(not PINNED_UV_AVAILABLE, reason="pinned uv is not on PATH")
def test_generated_dispatcher_shim_reaches_the_real_release_interpreter_with_clean_env(tmp_path, monkeypatch):
    """End-to-end through the actual generated dispatcher content, also with
    a clean environment: a real uv-managed release is built and activated
    (build_candidate_release deploys the dependency-free resolver and its
    trust sidecar at its fixed path as part of that activation -- see
    test_build_candidate_release_auto_deploys_resolver_with_clean_env above,
    and installs `officina` itself into the release venv -- see
    managed_runtime._install_officina_self), and the exact shim content
    _unix_dispatcher_content produces is written and executed with no argv,
    no PYTHONPATH, and no other officina-specific env var. Success here means
    getting all the way through pointer resolution, exec into the release
    interpreter, and into officina.dispatcher.cli's own argument parsing --
    which then fails on missing required arguments (this test passes none),
    not on failing to find the module.
    """
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parents[1] / "skills" / "install-assistant-tools" / "_rtx"),
    )
    from _install_launcher._linux_launcher import _unix_dispatcher_content

    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)

    home = tmp_path / "home"
    runtime_root = home / ".local" / "share" / "famulus" / "runtime"
    # Uses the real dependency manifest, minus marker-pdf (see
    # _manifest_without_heavy_ml_deps): officina's own code is now loaded
    # into the release venv and imports third-party packages (e.g.
    # jsonschema) at module load, same as it would in a real deployment --
    # a stub manifest with no declared packages would fail with a spurious
    # ModuleNotFoundError unrelated to what this test checks.
    managed_uv = _deploy_managed_uv(runtime_root)
    build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=_manifest_without_heavy_ml_deps(tmp_path),
        lock_input_path=RUNTIME_LOCK_INPUT,
        lock_path=RUNTIME_LOCK,
        platform="linux",
        uv_bin=managed_uv,
        uv_version=PINNED_UV_VERSION,
        python_version=PINNED_PYTHON_VERSION,
    )
    # No manual _deploy_resolver call: build_candidate_release above already
    # deployed the resolver and its trusted-roots.json sidecar as part of
    # activation (Task 7's Step 3b).

    content = _unix_dispatcher_content(repo_root=tmp_path / "repo", home=home)
    dispatcher = tmp_path / "bin" / "dispatcher"
    dispatcher.parent.mkdir(parents=True, exist_ok=True)
    dispatcher.write_text(content, encoding="utf-8")
    dispatcher.chmod(0o755)

    # Explicit clean env, not just "no PYTHONPATH set in this test": whatever
    # ran this test process itself may have its own PYTHONPATH (e.g. to make
    # a non-editable-installed `officina` importable for collection), and
    # subprocess.run inherits the parent env by default -- an inherited
    # PYTHONPATH pointing at src/ would make this subprocess resolve
    # `officina` from the source tree instead of proving the release venv's
    # own copy resolves it standalone, silently defeating the point of this
    # test.
    result = subprocess.run(
        [str(dispatcher)], capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")}
    )

    assert result.returncode != 0
    assert "RuntimePointerError" not in result.stderr
    assert "famulus launcher:" not in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    # Reached officina.dispatcher.cli's own argparse, which fails because
    # this test passes no arguments -- proof execution got all the way in,
    # not evidence of a bug.
    assert "--caller-skill" in result.stderr
