"""The CI-debug machine layer must remain a transparent runner adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from officina.dispatcher.direct_runtime import _resolve_host_dispatch_metadata


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


def test_run_ci_forwards_a_persistent_context(capfd, tmp_path: Path) -> None:
    """Catch the skill adapter discarding resumable GitHub setup."""

    module = load("ci_debug_run_ci_context", "_run_ci.py")
    repo = fake_repo(tmp_path)
    context = repo / "session"
    sha = "d" * 40
    assert module.main([
        "--repo-root", str(repo), "--ref", "repair", "--expected-sha", sha,
        "--context", str(context),
    ]) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["argv"] == [
        "remote", "matrix", "--ref", "repair", "--expected-sha", sha,
        "--context", str(context), "--timeout", "7200",
    ]


def test_run_ci_context_caps_the_adapter_process_wait(monkeypatch, tmp_path: Path) -> None:
    """Catch the outer MCP call inheriting the persisted remote deadline."""

    module = load("ci_debug_run_ci_budget", "_run_ci.py")
    captured = {}

    def invoke(_root, arguments, *, timeout_seconds):
        captured["arguments"] = tuple(arguments)
        captured["timeout_seconds"] = timeout_seconds
        return 0

    monkeypatch.setattr(module, "invoke_runner", invoke)
    assert module.main([
        "--repo-root", str(tmp_path), "--ref", "repair",
        "--expected-sha", "a" * 40, "--context", str(tmp_path / "session"),
        "--timeout", "7200",
    ]) == 0
    assert captured["timeout_seconds"] == 210
    assert captured["arguments"][-2:] == ("--timeout", "7200")


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


def test_targeted_interface_forwards_a_persistent_context(capfd, tmp_path: Path) -> None:
    """Catch targeted probes falling back to one-off output directories."""

    module = load("ci_debug_run_targeted_context", "_run_targeted_tests.py")
    repo = fake_repo(tmp_path)
    context = repo / "session"
    sha = "e" * 40
    assert module.main([
        "--repo-root", str(repo), "--ref", "repair", "--expected-sha", sha,
        "--os", "windows-latest", "--task", "tests:shared",
        "--selector", "tests/test_x.py::test_y", "--context", str(context),
    ]) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["argv"] == [
        "remote", "probe", "--ref", "repair", "--expected-sha", sha,
        "--os", "windows-latest", "--task", "tests:shared",
        "--context", str(context), "--timeout", "1800",
        "--selector", "tests/test_x.py::test_y",
    ]


def test_targeted_interface_preserves_every_exact_selector(capfd, tmp_path: Path) -> None:
    """Catch the public skill adapter collapsing a minimal selector set."""

    module = load("ci_debug_run_targeted_selectors", "_run_targeted_tests.py")
    repo = fake_repo(tmp_path)
    output = repo / "out"
    sha = "f" * 40
    assert module.main([
        "--repo-root", str(repo), "--ref", "repair", "--expected-sha", sha,
        "--os", "windows-latest", "--task", "tests:shared",
        "--selectors-json",
        '["tests/test_x.py::test_a","tests/test_y.py::test_b"]',
        "--output-dir", str(output),
    ]) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["argv"][-4:] == [
        "--selector", "tests/test_x.py::test_a",
        "--selector", "tests/test_y.py::test_b",
    ]


def test_dispatcher_accepts_one_json_selector_set_for_the_public_route(
    tmp_path: Path,
) -> None:
    """Catch the dispatcher rejecting a minimal set containing two failures."""

    selectors = '["tests/test_x.py::test_a","tests/test_y.py::test_b"]'
    metadata = _resolve_host_dispatch_metadata(
        caller_skill="ci-debug",
        target="ci-debug._rtx.interface.run-targeted-tests",
        args=[
            "--repo-root", str(ROOT), "--ref", "repair",
            "--expected-sha", "f" * 40, "--os", "windows-latest",
            "--task", "tests:shared", "--selectors-json", selectors,
            "--context", str(tmp_path / "session"),
        ],
        repository_config=ROOT / "officina.toml",
    )

    assert metadata.command[-4:] == [
        "--selectors-json", selectors, "--context", str(tmp_path / "session")
    ]


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
