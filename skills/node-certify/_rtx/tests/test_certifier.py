from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
import officina.git.provenance as git_provenance

MODULE_PATH = Path(__file__).resolve().parents[1] / "_node_certifier.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from officina.blueprints.graph import BlueprintNode, RepositoryBlueprintGraph
from officina.certification.hashing import NodeHashState
from officina.git.provenance import GitSnapshot
from test_support.git_repository import GitTestRepository

SPEC = importlib.util.spec_from_file_location("skill_certifier_certifier", MODULE_PATH)
certifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = certifier
SPEC.loader.exec_module(certifier)

VALID_COMMIT = "a" * 40


@pytest.fixture
def stable_empty_route_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate writer tests whose observable is independent of route tracing."""
    monkeypatch.setattr(
        certifier.RouteSmokeAuditor,
        "trace_dependencies",
        lambda _self: (),
    )

def _load_repository_graph(repo: Path):
    return certifier.load_repository_blueprint_graph(
        repo,
        schema_root=repo / "references" / "blueprint-schema",
    )

def _bind_repository_graph(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
    *,
    schema_root: Path,
) -> None:
    resolved_root = repo_root.resolve()
    resolved_schema_root = schema_root.resolve()
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
    ) -> RepositoryBlueprintGraph:
        if (
            Path(requested_root).resolve() == resolved_root
            and schema_root is not None
            and Path(schema_root).resolve() == resolved_schema_root
        ):
            return graph
        return physical_load(
            requested_root,
            schema_root=schema_root,
        )

    monkeypatch.setattr(certifier, "load_repository_blueprint_graph", load)

def _synthetic_repository_graph(
    repo_root: Path,
    *,
    modules: tuple[tuple[str, str], ...] = (("demo-skill", "demo-skill"),),
    schema_version: int = 6,
) -> RepositoryBlueprintGraph:
    nodes: dict[str, BlueprintNode] = {}
    module_sources: dict[str, tuple[str, ...]] = {}
    source_modules: dict[str, str] = {}
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
        source_modules[source_id] = module_id
    return RepositoryBlueprintGraph(
        nodes=nodes,
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        module_sources=module_sources,
        source_modules=source_modules,
        schema_version=schema_version,
    )


def _synthetic_python_source_graph(repo_root: Path) -> RepositoryBlueprintGraph:
    graph = _synthetic_repository_graph(repo_root, schema_version=6)
    source_id = "demo-skill.source.gateway"
    source = graph.nodes[source_id]
    binding = {
        "kind": "process",
        "entry": "Interface",
        "arguments": {},
        "fixed": [],
    }
    declaration = {
        "schema_version": 6,
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
        "secret_backend": None,
        "reviewed_commit": snapshot.commit,
        "certified_at": "2026-07-20T12:00:00Z",
        "schema_root": repo / "references" / "blueprint-schema",
    }
    options.update(overrides)
    return certifier._certify_repository(repo, **options)


def _scoped_writer_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use synthetic v6 declarations while keeping Git, scope checks and signing real."""
    repository = GitTestRepository.initialize_existing_empty(tmp_path)
    graph = _synthetic_repository_graph(tmp_path, modules=(
        ("demo-skill", "demo-skill"), ("unrelated-skill", "unrelated-skill"),
        ("node-certify", "node-certify"),
    ))
    for node in graph.nodes.values():
        node.declaration.update({"description": "Fixture instructions.", "gateway": {"language": "Markdown"}})
        for path in (node.blueprint_path, node.gateway_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Fixture instructions.\n")
    (tmp_path / ".gitignore").write_text("public-keys/\n.certificates/\n")
    repository.git("add", ".")
    repository.git("commit", "-qm", "v6 scoped writer fixture")
    commit = certifier.capture_git_snapshot(tmp_path).commit
    basis_hash = "sha256:" + "b" * 64
    states = {
        node_id: NodeHashState(
            node_hash="sha256:" + hashlib.sha256(node_id.encode()).hexdigest(),
            certification_basis_hash=basis_hash,
            input_manifest=({
                "path": node.gateway_path.relative_to(tmp_path).as_posix(),
                "digest": "sha256:" + hashlib.sha256(node.gateway_path.read_bytes()).hexdigest(),
                "git_provenance": "tracked",
            },),
        )
        for node_id, node in graph.nodes.items()
    }
    identity = {
        "interface": "node-certify._rtx.interface.certify", "version": 2,
        "node_hash": states["node-certify"].node_hash, "source_commit": commit,
    }
    secrets = {}
    backend = SimpleNamespace(
        name="memory",
        store=lambda namespace, key, secret: secrets.__setitem__((namespace, key), secret),
        lookup=lambda namespace, key: secrets.get((namespace, key)),
    )
    (tmp_path / "public-keys").mkdir()
    key = certifier.load_or_create_certificate_signing_key(
        tmp_path / "public-keys", secret_backend=backend,
    )
    monkeypatch.setattr(certifier, "load_or_create_certificate_signing_key", lambda *_a, **_k: key)
    monkeypatch.setattr(certifier, "load_repository_blueprint_graph", lambda *_a, **_k: graph)
    monkeypatch.setattr(certifier, "compute_node_hash_states", lambda *_a, **_k: states)
    monkeypatch.setattr(certifier, "resolve_certification_basis_paths", lambda *_a, **_k: ())
    monkeypatch.setattr(certifier, "_hash_certification_basis_paths", lambda *_a, **_k: basis_hash)
    monkeypatch.setattr(certifier, "derive_certifier_identity", lambda *_a, **_k: identity)
    return graph, states, commit


def _certify_scoped(tmp_path: Path, **kwargs):
    return _certify(
        tmp_path, target_node_ids=("demo-skill.source.gateway",), exact_target=True,
        schema_root=SRC_ROOT.parent / "references/blueprint-schema", **kwargs,
    )


def test_exact_writer_requires_current_dependency_without_expanding_target(tmp_path, monkeypatch):
    graph, states, _commit = _scoped_writer_fixture(tmp_path, monkeypatch)
    target, dependency = "demo-skill.source.gateway", "node-certify.source.gateway"
    states[target] = replace(states[target], dependency_hashes=({
        "relation": "uses-source", "target": dependency, "version": 1,
        "node_hash": states[dependency].node_hash,
    },))
    with pytest.raises(certifier.CertificationError, match="requires current dependencies"):
        _certify_scoped(tmp_path)
    assert not certifier.certificate_log_path(graph.nodes[target]).exists()
    assert not certifier.certificate_log_path(graph.nodes[dependency]).exists()
    options = {"exact_target": True, "schema_root": SRC_ROOT.parent / "references/blueprint-schema"}
    assert _certify(tmp_path, target_node_ids=(dependency,), **options).node_ids == (dependency,)
    dependency_log = certifier.certificate_log_path(graph.nodes[dependency]).read_bytes()
    assert _certify_scoped(tmp_path).node_ids == (target,)
    assert certifier.certificate_log_path(graph.nodes[dependency]).read_bytes() == dependency_log
    assert not certifier.certificate_log_path(graph.nodes["demo-skill"]).exists()


def test_payload_uses_current_certificate_schema(
    tmp_path: Path,
) -> None:
    commit = VALID_COMMIT
    graph = _synthetic_repository_graph(tmp_path)
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

    v6_payload = certifier._build_certificate_payload(
        tmp_path,
        graph,
        states,
        node_id,
        **common,
    )
    assert v6_payload["certificate_schema_version"] == 3
    assert v6_payload["facets"] == []


def test_gate_records_use_the_v6_registry() -> None:
    assert certifier._passed_check("deterministic") == {
        "id": "v6-deterministic",
        "version": 1,
        "passed": True,
        "findings": [],
    }


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
        schema_version=6,
        nodes={source_id: node},
        source_modules={source_id: "node-certify"},
    )
    with pytest.raises(
        certifier.CertificationError,
        match="node-certify._rtx",
    ):
        certifier._verify_executing_candidate_certifier(
            root,
            parent_owned,
            {source_id: state},
        )

    child_owned = SimpleNamespace(
        schema_version=6,
        nodes={source_id: node},
        source_modules={source_id: "node-certify._rtx"},
    )
    certifier._verify_executing_candidate_certifier(
        root,
        child_owned,
        {source_id: state},
    )


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

    monkeypatch.setattr(git_provenance, "run_git", batched_git)
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

    monkeypatch.setattr(git_provenance, "run_git", query_tree)

    entries = git_provenance._commit_entries_batch(snapshot, relative_paths)

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

        monkeypatch.setattr(git_provenance, "run_git", fail_metadata_query)
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
        git_provenance,
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
        git_provenance,
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

    monkeypatch.setattr(git_provenance, "run_git", conflicted_metadata)

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

    monkeypatch.setattr(git_provenance, "run_git", unsupported_metadata)

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
    path = tmp_path / "skills/demo-skill/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"committed bytes\n")
    calls = []

    def native_read(path, *, allowed_root, allow_non_atomic):
        calls.append((path, allowed_root, allow_non_atomic))
        return path.read_bytes()

    monkeypatch.setattr(git_provenance, "read_regular_file_bytes", native_read)
    monkeypatch.setattr(git_provenance, "_use_native_confined_read", lambda: True)
    monkeypatch.setattr(git_provenance, "run_git", lambda _root, *args, **_kw:
                        _git_metadata_result(args[0], "skills/demo-skill/SKILL.md"))
    result = certifier.CommitReadinessInspector(
        GitSnapshot(repo_root=tmp_path, commit=VALID_COMMIT), (path,), {}, allow_non_atomic=True,
    ).inspect()
    assert result.stamp_worthy, result.reasons
    assert calls == [(path, tmp_path, True)]


def test_certifier_route_audit_rejects_non_v6_graph_before_tracing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _synthetic_repository_graph(tmp_path, schema_version=5)
    states = {node_id: NodeHashState() for node_id in graph.nodes}

    monkeypatch.setattr(
        certifier,
        "trace_python_route_smoke_dependencies_batch",
        lambda *_args, **_kwargs: pytest.fail("unknown scope reached tracing"),
    )

    with pytest.raises(
        certifier.CertificationHashError,
        match="route-smoke certification requires a schema v6 graph",
    ):
        certifier.RouteSmokeAuditor(
            graph,
            states,
            repo_root=tmp_path,
            certification_basis_paths=(),
            certification_node_ids=("demo-skill.source.gateway",),
        ).trace_dependencies()


@pytest.mark.parametrize("reuse_graph", [False, True])
def test_route_auditor_prepares_once_but_traces_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reuse_graph: bool,
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
        assert _kwargs == ({"prepared_graph": graph} if reuse_graph else {})
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
        reuse_graph=reuse_graph,
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
    logical_package = certifier.logical_python_package_name("demo-skill")
    target = certifier.PythonProcessTarget(
        Path("_rtx/worker.py"),
        "Interface",
        logical_package=logical_package,
        logical_entrypoint=f"{logical_package}._rtx.worker",
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


def _passed_mechanical_result() -> certifier.CommandResult:
    return certifier.CommandResult(
        name="validators",
        command=[sys.executable, "repo_checks.py", "--suite", "validators"],
        exit_code=0,
        stdout="",
        stderr="",
    )


def test_v6_writer_rejects_a_predecessor_log_race(tmp_path: Path) -> None:
    graph = _synthetic_repository_graph(tmp_path)
    node_id = "demo-skill"
    log_path = certifier.certificate_log_path(graph.nodes[node_id])
    log_path.parent.mkdir(parents=True)
    log_path.write_bytes(b"observed\n")
    issuer = object.__new__(certifier.CertificateBatchIssuer)
    issuer._graph = graph
    issuer._allow_non_atomic = False
    issuer._require_unchanged_log(node_id, log_path, b"observed\n")
    log_path.write_bytes(b"raced\n")
    with pytest.raises(certifier.CertificationError, match="log changed"):
        issuer._require_unchanged_log(node_id, log_path, b"observed\n")
    assert log_path.read_bytes() == b"raced\n"


def test_mechanical_gate_reports_validator_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        certifier,
        "run_local_command",
        lambda *_args, **_kwargs: certifier.CommandResult(
            name="validators", command=[], exit_code=1,
            stdout="validator finding\n", stderr="missing dependency\n",
        ),
    )
    with pytest.raises(certifier.CertificationError) as error:
        certifier.run_mechanical_checks(tmp_path, validation_paths=(), validation_node_ids=())
    assert str(error.value) == (
        "mechanical certification checks failed: validators (exit 1)\n"
        "validator finding\nmissing dependency\n"
    )


@pytest.mark.parametrize("order", [(), ("target", "dependency")])
def test_mechanical_subjects_and_receipt_keep_only_selected_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: tuple[str, ...],
) -> None:
    states = {node: NodeHashState(node_hash="sha256:" + "a" * 64,
        input_manifest=({"path": path},)) for node, path in (
            ("target", "skills/demo/main.py"), ("dependency", "src/common.py"),
            ("authority", "validators/unrelated.py"))}
    paths = certifier.mechanical_validation_paths(states, order)
    assert paths == (() if not order else ("skills/demo/main.py", "src/common.py"))

    def run(name, command, *, repo_root):
        assert name == "validators"
        assert repo_root == tmp_path
        assert command[:-2] == [sys.executable, "repo_checks.py", "--suite", "validators"]
        assert command[-2] == "--validation-scope-file"
        assert json.loads(Path(command[-1]).read_text(encoding="utf-8")) == {"paths": list(paths), "node_ids": list(order)}
        return _passed_mechanical_result()

    monkeypatch.setattr(certifier, "run_local_command", run)
    result = certifier.run_mechanical_checks(tmp_path, validation_paths=paths, validation_node_ids=order)
    assert result.passed and result.as_payload()["validation_paths"] == list(paths)
    assert result.as_payload()["validation_node_ids"] == list(order)


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
        kwargs["before_stale_issuance"](("skills/demo-skill/main.py",), ("demo-skill",))
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
        lambda _repo_root, **_kwargs: (events.append("mechanical") or _passed_mechanical_result()),
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
    assert calls[0]["scope_whole_graph"] is False
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
        lambda repo_root, **_kwargs: (
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
        lambda _repo_root, **_kwargs: _passed_mechanical_result(),
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
    assert calls[0]["scope_whole_graph"] is True


def test_reviewed_target_resolution_is_exact_deduplicated_and_fail_closed(
    tmp_path: Path,
) -> None:
    graph = _synthetic_repository_graph(tmp_path)
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


def test_public_certification_has_no_mechanical_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = VALID_COMMIT
    graph = _synthetic_repository_graph(tmp_path)
    signed = False

    def fail_mechanical(_repo_root: Path, **_kwargs) -> certifier.CommandResult:
        raise certifier.CertificationError("mechanical certification checks failed")

    def issue(*_args: object, **kwargs: object) -> object:
        nonlocal signed
        kwargs["before_stale_issuance"](("skills/demo-skill/main.py",), ("demo-skill",))
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


def test_scoped_writer_ignores_unrelated_dirt_and_incomplete_contract(tmp_path, monkeypatch):
    graph, states, commit = _scoped_writer_fixture(tmp_path, monkeypatch)
    unrelated = graph.nodes["unrelated-skill.source.gateway"]
    unrelated.declaration["interfaces"] = {"unrelated-skill.source.gateway.interface.run": {}}
    unrelated.gateway_path.write_text("unrelated pending edit\n")
    GitTestRepository(tmp_path).git("add", str(unrelated.gateway_path))
    before = unrelated.gateway_path.read_bytes()
    result = _certify_scoped(tmp_path)
    target = "demo-skill.source.gateway"
    assert result.node_ids == (target,)
    assert unrelated.gateway_path.read_bytes() == before
    report = certifier.evaluate_certificate_currentness(
        graph, states, repo_root=tmp_path, public_key_root=tmp_path / "public-keys",
        source_commit=commit, certifier_identity=certifier.derive_certifier_identity(graph, states, commit),
        certification_basis_paths=certifier.resolve_certification_basis_paths(tmp_path),
        checks_by_node={key: certifier.expected_certifier_checks() for key in graph.nodes},
        schema_root=SRC_ROOT.parent / "references/blueprint-schema",
    )
    assert report.nodes[target].current, report.nodes[target].concerns


@pytest.mark.parametrize("race", ("target", "authority", "index", "membership", "unrelated", "entry-scope"))
def test_scoped_writer_preserves_relevant_append_guards(tmp_path, monkeypatch, race):
    graph, states, _commit = _scoped_writer_fixture(tmp_path, monkeypatch)
    target = "demo-skill.source.gateway"

    def mutate(_node_id):
        if race == "membership":
            states[target] = replace(states[target], dependency_hashes=(*states[target].dependency_hashes, {
                "relation": "scope-test", "target": "unrelated-skill.source.gateway", "version": 1,
                "node_hash": states["unrelated-skill.source.gateway"].node_hash,
            }))
        elif race == "index":
            GitTestRepository(tmp_path).git("update-index", "--chmod=+x", str(graph.nodes[target].gateway_path))
        else:
            node_id = {"target": target, "authority": "node-certify", "unrelated": "unrelated-skill.source.gateway"}[race]
            graph.nodes[node_id].gateway_path.write_text("changed during append\n")

    if race == "unrelated":
        assert _certify_scoped(tmp_path, before_append=mutate).node_ids == (target,)
    else:
        with pytest.raises(certifier.CertificationError, match="changed"):
            _certify_scoped(
                tmp_path, before_append=mutate,
                expected_scope_identity="stale-scope" if race == "entry-scope" else None,
            )
        assert not certifier.certificate_log_path(graph.nodes[target]).exists()


@pytest.mark.parametrize("changed", ["node_hash", "dependency_hashes", "certification_basis_hash", "facets"])
def test_exact_writer_rejects_audit_identity_from_different_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    """A caller-side drift check cannot bind the writer's independently loaded inputs."""

    graph, states, _commit = _scoped_writer_fixture(tmp_path, monkeypatch)
    target = "demo-skill.source.gateway"
    expected = certifier.audited_inputs(states[target])
    expected[changed] = (
        [{"target": "provider", "hash": "previous-provider-hash"}]
        if changed in {"dependency_hashes", "facets"} else "sha256:" + "0" * 64
    )
    with pytest.raises(certifier.CertificationError, match="audited inputs changed"):
        _certify(
            tmp_path, target_node_ids=(target,), exact_target=True,
            expected_audited_inputs={target: expected},
        )
    assert not certifier.certificate_log_path(graph.nodes[target]).exists()


def test_rutter_python_api_contracts_satisfy_signing_completeness() -> None:
    """Schema-valid Rutter APIs must not block every live certification run."""
    directory = SRC_ROOT / "officina/rutter/blueprints"
    nodes = {}
    for name in ("authoring", "evaluation", "history", "reducer", "values"):
        path = directory / f"{name}.yaml"
        declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
        nodes[declaration["id"]] = SimpleNamespace(
            declaration=declaration, node_type="behavioral_source", blueprint_path=path,
        )
    assert certifier.certification_completeness_findings(SimpleNamespace(nodes=nodes)) == ()


def test_public_exact_node_certification_never_expands_the_selected_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expanding an exact source would allow unaudited siblings to be signed."""

    commit = VALID_COMMIT
    graph = _synthetic_repository_graph(tmp_path)
    _states = {node_id: NodeHashState(node_hash="sha256:" + "a" * 64) for node_id in graph.nodes}
    source_id = "demo-skill.source.gateway"
    calls: list[dict[str, object]] = []

    def issue(_repo_root: Path, **kwargs: object):
        calls.append(dict(kwargs))
        kwargs["before_stale_issuance"](("skills/demo-skill/main.py",), ("demo-skill",))
        return certifier.CertificationResult(
            node_ids=(source_id,),
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
        lambda _repo_root, **_kwargs: _passed_mechanical_result(),
    )

    evidence, outcome = certifier.certify_exact_node(
        node_id=source_id,
        reviewed_repository=tmp_path,
        reviewed_commit=commit,
        expected_audited_inputs=certifier.audited_inputs(_states[source_id]),
        scope_target_node_ids=tuple(graph.nodes),
        expected_scope_identity="reviewed-whole-graph-scope",
        scope_whole_graph=True,
    )

    assert calls[0]["target_node_ids"] == (source_id,)
    assert calls[0]["exact_target"] is True
    assert calls[0]["scope_target_node_ids"] == tuple(graph.nodes)
    assert calls[0]["expected_scope_identity"] == "reviewed-whole-graph-scope"
    assert calls[0]["scope_whole_graph"] is True
    assert calls[0]["expected_audited_inputs"] == {
        source_id: certifier.audited_inputs(_states[source_id])
    }
    assert evidence == [_passed_mechanical_result()]
    assert outcome.node_id == source_id
    assert outcome.status == "certificate-issued"
