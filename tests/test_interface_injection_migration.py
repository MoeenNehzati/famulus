from __future__ import annotations

from pathlib import Path

import pytest

from officina.common.blueprint_graph import (
    BlueprintEdge,
    BlueprintNode,
    MachineInterfaceExport,
    RepositoryBlueprintGraph,
)
from officina.common.interface_injection_migration import (
    InterfaceInjectionMigrationError,
    build_interface_injection_migration_report,
)


def _graph() -> RepositoryBlueprintGraph:
    root = Path("/repo/skills/consumer-skill")
    consumer = BlueprintNode(
        node_id="consumer-skill.llm.default",
        node_type="llm-interface",
        version=1,
        skill_root=root,
        blueprint_path=root / "blueprint.yaml",
        gateway_path=root / "SKILL.md",
        declaration={},
    )
    module = BlueprintNode(
        node_id="provider-skill.machine-module.worker",
        node_type="machine-module",
        version=1,
        skill_root=Path("/repo/skills/provider-skill"),
        blueprint_path=Path("/repo/skills/provider-skill/_rtx/._worker.py.blueprint.yaml"),
        gateway_path=Path("/repo/skills/provider-skill/_rtx/_worker.py"),
        declaration={},
    )
    export = MachineInterfaceExport(
        interface_id="provider-skill.machine.run",
        version=1,
        local_name="run",
        module_node_id=module.node_id,
        declaration={},
    )
    return RepositoryBlueprintGraph(
        nodes={consumer.node_id: consumer, module.node_id: module},
        node_edges=(
            BlueprintEdge(
                "uses-interface", consumer.node_id, export.interface_id, 1
            ),
        ),
        machine_exports={export.interface_id: export},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
    )


def test_report_is_complete_deterministic_and_machine_readable() -> None:
    report = build_interface_injection_migration_report(
        _graph(),
        ["stale-skill.machine.old", "provider-skill.machine.run"],
        {
            "provider-skill.machine.run": "add-direct-edge",
            "stale-skill.machine.old": "retire",
        },
    )

    assert [entry.interface_id for entry in report.entries] == [
        "provider-skill.machine.run",
        "stale-skill.machine.old",
    ]
    assert report.entries[0].authored_consumers == (
        "consumer-skill.llm.default",
    )
    assert report.entries[1].target_exists is False
    assert report.as_document()["schema_version"] == 1


@pytest.mark.parametrize(
    ("legacy", "dispositions", "message"),
    [
        (["provider-skill.machine.run"], {}, "missing dispositions"),
        ([], {"provider-skill.machine.run": "retire"}, "unexpected dispositions"),
        (["x", "x"], {"x": "retire"}, "duplicate interface IDs"),
        (["x"], {"x": "unknown"}, "invalid disposition"),
        (["x"], {"x": "add-direct-edge"}, "requires a target export"),
    ],
)
def test_report_rejects_incomplete_duplicate_or_invalid_dispositions(
    legacy: list[str], dispositions: dict[str, str], message: str
) -> None:
    with pytest.raises(InterfaceInjectionMigrationError, match=message):
        build_interface_injection_migration_report(
            _graph(), legacy, dispositions
        )
