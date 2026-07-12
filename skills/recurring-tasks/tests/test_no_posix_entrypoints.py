from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / "skills" / "recurring-tasks"


def test_recurring_tasks_blueprint_has_no_shell_runtime_interfaces():
    blueprint = yaml.safe_load((SKILL_DIR / "blueprint.yaml").read_text(encoding="utf-8"))
    machine = blueprint["interfaces"]["machine"]

    assert "scripts-invoke-agent" not in machine
    for name, spec in machine.items():
        runtime = spec["invocation"]
        assert runtime["kind"] == "python_machine_interface", name
        assert not any(str(value).endswith(".sh") for value in runtime.values())


def test_recurring_tasks_runtime_tree_has_no_posix_shell_files():
    shell_files = sorted(path.name for path in (SKILL_DIR / "_rtx").glob("*.sh"))
    assert shell_files == []


def test_generated_services_do_not_use_posix_shell(tmp_path):
    from test_sync_units import JOBS_ONE_ENABLED, _run_sync

    _run_sync(JOBS_ONE_ENABLED, str(tmp_path))
    service = (tmp_path / "ai-test-job.service").read_text(encoding="utf-8")
    assert "/bin/bash" not in service
    assert "bash -c" not in service
    assert ">>" not in service
