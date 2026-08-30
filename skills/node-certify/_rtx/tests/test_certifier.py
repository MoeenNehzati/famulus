from __future__ import annotations

import copy
import hashlib
import importlib.util
import shutil
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator

import pytest
import yaml

MODULE_PATH = Path(__file__).resolve().parents[1] / "_node_certifier.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from officina.blueprints.graph import BlueprintNode, RepositoryBlueprintGraph
from officina.certification.hashing import NodeHashState
from officina.certification.records import (
    canonical_certificate_envelope_bytes,
    certificate_public_key_root,
    certificate_entry_hash,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    rotate_certificate_signing_key,
    sign_certificate_payload,
)
from officina.git.provenance import GitSnapshot
from officina.runtime.python_machine_interface import (
    logical_python_package_name,
)
from test_support.v4_certification_fixtures import (
    MemorySecretBackend,
    contract,
    load_v4_repository as load_repository_fixture,
    materialize_v4_repository as materialize_repository_fixture,
    write_yaml,
)
from test_support.git_repository import GitTestRepository
from test_support.v5_blueprint_fixtures import (
    copy_v5_fixture_tree as copy_legacy_fixture_tree,
)

SPEC = importlib.util.spec_from_file_location("skill_certifier_certifier", MODULE_PATH)
certifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = certifier
SPEC.loader.exec_module(certifier)

LEGACY_SCHEMA_ROOT = (
    MODULE_PATH.parents[3] / "tests" / "fixtures" / "blueprint_schemas" / "v5"
)
LEGACY_AUTHORIZATION_FIXTURE = (
    MODULE_PATH.parents[3] / "tests" / "fixtures" / "blueprint_v5" / "authorization"
)

RepositoryCopyFactory = Callable[[Path], str]
RepositoryGraphBinder = Callable[[Path, RepositoryBlueprintGraph], None]
VALID_COMMIT = "a" * 40


def _copy_repository_tree(template_root: Path, destination: Path) -> None:
    target = Path(destination)
    if target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise ValueError(f"repository copy destination must be empty: {target}")
        shutil.copytree(
            template_root,
            target,
            copy_function=shutil.copy2,
            dirs_exist_ok=True,
            symlinks=True,
        )
        return
    shutil.copytree(
        template_root,
        target,
        copy_function=shutil.copy2,
        symlinks=True,
    )


@pytest.fixture(scope="session")
def _base_v4_repository_copier(
    tmp_path_factory: pytest.TempPathFactory,
) -> RepositoryCopyFactory:
    template_root = tmp_path_factory.mktemp("node-certify-v4-template") / "repository"
    commit = materialize_repository_fixture(template_root)

    def copy_repository(destination: Path) -> str:
        _copy_repository_tree(template_root, destination)
        return commit

    return copy_repository


@pytest.fixture
def copy_base_v4_repository(
    _base_v4_repository_copier: RepositoryCopyFactory,
) -> RepositoryCopyFactory:
    return _base_v4_repository_copier


@pytest.fixture(scope="session")
def _cross_owner_v4_repository_copier(
    tmp_path_factory: pytest.TempPathFactory,
    _base_v4_repository_copier: RepositoryCopyFactory,
) -> RepositoryCopyFactory:
    template_root = (
        tmp_path_factory.mktemp("node-certify-v4-cross-owner-template") / "repository"
    )
    _base_v4_repository_copier(template_root)
    _add_cross_owner_contract(template_root)
    commit = (
        GitTestRepository(template_root)
        .git("rev-parse", "HEAD")
        .stdout.decode("ascii")
        .strip()
    )

    def copy_repository(destination: Path) -> str:
        _copy_repository_tree(template_root, destination)
        return commit

    return copy_repository


@pytest.fixture
def copy_cross_owner_v4_repository(
    _cross_owner_v4_repository_copier: RepositoryCopyFactory,
) -> RepositoryCopyFactory:
    return _cross_owner_v4_repository_copier


@pytest.fixture
def stable_empty_route_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate writer tests whose observable is independent of route tracing."""
    monkeypatch.setattr(
        certifier.RouteSmokeAuditor,
        "trace_dependencies",
        lambda _self: (),
    )


@pytest.fixture(scope="session")
def immutable_legacy_route_graph(
    tmp_path_factory: pytest.TempPathFactory,
) -> RepositoryBlueprintGraph:
    root = tmp_path_factory.mktemp("node-certify-v5-route-graph")
    copy_legacy_fixture_tree(
        LEGACY_AUTHORIZATION_FIXTURE / "modules",
        root / "modules",
    )
    copy_legacy_fixture_tree(
        LEGACY_AUTHORIZATION_FIXTURE / "skills",
        root / "skills",
    )
    return certifier.load_repository_blueprint_graph(
        root,
        schema_root=LEGACY_SCHEMA_ROOT,
        expected_schema_version=5,
    )


def _load_repository_graph(repo: Path):
    return certifier.load_repository_blueprint_graph(
        repo,
        schema_root=repo / "references" / "blueprint-schema",
        expected_schema_version=4,
    )


def _bind_repository_graph(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
    *,
    schema_root: Path,
    expected_schema_version: int,
) -> None:
    resolved_root = repo_root.resolve()
    resolved_schema_root = schema_root.resolve()
    if graph.schema_version != expected_schema_version or any(
        node.declaration.get("schema_version") != expected_schema_version
        for node in graph.nodes.values()
    ):
        raise ValueError("bound graph does not match the requested schema version")
    if any(
        not node.blueprint_path.resolve().is_relative_to(resolved_root)
        for node in graph.nodes.values()
    ):
        raise ValueError("bound graph does not belong to the requested repository")
    physical_load = certifier.load_repository_blueprint_graph

    def load(
        requested_root: Path,
        *,
        schema_root: Path | None = None,
        expected_schema_version: int = 6,
    ) -> RepositoryBlueprintGraph:
        if (
            Path(requested_root).resolve() == resolved_root
            and schema_root is not None
            and Path(schema_root).resolve() == resolved_schema_root
            and expected_schema_version == graph.schema_version
        ):
            return graph
        return physical_load(
            requested_root,
            schema_root=schema_root,
            expected_schema_version=expected_schema_version,
        )

    monkeypatch.setattr(certifier, "load_repository_blueprint_graph", load)


@pytest.fixture
def bind_repository_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[RepositoryGraphBinder]:
    snapshots: list[tuple[RepositoryBlueprintGraph, RepositoryBlueprintGraph]] = []

    def bind(repo_root: Path, graph: RepositoryBlueprintGraph) -> None:
        snapshots.append((graph, copy.deepcopy(graph)))
        _bind_repository_graph(
            monkeypatch,
            repo_root,
            graph,
            schema_root=repo_root / "references" / "blueprint-schema",
            expected_schema_version=4,
        )

    yield bind

    for graph, snapshot in snapshots:
        assert graph == snapshot, "bound repository graph must remain immutable"


def _synthetic_repository_graph(
    repo_root: Path,
    *,
    modules: tuple[tuple[str, str], ...] = (("demo-skill", "demo-skill"),),
    schema_version: int = 6,
) -> RepositoryBlueprintGraph:
    nodes: dict[str, BlueprintNode] = {}
    module_sources: dict[str, tuple[str, ...]] = {}
    for module_id, module_name in modules:
        module_root = (repo_root / "skills" / module_name).resolve()
        source_id = f"{module_id}.source.gateway"
        declaration = {"schema_version": schema_version}
        nodes[module_id] = BlueprintNode(
            node_id=module_id,
            node_type="module",
            version=1,
            module_root=module_root,
            blueprint_path=module_root / "blueprint.yaml",
            gateway_path=module_root / "SKILL.md",
            declaration=declaration,
        )
        nodes[source_id] = BlueprintNode(
            node_id=source_id,
            node_type="behavioral_source",
            version=1,
            module_root=module_root,
            blueprint_path=module_root / "blueprints" / "gateway.yaml",
            gateway_path=module_root / "_rtx" / "gateway.py",
            declaration=declaration,
        )
        module_sources[module_id] = (source_id,)
    return RepositoryBlueprintGraph(
        nodes=nodes,
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        module_sources=module_sources,
        schema_version=schema_version,
    )


def _synthetic_python_source_graph(repo_root: Path) -> RepositoryBlueprintGraph:
    graph = _synthetic_repository_graph(repo_root, schema_version=4)
    source_id = "demo-skill.source.gateway"
    source = graph.nodes[source_id]
    binding = {
        "kind": "process",
        "entry": "Interface",
        "arguments": {},
        "fixed": [],
    }
    declaration = {
        "schema_version": 4,
        "gateway": {"path": "_rtx/worker.py", "language": "Python"},
        "interfaces": {
            "demo-skill.source.gateway.interface.run": {
                "process_binding": binding,
            },
            "demo-skill.source.gateway.interface.inspect": {
                "process_binding": dict(binding),
            },
        },
    }
    return replace(
        graph,
        nodes={
            **graph.nodes,
            source_id: replace(
                source,
                gateway_path=source.module_root / "_rtx" / "worker.py",
                declaration=declaration,
            ),
        },
    )


def _git_metadata_result(
    operation: str,
    relative_path: str,
    *,
    object_id: str = "1" * 40,
) -> SimpleNamespace:
    if operation == "ls-tree":
        stdout = (
            f"100644 blob {object_id}\t{relative_path}\0"
            f"100644 blob {'2' * 40}\tunrelated.txt\0"
        ).encode()
    elif operation == "ls-files":
        stdout = f"100644 {object_id} 0\t{relative_path}\0".encode()
    elif operation == "cat-file":
        payload = b"committed bytes\n"
        stdout = f"{object_id} blob {len(payload)}\n".encode() + payload + b"\n"
    else:
        raise AssertionError(f"unexpected Git operation: {operation}")
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")


def test_repository_fixture_materializes_without_preparation_then_loads_bound_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_support import v4_certification_fixtures as certification_fixtures

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("repository-only fixture performed graph preparation")

    with monkeypatch.context() as materialization_patch:
        materialization_patch.setattr(
            certification_fixtures,
            "load_repository_blueprint_graph",
            unexpected,
        )
        materialization_patch.setattr(
            certification_fixtures,
            "compute_node_hash_states",
            unexpected,
        )

        commit = certification_fixtures.materialize_v4_repository(tmp_path)

    assert (
        GitTestRepository(tmp_path)
        .git("rev-parse", "HEAD")
        .stdout.decode("ascii")
        .strip()
        == commit
    )

    graph, states, loaded_commit = certification_fixtures.load_v4_repository(tmp_path)

    assert loaded_commit == commit
    assert set(states) == set(graph.nodes)
    assert all(
        node.blueprint_path.is_relative_to(tmp_path) for node in graph.nodes.values()
    )
    with pytest.raises(ValueError, match="does not match repository HEAD"):
        certification_fixtures.load_v4_repository(
            tmp_path,
            commit="0" * 40,
        )


def test_repository_graph_binding_requires_exact_root_schema_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _synthetic_repository_graph(tmp_path, schema_version=4)
    schema_root = tmp_path / "references" / "blueprint-schema"
    fallback = _synthetic_repository_graph(tmp_path / "fallback", schema_version=6)
    physical_calls: list[tuple[Path, Path | None, int]] = []

    def physical_load(
        repo_root: Path,
        *,
        schema_root: Path | None = None,
        expected_schema_version: int = 6,
    ) -> RepositoryBlueprintGraph:
        physical_calls.append((Path(repo_root), schema_root, expected_schema_version))
        return fallback

    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        physical_load,
    )
    _bind_repository_graph(
        monkeypatch,
        tmp_path,
        graph,
        schema_root=schema_root,
        expected_schema_version=4,
    )

    assert (
        certifier.load_repository_blueprint_graph(
            tmp_path / ".",
            schema_root=schema_root / ".",
            expected_schema_version=4,
        )
        is graph
    )
    assert (
        certifier.load_repository_blueprint_graph(
            tmp_path / "other",
            schema_root=schema_root,
            expected_schema_version=4,
        )
        is fallback
    )
    assert (
        certifier.load_repository_blueprint_graph(
            tmp_path,
            schema_root=tmp_path / "other-schema",
            expected_schema_version=4,
        )
        is fallback
    )
    assert (
        certifier.load_repository_blueprint_graph(
            tmp_path,
            schema_root=schema_root,
            expected_schema_version=6,
        )
        is fallback
    )
    assert len(physical_calls) == 3


def test_certifier_does_not_expose_legacy_audit_health_authority() -> None:
    for name in (
        "AUDIT_RECORD_NAME",
        "AuditContext",
        "audit_typed_graph",
        "check_graph_health_from_disk",
        "_audit_legacy_target",
        "_legacy_record_is_current",
        "certify_pooled_review",
        "load_or_create_hmac_key",
        "TargetHash",
        "compute_hash_payload",
        "_hash_items",
        "collect_targets",
        "reviewed_repository_target_requests",
        "_EphemeralSecretBackend",
    ):
        assert not hasattr(certifier, name)
    assert "compute-hashes" not in certifier.Interface.dispatches


def _certify(
    repo: Path,
    *,
    target_node_ids: tuple[str, ...] = ("demo-skill",),
    **overrides: object,
):
    public_key_root = repo / "public-keys"
    public_key_root.mkdir(exist_ok=True)
    snapshot = certifier.capture_git_snapshot(repo)
    assert snapshot is not None
    options = {
        "target_node_ids": target_node_ids,
        "public_key_root": public_key_root,
        "secret_backend": MemorySecretBackend(),
        "reviewed_commit": snapshot.commit,
        "certified_at": "2026-07-20T12:00:00Z",
        "expected_schema_version": 4,
        "schema_root": repo / "references" / "blueprint-schema",
        "require_migration_review": True,
    }
    options.update(overrides)
    return certifier._certify_repository(repo, **options)


def test_payload_schema_tracks_repository_schema(
    tmp_path: Path,
) -> None:
    commit = VALID_COMMIT
    graph = _synthetic_repository_graph(tmp_path, schema_version=4)
    node_id = "demo-skill"
    states = {node_id: NodeHashState()}
    common = {
        "source_commit": commit,
        "key_id": "sha256:" + "a" * 64,
        "previous_entry_hash": None,
        "certifier_identity": {
            "interface": "node-certify._rtx.interface.certify",
            "version": 1,
            "node_hash": "sha256:" + "b" * 64,
            "source_commit": commit,
        },
        "checks": (),
        "certified_at": "2026-07-20T12:00:00Z",
    }

    assert (
        certifier._build_certificate_payload(
            tmp_path,
            graph,
            states,
            node_id,
            **common,
        )["certificate_schema_version"]
        == 1
    )
    assert (
        certifier._build_certificate_payload(
            tmp_path,
            replace(graph, schema_version=5),
            states,
            node_id,
            expected_schema_version=5,
            **common,
        )["certificate_schema_version"]
        == 2
    )
    v6_payload = certifier._build_certificate_payload(
        tmp_path,
        replace(graph, schema_version=6),
        states,
        node_id,
        expected_schema_version=6,
        **common,
    )
    assert v6_payload["certificate_schema_version"] == 3
    assert v6_payload["facets"] == []


def test_gate_records_use_the_schema_selected_registry() -> None:
    assert certifier._passed_check(
        "deterministic",
        expected_schema_version=5,
    ) == {
        "id": "v5-deterministic",
        "version": 1,
        "passed": True,
        "findings": [],
    }
    assert certifier._passed_check("deterministic")["id"] == "v4-deterministic"


def test_executing_certifier_must_be_owned_by_runtime_child() -> None:
    root = MODULE_PATH.parents[3]
    executing = Path(certifier.__file__).resolve()
    source_id = "node-certify.source.certifier"
    node = SimpleNamespace(
        node_type="behavioral_source",
        gateway_path=executing,
    )
    state = certifier.NodeHashState(
        input_manifest=(
            {
                "path": executing.relative_to(root).as_posix(),
                "digest": "sha256:"
                + hashlib.sha256(executing.read_bytes()).hexdigest(),
                "git_provenance": "tracked",
            },
        )
    )

    parent_owned = SimpleNamespace(
        schema_version=5,
        nodes={source_id: node},
        source_modules={source_id: "node-certify"},
    )
    with pytest.raises(
        certifier.CertificationError,
        match="node-certify-rtx",
    ):
        certifier._verify_executing_candidate_certifier(
            root,
            parent_owned,
            {source_id: state},
        )

    child_owned = SimpleNamespace(
        schema_version=5,
        nodes={source_id: node},
        source_modules={source_id: "node-certify-rtx"},
    )
    certifier._verify_executing_candidate_certifier(
        root,
        child_owned,
        {source_id: state},
    )
    certifier._verify_executing_candidate_certifier(
        root,
        SimpleNamespace(
            schema_version=4,
            nodes=parent_owned.nodes,
            source_modules=parent_owned.source_modules,
        ),
        {source_id: state},
    )


def _add_cross_owner_contract(repo: Path):
    module_root = repo / "skills" / "demo-skill"
    contract_path = module_root / "contracts" / "shared.schema.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text('{"type":"string"}\n', encoding="utf-8")
    contract_node_id = "demo-skill.source.contract"
    contract_interface_id = f"{contract_node_id}.interface.read"
    write_yaml(
        module_root / "blueprints" / "contract.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": contract_node_id,
            "version": 1,
            "description": "Shared contract source.",
            "gateway": {
                "path": "contracts/shared.schema.json",
                "language": "JSON",
            },
            "content": [r"contracts/shared\.schema\.json"],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {
                contract_interface_id: {
                    "version": 1,
                    "description": "Read the shared contract.",
                    "contract": contract(),
                }
            },
        },
    )
    module_path = module_root / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["sources"][contract_node_id] = {
        "blueprint": {
            "base": "module-root",
            "path": "blueprints/contract.yaml",
        }
    }
    module["content"].append(r"contracts/shared\.schema\.json")
    write_yaml(module_path, module)
    gateway_path = module_root / "blueprints" / "gateway.yaml"
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    interface = gateway["interfaces"]["demo-skill.source.gateway.interface.run"]
    output = interface["contract"]["outputs"][0]
    output.pop("type")
    output["schema"] = {
        "path": "contracts/shared.schema.json",
        "fragment": "#",
    }
    write_yaml(gateway_path, gateway)
    repository = GitTestRepository(repo)
    repository.git("add", ".")
    repository.git("commit", "-qm", "add contract source")
    return certifier.load_repository_blueprint_graph(
        repo,
        schema_root=repo / "references" / "blueprint-schema",
        expected_schema_version=4,
    )


def test_private_writer_successful_real_route_issuance_has_complete_payload_and_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_base_v4_repository: RepositoryCopyFactory,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    commit = copy_base_v4_repository(tmp_path)
    real_load_graph = certifier.load_repository_blueprint_graph
    graph_load_calls = 0

    def counted_load_graph(*args: object, **kwargs: object):
        nonlocal graph_load_calls
        graph_load_calls += 1
        return real_load_graph(*args, **kwargs)

    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        counted_load_graph,
    )
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)
    public_key_root = certificate_public_key_root(tmp_path)
    backend = MemorySecretBackend()
    real_compute = certifier.compute_node_hash_states
    real_inspect = certifier.CommitReadinessInspector.inspect
    compute_calls = 0
    readiness_calls = 0
    atomic_modes: dict[str, list[object]] = {
        "read": [],
        "replace": [],
        "compare-and-append": [],
    }

    def fail_if_read(_root: Path) -> str:
        raise AssertionError("live certification must not read migration-only refs")

    def counted_compute(*args: object, **kwargs: object):
        nonlocal compute_calls
        compute_calls += 1
        return real_compute(*args, **kwargs)

    def counted_inspect(self):
        nonlocal readiness_calls
        readiness_calls += 1
        return real_inspect(self)

    def observe_atomic_mode(name: str, operation):
        def wrapped(*args, **kwargs):
            atomic_modes[name].append(kwargs.get("allow_non_atomic"))
            return operation(*args, **kwargs)

        return wrapped

    (tmp_path / "preexisting-untracked.txt").write_text(
        "preexisting\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(certifier, "blueprint_mechanical_commit", fail_if_read)
    monkeypatch.setattr(certifier, "compute_node_hash_states", counted_compute)
    monkeypatch.setattr(certifier.CommitReadinessInspector, "inspect", counted_inspect)
    monkeypatch.setattr(
        certifier,
        "read_regular_file_bytes",
        observe_atomic_mode("read", certifier.read_regular_file_bytes),
    )
    monkeypatch.setattr(
        certifier,
        "atomic_replace_bytes",
        observe_atomic_mode("replace", certifier.atomic_replace_bytes),
    )
    monkeypatch.setattr(
        certifier,
        "atomic_compare_and_append_bytes",
        observe_atomic_mode(
            "compare-and-append",
            certifier.atomic_compare_and_append_bytes,
        ),
    )

    result = _certify(
        tmp_path,
        public_key_root=public_key_root,
        secret_backend=backend,
        require_migration_review=False,
    )

    assert result.node_ids == (
        "demo-skill",
        "demo-skill.source.gateway",
    )
    assert result.source_commit == commit
    assert (public_key_root / "active-key-id").is_file()
    assert graph_load_calls == 1
    assert compute_calls == 2
    assert readiness_calls == 2
    assert {name: set(values) for name, values in atomic_modes.items()} == {
        "read": {False},
        "replace": {False},
        "compare-and-append": {False},
    }
    path = certifier.certificate_log_path(graph.nodes["demo-skill"])
    entries = parse_certificate_log(path.read_bytes(), public_key_root)
    assert len(entries) == 1
    payload = entries[0]["payload"]
    assert payload["subject"]["id"] == "demo-skill"
    assert payload["source_commit"] == commit
    assert payload["checks"] == list(certifier.expected_certifier_checks(4))
    assert {(check["id"], check["version"]) for check in payload["checks"]} >= {
        ("route-smoke-dependencies", 1)
    }


def test_private_writer_noop_then_renews_only_stale_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_base_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)
    backend = MemorySecretBackend()
    parent_id = "demo-skill"
    child_id = "demo-skill.source.gateway"

    first = _certify(tmp_path, secret_backend=backend)
    log_bytes = {
        node_id: certifier.certificate_log_path(graph.nodes[node_id]).read_bytes()
        for node_id in first.node_ids
    }
    pooled_path = tmp_path / "skills" / "demo-skill" / ".pooled-blueprint-review.yaml"
    pooled_identity = pooled_path.stat().st_ino
    audit_scopes: list[tuple[str, ...]] = []
    mechanical_gates: list[str] = []
    real_audit = certifier.RouteSmokeAuditor.require_stable_dependencies

    def unexpected_audit(self) -> None:
        audit_scopes.append(self._certification_node_ids)

    monkeypatch.setattr(
        certifier.RouteSmokeAuditor,
        "require_stable_dependencies",
        unexpected_audit,
    )

    second = _certify(
        tmp_path,
        secret_backend=backend,
        before_stale_issuance=lambda: mechanical_gates.append("mechanical"),
    )
    pooled_document = yaml.safe_load(pooled_path.read_text(encoding="utf-8"))
    module_entries = parse_certificate_log(
        certifier.certificate_log_path(graph.nodes["demo-skill"]).read_bytes(),
        tmp_path / "public-keys",
    )

    assert second.node_ids == ()
    assert second.current_node_ids == first.node_ids
    assert {
        node_id: certifier.certificate_log_path(graph.nodes[node_id]).read_bytes()
        for node_id in first.node_ids
    } == log_bytes
    assert pooled_path.stat().st_ino == pooled_identity
    assert audit_scopes == []
    assert mechanical_gates == []
    assert pooled_document["document_type"] == "pooled-blueprint-review"
    assert pooled_document["root"]["id"] == "demo-skill"
    assert pooled_document["root"]["certificate_hash"] == certificate_entry_hash(
        module_entries[-1]
    )
    assert not (
        tmp_path / "skills" / "demo-skill" / ".pooled-blueprint-review.health.json"
    ).exists()
    parent_log = certifier.certificate_log_path(graph.nodes[parent_id])
    child_log = certifier.certificate_log_path(graph.nodes[child_id])
    child_bytes = child_log.read_bytes()
    parent_log.unlink()
    audit_scopes: list[tuple[str, ...]] = []
    events: list[str] = []

    def capture_audit(self) -> None:
        events.append("route")
        audit_scopes.append(self._certification_node_ids)
        real_audit(self)

    monkeypatch.setattr(
        certifier.RouteSmokeAuditor,
        "require_stable_dependencies",
        capture_audit,
    )

    result = _certify(
        tmp_path,
        secret_backend=backend,
        before_stale_issuance=lambda: events.append("mechanical"),
    )

    assert result.node_ids == (parent_id,)
    assert result.current_node_ids == (child_id,)
    assert child_log.read_bytes() == child_bytes
    assert (
        len(parse_certificate_log(parent_log.read_bytes(), tmp_path / "public-keys"))
        == 1
    )
    assert audit_scopes == [(parent_id,)]
    assert events == ["mechanical", "route"]


def test_private_writer_reuses_consumer_for_restored_provider_then_renews_changed_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_cross_owner_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_cross_owner_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)
    target = "demo-skill.source.gateway"
    dependency = "demo-skill.source.contract"
    backend = MemorySecretBackend()

    _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
        require_migration_review=False,
    )
    target_log = certifier.certificate_log_path(graph.nodes[target])
    dependency_log = certifier.certificate_log_path(graph.nodes[dependency])
    target_bytes = target_log.read_bytes()
    dependency_log.unlink()

    result = _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
        require_migration_review=False,
    )

    assert result.node_ids == (dependency,)
    assert result.current_node_ids == (target,)
    assert target_log.read_bytes() == target_bytes
    assert (
        len(
            parse_certificate_log(dependency_log.read_bytes(), tmp_path / "public-keys")
        )
        == 1
    )
    assert not certifier.certificate_log_path(graph.nodes["demo-skill"]).exists()
    contract_path = (
        tmp_path / "skills" / "demo-skill" / "contracts" / "shared.schema.json"
    )
    contract_path.write_text('{"type":"integer"}\n', encoding="utf-8")
    repository = GitTestRepository(tmp_path)
    repository.git("add", "skills/demo-skill/contracts/shared.schema.json")
    repository.git("commit", "-qm", "change provider contract")

    result = _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
        require_migration_review=False,
    )

    assert result.node_ids == (dependency, target)


def test_selected_legacy_writer_issues_current_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copy_legacy_fixture_tree(
        LEGACY_AUTHORIZATION_FIXTURE / "modules",
        tmp_path / "modules",
    )
    copy_legacy_fixture_tree(
        LEGACY_AUTHORIZATION_FIXTURE / "skills",
        tmp_path / "skills",
    )
    policy_source = (
        MODULE_PATH.parents[3]
        / "references"
        / "certification-policy"
        / "node-hash-policy.yaml"
    )
    policy_path = (
        tmp_path / "references" / "certification-policy" / "node-hash-policy.yaml"
    )
    policy_path.parent.mkdir(parents=True)
    shutil.copy2(policy_source, policy_path)
    repository = GitTestRepository._initialize(
        tmp_path,
        branch="main",
        filemode=True,
    )
    repository.git("add", ".")
    repository.git("commit", "-qm", "v5 writer fixture")
    graph = certifier.load_repository_blueprint_graph(
        tmp_path,
        schema_root=LEGACY_SCHEMA_ROOT,
        expected_schema_version=5,
    )
    commit = repository.git("rev-parse", "HEAD").stdout.decode().strip()
    monkeypatch.setattr(
        certifier,
        "resolve_certification_basis_paths",
        lambda *_args, **_kwargs: (policy_path,),
    )
    monkeypatch.setattr(
        certifier,
        "compute_certification_basis_hash",
        lambda *_args, **_kwargs: "sha256:" + "c" * 64,
    )
    monkeypatch.setattr(
        certifier,
        "derive_certifier_identity",
        lambda *_args, **_kwargs: {
            "interface": "node-certify-rtx.interface.certify",
            "version": 1,
            "node_hash": "sha256:" + "d" * 64,
            "source_commit": commit,
        },
    )
    with pytest.raises(
        certifier.CertificationError,
        match="v5 certification completeness failed",
    ):
        _certify(
            tmp_path,
            target_node_ids=("demo-rtx",),
            expected_schema_version=5,
            schema_root=LEGACY_SCHEMA_ROOT,
            require_migration_review=False,
        )

    monkeypatch.setattr(
        certifier,
        "certification_completeness_findings",
        lambda _graph: (),
    )
    result = _certify(
        tmp_path,
        target_node_ids=("demo-rtx",),
        expected_schema_version=5,
        schema_root=LEGACY_SCHEMA_ROOT,
        require_migration_review=False,
    )

    for node_id in result.node_ids:
        entries = parse_certificate_log(
            certifier.certificate_log_path(graph.nodes[node_id]).read_bytes(),
            tmp_path / "public-keys",
        )
        assert entries[-1]["payload"]["certificate_schema_version"] == 2
        assert entries[-1]["payload"]["checks"] == list(
            certifier.expected_certifier_checks(5)
        )


@pytest.mark.parametrize(
    "race",
    [
        "tracked-input",
        "worktree-mode",
        "index-mode",
        "head",
        "log",
        "untracked-file",
    ],
)
def test_private_writer_aborts_pre_append_races(
    tmp_path: Path,
    race: str,
    copy_base_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    if race == "worktree-mode" and sys.platform == "win32":
        # famulus-skip: category=platform-contract; reason=Windows worktrees do not expose a reliable POSIX executable mode; alternate=index-mode covers the authoritative Git mode boundary
        pytest.skip("POSIX worktree mode is unavailable")
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)

    def mutate(node_id: str) -> None:
        if race == "tracked-input":
            (tmp_path / "skills" / "demo-skill" / "SKILL.md").write_text(
                "changed\n",
                encoding="utf-8",
            )
        elif race == "worktree-mode":
            path = tmp_path / "skills" / "demo-skill" / "SKILL.md"
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        elif race == "index-mode":
            GitTestRepository(tmp_path).git(
                "update-index",
                "--chmod=+x",
                "skills/demo-skill/SKILL.md",
            )
        elif race == "head":
            (tmp_path / "race.txt").write_text("race\n", encoding="utf-8")
            repository = GitTestRepository(tmp_path)
            repository.git("add", "race.txt")
            repository.git("commit", "-qm", "race")
        elif race == "untracked-file":
            (tmp_path / "unexpected.txt").write_text(
                "race\n",
                encoding="utf-8",
            )
        else:
            path = certifier.certificate_log_path(graph.nodes[node_id])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(certifier.CertificationError, match="changed"):
        _certify(tmp_path, before_append=mutate)


def test_private_writer_detects_post_append_log_corruption(
    tmp_path: Path,
    copy_base_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)

    def corrupt(node_id: str) -> None:
        with certifier.certificate_log_path(graph.nodes[node_id]).open("ab") as stream:
            stream.write(b"{}\n")

    with pytest.raises(
        certifier.CertificationError, match="post-write certificate log changed"
    ):
        _certify(tmp_path, after_append=corrupt)


def test_private_writer_propagates_explicit_non_atomic_mode_to_existing_file_apis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_base_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)
    (tmp_path / "public-keys").mkdir()
    observed: dict[str, list[object]] = {
        "read": [],
        "replace": [],
        "compare-and-append": [],
    }

    def observe(name: str, operation):
        def wrapped(*args, **kwargs):
            observed[name].append(kwargs.get("allow_non_atomic"))
            return operation(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(
        certifier,
        "read_regular_file_bytes",
        observe("read", certifier.read_regular_file_bytes),
    )
    monkeypatch.setattr(
        certifier,
        "atomic_replace_bytes",
        observe("replace", certifier.atomic_replace_bytes),
    )
    monkeypatch.setattr(
        certifier,
        "atomic_compare_and_append_bytes",
        observe("compare-and-append", certifier.atomic_compare_and_append_bytes),
    )
    monkeypatch.setattr(
        certifier,
        "render_pooled_review",
        lambda *_args, **_kwargs: "pooled review\n",
    )

    result = _certify(tmp_path, allow_non_atomic=True)

    assert "demo-skill" in result.node_ids
    assert {name: set(values) for name, values in observed.items()} == {
        "read": {True},
        "replace": {True},
        "compare-and-append": {True},
    }


def test_private_writer_rejects_caller_supplied_gate_callbacks(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="semantic_audit"):
        certifier._certify_repository(
            tmp_path,
            semantic_audit=lambda _snapshot: None,
        )


def test_private_writer_certifies_certifier_through_same_path(
    tmp_path: Path,
    copy_base_v4_repository: RepositoryCopyFactory,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)

    result = _certify(
        tmp_path,
        target_node_ids=("node-certify",),
    )

    assert result.node_ids == (
        "node-certify",
        "node-certify.source.gateway",
    )
    assert certifier.certificate_log_path(graph.nodes["node-certify"]).is_file()
    assert certifier.certificate_log_path(
        graph.nodes["node-certify.source.gateway"]
    ).is_file()


def test_private_writer_fails_closed_without_current_certifier(
    tmp_path: Path,
    copy_base_v4_repository: RepositoryCopyFactory,
) -> None:
    copy_base_v4_repository(tmp_path)
    shutil.rmtree(tmp_path / "skills" / "node-certify")
    repository = GitTestRepository(tmp_path)
    repository.git("add", "-A")
    repository.git("commit", "-qm", "remove certifier")

    with pytest.raises(certifier.CertificationError, match="node-certify"):
        _certify(tmp_path)


def test_private_writer_rotates_valid_history_then_rejects_invalid_signed_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_base_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)
    backend = MemorySecretBackend()
    target = "demo-skill.source.gateway"
    public_key_root = tmp_path / "public-keys"

    _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
    )
    new_key = rotate_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    )
    _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
    )

    path = certifier.certificate_log_path(graph.nodes[target])
    entries = parse_certificate_log(path.read_bytes(), public_key_root)
    assert len(entries) == 2
    assert entries[0]["payload"]["key_id"] != new_key.key_id
    assert entries[1]["payload"]["key_id"] == new_key.key_id
    assert entries[1]["payload"]["previous_entry_hash"] == certificate_entry_hash(
        entries[0]
    )
    log_path = path
    entry = parse_certificate_log(log_path.read_bytes(), public_key_root)[-1]
    invalid_payload = dict(entry["payload"])
    invalid_payload["previous_entry_hash"] = certificate_entry_hash(entry)
    invalid_payload["unexpected_field"] = []
    key = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    )
    invalid_entry = sign_certificate_payload(invalid_payload, key)
    invalid_bytes = (
        log_path.read_bytes()
        + canonical_certificate_envelope_bytes(invalid_entry)
        + b"\n"
    )
    log_path.write_bytes(invalid_bytes)
    events: list[str] = []

    with pytest.raises(certifier.CertificationError, match="history schema"):
        _certify(
            tmp_path,
            target_node_ids=(target,),
            secret_backend=backend,
            before_stale_issuance=lambda: events.append("mechanical"),
        )

    assert events == []
    assert log_path.read_bytes() == invalid_bytes


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    (
        ("clean", None),
        (
            "content",
            "worktree-differs-from-commit:skills/demo-skill/SKILL.md",
        ),
        (
            "worktree-mode",
            "worktree-mode-differs-from-commit:skills/demo-skill/SKILL.md",
        ),
        (
            "index-mode",
            "index-mode-differs-from-commit:skills/demo-skill/SKILL.md",
        ),
        ("missing", "unsafe-worktree-input:skills/demo-skill/SKILL.md"),
        ("symlink", "unsafe-worktree-input:skills/demo-skill/SKILL.md"),
        (
            "expected-hash",
            "expected-hash-mismatch:skills/demo-skill/SKILL.md",
        ),
    ),
)
def test_batched_readiness_preserves_canonical_per_path_decisions_without_git_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_reason: str | None,
) -> None:
    if mutation in {"worktree-mode", "symlink"} and sys.platform == "win32":
        # famulus-skip: category=platform-contract; reason=requires POSIX mode and symlink semantics; alternate=non-Windows parity cases
        pytest.skip("POSIX worktree behavior is unavailable")
    snapshot = GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT)
    relative_path = "skills/demo-skill/SKILL.md"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    committed_bytes = b"committed bytes\n"
    target.write_bytes(committed_bytes)
    object_id = "1" * 40
    index_mode = "100644"
    expected_hashes = {
        relative_path: "sha256:" + hashlib.sha256(committed_bytes).hexdigest()
    }
    if mutation == "content":
        target.write_text("changed\n", encoding="utf-8")
    elif mutation == "worktree-mode":
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
    elif mutation == "index-mode":
        index_mode = "100755"
    elif mutation == "missing":
        target.unlink()
    elif mutation == "symlink":
        target.unlink()
        target.symlink_to("replacement.md")
    elif mutation == "expected-hash":
        expected_hashes[relative_path] = "sha256:incorrect"

    operations: list[tuple[str, ...]] = []

    def batched_git(_repo_root: Path, *args: str, **_kwargs: object):
        operations.append(args)
        if args[0] == "ls-tree":
            stdout = f"100644 blob {object_id}\t{relative_path}\0".encode()
        elif args[0] == "ls-files":
            stdout = f"{index_mode} {object_id} 0\t{relative_path}\0".encode()
        elif args[0] == "cat-file":
            stdout = (
                f"{object_id} blob {len(committed_bytes)}\n".encode()
                + committed_bytes
                + b"\n"
            )
        else:
            raise AssertionError(f"unexpected Git operation: {args}")
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(certifier, "run_git", batched_git)
    batched = certifier.CommitReadinessInspector(
        snapshot,
        (target,),
        expected_hashes,
    ).inspect()

    assert batched.reasons == (() if expected_reason is None else (expected_reason,))
    if mutation == "clean":
        assert batched.stamp_worthy
        assert operations == [
            ("ls-tree", "-r", "-z", "--full-tree", snapshot.commit),
            ("ls-files", "--stage", "-z"),
            ("cat-file", "--batch"),
        ]


def test_commit_tree_filter_does_not_scan_the_requested_sequence_for_each_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT)

    class MembershipRejectingPaths(tuple[str, ...]):
        def __contains__(self, _value: object) -> bool:
            raise AssertionError("tree filtering must use a set")

    relative_paths = MembershipRejectingPaths(
        (
            "skills/demo-skill/SKILL.md",
            "skills/demo-skill/blueprint.yaml",
        )
    )

    def query_tree(_repo_root: Path, *args: str, **_kwargs: object):
        assert args == ("ls-tree", "-r", "-z", "--full-tree", VALID_COMMIT)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f"100644 blob {'1' * 40}\t{relative_paths[0]}\0"
                f"100644 blob {'2' * 40}\t{relative_paths[1]}\0"
                f"100644 blob {'3' * 40}\tunrelated.txt\0"
            ).encode(),
            stderr=b"",
        )

    monkeypatch.setattr(certifier, "run_git", query_tree)

    entries = certifier.CommitReadinessInspector(
        snapshot,
        (),
        {},
    )._commit_entries(relative_paths)

    assert entries is not None
    assert set(entries) == set(relative_paths)


def test_batched_readiness_names_each_failed_metadata_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT)
    path = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    relative_path = "skills/demo-skill/SKILL.md"
    cases = (
        ("ls-tree", "git-tree-query-failed", False),
        ("ls-files", "git-index-query-failed", False),
        ("cat-file", "git-blob-query-failed", False),
        ("cat-file", f"git-unavailable:{relative_path}", True),
    )
    for failed_operation, expected_reason, unavailable in cases:

        def fail_metadata_query(_repo_root: Path, *args: str, **_kwargs: object):
            if args[0] == failed_operation:
                if unavailable:
                    raise OSError("Git unavailable")
                return SimpleNamespace(
                    returncode=1,
                    stdout=b"",
                    stderr=b"query failed",
                )
            return _git_metadata_result(args[0], relative_path)

        monkeypatch.setattr(certifier, "run_git", fail_metadata_query)
        readiness = certifier.CommitReadinessInspector(
            snapshot,
            (path,),
            {},
        ).inspect()
        assert readiness.reasons == (expected_reason,)


def test_batched_readiness_preserves_unusual_tracked_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Square brackets exercise Git pathspec quoting while remaining a valid
    # filename on every supported platform (unlike `*` and `?` on Windows).
    relative_path = "skills/demo-skill/literal[edge].txt"
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_bytes(b"committed bytes\n")
    snapshot = GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT)

    monkeypatch.setattr(
        certifier,
        "run_git",
        lambda _repo_root, *args, **_kwargs: _git_metadata_result(
            args[0],
            relative_path,
        ),
    )

    readiness = certifier.CommitReadinessInspector(
        snapshot,
        (path,),
        {},
    ).inspect()

    assert readiness.stamp_worthy


def test_batched_readiness_matches_outside_repository_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT)
    outside_path = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside_path.write_text("outside\n", encoding="utf-8")
    monkeypatch.setattr(
        certifier,
        "run_git",
        lambda _repo_root, *args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"",
            stderr=b"",
        ),
    )

    batched = certifier.CommitReadinessInspector(
        snapshot,
        (outside_path,),
        {},
    ).inspect()

    assert batched.reasons == ("input-outside-repository",)


def test_batched_readiness_matches_conflicted_index_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_path = "skills/demo-skill/SKILL.md"
    path = tmp_path / relative_path
    snapshot = GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT)
    object_id = "1" * 40

    def conflicted_metadata(_repo_root: Path, *args: str, **_kwargs: object):
        if args[0] == "ls-files":
            stdout = (
                f"100644 {object_id} 1\t{relative_path}\0"
                f"100644 {'2' * 40} 2\t{relative_path}\0"
            ).encode()
            return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")
        return _git_metadata_result(args[0], relative_path, object_id=object_id)

    monkeypatch.setattr(certifier, "run_git", conflicted_metadata)

    batched = certifier.CommitReadinessInspector(
        snapshot,
        (path,),
        {},
    ).inspect()

    assert batched.reasons == (f"nonzero-index-stage:{relative_path}",)


def test_batched_readiness_matches_unsupported_commit_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_path = "skills/demo-skill/linked-input"
    path = tmp_path / relative_path
    snapshot = GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT)
    object_id = "1" * 40

    def unsupported_metadata(_repo_root: Path, *args: str, **_kwargs: object):
        if args[0] == "ls-tree":
            return SimpleNamespace(
                returncode=0,
                stdout=f"120000 blob {object_id}\t{relative_path}\0".encode(),
                stderr=b"",
            )
        return _git_metadata_result(args[0], relative_path, object_id=object_id)

    monkeypatch.setattr(certifier, "run_git", unsupported_metadata)

    batched = certifier.CommitReadinessInspector(
        snapshot,
        (path,),
        {},
    ).inspect()

    assert batched.reasons == (f"unsupported-commit-mode:{relative_path}",)


def test_read_worktree_file_uses_native_confined_reader_when_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT)
    calls: list[tuple[Path, Path, bool]] = []

    def native_read(
        path: Path,
        *,
        allowed_root: Path,
        allow_non_atomic: bool,
    ) -> bytes:
        calls.append((path, allowed_root, allow_non_atomic))
        return b"native bytes"

    monkeypatch.setattr(certifier, "read_regular_file_bytes", native_read)
    monkeypatch.setattr(certifier, "os", SimpleNamespace(name="nt"))
    inspector = certifier.CommitReadinessInspector(
        snapshot,
        (),
        {},
        allow_non_atomic=True,
    )

    result = inspector._read_worktree_file("skills/demo-skill/SKILL.md")

    assert result == (b"native bytes", None, None)
    assert calls == [
        (
            tmp_path / "skills" / "demo-skill" / "SKILL.md",
            tmp_path,
            True,
        )
    ]


@pytest.mark.parametrize(
    "race",
    [
        "content",
        "head",
        "basis",
        "checks",
        "log-replacement",
    ],
)
def test_private_writer_rederives_every_final_state_after_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
    copy_base_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)
    target = "demo-skill.source.gateway"

    def mutate(node_id: str) -> None:
        assert node_id == target
        if race == "content":
            (tmp_path / "skills" / "demo-skill" / "SKILL.md").write_text(
                "changed after append\n",
                encoding="utf-8",
            )
        elif race == "head":
            (tmp_path / "head-race.txt").write_text(
                "race\n",
                encoding="utf-8",
            )
            repository = GitTestRepository(tmp_path)
            repository.git("add", "head-race.txt")
            repository.git("commit", "-qm", "head race")
        elif race == "basis":
            policy = tmp_path / "references" / "certification" / "node-hash-policy.yaml"
            policy.write_text(
                policy.read_text(encoding="utf-8").replace(
                    "**/*.log",
                    "**/*.changed",
                ),
                encoding="utf-8",
            )
        elif race == "checks":
            monkeypatch.setitem(
                certifier.CERTIFIER_CHECK_REGISTRY,
                "deterministic",
                ("v4-deterministic", 2),
            )
        else:
            path = certifier.certificate_log_path(graph.nodes[node_id])
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)

    with pytest.raises(certifier.CertificationError, match="post-write|changed"):
        _certify(
            tmp_path,
            target_node_ids=(target,),
            after_append=mutate,
        )


def test_private_writer_rechecks_dependency_certificate_after_append(
    tmp_path: Path,
    copy_cross_owner_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_cross_owner_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, graph)
    target = "demo-skill.source.gateway"
    dependency_id = "demo-skill.source.contract"

    def corrupt_dependency(node_id: str) -> None:
        if node_id != target:
            return
        dependency = certifier.certificate_log_path(graph.nodes[dependency_id])
        with dependency.open("ab") as stream:
            stream.write(b"{}\n")

    with pytest.raises(certifier.CertificationError, match="post-write"):
        _certify(
            tmp_path,
            target_node_ids=(target,),
            require_migration_review=False,
            after_append=corrupt_dependency,
        )


def test_private_writer_orders_dependency_and_audits_exact_postorder_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_cross_owner_v4_repository: RepositoryCopyFactory,
    bind_repository_graph: RepositoryGraphBinder,
) -> None:
    copy_cross_owner_v4_repository(tmp_path)
    repository_graph = _load_repository_graph(tmp_path)
    bind_repository_graph(tmp_path, repository_graph)
    target = "demo-skill.source.gateway"
    dependency = "demo-skill.source.contract"
    backend = MemorySecretBackend()
    attempted: list[str] = []
    real_postorder = certifier.certification_target_postorder
    postorders: list[tuple[str, ...]] = []
    audit_scopes: list[tuple[str, ...]] = []

    def record_postorder(
        graph: object,
        states: object,
        requested: tuple[str, ...],
    ) -> tuple[str, ...]:
        result = real_postorder(graph, states, requested)
        postorders.append(result)
        return result

    def audit(self) -> tuple[tuple[str, str, tuple[object, ...]], ...]:
        assert not certifier.certificate_log_path(
            repository_graph.nodes[dependency]
        ).exists()
        assert not certifier.certificate_log_path(
            repository_graph.nodes[target]
        ).exists()
        audit_scopes.append(self._certification_node_ids)
        return ()

    def stop_dependency(node_id: str) -> None:
        attempted.append(node_id)
        if node_id == dependency:
            raise certifier.CertificationError("stop dependency issuance")

    monkeypatch.setattr(certifier, "certification_target_postorder", record_postorder)
    monkeypatch.setattr(certifier.RouteSmokeAuditor, "trace_dependencies", audit)

    with pytest.raises(
        certifier.CertificationError,
        match="stop dependency issuance",
    ):
        _certify(
            tmp_path,
            target_node_ids=(target,),
            secret_backend=backend,
            require_migration_review=False,
            before_append=stop_dependency,
        )

    assert attempted == [dependency]
    assert not certifier.certificate_log_path(repository_graph.nodes[target]).exists()
    assert not certifier.certificate_log_path(
        repository_graph.nodes[dependency]
    ).exists()
    assert postorders == [(dependency, target)]
    assert audit_scopes == [(dependency, target), (dependency, target)]

    postorders.clear()
    audit_scopes.clear()

    result = _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
        require_migration_review=False,
    )

    assert result.node_ids == (dependency, target)
    assert postorders == [
        (dependency, target),
    ]
    assert audit_scopes == [
        (dependency, target),
        (dependency, target),
    ]


def test_certifier_route_audit_rejects_unknown_scope_before_tracing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _synthetic_python_source_graph(tmp_path)
    states = {node_id: NodeHashState() for node_id in graph.nodes}

    monkeypatch.setattr(
        certifier,
        "trace_python_route_smoke_dependencies_batch",
        lambda *_args, **_kwargs: pytest.fail("unknown scope reached tracing"),
    )

    with pytest.raises(
        certifier.CertificationHashError,
        match="unknown route-smoke certification node: missing.source",
    ):
        certifier.RouteSmokeAuditor(
            graph,
            states,
            repo_root=tmp_path,
            certification_basis_paths=(),
            certification_node_ids=("missing.source",),
        ).trace_dependencies()


def test_route_auditor_prepares_once_but_traces_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _synthetic_python_source_graph(tmp_path)
    states = {node_id: NodeHashState() for node_id in graph.nodes}
    prepare_calls = 0
    trace_calls = 0
    real_prepare = certifier._python_route_smoke_trace_specs

    def prepare(*args: object, **kwargs: object):
        nonlocal prepare_calls
        prepare_calls += 1
        return real_prepare(*args, **kwargs)

    def trace(*_args: object, **_kwargs: object):
        nonlocal trace_calls
        trace_calls += 1
        return {}

    monkeypatch.setattr(certifier, "_python_route_smoke_trace_specs", prepare)
    monkeypatch.setattr(
        certifier,
        "trace_python_route_smoke_dependencies_batch",
        trace,
    )
    auditor = certifier.RouteSmokeAuditor(
        graph,
        states,
        repo_root=tmp_path,
        certification_basis_paths=(),
        certification_node_ids=("demo-skill",),
    )

    assert auditor.require_stable_dependencies() == ()
    assert prepare_calls == 1
    assert trace_calls == 2


def test_certifier_route_audit_batches_unique_source_entrypoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _synthetic_python_source_graph(tmp_path)
    states = {node_id: NodeHashState() for node_id in graph.nodes}
    source_id = "demo-skill.source.gateway"
    source = graph.nodes[source_id]
    worker = source.module_root / "_rtx" / "worker.py"
    target = certifier.PythonProcessTarget(
        Path("_rtx/worker.py"),
        "Interface",
    )
    batch_calls: list[
        tuple[
            Path,
            tuple[tuple[Path, certifier.PythonProcessTarget], ...],
        ]
    ] = []

    def trace_batch(
        repo_root: Path,
        specifications: tuple[
            tuple[Path, certifier.PythonProcessTarget],
            ...,
        ],
    ) -> dict[
        tuple[Path, certifier.PythonProcessTarget],
        tuple[Path, ...],
    ]:
        batch_calls.append((repo_root, tuple(specifications)))
        return {(source.module_root.resolve(), target): (worker,)}

    monkeypatch.setattr(
        certifier,
        "trace_python_route_smoke_dependencies_batch",
        trace_batch,
    )
    monkeypatch.setattr(
        certifier,
        "map_route_smoke_dependencies",
        lambda *_args, **_kwargs: (),
    )

    result = certifier.RouteSmokeAuditor(
        graph,
        states,
        repo_root=tmp_path,
        certification_basis_paths=(),
        certification_node_ids=(source_id,),
    ).trace_dependencies()

    assert batch_calls == [
        (
            tmp_path,
            ((source.module_root, target),),
        )
    ]
    assert result == ((source_id, target, ()),)


def test_route_audit_uses_schema_specific_logical_package_identity(
    immutable_legacy_route_graph: RepositoryBlueprintGraph,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = immutable_legacy_route_graph
    repo_root = next(iter(graph.nodes.values())).module_root.parents[1]
    source_id = "demo-rtx.source.runtime"
    source = graph.nodes[source_id]
    declaration = dict(source.declaration)
    interfaces = dict(declaration["interfaces"])
    interface_id = "demo-rtx.source.runtime.interface.execute"
    interface = dict(interfaces[interface_id])
    interface["process_binding"] = {
        "kind": "process",
        "entry": "Interface",
        "arguments": {},
        "fixed": [],
    }
    interfaces[interface_id] = interface
    declaration["interfaces"] = interfaces
    source = replace(source, declaration=declaration)
    graph = replace(
        graph,
        nodes={**graph.nodes, source_id: source},
    )
    observed = []

    def trace_batch(repo_root, specifications, **kwargs):
        normalized = tuple(specifications)
        observed.append((repo_root, normalized, kwargs))
        return {
            (source.module_root.resolve(), normalized[0][1]): (source.gateway_path,)
        }

    monkeypatch.setattr(
        certifier,
        "trace_python_route_smoke_dependencies_batch",
        trace_batch,
    )
    monkeypatch.setattr(
        certifier,
        "map_route_smoke_dependencies",
        lambda *_args, **_kwargs: (),
    )

    result = certifier.RouteSmokeAuditor(
        graph,
        {},
        repo_root=repo_root,
        certification_basis_paths=(),
        certification_node_ids=(source_id,),
        schema_root=LEGACY_SCHEMA_ROOT,
    ).trace_dependencies()

    target = observed[0][1][0][1]
    package = logical_python_package_name("demo-rtx")
    assert target.gateway_path == Path("runtime.py")
    assert target.logical_package == package
    assert target.logical_entrypoint == f"{package}.runtime"
    assert observed[0][2] == {
        "expected_schema_version": 5,
        "schema_root": LEGACY_SCHEMA_ROOT,
    }
    assert result == ((source_id, target, ()),)
    v6_graph = replace(
        graph,
        schema_version=6,
        nodes={
            **graph.nodes,
            source_id: replace(source, declaration=declaration),
        },
        source_modules={
            **graph.source_modules,
            source_id: "demo._rtx",
        },
    )

    specification = certifier._python_route_smoke_trace_specs(
        v6_graph,
        (source_id,),
    )[0]

    target = specification[2]
    package = logical_python_package_name("demo._rtx")
    assert target.gateway_path == Path("runtime.py")
    assert target.logical_package == package
    assert target.logical_entrypoint == f"{package}.runtime"


def test_private_writer_route_audit_failures_precede_key_and_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_base_v4_repository: RepositoryCopyFactory,
) -> None:
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)

    def reject(*_args: object, **_kwargs: object) -> None:
        raise certifier.CertificationHashError("route audit failed")

    monkeypatch.setattr(
        certifier.RouteSmokeAuditor,
        "trace_dependencies",
        reject,
    )

    with pytest.raises(certifier.CertificationError, match="route audit failed"):
        _certify(tmp_path)

    assert not certifier.certificate_log_path(graph.nodes["demo-skill"]).exists()
    assert not (certificate_public_key_root(tmp_path) / "active-key-id").exists()
    results = iter((("before",), ("after",)))

    monkeypatch.setattr(
        certifier.RouteSmokeAuditor,
        "trace_dependencies",
        lambda _self: next(results),
    )

    with pytest.raises(
        certifier.CertificationError,
        match="route-smoke dependency audit changed during certification",
    ):
        _certify(tmp_path)

    assert not certifier.certificate_log_path(graph.nodes["demo-skill"]).exists()
    assert not (certificate_public_key_root(tmp_path) / "active-key-id").exists()


def test_private_writer_rechecks_forced_untracked_input_after_append(
    tmp_path: Path,
    copy_base_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
) -> None:
    copy_base_v4_repository(tmp_path)
    source_blueprint = (
        tmp_path / "skills" / "demo-skill" / "blueprints" / "gateway.yaml"
    )
    declaration = yaml.safe_load(source_blueprint.read_text(encoding="utf-8"))
    declaration["content"].append(r"local\.txt")
    write_yaml(source_blueprint, declaration)
    module_blueprint = tmp_path / "skills" / "demo-skill" / "blueprint.yaml"
    module_declaration = yaml.safe_load(module_blueprint.read_text(encoding="utf-8"))
    module_declaration["content"].append(r"local\.txt")
    write_yaml(module_blueprint, module_declaration)
    policy_path = (
        tmp_path / "references" / "certification-policy" / "node-hash-policy.yaml"
    )
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["rules"].append(
        {
            "action": "include",
            "pattern": "skills/demo-skill/local.txt",
            "require_match": True,
        }
    )
    write_yaml(policy_path, policy)
    local_input = tmp_path / "skills" / "demo-skill" / "local.txt"
    local_input.write_text("forced local input\n", encoding="utf-8")
    repository = GitTestRepository(tmp_path)
    repository.git(
        "add",
        str(source_blueprint),
        str(module_blueprint),
        str(policy_path),
    )
    repository.git("commit", "-qm", "add local input policy")

    def mutate(_node_id: str) -> None:
        local_input.write_text("changed local input\n", encoding="utf-8")

    with pytest.raises(certifier.CertificationError, match="local input changed"):
        _certify(
            tmp_path,
            target_node_ids=("demo-skill.source.gateway",),
            require_migration_review=False,
            after_append=mutate,
        )


def test_private_writers_cannot_append_against_one_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copy_base_v4_repository: RepositoryCopyFactory,
    stable_empty_route_trace: None,
) -> None:
    copy_base_v4_repository(tmp_path)
    graph = _load_repository_graph(tmp_path)
    target = "demo-skill.source.gateway"
    backend = MemorySecretBackend()
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    )
    certifier.certificate_log_path(graph.nodes[target]).parent.mkdir()
    compare_barrier = threading.Barrier(2)
    real_compare = certifier.atomic_compare_and_append_bytes

    def synchronized_compare(*args: object, **kwargs: object) -> None:
        compare_barrier.wait()
        real_compare(*args, **kwargs)

    monkeypatch.setattr(
        certifier,
        "atomic_compare_and_append_bytes",
        synchronized_compare,
    )

    def issue() -> object:
        try:
            return _certify(
                tmp_path,
                target_node_ids=(target,),
                secret_backend=backend,
            )
        except certifier.CertificationError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: issue(), range(2)))

    assert (
        sum(isinstance(outcome, certifier.CertificationResult) for outcome in outcomes)
        == 1
    )
    assert (
        sum(isinstance(outcome, certifier.CertificationError) for outcome in outcomes)
        == 1
    )
    entries = parse_certificate_log(
        certifier.certificate_log_path(graph.nodes[target]).read_bytes(),
        public_key_root,
    )
    assert len(entries) == 1


def test_completeness_findings_block_structural_draft_signing(
    tmp_path: Path,
    copy_base_v4_repository: RepositoryCopyFactory,
) -> None:
    copy_base_v4_repository(tmp_path)
    module_path = tmp_path / "skills" / "demo-skill" / "blueprint.yaml"
    declaration = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    declaration.pop("description")
    module_path.write_text(
        yaml.safe_dump(declaration, sort_keys=False),
        encoding="utf-8",
    )
    repository = GitTestRepository(tmp_path)
    repository.git("add", ".")
    repository.git("commit", "-qm", "draft")
    graph = certifier.load_repository_blueprint_graph(
        tmp_path,
        schema_root=tmp_path / "references" / "blueprint-schema",
        expected_schema_version=4,
    )

    findings = certifier.certification_completeness_findings(graph)

    assert any(finding.field == "description" for finding in findings)
    with pytest.raises(
        certifier.CertificationError, match="certification completeness"
    ):
        _certify(tmp_path)


def _passed_mechanical_result() -> certifier.CommandResult:
    return certifier.CommandResult(
        name="validators",
        command=[sys.executable, "repo_checks.py", "--suite", "validators"],
        exit_code=0,
        stdout="",
        stderr="",
    )


def test_mechanical_gate_runs_only_the_repository_checks_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str], Path]] = []

    def run(
        name: str,
        command: list[str],
        *,
        repo_root: Path,
    ) -> certifier.CommandResult:
        calls.append((name, command, repo_root))
        return _passed_mechanical_result()

    monkeypatch.setattr(certifier, "run_local_command", run)

    result = certifier.run_mechanical_checks(tmp_path)

    assert result == _passed_mechanical_result()
    assert calls == [
        (
            "validators",
            [sys.executable, "repo_checks.py", "--suite", "validators"],
            tmp_path,
        )
    ]


def test_cli_propagates_explicit_non_atomic_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def issue(**kwargs: object):
        calls.append(dict(kwargs))
        return [_passed_mechanical_result()], []

    monkeypatch.setattr(certifier, "certify", issue)

    exit_code = certifier.main(
        [
            "certify",
            "demo-skill",
            "--allow-non-atomic",
            "--reviewed-repository",
            str(tmp_path),
            "--reviewed-commit",
            "a" * 40,
        ],
    )

    assert exit_code == 0
    assert calls[0]["allow_non_atomic"] is True


@pytest.mark.parametrize(
    ("allow_non_atomic", "overrides"),
    [
        (False, {}),
        (True, {"allow_non_atomic": True}),
    ],
    ids=("default-atomic", "explicit-non-atomic"),
)
def test_public_certification_resolves_one_target_without_hash_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_non_atomic: bool,
    overrides: dict[str, object],
) -> None:
    commit = VALID_COMMIT
    graph = _synthetic_repository_graph(tmp_path)
    calls: list[dict[str, object]] = []
    events: list[str] = []

    def issue(repo_root: Path, **kwargs: object):
        calls.append({"repo_root": repo_root, **kwargs})
        kwargs["before_stale_issuance"]()
        events.append("issue")
        return certifier.CertificationResult(
            node_ids=tuple(kwargs["target_node_ids"]),
            source_commit=commit,
        )

    monkeypatch.setattr(certifier, "_certify_repository", issue)
    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: graph,
    )
    monkeypatch.setattr(
        certifier,
        "run_mechanical_checks",
        lambda _repo_root: (events.append("mechanical") or _passed_mechanical_result()),
    )

    evidence, outcomes = certifier.certify(
        targets=("demo-skill",),
        reviewed_repository=tmp_path,
        reviewed_commit=commit,
        **overrides,
    )

    assert evidence == [_passed_mechanical_result()]
    assert events == ["mechanical", "issue"]
    assert len(calls) == 1
    assert calls[0]["repo_root"] == tmp_path.resolve()
    assert calls[0]["allow_non_atomic"] is allow_non_atomic
    assert calls[0]["expected_schema_version"] == 6
    assert calls[0]["schema_root"] == tmp_path / "references" / "blueprint-schema"
    assert set(calls[0]["target_node_ids"]) == {
        node_id
        for node_id, node in graph.nodes.items()
        if node.module_root == tmp_path / "skills" / "demo-skill"
    }
    assert outcomes[0].module == "demo-skill"
    assert outcomes[0].source == "reviewed-repository"
    assert outcomes[0].module_root == (tmp_path / "skills" / "demo-skill").resolve()
    assert all(
        node.certificate_path.parent.name == ".certificates"
        and node.certificate_path.suffix == ".jsonl"
        for node in outcomes[0].nodes
    )


def test_public_certification_reports_already_current_nodes_as_satisfied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = VALID_COMMIT
    graph = _synthetic_repository_graph(tmp_path)
    requested = tuple(
        sorted(
            node_id
            for node_id, node in graph.nodes.items()
            if node.module_root == tmp_path / "skills" / "demo-skill"
        )
    )
    mechanical_calls: list[Path] = []

    monkeypatch.setattr(
        certifier,
        "_certify_repository",
        lambda *_args, **_kwargs: certifier.CertificationResult(
            node_ids=(),
            current_node_ids=requested,
            source_commit=commit,
        ),
    )
    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: graph,
    )
    monkeypatch.setattr(
        certifier,
        "run_mechanical_checks",
        lambda repo_root: (
            mechanical_calls.append(repo_root) or _passed_mechanical_result()
        ),
    )

    evidence, outcomes = certifier.certify(
        targets=("demo-skill",),
        reviewed_repository=tmp_path,
        reviewed_commit=commit,
    )

    assert tuple(node.node_id for node in outcomes[0].nodes) == requested
    assert {node.status for node in outcomes[0].nodes} == {"certificate-current"}
    assert outcomes[0].as_payload()["status"] == "certificate-current"
    assert "certificate-current" in certifier.render_text(outcomes)
    assert evidence == []
    assert mechanical_calls == []


def test_public_certification_without_targets_selects_all_reviewed_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = VALID_COMMIT
    graph = _synthetic_repository_graph(
        tmp_path,
        modules=(
            ("demo-skill", "demo-skill"),
            ("other-skill", "other-skill"),
        ),
    )
    expected_modules = tuple(
        sorted(
            node.node_id for node in graph.nodes.values() if node.node_type == "module"
        )
    )
    calls: list[dict[str, object]] = []

    def issue(_repo_root: Path, **kwargs: object):
        calls.append(dict(kwargs))
        return certifier.CertificationResult(
            node_ids=tuple(kwargs["target_node_ids"]),
            source_commit=commit,
        )

    monkeypatch.setattr(certifier, "_certify_repository", issue)
    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: graph,
    )
    monkeypatch.setattr(
        certifier,
        "run_mechanical_checks",
        lambda _repo_root: _passed_mechanical_result(),
    )

    _evidence, outcomes = certifier.certify(
        targets=(),
        reviewed_repository=tmp_path,
        reviewed_commit=commit,
    )

    assert len(calls) == 1
    assert {outcome.module for outcome in outcomes} == set(expected_modules)
    assert {outcome.source for outcome in outcomes} == {"reviewed-repository"}
    assert calls[0]["target_node_ids"] == tuple(sorted(graph.nodes))


def test_reviewed_target_resolution_is_exact_deduplicated_and_fail_closed(
    tmp_path: Path,
) -> None:
    graph = _synthetic_repository_graph(tmp_path, schema_version=4)
    module_root = (tmp_path / "skills" / "demo-skill").resolve()

    resolved = certifier.resolve_reviewed_repository_targets(
        graph,
        ("demo-skill", module_root.as_posix(), "demo-skill"),
    )

    assert tuple(node.node_id for node in resolved) == ("demo-skill",)
    with pytest.raises(
        certifier.CertificationError,
        match="target 'missing-skill' resolves to 0 modules",
    ):
        certifier.resolve_reviewed_repository_targets(graph, ("missing-skill",))

    duplicate = replace(
        graph.nodes["demo-skill"],
        node_id="duplicate-module",
    )
    ambiguous_graph = replace(
        graph,
        nodes={**graph.nodes, duplicate.node_id: duplicate},
    )
    with pytest.raises(
        certifier.CertificationError,
        match="resolves to 2 modules",
    ):
        certifier.resolve_reviewed_repository_targets(
            ambiguous_graph,
            (module_root.as_posix(),),
        )


def test_reviewed_target_resolution_supports_distinct_module_id_and_name(
    tmp_path: Path,
) -> None:
    graph = _synthetic_repository_graph(
        tmp_path,
        modules=(("demo-module-id", "demo-skill"),),
        schema_version=4,
    )
    renamed = graph.nodes["demo-module-id"]

    by_id = certifier.resolve_reviewed_repository_targets(
        graph,
        ("demo-module-id",),
    )
    by_name = certifier.resolve_reviewed_repository_targets(
        graph,
        ("demo-skill",),
    )

    assert by_id == (renamed,)
    assert by_name == (renamed,)


def test_legacy_reviewed_target_resolution_never_uses_runtime_basename(
    immutable_legacy_route_graph: RepositoryBlueprintGraph,
) -> None:
    graph = immutable_legacy_route_graph

    assert tuple(
        node.node_id
        for node in certifier.resolve_reviewed_repository_targets(
            graph,
            ("demo-rtx",),
        )
    ) == ("demo-rtx",)
    with pytest.raises(
        certifier.CertificationError,
        match="target '_rtx' resolves to 0 modules",
    ):
        certifier.resolve_reviewed_repository_targets(graph, ("_rtx",))


def test_public_certification_has_no_mechanical_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = VALID_COMMIT
    graph = _synthetic_repository_graph(tmp_path)
    signed = False

    def fail_mechanical(_repo_root: Path) -> certifier.CommandResult:
        raise certifier.CertificationError("mechanical certification checks failed")

    def issue(*_args: object, **kwargs: object) -> object:
        nonlocal signed
        kwargs["before_stale_issuance"]()
        signed = True
        pytest.fail("signing ran after the mechanical gate failed")

    monkeypatch.setattr(certifier, "run_mechanical_checks", fail_mechanical)
    monkeypatch.setattr(certifier, "_certify_repository", issue)
    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: graph,
    )

    with pytest.raises(
        certifier.CertificationError,
        match="mechanical certification checks failed",
    ):
        certifier.certify(
            targets=("demo-skill",),
            reviewed_repository=tmp_path,
            reviewed_commit=commit,
        )

    assert not signed
    assert "--skip-mechanical" not in certifier.build_parser().format_help()
