from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import officina.install.launcher_entry as launcher_entry
import officina.install.runtime_pointer as runtime_pointer
from officina.install.launcher_entry import ResolverError, main
from officina.install.managed_runtime import build_candidate_release, uv_python_install_dir
from officina.install.runtime_pointer import RuntimePointerError, activate_release

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
RESOLVER_SOURCE = SRC_DIR / "officina" / "install" / "resolvers" / "launch.py"
UV_BIN = shutil.which("uv")


def _resolver_argv(runtime_root: Path, *extra: str) -> list[str]:
    resolver_path = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    return [str(resolver_path), *extra]


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
    shutil.copy2(RESOLVER_SOURCE, resolver_path)
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


def test_trusted_interpreter_roots_reads_sidecar_file(tmp_path, monkeypatch):
    """The dependency-free resolver must not shell out to uv or import
    anything: trust comes from a plain JSON sidecar file placed next to the
    deployed resolver at release-activation time."""
    monkeypatch.setattr(
        launcher_entry._resolver, "__file__", str(tmp_path / "resolvers" / "launch.py")
    )
    resolver_dir = tmp_path / "resolvers"
    resolver_dir.mkdir()
    trust_file = resolver_dir / "trusted-roots.json"
    trust_file.write_text(json.dumps([str(tmp_path / "uv-python-store")]))

    assert launcher_entry._trusted_interpreter_roots() == (tmp_path / "uv-python-store",)


def test_trusted_interpreter_roots_returns_empty_when_sidecar_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        launcher_entry._resolver, "__file__", str(tmp_path / "resolvers" / "launch.py")
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

    tree = ast.parse(RESOLVER_SOURCE.read_text(encoding="utf-8"))
    stdlib_only = {"__future__", "json", "os", "sys", "pathlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                assert top_level in stdlib_only, f"non-stdlib import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None
            top_level = node.module.split(".")[0]
            assert top_level in stdlib_only, f"non-stdlib import: {node.module}"


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
        # The two implementations deliberately return different (but each
        # internally consistent) shapes on acceptance: the real
        # implementation preserves the original (root-normalized, not
        # leaf-resolved) path so a stored pointer keeps referring to the
        # venv location, while the resolver returns the fully resolved leaf
        # so it can exec straight into the real interpreter. Cross-checking
        # accept/reject agreement (above) is what guards against drift in
        # the security-relevant containment decision; the return shapes are
        # allowed to differ.
        assert real_result is not None
        assert resolver_result == path.resolve()


# ── Real end-to-end smoke tests ──────────────────────────────────────────────

# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover main()'s pointer-resolution and trusted-roots logic without uv installed
@pytest.mark.skipif(UV_BIN is None, reason="uv is not installed on this machine")
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
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({"version": 1, "skills": {}}))

    managed_uv = _deploy_managed_uv(runtime_root)
    pointer = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=managed_uv,
        python_version="3.11",
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
    # The resolver execs into the fully resolved interpreter target (real
    # uv-store path), not the venv's symlink path, so sys.executable in the
    # launched process reports the resolved target.
    assert result.stdout.strip() == str(pointer.python_bin.resolve())


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover main()'s pointer-resolution and trusted-roots logic without uv installed
@pytest.mark.skipif(UV_BIN is None, reason="uv is not installed on this machine")
def test_build_candidate_release_auto_deploys_resolver_with_clean_env(tmp_path):
    """Task 7 (Step 3b) closes the Task 6 gap: build_candidate_release itself
    must deploy the resolver and its trust sidecar as part of activation, not
    leave that to a separate manual deployment step. Unlike
    test_resolver_end_to_end_execs_into_real_uv_managed_interpreter_with_clean_env
    above, this test deliberately does NOT call _deploy_resolver -- proving
    the deployment happens automatically -- and invokes the resolver
    directly (not the full dispatcher shim, which still hits
    ModuleNotFoundError for officina.dispatcher.cli since that package isn't
    pip-installed into the managed venv; that is a separate, deliberate
    design decision, not this test's concern) with a clean environment.
    """
    runtime_root = tmp_path / "runtime"
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({"version": 1, "skills": {}}))

    managed_uv = _deploy_managed_uv(runtime_root)
    pointer = build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=managed_uv,
        python_version="3.11",
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
    assert result.stdout.strip() == str(pointer.python_bin.resolve())


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover main()'s pointer-resolution and trusted-roots logic without uv installed
@pytest.mark.skipif(UV_BIN is None, reason="uv is not installed on this machine")
def test_generated_dispatcher_shim_reaches_the_real_release_interpreter_with_clean_env(tmp_path, monkeypatch):
    """End-to-end through the actual generated dispatcher content, also with
    a clean environment: a real uv-managed release is built and activated
    (build_candidate_release deploys the dependency-free resolver and its
    trust sidecar at its fixed path as part of that activation -- see
    test_build_candidate_release_auto_deploys_resolver_with_clean_env above),
    and the exact shim content _unix_dispatcher_content produces is written
    and executed. The release venv has no `officina` package installed (that
    wiring is a separately scoped, later task), so success here means
    getting all the way through pointer resolution and exec into the release
    interpreter with no PYTHONPATH help -- the only expected failure is
    ModuleNotFoundError for officina.dispatcher.cli itself, raised by the
    release interpreter, never a resolver-side error.
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
    manifest = tmp_path / "runtime_dependencies.json"
    manifest.write_text(json.dumps({"version": 1, "skills": {}}))

    managed_uv = _deploy_managed_uv(runtime_root)
    build_candidate_release(
        runtime_root=runtime_root,
        manifest_path=manifest,
        platform="linux",
        uv_bin=managed_uv,
        python_version="3.11",
    )
    # No manual _deploy_resolver call: build_candidate_release above already
    # deployed the resolver and its trusted-roots.json sidecar as part of
    # activation (Task 7's Step 3b).

    content = _unix_dispatcher_content(repo_root=tmp_path / "repo", home=home)
    dispatcher = tmp_path / "bin" / "dispatcher"
    dispatcher.parent.mkdir(parents=True, exist_ok=True)
    dispatcher.write_text(content, encoding="utf-8")
    dispatcher.chmod(0o755)

    # No PYTHONPATH injected.
    result = subprocess.run([str(dispatcher)], capture_output=True, text=True)

    assert result.returncode != 0
    assert "RuntimePointerError" not in result.stderr
    assert "famulus launcher:" not in result.stderr
    assert "ModuleNotFoundError" in result.stderr
    assert "officina" in result.stderr
