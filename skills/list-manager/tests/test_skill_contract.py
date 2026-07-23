from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]


def test_cloud_update_contract_requires_list_patches_with_quoted_string_ids():
    module = yaml.safe_load((SKILL_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))
    export = module["exports"]["list-manager.interface.cloud-update"]
    source_interface = export["source_interface"]
    source_id = source_interface.partition(".interface.")[0]
    source_path = module["sources"][source_id]["blueprint"]["path"]
    source = yaml.safe_load((SKILL_ROOT / source_path).read_text(encoding="utf-8"))
    cloud_update = source["interfaces"][source_interface]
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
