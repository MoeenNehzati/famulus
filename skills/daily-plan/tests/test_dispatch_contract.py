from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_forced_orchestrate_dispatch_pattern_is_unambiguous() -> None:
    env = os.environ.copy()
    src_root = str(REPO_ROOT / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_root if not existing_pythonpath else os.pathsep.join([src_root, existing_pythonpath])
    )
    env["AI"] = str(REPO_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "officina.dispatcher.cli",
            "--dry-run",
            "--caller-skill",
            "daily-plan",
            "daily-plan.machine.orchestrate",
            "--forced",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
