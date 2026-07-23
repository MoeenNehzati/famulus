"""Smoke tests for skills/skill-maker/validators/skill_md_dispatch.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil


_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR = (
    _REPO_ROOT
    / "skills" / "skill-maker" / "validators" / "skill_md_dispatch.py"
)
_spec = importlib.util.spec_from_file_location("skill_md_dispatch", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)

_BLUEPRINT = (
    "interfaces:\n"
    "  machine:\n"
    "    run:\n"
    "      description: 'Run the demo script.'\n"
    "      usage: '[args]'\n"
    "      invocation:\n"
    "        kind: python_machine_interface\n"
    "        entrypoint: _rtx/_run_tool.py:Interface\n"
    "      dependencies: []\n"
)

_DISPATCH = "dispatcher --caller-skill demo-skill demo-skill.machine.run"


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_missing_interface_block_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")
    errors = _mod.validate(tmp_path)
    assert any("missing generated blueprint interface block" in error for error in errors)


def test_raw_script_in_generated_block_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "_rtx/_run_tool.py\n"
        "<!-- END BLUEPRINT INTERFACES -->\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("must not expose raw runtime files" in error for error in errors)


def test_dispatcher_command_required(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "dispatcher --caller-skill other other run\n"
        "<!-- END BLUEPRINT INTERFACES -->\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("missing dispatcher command" in error for error in errors)


def test_valid_generated_block_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        f"{_DISPATCH}\n"
        "<!-- END BLUEPRINT INTERFACES -->\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_raw_script_in_body_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        f"{_DISPATCH}\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "\nDo this: `_rtx/_run_tool.py foo`\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("skill body must not invoke runtime files directly" in e for e in errors)


def test_raw_script_in_generated_block_only_not_flagged_by_body_check(tmp_path: Path) -> None:
    """A raw script in the generated block is caught by the block check, not the body check."""
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "_rtx/_run_tool.py\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "\nBody is clean.\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert not any("skill body must not invoke runtime files directly" in e for e in errors)
    assert any("must not expose raw runtime files" in e for e in errors)


def test_dispatcher_invocation_in_body_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        f"{_DISPATCH}\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        f"\nRun it: `{_DISPATCH} myarg`\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("skill body must not invoke dispatcher directly" in e for e in errors)


def test_dispatcher_prose_mention_in_body_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        f"{_DISPATCH} myarg\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "\nUse `run`; the dispatcher resolves paths automatically.\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_body_referencing_interface_name_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(_BLUEPRINT, encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        f"{_DISPATCH} myarg --cloud\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "\nUse `run` to execute. See the interface block above for the full invocation.\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_v4_machine_export_requires_generic_dispatcher_command(tmp_path: Path) -> None:
    shutil.copytree(
        _REPO_ROOT / "references" / "blueprint",
        tmp_path / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )
    skill = tmp_path / "skills" / "get-weather"
    shutil.copytree(
        _REPO_ROOT / "skills" / "get-weather",
        skill,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    skill_md = skill / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8").replace(
            "get-weather.interface.scripts-weather",
            "get-weather.interface.wrong",
        ),
        encoding="utf-8",
    )

    errors = _mod.validate(tmp_path)

    assert any(
        "get-weather.interface.scripts-weather" in error
        and "missing dispatcher command" in error
        for error in errors
    )
