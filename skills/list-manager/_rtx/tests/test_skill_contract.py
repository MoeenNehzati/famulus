from pathlib import Path

from officina.common.blueprint_graph import (
    load_repository_blueprint_graph,
    repository_schema_version,
    resolve_export,
)

TEST_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = TEST_ROOT.parent
SKILL_ROOT = (
    RUNTIME_ROOT.parent if RUNTIME_ROOT.name == "_rtx" else RUNTIME_ROOT
)
REPO_ROOT = SKILL_ROOT.parents[1]
SCHEMA_VERSION = repository_schema_version(REPO_ROOT)
SCHEMA_ROOT = REPO_ROOT / "references/blueprint"
if SCHEMA_VERSION == 5:
    SCHEMA_ROOT = SCHEMA_ROOT / "v5"


def test_cloud_update_contract_requires_list_patches_with_quoted_string_ids():
    graph = load_repository_blueprint_graph(
        REPO_ROOT,
        schema_root=SCHEMA_ROOT,
        expected_schema_version=SCHEMA_VERSION,
    )
    _module, _source, export = resolve_export(
        graph,
        "list-manager.interface.cloud-update",
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


def test_local_update_description_matches_its_sequence_patch_contract():
    graph = load_repository_blueprint_graph(
        REPO_ROOT,
        schema_root=SCHEMA_ROOT,
        expected_schema_version=SCHEMA_VERSION,
    )
    _module, _source, export = resolve_export(
        graph,
        "list-manager.interface.update-list",
    )
    update_list = export.declaration

    assert "YAML sequence of patch objects" in update_list["description"]
    assert "keyed by id" not in update_list["description"]
