from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run-precommit-gate.py"
)
SPEC = importlib.util.spec_from_file_location("run_precommit_gate", MODULE_PATH)
assert SPEC is not None
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def load_report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def fake_phases(
    returncodes: dict[str, int], calls: list[list[str]]
):
    """Return a subprocess fake keyed by the phase command's script or binary."""

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        joined = " ".join(command)
        returncode = next(
            (
                code
                for marker, code in returncodes.items()
                if marker in joined
            ),
            0,
        )
        return SimpleNamespace(returncode=returncode)

    return run


def configured_repo(tmp_path: Path) -> Path:
    """Create all optional generator paths so the complete phase list applies."""
    (tmp_path / ".git").write_text("gitdir: synthetic\n", encoding="utf-8")
    for relative in (
        "scripts/generate-settings-table.sh",
        "scripts/generate-doc-artifacts.py",
        "scripts/generate-previews.py",
        "validators/runner.py",
        "scripts/run-python-tests.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return tmp_path


def test_phase_subprocess_does_not_inherit_outer_git_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nested repository command must not inherit the committing Git context."""
    routed_names = (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    )
    for name in routed_names:
        monkeypatch.setenv(name, f"/outer/{name.lower()}")
    monkeypatch.setenv("PRECOMMIT_SENTINEL", "preserved")
    captured: dict[str, object] = {}

    def run(_command: list[str], **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", run)

    result = gate._run_phase(tmp_path, "nested", ["python3", "nested.py"])

    assert result.returncode == 0
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["PRECOMMIT_SENTINEL"] == "preserved"
    assert all(name not in environment for name in routed_names)
    assert os.environ["GIT_DIR"] == "/outer/git_dir"


def test_missing_repository_marks_report_incomplete(tmp_path: Path) -> None:
    """A missing repository is an infrastructure stop, not an ordinary phase."""
    report = tmp_path / "precommit-gate.json"

    with pytest.raises(RuntimeError, match="repository root does not exist"):
        gate.run_precommit_gate(tmp_path / "missing", report)

    assert load_report(report)["complete"] is False


def test_gate_records_every_ordinary_phase_before_returning_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """Generator, scan, and validator failures must not make tests unreachable."""
    repo_root = configured_repo(tmp_path)
    calls: list[list[str]] = []
    report = tmp_path / "precommit-gate.json"
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        fake_phases(
            {
                "generate-settings-table.sh": 1,
                "generate-doc-artifacts.py": 0,
                "generate-previews.py": 0,
                "gitleaks": 1,
                "validators/runner.py": 1,
                "run-python-tests.py": 0,
            },
            calls,
        ),
    )

    assert gate.run_precommit_gate(repo_root, report) == 1
    phase_ids = [row["phase_id"] for row in load_report(report)["phases"]]
    assert phase_ids == [
        "settings-generation",
        "documentation-generation",
        "preview-generation",
        "gitleaks",
        "validators",
        "python-tests",
    ]
    assert sum("run-python-tests.py" in " ".join(call) for call in calls) == 1
    assert any(
        "run-python-tests.py --suite precommit --keep-going --report" in " ".join(call)
        for call in calls
    )


def test_gate_marks_missing_optional_generator_not_configured(
    tmp_path: Path, monkeypatch
) -> None:
    """An absent optional generator is visible without claiming it ran."""
    repo_root = configured_repo(tmp_path)
    (repo_root / "scripts/generate-previews.py").unlink()
    calls: list[list[str]] = []
    report = tmp_path / "precommit-gate.json"
    monkeypatch.setattr(
        gate.subprocess, "run", fake_phases({}, calls)
    )

    assert gate.run_precommit_gate(repo_root, report) == 0
    preview = next(
        row
        for row in load_report(report)["phases"]
        if row["phase_id"] == "preview-generation"
    )
    assert preview["status"] == "not-configured"
    assert all("generate-previews.py" not in " ".join(call) for call in calls)


def test_missing_gitleaks_is_an_ordinary_failure_and_tests_still_run(
    tmp_path: Path, monkeypatch
) -> None:
    """A missing scanner blocks the commit but does not make tests unreachable."""
    repo_root = configured_repo(tmp_path)
    calls: list[list[str]] = []
    report = tmp_path / "precommit-gate.json"

    def missing_gitleaks(
        command: list[str], **_kwargs: object
    ) -> SimpleNamespace:
        calls.append(command)
        if command[0] == "gitleaks":
            raise FileNotFoundError
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", missing_gitleaks)

    assert gate.run_precommit_gate(repo_root, report) == 1
    assert any("run-python-tests.py" in " ".join(call) for call in calls)
    gitleaks = next(
        row
        for row in load_report(report)["phases"]
        if row["phase_id"] == "gitleaks"
    )
    assert gitleaks["returncode"] == 127


def test_gate_preserves_incomplete_python_group_report(
    tmp_path: Path, monkeypatch
) -> None:
    """An interrupted child test runner makes the enclosing gate incomplete."""
    repo_root = configured_repo(tmp_path)
    report = tmp_path / "precommit-gate.json"

    def incomplete_test_runner(
        command: list[str], **_kwargs: object
    ) -> SimpleNamespace:
        if any("run-python-tests.py" in part for part in command):
            Path(command[-1]).write_text(
                '{"complete": false, "groups": []}\n', encoding="utf-8"
            )
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", incomplete_test_runner)

    assert gate.run_precommit_gate(repo_root, report) == 1
    assert load_report(report)["complete"] is False


def test_gate_interrupt_marks_report_incomplete_and_stops(
    tmp_path: Path, monkeypatch
) -> None:
    """User interrupts are infrastructure stops, not ordinary accumulated failures."""
    repo_root = configured_repo(tmp_path)
    report = tmp_path / "precommit-gate.json"

    def interrupt(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        raise KeyboardInterrupt

    monkeypatch.setattr(gate.subprocess, "run", interrupt)

    try:
        gate.run_precommit_gate(repo_root, report)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("the gate must propagate a user interrupt")

    assert load_report(report)["complete"] is False
