"""Integration contracts for the repository's single check entry point."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_support.git_repository import GitTestRepository, isolated_git_environment


REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "repo_checks.py"
IMPLEMENTATION = REPO_ROOT / "src" / "officina" / "repository" / "checks" / "runner.py"


def _load_checks():
    spec = importlib.util.spec_from_file_location("repository_checks", IMPLEMENTATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_named_suites_have_one_internal_phase_plan() -> None:
    checks = _load_checks()

    assert checks.SUITE_PHASES == {
        "validators": ("validators",),
        "tests": ("tests:shared", "tests:performance", "tests:browser"),
        "precommit": ("validators", "tests:shared"),
        "pre-push": ("validators", "tests:shared", "tests:browser"),
        "portability": ("tests:shared",),
        "full": (
            "tests:performance",
            "validators",
            "tests:shared",
            "tests:browser",
        ),
    }


def test_root_entrypoint_exposes_named_suites() -> None:
    completed = subprocess.run(
        [sys.executable, str(ENTRYPOINT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "precommit" in completed.stdout
    assert "pre-push" in completed.stdout
    assert "validators" in completed.stdout
    assert "--jobs" in completed.stdout


def test_root_entrypoint_exposes_remote_matrix_and_probe_help() -> None:
    """Catch a root launcher that cannot reach the remote debugging interface."""

    for command in (("remote", "--help"), ("remote", "matrix", "--help"), ("remote", "probe", "--help")):
        completed = subprocess.run(
            [sys.executable, str(ENTRYPOINT), *command],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert "remote" in completed.stdout.casefold() or command[1] in completed.stdout


def test_remote_entrypoint_does_not_require_pytest(tmp_path: Path) -> None:
    """Catch the lightweight remote route importing the local pytest runner."""

    (tmp_path / "pytest.py").write_text(
        "raise ImportError('pytest intentionally unavailable')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (str(tmp_path), environment.get("PYTHONPATH", ""))
        if item
    )

    completed = subprocess.run(
        [sys.executable, str(ENTRYPOINT), "remote", "--help"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "matrix" in completed.stdout
    assert "probe" in completed.stdout


def test_legacy_execution_entrypoints_are_removed() -> None:
    assert not (REPO_ROOT / "repo_tests.py").exists()
    assert not (REPO_ROOT / "validators" / "runner.py").exists()
    assert not (REPO_ROOT / "scripts" / "run-python-tests.py").exists()


def test_pre_push_hook_uses_root_checks_suite() -> None:
    hook = (REPO_ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")

    assert 'python3 "$REPO_ROOT/repo_checks.py" --suite pre-push' in hook


OID40 = "1" * 40
OID64 = "a" * 64
ZERO40 = "0" * 40
ZERO64 = "0" * 64


def _pre_push_fixture(tmp_path: Path) -> tuple[GitTestRepository, Path, dict[str, str]]:
    repository = GitTestRepository.create(tmp_path / "repository")
    hooks = repository.root / ".githooks"
    hooks.mkdir()
    shutil.copy2(REPO_ROOT / ".githooks" / "pre-push", hooks / "pre-push")
    calls = tmp_path / "calls"
    (repository.root / "repo_checks.py").write_text(
        "import os, pathlib, sys\n"
        "pathlib.Path(os.environ['HOOK_CALLS']).write_text(' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    environment = isolated_git_environment({"HOOK_CALLS": str(calls)})
    return repository, calls, environment


def _run_pre_push(
    repository: GitTestRepository,
    environment: dict[str, str],
    records: str,
    *,
    arguments: tuple[str, ...] = ("origin", "file:///tmp/remote.git"),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repository.root / ".githooks" / "pre-push"), *arguments],
        cwd=repository.root,
        env=environment,
        input=records,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )


@pytest.mark.parametrize(
    ("records", "delegates"),
    [
        (f"(delete) {ZERO40} refs/heads/tmp {OID40}\n", False),
        (f"(delete) {ZERO64} refs/heads/tmp {OID64}\n", False),
        (
            f"(delete) {ZERO40} refs/heads/one {OID40}\n"
            f"(delete) {ZERO40} refs/heads/two {OID40}\n",
            False,
        ),
        (f"refs/heads/main {OID40} refs/heads/main {OID40}\n", True),
        (
            f"(delete) {ZERO40} refs/heads/tmp {OID40}\n"
            f"refs/heads/main {OID40} refs/heads/main {OID40}\n",
            True,
        ),
        ("", True),
        (f" (delete) {ZERO40} refs/heads/tmp {OID40}\n", True),
        (f"(delete)  {ZERO40} refs/heads/tmp {OID40}\n", True),
        (f"(delete) {ZERO40} refs/heads/tmp {OID40} \n", True),
        (f"(delete)\t{ZERO40} refs/heads/tmp {OID40}\n", True),
        (f"(delete) {ZERO40} refs/heads/tmp {OID40}\r\n", True),
        ("\n", True),
        (f"(delete) {ZERO40} refs/heads/tmp {OID40} extra\n", True),
        (f"(delete) {ZERO40} refs/heads/tmp\n", True),
        (f"(delete) {ZERO40} refs/heads/tmp {OID40}", True),
        (f"(delete) {ZERO40} tmp {OID40}\n", True),
        (f"(delete) {ZERO40} refs/heads/tmp xyz\n", True),
    ],
)
def test_pre_push_hook_skips_only_well_formed_deletions(
    tmp_path: Path, records: str, delegates: bool
) -> None:
    repository, calls, environment = _pre_push_fixture(tmp_path)

    completed = _run_pre_push(repository, environment, records)

    assert completed.returncode == 0, completed.stderr
    assert calls.exists() is delegates
    if delegates:
        assert calls.read_text(encoding="utf-8") == "--suite pre-push"


def test_pre_push_hook_without_arguments_delegates_without_reading_stdin(
    tmp_path: Path,
) -> None:
    repository, calls, environment = _pre_push_fixture(tmp_path)
    process = subprocess.Popen(
        ["bash", str(repository.root / ".githooks" / "pre-push")],
        cwd=repository.root,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.wait(timeout=2) == 0
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
    assert calls.read_text(encoding="utf-8") == "--suite pre-push"


# famulus-skip: category=platform-contract; reason=Windows has no POSIX PTY API; alternate=no-argument open-stdin case verifies immediate delegation on every host
@pytest.mark.skipif(os.name == "nt", reason="PTYs are not available on Windows")
def test_pre_push_hook_with_tty_stdin_delegates(tmp_path: Path) -> None:
    import pty

    repository, calls, environment = _pre_push_fixture(tmp_path)
    master, slave = pty.openpty()
    try:
        completed = subprocess.run(
            ["bash", str(repository.root / ".githooks" / "pre-push"), "origin", "remote"],
            cwd=repository.root,
            env=environment,
            stdin=slave,
            capture_output=True,
            check=False,
            timeout=2,
        )
    finally:
        os.close(master)
        os.close(slave)
    assert completed.returncode == 0, completed.stderr
    assert calls.read_text(encoding="utf-8") == "--suite pre-push"


def test_real_git_push_delete_skips_repository_checks(tmp_path: Path) -> None:
    repository, calls, environment = _pre_push_fixture(tmp_path)
    tracked = repository.root / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    repository.git("add", "tracked.txt")
    repository.git("commit", "--quiet", "-m", "baseline")
    repository.git("branch", "tmp")
    remote = tmp_path / "remote.git"
    # famulus-raw-git: category=hooks; reason=the test needs a real bare remote for hook execution
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True, env=environment)
    repository.git("remote", "add", "origin", str(remote))
    # famulus-raw-git: category=hooks; reason=the test must seed the remote without suppressing later hook execution
    subprocess.run(
        ["git", "-C", str(repository.root), "push", "--quiet", "origin", "tmp"],
        check=True,
        env=environment,
    )
    repository.git("config", "core.hooksPath", ".githooks")

    # famulus-raw-git: category=hooks; reason=the test must exercise Git's real pre-push record stream
    completed = subprocess.run(
        ["git", "-C", str(repository.root), "push", "--delete", "origin", "tmp"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert not calls.exists()
