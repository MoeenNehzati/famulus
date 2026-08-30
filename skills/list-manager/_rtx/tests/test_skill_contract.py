from pathlib import Path

from officina.blueprints.graph import (
    RepositoryBlueprintGraph,
    resolve_export,
)

TEST_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = TEST_ROOT.parent
SKILL_ROOT = (
    RUNTIME_ROOT.parent if RUNTIME_ROOT.name == "_rtx" else RUNTIME_ROOT
)


def test_update_contracts_require_sequence_patches_and_quoted_string_ids(
    ordinary_repository_graph: RepositoryBlueprintGraph,
):
    repository_graph = ordinary_repository_graph
    _module, _source, export = resolve_export(
        repository_graph,
        "list-manager._rtx.interface.cloud-update",
    )
    cloud_update = export.declaration
    contract = "\n".join(
        [cloud_update["description"]]
        + [
            pattern["notes"]
            for pattern in cloud_update["process_binding"]["patterns"]
        ]
    )

    assert "YAML list of patch objects" in contract
    assert "quoted string `id`" in contract
    assert "not a mapping keyed by id" in contract

    skill_body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "- id: \"421753\"" in skill_body
    assert "never use an id-keyed YAML mapping" in skill_body
    assert "quote every `id`" in skill_body
    assert "number-to-id mapping" in skill_body
    assert "report the resolved ids and intended change" in skill_body

    _module, _source, export = resolve_export(
        repository_graph,
        "list-manager._rtx.interface.update-list",
    )
    update_list = export.declaration

    assert "YAML sequence of patch objects" in update_list["description"]
    assert "keyed by id" not in update_list["description"]
