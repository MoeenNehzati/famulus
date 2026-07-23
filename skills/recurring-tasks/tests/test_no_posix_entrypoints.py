from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / "skills" / "recurring-tasks"


def test_recurring_tasks_blueprint_has_no_shell_runtime_interfaces():
    module = yaml.safe_load((SKILL_DIR / "blueprint.yaml").read_text(encoding="utf-8"))
    process_exports = {}
    for export_id, export in module["exports"].items():
        source_interface = export["source_interface"]
        source_id = source_interface.partition(".interface.")[0]
        source_path = module["sources"][source_id]["blueprint"]["path"]
        source = yaml.safe_load((SKILL_DIR / source_path).read_text(encoding="utf-8"))
        interface = source["interfaces"][source_interface]
        binding = interface.get("process_binding")
        if binding is not None:
            process_exports[export_id] = (source, binding)

    assert "recurring-tasks.interface.scripts-invoke-agent" not in module["exports"]
    assert process_exports
    for export_id, (source, binding) in process_exports.items():
        assert source["gateway"]["language"] == "Python", export_id
        assert binding["kind"] == "process", export_id
        assert not source["gateway"]["path"].endswith(".sh"), export_id
        assert not any(
            str(value).endswith(".sh")
            for value in binding.values()
        ), export_id


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
