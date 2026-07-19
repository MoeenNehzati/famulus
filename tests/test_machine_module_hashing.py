from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from officina.common.artifact_health import (
    ArtifactHealthError,
    compute_machine_module_hash_states,
    machine_module_hashes_current,
)
from officina.common.blueprint_graph import load_repository_blueprint_graph


def _write_repository(root: Path) -> Path:
    skill = root / "skills" / "hash-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "_worker.py").write_text("class Interface:\n    pass\n", encoding="utf-8")
    (skill / "defs.schema.json").write_text(
        '{"definitions":{"value":{"type":"string"}}}', encoding="utf-8"
    )
    (skill / "output.schema.json").write_text(
        '{"$ref":"defs.schema.json#/definitions/value"}', encoding="utf-8"
    )
    (skill / "interface-conformance.yaml").write_text(
        "schema_version: 1\nexpected_streams:\n  stdout:\n    schema: output.schema.json\n",
        encoding="utf-8",
    )
    export = {
        "id": "hash-skill.machine.run",
        "version": 1,
        "description": "Run.",
        "allow_all_skills": True,
        "allowed_callers": [],
        "invocation_binding": {"fixed": []},
        "uses_interfaces": [],
        "helpers": [],
        "direct_io": {"reads": [], "writes": [], "network": []},
        "owns_filesystem": [],
        "contract": {
            "arguments": {},
            "preconditions": [],
            "interaction": {"mode": "unattended"},
            "caller_warnings": [],
            "outputs": [
                {
                    "id": "value",
                    "schema": {"path": "output.schema.json", "fragment": "#"},
                }
            ],
            "outcomes": [],
            "execution": {},
        },
    }
    declaration = {
        "schema_version": 3,
        "node_type": "machine-module",
        "id": "hash-skill.machine-module.worker",
        "version": 1,
        "description": "Worker.",
        "gateway": {
            "kind": "python-entrypoint",
            "path": "_rtx/_worker.py",
            "symbol": "Interface",
            "args_prefix": [],
            "conformance": {
                "adapter_protocol": "officina-python-adapters@1",
                "bind_method": "bind_conformance_adapters",
                "sandbox_profile": "officina-isolated-effects@1",
            },
        },
        "content": [r"_rtx/_worker\.py"],
        "conformance_manifest": {
            "base": "skill-root",
            "path": "interface-conformance.yaml",
        },
        "platform_support": {"linux": True, "macos": True, "windows": True},
        "dependencies": [],
        "behavior_sources": [],
        "owns_filesystem": [],
        "uses_interfaces": [],
        "interfaces": {"run": export, "inspect": {**export, "id": "hash-skill.machine.inspect"}},
    }
    blueprint = runtime / "._worker.py.blueprint.yaml"
    blueprint.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
    return blueprint


def _state(root: Path):
    graph = load_repository_blueprint_graph(root)
    states = compute_machine_module_hash_states(graph, root)
    return states["hash-skill.machine-module.worker"]


def test_module_hashes_blueprint_and_content_separately_from_contract_references(
    tmp_path: Path,
) -> None:
    blueprint = _write_repository(tmp_path)
    first = _state(tmp_path)

    assert first.export_ids == (
        "hash-skill.machine.inspect",
        "hash-skill.machine.run",
    )
    assert {entry["locator"] for entry in first.reference_digests} == {
        "defs.schema.json#/definitions/value",
        "interface-conformance.yaml#",
        "output.schema.json#",
    }

    (tmp_path / "skills" / "hash-skill" / "defs.schema.json").write_text(
        '{"definitions":{"value":{"type":"integer"}}}', encoding="utf-8"
    )
    reference_changed = _state(tmp_path)
    assert reference_changed.node_hash == first.node_hash
    assert reference_changed.contract_reference_hash != first.contract_reference_hash

    (tmp_path / "skills" / "hash-skill" / "_rtx" / "_worker.py").write_text(
        "class Interface:\n    changed = True\n", encoding="utf-8"
    )
    content_changed = _state(tmp_path)
    assert content_changed.node_hash != reference_changed.node_hash

    declaration = yaml.safe_load(blueprint.read_text(encoding="utf-8"))
    declaration["interfaces"]["run"]["description"] = "Changed contract."
    blueprint.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
    contract_changed = _state(tmp_path)
    assert contract_changed.node_hash != content_changed.node_hash


def test_currentness_requires_both_hashes(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    state = _state(tmp_path)
    assert machine_module_hashes_current(
        state,
        node_hash=state.node_hash,
        contract_reference_hash=state.contract_reference_hash,
    )
    assert not machine_module_hashes_current(
        state,
        node_hash="sha256:" + "0" * 64,
        contract_reference_hash=state.contract_reference_hash,
    )
    assert not machine_module_hashes_current(
        state,
        node_hash=state.node_hash,
        contract_reference_hash="sha256:" + "0" * 64,
    )


def test_reference_hashing_rejects_missing_and_symlinked_files(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    schema = tmp_path / "skills" / "hash-skill" / "output.schema.json"
    schema.unlink()
    with pytest.raises(ArtifactHealthError, match="does not exist"):
        _state(tmp_path)

    schema.write_text('{"type":"string"}', encoding="utf-8")
    schema.unlink()
    schema.symlink_to("defs.schema.json")
    with pytest.raises(ArtifactHealthError, match="symlink"):
        _state(tmp_path)
