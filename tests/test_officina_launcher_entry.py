from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from officina.install.launcher_entry import _trusted_interpreter_roots, main
from officina.install.managed_runtime import build_candidate_release
from officina.install.runtime_pointer import activate_release

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
LAUNCHER_ENTRY_SOURCE = SRC_DIR / "officina" / "install" / "launcher_entry.py"
UV_BIN = shutil.which("uv")


def _resolver_argv(runtime_root: Path, *extra: str) -> list[str]:
    resolver_path = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    return [str(resolver_path), *extra]


def _deploy_managed_uv(runtime_root: Path) -> Path:
    """Copy the real system uv to ``<data_root>/tools/uv``, the exact
    location ``officina.common.famulus_paths.resolve_famulus_paths`` and
    ``launcher_entry._trusted_interpreter_roots`` both assume for the
    machine-local managed uv binary. Using the system uv's own PATH location
    instead (as opposed to this canonical path) is exactly the mismatch that
    would make ``_trusted_interpreter_roots`` derive an empty allowlist at
    launch time even though ``build_candidate_release`` activated a pointer
    using a real, valid uv-managed interpreter -- this fixture makes the
    test build the release the same way a real deployment would.
    """
    tools_dir = runtime_root.parent / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    managed_uv = tools_dir / "uv"
    shutil.copy2(UV_BIN, managed_uv)
    managed_uv.chmod(0o755)
    return managed_uv


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

    def fake_execve(path, argv, env):
        recorded["path"] = path
        recorded["argv"] = argv

    monkeypatch.setattr("os.execve", fake_execve)
    exit_code = main(_resolver_argv(runtime_root, "-c", "print(1)"))

    assert recorded["path"] == str(python_bin)
    assert recorded["argv"] == [str(python_bin), "-c", "print(1)"]
    assert exit_code == 1  # unreachable in a real exec; fake stand-in returns None -> main returns 1


def test_trusted_interpreter_roots_derives_uv_python_dir(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=str(tmp_path / "uv-python-store") + "\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    roots = _trusted_interpreter_roots(runtime_root)

    assert roots == (tmp_path / "uv-python-store",)
    assert calls[0][0] == str(runtime_root.parent / "tools" / "uv")
    assert calls[0][1:] == ["python", "dir"]


def test_trusted_interpreter_roots_returns_empty_on_uv_failure(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="uv not found")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert _trusted_interpreter_roots(tmp_path / "runtime") == ()


def test_main_rejects_untrusted_symlinked_python_bin(tmp_path):
    """Without a real uv-derived trusted root, a symlinked python_bin
    pointing outside runtime_root must be rejected, not silently launched."""
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
    # No tools/uv present, so _trusted_interpreter_roots resolves to () and
    # the symlink escape must still be rejected.
    exit_code = main(_resolver_argv(runtime_root))
    assert exit_code == 1


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover main()'s pointer-resolution and trusted-roots logic without uv installed
@pytest.mark.skipif(UV_BIN is None, reason="uv is not installed on this machine")
def test_resolver_end_to_end_execs_into_real_uv_managed_interpreter(tmp_path):
    """Full integration smoke test: build a real uv-managed release, deploy
    the actual launcher_entry.py source at its fixed resolver path, and spawn
    it as a real subprocess (mirroring exactly how a generated dispatcher
    shim invokes it via os.execv). Proves the trusted-interpreter-roots wiring
    from Task 5 actually lets this resolver accept the very pointer that
    build_candidate_release just activated, rather than rejecting it."""
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
    resolver_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LAUNCHER_ENTRY_SOURCE, resolver_path)
    resolver_path.chmod(0o755)

    # Exec the resolver exactly as a generated Unix dispatcher shim would:
    # `os.execv(RESOLVER, [RESOLVER, *args])`, i.e. run the file directly
    # (relying on its shebang), not `python3 <resolver_path>`. The resolver's
    # own interpreter (whatever `/usr/bin/env python3` finds) needs
    # `officina` importable to run this module's own top-level imports --
    # a real deployment must guarantee that (e.g. installing officina into
    # the bootstrap environment that owns this fixed resolver path); here we
    # supply it via PYTHONPATH to isolate this test to the resolver logic.
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    result = subprocess.run(
        [str(resolver_path), "-c", "import sys; print(sys.executable)"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(pointer.python_bin)


# famulus-skip: category=capability-unavailable; reason=requires a real uv binary on PATH; alternate=mocked tests above cover main()'s pointer-resolution and trusted-roots logic without uv installed
@pytest.mark.skipif(UV_BIN is None, reason="uv is not installed on this machine")
def test_generated_dispatcher_shim_reaches_the_real_release_interpreter(tmp_path, monkeypatch):
    """End-to-end through the actual generated dispatcher content: a real
    uv-managed release is built and activated, the resolver is deployed at
    its fixed path, and the exact shim content _unix_dispatcher_content
    produces is written and executed. The release venv has no `officina`
    package installed, so success here means getting all the way through
    pointer resolution and exec into the release interpreter -- the failure
    is only the expected ModuleNotFoundError for officina.dispatcher.cli,
    not a RuntimePointerError or an exec failure.
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

    resolver_path = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    resolver_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LAUNCHER_ENTRY_SOURCE, resolver_path)
    resolver_path.chmod(0o755)

    content = _unix_dispatcher_content(repo_root=tmp_path / "repo", home=home)
    dispatcher = tmp_path / "bin" / "dispatcher"
    dispatcher.parent.mkdir(parents=True, exist_ok=True)
    dispatcher.write_text(content, encoding="utf-8")
    dispatcher.chmod(0o755)

    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    result = subprocess.run([str(dispatcher)], capture_output=True, text=True, env=env)

    assert result.returncode != 0
    assert "RuntimePointerError" not in result.stderr
    assert "famulus launcher:" not in result.stderr
    assert "ModuleNotFoundError" in result.stderr
    assert "officina" in result.stderr
