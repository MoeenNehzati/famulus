from __future__ import annotations

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

import pytest
import yaml

MODULE_PATH = Path(__file__).resolve().parents[1] / "_node_certifier.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
TEST_ROOT = MODULE_PATH.parents[3] / "tests"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from officina.common.certificate_records import (
    certificate_public_key_root,
    certificate_entry_hash,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    rotate_certificate_signing_key,
)
from officina.runtime.python_machine_interface import (
    logical_python_package_name,
)
from v4_certification_fixtures import (
    MemorySecretBackend,
    contract,
    create_v4_repository,
    write_yaml,
)
from v5_blueprint_fixtures import copy_v5_fixture_tree
from test_support.git_repository import GitTestRepository

SPEC = importlib.util.spec_from_file_location("skill_certifier_certifier", MODULE_PATH)
certifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = certifier
SPEC.loader.exec_module(certifier)

V5_SCHEMA_ROOT = MODULE_PATH.parents[3] / "references" / "blueprint"
V5_AUTHORIZATION_FIXTURE = (
    MODULE_PATH.parents[3]
    / "tests"
    / "fixtures"
    / "blueprint_v5"
    / "authorization"
)


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
    ):
        assert not hasattr(certifier, name)
    assert "compute-hashes" not in certifier.Interface.dispatches


def test_repository_fixture_preserves_exact_bytes_under_ambient_autocrlf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    repository = tmp_path / "repo"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    create_v4_repository(repository)
    tracked = repository / "exact-bytes.txt"
    tracked.write_bytes(b"exact\r\nbytes\r\n")

    git = GitTestRepository(repository)
    git.git("add", "exact-bytes.txt")
    git.git("commit", "-qm", "exact bytes")
    committed = git.git("show", "HEAD:exact-bytes.txt").stdout

    assert committed == tracked.read_bytes()


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
        "schema_root": repo / "references" / "blueprint",
        "require_migration_review": True,
    }
    options.update(overrides)
    return certifier._certify_v4_repository(repo, **options)


def test_payload_issuance_is_v1_for_frozen_v4_and_v2_for_v5(
    tmp_path: Path,
) -> None:
    graph, states, commit = create_v4_repository(tmp_path)
    node_id = "demo-skill"
    common = {
        "source_commit": commit,
        "key_id": "sha256:" + "a" * 64,
        "previous_entry_hash": None,
        "certifier_identity": {
            "interface": "skill-certifier.interface.certify",
            "version": 1,
            "node_hash": "sha256:" + "b" * 64,
            "source_commit": commit,
        },
        "checks": (),
        "certified_at": "2026-07-20T12:00:00Z",
    }

    assert certifier._v4_payload(
        tmp_path,
        graph,
        states,
        node_id,
        **common,
    )["certificate_schema_version"] == 1
    assert certifier._v4_payload(
        tmp_path,
        replace(graph, schema_version=5),
        states,
        node_id,
        expected_schema_version=5,
        **common,
    )["certificate_schema_version"] == 2


def test_v5_gate_records_use_the_version_selected_registry() -> None:
    assert certifier._passed_v4_check(
        "deterministic",
        expected_schema_version=5,
    ) == {
        "id": "v5-deterministic",
        "version": 1,
        "passed": True,
        "findings": [],
    }
    assert certifier._passed_v4_check("deterministic")["id"] == "v4-deterministic"


def test_v5_executing_certifier_must_be_owned_by_runtime_child() -> None:
    root = MODULE_PATH.parents[3]
    executing = Path(certifier.__file__).resolve()
    source_id = "skill-certifier.source.certifier"
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
        source_modules={source_id: "skill-certifier"},
    )
    with pytest.raises(
        certifier.CertificationError,
        match="skill-certifier-rtx",
    ):
        certifier._verify_executing_candidate_certifier(
            root,
            parent_owned,
            {source_id: state},
        )

    child_owned = SimpleNamespace(
        schema_version=5,
        nodes={source_id: node},
        source_modules={source_id: "skill-certifier-rtx"},
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
        schema_root=repo / "references" / "blueprint",
        expected_schema_version=4,
    )


def _as_v5_graph(graph):
    return replace(
        graph,
        schema_version=5,
        nodes={
            node_id: replace(
                node,
                declaration={**node.declaration, "schema_version": 5},
            )
            for node_id, node in graph.nodes.items()
        },
    )


def test_live_certification_provisions_missing_canonical_key_root(
    tmp_path: Path,
) -> None:
    _graph, _states, commit = create_v4_repository(tmp_path)
    public_key_root = certificate_public_key_root(tmp_path)

    result = _certify(
        tmp_path,
        public_key_root=public_key_root,
        secret_backend=MemorySecretBackend(),
    )

    assert result.source_commit == commit
    assert (public_key_root / "active-key-id").is_file()


def test_private_writer_issues_parseable_append_only_certificate(
    tmp_path: Path,
) -> None:
    graph, _states, commit = create_v4_repository(tmp_path)

    result = _certify(tmp_path)

    assert result.node_ids == (
        "demo-skill",
        "demo-skill.source.gateway",
    )
    assert result.source_commit == commit
    path = certifier.certificate_log_path(graph.nodes["demo-skill"])
    entries = parse_certificate_log(path.read_bytes(), tmp_path / "public-keys")
    assert len(entries) == 1
    payload = entries[0]["payload"]
    assert payload["subject"]["id"] == "demo-skill"
    assert payload["source_commit"] == commit
    assert payload["checks"] == list(certifier.expected_certifier_checks(4))
    assert {
        (check["id"], check["version"]) for check in payload["checks"]
    } >= {("route-smoke-dependencies", 1)}


def test_selected_v5_writer_issues_only_payload_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "modules",
        tmp_path / "modules",
    )
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "skills",
        tmp_path / "skills",
    )
    policy_source = (
        MODULE_PATH.parents[3]
        / "references"
        / "certification"
        / "node-hash-policy.yaml"
    )
    policy_path = (
        tmp_path
        / "references"
        / "certification"
        / "node-hash-policy.yaml"
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
        schema_root=V5_SCHEMA_ROOT,
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
            "interface": "skill-certifier.interface.certify",
            "version": 1,
            "node_hash": "sha256:" + "d" * 64,
            "source_commit": commit,
        },
    )
    monkeypatch.setattr(
        certifier,
        "_v4_route_smoke_audit",
        lambda *_args, **_kwargs: (),
    )

    with pytest.raises(
        certifier.CertificationError,
        match="v5 certification completeness failed",
    ):
        _certify(
            tmp_path,
            target_node_ids=("demo-rtx",),
            expected_schema_version=5,
            schema_root=V5_SCHEMA_ROOT,
            require_migration_review=False,
        )

    monkeypatch.setattr(
        certifier,
        "v4_certification_completeness_findings",
        lambda _graph: (),
    )
    result = _certify(
        tmp_path,
        target_node_ids=("demo-rtx",),
        expected_schema_version=5,
        schema_root=V5_SCHEMA_ROOT,
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


def test_private_writer_module_target_certifies_contained_source(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)
    source_id = "demo-skill.source.gateway"

    result = _certify(tmp_path, target_node_ids=("demo-skill",))

    assert source_id in result.node_ids
    assert certifier.certificate_log_path(graph.nodes[source_id]).is_file()


def test_private_writer_writes_certificate_backed_pooled_review(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)
    backend = MemorySecretBackend()

    _certify(tmp_path, secret_backend=backend)
    _certify(tmp_path, secret_backend=backend)

    path = tmp_path / "skills" / "demo-skill" / ".pooled-blueprint-review.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = parse_certificate_log(
        certifier.certificate_log_path(graph.nodes["demo-skill"]).read_bytes(),
        tmp_path / "public-keys",
    )
    assert len(entries) == 2
    assert document["document_type"] == "pooled-blueprint-review"
    assert document["root"]["id"] == "demo-skill"
    assert document["root"]["certificate_hash"] == certificate_entry_hash(entries[-1])
    assert not (
        tmp_path
        / "skills"
        / "demo-skill"
        / ".pooled-blueprint-review.health.json"
    ).exists()


def test_private_writer_exact_source_does_not_write_parent(tmp_path: Path) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)
    target = "demo-skill.source.gateway"

    result = _certify(tmp_path, target_node_ids=(target,))

    assert result.node_ids == (target,)
    assert certifier.certificate_log_path(graph.nodes[target]).is_file()
    assert not certifier.certificate_log_path(graph.nodes["demo-skill"]).exists()


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
) -> None:
    if race == "worktree-mode" and sys.platform == "win32":
        # famulus-skip: category=platform-contract; reason=Windows worktrees do not expose a reliable POSIX executable mode; alternate=index-mode covers the authoritative Git mode boundary
        pytest.skip("POSIX worktree mode is unavailable")
    graph, _states, _commit = create_v4_repository(tmp_path)

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


def test_private_writer_allows_preexisting_but_rejects_new_untracked_file(
    tmp_path: Path,
) -> None:
    create_v4_repository(tmp_path)
    (tmp_path / "preexisting-untracked.txt").write_text(
        "preexisting\n",
        encoding="utf-8",
    )
    backend = MemorySecretBackend()

    result = _certify(tmp_path, secret_backend=backend)

    assert result.node_ids == (
        "demo-skill",
        "demo-skill.source.gateway",
    )

    def add_untracked(_node_id: str) -> None:
        (tmp_path / "new-untracked.txt").write_text(
            "new\n",
            encoding="utf-8",
        )

    with pytest.raises(certifier.CertificationError, match="changed"):
        _certify(
            tmp_path,
            secret_backend=backend,
            before_append=add_untracked,
        )


def test_private_writer_detects_post_append_log_corruption(tmp_path: Path) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)

    def corrupt(node_id: str) -> None:
        with certifier.certificate_log_path(graph.nodes[node_id]).open("ab") as stream:
            stream.write(b"{}\n")

    with pytest.raises(certifier.CertificationError, match="post-write certificate log changed"):
        _certify(tmp_path, after_append=corrupt)


@pytest.mark.parametrize(
    ("allow_non_atomic", "overrides"),
    [
        (False, {}),
        (True, {"allow_non_atomic": True}),
    ],
    ids=("default-atomic", "explicit-non-atomic"),
)
def test_private_writer_propagates_atomic_mode_to_existing_file_apis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_non_atomic: bool,
    overrides: dict[str, object],
) -> None:
    create_v4_repository(tmp_path)
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

    result = _certify(tmp_path, **overrides)

    assert "demo-skill" in result.node_ids
    assert {
        name: set(values)
        for name, values in observed.items()
    } == {
        "read": {allow_non_atomic},
        "replace": {allow_non_atomic},
        "compare-and-append": {allow_non_atomic},
    }


def test_live_writer_does_not_require_migration_review_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_v4_repository(tmp_path)

    def fail_if_read(_root: Path) -> str:
        raise AssertionError("live certification must not read migration-only refs")

    monkeypatch.setattr(
        certifier,
        "blueprint_v4_mechanical_commit",
        fail_if_read,
    )

    result = _certify(
        tmp_path,
        require_migration_review=False,
    )

    assert result.node_ids == (
        "demo-skill",
        "demo-skill.source.gateway",
    )


def test_private_writer_rejects_caller_supplied_gate_callbacks(
    tmp_path: Path,
) -> None:
    create_v4_repository(tmp_path)

    with pytest.raises(TypeError, match="semantic_audit"):
        _certify(
            tmp_path,
            semantic_audit=lambda _snapshot: None,
        )


def test_private_writer_certifies_certifier_through_same_path(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)

    result = _certify(
        tmp_path,
        target_node_ids=("skill-certifier",),
    )

    assert result.node_ids == (
        "skill-certifier",
        "skill-certifier.source.gateway",
    )
    assert certifier.certificate_log_path(
        graph.nodes["skill-certifier"]
    ).is_file()
    assert certifier.certificate_log_path(
        graph.nodes["skill-certifier.source.gateway"]
    ).is_file()


def test_private_writer_fails_closed_without_current_certifier(
    tmp_path: Path,
) -> None:
    create_v4_repository(tmp_path)
    shutil.rmtree(tmp_path / "skills" / "skill-certifier")
    repository = GitTestRepository(tmp_path)
    repository.git("add", "-A")
    repository.git("commit", "-qm", "remove certifier")

    with pytest.raises(certifier.CertificationError, match="certifier"):
        _certify(tmp_path)


def test_private_writer_reissues_same_key_against_complete_predecessor(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)
    backend = MemorySecretBackend()
    target = "demo-skill.source.gateway"

    _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
    )
    _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
    )

    path = certifier.certificate_log_path(graph.nodes[target])
    entries = parse_certificate_log(path.read_bytes(), tmp_path / "public-keys")
    assert len(entries) == 2
    assert entries[1]["payload"]["key_id"] == entries[0]["payload"]["key_id"]
    assert entries[1]["payload"]["previous_entry_hash"] == certificate_entry_hash(
        entries[0]
    )


def test_private_writer_appends_after_rotation_against_old_signed_envelope(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)
    backend = MemorySecretBackend()
    target = "demo-skill.source.gateway"

    _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
    )
    new_key = rotate_certificate_signing_key(
        tmp_path / "public-keys",
        secret_backend=backend,
    )
    _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
    )

    path = certifier.certificate_log_path(graph.nodes[target])
    entries = parse_certificate_log(path.read_bytes(), tmp_path / "public-keys")
    assert len(entries) == 2
    assert entries[0]["payload"]["key_id"] != new_key.key_id
    assert entries[1]["payload"]["key_id"] == new_key.key_id
    assert entries[1]["payload"]["previous_entry_hash"] == certificate_entry_hash(
        entries[0]
    )


def test_private_writer_derives_repository_only_at_batch_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_v4_repository(tmp_path)
    real_compute = certifier.compute_node_hash_states
    calls = 0

    def counted_compute(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(certifier, "compute_node_hash_states", counted_compute)

    _certify(tmp_path)

    assert calls == 2


def test_private_writer_runs_full_readiness_only_at_batch_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_v4_repository(tmp_path)
    real_readiness = certifier.check_commit_readiness
    calls = 0

    def counted_readiness(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return real_readiness(*args, **kwargs)

    monkeypatch.setattr(certifier, "check_commit_readiness", counted_readiness)

    _certify(tmp_path)

    assert calls == 2


@pytest.mark.parametrize(
    "race",
    [
        "content",
        "worktree-mode",
        "index-mode",
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
) -> None:
    if race == "worktree-mode" and sys.platform == "win32":
        # famulus-skip: category=platform-contract; reason=Windows worktrees do not expose a reliable POSIX executable mode; alternate=index-mode covers the authoritative Git mode boundary
        pytest.skip("POSIX worktree mode is unavailable")
    graph, _states, _commit = create_v4_repository(tmp_path)
    target = "demo-skill.source.gateway"

    def mutate(node_id: str) -> None:
        assert node_id == target
        if race == "content":
            (tmp_path / "skills" / "demo-skill" / "SKILL.md").write_text(
                "changed after append\n",
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
            (tmp_path / "head-race.txt").write_text(
                "race\n",
                encoding="utf-8",
            )
            repository = GitTestRepository(tmp_path)
            repository.git("add", "head-race.txt")
            repository.git("commit", "-qm", "head race")
        elif race == "basis":
            policy = (
                tmp_path
                / "references"
                / "certification"
                / "node-hash-policy.yaml"
            )
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
) -> None:
    create_v4_repository(tmp_path)
    graph = _add_cross_owner_contract(tmp_path)
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


def test_private_writer_orders_dependency_before_exact_target(
    tmp_path: Path,
) -> None:
    create_v4_repository(tmp_path)
    graph = _add_cross_owner_contract(tmp_path)
    target = "demo-skill.source.gateway"
    dependency = "demo-skill.source.contract"
    backend = MemorySecretBackend()
    attempted: list[str] = []

    def stop_dependency(node_id: str) -> None:
        attempted.append(node_id)
        if node_id == dependency:
            raise certifier.CertificationError("stop dependency issuance")

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
    assert not certifier.certificate_log_path(graph.nodes[target]).exists()
    assert not certifier.certificate_log_path(graph.nodes[dependency]).exists()

    result = _certify(
        tmp_path,
        target_node_ids=(target,),
        secret_backend=backend,
        require_migration_review=False,
    )

    assert result.node_ids == (dependency, target)


def test_private_writer_audits_exact_dependency_postorder_twice_before_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_v4_repository(tmp_path)
    repository_graph = _add_cross_owner_contract(tmp_path)
    target = "demo-skill.source.gateway"
    dependency = "demo-skill.source.contract"
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

    def audit(
        graph: object,
        states: object,
        *,
        repo_root: Path,
        certification_basis_paths: object,
        certification_node_ids: tuple[str, ...],
    ) -> tuple[tuple[str, str, tuple[object, ...]], ...]:
        del graph, states, repo_root, certification_basis_paths
        assert not certifier.certificate_log_path(
            repository_graph.nodes[dependency]
        ).exists()
        assert not certifier.certificate_log_path(
            repository_graph.nodes[target]
        ).exists()
        audit_scopes.append(certification_node_ids)
        return ()

    monkeypatch.setattr(certifier, "certification_target_postorder", record_postorder)
    monkeypatch.setattr(certifier, "audit_route_smoke_dependencies", audit)

    result = _certify(
        tmp_path,
        target_node_ids=(target,),
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
    graph, states, _commit = create_v4_repository(tmp_path)

    monkeypatch.setattr(
        certifier,
        "trace_python_route_smoke_dependencies_batch",
        lambda *_args, **_kwargs: pytest.fail("unknown scope reached tracing"),
    )

    with pytest.raises(
        certifier.CertificationHashError,
        match="unknown route-smoke certification node: missing.source",
    ):
        certifier.audit_route_smoke_dependencies(
            graph,
            states,
            repo_root=tmp_path,
            certification_basis_paths=(),
            certification_node_ids=("missing.source",),
        )


def test_certifier_route_audit_batches_unique_source_entrypoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, states, _commit = create_v4_repository(tmp_path)
    source_id = "demo-skill.source.gateway"
    source = graph.nodes[source_id]
    worker = source.module_root / "_rtx" / "worker.py"
    worker.parent.mkdir(exist_ok=True)
    worker.write_text("# route-smoke fixture\n", encoding="utf-8")
    declaration = dict(source.declaration)
    declaration["gateway"] = {"path": "_rtx/worker.py", "language": "Python"}
    interfaces = dict(declaration["interfaces"])
    first_id = "demo-skill.source.gateway.interface.run"
    first = dict(interfaces[first_id])
    first["process_binding"] = {
        "kind": "process",
        "entry": "Interface",
        "arguments": {},
        "fixed": [],
    }
    interfaces[first_id] = first
    interfaces["demo-skill.source.gateway.interface.inspect"] = dict(first)
    declaration["interfaces"] = interfaces
    graph = replace(
        graph,
        nodes={
            **graph.nodes,
            source_id: replace(
                source,
                declaration=declaration,
                gateway_path=worker,
            ),
        },
    )
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
        return {
            (source.module_root.resolve(), target): (worker,)
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

    result = certifier.audit_route_smoke_dependencies(
        graph,
        states,
        repo_root=tmp_path,
        certification_basis_paths=(),
        certification_node_ids=(source_id,),
    )

    assert batch_calls == [
        (
            tmp_path,
            ((source.module_root, target),),
        )
    ]
    assert result == ((source_id, target, ()),)


def test_v5_route_audit_uses_logical_package_and_explicit_shadow_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "modules",
        tmp_path / "modules",
    )
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "skills",
        tmp_path / "skills",
    )
    graph = certifier.load_repository_blueprint_graph(
        tmp_path,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )
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
            (source.module_root.resolve(), normalized[0][1]): (
                source.gateway_path,
            )
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

    result = certifier.audit_route_smoke_dependencies(
        graph,
        {},
        repo_root=tmp_path,
        certification_basis_paths=(),
        certification_node_ids=(source_id,),
        schema_root=V5_SCHEMA_ROOT,
    )

    target = observed[0][1][0][1]
    package = logical_python_package_name("demo-rtx")
    assert target.gateway_path == Path("runtime.py")
    assert target.logical_package == package
    assert target.logical_entrypoint == f"{package}.runtime"
    assert observed[0][2] == {
        "expected_schema_version": 5,
        "schema_root": V5_SCHEMA_ROOT,
    }
    assert result == ((source_id, target, ()),)


def test_private_writer_route_audit_failure_precedes_every_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)

    def reject(*_args: object, **_kwargs: object) -> None:
        raise certifier.CertificationHashError("route audit failed")

    monkeypatch.setattr(certifier, "audit_route_smoke_dependencies", reject)

    with pytest.raises(certifier.CertificationError, match="route audit failed"):
        _certify(tmp_path)

    assert not certifier.certificate_log_path(graph.nodes["demo-skill"]).exists()


def test_private_writer_route_audit_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)
    results = iter((("before",), ("after",)))

    monkeypatch.setattr(
        certifier,
        "audit_route_smoke_dependencies",
        lambda *_args, **_kwargs: next(results),
    )

    with pytest.raises(
        certifier.CertificationError,
        match="route-smoke dependency audit changed during certification",
    ):
        _certify(tmp_path)

    assert not certifier.certificate_log_path(graph.nodes["demo-skill"]).exists()


def test_private_writer_rechecks_forced_untracked_input_after_append(
    tmp_path: Path,
) -> None:
    create_v4_repository(tmp_path)
    source_blueprint = (
        tmp_path / "skills" / "demo-skill" / "blueprints" / "gateway.yaml"
    )
    declaration = yaml.safe_load(source_blueprint.read_text(encoding="utf-8"))
    declaration["content"].append(r"local\.txt")
    write_yaml(source_blueprint, declaration)
    module_blueprint = tmp_path / "skills" / "demo-skill" / "blueprint.yaml"
    module_declaration = yaml.safe_load(
        module_blueprint.read_text(encoding="utf-8")
    )
    module_declaration["content"].append(r"local\.txt")
    write_yaml(module_blueprint, module_declaration)
    policy_path = (
        tmp_path / "references" / "certification" / "node-hash-policy.yaml"
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
) -> None:
    graph, _states, _commit = create_v4_repository(tmp_path)
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

    assert sum(
        isinstance(outcome, certifier.V4CertificationResult)
        for outcome in outcomes
    ) == 1
    assert sum(
        isinstance(outcome, certifier.CertificationError)
        for outcome in outcomes
    ) == 1
    entries = parse_certificate_log(
        certifier.certificate_log_path(graph.nodes[target]).read_bytes(),
        public_key_root,
    )
    assert len(entries) == 1


def test_completeness_findings_block_structural_draft_signing(
    tmp_path: Path,
) -> None:
    create_v4_repository(tmp_path)
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
        schema_root=tmp_path / "references" / "blueprint",
        expected_schema_version=4,
    )

    findings = certifier.v4_certification_completeness_findings(graph)

    assert any(finding.field == "description" for finding in findings)
    with pytest.raises(certifier.CertificationError, match="certification completeness"):
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

    result = certifier.run_v4_mechanical_checks(tmp_path)

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


def test_public_certification_resolves_one_target_without_hash_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_graph, _states, commit = create_v4_repository(tmp_path)
    graph = _as_v5_graph(legacy_graph)
    calls: list[dict[str, object]] = []

    def issue(repo_root: Path, **kwargs: object):
        calls.append({"repo_root": repo_root, **kwargs})
        return certifier.V4CertificationResult(
            node_ids=tuple(kwargs["target_node_ids"]),
            source_commit=commit,
        )

    monkeypatch.setattr(certifier, "_certify_v4_repository", issue)
    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: graph,
    )
    monkeypatch.setattr(
        certifier,
        "run_v4_mechanical_checks",
        lambda _repo_root: _passed_mechanical_result(),
    )

    evidence, outcomes = certifier.certify(
        targets=("demo-skill",),
        reviewed_repository=tmp_path,
        reviewed_commit=commit,
    )

    assert evidence == [_passed_mechanical_result()]
    assert len(calls) == 1
    assert calls[0]["repo_root"] == tmp_path.resolve()
    assert calls[0]["allow_non_atomic"] is False
    assert set(calls[0]["target_node_ids"]) == {
        node_id
        for node_id, node in graph.nodes.items()
        if node.module_root == tmp_path / "skills" / "demo-skill"
    }
    assert outcomes[0].module == "demo-skill"
    assert outcomes[0].source == "reviewed-repository"
    assert outcomes[0].module_root == (
        tmp_path / "skills" / "demo-skill"
    ).resolve()
    assert all(
        node.certificate_path.parent.name == ".certificates"
        and node.certificate_path.suffix == ".jsonl"
        for node in outcomes[0].nodes
    )


def test_public_certification_propagates_explicit_non_atomic_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_graph, _states, commit = create_v4_repository(tmp_path)
    graph = _as_v5_graph(legacy_graph)
    calls: list[dict[str, object]] = []

    def issue(_repo_root: Path, **kwargs: object):
        calls.append(dict(kwargs))
        return certifier.V4CertificationResult(
            node_ids=tuple(kwargs["target_node_ids"]),
            source_commit=commit,
        )

    monkeypatch.setattr(certifier, "_certify_v4_repository", issue)
    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: graph,
    )
    monkeypatch.setattr(
        certifier,
        "run_v4_mechanical_checks",
        lambda _repo_root: _passed_mechanical_result(),
    )

    certifier.certify(
        targets=("demo-skill",),
        reviewed_repository=tmp_path,
        reviewed_commit=commit,
        allow_non_atomic=True,
    )

    assert calls[0]["allow_non_atomic"] is True


def test_public_certification_without_targets_selects_all_reviewed_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_graph, _states, commit = create_v4_repository(
        tmp_path,
        extra_modules=("other-skill",),
    )
    graph = _as_v5_graph(legacy_graph)
    expected_modules = tuple(
        sorted(
            node.node_id
                for node in graph.nodes.values()
                if node.node_type == "module"
        )
    )
    calls: list[dict[str, object]] = []

    def issue(_repo_root: Path, **kwargs: object):
        calls.append(dict(kwargs))
        return certifier.V4CertificationResult(
            node_ids=tuple(kwargs["target_node_ids"]),
            source_commit=commit,
        )

    monkeypatch.setattr(certifier, "_certify_v4_repository", issue)
    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: graph,
    )
    monkeypatch.setattr(
        certifier,
        "run_v4_mechanical_checks",
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
    graph, _states, _commit = create_v4_repository(tmp_path)
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
    graph, _states, _commit = create_v4_repository(tmp_path)
    renamed = replace(
        graph.nodes["demo-skill"],
        node_id="demo-module-id",
    )
    nodes = dict(graph.nodes)
    del nodes["demo-skill"]
    nodes[renamed.node_id] = renamed
    mismatched_graph = replace(graph, nodes=nodes)

    by_id = certifier.resolve_reviewed_repository_targets(
        mismatched_graph,
        ("demo-module-id",),
    )
    by_name = certifier.resolve_reviewed_repository_targets(
        mismatched_graph,
        ("demo-skill",),
    )

    assert by_id == (renamed,)
    assert by_name == (renamed,)


def test_v5_reviewed_target_resolution_never_uses_rtx_basename(
    tmp_path: Path,
) -> None:
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "modules",
        tmp_path / "modules",
    )
    copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE / "skills",
        tmp_path / "skills",
    )
    graph = certifier.load_repository_blueprint_graph(
        tmp_path,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )

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


def test_public_mechanical_gate_precedes_route_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_graph, _states, commit = create_v4_repository(tmp_path)
    graph = _as_v5_graph(legacy_graph)
    events: list[str] = []

    def issue(_repo_root: Path, **kwargs: object):
        events.append("issue")
        return certifier.V4CertificationResult(
            node_ids=tuple(kwargs["target_node_ids"]),
            source_commit=commit,
        )

    monkeypatch.setattr(certifier, "_certify_v4_repository", issue)
    monkeypatch.setattr(
        certifier,
        "run_v4_mechanical_checks",
        lambda _repo_root: (
            events.append("mechanical") or _passed_mechanical_result()
        ),
    )
    monkeypatch.setattr(
        certifier,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: graph,
    )

    evidence, _outcomes = certifier.certify(
        targets=("demo-skill",),
        reviewed_repository=tmp_path,
        reviewed_commit=commit,
    )

    assert evidence == [_passed_mechanical_result()]
    assert events == ["mechanical", "issue"]


def test_public_certification_has_no_mechanical_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_graph, _states, commit = create_v4_repository(tmp_path)
    graph = _as_v5_graph(legacy_graph)
    signed = False

    def fail_mechanical(_repo_root: Path) -> certifier.CommandResult:
        raise certifier.CertificationError("mechanical certification checks failed")

    def issue(*_args: object, **_kwargs: object) -> object:
        nonlocal signed
        signed = True
        pytest.fail("signing ran after the mechanical gate failed")

    monkeypatch.setattr(certifier, "run_v4_mechanical_checks", fail_mechanical)
    monkeypatch.setattr(certifier, "_certify_v4_repository", issue)
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
