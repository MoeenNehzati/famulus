"""Tests for skills/skill-maker/validators/skill_body_execution.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "skill_body_execution.py"
)
_spec = importlib.util.spec_from_file_location("skill_body_execution", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _write_skill(tmp_path: Path, body: str, name: str = "demo-skill") -> Path:
    skill = tmp_path / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(body, encoding="utf-8")
    return skill


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_executable_filename_in_execution_context_is_rejected(tmp_path: Path) -> None:
    _write_skill(tmp_path, "Run `tmp.py` after editing.\n")

    errors = _mod.validate(tmp_path)

    assert any("SBE001" in error and "tmp.py" in error for error in errors)


def test_executable_relative_path_in_body_is_rejected(tmp_path: Path) -> None:
    _write_skill(tmp_path, "Run ./install.sh for setup.\n")

    errors = _mod.validate(tmp_path)

    assert any("SBE001" in error and "./install.sh" in error for error in errors)


def test_interpreter_plus_executable_filename_is_rejected(tmp_path: Path) -> None:
    _write_skill(tmp_path, "Use `python helper.py` to inspect the state.\n")

    errors = _mod.validate(tmp_path)

    assert any("SBE001" in error and "helper.py" in error for error in errors)


def test_frontmatter_is_ignored(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "---\n"
        "name: demo-skill\n"
        "description: Use when checking setup.py references.\n"
        "---\n"
        "Body is clean.\n",
    )

    assert _mod.validate(tmp_path) == []


def test_generated_blocks_are_ignored(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "<!-- BEGIN BLUEPRINT CONTRACT -->\n"
        "`_rtx/_run_tool.py`\n"
        "<!-- END BLUEPRINT CONTRACT -->\n"
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "dispatcher --caller-skill demo-skill demo-skill.machine.run _rtx/_run_tool.py\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "Use the `run` interface.\n",
    )

    assert _mod.validate(tmp_path) == []


def test_hand_authored_fenced_code_is_scanned(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "Avoid examples like:\n"
        "```bash\n"
        "python tmp.py\n"
        "```\n",
    )

    errors = _mod.validate(tmp_path)

    assert any("SBE001" in error and "tmp.py" in error for error in errors)


def test_architectural_executable_filename_reference_passes(tmp_path: Path) -> None:
    _write_skill(tmp_path, "The reusable base scaffold lives at `llmhooks/lib/cross_host.py`.\n")

    assert _mod.validate(tmp_path) == []


def test_plain_executable_filename_reference_passes_without_execution_context(tmp_path: Path) -> None:
    _write_skill(tmp_path, "Document helper.ps1 as a launcher artifact.\n")

    assert _mod.validate(tmp_path) == []


def test_non_executable_document_and_data_paths_pass(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "Read `README.md`, `schema.json`, `config.yaml`, and `notes.txt`.\n",
    )

    assert _mod.validate(tmp_path) == []


def test_system_skill_is_skipped(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / ".system" / "tool"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Run tmp.py.\n", encoding="utf-8")

    assert _mod.validate(tmp_path) == []
