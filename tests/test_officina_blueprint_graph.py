from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import officina.blueprints.graph as blueprint_graph
from officina.blueprints.graph import (
    BlueprintDiagnostic,
    BlueprintGraphError,
    BlueprintNode,
    CertificationEdge,
    InterfaceExport,
    load_dispatch_blueprint_graph,
    load_repository_blueprint_graph,
    resolved_node_content_paths,
    resolve_export,
    validate_runtime_file_path,
)
from test_support.v5_blueprint_fixtures import copy_v5_fixture_tree


CANONICAL_SCHEMA_ROOT = (
    Path(__file__).resolve().parents[1] / "references" / "blueprint"
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
    """Keep frozen-v4 graph cases explicit inside this mixed test module."""

    if schema_root is None:
        schema_root = {
            4: SCHEMA_ROOT,
            5: V5_SCHEMA_ROOT,
            6: CANONICAL_SCHEMA_ROOT,
        }[expected_schema_version]

    return _canonical_load_repository_blueprint_graph(
        repo_root,
        schema_root=schema_root,
        expected_schema_version=expected_schema_version,
    )


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _copy_v5_authorization_fixture(tmp_path: Path) -> Path:
    return copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE,
        tmp_path / "repo",
    )


def test_dispatch_scoped_graph_warns_for_unrelated_invalid_module(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    outsider_source = root / "modules" / "outsider" / "blueprints" / "caller.yaml"
    declaration = yaml.safe_load(outsider_source.read_text(encoding="utf-8"))
    declaration["uses_interfaces"] = [
        {"interface": "missing.interface.run", "version": 1}
    ]
    _write_yaml(outsider_source, declaration)

    result = load_dispatch_blueprint_graph(
        root,
        caller_module_id="demo",
        interface_id="demo.interface.execute",
        schema_root=V5_SCHEMA_ROOT,
    )

    assert "demo.interface.execute" in result.graph.exports
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0] == BlueprintDiagnostic(
        code="unrelated-blueprint-invalid",
        message=(
            "outsider.source.caller: unresolved interface "
            "'missing.interface.run'"
        ),
        path=None,
    )


def test_dispatch_scoped_graph_includes_absolute_access_policy_callers(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    runtime_module = root / "skills" / "demo" / "_rtx" / "blueprint.yaml"
    declaration = yaml.safe_load(runtime_module.read_text(encoding="utf-8"))
    declaration["exports"]["demo-rtx.interface.execute"]["access"][
        "allowed_callers"
    ].append("beta")
    _write_yaml(runtime_module, declaration)
    runtime_source = (
        root / "skills" / "demo" / "_rtx" / "blueprints" / "runtime.yaml"
    )
    runtime = yaml.safe_load(runtime_source.read_text(encoding="utf-8"))
    runtime["dependencies"] = [
        {
            "source": "dependency.source.runtime",
            "version": 1,
            "reason": "Exercise transitive source dependency closure.",
            "blueprint": {
                "base": "repository-root",
                "path": "modules/dependency/blueprints/runtime.yaml",
            },
        }
    ]
    _write_yaml(runtime_source, runtime)
    broken_root = root / "modules" / "broken"
    (broken_root / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (broken_root / "README.md").write_text("Broken module.\n", encoding="utf-8")
    (broken_root / "caller.py").write_text("pass\n", encoding="utf-8")
    _write_yaml(
        broken_root / "blueprint.yaml",
        {
            "schema_version": 5,
            "node_type": "module",
            "id": "broken",
            "version": 1,
            "gateway": {"path": "README.md", "language": "Markdown"},
            "content": [r"(?:README\.md|caller\.py)"],
            "authority": {"owns_filesystem": []},
            "sources": {
                "broken.source.caller": {
                    "blueprint": {
                        "base": "module-root",
                        "path": "blueprints/caller.yaml",
                    }
                }
            },
            "children": {},
            "namespace_exports": {},
            "exports": {},
        },
    )
    _write_yaml(
        broken_root / "blueprints" / "caller.yaml",
        {
            "schema_version": 5,
            "node_type": "behavioral_source",
            "id": "broken.source.caller",
            "version": 1,
            "gateway": {"path": "caller.py", "language": "Python>=3.11"},
            "content": [r"caller\.py"],
            "dependencies": [],
            "uses_interfaces": [
                {"interface": "missing.interface.run", "version": 1}
            ],
            "interfaces": {},
        },
    )
    dependency_root = root / "modules" / "dependency"
    dependency_root.mkdir(parents=True, exist_ok=True)
    (dependency_root / "README.md").write_text(
        "Dependency module.\n", encoding="utf-8"
    )
    (dependency_root / "runtime.py").write_text("pass\n", encoding="utf-8")
    _write_yaml(
        dependency_root / "blueprint.yaml",
        {
            "schema_version": 5,
            "node_type": "module",
            "id": "dependency",
            "version": 1,
            "gateway": {"path": "README.md", "language": "Markdown"},
            "content": [r"(?:README\.md|runtime\.py)"],
            "authority": {"owns_filesystem": []},
            "sources": {
                "dependency.source.runtime": {
                    "blueprint": {
                        "base": "module-root",
                        "path": "blueprints/runtime.yaml",
                    }
                }
            },
            "children": {},
            "namespace_exports": {},
            "exports": {},
        },
    )
    _write_yaml(
        dependency_root / "blueprints" / "runtime.yaml",
        {
            "schema_version": 5,
            "node_type": "behavioral_source",
            "id": "dependency.source.runtime",
            "version": 1,
            "gateway": {"path": "runtime.py", "language": "Python>=3.11"},
            "content": [r"runtime\.py"],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {},
        },
    )

    result = load_dispatch_blueprint_graph(
        root,
        caller_module_id="demo",
        interface_id="demo.interface.execute",
        schema_root=V5_SCHEMA_ROOT,
    )

    assert "beta" in result.graph.nodes
    assert "dependency.source.runtime" in result.graph.nodes
    assert result.diagnostics[0].code == "unrelated-blueprint-invalid"


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
        node.module_root,
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
        module_root=module,
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


def test_content_ownership_excludes_python_caches_but_keeps_authored_fixtures(
    tmp_path: Path,
) -> None:
    module = tmp_path / "modules" / "demo"
    tests = module / "tests"
    fixtures = tests / "fixtures"
    nested = tests / "nested"
    cache = tests / "__pycache__"
    fixtures.mkdir(parents=True)
    nested.mkdir()
    cache.mkdir()
    gateway = tests / "test_worker.py"
    nested_test = nested / "test_nested.py"
    json_fixture = fixtures / "case.json"
    binary_fixture = fixtures / "payload.bin"
    gateway.write_text("", encoding="utf-8")
    nested_test.write_text("", encoding="utf-8")
    json_fixture.write_text("{}\n", encoding="utf-8")
    binary_fixture.write_bytes(b"fixture\x00data")
    (cache / "accidental.py").write_text("", encoding="utf-8")
    (cache / "test_worker.cpython-313.pyc").write_bytes(b"cache")
    (tests / "standalone.pyc").write_bytes(b"cache")
    node = BlueprintNode(
        node_id="demo",
        node_type="module",
        version=1,
        module_root=module,
        blueprint_path=module / "blueprint.yaml",
        gateway_path=gateway,
        declaration={
            "schema_version": 5,
            "content": [r"tests/.*"],
        },
    )

    assert resolved_node_content_paths(node, tmp_path) == (
        json_fixture,
        binary_fixture,
        nested_test,
        gateway,
    )


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path, "target-skill", allow_callers=[])
    sibling = tmp_path / "skills" / "invalid-sibling"
    sibling.mkdir()
    (sibling / "blueprint.yaml").write_text("not: [valid\n", encoding="utf-8")
    monkeypatch.setattr(
        blueprint_graph,
        "_descriptor_safe_open_supported",
        lambda: False,
    )

    node = blueprint_graph.load_module_blueprint(
        tmp_path,
        tmp_path / "skills" / "target-skill",
        schema_root=SCHEMA_ROOT,
    )

    assert node.node_id == "target-skill"
    assert node.node_type == "module"
    assert node.module_root == tmp_path / "skills" / "target-skill"
    assert node.declaration["schema_version"] == 4


def test_load_module_blueprints_reuses_one_schema_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_v4_module(tmp_path, "first-skill", allow_callers=[])
    _write_v4_module(tmp_path, "second-skill", allow_callers=[])
    first = tmp_path / "skills" / "first-skill"
    second = tmp_path / "skills" / "second-skill"
    loaded_schema_names: list[str] = []
    real_load_schema_validator = blueprint_graph._load_schema_validator

    def counted_load_schema_validator(schema_path: Path):
        loaded_schema_names.append(schema_path.name)
        return real_load_schema_validator(schema_path)

    monkeypatch.setattr(
        blueprint_graph,
        "_load_schema_validator",
        counted_load_schema_validator,
    )

    nodes = blueprint_graph.load_module_blueprints(
        tmp_path,
        (first, second),
        schema_root=SCHEMA_ROOT,
        expected_schema_version=4,
    )

    assert tuple(node.node_id for node in nodes) == (
        "first-skill",
        "second-skill",
    )
    assert loaded_schema_names == ["module.schema.json"]


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


def test_v5_repository_loader_is_explicit_and_v4_default_is_unchanged(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)

    with pytest.raises(ValueError, match="schema_version 4"):
        load_repository_blueprint_graph(root, schema_root=V5_SCHEMA_ROOT)

    graph = load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )

    assert graph.schema_version == 5
    assert all(
        node.declaration["schema_version"] == 5 for node in graph.nodes.values()
    )


def test_blueprints_module_declares_resolve_authorization_as_one_enum_value() -> None:
    declaration = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "src"
            / "officina"
            / "blueprints"
            / "blueprints"
            / "graph.yaml"
        ).read_text(encoding="utf-8")
    )
    operations = declaration["interfaces"][
        "blueprints.source.graph.interface.python-api"
    ]["contract"]["arguments"]["operation"]["type"]["values"]
    resolve_authorization = next(
        operation
        for operation in operations
        if operation["value"] == "resolve-authorization"
    )

    assert set(resolve_authorization) == {"value", "description"}


def test_v5_graph_indexes_registered_topology_and_deepest_ownership(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)

    graph = load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )

    assert graph.module_parents == {
        "alpha": "root",
        "beta": "root",
        "beta-leaf": "beta",
        "demo": None,
        "demo-rtx": "demo",
        "leaf": "alpha",
        "outsider": None,
        "root": None,
    }
    assert graph.module_children["root"] == ("alpha", "beta")
    assert graph.module_children["alpha"] == ("leaf",)
    assert graph.module_children["demo"] == ("demo-rtx",)
    assert graph.module_local_segments["demo-rtx"] == "_rtx"
    assert graph.module_ancestry["leaf"] == ("root", "alpha", "leaf")
    assert graph.module_ancestry["demo-rtx"] == ("demo", "demo-rtx")
    assert graph.nodes["demo-rtx"].module_root == (
        root / "skills" / "demo" / "_rtx"
    )
    assert graph.nodes["demo-rtx"].module_root == graph.nodes["demo-rtx"].module_root

    assert graph.direct_file_owners[
        root / "modules" / "root" / "README.md"
    ] == "root"
    assert graph.direct_file_owners[
        root / "modules" / "root" / "alpha" / "caller.py"
    ] == "alpha.source.caller"
    assert graph.direct_file_owners[
        root / "modules" / "root" / "alpha" / "leaf" / "runtime.py"
    ] == "leaf.source.runtime"
    assert graph.direct_file_owners[
        root / "skills" / "demo" / "_rtx" / "runtime.py"
    ] == "demo-rtx.source.runtime"
    assert not any(
        owner == "root"
        and path.is_relative_to(root / "modules" / "root" / "alpha")
        for path, owner in graph.direct_file_owners.items()
    )
    assert not any(
        owner == "demo"
        and path.is_relative_to(root / "skills" / "demo" / "_rtx")
        for path, owner in graph.direct_file_owners.items()
    )


def test_v5_graph_materializes_routes_facades_and_exact_new_relations(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)

    graph = load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )

    relation_names = {edge.relation for edge in graph.node_edges}
    assert {
        "contains-module",
        "routes-child-namespace",
        "routes-terminal-module",
        "facades-child-export",
        "facades-implementing-source",
    } <= relation_names
    assert all(
        edge.relation != "contains-module" for edge in graph.certification_edges
    )
    new_certification_edges = {
        (
            edge.relation,
            edge.source_node_id,
            edge.target_node_id,
            edge.target_version,
        )
        for edge in graph.certification_edges
        if edge.relation
        in {
            "routes-child-namespace",
            "routes-terminal-module",
            "facades-child-export",
            "facades-implementing-source",
        }
    }
    assert new_certification_edges == {
        ("routes-child-namespace", "alpha", "leaf", 1),
        ("routes-terminal-module", "alpha", "leaf", 1),
        ("routes-child-namespace", "root", "alpha", 1),
        ("routes-terminal-module", "root", "leaf", 1),
        ("facades-child-export", "demo", "demo-rtx", 1),
        (
            "facades-implementing-source",
            "demo",
            "demo-rtx.source.runtime",
            1,
        ),
    }

    assert {
        (
            routed.route_owner_id,
            routed.child_module_id,
            routed.interface_id,
            routed.version,
            routed.terminal_module_id,
        )
        for routed in graph.routed_interfaces
    } == {
        ("alpha", "leaf", "leaf.interface.hidden", 1, "leaf"),
        ("alpha", "leaf", "leaf.interface.run", 1, "leaf"),
        ("root", "alpha", "leaf.interface.run", 1, "leaf"),
    }
    facade = graph.exports["demo.interface.execute"]
    assert facade.version == 3
    assert facade.terminal_interface_id == "demo-rtx.interface.execute"
    assert facade.terminal_module_node_id == "demo-rtx"
    assert facade.source_node_id == "demo-rtx.source.runtime"
    assert facade.declaration is graph.exports[
        "demo-rtx.interface.execute"
    ].declaration


def test_v5_graph_rejects_registration_cycles_through_shared_inventory(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    marker = root / "modules" / "root" / "blueprint.yaml"
    declaration = yaml.safe_load(marker.read_text(encoding="utf-8"))
    declaration["children"]["root"] = {
        "base": "module-root",
        "path": "blueprint.yaml",
    }
    _write_yaml(marker, declaration)

    with pytest.raises(ValueError, match="registration cycle"):
        load_repository_blueprint_graph(
            root,
            schema_root=V5_SCHEMA_ROOT,
            expected_schema_version=5,
        )


def test_v5_graph_relationship_validation_requires_child_facade_admission(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    gateway_marker = (
        root / "skills" / "demo" / "blueprints" / "gateway.yaml"
    )
    gateway = yaml.safe_load(gateway_marker.read_text(encoding="utf-8"))
    gateway["uses_interfaces"] = [
        {"interface": "demo.interface.execute", "version": 3}
    ]
    _write_yaml(gateway_marker, gateway)
    marker = root / "skills" / "demo" / "_rtx" / "blueprint.yaml"
    declaration = yaml.safe_load(marker.read_text(encoding="utf-8"))
    declaration["exports"]["demo-rtx.interface.execute"]["access"][
        "allowed_callers"
    ] = []
    _write_yaml(marker, declaration)

    with pytest.raises(
        BlueprintGraphError,
        match="demo.*demo-rtx.interface.execute",
    ):
        load_repository_blueprint_graph(
            root,
            schema_root=V5_SCHEMA_ROOT,
            expected_schema_version=5,
        )


@pytest.mark.parametrize("filter_kind", ["namespace-route", "facade"])
def test_v5_graph_allows_broader_outer_filter_when_owner_can_call_next_hop(
    tmp_path: Path,
    filter_kind: str,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    if filter_kind == "namespace-route":
        marker = root / "modules" / "root" / "alpha" / "blueprint.yaml"
        declaration = yaml.safe_load(marker.read_text(encoding="utf-8"))
        declaration["namespace_exports"]["leaf"]["access"] = {
            "allow_all_modules": True,
            "allowed_callers": [],
        }
    else:
        marker = root / "skills" / "demo" / "blueprint.yaml"
        declaration = yaml.safe_load(marker.read_text(encoding="utf-8"))
        declaration["exports"]["demo.interface.execute"]["access"] = {
            "allow_all_modules": True,
            "allowed_callers": [],
        }
    _write_yaml(marker, declaration)

    load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )


@pytest.mark.parametrize(
    ("ancestor_claim", "descendant_claim"),
    [
        (
            {
                "match": "exact",
                "path": "$home/.config/shared-state.json",
                "allowed_readers": [],
            },
            {
                "match": "exact",
                "path": "$home/.config/shared-state.json",
                "allowed_readers": [],
            },
        ),
        (
            {
                "match": "regex",
                "path": r"\$home/\.config/.*",
                "allowed_readers": [],
            },
            {
                "match": "regex",
                "path": r"\$home/\.config/.+",
                "allowed_readers": [],
            },
        ),
    ],
)
def test_v5_graph_rejects_parent_child_filesystem_authority_overlap(
    tmp_path: Path,
    ancestor_claim: dict[str, object],
    descendant_claim: dict[str, object],
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    for marker, claim in (
        (root / "modules" / "root" / "blueprint.yaml", ancestor_claim),
        (
            root / "modules" / "root" / "alpha" / "blueprint.yaml",
            descendant_claim,
        ),
    ):
        declaration = yaml.safe_load(marker.read_text(encoding="utf-8"))
        declaration["authority"]["owns_filesystem"] = [claim]
        _write_yaml(marker, declaration)

    with pytest.raises(BlueprintGraphError, match="authority overlap.*root.*alpha"):
        load_repository_blueprint_graph(
            root,
            schema_root=V5_SCHEMA_ROOT,
            expected_schema_version=5,
        )


def test_v5_managed_skill_parent_rejects_executable_source(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    gateway_path = root / "skills" / "demo" / "blueprints" / "gateway.yaml"
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    gateway["gateway"]["language"] = "Python>=3.11"
    _write_yaml(gateway_path, gateway)

    with pytest.raises(
        BlueprintGraphError,
        match="skill parents may contain only Markdown instruction sources",
    ):
        load_repository_blueprint_graph(
            root,
            schema_root=V5_SCHEMA_ROOT,
            expected_schema_version=5,
        )


def test_v5_managed_skill_parent_rejects_process_bound_interface(
    tmp_path: Path,
) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    gateway_path = root / "skills" / "demo" / "blueprints" / "gateway.yaml"
    runtime_path = (
        root / "skills" / "demo" / "_rtx" / "blueprints" / "runtime.yaml"
    )
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    declaration = dict(next(iter(runtime["interfaces"].values())))
    declaration["process_binding"] = {
        "kind": "process",
        "entry": "Interface",
        "args_prefix": [],
        "arguments": {},
        "fixed": [],
    }
    interface_id = "demo.source.gateway.interface.machine"
    gateway["interfaces"][interface_id] = declaration
    _write_yaml(gateway_path, gateway)
    marker_path = root / "skills" / "demo" / "blueprint.yaml"
    marker = yaml.safe_load(marker_path.read_text(encoding="utf-8"))
    marker["exports"]["demo.interface.machine"] = {
        "source_interface": interface_id,
        "access": {"allow_all_modules": True, "allowed_callers": []},
    }
    _write_yaml(marker_path, marker)

    with pytest.raises(
        BlueprintGraphError,
        match="cannot declare a process binding",
    ):
        load_repository_blueprint_graph(
            root,
            schema_root=V5_SCHEMA_ROOT,
            expected_schema_version=5,
        )
