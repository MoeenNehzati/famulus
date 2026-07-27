"""Tests for version-4 generated SKILL.md dispatcher exposure."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    REPO_ROOT
    / "validators"
    / "skill"
    / "skill_md_dispatch.py"
)
SPEC = importlib.util.spec_from_file_location("skill_md_dispatch", VALIDATOR)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

DISPATCH = (
    "dispatcher --caller-skill get-weather "
    "get-weather.interface.scripts-weather"
)


def _copy_weather_module(repo_root: Path) -> Path:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )
    target = repo_root / "skills" / "get-weather"
    shutil.copytree(
        REPO_ROOT / "skills" / "get-weather",
        target,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return target


def _replace(skill_file: Path, old: str, new: str) -> None:
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace(old, new),
        encoding="utf-8",
    )


def test_repo_without_modules_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()

    assert MOD.validate(tmp_path) == []


def test_valid_v4_generated_block_passes(tmp_path: Path) -> None:
    _copy_weather_module(tmp_path)

    assert MOD.validate(tmp_path) == []


def test_missing_interface_block_is_rejected(tmp_path: Path) -> None:
    skill = _copy_weather_module(tmp_path)
    skill_file = skill / "SKILL.md"
    start = skill_file.read_text(encoding="utf-8").index(
        "<!-- BEGIN BLUEPRINT INTERFACES -->"
    )
    end_marker = "<!-- END BLUEPRINT INTERFACES -->"
    text = skill_file.read_text(encoding="utf-8")
    end = text.index(end_marker, start) + len(end_marker)
    skill_file.write_text(text[:start] + text[end:], encoding="utf-8")

    errors = MOD.validate(tmp_path)

    assert any("missing generated blueprint interface block" in error for error in errors)


def test_process_export_requires_canonical_dispatcher_command(
    tmp_path: Path,
) -> None:
    skill = _copy_weather_module(tmp_path)
    _replace(
        skill / "SKILL.md",
        "get-weather.interface.scripts-weather",
        "get-weather.interface.wrong",
    )

    errors = MOD.validate(tmp_path)

    assert any(
        "get-weather.interface.scripts-weather" in error
        and "missing dispatcher command" in error
        for error in errors
    )


def test_generated_block_rejects_raw_runtime_path(tmp_path: Path) -> None:
    skill = _copy_weather_module(tmp_path)
    marker = "<!-- END BLUEPRINT INTERFACES -->"
    _replace(
        skill / "SKILL.md",
        marker,
        "_rtx/_weather_client.py\n" + marker,
    )

    errors = MOD.validate(tmp_path)

    assert any("must not expose raw runtime files" in error for error in errors)


def test_hand_authored_body_rejects_runtime_invocation(tmp_path: Path) -> None:
    skill = _copy_weather_module(tmp_path)
    skill_file = skill / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8")
        + "\nRun `_rtx/_weather_client.py` directly.\n",
        encoding="utf-8",
    )

    errors = MOD.validate(tmp_path)

    assert any(
        "skill body must not invoke runtime files directly" in error
        for error in errors
    )


def test_hand_authored_body_rejects_dispatcher_command(tmp_path: Path) -> None:
    skill = _copy_weather_module(tmp_path)
    skill_file = skill / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8") + f"\nRun `{DISPATCH}`.\n",
        encoding="utf-8",
    )

    errors = MOD.validate(tmp_path)

    assert any("skill body must not invoke dispatcher directly" in error for error in errors)
