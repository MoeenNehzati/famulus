from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess

import pytest
import yaml

import officina.certification.hashing as certification_hashing
import officina.git.provenance as git_provenance
import officina.runtime.python_machine_interface as python_interface
from officina.certification.hashing import (
    CertificationHashError,
    NodeHashState,
    compute_node_hash_states,
    map_route_smoke_dependencies,
    route_smoke_trace_signature,
)
from officina.blueprints.graph import (
    BlueprintGraphError,
    load_repository_blueprint_graph,
)
from officina.git.provenance import git_file_provenance
from test_support.git_repository import GitTestRepository
from test_support.v5_blueprint_fixtures import copy_v5_fixture_tree


CANONICAL_SCHEMA_ROOT = (
    Path(__file__).resolve().parents[1] / "references" / "blueprint-schema"
)
SCHEMA_ROOT = Path(__file__).parent / "fixtures" / "blueprint_schemas" / "v4"
V5_SCHEMA_ROOT = Path(__file__).parent / "fixtures" / "blueprint_schemas" / "v5"
V5_AUTHORIZATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "blueprint_v5" / "authorization"
)
_canonical_load_repository_blueprint_graph = load_repository_blueprint_graph


def load_repository_blueprint_graph(
    repo_root: Path,
    *,
    schema_root: Path | None = None,
    expected_schema_version: int = 4,
):
    """Keep frozen-v4 hashing fixtures explicit."""

    return _canonical_load_repository_blueprint_graph(
        repo_root,
        schema_root=schema_root,
        expected_schema_version=expected_schema_version,
    )


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _contract() -> dict[str, object]:
    return {
        "arguments": {},
        "preconditions": [],
        "interaction": {"mode": "unattended"},
        "caller_warnings": [],
        "outputs": [
            {
                "id": "result",
                "audience": "machine",
                "description": "Result.",
                "type": {"kind": "string"},
                "direct_io_ref": "stdout",
                "cardinality": {"minimum": 1, "maximum": 1},
                "ordering": "stable",
                "pagination": {"kind": "none"},
                "truncation": {"kind": "none"},
                "empty": "Never empty.",
            }
        ],
        "outcomes": [
            {
                "id": "success",
                "class": "success",
                "outputs": ["result"],
                "effects": [],
                "caller_action": "Continue.",
            }
        ],
        "execution": {
            "state_effect": "read-only",
            "lifecycle": "finite",
            "consistency": {"snapshot": "One snapshot."},
            "verification": [{"method": "output-schema", "output_ref": "result"}],
        },
        "helpers": [],
        "direct_io": {
            "reads": [],
            "writes": [
                {
                    "id": "stdout",
                    "medium": "stdout",
                    "access": "write",
                    "content": "Result.",
                    "formats": ["text"],
                    "sensitivity": "public",
                }
            ],
            "network": [],
        },
    }


def _write_module(
    root: Path,
    module_id: str,
    *,
    uses_export: str | None = None,
    schema_version: int = 4,
) -> None:
    module = root / "skills" / module_id
    (module / "_rtx").mkdir(parents=True)
    (module / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (module / "README.md").write_text("Module notes.\n", encoding="utf-8")
    (module / "_rtx" / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (module / "ignored.txt").write_text("included local state\n", encoding="utf-8")
    (module / "events.log").write_text("runtime log\n", encoding="utf-8")
    if schema_version == 6:
        (module / "remainder.txt").write_text(
            "remainder state\n",
            encoding="utf-8",
        )
    source_id = f"{module_id}.source.gateway"
    source_interface = f"{source_id}.interface.run"
    source_content = [
        r"SKILL\.md",
        r"_rtx/worker\.py",
        r"ignored\.txt",
        r"events\.log",
    ]
    if schema_version == 6:
        source_content.append(r"remainder\.txt")
    source_uses = (
        [{"interface": uses_export, "version": 1}]
        if uses_export is not None
        else []
    )
    interface_declaration = {
        "version": 1,
        "description": "Run.",
        "contract": _contract(),
    }
    interfaces = {source_interface: interface_declaration}
    if schema_version == 6:
        interface_declaration["content"] = [
            r"SKILL\.md",
            r"_rtx/worker\.py",
        ]
        interface_declaration["uses_interfaces"] = source_uses
        interfaces[f"{source_id}.interface.inspect"] = {
            "version": 1,
            "description": "Inspect.",
            "content": [r"SKILL\.md", r"ignored\.txt"],
            "uses_interfaces": [],
            "contract": _contract(),
        }
    _write_yaml(
        module / "blueprints" / "gateway.yaml",
        {
            "schema_version": schema_version,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": 1,
            **({"maturity": "stable"} if schema_version == 6 else {}),
            "description": "Gateway source.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": source_content,
            "dependencies": [],
            "uses_interfaces": source_uses,
            "interfaces": interfaces,
        },
    )
    _write_yaml(
        module / "blueprint.yaml",
        {
            "schema_version": schema_version,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            **({"maturity": "stable"} if schema_version == 6 else {}),
            "description": "Module.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [
                r"SKILL\.md",
                r"README\.md",
                r"_rtx/worker\.py",
                r"ignored\.txt",
                r"events\.log",
                *([r"remainder\.txt"] if schema_version == 6 else []),
            ],
            "authority": {"owns_filesystem": []},
            "sources": {
                source_id: {
                    "blueprint": {
                        "base": "module-root",
                        "path": "blueprints/gateway.yaml",
                    }
                }
            },
            **(
                {"children": {}, "namespace_exports": {}}
                if schema_version == 6
                else {}
            ),
            "exports": {
                f"{module_id}.interface.run": {
                    "source_interface": source_interface,
                    "access": {"allow_all_modules": True, "allowed_callers": []},
                }
            },
        },
    )


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    repository = GitTestRepository.initialize_existing_empty(tmp_path)
    _write_module(tmp_path, "provider-skill")
    _write_module(
        tmp_path,
        "consumer-skill",
        uses_export="provider-skill.interface.run",
    )
    (tmp_path / ".gitignore").write_text(
        "ignored.txt\n*.log\n", encoding="utf-8"
    )
    policy = tmp_path / "node-hash-policy.yaml"
    _write_yaml(
        policy,
        {
            "policy_version": 1,
            "path_syntax": "gitignore",
            "starting_set": "git-tracked-directly-owned-regular-files",
            "rules": [
                {"action": "exclude", "pattern": "**/*.log"},
                {
                    "action": "include",
                    "pattern": "**/ignored.txt",
                    "require_match": True,
                },
            ],
        },
    )
    repository.git("add", ".")
    repository.git("commit", "-qm", "fixture")
    return tmp_path, policy


def _states(
    root: Path,
    policy: Path,
    *,
    certification_basis_paths: tuple[Path, ...] = (),
) -> dict[str, NodeHashState]:
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)
    return compute_node_hash_states(
        graph,
        repo_root=root,
        policy_path=policy,
        certification_basis_hash="sha256:" + "b" * 64,
        certification_basis_paths=certification_basis_paths,
    )


def _v6_repository(tmp_path: Path) -> tuple[Path, Path]:
    repository = GitTestRepository.initialize_existing_empty(tmp_path)
    _write_module(tmp_path, "provider-skill", schema_version=6)
    _write_module(
        tmp_path,
        "consumer-skill",
        uses_export="provider-skill.interface.run",
        schema_version=6,
    )
    (tmp_path / ".gitignore").write_text(
        "ignored.txt\n*.log\n", encoding="utf-8"
    )
    policy = tmp_path / "node-hash-policy.yaml"
    _write_yaml(
        policy,
        {
            "policy_version": 1,
            "path_syntax": "gitignore",
            "starting_set": "git-tracked-directly-owned-regular-files",
            "rules": [
                {"action": "exclude", "pattern": "**/*.log"},
                {
                    "action": "include",
                    "pattern": "**/ignored.txt",
                    "require_match": True,
                },
            ],
        },
    )
    repository.git("add", ".")
    repository.git("commit", "-qm", "v6 fixture")
    return tmp_path, policy


def _v6_states(root: Path, policy: Path) -> dict[str, NodeHashState]:
    graph = load_repository_blueprint_graph(
        root,
        schema_root=CANONICAL_SCHEMA_ROOT,
        expected_schema_version=6,
    )
    return compute_node_hash_states(
        graph,
        repo_root=root,
        policy_path=policy,
        certification_basis_hash="sha256:" + "b" * 64,
    )


def test_v5_hashes_record_static_route_and_facade_edges_without_containment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = GitTestRepository.initialize_existing_empty(tmp_path)
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "modules",
        tmp_path / "modules",
    )
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "skills",
        tmp_path / "skills",
    )
    policy = tmp_path / "node-hash-policy.yaml"
    _write_yaml(
        policy,
        {
            "policy_version": 1,
            "path_syntax": "gitignore",
            "starting_set": "git-tracked-directly-owned-regular-files",
            "rules": [{"action": "exclude", "pattern": "**/.certificates/**"}],
        },
    )
    repository.git("add", ".")
    repository.git("commit", "-qm", "v5 fixture")
    graph = load_repository_blueprint_graph(
        tmp_path,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )
    real_manifests = certification_hashing._v4_node_input_manifests

    def manifests_with_legacy_contract_dependency(*args, **kwargs):
        manifests, contract_dependencies = real_manifests(*args, **kwargs)
        return manifests, {
            **contract_dependencies,
            "root": {"leaf"},
        }

    monkeypatch.setattr(
        certification_hashing,
        "_v4_node_input_manifests",
        manifests_with_legacy_contract_dependency,
    )

    states = compute_node_hash_states(
        graph,
        repo_root=tmp_path,
        policy_path=policy,
        certification_basis_hash="sha256:" + "b" * 64,
    )
    dependencies = {
        node_id: {
            (item["relation"], item["target"])
            for item in state.dependency_hashes
        }
        for node_id, state in states.items()
    }
    dependency_triples = {
        node_id: {
            (item["relation"], item["target"], item["version"])
            for item in state.dependency_hashes
        }
        for node_id, state in states.items()
    }
    expected_triples = {node_id: set() for node_id in graph.nodes}
    for edge in graph.certification_edges:
        expected_triples[edge.source_node_id].add(
            (edge.relation, edge.target_node_id, edge.target_version)
        )
    expected_triples["root"].add(
        ("references-cross-owner-contract", "leaf", graph.nodes["leaf"].version)
    )

    assert dependency_triples == expected_triples
    assert {
        ("routes-child-namespace", "alpha"),
        ("routes-terminal-module", "leaf"),
    } <= dependencies["root"]
    assert {
        ("routes-child-namespace", "leaf"),
        ("routes-terminal-module", "leaf"),
    } <= dependencies["alpha"]
    assert {
        ("facades-child-export", "demo-rtx"),
        ("facades-implementing-source", "demo-rtx.source.runtime"),
    } <= dependencies["demo"]
    assert all(
        relation != "contains-module"
        for node_dependencies in dependencies.values()
        for relation, _target in node_dependencies
    )

    root_hash = states["root"].node_hash
    alpha_hash = states["alpha"].node_hash
    leaf_runtime = (
        tmp_path
        / "modules"
        / "root"
        / "alpha"
        / "leaf"
        / "runtime.py"
    )
    leaf_runtime.write_text("VALUE = 'changed child bytes'\n", encoding="utf-8")
    changed = compute_node_hash_states(
        graph,
        repo_root=tmp_path,
        policy_path=policy,
        certification_basis_hash="sha256:" + "b" * 64,
    )

    assert changed["root"].node_hash == root_hash
    assert changed["alpha"].node_hash == alpha_hash
    assert (
        changed["leaf.source.runtime"].node_hash
        != states["leaf.source.runtime"].node_hash
    )


def _python_certification_basis_paths() -> tuple[Path, ...]:
    source_root = Path(certification_hashing.__file__).resolve().parents[2]
    return tuple(sorted((source_root / "officina").rglob("*.py")))


def _make_python_gateway(root: Path, module_id: str, *, import_unowned: bool) -> None:
    module = root / "skills" / module_id
    source_path = module / "blueprints" / "gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["gateway"] = {"path": "_rtx/worker.py", "language": "Python>=3.11"}
    source_interface = f"{module_id}.source.gateway.interface.run"
    source["interfaces"][source_interface]["process_binding"] = {
        "kind": "process",
        "entry": "Interface",
        "arguments": {},
        "fixed": [],
    }
    _write_yaml(source_path, source)
    unowned_import = ""
    if import_unowned:
        (module / "_rtx" / "unowned.py").write_text("VALUE = 1\n", encoding="utf-8")
        unowned_import = (
            "        import importlib.util\n"
            "        import sys\n"
            "        path = Path(__file__).with_name('unowned.py')\n"
            "        spec = importlib.util.spec_from_file_location('_unowned_dependency', path)\n"
            "        module = importlib.util.module_from_spec(spec)\n"
            "        sys.modules['_unowned_dependency'] = module\n"
            "        spec.loader.exec_module(module)\n"
        )
    (module / "_rtx" / "worker.py").write_text(
        "from pathlib import Path\n"
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "\n"
        "class Interface(PythonMachineInterface):\n"
        "    def route_smoke(self):\n"
        + unowned_import
        + "        return None\n"
        "\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )


def _set_output_schema(contract: dict[str, object], path: str) -> None:
    output = contract["outputs"][0]  # type: ignore[index]
    output.pop("type")
    output["schema"] = {"path": path, "fragment": "#"}


def _add_contract_source(
    root: Path,
    module_id: str,
    source_name: str,
    gateway_path: str,
    referenced_path: str | None = None,
    content_paths: tuple[str, ...] = (),
    schema_version: int = 4,
) -> str:
    module_root = root / "skills" / module_id
    source_id = f"{module_id}.source.{source_name}"
    interface_id = f"{source_id}.interface.read"
    contract = _contract()
    if referenced_path is not None:
        _set_output_schema(contract, referenced_path)
    source = {
        "schema_version": schema_version,
        "node_type": "behavioral_source",
        "id": source_id,
        "version": 1,
        **({"maturity": "stable"} if schema_version == 6 else {}),
        "description": "Contract source.",
        "gateway": {"path": gateway_path, "language": "JSON"},
        "content": [
            path.replace(".", r"\.")
            for path in (gateway_path, *content_paths)
        ],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {
            interface_id: {
                "version": 1,
                "description": "Read.",
                "contract": contract,
            }
        },
    }
    if schema_version == 6:
        source["interfaces"][interface_id]["content"] = list(source["content"])
        source["interfaces"][interface_id]["uses_interfaces"] = []
    blueprint_relative = f"blueprints/{source_name}.yaml"
    _write_yaml(module_root / blueprint_relative, source)
    module_path = module_root / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["sources"][source_id] = {
        "blueprint": {"base": "module-root", "path": blueprint_relative}
    }
    module["content"].extend(
        path.replace(".", r"\.")
        for path in (gateway_path, *content_paths)
    )
    _write_yaml(module_path, module)
    return source_id


def test_v4_uses_one_node_hash_state_and_policy_selected_input_manifest(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)

    states = _states(root, policy)
    source = states["provider-skill.source.gateway"]
    module = states["provider-skill"]

    assert isinstance(source, NodeHashState)
    assert source.certification_basis_hash == "sha256:" + "b" * 64
    assert {entry["git_provenance"] for entry in source.input_manifest} == {
        "tracked",
        "ignored",
    }
    assert {entry["path"] for entry in source.input_manifest} == {
        "skills/provider-skill/SKILL.md",
        "skills/provider-skill/_rtx/worker.py",
        "skills/provider-skill/blueprints/gateway.yaml",
        "skills/provider-skill/ignored.txt",
    }
    assert "skills/provider-skill/SKILL.md" in {
        entry["path"] for entry in module.input_manifest
    }
    assert all("rule" not in entry and "kind" not in entry for entry in source.input_manifest)


def test_dependency_change_does_not_recursively_change_consumer_local_hash(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    first = _states(root, policy)
    consumer_id = "consumer-skill.source.gateway"

    (root / "skills" / "provider-skill" / "_rtx" / "worker.py").write_text(
        "print('changed')\n", encoding="utf-8"
    )
    second = _states(root, policy)

    assert second[consumer_id].node_hash == first[consumer_id].node_hash
    assert second[consumer_id].dependency_hashes != first[consumer_id].dependency_hashes
    assert (
        second["provider-skill.source.gateway"].node_hash
        != first["provider-skill.source.gateway"].node_hash
    )
    assert second["provider-skill"].node_hash == first["provider-skill"].node_hash


def test_v6_interface_dependency_hash_ignores_unrelated_provider_blueprint_fields(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    first = _v6_states(root, policy)
    consumer_id = "consumer-skill.source.gateway"
    provider_id = "provider-skill.source.gateway"
    interface_id = "provider-skill.interface.run"
    first_dependency = next(
        dependency
        for dependency in first[consumer_id].dependency_hashes
        if dependency["relation"] == "uses-export"
    )

    provider_blueprint = root / "skills/provider-skill/blueprints/gateway.yaml"
    provider = yaml.safe_load(provider_blueprint.read_text(encoding="utf-8"))
    source_interface_id = "provider-skill.source.gateway.interface.run"
    provider["interfaces"][
        "provider-skill.source.gateway.interface.other"
    ] = deepcopy(provider["interfaces"][source_interface_id])
    provider["interfaces"][
        "provider-skill.source.gateway.interface.other"
    ]["description"] = "An unrelated interface."
    _write_yaml(provider_blueprint, provider)
    second = _v6_states(root, policy)
    second_dependency = next(
        dependency
        for dependency in second[consumer_id].dependency_hashes
        if dependency["relation"] == "uses-export"
    )

    assert first_dependency == second_dependency
    assert first_dependency["interface"] == interface_id
    assert first_dependency["interface_hash"].startswith("sha256:")
    assert "node_hash" not in first_dependency
    assert second[provider_id].node_hash != first[provider_id].node_hash


def test_v6_interface_dependency_hash_changes_with_used_contract(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    graph = load_repository_blueprint_graph(
        root,
        schema_root=CANONICAL_SCHEMA_ROOT,
        expected_schema_version=6,
    )
    interface_id = "provider-skill.interface.run"
    extracted = certification_hashing.extract_interface_from_blueprint(
        graph,
        interface_id,
        1,
    )
    first_hash = certification_hashing.compute_interface_hash(extracted)

    provider_blueprint = root / "skills/provider-skill/blueprints/gateway.yaml"
    provider = yaml.safe_load(provider_blueprint.read_text(encoding="utf-8"))
    source_interface_id = "provider-skill.source.gateway.interface.run"
    provider["interfaces"][source_interface_id]["contract"]["execution"][
        "consistency"
    ]["snapshot"] = "The contract changed."
    _write_yaml(provider_blueprint, provider)
    changed_graph = load_repository_blueprint_graph(
        root,
        schema_root=CANONICAL_SCHEMA_ROOT,
        expected_schema_version=6,
    )
    changed = certification_hashing.extract_interface_from_blueprint(
        changed_graph,
        interface_id,
        1,
    )

    assert extracted["id"] == interface_id
    assert extracted["source_interface"] == source_interface_id
    assert certification_hashing.compute_interface_hash(changed) != first_hash


def _facet(state: NodeHashState, facet_id: str):
    return next(facet for facet in state.facets if facet.facet_id == facet_id)


def test_v6_claimed_file_changes_only_its_interface_facet(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    first = _v6_states(root, policy)
    source_id = "provider-skill.source.gateway"
    run_id = f"{source_id}.interface.run"
    inspect_id = f"{source_id}.interface.inspect"

    (root / "skills/provider-skill/_rtx/worker.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    second = _v6_states(root, policy)

    assert _facet(second[source_id], run_id).local_hash != _facet(
        first[source_id], run_id
    ).local_hash
    assert _facet(second[source_id], inspect_id) == _facet(
        first[source_id], inspect_id
    )
    assert _facet(second[source_id], source_id) == _facet(
        first[source_id], source_id
    )
    assert second[source_id].node_hash != first[source_id].node_hash


def test_v6_unclaimed_file_changes_only_remainder_facet(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    first = _v6_states(root, policy)
    source_id = "provider-skill.source.gateway"
    run_id = f"{source_id}.interface.run"
    inspect_id = f"{source_id}.interface.inspect"

    (root / "skills/provider-skill/remainder.txt").write_text(
        "changed remainder\n",
        encoding="utf-8",
    )
    second = _v6_states(root, policy)

    assert _facet(second[source_id], run_id) == _facet(first[source_id], run_id)
    assert _facet(second[source_id], inspect_id) == _facet(
        first[source_id], inspect_id
    )
    assert _facet(second[source_id], source_id).local_hash != _facet(
        first[source_id], source_id
    ).local_hash
    assert second[source_id].node_hash != first[source_id].node_hash


def test_v6_used_interface_change_updates_dependency_not_consumer_local_hash(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    first = _v6_states(root, policy)
    consumer_id = "consumer-skill.source.gateway"
    consumer_interface = f"{consumer_id}.interface.run"

    (root / "skills/provider-skill/_rtx/worker.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    second = _v6_states(root, policy)

    first_facet = _facet(first[consumer_id], consumer_interface)
    second_facet = _facet(second[consumer_id], consumer_interface)
    assert second_facet.local_hash == first_facet.local_hash
    assert second_facet.dependency_hashes != first_facet.dependency_hashes
    assert second[consumer_id].node_hash == first[consumer_id].node_hash


def test_v6_source_without_interfaces_has_only_remainder_facet(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    source_path = root / "skills/provider-skill/blueprints/gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["interfaces"] = {}
    _write_yaml(source_path, source)
    module_path = root / "skills/provider-skill/blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["exports"] = {}
    _write_yaml(module_path, module)
    consumer_path = root / "skills/consumer-skill/blueprints/gateway.yaml"
    consumer = yaml.safe_load(consumer_path.read_text(encoding="utf-8"))
    consumer["uses_interfaces"] = []
    consumer["interfaces"][
        "consumer-skill.source.gateway.interface.run"
    ]["uses_interfaces"] = []
    _write_yaml(consumer_path, consumer)

    states = _v6_states(root, policy)
    facets = states["provider-skill.source.gateway"].facets

    assert [(facet.facet_type, facet.facet_id) for facet in facets] == [
        ("remainder", "provider-skill.source.gateway")
    ]


def test_v6_source_hash_uses_versioned_interface_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, policy = _v6_repository(tmp_path)
    captured: list[object] = []
    real_hash_value = certification_hashing._hash_value

    def capture(value: object) -> str:
        captured.append(deepcopy(value))
        return real_hash_value(value)

    monkeypatch.setattr(certification_hashing, "_hash_value", capture)
    _v6_states(root, policy)

    source_id = "provider-skill.source.gateway"
    aggregate = next(
        value
        for value in captured
        if isinstance(value, dict)
        and value.get("node_id") == source_id
        and "remainder_hash" in value
    )
    assert aggregate["interfaces"] == [
        {
            "id": f"{source_id}.interface.inspect",
            "version": 1,
            "interface_hash": aggregate["interfaces"][0]["interface_hash"],
        },
        {
            "id": f"{source_id}.interface.run",
            "version": 1,
            "interface_hash": aggregate["interfaces"][1]["interface_hash"],
        },
    ]


def test_v6_interface_contract_files_belong_to_originating_facet(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    module = root / "skills/provider-skill"
    contract_path = module / "run.schema.json"
    contract_path.write_text('{"type":"string"}\n', encoding="utf-8")
    source_path = module / "blueprints/gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["content"].append(r"run\.schema\.json")
    run_id = "provider-skill.source.gateway.interface.run"
    inspect_id = "provider-skill.source.gateway.interface.inspect"
    _set_output_schema(source["interfaces"][run_id]["contract"], "run.schema.json")
    _write_yaml(source_path, source)
    module_path = module / "blueprint.yaml"
    module_declaration = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module_declaration["content"].append(r"run\.schema\.json")
    _write_yaml(module_path, module_declaration)
    repository = GitTestRepository(root)
    repository.git("add", ".")
    repository.git("commit", "-qm", "add interface contract")

    first = _v6_states(root, policy)
    contract_path.write_text('{"type":"number"}\n', encoding="utf-8")
    second = _v6_states(root, policy)
    source_id = "provider-skill.source.gateway"

    assert "skills/provider-skill/run.schema.json" in {
        entry["path"] for entry in _facet(first[source_id], run_id).input_manifest
    }
    assert _facet(second[source_id], run_id).local_hash != _facet(
        first[source_id], run_id
    ).local_hash
    assert _facet(second[source_id], inspect_id) == _facet(
        first[source_id], inspect_id
    )
    assert _facet(second[source_id], source_id) == _facet(
        first[source_id], source_id
    )


def test_v6_cross_owner_contract_dependency_belongs_to_originating_facet(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    module = root / "skills/provider-skill"
    contract_path = module / "run.schema.json"
    contract_path.write_text('{"type":"string"}\n', encoding="utf-8")
    contract_source = _add_contract_source(
        root,
        "provider-skill",
        "run-contract",
        "run.schema.json",
        schema_version=6,
    )
    source_path = module / "blueprints/gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    run_id = "provider-skill.source.gateway.interface.run"
    _set_output_schema(source["interfaces"][run_id]["contract"], "run.schema.json")
    _write_yaml(source_path, source)
    repository = GitTestRepository(root)
    repository.git("add", ".")
    repository.git("commit", "-qm", "add cross-owner interface contract")

    state = _v6_states(root, policy)["provider-skill.source.gateway"]
    interface_dependencies = {
        (entry["relation"], entry["target"])
        for entry in _facet(state, run_id).dependency_hashes
    }
    remainder_dependencies = {
        (entry["relation"], entry["target"])
        for entry in _facet(state, "provider-skill.source.gateway").dependency_hashes
    }

    assert ("references-cross-owner-contract", contract_source) in interface_dependencies
    assert ("references-cross-owner-contract", contract_source) not in remainder_dependencies


def test_repository_root_contract_reference_targets_exact_file_owner(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    shared_contract = root / "skills" / "provider-skill" / "shared.schema.json"
    shared_contract.write_text(
        '{"$ref": "nested/child.schema.json"}\n', encoding="utf-8"
    )
    child_contract = (
        root / "skills" / "provider-skill" / "nested" / "child.schema.json"
    )
    child_contract.parent.mkdir()
    child_contract.write_text(
        '{"$ref": "../sibling.schema.json"}\n', encoding="utf-8"
    )
    sibling_contract = root / "skills" / "provider-skill" / "sibling.schema.json"
    sibling_contract.write_text('{"type": "string"}\n', encoding="utf-8")
    owner_id = _add_contract_source(
        root,
        "provider-skill",
        "shared-contract",
        "shared.schema.json",
        content_paths=("nested/child.schema.json", "sibling.schema.json"),
    )
    consumer_path = (
        root / "skills" / "consumer-skill" / "blueprints" / "gateway.yaml"
    )
    consumer = yaml.safe_load(consumer_path.read_text(encoding="utf-8"))
    consumer["contract_references"] = [
        {
            "base": "repository-root",
            "path": "skills/provider-skill/shared.schema.json",
        }
    ]
    _write_yaml(consumer_path, consumer)
    repository = GitTestRepository(root)
    repository.git("add", ".")
    repository.git("commit", "-qm", "add shared contract")

    states = _states(root, policy)
    consumer_state = states["consumer-skill.source.gateway"]

    assert {
        (item["relation"], item["target"])
        for item in consumer_state.dependency_hashes
    } >= {("references-cross-owner-contract", owner_id)}
    assert {
        "skills/provider-skill/shared.schema.json",
        "skills/provider-skill/nested/child.schema.json",
        "skills/provider-skill/sibling.schema.json",
    } <= {item["path"] for item in states[owner_id].input_manifest}
    assert "skills/provider-skill/shared.schema.json" not in {
        item["path"] for item in consumer_state.input_manifest
    }


def test_policy_last_match_wins_and_reserved_outputs_fail_closed(tmp_path: Path) -> None:
    root, policy = _repository(tmp_path)
    document = yaml.safe_load(policy.read_text(encoding="utf-8"))
    document["rules"].append(
        {"action": "include", "pattern": "**/*.log", "require_match": True}
    )
    _write_yaml(policy, document)
    assert any(
        entry["path"].endswith("events.log")
        for entry in _states(root, policy)["provider-skill.source.gateway"].input_manifest
    )

    certificate = root / "skills" / "provider-skill" / ".certificates" / "current.json"
    certificate.parent.mkdir()
    certificate.write_text("{}\n", encoding="utf-8")
    module_path = root / "skills" / "provider-skill" / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["content"].append(r"\.certificates/current\.json")
    _write_yaml(module_path, module)
    document["rules"].append(
        {
            "action": "include",
            "pattern": "**/.certificates/**",
            "require_match": True,
        }
    )
    _write_yaml(policy, document)
    with pytest.raises(BlueprintGraphError, match="certification artifact"):
        _states(root, policy)


def test_required_include_matching_only_mandatory_blueprint_still_fails(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    blueprint = "skills/provider-skill/blueprints/gateway.yaml"
    document = yaml.safe_load(policy.read_text(encoding="utf-8"))
    document["rules"].append(
        {
            "action": "include",
            "pattern": blueprint,
            "require_match": True,
        }
    )
    _write_yaml(policy, document)
    assert certification_hashing._git_exclude_matches(  # type: ignore[attr-defined]
        root,
        (blueprint,),
        blueprint,
    ) == {blueprint}

    with pytest.raises(
        CertificationHashError,
        match="requires at least one match",
    ):
        _states(root, policy)


def test_excluding_mandatory_blueprint_still_fails(tmp_path: Path) -> None:
    root, policy = _repository(tmp_path)
    blueprint = "skills/provider-skill/blueprints/gateway.yaml"
    document = yaml.safe_load(policy.read_text(encoding="utf-8"))
    document["rules"].append(
        {
            "action": "exclude",
            "pattern": blueprint,
        }
    )
    _write_yaml(policy, document)

    with pytest.raises(
        CertificationHashError,
        match="mandatory blueprint, gateway, or contract input cannot be excluded",
    ):
        _states(root, policy)


def test_git_policy_matcher_covers_tracked_ignored_and_untracked_files(
    tmp_path: Path,
) -> None:
    root, _policy = _repository(tmp_path)
    tracked = "skills/provider-skill/_rtx/worker.py"
    ignored = "skills/provider-skill/ignored.txt"
    untracked = "skills/provider-skill/notes.tmp"
    (root / untracked).write_text("notes\n", encoding="utf-8")
    candidates = (tracked, ignored, untracked)
    assert [git_file_provenance(root, root / path) for path in candidates] == [
        "tracked",
        "ignored",
        "untracked",
    ]

    cases = {
        "/skills/provider-skill/_rtx/worker.py": {tracked},
        "skills/provider-skill/ignored.txt": {ignored},
        "skills/provider-skill/_rtx/": {tracked},
        "skills/**/notes.tmp": {untracked},
    }
    assert {
        pattern: certification_hashing._git_exclude_matches(  # type: ignore[attr-defined]
            root, candidates, pattern
        )
        for pattern in cases
    } == cases


def test_v4_hashing_batches_git_provenance_and_policy_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, policy = _repository(tmp_path)
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)
    document = yaml.safe_load(policy.read_text(encoding="utf-8"))
    rules = document["rules"]
    git_commands: list[list[str]] = []
    real_run = subprocess.run

    def counting_run(
        command: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if command and command[0] == "git":
            git_commands.append(command)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting_run)

    compute_node_hash_states(
        graph,
        repo_root=root,
        policy_path=policy,
        certification_basis_hash="sha256:" + "b" * 64,
        certification_basis_paths=(),
    )

    assert len(git_commands) <= len(rules) + 2


def test_v4_hashing_wraps_fatal_batch_provenance_as_certification_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, policy = _repository(tmp_path)
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)

    def fatal_tracked_query(
        _repo_root: Path,
        *args: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args,
            128,
            b"",
            b"fatal: node provenance failed\n",
        )

    monkeypatch.setattr(git_provenance, "run_git", fatal_tracked_query)

    with pytest.raises(
        CertificationHashError,
        match="cannot determine Git provenance",
    ) as error:
        compute_node_hash_states(
            graph,
            repo_root=root,
            policy_path=policy,
            certification_basis_hash="sha256:" + "b" * 64,
            certification_basis_paths=(),
        )

    assert isinstance(error.value.__cause__, ValueError)
    assert "fatal: node provenance failed" in str(error.value.__cause__)


def test_route_smoke_paths_map_to_input_dependency_or_basis(tmp_path: Path) -> None:
    root, policy = _repository(tmp_path)
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)
    states = _states(root, policy)
    basis_path = root / "src" / "officina" / "runtime" / "support.py"
    basis_path.parent.mkdir(parents=True)
    basis_path.write_text("VALUE = 1\n", encoding="utf-8")
    provider_path = root / "skills" / "provider-skill" / "_rtx" / "worker.py"

    mappings = map_route_smoke_dependencies(
        graph,
        states,
        source_node_id="consumer-skill.source.gateway",
        loaded_paths=[
            provider_path,
            basis_path,
            root / "skills" / "consumer-skill" / "_rtx" / "worker.py",
        ],
        certification_basis_paths=[basis_path, provider_path],
        repo_root=root,
    )

    assert [
        (mapping.path, mapping.authority, mapping.target_node_id)
        for mapping in mappings
    ] == [
        (
            "skills/consumer-skill/_rtx/worker.py",
            "direct-input",
            "consumer-skill.source.gateway",
        ),
        (
            "skills/provider-skill/_rtx/worker.py",
            "certification-dependency",
            "provider-skill.source.gateway",
        ),
        (
            "src/officina/runtime/support.py",
            "certification-basis",
            None,
        ),
    ]
    assert route_smoke_trace_signature(mappings) == route_smoke_trace_signature(
        tuple(reversed(mappings))
    )


def test_v6_route_smoke_accepts_manifest_bound_interface_dependency(
    tmp_path: Path,
) -> None:
    root, policy = _v6_repository(tmp_path)
    graph = load_repository_blueprint_graph(
        root,
        schema_root=CANONICAL_SCHEMA_ROOT,
        expected_schema_version=6,
    )
    states = _v6_states(root, policy)
    provider_path = root / "skills/provider-skill/_rtx/worker.py"

    mappings = map_route_smoke_dependencies(
        graph,
        states,
        source_node_id="consumer-skill.source.gateway",
        loaded_paths=[provider_path],
        certification_basis_paths=(),
        repo_root=root,
    )

    assert mappings == (
        certification_hashing.RouteSmokeDependencyMapping(
            "skills/provider-skill/_rtx/worker.py",
            "certification-dependency",
            "provider-skill.source.gateway",
        ),
    )


def test_route_smoke_maps_transitive_contract_only_dependency(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    module = root / "skills" / "provider-skill"
    contracts = module / "contracts"
    contracts.mkdir()
    (contracts / "root.schema.json").write_text(
        '{"type":"string"}\n', encoding="utf-8"
    )
    child = contracts / "child.schema.json"
    child.write_text('{"type":"string"}\n', encoding="utf-8")
    contract_b = _add_contract_source(
        root,
        "provider-skill",
        "contract-b",
        "contracts/child.schema.json",
    )
    contract_a = _add_contract_source(
        root,
        "provider-skill",
        "contract-a",
        "contracts/root.schema.json",
        referenced_path="contracts/child.schema.json",
    )
    source_path = module / "blueprints" / "gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    interface = source["interfaces"]["provider-skill.source.gateway.interface.run"]
    _set_output_schema(interface["contract"], "contracts/root.schema.json")
    _write_yaml(source_path, source)

    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)
    states = _states(root, policy)
    gateway_id = "provider-skill.source.gateway"
    assert {
        dependency["target"]
        for dependency in states[gateway_id].dependency_hashes
        if dependency["relation"] == "references-cross-owner-contract"
    } == {contract_a}
    assert {
        dependency["target"]
        for dependency in states[contract_a].dependency_hashes
        if dependency["relation"] == "references-cross-owner-contract"
    } == {contract_b}
    mappings = map_route_smoke_dependencies(
        graph,
        states,
        source_node_id=gateway_id,
        loaded_paths=[child],
        certification_basis_paths=[],
        repo_root=root,
    )

    assert mappings == (
        certification_hashing.RouteSmokeDependencyMapping(
            "skills/provider-skill/contracts/child.schema.json",
            "certification-dependency",
            contract_b,
        ),
    )


@pytest.mark.parametrize(
    "dependency_hashes",
    [
        ({"relation": "references-cross-owner-contract"},),
        (
            {
                "relation": "references-cross-owner-contract",
                "target": "missing.source.contract",
                "version": 1,
                "node_hash": "sha256:" + "a" * 64,
            },
        ),
    ],
)
def test_route_smoke_rejects_invalid_dependency_state_shape_or_target(
    tmp_path: Path,
    dependency_hashes: tuple[dict[str, object], ...],
) -> None:
    root, policy = _repository(tmp_path)
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)
    states = _states(root, policy)
    source_id = "consumer-skill.source.gateway"
    states[source_id] = replace(
        states[source_id], dependency_hashes=dependency_hashes
    )

    with pytest.raises(CertificationHashError, match="invalid dependency hash"):
        map_route_smoke_dependencies(
            graph,
            states,
            source_node_id=source_id,
            loaded_paths=[root / "skills" / "consumer-skill" / "_rtx" / "worker.py"],
            certification_basis_paths=[],
            repo_root=root,
        )


def test_route_smoke_rejects_unmapped_loaded_path(tmp_path: Path) -> None:
    root, policy = _repository(tmp_path)
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)
    states = _states(root, policy)
    unmapped = root / "tools" / "unmapped.py"
    unmapped.parent.mkdir()
    unmapped.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(CertificationHashError, match="unmapped route-smoke dependency"):
        map_route_smoke_dependencies(
            graph,
            states,
            source_node_id="consumer-skill.source.gateway",
            loaded_paths=[unmapped],
            certification_basis_paths=[],
            repo_root=root,
        )


def test_v5_route_smoke_maps_runtime_package_init_to_containing_module(
    tmp_path: Path,
) -> None:
    repository = GitTestRepository.initialize_existing_empty(tmp_path)
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "modules",
        tmp_path / "modules",
    )
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "skills",
        tmp_path / "skills",
    )
    policy = tmp_path / "node-hash-policy.yaml"
    _write_yaml(
        policy,
        {
            "policy_version": 1,
            "path_syntax": "gitignore",
            "starting_set": "git-tracked-directly-owned-regular-files",
            "rules": [{"action": "exclude", "pattern": "**/.certificates/**"}],
        },
    )
    repository.git("add", ".")
    repository.git("commit", "-qm", "v5 fixture")
    graph = load_repository_blueprint_graph(
        tmp_path,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )
    states = compute_node_hash_states(
        graph,
        repo_root=tmp_path,
        policy_path=policy,
        certification_basis_hash="sha256:" + "b" * 64,
    )

    mappings = map_route_smoke_dependencies(
        graph,
        states,
        source_node_id="demo-rtx.source.runtime",
        loaded_paths=[
            tmp_path / "skills" / "demo" / "_rtx" / "__init__.py",
            tmp_path / "skills" / "demo" / "_rtx" / "runtime.py",
        ],
        certification_basis_paths=[],
        repo_root=tmp_path,
    )

    assert [
        (mapping.path, mapping.authority, mapping.target_node_id)
        for mapping in mappings
    ] == [
        (
            "skills/demo/_rtx/__init__.py",
            "module-package-input",
            "demo-rtx",
        ),
        (
            "skills/demo/_rtx/runtime.py",
            "direct-input",
            "demo-rtx.source.runtime",
        ),
    ]

    dependency_mappings = map_route_smoke_dependencies(
        graph,
        states,
        source_node_id="demo.source.gateway",
        loaded_paths=[
            tmp_path / "skills" / "demo" / "_rtx" / "__init__.py",
            tmp_path / "skills" / "demo" / "_rtx" / "runtime.py",
        ],
        certification_basis_paths=[],
        repo_root=tmp_path,
    )

    assert [
        (mapping.path, mapping.authority, mapping.target_node_id)
        for mapping in dependency_mappings
    ] == [
        (
            "skills/demo/_rtx/__init__.py",
            "module-package-input",
            "demo-rtx",
        ),
        (
            "skills/demo/_rtx/runtime.py",
            "certification-dependency",
            "demo-rtx.source.runtime",
        ),
    ]

    dependency_mappings = map_route_smoke_dependencies(
        graph,
        states,
        source_node_id="demo.source.gateway",
        loaded_paths=[
            tmp_path / "skills" / "demo" / "_rtx" / "__init__.py",
        ],
        certification_basis_paths=[],
        repo_root=tmp_path,
    )

    assert [
        (mapping.path, mapping.authority, mapping.target_node_id)
        for mapping in dependency_mappings
    ] == [
        (
            "skills/demo/_rtx/__init__.py",
            "module-package-input",
            "demo-rtx",
        ),
    ]


def test_compute_node_hash_states_does_not_trace_route_smoke_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, policy = _repository(tmp_path)
    _make_python_gateway(root, "provider-skill", import_unowned=False)
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)

    def reject_trace(*_args: object) -> tuple[Path, ...]:
        pytest.fail("node hashing launched a route-smoke dependency trace")

    monkeypatch.setattr(
        python_interface,
        "trace_python_route_smoke_dependencies_batch",
        reject_trace,
    )

    states = compute_node_hash_states(
        graph,
        repo_root=root,
        policy_path=policy,
        certification_basis_hash="sha256:" + "b" * 64,
    )

    assert states["provider-skill.source.gateway"].node_hash is not None


def test_v4_hashing_makes_transitive_same_owner_contract_closure_mandatory(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    module = root / "skills" / "provider-skill"
    contracts = module / "contracts"
    contracts.mkdir()
    (contracts / "root.schema.json").write_text(
        '{"$ref":"child.schema.json"}\n', encoding="utf-8"
    )
    (contracts / "child.schema.json").write_text(
        '{"type":"string"}\n', encoding="utf-8"
    )
    source_path = module / "blueprints" / "gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["content"].extend(
        [r"contracts/root\.schema\.json", r"contracts/child\.schema\.json"]
    )
    interface = source["interfaces"]["provider-skill.source.gateway.interface.run"]
    _set_output_schema(interface["contract"], "contracts/root.schema.json")
    _write_yaml(source_path, source)
    module_path = module / "blueprint.yaml"
    declaration = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    declaration["content"].extend(
        [r"contracts/root\.schema\.json", r"contracts/child\.schema\.json"]
    )
    _write_yaml(module_path, declaration)

    state = _states(root, policy)["provider-skill.source.gateway"]

    assert {
        "skills/provider-skill/contracts/root.schema.json",
        "skills/provider-skill/contracts/child.schema.json",
    } <= {entry["path"] for entry in state.input_manifest}


def test_v4_hashing_attributes_transitive_contract_files_to_direct_owner(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    module = root / "skills" / "provider-skill"
    contracts = module / "contracts"
    contracts.mkdir()
    (contracts / "root.schema.json").write_text(
        '{"$ref":"child.schema.json"}\n', encoding="utf-8"
    )
    (contracts / "child.schema.json").write_text(
        '{"type":"string"}\n', encoding="utf-8"
    )
    contract_source = _add_contract_source(
        root,
        "provider-skill",
        "contracts",
        "contracts/root.schema.json",
        content_paths=("contracts/child.schema.json",),
    )
    source_path = module / "blueprints" / "gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    interface = source["interfaces"]["provider-skill.source.gateway.interface.run"]
    _set_output_schema(interface["contract"], "contracts/root.schema.json")
    _write_yaml(source_path, source)

    states = _states(root, policy)

    assert {
        "skills/provider-skill/contracts/root.schema.json",
        "skills/provider-skill/contracts/child.schema.json",
    } <= {entry["path"] for entry in states[contract_source].input_manifest}
    assert any(
        dependency["relation"] == "references-cross-owner-contract"
        and dependency["target"] == contract_source
        for dependency in states["provider-skill.source.gateway"].dependency_hashes
    )


def test_v4_hashing_rejects_cycle_after_cross_owner_contract_edges(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    module = root / "skills" / "provider-skill"
    contracts = module / "contracts"
    contracts.mkdir()
    (contracts / "a.json").write_text("{}\n", encoding="utf-8")
    (contracts / "b.json").write_text("{}\n", encoding="utf-8")
    _add_contract_source(
        root,
        "provider-skill",
        "contract-a",
        "contracts/a.json",
        "contracts/b.json",
    )
    _add_contract_source(
        root,
        "provider-skill",
        "contract-b",
        "contracts/b.json",
        "contracts/a.json",
    )

    with pytest.raises(CertificationHashError, match="certification dependency cycle"):
        _states(root, policy)
