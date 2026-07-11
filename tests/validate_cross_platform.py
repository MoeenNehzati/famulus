"""Tests for validators/cross_platform.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators.cross_platform import validate  # noqa: E402


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_clean_python_skill_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "clean-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "interfaces:\n"
        "  machine:\n"
        "    run:\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_run_tool.py:Interface\n"
        "      dependencies: []\n",
        encoding="utf-8",
    )
    (skill / "_rtx" / "run.py").write_text(
        "import subprocess\nsubprocess.run([\"python3\", \"_rtx/_helper_tool.py\"])\n",
        encoding="utf-8",
    )
    assert validate(tmp_path) == []


def test_shell_script_is_rejected(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill" / "_rtx"
    skill.mkdir(parents=True)
    (skill / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    errors = validate(tmp_path)
    assert any("shell scripts are not allowed" in error for error in errors)


def test_blueprint_shell_command_is_rejected(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "interfaces:\n"
        "  machine:\n"
        "    run:\n"
        "      runtime:\n"
        "        kind: command\n"
        "        argv: [\"_rtx/_run_tool.sh\"]\n"
        "      dependencies: []\n",
        encoding="utf-8",
    )
    errors = validate(tmp_path)
    assert any("shell script token `_rtx/_run_tool.sh`" in error for error in errors)


def test_blueprint_unix_command_in_permission_is_rejected(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "suggested_permissions:\n"
        "  bash:\n"
        "    - command: [\"grep\"]\n"
        "      reason: test\n",
        encoding="utf-8",
    )
    errors = validate(tmp_path)
    assert any("command `grep` is not cross-platform" in error for error in errors)


def test_blueprint_windows_command_is_rejected(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "interfaces:\n"
        "  machine:\n"
        "    run:\n"
        "      runtime:\n"
        "        kind: command\n"
        "        argv: [\"powershell.exe\", \"-File\", \"_rtx/_run_tool.ps1\"]\n"
        "      dependencies: []\n",
        encoding="utf-8",
    )
    errors = validate(tmp_path)
    assert any("command `powershell.exe` is not cross-platform" in error for error in errors)


def test_python_macos_subprocess_is_rejected(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill" / "_rtx"
    skill.mkdir(parents=True)
    (skill / "run.py").write_text(
        "import subprocess\nsubprocess.run(['osascript', '-e', 'beep'])\n",
        encoding="utf-8",
    )
    errors = validate(tmp_path)
    assert any("command `osascript` is not cross-platform" in error for error in errors)


def test_python_shell_true_is_rejected(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill" / "_rtx"
    skill.mkdir(parents=True)
    (skill / "run.py").write_text(
        "import subprocess\nsubprocess.run('echo hi', shell=True)\n",
        encoding="utf-8",
    )
    errors = validate(tmp_path)
    assert any("shell=True is not allowed" in error for error in errors)


def test_python_unix_subprocess_is_rejected(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill" / "_rtx"
    skill.mkdir(parents=True)
    (skill / "run.py").write_text(
        "import subprocess\nsubprocess.run(['grep', 'x', 'file.txt'])\n",
        encoding="utf-8",
    )
    errors = validate(tmp_path)
    assert any("command `grep` is not cross-platform" in error for error in errors)


def test_cross_platform_false_skips_skill(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "daily-plan" / "_rtx"
    skill.mkdir(parents=True)
    (tmp_path / "skills" / "daily-plan" / "blueprint.yaml").write_text(
        "cross_platform: false\n",
        encoding="utf-8",
    )
    (skill / "plans.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    assert validate(tmp_path) == []


def test_cross_platform_defaults_to_true(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "default-skill" / "_rtx"
    skill.mkdir(parents=True)
    (skill / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    errors = validate(tmp_path)
    assert any("shell scripts are not allowed" in error for error in errors)


def test_runner_reports_cross_platform_errors(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill" / "_rtx"
    skill.mkdir(parents=True)
    (skill / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    runner = Path(__file__).resolve().parents[1] / "validators" / "runner.py"
    result = subprocess.run(
        ["python3", str(runner), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "cross_platform" in (result.stdout + result.stderr)
