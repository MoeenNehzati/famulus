"""Focused tests for list-manager Python machine-interface runtimes."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

TEST_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = TEST_ROOT.parent
SKILL_ROOT = (
    RUNTIME_ROOT.parent if RUNTIME_ROOT.name == "_rtx" else RUNTIME_ROOT
)
REPO_ROOT = SKILL_ROOT.parents[1]
SRC_ROOT = REPO_ROOT / "src"
RUNNER = "officina.runtime.python_machine_interface_runner"


def run_interface(
    gateway_path: str,
    process_entry: str,
    args: list[str],
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_ROOT),
        "PYTHONIOENCODING": "utf-8:strict",
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            RUNNER,
            gateway_path,
            process_entry,
            *args,
        ],
        cwd=SKILL_ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=env,
        check=False,
    )


def test_describe_schema_interface_reports_field_spec() -> None:
    result = run_interface(
        "_rtx/_yaml_store.py",
        "DescribeSchemaInterface",
        ["todo", "state"],
    )

    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(result.stdout) == {
        "state": {"enum": ["incomplete", "inprogress", "complete"]}
    }


def test_beautify_list_interface_renders_stdin_yaml() -> None:
    yaml_in = """\
schema: todo
name: todo
categories:
- name: Work
  entries:
  - id: aaaaaa
    title: Task
    state: incomplete
    created: '2026-01-01'
    deadline: '2026-01-05'
"""

    result = run_interface(
        "_rtx/_list_beautify.py",
        "BeautifyListInterface",
        ["--markdown", "--ids"],
        stdin=yaml_in,
    )

    assert result.returncode == 0, result.stderr
    assert "- [ ] Task" in result.stdout
    assert "`#aaaaaa`" in result.stdout
