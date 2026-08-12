"""The CI-debug machine layer must remain a transparent runner adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[4]
RTX = ROOT / "skills" / "ci-debug" / "_rtx"


def load(name: str, filename: str):
    if str(RTX) not in sys.path:
        sys.path.insert(0, str(RTX))
    spec = importlib.util.spec_from_file_location(name, RTX / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_repo(tmp_path: Path) -> Path:
    runner = tmp_path / "repo_checks.py"
    runner.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n",
        encoding="utf-8",
    )
    return tmp_path


def test_run_ci_delegates_the_complete_matrix(capfd, tmp_path: Path) -> None:
    module = load("ci_debug_run_ci", "_run_ci.py")
    repo = fake_repo(tmp_path)
    output = repo / "out"
    sha = "a" * 40
    assert (
        module.main(
            [
                "--repo-root",
                str(repo),
                "--ref",
                "repair",
                "--expected-sha",
                sha,
                "--output-dir",
                str(output),
                "--timeout",
                "30",
            ]
        )
        == 0
    )
    payload = json.loads(capfd.readouterr().out)
    assert payload["cwd"] == str(repo)
    assert payload["argv"] == [
        "remote",
        "matrix",
        "--ref",
        "repair",
        "--expected-sha",
        sha,
        "--output-dir",
        str(output),
        "--timeout",
        "30",
    ]


@pytest.mark.parametrize(
    ("selection", "expected_tail"),
    [
        (
            ["--selector", "tests/test_x.py::test_y"],
            ["--selector", "tests/test_x.py::test_y"],
        ),
        (["--from-report", "previous.json"], ["--from-report", "previous.json"]),
        (["--whole-element"], ["--whole-element"]),
    ],
)
def test_targeted_interface_delegates_only_the_requested_element(
    capfd, tmp_path: Path, selection: list[str], expected_tail: list[str]
) -> None:
    module = load("ci_debug_run_targeted", "_run_targeted_tests.py")
    repo = fake_repo(tmp_path)
    output = repo / "out"
    sha = "b" * 40
    argv = [
        "--repo-root", str(repo), "--ref", "repair", "--expected-sha", sha,
        "--os", "windows-latest", "--task", "tests:shared", *selection,
        "--output-dir", str(output), "--timeout", "30",
    ]
    assert module.main(argv) == 0
    payload = json.loads(capfd.readouterr().out)
    expected = [
        "remote", "probe", "--ref", "repair", "--expected-sha", sha,
        "--os", "windows-latest", "--task", "tests:shared",
        "--output-dir", str(output), "--timeout", "30", *expected_tail,
    ]
    assert payload["argv"] == expected


def test_missing_runner_fails_closed(capsys, tmp_path: Path) -> None:
    module = load("ci_debug_run_ci_missing", "_run_ci.py")
    assert module.main([
        "--repo-root", str(tmp_path), "--ref", "repair",
        "--expected-sha", "c" * 40, "--output-dir", str(tmp_path / "out"),
    ]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "runner_interface_unavailable"


def test_runtime_blueprint_exports_only_the_two_thin_interfaces() -> None:
    module = yaml.safe_load((RTX / "blueprint.yaml").read_text(encoding="utf-8"))
    assert set(module["exports"]) == {
        "ci-debug._rtx.interface.run-ci",
        "ci-debug._rtx.interface.run-targeted-tests",
    }
    assert set(module["sources"]) == {
        "ci-debug._rtx.source.rtx-init",
        "ci-debug._rtx.source.rtx-run-ci",
        "ci-debug._rtx.source.rtx-run-targeted-tests",
    }
    assert "incident" not in str(module).casefold()
    assert "ledger" not in str(module).casefold()
