from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import officina.common.blueprint_graph as blueprint_graph
from officina.common.blueprint_graph import (
    BlueprintGraphError,
    BlueprintNode,
    CertificationEdge,
    InterfaceExport,
    load_repository_blueprint_graph,
    resolved_node_content_paths,
    resolve_export,
    validate_runtime_file_path,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint"


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _v4_contract(*, helper: dict[str, object] | None = None) -> dict[str, object]:
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
            "consistency": {"snapshot": "One invocation snapshot."},
            "verification": [{"method": "output-schema", "output_ref": "result"}],
        },
        "helpers": [helper] if helper is not None else [],
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


def _write_v4_module(
    root: Path,
    module_id: str,
    *,
    caller_export: str | None = None,
    allow_callers: list[str] | None = None,
    interface_version: int = 1,
) -> None:
    module = root / "skills" / module_id
    (module / "_rtx").mkdir(parents=True)
    (module / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (module / "README.md").write_text("Module-owned.\n", encoding="utf-8")
    (module / "_rtx" / "worker.py").write_text(
        "class Interface:\n    pass\n", encoding="utf-8"
    )
    gateway_source_id = f"{module_id}.source.gateway"
    worker_source_id = f"{module_id}.source.worker"
    run_source_interface = f"{worker_source_id}.interface.run"
    uses = (
        [{"interface": caller_export, "version": interface_version}]
        if caller_export is not None
        else []
    )
    _write_yaml(
        module / "blueprints" / "gateway.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": gateway_source_id,
            "version": 1,
            "description": "Primary instructions.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
            "dependencies": [],
            "uses_interfaces": uses,
            "interfaces": {},
        },
    )
    _write_yaml(
        module / "blueprints" / "worker.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": worker_source_id,
            "version": 1,
            "description": "Python worker.",
            "gateway": {"path": "_rtx/worker.py", "language": "Python>=3.11"},
            "content": [r"_rtx/worker\.py"],
            "platform_support": {"linux": True, "macos": True, "windows": True},
            "runtime_dependencies": [],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {
                run_source_interface: {
                    "version": interface_version,
                    "description": "Run the worker.",
                    "contract": _v4_contract(),
                    "process_binding": {
                        "kind": "process",
                        "entry": "Interface",
                        "arguments": {},
                        "fixed": [],
                    },
                }
            },
        },
    )
    exports: dict[str, object] = {}
    if allow_callers is not None:
        exports[f"{module_id}.interface.run"] = {
            "source_interface": run_source_interface,
            "access": {
                "allow_all_modules": not allow_callers,
                "allowed_callers": allow_callers,
            },
        }
    _write_yaml(
        module / "blueprint.yaml",
        {
            "schema_version": 4,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            "description": f"{module_id} module.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md", r"README\.md", r"_rtx/worker\.py"],
            "authority": {"owns_filesystem": []},
            "sources": {
                gateway_source_id: {
                    "blueprint": {"base": "module-root", "path": "blueprints/gateway.yaml"}
                },
                worker_source_id: {
                    "blueprint": {"base": "module-root", "path": "blueprints/worker.yaml"}
                },
            },
            "exports": exports,
        },
    )


def test_v4_schema_loading_does_not_require_posix_runtime_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path, "demo-skill")
    monkeypatch.setattr(
        blueprint_graph,
        "_descriptor_safe_open_supported",
        lambda: False,
    )

    graph = load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)

    assert "demo-skill" in graph.nodes
    node = graph.nodes["demo-skill"]
    assert node.gateway_path is not None
    assert validate_runtime_file_path(
        node.gateway_path,
        node.skill_root,
        tmp_path,
    ) == node.gateway_path


def test_content_ownership_accepts_equivalent_repository_alias(
    tmp_path: Path,
) -> None:
    physical_parent = tmp_path / "physical"
    repository = physical_parent / "repository"
    module = repository / "skills" / "demo-skill"
    module.mkdir(parents=True)
    content = module / "SKILL.md"
    content.write_text("Instructions.\n", encoding="utf-8")
    alias_parent = tmp_path / "alias"
    try:
        alias_parent.symlink_to(physical_parent, target_is_directory=True)
    except OSError:
        # famulus-skip: category=platform-contract; reason=some Windows runners deny directory-symlink creation; alternate=Linux and macOS exercise the repository-alias regression
        pytest.skip("directory symlinks are unavailable")
    node = BlueprintNode(
        node_id="demo-skill",
        node_type="module",
        version=1,
        skill_root=module,
        blueprint_path=module / "blueprint.yaml",
        gateway_path=content,
        declaration={
            "schema_version": 4,
            "content": [r"SKILL\.md"],
        },
    )

    assert resolved_node_content_paths(
        node,
        alias_parent / "repository",
    ) == (content,)
    assert validate_runtime_file_path(
        content,
        module,
        alias_parent / "repository",
    ) == content


def test_v4_repository_graph_uses_one_generic_export_and_direct_ownership(
    tmp_path: Path,
) -> None:
    _write_v4_module(
        tmp_path,
        "provider-skill",
        allow_callers=["consumer-skill"],
    )
    _write_v4_module(
        tmp_path,
        "consumer-skill",
        caller_export="provider-skill.interface.run",
        allow_callers=None,
    )

    graph = load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)
    module, source, export = resolve_export(
        graph, "provider-skill.interface.run", 1
    )

    assert isinstance(export, InterfaceExport)
    assert not hasattr(graph, "machine_exports")
    assert not hasattr(blueprint_graph, "MachineInterfaceExport")
    assert module.node_id == "provider-skill"
    assert source.node_id == "provider-skill.source.worker"
    assert export.source_interface_id == (
        "provider-skill.source.worker.interface.run"
    )
    assert export.version == 1
    assert export.declaration is source.declaration["interfaces"][export.source_interface_id]
    assert graph.direct_file_owners[
        tmp_path / "skills" / "provider-skill" / "SKILL.md"
    ] == "provider-skill.source.gateway"
    assert graph.direct_file_owners[
        tmp_path / "skills" / "provider-skill" / "README.md"
    ] == "provider-skill"
    assert set(graph.module_sources["provider-skill"]) == {
        "provider-skill.source.gateway",
        "provider-skill.source.worker",
    }
    certification_edges = {
        (edge.relation, edge.source_node_id, edge.target_node_id)
        for edge in graph.certification_edges
    }
    assert not any(
        relation == "contains-source"
        for relation, _source, _target in certification_edges
    )
    assert certification_edges >= {
        (
            "uses-export",
            "consumer-skill.source.gateway",
            "provider-skill.source.worker",
        ),
    }
    assert all(isinstance(edge, CertificationEdge) for edge in graph.certification_edges)


@pytest.mark.parametrize("schema_version", [2, 3])
def test_repository_graph_rejects_pre_v4_documents(
    tmp_path: Path,
    schema_version: int,
) -> None:
    _write_v4_module(tmp_path, "provider-skill", allow_callers=[])
    _write_yaml(
        tmp_path
        / "skills"
        / "provider-skill"
        / "blueprints"
        / "legacy.yaml",
        {
            "schema_version": schema_version,
            "node_type": "behavior" "-source",
            "id": "provider-skill.source.legacy",
            "version": 1,
        },
    )

    with pytest.raises(BlueprintGraphError, match="requires schema_version 4"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


def test_load_module_blueprint_is_exact_and_ignores_invalid_siblings(
    tmp_path: Path,
) -> None:
    _write_v4_module(tmp_path, "target-skill", allow_callers=[])
    sibling = tmp_path / "skills" / "invalid-sibling"
    sibling.mkdir()
    (sibling / "blueprint.yaml").write_text("not: [valid\n", encoding="utf-8")

    node = blueprint_graph.load_module_blueprint(
        tmp_path,
        tmp_path / "skills" / "target-skill",
        schema_root=SCHEMA_ROOT,
    )

    assert node.node_id == "target-skill"
    assert node.node_type == "module"
    assert node.skill_root == tmp_path / "skills" / "target-skill"
    assert node.declaration["schema_version"] == 4


def test_v4_same_module_export_dependency_targets_its_source_without_cycle(
    tmp_path: Path,
) -> None:
    _write_v4_module(
        tmp_path,
        "provider-skill",
        caller_export="provider-skill.interface.run",
        allow_callers=[],
        interface_version=2,
    )

    graph = load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)

    assert (
        "uses-export",
        "provider-skill.source.gateway",
        "provider-skill.source.worker",
        1,
    ) in {
        (
            edge.relation,
            edge.source_node_id,
            edge.target_node_id,
            edge.target_version,
        )
        for edge in graph.certification_edges
    }


def test_runtime_authority_uses_generic_v4_export_edges(tmp_path: Path) -> None:
    _write_v4_module(
        tmp_path,
        "provider-skill",
        allow_callers=["consumer-skill"],
    )
    _write_v4_module(tmp_path, "consumer-skill", allow_callers=[])
    source_path = (
        tmp_path / "skills" / "consumer-skill" / "blueprints" / "worker.yaml"
    )
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["uses_interfaces"] = [
        {"interface": "provider-skill.interface.run", "version": 1}
    ]
    _write_yaml(source_path, source)

    graph = load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)

    assert blueprint_graph.runtime_authority_for_export(
        graph,
        "consumer-skill.interface.run",
    ) == ("provider-skill.interface.run",)


@pytest.mark.parametrize("node_type", [None, "invented-node"])
def test_v4_repository_graph_validates_claimed_v4_documents_before_filtering(
    tmp_path: Path,
    node_type: str | None,
) -> None:
    _write_v4_module(tmp_path, "provider-skill", allow_callers=[])
    rogue = {
        "schema_version": 4,
        "id": "provider-skill.rogue",
    }
    if node_type is not None:
        rogue["node_type"] = node_type
    _write_yaml(
        tmp_path / "skills" / "provider-skill" / "blueprints" / "rogue.yaml",
        rogue,
    )

    with pytest.raises(BlueprintGraphError, match="unsupported typed node type"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


def test_v4_process_pattern_accepts_short_flags(tmp_path: Path) -> None:
    _write_v4_module(tmp_path, "provider-skill", allow_callers=[])
    source_path = tmp_path / "skills" / "provider-skill" / "blueprints" / "worker.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    interface = source["interfaces"][
        "provider-skill.source.worker.interface.run"
    ]
    interface["process_binding"]["patterns"] = [
        {
            "min_positionals": 0,
            "max_positionals": 0,
            "required_flags": ["-a"],
            "allowed_flags": ["-a"],
            "flag_patterns": {"-a": "^.+$"},
        }
    ]
    _write_yaml(source_path, source)

    graph = load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)

    assert graph.nodes["provider-skill.source.worker"].declaration["interfaces"][
        "provider-skill.source.worker.interface.run"
    ]["process_binding"]["patterns"][0]["required_flags"] == ["-a"]


@pytest.mark.parametrize(
    ("case", "match"),
    [
        ("output-direct-io", "unknown direct-I/O 'missing'"),
        ("outcome-output", "unknown output 'missing'"),
        ("precondition-argument", "unknown argument 'missing'"),
        ("verification-output", "unknown output 'missing'"),
        ("argument-helper", "unknown helper 'missing'"),
        ("effect-direct-io", "unknown direct-I/O 'missing'"),
        ("effect-inverse", "outcome/effect references must be exact inverses"),
        ("unsafe-path", "path must be relative without parent traversal"),
    ],
)
def test_v4_repository_graph_rejects_invalid_local_contract_references(
    tmp_path: Path,
    case: str,
    match: str,
) -> None:
    _write_v4_module(tmp_path, "provider-skill", allow_callers=[])
    source_path = tmp_path / "skills" / "provider-skill" / "blueprints" / "worker.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    contract = source["interfaces"][
        "provider-skill.source.worker.interface.run"
    ]["contract"]

    if case == "output-direct-io":
        contract["outputs"][0]["direct_io_ref"] = "missing"
    elif case == "outcome-output":
        contract["outcomes"][0]["outputs"] = ["missing"]
    elif case == "precondition-argument":
        contract["preconditions"] = [
            {
                "id": "ready",
                "description": "Required input exists.",
                "check": {
                    "kind": "argument",
                    "argument_ref": "missing",
                    "predicate": "present",
                },
                "unmet_outcome": "success",
                "caller_action": "Provide the input.",
            }
        ]
    elif case == "verification-output":
        contract["execution"]["verification"] = [
            {"method": "output-schema", "output_ref": "missing"}
        ]
    elif case == "argument-helper":
        contract["arguments"]["mode"] = {
            "description": "Mode.",
            "required": False,
            "sensitivity": "public",
            "type": {"kind": "enum", "values_from_helper": "missing"},
        }
    else:
        contract["execution"] = {
            "state_effect": "mutating",
            "lifecycle": "finite",
            "consistency": {"snapshot": "One invocation snapshot."},
            "effects": [
                {
                    "id": "changed",
                    "direct_io_ref": (
                        "missing" if case == "effect-direct-io" else "stdout"
                    ),
                    "action": "update",
                    "value_source": {
                        "kind": "direct-io",
                        "direct_io_ref": "stdout",
                    },
                    "may_occur_in_outcomes": ["success"],
                    "confirmation_evidence": {
                        "kind": "direct-io",
                        "direct_io_ref": "stdout",
                    },
                    "reversibility": {"irreversible": "No rollback is retained."},
                }
            ],
            "mutation_safety": {
                "atomicity": {"atomic": "One write."},
                "concurrent_invocations": {"safe": "Independent calls."},
                "idempotency": {"idempotent": "Repeated writes agree."},
                "on_uncertain_completion": {
                    "verify_then_decide": "Inspect the declared output."
                },
                "partial_effects_on_failure": {"impossible": "One atomic write."},
                "rollback_on_failure": {"unavailable": "No rollback is needed."},
            },
            "verification": [
                {"method": "direct-io-state", "direct_io_ref": "stdout"}
            ],
        }
        if case != "effect-inverse":
            contract["outcomes"][0]["effects"] = ["changed"]
        if case == "unsafe-path":
            contract["direct_io"]["writes"].append(
                {
                    "id": "state",
                    "medium": "local-filesystem",
                    "access": "write",
                    "content": "State.",
                    "formats": ["json"],
                    "sensitivity": "user-private",
                    "path": "../state.json",
                    "path_match": "exact",
                }
            )

    _write_yaml(source_path, source)

    with pytest.raises(BlueprintGraphError, match=match):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


def test_v4_repository_graph_rejects_write_into_another_module_authority(
    tmp_path: Path,
) -> None:
    _write_v4_module(tmp_path, "writer-skill", allow_callers=[])
    _write_v4_module(tmp_path, "owner-skill", allow_callers=[])
    writer_path = tmp_path / "skills" / "writer-skill" / "blueprints" / "worker.yaml"
    writer = yaml.safe_load(writer_path.read_text(encoding="utf-8"))
    contract = writer["interfaces"]["writer-skill.source.worker.interface.run"][
        "contract"
    ]
    contract["direct_io"]["writes"].append(
        {
            "id": "shared-state",
            "medium": "local-filesystem",
            "access": "write",
            "content": "Shared state.",
            "formats": ["json"],
            "sensitivity": "user-private",
            "path": "$home/.config/shared.json",
            "path_match": "exact",
        }
    )
    _write_yaml(writer_path, writer)
    owner_path = tmp_path / "skills" / "owner-skill" / "blueprint.yaml"
    owner = yaml.safe_load(owner_path.read_text(encoding="utf-8"))
    owner["authority"]["owns_filesystem"] = [
        {
            "match": "exact",
            "path": "$home/.config/shared.json",
            "allowed_readers": [],
        }
    ]
    _write_yaml(owner_path, owner)

    with pytest.raises(
        BlueprintGraphError,
        match=r"writer-skill.*write '\$home/.config/shared.json'.*owned by owner-skill",
    ):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


def test_v4_repository_graph_enforces_export_authorization_and_private_ownership(
    tmp_path: Path,
) -> None:
    _write_v4_module(
        tmp_path,
        "provider-skill",
        allow_callers=["different-skill"],
    )
    _write_v4_module(
        tmp_path,
        "consumer-skill",
        caller_export="provider-skill.interface.run",
        allow_callers=None,
    )

    with pytest.raises(BlueprintGraphError, match="consumer-skill.*not allowed"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)

    consumer = tmp_path / "skills" / "consumer-skill"
    source_path = consumer / "blueprints" / "gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["uses_interfaces"] = [
        {
            "interface": "provider-skill.source.worker.interface.run",
            "version": 1,
        }
    ]
    _write_yaml(source_path, source)
    with pytest.raises(BlueprintGraphError, match="private interface.*cross-module"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


@pytest.mark.parametrize(
    ("locator", "make_symlink", "match"),
    [
        ("blueprints/missing.yaml", False, "does not identify its canonical blueprint"),
        ("../outside.yaml", False, "does not match"),
        ("blueprints/linked.yaml", True, "must resolve under module-root"),
    ],
)
def test_v4_source_locator_is_confined_and_exact(
    tmp_path: Path,
    locator: str,
    make_symlink: bool,
    match: str,
) -> None:
    _write_v4_module(tmp_path, "demo-skill", allow_callers=[])
    module = tmp_path / "skills" / "demo-skill"
    if make_symlink:
        outside = tmp_path / "outside.yaml"
        outside.write_text("outside: true\n", encoding="utf-8")
        (module / locator).symlink_to(outside)
    blueprint_path = module / "blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["sources"]["demo-skill.source.worker"]["blueprint"]["path"] = locator
    _write_yaml(blueprint_path, blueprint)

    with pytest.raises(BlueprintGraphError, match=match):
        load_repository_blueprint_graph(tmp_path)


@pytest.mark.parametrize("gateway_state", ["missing", "directory", "symlink", "traversal"])
def test_v4_gateway_must_be_a_confined_regular_owned_file(
    tmp_path: Path,
    gateway_state: str,
) -> None:
    _write_v4_module(tmp_path, "demo-skill", allow_callers=[])
    module = tmp_path / "skills" / "demo-skill"
    worker = module / "_rtx" / "worker.py"
    source_path = module / "blueprints" / "worker.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if gateway_state == "missing":
        worker.unlink()
    elif gateway_state == "directory":
        worker.unlink()
        worker.mkdir()
    elif gateway_state == "symlink":
        outside = tmp_path / "outside.py"
        outside.write_text("VALUE = 1\n", encoding="utf-8")
        worker.unlink()
        worker.symlink_to(outside)
    else:
        outside = tmp_path / "outside.py"
        outside.write_text("VALUE = 1\n", encoding="utf-8")
        source["gateway"]["path"] = "../outside.py"
        _write_yaml(source_path, source)

    with pytest.raises(
        BlueprintGraphError,
        match="matched no files|does not match",
    ):
        load_repository_blueprint_graph(tmp_path)


def test_v4_source_content_is_contained_and_sibling_exclusive(
    tmp_path: Path,
) -> None:
    _write_v4_module(tmp_path, "demo-skill", allow_callers=[])
    module = tmp_path / "skills" / "demo-skill"
    extra = module / "_rtx" / "extra.py"
    extra.write_text("VALUE = 1\n", encoding="utf-8")
    worker_path = module / "blueprints" / "worker.yaml"
    worker = yaml.safe_load(worker_path.read_text(encoding="utf-8"))
    worker["content"].append(r"_rtx/extra\.py")
    _write_yaml(worker_path, worker)

    with pytest.raises(BlueprintGraphError, match="source content must be contained"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)

    module_path = module / "blueprint.yaml"
    module_blueprint = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module_blueprint["content"].append(r"_rtx/extra\.py")
    _write_yaml(module_path, module_blueprint)
    gateway_path = module / "blueprints" / "gateway.yaml"
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    gateway["content"].append(r"_rtx/worker\.py")
    _write_yaml(gateway_path, gateway)

    with pytest.raises(BlueprintGraphError, match="sibling sources.*overlap"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


def test_v4_certification_dependencies_are_unique_sorted_and_stable(
    tmp_path: Path,
) -> None:
    _write_v4_module(tmp_path, "demo-skill", allow_callers=[])
    module = tmp_path / "skills" / "demo-skill"
    gateway_path = module / "blueprints" / "gateway.yaml"
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    gateway["dependencies"] = [
        {
            "source": "demo-skill.source.worker",
            "version": 1,
            "blueprint": {
                "base": "module-root",
                "path": "blueprints/worker.yaml",
            },
            "reason": "Loads the worker implementation.",
        }
    ]
    gateway["uses_interfaces"] = [
        {
            "interface": "demo-skill.source.worker.interface.run",
            "version": 1,
        }
    ]
    _write_yaml(gateway_path, gateway)

    first = load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)
    second = load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)
    edge_keys = [
        (
            edge.source_node_id,
            edge.relation,
            edge.target_node_id,
            edge.target_version,
        )
        for edge in first.certification_edges
    ]

    assert first.certification_edges == second.certification_edges
    assert edge_keys == sorted(edge_keys)
    assert len(edge_keys) == len(set(edge_keys))
    assert edge_keys == [
        (
            "demo-skill.source.gateway",
            "uses-private-interface",
            "demo-skill.source.worker",
            1,
        ),
        (
            "demo-skill.source.gateway",
            "uses-source",
            "demo-skill.source.worker",
            1,
        ),
    ]


def test_v4_export_version_and_namespace_prevent_duplicate_public_ids(
    tmp_path: Path,
) -> None:
    _write_v4_module(tmp_path, "provider-skill", allow_callers=[])
    _write_v4_module(
        tmp_path,
        "consumer-skill",
        caller_export="provider-skill.interface.run",
        allow_callers=[],
        interface_version=2,
    )

    with pytest.raises(BlueprintGraphError, match="pins.*version 2.*version is 1"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)

    gateway_path = (
        tmp_path / "skills" / "consumer-skill" / "blueprints" / "gateway.yaml"
    )
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    gateway["uses_interfaces"][0]["version"] = 1
    _write_yaml(gateway_path, gateway)
    module_path = tmp_path / "skills" / "consumer-skill" / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["exports"]["provider-skill.interface.run"] = module["exports"].pop(
        "consumer-skill.interface.run"
    )
    _write_yaml(module_path, module)

    with pytest.raises(BlueprintGraphError, match="must use module namespace"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


def test_v4_cross_module_export_requires_compatible_platforms(
    tmp_path: Path,
) -> None:
    _write_v4_module(tmp_path, "provider-skill", allow_callers=[])
    _write_v4_module(
        tmp_path,
        "consumer-skill",
        caller_export="provider-skill.interface.run",
        allow_callers=[],
    )
    provider_path = (
        tmp_path / "skills" / "provider-skill" / "blueprints" / "worker.yaml"
    )
    provider = yaml.safe_load(provider_path.read_text(encoding="utf-8"))
    provider["platform_support"]["windows"] = False
    _write_yaml(provider_path, provider)
    consumer_path = (
        tmp_path / "skills" / "consumer-skill" / "blueprints" / "gateway.yaml"
    )
    consumer = yaml.safe_load(consumer_path.read_text(encoding="utf-8"))
    consumer["platform_support"] = {
        "linux": True,
        "macos": True,
        "windows": True,
    }
    consumer["runtime_dependencies"] = []
    _write_yaml(consumer_path, consumer)

    with pytest.raises(BlueprintGraphError, match="does not support.*windows"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


@pytest.mark.parametrize("cycle_kind", ["source", "export"])
def test_v4_repository_graph_rejects_certification_and_export_cycles(
    tmp_path: Path,
    cycle_kind: str,
) -> None:
    _write_v4_module(tmp_path, "first-skill", allow_callers=[])
    _write_v4_module(tmp_path, "second-skill", allow_callers=[])
    first_path = (
        tmp_path / "skills" / "first-skill" / "blueprints" / "worker.yaml"
    )
    second_path = (
        tmp_path / "skills" / "second-skill" / "blueprints" / "worker.yaml"
    )
    first = yaml.safe_load(first_path.read_text(encoding="utf-8"))
    second = yaml.safe_load(second_path.read_text(encoding="utf-8"))
    if cycle_kind == "export":
        first["uses_interfaces"] = [
            {"interface": "second-skill.interface.run", "version": 1}
        ]
        second["uses_interfaces"] = [
            {"interface": "first-skill.interface.run", "version": 1}
        ]
    else:
        first["dependencies"] = [
            {
                "source": "second-skill.source.worker",
                "version": 1,
                "blueprint": {
                    "base": "repository-root",
                    "path": "skills/second-skill/blueprints/worker.yaml",
                },
                "reason": "Loads the second worker.",
            }
        ]
        second["dependencies"] = [
            {
                "source": "first-skill.source.worker",
                "version": 1,
                "blueprint": {
                    "base": "repository-root",
                    "path": "skills/first-skill/blueprints/worker.yaml",
                },
                "reason": "Loads the first worker.",
            }
        ]
    _write_yaml(first_path, first)
    _write_yaml(second_path, second)

    with pytest.raises(BlueprintGraphError, match="certification dependency cycle"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)
