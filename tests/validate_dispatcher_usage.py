"""Smoke tests for skills/skill-maker/validators/dispatcher_usage.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "dispatcher_usage.py"
)
_spec = importlib.util.spec_from_file_location("dispatcher_usage", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_declared_dispatch_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "good-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: good-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    dispatches = {\n"
        "        'other': DispatchCall(caller_skill='good-skill', target_skill='other', interface='x')\n"
        "    }\n"
        "    def run(self, args):\n"
        "        return self.dispatch('other').returncode\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_raw_dispatch_import_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill='bad-skill', target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("not raw officina.dispatcher" in error for error in errors)


def test_cli_dispatcher_call_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "import subprocess\n"
        "subprocess.run(['dispatcher', '--caller-skill', 'bad-skill'])\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("dispatcher CLI" in error for error in errors)


def test_sys_path_hack_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "hacky-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: hacky-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '/repo/src')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("do not modify sys.path" in error for error in errors)


def test_installer_skill_exempt(tmp_path: Path) -> None:
    # install-assistant-tools manages the launcher and bootstraps imports;
    # it is exempt as a whole (see references/skill-standards/skill-guidelines.md).
    skill = tmp_path / "skills" / "install-assistant-tools"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: install-assistant-tools\n", encoding="utf-8")
    (skill / "_rtx" / "setup.py").write_text(
        "import sys\n"
        "sys.path.insert(0, str(root / 'src'))\n"
        "launcher = bin_dir / \"dispatcher\"\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_other_skills_not_exempt(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "sneaky-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: sneaky-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "sys.path.insert(0, '/repo/src')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("do not modify sys.path" in error for error in errors)
