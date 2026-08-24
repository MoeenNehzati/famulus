from pathlib import Path

import pytest

from officina.blueprints.graph import (
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
SCHEMA_ROOT = REPO_ROOT / "references/blueprint-schema"
if SCHEMA_VERSION == 5:
    SCHEMA_ROOT = SCHEMA_ROOT / "v5"


@pytest.fixture(scope="module")
def repository_graph():
    """Load the immutable repository graph once for both contract lookups."""
    return load_repository_blueprint_graph(
        REPO_ROOT,
        schema_root=SCHEMA_ROOT,
        expected_schema_version=SCHEMA_VERSION,
    )


def test_cloud_update_contract_requires_list_patches_with_quoted_string_ids(
    repository_graph,
):
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


def test_local_update_description_matches_its_sequence_patch_contract(
    repository_graph,
):
    _module, _source, export = resolve_export(
        repository_graph,
        "list-manager._rtx.interface.update-list",
    )
    update_list = export.declaration

    assert "YAML sequence of patch objects" in update_list["description"]
    assert "keyed by id" not in update_list["description"]
