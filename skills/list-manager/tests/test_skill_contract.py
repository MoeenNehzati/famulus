from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]


def test_cloud_update_contract_requires_list_patches_with_quoted_string_ids():
    blueprint = yaml.safe_load((SKILL_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))
    cloud_update = blueprint["interfaces"]["machine"]["cloud-update"]
    contract = "\n".join(
        [cloud_update["description"]]
        + [pattern["notes"] for pattern in cloud_update["patterns"]]
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
