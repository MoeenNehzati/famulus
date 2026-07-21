from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess

import pytest
import yaml

import officina.common.artifact_health as artifact_health
from officina.common.artifact_health import (
    ArtifactHealthError,
    NodeHashState,
    compute_node_hash_states,
    map_route_smoke_dependencies,
    route_smoke_trace_signature,
)
from officina.common.blueprint_graph import load_repository_blueprint_graph
from officina.common.git_provenance import git_file_provenance


SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint"


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
                    "format": "text",
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
) -> None:
    module = root / "skills" / module_id
    (module / "_rtx").mkdir(parents=True)
    (module / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (module / "README.md").write_text("Module notes.\n", encoding="utf-8")
    (module / "_rtx" / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (module / "ignored.txt").write_text("included local state\n", encoding="utf-8")
    (module / "events.log").write_text("runtime log\n", encoding="utf-8")
    source_id = f"{module_id}.source.gateway"
    source_interface = f"{source_id}.interface.run"
    _write_yaml(
        module / "blueprints" / "gateway.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": 1,
            "description": "Gateway source.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [
                r"SKILL\.md",
                r"_rtx/worker\.py",
                r"ignored\.txt",
                r"events\.log",
            ],
            "dependencies": [],
            "uses_interfaces": (
                [{"interface": uses_export, "version": 1}]
                if uses_export is not None
                else []
            ),
            "interfaces": {
                source_interface: {
                    "version": 1,
                    "description": "Run.",
                    "contract": _contract(),
                }
            },
        },
    )
    _write_yaml(
        module / "blueprint.yaml",
        {
            "schema_version": 4,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            "description": "Module.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [
                r"SKILL\.md",
                r"README\.md",
                r"_rtx/worker\.py",
                r"ignored\.txt",
                r"events\.log",
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
            "exports": {
                f"{module_id}.interface.run": {
                    "source_interface": source_interface,
                    "access": {"allow_all_modules": True, "allowed_callers": []},
                }
            },
        },
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repository(tmp_path: Path) -> tuple[Path, Path]:
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
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Tests")
    _git(tmp_path, "config", "user.email", "tests@example.invalid")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
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


def _python_certification_basis_paths() -> tuple[Path, ...]:
    source_root = Path(artifact_health.__file__).resolve().parents[2]
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
) -> str:
    module_root = root / "skills" / module_id
    source_id = f"{module_id}.source.{source_name}"
    interface_id = f"{source_id}.interface.read"
    contract = _contract()
    if referenced_path is not None:
        _set_output_schema(contract, referenced_path)
    source = {
        "schema_version": 4,
        "node_type": "behavioral_source",
        "id": source_id,
        "version": 1,
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

    (root / "skills" / "provider-skill" / "README.md").write_text(
        "Changed module notes.\n", encoding="utf-8"
    )
    second = _states(root, policy)

    assert second[consumer_id].node_hash == first[consumer_id].node_hash
    assert second[consumer_id].dependency_hashes != first[consumer_id].dependency_hashes
    assert second["provider-skill"].node_hash != first["provider-skill"].node_hash


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
    with pytest.raises(ArtifactHealthError, match="reserved certification output"):
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
        pattern: artifact_health._git_exclude_matches(  # type: ignore[attr-defined]
            root, candidates, pattern
        )
        for pattern in cases
    } == cases


def test_route_smoke_paths_map_to_input_dependency_or_basis(tmp_path: Path) -> None:
    root, policy = _repository(tmp_path)
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)
    states = _states(root, policy)
    basis_path = root / "src" / "officina" / "runtime" / "support.py"
    basis_path.parent.mkdir(parents=True)
    basis_path.write_text("VALUE = 1\n", encoding="utf-8")

    mappings = map_route_smoke_dependencies(
        graph,
        states,
        source_node_id="consumer-skill.source.gateway",
        loaded_paths=[
            root / "skills" / "provider-skill" / "_rtx" / "worker.py",
            basis_path,
            root / "skills" / "consumer-skill" / "_rtx" / "worker.py",
        ],
        certification_basis_paths=[basis_path],
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
        artifact_health.RouteSmokeDependencyMapping(
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

    with pytest.raises(ArtifactHealthError, match="invalid dependency hash"):
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

    with pytest.raises(ArtifactHealthError, match="unmapped route-smoke dependency"):
        map_route_smoke_dependencies(
            graph,
            states,
            source_node_id="consumer-skill.source.gateway",
            loaded_paths=[unmapped],
            certification_basis_paths=[],
            repo_root=root,
        )


def test_v4_hash_preparation_rejects_unmapped_route_smoke_dependency(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    _make_python_gateway(root, "provider-skill", import_unowned=True)

    with pytest.raises(ArtifactHealthError, match="unmapped route-smoke dependency"):
        _states(
            root,
            policy,
            certification_basis_paths=_python_certification_basis_paths(),
        )


def test_v4_hash_preparation_accepts_stable_mapped_route_smoke_trace(
    tmp_path: Path,
) -> None:
    root, policy = _repository(tmp_path)
    _make_python_gateway(root, "provider-skill", import_unowned=False)

    states = _states(
        root,
        policy,
        certification_basis_paths=_python_certification_basis_paths(),
    )

    assert states["provider-skill.source.gateway"].node_hash is not None


def test_v4_hash_preparation_rejects_pre_post_route_smoke_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, policy = _repository(tmp_path)
    _make_python_gateway(root, "provider-skill", import_unowned=False)
    worker = root / "skills" / "provider-skill" / "_rtx" / "worker.py"
    source_blueprint = (
        root / "skills" / "provider-skill" / "blueprints" / "gateway.yaml"
    )
    traces = iter(((worker,), (worker, source_blueprint)))
    monkeypatch.setattr(
        artifact_health,
        "trace_python_route_smoke_dependencies",
        lambda *_args: next(traces),
    )

    with pytest.raises(ArtifactHealthError, match="route-smoke dependency trace changed"):
        _states(root, policy)


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

    with pytest.raises(ArtifactHealthError, match="certification dependency cycle"):
        _states(root, policy)
