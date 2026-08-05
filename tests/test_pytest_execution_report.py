from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_pytest_with_report(
    tmp_path: Path, *pytest_args: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    """Run the real reporting plugin and return its single execution report."""
    report_template = tmp_path / "execution-{pid}.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["OFFICINA_PYTEST_EXECUTION_REPORT"] = str(report_template)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "officina.common.pytest_execution_report",
            *pytest_args,
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    paths = list(tmp_path.glob("execution-*.json"))
    assert len(paths) == 1, completed.stdout
    return completed, json.loads(paths[0].read_text(encoding="utf-8"))


def test_report_records_exact_collection_outcomes_reasons_and_durations(
    tmp_path: Path,
) -> None:
    """Dropping any required pytest hook must lose an asserted execution fact."""
    (tmp_path / "test_sample.py").write_text(
        """
import pytest

def test_passes():
    assert True

def test_fails():
    assert False

@pytest.mark.skip(reason="requires a rare capability")
def test_skips():
    assert False

def test_deselected():
    assert True
""".lstrip(),
        encoding="utf-8",
    )

    completed, report = run_pytest_with_report(
        tmp_path,
        "--deselect",
        "test_sample.py::test_deselected",
        "test_sample.py",
    )

    assert completed.returncode == 1
    assert report["complete"] is True
    assert report["cancelled"] is False
    collection = report["collection"]
    assert collection["finished"] is True
    assert collection["selected_nodeids"] == [
        "test_sample.py::test_passes",
        "test_sample.py::test_fails",
        "test_sample.py::test_skips",
    ]
    assert collection["deselected_nodeids"] == [
        "test_sample.py::test_deselected"
    ]
    assert collection["errors"] == []
    call_reports = {
        row["nodeid"]: row
        for row in report["test_reports"]
        if row["when"] == "call"
    }
    assert call_reports["test_sample.py::test_passes"]["outcome"] == "passed"
    assert call_reports["test_sample.py::test_fails"]["outcome"] == "failed"
    skipped = next(
        row
        for row in report["test_reports"]
        if row["nodeid"] == "test_sample.py::test_skips"
        and row["outcome"] == "skipped"
    )
    assert skipped["skip_reason"] == "Skipped: requires a rare capability"
    assert all(row["duration_seconds"] >= 0 for row in report["test_reports"])


def test_report_records_collection_errors_without_a_collect_only_pass(
    tmp_path: Path,
) -> None:
    """A collection error must remain visible while other tests still execute."""
    (tmp_path / "test_broken.py").write_text(
        "raise RuntimeError('broken during collection')\n", encoding="utf-8"
    )
    (tmp_path / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    completed, report = run_pytest_with_report(
        tmp_path,
        "--continue-on-collection-errors",
        ".",
    )

    assert completed.returncode == 1
    assert report["collection"]["selected_nodeids"] == ["test_ok.py::test_ok"]
    errors = report["collection"]["errors"]
    assert len(errors) == 1
    assert errors[0]["nodeid"] == "test_broken.py"
    assert "broken during collection" in errors[0]["longrepr"]
    assert any(
        row["nodeid"] == "test_ok.py::test_ok" and row["outcome"] == "passed"
        for row in report["test_reports"]
    )
