"""Runtime-boundary tests for the cloud-files drive-readiness machine interfaces.

These exercise the real dispatcher machine-interface runner
(``officina.runtime.python_machine_interface_runner``) against the declared
process entries, proving the interfaces are actually loadable and executable
through the normal Officina runtime -- not just that their underlying pure
functions behave correctly (already covered by test_drive_readiness.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = TEST_ROOT.parent
SKILL_ROOT = RUNTIME_ROOT.parent if RUNTIME_ROOT.name == "_rtx" else RUNTIME_ROOT
REPO_ROOT = SKILL_ROOT.parents[1]
SRC_ROOT = REPO_ROOT / "src"
RUNNER = "officina.runtime.python_machine_interface_runner"


def run_interface(
    gateway_path: str,
    process_entry: str,
    args: list[str],
    *,
    home: Path,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_ROOT),
        "PYTHONIOENCODING": "utf-8:strict",
        "HOME": str(home),
    }
    return subprocess.run(
        [sys.executable, "-m", RUNNER, gateway_path, process_entry, *args],
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=env,
        check=False,
    )


def test_ensure_assistant_root_interface_executes_through_declared_entry(
    tmp_path: Path,
) -> None:
    result = run_interface(
        "_rtx/_drive_readiness.py",
        "EnsureAssistantRootInterface",
        [],
        home=tmp_path,
    )

    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stderr)
    assert "error" in payload


def test_lists_exists_interface_executes_through_declared_entry(
    tmp_path: Path,
) -> None:
    result = run_interface(
        "_rtx/_drive_readiness.py",
        "ListsExistsInterface",
        ["lists/todo.yaml"],
        home=tmp_path,
    )

    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stderr)
    assert "error" in payload


def test_lists_exists_interface_rejects_non_lists_path_before_touching_config(
    tmp_path: Path,
) -> None:
    result = run_interface(
        "_rtx/_drive_readiness.py",
        "ListsExistsInterface",
        ["plans/file.md"],
        home=tmp_path,
    )

    assert result.returncode == 1
    payload = json.loads(result.stderr)
    assert "invalid path" in payload["error"]
