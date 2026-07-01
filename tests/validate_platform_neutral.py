"""Tests for validators/platform_neutral.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators.platform_neutral import validate  # noqa: E402


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_clean_skill_passes(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: my-skill\n---\nHello world.\n")
    assert validate(tmp_path) == []


def test_platform_reference_in_skill_detected(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: my-skill\n---\nUse Claude for this.\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "Claude" in errors[0]


def test_excluded_install_path_skipped(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "install-assistant-tools"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Install Claude Code here.\n")
    assert validate(tmp_path) == []


def test_tests_subdir_skipped(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill" / "tests"
    d.mkdir(parents=True)
    (d / "test_something.py").write_text("# test for claude or codex\n")
    assert validate(tmp_path) == []


def test_references_dir_scanned(tmp_path: Path) -> None:
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("Use Claude Code to run this.\n")
    errors = validate(tmp_path)
    assert any("Claude" in e for e in errors)


def test_multiple_violations_all_reported(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Use Claude.\nAlso codex.\n")
    errors = validate(tmp_path)
    assert len(errors) == 2


def test_runner_exits_zero_on_clean_repo(tmp_path: Path) -> None:
    runner = Path(__file__).resolve().parents[1] / "validators" / "runner.py"
    result = subprocess.run(
        ["python3", str(runner), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_runner_exits_nonzero_on_violation(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Use Claude here.\n")
    runner = Path(__file__).resolve().parents[1] / "validators" / "runner.py"
    result = subprocess.run(
        ["python3", str(runner), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
