"""Integration contracts for the repository's single check entry point."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys


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


def test_legacy_execution_entrypoints_are_removed() -> None:
    assert not (REPO_ROOT / "repo_tests.py").exists()
    assert not (REPO_ROOT / "validators" / "runner.py").exists()
    assert not (REPO_ROOT / "scripts" / "run-python-tests.py").exists()


def test_pre_push_hook_uses_root_checks_suite() -> None:
    hook = (REPO_ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")

    assert 'python3 "$REPO_ROOT/repo_checks.py" --suite pre-push' in hook
