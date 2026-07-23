"""Tests for the version-4 cross-platform validator."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from validators.cross_platform import validate  # noqa: E402


def _copy_module(repo_root: Path, source_name: str = "get-weather") -> Path:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )
    target = repo_root / "skills" / source_name
    shutil.copytree(
        REPO_ROOT / "skills" / source_name,
        target,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return target


def _write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_clean_v4_python_module_passes(tmp_path: Path) -> None:
    _copy_module(tmp_path)

    assert validate(tmp_path) == []


def test_shell_script_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "bad-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    errors = validate(tmp_path)

    assert any("shell scripts are not allowed" in error for error in errors)


def test_module_permission_shell_command_is_rejected(tmp_path: Path) -> None:
    skill = _copy_module(tmp_path, "loose-mode")
    path = skill / "blueprint.yaml"
    module = yaml.safe_load(path.read_text(encoding="utf-8"))
    module["authority"]["suggested_permissions"]["bash"] = [
        {
            "command": ["grep"],
            "reason": "Invalid portable permission.",
        }
    ]
    _write_yaml(path, module)

    errors = validate(tmp_path)

    assert any("command `grep` is not cross-platform" in error for error in errors)


def test_module_permission_shell_script_argument_is_rejected(
    tmp_path: Path,
) -> None:
    skill = _copy_module(tmp_path, "loose-mode")
    path = skill / "blueprint.yaml"
    module = yaml.safe_load(path.read_text(encoding="utf-8"))
    module["authority"]["suggested_permissions"]["bash"] = [
        {
            "command": ["python3"],
            "args_prefix": ["_rtx/run.sh"],
            "reason": "Invalid shell entrypoint.",
        }
    ]
    _write_yaml(path, module)

    errors = validate(tmp_path)

    assert any("shell script token `_rtx/run.sh`" in error for error in errors)


def test_all_platform_source_binary_is_rejected(tmp_path: Path) -> None:
    skill = _copy_module(tmp_path)
    source_path = skill / "blueprints" / "rtx-weather-client.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["runtime_dependencies"] = [
        {
            "kind": "binary",
            "name": "grep",
            "version": "any",
            "platforms": {
                "linux": True,
                "macos": True,
                "windows": True,
            },
            "reason": "Invalid all-platform command.",
        }
    ]
    _write_yaml(source_path, source)

    errors = validate(tmp_path)

    assert any("command `grep` is not cross-platform" in error for error in errors)


def test_python_macos_subprocess_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "bad-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.py").write_text(
        "import subprocess\nsubprocess.run(['osascript', '-e', 'beep'])\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("command `osascript` is not cross-platform" in error for error in errors)


def test_platform_named_python_file_may_use_platform_command(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "skills" / "scheduler-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "_osx_backend.py").write_text(
        "import subprocess\nsubprocess.run(['launchctl', 'list'])\n",
        encoding="utf-8",
    )

    assert validate(tmp_path) == []


def test_python_shell_true_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "bad-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.py").write_text(
        "import subprocess\nsubprocess.run('echo hi', shell=True)\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("shell=True is not allowed" in error for error in errors)


def test_runner_reports_cross_platform_errors(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "bad-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    runner = REPO_ROOT / "validators" / "runner.py"

    result = subprocess.run(
        [sys.executable, str(runner), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
