from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import jsonschema
import pytest
import yaml

import officina.common.pooled_blueprint as pooled_blueprint
from officina.common.artifact_health import certify_graph, check_graph_health
from officina.common.audit_records import attach_record_authentication
from officina.common.blueprint_graph import (
    load_repository_blueprint_graphs,
    load_skill_blueprint_graph,
    resolve_repository_skill_graph,
)
from officina.common.blueprint_template import load_schema, schema_validator
from officina.common.pooled_blueprint import (
    PooledReviewValidationError,
    certify_pooled_review,
    check_pooled_review,
    pooled_review_health_path,
    pooled_review_path,
    render_pooled_review,
)


KEY = b"p" * 32
POLICY_HASH = "sha256:" + "1" * 64
SCHEMA_HASH = "sha256:" + "2" * 64
SCHEMA_ROOT = Path("references/blueprint").resolve()


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _copy_pool_schema_bundle(destination: Path) -> None:
    destination.mkdir()
    for name in ("pooled-review.schema.json", "health.schema.json"):
        (destination / name).write_bytes((SCHEMA_ROOT / name).read_bytes())


def _fixture(tmp_path: Path):
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Body one.\n", encoding="utf-8")
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "interfaces": [
                {
                    "interface": "demo-skill.llm.default",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": ".SKILL.md.blueprint.yaml",
                    },
                }
            ],
        },
    )
    _write_yaml(
        skill / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "demo-skill.llm.default",
            "version": 1,
            "description": "Primary.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "uses_interfaces": [],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    graph = load_skill_blueprint_graph(skill)
    records = certify_graph(
        graph,
        POLICY_HASH,
        SCHEMA_HASH,
        [{"id": "schema", "passed": True}],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    root_report = check_graph_health(graph, records, POLICY_HASH, SCHEMA_HASH, KEY)
    pool_path = pooled_review_path(skill)
    pool_path.write_text(render_pooled_review(graph, records), encoding="utf-8")
    health_path = pooled_review_health_path(skill)
    health = certify_pooled_review(
        pool_path,
        records[graph.root.node_id],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    health_path.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")
    return skill, graph, records, root_report, pool_path, health_path


def _inline_fixture(tmp_path: Path):
    skill, _graph, _records, _report, _pool_path, _health_path = _fixture(tmp_path)
    sidecar_path = skill / ".SKILL.md.blueprint.yaml"
    sidecar = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    root = yaml.safe_load((skill / "blueprint.yaml").read_text(encoding="utf-8"))
    root["default_interface"] = {
        key: value
        for key, value in sidecar.items()
        if key not in {"schema_version", "blueprint_type", "id", "binding"}
    }
    root["interfaces"] = []
    _write_yaml(skill / "blueprint.yaml", root)
    sidecar_path.unlink()
    graph = load_skill_blueprint_graph(skill)
    records = certify_graph(
        graph,
        POLICY_HASH,
        SCHEMA_HASH,
        [{"id": "schema", "passed": True}],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    report = check_graph_health(graph, records, POLICY_HASH, SCHEMA_HASH, KEY)
    pool_path = pooled_review_path(skill)
    pool_path.write_text(render_pooled_review(graph, records), encoding="utf-8")
    health_path = pooled_review_health_path(skill)
    health_path.write_text(
        json.dumps(
            certify_pooled_review(
                pool_path,
                records[graph.root.node_id],
                key=KEY,
                certified_at="2026-07-13T12:00:00-04:00",
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return skill, graph, records, report, pool_path, health_path


def _check(
    pool_path: Path,
    health_path: Path,
    root_report,
    graph,
    records,
    *,
    schema_root: Path = SCHEMA_ROOT,
):
    return check_pooled_review(
        pool_path,
        health_path,
        root_report,
        KEY,
        graph=graph,
        records=records,
        schema_root=schema_root,
    )


def test_pooled_review_is_deterministic_and_expands_nodes(tmp_path: Path) -> None:
    _skill, graph, records, _report, _pool_path, _health_path = _fixture(tmp_path)

    first = render_pooled_review(graph, records)
    second = render_pooled_review(graph, records)
    loaded = yaml.safe_load(first)

    assert first == second
    assert loaded["document_type"] == "pooled-blueprint-review"
    assert [node["id"] for node in loaded["nodes"]] == [
        "demo-skill",
        "demo-skill.llm.default",
    ]


def test_inline_default_pool_inherits_root_health_without_extra_record(
    tmp_path: Path,
) -> None:
    _skill, graph, records, report, pool_path, health_path = _inline_fixture(tmp_path)

    loaded = yaml.safe_load(pool_path.read_text(encoding="utf-8"))
    checked = _check(pool_path, health_path, report, graph, records)

    assert set(records) == {"demo-skill"}
    assert loaded["nodes"][0]["health"] == loaded["nodes"][1]["health"]
    assert checked.healthy


def test_pool_content_change_only_makes_pool_unhealthy(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    pool_path.write_text(pool_path.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")

    pool_report = _check(pool_path, health_path, root_report, graph, records)

    assert root_report.healthy
    assert not pool_report.healthy
    assert "pooled-review-stale" in pool_report.concerns


def test_arbitrary_authenticated_yaml_is_not_a_healthy_pool(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    pool_path.write_text("not: a pooled review\n", encoding="utf-8")
    health = certify_pooled_review(
        pool_path,
        records[root_report.root_id],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    health_path.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")

    result = _check(pool_path, health_path, root_report, graph, records)

    assert not result.healthy
    assert "invalid-pooled-review" in result.concerns


def test_noncanonical_schema_valid_pool_is_unhealthy(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    document = yaml.safe_load(pool_path.read_text(encoding="utf-8"))
    document["generated_at"] = "2026-07-13T12:00:01-04:00"
    pool_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    health = certify_pooled_review(
        pool_path,
        records[root_report.root_id],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    health_path.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")

    result = _check(pool_path, health_path, root_report, graph, records)

    assert not result.healthy
    assert "noncanonical-pooled-review" in result.concerns


def test_unhealthy_root_makes_pool_unhealthy_without_reverse_dependency(tmp_path: Path) -> None:
    skill, graph, records, _root_report, pool_path, health_path = _fixture(tmp_path)
    (skill / "SKILL.md").write_text("Body two.\n", encoding="utf-8")
    stale_root = check_graph_health(graph, records, POLICY_HASH, SCHEMA_HASH, KEY)

    pool_report = _check(pool_path, health_path, stale_root, graph, records)

    assert not stale_root.healthy
    assert not pool_report.healthy
    assert "root-unhealthy" in pool_report.concerns
    assert "invalid-pooled-review" not in pool_report.concerns


def test_missing_pool_does_not_change_canonical_root_health(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, _health_path = _fixture(tmp_path)
    pool_path.unlink()

    repeated = check_graph_health(graph, records, POLICY_HASH, SCHEMA_HASH, KEY)

    assert root_report.healthy
    assert repeated.healthy


def test_generated_pool_and_health_records_validate_against_normative_schemas(
    tmp_path: Path,
) -> None:
    _skill, _graph, records, _root_report, pool_path, health_path = _fixture(tmp_path)
    health_validator = schema_validator(
        load_schema(Path("references/blueprint/health.schema.json"))
    )
    pool_validator = schema_validator(
        load_schema(Path("references/blueprint/pooled-review.schema.json"))
    )

    for record in records.values():
        health_validator.validate(record)
    health_validator.validate(json.loads(health_path.read_text(encoding="utf-8")))
    pool_validator.validate(yaml.safe_load(pool_path.read_text(encoding="utf-8")))


def test_authenticated_invalid_pooled_health_is_rejected(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    record = json.loads(health_path.read_text(encoding="utf-8"))
    record["checks"] = [{"id": "pool", "passed": False}]
    health_path.write_text(
        json.dumps(attach_record_authentication(record, KEY), indent=2) + "\n",
        encoding="utf-8",
    )

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert "invalid-pooled-review-health" in report.concerns


def test_authenticated_pooled_health_must_name_expected_root(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    record = json.loads(health_path.read_text(encoding="utf-8"))
    record["subject"]["root_id"] = "other-skill"
    health_path.write_text(
        json.dumps(attach_record_authentication(record, KEY), indent=2) + "\n",
        encoding="utf-8",
    )

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert "invalid-pooled-review-health" in report.concerns


def test_exact_authenticated_canonical_pool_is_healthy(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)

    report = _check(pool_path, health_path, root_report, graph, records)

    assert report.healthy
    assert report.concerns == ()


def test_malformed_pool_yaml_is_a_concern_not_an_exception(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    pool_path.write_text("nodes: [\n", encoding="utf-8")

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_canonical_render_failure_is_a_concern_not_an_exception(tmp_path: Path) -> None:
    _skill, graph, _records, root_report, pool_path, health_path = _fixture(tmp_path)

    report = _check(pool_path, health_path, root_report, graph, {})

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_unadmitted_records_cannot_define_a_healthy_pool(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    tampered_records = json.loads(json.dumps(records))
    tampered_records[root_report.root_id]["certification"]["certified_at"] = (
        "2026-07-13T12:00:01-04:00"
    )
    pool_path.write_text(
        render_pooled_review(graph, tampered_records),
        encoding="utf-8",
    )
    health = certify_pooled_review(
        pool_path,
        records[root_report.root_id],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    health_path.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")

    report = _check(pool_path, health_path, root_report, graph, tampered_records)

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_authenticated_record_with_altered_artifact_hash_cannot_define_pool(
    tmp_path: Path,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    altered_records = json.loads(json.dumps(records))
    root_record = altered_records[root_report.root_id]
    certified_health_hash = root_record["hashes"]["certified_health_hash"]
    root_record["hashes"]["artifact_graph_hash"] = "sha256:" + "9" * 64
    altered_records[root_report.root_id] = attach_record_authentication(root_record, KEY)
    assert (
        altered_records[root_report.root_id]["hashes"]["certified_health_hash"]
        == certified_health_hash
    )
    pool_path.write_text(
        render_pooled_review(graph, altered_records),
        encoding="utf-8",
    )
    health = certify_pooled_review(
        pool_path,
        altered_records[root_report.root_id],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    health_path.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")

    report = _check(pool_path, health_path, root_report, graph, altered_records)

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_authenticated_record_subject_must_match_full_graph_identity(
    tmp_path: Path,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    altered_records = json.loads(json.dumps(records))
    node_id = "demo-skill.llm.default"
    altered_records[node_id]["subject"]["version"] = 2
    altered_records[node_id] = attach_record_authentication(altered_records[node_id], KEY)
    pool_path.write_text(
        render_pooled_review(graph, altered_records),
        encoding="utf-8",
    )
    health = certify_pooled_review(
        pool_path,
        altered_records[root_report.root_id],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    health_path.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")

    report = _check(pool_path, health_path, root_report, graph, altered_records)

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_report_status_identity_must_match_graph_key(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    node_id = "demo-skill.llm.default"
    mismatched_report = replace(
        root_report,
        nodes={
            **root_report.nodes,
            node_id: replace(root_report.nodes[node_id], node_id="other.llm.default"),
        },
    )

    report = _check(
        pool_path,
        health_path,
        mismatched_report,
        graph,
        records,
    )

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_pool_read_failure_is_a_concern_not_an_exception(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    original_read = pooled_blueprint.os.read

    def fail_pool_read(descriptor: int, size: int) -> bytes:
        if Path(os.readlink(f"/proc/self/fd/{descriptor}")) == pool_path:
            raise OSError("pool read failed")
        return original_read(descriptor, size)

    monkeypatch.setattr(pooled_blueprint.os, "read", fail_pool_read)

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_malformed_pool_schema_is_a_concern_not_an_exception(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "malformed-schema"
    schema_root.mkdir()
    (schema_root / "pooled-review.schema.json").write_text("{", encoding="utf-8")

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_pool_schema_direct_self_reference_is_a_stable_concern(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "self-referencing-schema"
    _copy_pool_schema_bundle(schema_root)
    (schema_root / "pooled-review.schema.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "pooled-review.schema.json",
                "$ref": "pooled-review.schema.json",
            }
        ),
        encoding="utf-8",
    )

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert not report.healthy
    assert report.concerns == ("invalid-pooled-review",)


def test_pool_and_health_schema_reference_cycle_is_a_stable_concern(
    tmp_path: Path,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "mutually-referencing-schema"
    _copy_pool_schema_bundle(schema_root)
    for name, referenced_name in (
        ("pooled-review.schema.json", "health.schema.json"),
        ("health.schema.json", "pooled-review.schema.json"),
    ):
        (schema_root / name).write_text(
            json.dumps(
                {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "$id": name,
                    "$ref": referenced_name,
                }
            ),
            encoding="utf-8",
        )

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert not report.healthy
    assert report.concerns == ("invalid-pooled-review",)


def test_pool_schema_direct_internal_fragment_cycle_is_a_stable_concern(
    tmp_path: Path,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "direct-internal-cycle-schema"
    _copy_pool_schema_bundle(schema_root)
    (schema_root / "pooled-review.schema.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "pooled-review.schema.json",
                "$ref": "#/definitions/loop",
                "definitions": {
                    "loop": {"$ref": "#/definitions/loop"},
                },
            }
        ),
        encoding="utf-8",
    )

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert not report.healthy
    assert report.concerns == ("invalid-pooled-review",)


def test_pool_schema_mutual_internal_fragment_cycle_is_a_stable_concern(
    tmp_path: Path,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "mutual-internal-cycle-schema"
    _copy_pool_schema_bundle(schema_root)
    (schema_root / "pooled-review.schema.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "pooled-review.schema.json",
                "$ref": "#/definitions/first",
                "definitions": {
                    "first": {"$ref": "#/definitions/second"},
                    "second": {"$ref": "#/definitions/first"},
                },
            }
        ),
        encoding="utf-8",
    )

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert not report.healthy
    assert report.concerns == ("invalid-pooled-review",)


def test_pool_schema_noncyclic_internal_fragment_remains_valid(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "noncyclic-internal-schema"
    _copy_pool_schema_bundle(schema_root)
    (schema_root / "pooled-review.schema.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "pooled-review.schema.json",
                "$ref": "#/definitions/value",
                "definitions": {
                    "value": {"type": "object"},
                },
            }
        ),
        encoding="utf-8",
    )

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert report.healthy
    assert report.concerns == ()


def test_pool_schema_acyclic_reference_chain_exceeding_recursion_limit_is_valid(
    tmp_path: Path,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "long-acyclic-chain-schema"
    _copy_pool_schema_bundle(schema_root)
    chain_length = sys.getrecursionlimit() + 100
    definitions = {
        f"d{index}": {"$ref": f"#/definitions/d{index + 1}"}
        for index in range(chain_length - 1)
    }
    definitions[f"d{chain_length - 1}"] = {"type": "object"}
    (schema_root / "pooled-review.schema.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "pooled-review.schema.json",
                "type": "object",
                "definitions": definitions,
            }
        ),
        encoding="utf-8",
    )

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert report.healthy
    assert report.concerns == ()


def test_ref_and_id_shaped_const_data_are_not_schema_references(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "literal-const-schema"
    _copy_pool_schema_bundle(schema_root)
    (schema_root / "pooled-review.schema.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "pooled-review.schema.json",
                "type": "object",
                "properties": {
                    "absent-literal": {
                        "const": {
                            "$ref": "unbundled.schema.json",
                            "$id": "literal-data",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert report.healthy
    assert report.concerns == ()


@pytest.mark.parametrize(
    "schema_ref",
    [
        "file:///outside-pooled-review.schema.json",
        "https://example.invalid/outside-pooled-review.schema.json",
        "../outside-pooled-review.schema.json",
        "/outside-pooled-review.schema.json",
        "unbundled.schema.json",
    ],
)
def test_pool_schema_rejects_nonlocal_or_unbundled_refs_without_resolving(
    tmp_path: Path,
    monkeypatch,
    schema_ref: str,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "confined-schema"
    _copy_pool_schema_bundle(schema_root)
    (schema_root / "pooled-review.schema.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "pooled-review.schema.json",
                "$ref": schema_ref,
            }
        ),
        encoding="utf-8",
    )
    (schema_root / "unbundled.schema.json").write_text(
        '{"type": "object"}\n',
        encoding="utf-8",
    )
    external_reads: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"type":"object"}'

    def record_external_read(uri: str):
        external_reads.append(uri)
        return Response()

    monkeypatch.setattr(jsonschema.validators, "urlopen", record_external_read)

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns
    assert external_reads == []


def test_unrelated_malformed_schema_sibling_is_not_eagerly_read(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "schema-with-unrelated-sibling"
    _copy_pool_schema_bundle(schema_root)
    (schema_root / "unrelated.schema.json").write_text("{", encoding="utf-8")

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert report.healthy
    assert report.concerns == ()


@pytest.mark.parametrize(
    "selected_name",
    ["pooled-review.schema.json", "health.schema.json"],
)
def test_selected_schema_symlink_is_rejected(
    tmp_path: Path,
    selected_name: str,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    schema_root = tmp_path / "schema-symlink"
    schema_root.mkdir()
    for name in ("pooled-review.schema.json", "health.schema.json"):
        destination = schema_root / name
        source = SCHEMA_ROOT / name
        if name == selected_name:
            destination.symlink_to(source)
        else:
            destination.write_bytes(source.read_bytes())

    report = _check(
        pool_path,
        health_path,
        root_report,
        graph,
        records,
        schema_root=schema_root,
    )

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns


def test_pooled_health_read_failure_is_a_concern_not_an_exception(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    original_read = pooled_blueprint.os.read

    def fail_health_read(descriptor: int, size: int) -> bytes:
        if Path(os.readlink(f"/proc/self/fd/{descriptor}")) == health_path:
            raise OSError("pooled health read failed")
        return original_read(descriptor, size)

    monkeypatch.setattr(pooled_blueprint.os, "read", fail_health_read)

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert "invalid-pooled-review-health" in report.concerns


def test_malformed_authentication_is_a_concern_not_an_exception(tmp_path: Path) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    health = json.loads(health_path.read_text(encoding="utf-8"))
    health["coverage"] = {"invalid": 1.5}
    health_path.write_text(json.dumps(health) + "\n", encoding="utf-8")

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert "pooled-review-authentication-failed" in report.concerns


@pytest.mark.parametrize(
    ("artifact", "expected_concern"),
    [
        ("pool", "invalid-pooled-review"),
        ("health", "invalid-pooled-review-health"),
    ],
)
def test_pooled_artifact_final_symlink_is_rejected(
    tmp_path: Path,
    artifact: str,
    expected_concern: str,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    target = pool_path if artifact == "pool" else health_path
    moved = target.with_name(target.name + ".moved")
    target.rename(moved)
    target.symlink_to(moved.name)

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert expected_concern in report.concerns


def test_pooled_certification_rejects_final_symlink(tmp_path: Path) -> None:
    _skill, _graph, records, root_report, pool_path, _health_path = _fixture(tmp_path)
    moved = pool_path.with_name(pool_path.name + ".moved")
    pool_path.rename(moved)
    pool_path.symlink_to(moved.name)

    with pytest.raises(PooledReviewValidationError):
        certify_pooled_review(
            pool_path,
            records[root_report.root_id],
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_pooled_certification_rejects_final_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _skill, _graph, records, root_report, pool_path, _health_path = _fixture(tmp_path)
    original_authenticate = pooled_blueprint.attach_record_authentication

    def authenticate_after_replacement(record, key):
        authenticated = original_authenticate(record, key)
        content = pool_path.read_bytes()
        pool_path.rename(pool_path.with_name(pool_path.name + ".replaced"))
        pool_path.write_bytes(content)
        return authenticated

    monkeypatch.setattr(
        pooled_blueprint,
        "attach_record_authentication",
        authenticate_after_replacement,
    )

    with pytest.raises(PooledReviewValidationError):
        certify_pooled_review(
            pool_path,
            records[root_report.root_id],
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_pooled_certification_rejects_parent_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill, _graph, records, root_report, pool_path, _health_path = _fixture(tmp_path)
    original_authenticate = pooled_blueprint.attach_record_authentication
    moved_skill = skill.with_name(skill.name + "-replaced")

    def authenticate_after_parent_replacement(record, key):
        authenticated = original_authenticate(record, key)
        skill.rename(moved_skill)
        skill.mkdir()
        pool_path.write_bytes((moved_skill / pool_path.name).read_bytes())
        return authenticated

    monkeypatch.setattr(
        pooled_blueprint,
        "attach_record_authentication",
        authenticate_after_parent_replacement,
    )

    with pytest.raises(PooledReviewValidationError):
        certify_pooled_review(
            pool_path,
            records[root_report.root_id],
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


@pytest.mark.parametrize(
    ("artifact", "expected_concern"),
    [
        ("pool", "invalid-pooled-review"),
        ("health", "invalid-pooled-review-health"),
    ],
)
def test_pooled_artifact_final_swap_is_detected_before_healthy_return(
    tmp_path: Path,
    monkeypatch,
    artifact: str,
    expected_concern: str,
) -> None:
    _skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    target = pool_path if artifact == "pool" else health_path
    original_render = pooled_blueprint.render_pooled_review
    swapped = False

    def swap_after_snapshot(*args, **kwargs):
        nonlocal swapped
        rendered = original_render(*args, **kwargs)
        if not swapped:
            swapped = True
            content = target.read_bytes()
            target.rename(target.with_name(target.name + ".replaced"))
            target.write_bytes(content)
        return rendered

    monkeypatch.setattr(pooled_blueprint, "render_pooled_review", swap_after_snapshot)

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert expected_concern in report.concerns


def test_pooled_artifact_parent_swap_is_detected_for_both_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill, graph, records, root_report, pool_path, health_path = _fixture(tmp_path)
    original_render = pooled_blueprint.render_pooled_review
    moved_skill = skill.with_name(skill.name + "-moved")
    swapped = False

    def swap_parent_after_snapshots(*args, **kwargs):
        nonlocal swapped
        rendered = original_render(*args, **kwargs)
        if not swapped:
            swapped = True
            skill.rename(moved_skill)
            skill.mkdir()
            for name in (pool_path.name, health_path.name):
                (skill / name).write_bytes((moved_skill / name).read_bytes())
        return rendered

    monkeypatch.setattr(
        pooled_blueprint,
        "render_pooled_review",
        swap_parent_after_snapshots,
    )

    report = _check(pool_path, health_path, root_report, graph, records)

    assert not report.healthy
    assert "invalid-pooled-review" in report.concerns
    assert "invalid-pooled-review-health" in report.concerns


@pytest.mark.parametrize(
    ("pool_failure", "expected_concern"),
    [
        ("missing", "missing-pooled-review"),
        ("invalid", "invalid-pooled-review"),
    ],
)
def test_unhealthy_root_concern_survives_pool_open_failure(
    tmp_path: Path,
    pool_failure: str,
    expected_concern: str,
) -> None:
    skill, graph, records, _root_report, pool_path, health_path = _fixture(tmp_path)
    (skill / "SKILL.md").write_text("Body two.\n", encoding="utf-8")
    stale_root = check_graph_health(graph, records, POLICY_HASH, SCHEMA_HASH, KEY)
    if pool_failure == "missing":
        pool_path.unlink()
    else:
        pool_path.rename(pool_path.with_name(pool_path.name + ".moved"))
        pool_path.mkdir()

    report = _check(pool_path, health_path, stale_root, graph, records)

    assert not stale_root.healthy
    assert not report.healthy
    assert report.concerns == ("root-unhealthy", expected_concern)


def test_pooled_review_uses_repository_relative_paths_for_cross_skill_nodes(
    tmp_path: Path,
) -> None:
    for name in ("provider", "consumer"):
        skill = tmp_path / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"{name}.\n", encoding="utf-8")
    _write_yaml(
        tmp_path / "skills" / "provider" / "blueprint.yaml",
        {
            "interfaces": {
                "llm": {
                    "default": {
                        "version": 1,
                        "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    }
                }
            }
        },
    )
    _write_yaml(
        tmp_path / "skills" / "consumer" / "blueprint.yaml",
        {
            "interfaces": {
                "llm": {
                    "default": {
                        "version": 1,
                        "binding": {"kind": "skill_file", "path": "SKILL.md"},
                        "uses_interfaces": [
                            {"interface": "provider.llm.default", "version": 1}
                        ],
                    }
                }
            }
        },
    )
    graph = resolve_repository_skill_graph(
        load_repository_blueprint_graphs(tmp_path),
        "consumer",
    )
    records = certify_graph(
        graph,
        POLICY_HASH,
        SCHEMA_HASH,
        [{"id": "schema", "passed": True}],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )

    loaded = yaml.safe_load(render_pooled_review(graph, records))
    provider = next(node for node in loaded["nodes"] if node["id"] == "provider.llm.default")

    assert provider["blueprint_path"] == "$repo/skills/provider/blueprint.yaml"
    assert provider["binding_path"] == "$repo/skills/provider/SKILL.md"
