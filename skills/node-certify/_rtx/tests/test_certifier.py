from __future__ import annotations

import hashlib
import importlib.util
import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

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
