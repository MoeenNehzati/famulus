from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint"


def _json(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def _yaml(name: str) -> dict:
    return yaml.safe_load((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def _profile_hash(profile: dict, catalog: dict) -> str:
    resolved_rules = [
        {
            "id": pin["id"],
            "version": pin["version"],
            "statement": catalog[pin["id"]]["statement"],
        }
        for pin in profile["rules"]
    ]
    payload = {"profile": profile, "resolved_rules": resolved_rules}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_catalog_uses_one_discriminated_rule_namespace() -> None:
    metadata = _json("schema-meta.json")
    catalog = metadata["x-famulus"]["validation_rule_catalog"]
    rule_schema = copy.deepcopy(metadata["definitions"]["validationRule"])
    rule_schema["definitions"] = metadata["definitions"]
    validator = jsonschema.Draft7Validator(rule_schema)

    assert {entry["rule_kind"] for entry in catalog.values()} == {
        "repository-validation",
        "interface-admissibility",
    }
    for rule_id, entry in catalog.items():
        validator.validate(entry)
        if rule_id.startswith("interface."):
            assert entry["rule_kind"] == "interface-admissibility"
            assert rule_id.endswith(f"@{entry['version']}")
        else:
            assert entry["rule_kind"] == "repository-validation"


def test_profile_pins_only_admissibility_rules_and_hashes_meaning() -> None:
    metadata = _json("schema-meta.json")
    catalog = metadata["x-famulus"]["validation_rule_catalog"]
    profile = _yaml("interface-admissibility.profile.yaml")
    jsonschema.Draft7Validator(
        _json("interface-admissibility-profile.schema.json")
    ).validate(profile)

    ids = [pin["id"] for pin in profile["rules"]]
    assert len(ids) == len(set(ids))
    assert all(catalog[rule_id]["rule_kind"] == "interface-admissibility" for rule_id in ids)
    assert all(catalog[pin["id"]]["version"] == pin["version"] for pin in profile["rules"])

    expected = _profile_hash(profile, catalog)
    reversed_catalog = dict(reversed(list(catalog.items())))
    assert _profile_hash(profile, reversed_catalog) == expected

    reordered_profile = copy.deepcopy(profile)
    reordered_profile["rules"][:2] = reversed(reordered_profile["rules"][:2])
    assert _profile_hash(reordered_profile, catalog) != expected

    changed_catalog = copy.deepcopy(catalog)
    changed_catalog[ids[0]]["statement"] += " Changed."
    assert _profile_hash(profile, changed_catalog) != expected


def test_admissibility_result_schema_has_four_closed_variants() -> None:
    schema = _json("interface-admissibility-result.schema.json")
    validator = jsonschema.Draft7Validator(schema)
    base = {
        "schema_version": 1,
        "subject": "example-skill.machine.inspect-records",
        "source_hash": "sha256:" + "a" * 64,
        "profile": {"id": "machine-export-admissibility", "version": 1},
        "profile_hash": "sha256:" + "b" * 64,
        "rule": {"id": "interface.document.closed@1", "version": 1},
        "findings": [],
        "evidence": ["blueprint:/interfaces/inspect-records"],
    }

    for result in ("passed", "not-applicable"):
        validator.validate({**base, "result": result})
    for result in ("failed", "checker-error"):
        validator.validate(
            {
                **base,
                "result": result,
                "findings": [
                    {
                        "code": "contract-invalid",
                        "location": "/contract",
                        "message": "The contract is invalid.",
                    }
                ],
            }
        )

    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**base, "result": "unknown"})
