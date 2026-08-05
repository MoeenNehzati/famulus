from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
import yaml

from officina.common.blueprint_graph import load_repository_blueprint_graph
from officina.common.certificate_records import (
    CertificateSigningKey,
    canonical_certificate_envelope_bytes,
    certificate_entry_hash,
    certificate_key_id,
    certificate_public_key_root,
    sign_certificate_payload,
)
from test_support.git_repository import GitTestRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = (
    PROJECT_ROOT / "tests" / "fixtures" / "nested_module_migration" / "valid"
)
MIGRATION_MODULE = "officina.common.nested_module_migration"
MIGRATION_SPEC = importlib.util.find_spec(MIGRATION_MODULE)


def _api() -> Any:
    return importlib.import_module(MIGRATION_MODULE)


def _fixture_repository(tmp_path: Path) -> GitTestRepository:
    repository = GitTestRepository.create(tmp_path / "repo")
    shutil.copytree(FIXTURE_ROOT, repository.root, dirs_exist_ok=True)
    shutil.copytree(
        PROJECT_ROOT / "references" / "blueprint",
        repository.root / "references" / "blueprint",
        ignore=shutil.ignore_patterns(
            ".certificates",
            ".pooled-blueprint-review.yaml",
        ),
    )
    blueprint_root = repository.root / "references" / "blueprint"
    for frozen in (blueprint_root / "migrations" / "v4").iterdir():
        if frozen.is_file():
            shutil.copy2(frozen, blueprint_root / frozen.name)
    (blueprint_root / "blueprint.yaml").unlink()
    shutil.rmtree(blueprint_root / "blueprints")
    for stored_marker in repository.root.rglob("blueprint.v4.yaml"):
        stored_marker.rename(stored_marker.with_name("blueprint.yaml"))
    for stored_python in repository.root.rglob("*.py.fixture"):
        stored_python.rename(stored_python.with_suffix(""))
    repository.git("add", ".")
    repository.git("commit", "-qm", "nested migration fixture")
    return repository


def _commit(repository: GitTestRepository, message: str) -> None:
    repository.git("add", "-A")
    repository.git("commit", "-qm", message)


def _read_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_signed_history(
    repository: GitTestRepository,
    *,
    module_root: str,
    node_id: str,
    subjects: list[dict[str, object]],
) -> Path:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    key_id = certificate_key_id(public_key)
    public_root = certificate_public_key_root(repository.root)
    public_root.mkdir(parents=True, exist_ok=True)
    public_root.joinpath(f"{key_id.removeprefix('sha256:')}.pub").write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    signing_key = CertificateSigningKey(key_id=key_id, signer=private_key)
    previous_hash: str | None = None
    envelopes: list[dict[str, object]] = []
    for subject in subjects:
        envelope = sign_certificate_payload(
            {
                "certificate_schema_version": 1,
                "key_id": key_id,
                "previous_entry_hash": previous_hash,
                "subject": subject,
            },
            signing_key,
        )
        envelopes.append(envelope)
        previous_hash = certificate_entry_hash(envelope)
    history = (
        repository.root
        / module_root
        / ".certificates"
        / f"{node_id}.jsonl"
    )
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(
        b"".join(
            canonical_certificate_envelope_bytes(envelope) + b"\n"
            for envelope in envelopes
        )
    )
    return history


def _manifest_section(plan: Any, name: str) -> dict[str, Any]:
    manifest = plan.to_manifest()
    assert isinstance(manifest, dict)
    section = manifest[name]
    assert isinstance(section, dict)
    return section


def _assert_access(
    plan: Any,
    interface_id: str,
    *,
    allow_all: bool,
    callers: list[str],
) -> None:
    access = dict(plan.access_map[interface_id])
    assert access == {
        "allow_all_modules": allow_all,
        "allowed_callers": callers,
    }


def test_nested_module_migration_api_exists() -> None:
    assert MIGRATION_SPEC is not None, (
        "Task 8 starts red: officina.common.nested_module_migration is missing"
    )
    module = _api()
    assert hasattr(module, "NestedModuleMigration")
    assert hasattr(module, "NestedModuleMigrationError")
    assert hasattr(module, "build_nested_module_migration")


def test_nested_migration_source_owns_only_its_candidate_helper_boundary() -> None:
    graph = load_repository_blueprint_graph(PROJECT_ROOT)
    source_id = "common.source.nested-module-migration"

    assert graph.direct_file_owners[
        PROJECT_ROOT / "src/officina/common/nested_module_migration.py"
    ] == source_id
    assert graph.direct_file_owners[
        PROJECT_ROOT / "src/officina/common/migration_candidate.py"
    ] == source_id
    assert graph.direct_file_owners[
        PROJECT_ROOT / "src/officina/common/interface_injection_migration.py"
    ] == "common"
    direct_dependencies = {
        edge.target_id
        for edge in graph.node_edges
        if edge.relation == "uses-source" and edge.source_id == source_id
    }
    assert direct_dependencies == {
        "common.source.atomic-files",
        "common.source.blueprint-graph",
        "common.source.blueprint-inventory",
        "common.source.certificate-records",
        "common.source.certification-hashing",
        "common.source.git-provenance",
    }


def test_nested_migration_contract_documents_v4_or_v5_noop_input() -> None:
    document = _read_yaml(
        PROJECT_ROOT
        / "src/officina/common/blueprints/nested-module-migration.yaml"
    )
    contract = document["interfaces"][
        "common.source.nested-module-migration.interface.python-api"
    ]["contract"]
    repository_root = contract["arguments"]["repository-root"][
        "description"
    ]
    precondition = contract["preconditions"][0]
    outcomes = {
        outcome["id"]: outcome for outcome in contract["outcomes"]
    }

    assert "all-v4" in repository_root
    assert "all-v5" in repository_root
    assert "all-v4" in precondition["description"]
    assert "all-v5" in precondition["description"]
    assert outcomes["already-v5-noop"]["effects"] == []
    assert "empty migration plan" in outcomes[
        "already-v5-noop"
    ]["caller_action"]
    assert "all-v4 or all-v5" in contract["direct_io"]["reads"][0][
        "content"
    ]


class TestNestedModuleMigrationContract:
    def test_repository_inventory_matches_reviewed_v6_cutover_surface(
        self,
        tmp_path: Path,
    ) -> None:
        # famulus-raw-git: category=validator-isolation; reason=the exact cutover assertion must reject an uncommitted working tree
        status = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert status.stdout == "", (
            "post-cutover inventory must be checked against a clean committed tree"
        )
        repository_root = tmp_path / "repository"
        # famulus-raw-git: category=validator-isolation; reason=the inventory assertion needs a clean committed snapshot of the live repository
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-local",
                str(PROJECT_ROOT),
                str(repository_root),
            ],
            check=True,
        )
        graph = load_repository_blueprint_graph(repository_root)
        existing_children = {
            path.parent.name
            for path in repository_root.glob("skills/*/_rtx")
            if path.is_dir()
        }
        assert existing_children == {
            "bib-audit",
            "cloud-files",
            "connect-google",
            "daily-plan",
            "email-client",
            "email-triage",
            "find-handoff-candidates",
            "g-calendar",
            "get-weather",
            "initialize-tdd",
            "install-assistant-tools",
            "list-manager",
            "math-dependency-graph",
            "pdf-to-markdown",
            "recurring-tasks",
            "regenerate-blueprints",
            "skill-certifier",
            "skill-drift",
            "skill-maker",
            "refactor-node",
        }
        child_module_ids = {f"{skill_id}._rtx" for skill_id in existing_children}

        assert graph.schema_version == 6
        assert all(
            node.declaration["schema_version"] == 6
            for node in graph.nodes.values()
        )
        assert child_module_ids <= set(graph.nodes)
        assert all(
            graph.module_parents[child_id] == child_id.removesuffix("._rtx")
            for child_id in child_module_ids
        )
        assert not any(
            edge.relation.startswith("facades-") for edge in graph.node_edges
        )
        assert not any(
            node_id.endswith("-rtx") for node_id in graph.nodes
        )

    def test_cli_dry_run_is_byte_identical_and_pure(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        script = PROJECT_ROOT / "scripts" / "migrate-blueprints-v5.py"
        command = [
            sys.executable,
            str(script),
            "--dry-run",
            "--repo-root",
            str(repository.root),
        ]
        before = repository.git("status", "--porcelain=v1", "-z").stdout

        first = subprocess.run(command, check=True, capture_output=True)
        second = subprocess.run(command, check=True, capture_output=True)

        assert first.stderr == second.stderr == b""
        assert first.stdout == second.stdout
        payload = json.loads(first.stdout)
        assert payload["operations"]
        assert payload["certificate_inputs"] == {}
        assert repository.git("status", "--porcelain=v1", "-z").stdout == before

    def test_plan_is_pure_deterministic_and_has_separate_maps(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        before = repository.git("status", "--porcelain=v1", "-z").stdout

        first = _api().build_nested_module_migration(repository.root)
        second = _api().build_nested_module_migration(repository.root)

        assert first == second
        assert first.render_manifest() == second.render_manifest()
        assert first.render_manifest().endswith(b"\n")
        assert repository.git("status", "--porcelain=v1", "-z").stdout == before
        expected_sections = {
            "node_versions",
            "interface_versions",
            "access",
            "identities",
            "paths",
            "imports",
            "authority",
            "histories",
            "certificate_inputs",
            "file_dispositions",
            "file_hashes",
            "operations",
        }
        assert expected_sections <= set(first.to_manifest())
        assert _manifest_section(first, "node_versions") == dict(
            first.node_version_map
        )
        assert _manifest_section(first, "interface_versions") == dict(
            first.interface_version_map
        )
        assert _manifest_section(first, "access") == dict(first.access_map)
        assert _manifest_section(first, "identities") == dict(first.identity_map)
        assert _manifest_section(first, "paths") == dict(first.path_map)
        assert _manifest_section(first, "imports") == dict(first.import_map)
        assert _manifest_section(first, "authority") == dict(
            first.authority_map
        )
        assert _manifest_section(first, "histories") == dict(first.history_map)
        assert _manifest_section(first, "certificate_inputs") == dict(
            first.certificate_input_hashes
        )
        assert _manifest_section(first, "file_dispositions") == dict(
            first.file_disposition_map
        )
        assert _manifest_section(first, "file_hashes") == dict(first.file_hash_map)

    def test_code_and_instruction_skills_get_exact_parent_child_topology(
        self, tmp_path: Path
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )
        producer = yaml.safe_load(plan.planned_files["skills/producer/blueprint.yaml"])
        producer_child = yaml.safe_load(
            plan.planned_files["skills/producer/_rtx/blueprint.yaml"]
        )
        guide = yaml.safe_load(plan.planned_files["skills/guide/blueprint.yaml"])
        guide_child = yaml.safe_load(
            plan.planned_files["skills/guide/_rtx/blueprint.yaml"]
        )

        assert producer["children"] == {
            "producer-rtx": {
                "base": "module-root",
                "path": "_rtx/blueprint.yaml",
            }
        }
        assert producer["namespace_exports"] == {}
        assert producer_child["id"] == "producer-rtx"
        assert producer_child["gateway"] == {
            "path": "__init__.py",
            "language": "Python>=3.11",
        }
        assert "discovery" not in producer_child
        assert producer_child["children"] == {}
        assert (
            plan.planned_files["skills/producer/_rtx/__init__.py"]
            == b'"""Producer runtime package."""\n'
        )

        assert guide["children"] == {
            "guide-rtx": {
                "base": "module-root",
                "path": "_rtx/blueprint.yaml",
            }
        }
        assert guide_child["id"] == "guide-rtx"
        assert guide_child["sources"] == {}
        assert guide_child["exports"] == {}
        assert guide_child["children"] == {}
        assert guide_child["namespace_exports"] == {}
        assert plan.planned_files["skills/guide/_rtx/__init__.py"]

    def test_code_skill_with_zero_exports_still_moves_runtime_content(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_marker = repository.root / "skills/producer/blueprint.yaml"
        producer = _read_yaml(producer_marker)
        producer["exports"] = {}
        _write_yaml(producer_marker, producer)
        _commit(repository, "code skill with zero exports")

        plan = _api().build_nested_module_migration(repository.root)
        parent = yaml.safe_load(
            plan.planned_files["skills/producer/blueprint.yaml"]
        )
        child = yaml.safe_load(
            plan.planned_files["skills/producer/_rtx/blueprint.yaml"]
        )

        assert parent["exports"] == {}
        assert child["exports"] == {}
        assert (
            "skills/producer/_rtx/runtime.py"
            in plan.planned_files
        )
        assert "producer-rtx.source.runtime" in child["sources"]

    def test_parent_facades_and_child_ceilings_apply_exact_access_migration(
        self, tmp_path: Path
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )
        parent = yaml.safe_load(plan.planned_files["skills/producer/blueprint.yaml"])
        child = yaml.safe_load(
            plan.planned_files["skills/producer/_rtx/blueprint.yaml"]
        )

        assert parent["exports"]["producer.interface.execute"][
            "facade_interface"
        ] == {
            "interface": "producer-rtx.interface.execute",
            "version": 7,
        }
        _assert_access(
            plan,
            "producer.interface.execute",
            allow_all=False,
            callers=["producer", "producer-rtx"],
        )
        _assert_access(
            plan,
            "producer-rtx.interface.execute",
            allow_all=False,
            callers=["producer", "producer-rtx"],
        )
        _assert_access(
            plan,
            "producer.interface.status",
            allow_all=False,
            callers=["consumer"],
        )
        _assert_access(
            plan,
            "producer-rtx.interface.status",
            allow_all=False,
            callers=["consumer", "producer"],
        )
        _assert_access(
            plan,
            "producer.interface.ping",
            allow_all=True,
            callers=[],
        )
        _assert_access(
            plan,
            "producer-rtx.interface.ping",
            allow_all=True,
            callers=[],
        )
        _assert_access(
            plan,
            "guide.interface.read",
            allow_all=False,
            callers=["producer", "producer-rtx"],
        )
        _assert_access(
            plan,
            "consumer.interface.read",
            allow_all=False,
            callers=["producer", "producer-rtx"],
        )
        assert child["exports"]["producer-rtx.interface.status"]["access"] == {
            "allow_all_modules": False,
            "allowed_callers": ["consumer", "producer"],
        }

    def test_node_and_interface_versions_follow_distinct_rules(
        self, tmp_path: Path
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )

        assert plan.node_version_map["producer"] == 4
        assert plan.node_version_map["producer-rtx"] == 1
        assert plan.node_version_map["producer-rtx.source.runtime"] == 1
        assert plan.node_version_map["guide"] == 5
        assert plan.node_version_map["guide-rtx"] == 1
        assert plan.node_version_map["consumer"] == 7
        assert plan.interface_version_map[
            "producer-rtx.source.runtime.interface.execute"
        ] == 7
        assert plan.interface_version_map["producer-rtx.interface.execute"] == 7
        assert plan.interface_version_map["producer.interface.execute"] == 7
        assert plan.interface_version_map[
            "producer-rtx.source.runtime.interface.status"
        ] == 4

    def test_access_migration_applies_to_non_skill_and_retained_exports(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        consumer_root = repository.root / "modules" / "consumer"
        (consumer_root / "api.py").write_text("VALUE = 'consumer'\n", encoding="utf-8")
        consumer_source = {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": "consumer.source.api",
            "version": 2,
            "description": "Restricted non-skill API.",
            "gateway": {"path": "api.py", "language": "Python>=3.11"},
            "content": [r"api\.py"],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {
                "consumer.source.api.interface.read": {"version": 3}
            },
        }
        _write_yaml(
            consumer_root / "blueprints" / "api.yaml",
            consumer_source,
        )
        consumer = _read_yaml(consumer_root / "blueprint.yaml")
        consumer["content"] = [r"(?:README\.md|api\.py)"]
        consumer["sources"] = {
            "consumer.source.api": {
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/api.yaml",
                }
            }
        }
        consumer["exports"] = {
            "consumer.interface.read": {
                "source_interface": "consumer.source.api.interface.read",
                "access": {
                    "allow_all_modules": False,
                    "allowed_callers": ["producer"],
                },
            }
        }
        _write_yaml(consumer_root / "blueprint.yaml", consumer)

        producer_root = repository.root / "skills" / "producer"
        gateway_path = producer_root / "blueprints" / "gateway.yaml"
        gateway = _read_yaml(gateway_path)
        gateway["interfaces"] = {
            "producer.source.gateway.interface.docs": {"version": 2}
        }
        _write_yaml(gateway_path, gateway)
        producer = _read_yaml(producer_root / "blueprint.yaml")
        producer["exports"]["producer.interface.docs"] = {
            "source_interface": "producer.source.gateway.interface.docs",
            "access": {
                "allow_all_modules": False,
                "allowed_callers": ["producer"],
            },
        }
        _write_yaml(producer_root / "blueprint.yaml", producer)
        caller_path = producer_root / "blueprints" / "caller.yaml"
        caller = _read_yaml(caller_path)
        caller["uses_interfaces"].extend(
            [
                {"interface": "consumer.interface.read", "version": 3},
                {"interface": "producer.interface.docs", "version": 2},
            ]
        )
        _write_yaml(caller_path, caller)
        _commit(repository, "exercise repository-wide access migration")

        plan = _api().build_nested_module_migration(repository.root)
        migrated_consumer = yaml.safe_load(
            plan.planned_files["modules/consumer/blueprint.yaml"]
        )
        migrated_producer = yaml.safe_load(
            plan.planned_files["skills/producer/blueprint.yaml"]
        )

        _assert_access(
            plan,
            "consumer.interface.read",
            allow_all=False,
            callers=["producer", "producer-rtx"],
        )
        assert migrated_consumer["version"] == 7
        assert migrated_consumer["exports"]["consumer.interface.read"][
            "access"
        ]["allowed_callers"] == ["producer", "producer-rtx"]
        _assert_access(
            plan,
            "producer.interface.docs",
            allow_all=False,
            callers=["producer", "producer-rtx"],
        )
        assert migrated_producer["exports"]["producer.interface.docs"][
            "access"
        ]["allowed_callers"] == ["producer", "producer-rtx"]

    def test_dependency_versions_locators_and_retained_source_versions_rewrite(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills" / "producer"
        gateway_path = producer_root / "blueprints" / "gateway.yaml"
        gateway = _read_yaml(gateway_path)
        gateway["dependencies"] = [
            {
                "source": "producer.source.runtime",
                "version": 5,
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/runtime.yaml",
                },
                "reason": "Retained instructions depend on runtime behavior.",
            }
        ]
        _write_yaml(gateway_path, gateway)
        caller_path = producer_root / "blueprints" / "caller.yaml"
        caller = _read_yaml(caller_path)
        caller["dependencies"] = [
            {
                "source": "producer.source.runtime",
                "version": 5,
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/runtime.yaml",
                },
                "reason": "Moved caller depends on moved runtime.",
            }
        ]
        _write_yaml(caller_path, caller)
        _commit(repository, "exercise node pin migration")

        plan = _api().build_nested_module_migration(repository.root)
        retained = yaml.safe_load(
            plan.planned_files["skills/producer/blueprints/gateway.yaml"]
        )
        moved = yaml.safe_load(
            plan.planned_files[
                "skills/producer/_rtx/blueprints/caller.yaml"
            ]
        )

        assert plan.node_version_map["producer.source.gateway"] == 3
        assert retained["version"] == 3
        assert retained["dependencies"] == [
            {
                "source": "producer-rtx.source.runtime",
                "version": 1,
                "blueprint": {
                    "base": "module-root",
                    "path": "_rtx/blueprints/runtime.yaml",
                },
                "reason": "Retained instructions depend on runtime behavior.",
            }
        ]
        assert moved["dependencies"] == [
            {
                "source": "producer-rtx.source.runtime",
                "version": 1,
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/runtime.yaml",
                },
                "reason": "Moved caller depends on moved runtime.",
            }
        ]

    def test_moved_source_rebases_module_root_paths_recursively(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills" / "producer"
        contract = producer_root / "_rtx" / "contracts" / "runtime.json"
        contract.parent.mkdir()
        contract.write_text('{"type":"string"}\n', encoding="utf-8")
        runtime_path = producer_root / "blueprints" / "runtime.yaml"
        runtime = _read_yaml(runtime_path)
        runtime["content"] = [
            r"(?:_rtx/(?:__init__\.py|runtime\.py|helper\.py|contracts/runtime\.json)|tests/.*|state/.*)"
        ]
        runtime["contract_references"] = [
            {
                "base": "module-root",
                "path": "_rtx/contracts/runtime.json",
            }
        ]
        migration_blueprint = _read_yaml(
            PROJECT_ROOT
            / "src"
            / "officina"
            / "common"
            / "blueprints"
            / "nested-module-migration.yaml"
        )
        migration_interface = migration_blueprint["interfaces"][
            "common.source.nested-module-migration.interface.python-api"
        ]
        contract_document = migration_interface["contract"]
        contract_document["outputs"][0].pop("type")
        contract_document["outputs"][0]["schema"] = {
            "path": "_rtx/contracts/runtime.json",
            "fragment": "#",
        }
        contract_document["direct_io"]["reads"][0]["path"] = (
            "$module/_rtx/contracts/runtime.json"
        )
        runtime["interfaces"]["producer.source.runtime.interface.execute"][
            "contract"
        ] = contract_document
        _write_yaml(runtime_path, runtime)
        _commit(repository, "exercise nested module-root path rebasing")

        plan = _api().build_nested_module_migration(repository.root)
        migrated = yaml.safe_load(
            plan.planned_files[
                "skills/producer/_rtx/blueprints/runtime.yaml"
            ]
        )

        assert migrated["contract_references"] == [
            {"base": "module-root", "path": "contracts/runtime.json"}
        ]
        contract = migrated["interfaces"][
            "producer-rtx.source.runtime.interface.execute"
        ]["contract"]
        assert contract["outputs"][0]["schema"]["path"] == (
            "contracts/runtime.json"
        )
        assert contract["direct_io"]["reads"][0]["path"] == (
            "$module/contracts/runtime.json"
        )

    def test_authority_is_split_by_claim_and_mixed_claim_is_rejected(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path / "split")
        producer_root = repository.root / "skills" / "producer"
        gateway_path = producer_root / "blueprints" / "gateway.yaml"
        gateway = _read_yaml(gateway_path)
        gateway["interfaces"] = {
            "producer.source.gateway.interface.docs": {"version": 2}
        }
        _write_yaml(gateway_path, gateway)
        producer_path = producer_root / "blueprint.yaml"
        producer = _read_yaml(producer_path)
        producer["exports"]["producer.interface.docs"] = {
            "source_interface": "producer.source.gateway.interface.docs",
            "access": {
                "allow_all_modules": False,
                "allowed_callers": ["producer"],
            },
        }
        producer["authority"]["owns_filesystem"].append(
            {
                "match": "exact",
                "path": "instruction-state.json",
                "allowed_readers": ["producer.interface.docs"],
                "reason": "Instruction-layer state remains parent-owned.",
            }
        )
        _write_yaml(producer_path, producer)
        _commit(repository, "exercise claim-level authority split")

        plan = _api().build_nested_module_migration(repository.root)
        parent = yaml.safe_load(
            plan.planned_files["skills/producer/blueprint.yaml"]
        )
        child = yaml.safe_load(
            plan.planned_files["skills/producer/_rtx/blueprint.yaml"]
        )
        assert [claim["path"] for claim in parent["authority"]["owns_filesystem"]] == [
            "instruction-state.json"
        ]
        assert [claim["path"] for claim in child["authority"]["owns_filesystem"]] == [
            "state/.*"
        ]

        ambiguous = _fixture_repository(tmp_path / "ambiguous")
        producer_path = ambiguous.root / "skills" / "producer" / "blueprint.yaml"
        producer = _read_yaml(producer_path)
        producer["exports"]["producer.interface.docs"] = {
            "source_interface": "producer.source.gateway.interface.docs",
            "access": {
                "allow_all_modules": False,
                "allowed_callers": ["producer"],
            },
        }
        gateway_path = (
            ambiguous.root
            / "skills"
            / "producer"
            / "blueprints"
            / "gateway.yaml"
        )
        gateway = _read_yaml(gateway_path)
        gateway["interfaces"] = {
            "producer.source.gateway.interface.docs": {"version": 2}
        }
        _write_yaml(gateway_path, gateway)
        producer["authority"]["owns_filesystem"] = [
            {
                "match": "exact",
                "path": "mixed-state.json",
                "allowed_readers": [
                    "producer.interface.execute",
                    "producer.interface.docs",
                ],
                "reason": "Deliberately mixes parent and child evidence.",
            }
        ]
        _write_yaml(producer_path, producer)
        _commit(ambiguous, "ambiguous authority disposition")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="ambiguous authority disposition|mixed parent.*child",
        ):
            _api().build_nested_module_migration(ambiguous.root)

    def test_identity_path_import_and_validator_maps_are_complete(
        self, tmp_path: Path
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )

        assert plan.identity_map["producer.source.runtime"] == (
            "producer-rtx.source.runtime"
        )
        assert plan.identity_map[
            "producer.source.runtime.interface.execute"
        ] == "producer-rtx.source.runtime.interface.execute"
        assert plan.path_map[
            "skills/producer/blueprints/runtime.yaml"
        ] == "skills/producer/_rtx/blueprints/runtime.yaml"
        assert plan.path_map[
            "skills/producer/tests/test_runtime.py"
        ] == "skills/producer/_rtx/tests/test_runtime.py"
        assert plan.path_map[
            "skills/producer/state/cache.json"
        ] == "skills/producer/_rtx/state/cache.json"
        assert plan.path_map[
            "skills/skill-maker/validators/probe.py"
        ] == "validators/skill/probe.py"
        assert plan.import_map["skills/producer/_rtx/runtime.py"] == {
            "from": "from _rtx.helper import VALUE",
            "to": "from .helper import VALUE",
        }
        runtime = yaml.safe_load(
            plan.planned_files[
                "skills/producer/_rtx/blueprints/runtime.yaml"
            ]
        )
        assert runtime["gateway"]["path"] == "runtime.py"
        assert len(runtime["content"]) == 1
        rebased_content = runtime["content"][0]
        assert "_rtx/" not in rebased_content
        pattern = re.compile(rebased_content)
        assert all(
            pattern.fullmatch(path)
            for path in (
                "__init__.py",
                "runtime.py",
                "helper.py",
                "tests/test_runtime.py",
                "state/cache.json",
            )
        )
        caller = yaml.safe_load(
            plan.planned_files[
                "skills/producer/_rtx/blueprints/caller.yaml"
            ]
        )
        assert caller["uses_interfaces"] == [
            {"interface": "consumer.interface.read", "version": 5},
            {"interface": "guide.interface.read", "version": 2},
            {"interface": "producer-rtx.interface.execute", "version": 7},
        ]
        assert (
            plan.planned_files["skills/producer/_rtx/runtime.py"]
            == b"from .helper import VALUE\n\n\ndef execute() -> str:\n"
            b"    return VALUE\n"
        )

    def test_canonical_validator_references_follow_relocation_only(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        basis = (
            repository.root
            / "references/certification/certification-basis-roots.json"
        )
        basis.parent.mkdir(parents=True, exist_ok=True)
        basis.write_text(
            '["skills/skill-maker/validators/*.py"]\n',
            encoding="utf-8",
        )
        standard = (
            repository.root
            / "references/skill-standards/"
            "skill-guidelines.standard.yaml"
        )
        standard.parent.mkdir(parents=True, exist_ok=True)
        standard.write_text(
            "validator: skills/skill-maker/validators/"
            "dispatch_caller_skill.py\n"
            "check_id: validate-dispatch-caller-skill\n",
            encoding="utf-8",
        )
        view = standard.with_suffix(".md")
        view.write_text(
            "`skills/skill-maker/validators/dispatch_caller_skill.py`\n",
            encoding="utf-8",
        )
        test_path = repository.root / "tests/validate_dispatch_caller_skill.py"
        test_path.parent.mkdir()
        test_path.write_text(
            'VALIDATOR = "skills/skill-maker/validators/'
            'dispatch_caller_skill.py"\n',
            encoding="utf-8",
        )
        multiline_test = (
            repository.root / "tests/validate_blueprint_relationships.py"
        )
        multiline_test.write_text(
            'VALIDATOR = (\n'
            '    REPO_ROOT\n'
            '    / "skills"\n'
            '    / "skill-maker"\n'
            '    / "validators"\n'
            '    / "blueprint_relationships.py"\n'
            ')\n',
            encoding="utf-8",
        )
        frozen = (
            repository.root
            / "references/blueprint/migrations/v4/reference.txt"
        )
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_text(
            "skills/skill-maker/validators/dispatch_caller_skill.py\n",
            encoding="utf-8",
        )
        _commit(repository, "canonical validator references")

        plan = _api().build_nested_module_migration(repository.root)

        assert (
            b"validators/skill/*.py"
            in plan.planned_files[
                "references/certification/"
                "certification-basis-roots.json"
            ]
        )
        standard_bytes = b"\n".join(
            content
            for path, content in plan.planned_files.items()
            if path.startswith("references/node-standards/")
            and path.endswith(".standard.yaml")
        )
        assert standard_bytes
        assert b"validators/skill/" in standard_bytes
        assert b"skills/skill-maker/validators/" not in standard_bytes
        assert b"dispatch-caller-skill" not in standard_bytes
        assert (
            "references/skill-standards/skill-guidelines.standard.yaml"
            not in plan.planned_files
        )
        assert (
            b"validators/skill/"
            in plan.planned_files["references/blueprint/schema-meta.json"]
        )
        assert (
            plan.path_map["tests/validate_dispatch_caller_skill.py"]
            == "tests/validate_dispatch_caller_module.py"
        )
        assert (
            b'REPO_ROOT\n    / "validators"\n    / "skill"\n'
            in plan.planned_files[
                "tests/validate_blueprint_relationships.py"
            ]
        )
        assert (
            b"validators/skill/dispatch_caller_module.py"
            in plan.planned_files[
                "tests/validate_dispatch_caller_module.py"
            ]
        )
        assert (
            "references/blueprint/migrations/v4/reference.txt"
            not in plan.planned_files
        )

    def test_materialized_relocated_validators_use_candidate_repository_root(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        validator_root = (
            repository.root / "skills/skill-maker/validators"
        )
        direct = validator_root / "direct_root.py"
        direct.write_text(
            "from pathlib import Path\n\n"
            "REPO_ROOT = Path(__file__).resolve().parents[3]\n\n"
            "def validate(repo_root):\n"
            "    if repo_root == REPO_ROOT and (repo_root / '.git').exists():\n"
            "        return []\n"
            "    return ['wrong repository root']\n",
            encoding="utf-8",
        )
        standalone = validator_root / "standalone_root.py"
        standalone.write_text(
            "from pathlib import Path\n\n"
            "def validate(repo_root):\n"
            "    return [] if (repo_root / '.git').exists() else ['wrong root']\n\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(bool(validate(\n"
            "        Path(__file__).resolve().parents[3]\n"
            "    )))\n",
            encoding="utf-8",
        )
        _commit(repository, "validator repository root fixtures")

        plan = _api().build_nested_module_migration(repository.root)
        candidate = plan.materialize(tmp_path / "candidate")
        direct_target = candidate.root / "validators/skill/direct_root.py"
        direct_spec = importlib.util.spec_from_file_location(
            "candidate_direct_root",
            direct_target,
        )
        assert direct_spec is not None
        assert direct_spec.loader is not None
        direct_module = importlib.util.module_from_spec(direct_spec)
        direct_spec.loader.exec_module(direct_module)

        assert direct_module.REPO_ROOT == candidate.root
        assert direct_module.validate(candidate.root) == []
        standalone_target = (
            candidate.root / "validators/skill/standalone_root.py"
        )
        completed = subprocess.run(
            [sys.executable, str(standalone_target)],
            cwd=candidate.root,
            check=False,
            capture_output=True,
        )
        assert completed.returncode == 0, completed.stderr.decode()

    def test_moved_python_callers_resources_and_permissions_are_rebased(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        runtime_path = producer_root / "_rtx/runtime.py"
        runtime_path.write_text(
            "from pathlib import Path\n"
            "from officina.runtime.python_machine_interface import DispatchCall\n\n"
            "CALL = DispatchCall(\n"
            "    caller_skill=\"producer\",\n"
            "    target_skill=\"consumer\",\n"
            "    interface=\"consumer.interface.read\",\n"
            ")\n"
            "SCHEMAS = Path(__file__).parent.parent / \"schemas\"\n"
            "SOURCE_BIN = (\n"
            "    repo_root / \"skills\" / \"producer\" / \"bin\"\n"
            ")\n",
            encoding="utf-8",
        )
        (producer_root / "schemas").mkdir()
        (producer_root / "schemas/data.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (producer_root / "bin").mkdir()
        (producer_root / "bin/launcher").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        runtime_blueprint_path = producer_root / "blueprints/runtime.yaml"
        runtime = _read_yaml(runtime_blueprint_path)
        runtime["content"] = [
            r"(?:_rtx/(?:__init__\.py|runtime\.py|helper\.py)|"
            r"tests/.*|state/.*|schemas/.*|bin/.*)"
        ]
        runtime["uses_interfaces"] = [
            {"interface": "consumer.interface.read", "version": 5}
        ]
        _write_yaml(runtime_blueprint_path, runtime)
        producer_path = producer_root / "blueprint.yaml"
        producer = _read_yaml(producer_path)
        producer["content"] = [
            r"(?:SKILL\.md|_rtx/.*|tests/.*|state/.*|schemas/.*|bin/.*)"
        ]
        producer["authority"]["suggested_permissions"] = {
            "bash": [
                {
                    "command": [
                        "python3",
                        "_rtx/runtime.py",
                        "--probe",
                    ],
                    "reason": "Run the owned runtime.",
                }
            ],
            "network": [],
        }
        _write_yaml(producer_path, producer)
        _commit(repository, "moved implementation path shapes")

        plan = _api().build_nested_module_migration(repository.root)
        migrated = plan.planned_files[
            "skills/producer/_rtx/runtime.py"
        ].decode("utf-8")
        child = yaml.safe_load(
            plan.planned_files["skills/producer/_rtx/blueprint.yaml"]
        )

        assert 'caller_skill="producer-rtx"' in migrated
        assert 'Path(__file__).parent / "schemas"' in migrated
        assert (
            'repo_root / "skills" / "producer" / "_rtx/assets/bin"'
            in migrated
        )
        assert "skills/producer/_rtx/schemas/data.json" in plan.planned_files
        assert (
            "skills/producer/_rtx/assets/bin/launcher"
            in plan.planned_files
        )
        assert child["authority"]["suggested_permissions"]["bash"][0][
            "command"
        ] == ["python3", "runtime.py", "--probe"]

    def test_module_only_tests_and_fixtures_move_but_source_ownership_wins(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        module_path = producer_root / "blueprint.yaml"
        module = _read_yaml(module_path)
        module["content"] = [
            (
                r"(?:SKILL\.md|_rtx/.*|tests/test_runtime\.py|"
                r"tests/test_instruction_docs\.py|"
                r"fixtures/runtime-case\.json|state/.*)"
            )
        ]
        _write_yaml(module_path, module)

        runtime_path = producer_root / "blueprints/runtime.yaml"
        runtime = _read_yaml(runtime_path)
        runtime["content"] = [
            r"(?:_rtx/(?:__init__\.py|runtime\.py|helper\.py)|state/.*)"
        ]
        _write_yaml(runtime_path, runtime)

        retained_test = producer_root / "tests/test_instruction_docs.py"
        retained_test.write_text(
            '"""Instruction-layer test retained by its source owner."""\n',
            encoding="utf-8",
        )
        gateway_path = producer_root / "blueprints/gateway.yaml"
        gateway = _read_yaml(gateway_path)
        gateway["content"] = [
            r"(?:SKILL\.md|tests/test_instruction_docs\.py)"
        ]
        _write_yaml(gateway_path, gateway)

        fixture = producer_root / "fixtures/runtime-case.json"
        fixture.parent.mkdir()
        fixture.write_text('{"case":"runtime"}\n', encoding="utf-8")
        undeclared = producer_root / "fixtures/undeclared.json"
        undeclared.write_text('{"owner":"none"}\n', encoding="utf-8")
        _commit(repository, "module-only test and fixture ownership")

        plan = _api().build_nested_module_migration(repository.root)

        assert "skills/producer/_rtx/tests/test_runtime.py" in (
            plan.planned_files
        )
        assert "skills/producer/tests/test_runtime.py" not in (
            plan.planned_files
        )
        assert "skills/producer/_rtx/fixtures/runtime-case.json" in (
            plan.planned_files
        )
        assert "skills/producer/fixtures/runtime-case.json" not in (
            plan.planned_files
        )
        assert "skills/producer/_rtx/tests/test_instruction_docs.py" not in (
            plan.planned_files
        )
        assert "skills/producer/fixtures/undeclared.json" not in plan.path_map
        assert "skills/producer/_rtx/fixtures/undeclared.json" not in (
            plan.planned_files
        )
        assert "skills/producer/tests/test_instruction_docs.py" not in (
            plan.path_map
        )
        parent = yaml.safe_load(
            plan.planned_files["skills/producer/blueprint.yaml"]
        )
        assert re.fullmatch(
            parent["content"][0],
            "tests/test_instruction_docs.py",
        )
        assert plan.path_map["skills/producer/tests/test_runtime.py"] == (
            "skills/producer/_rtx/tests/test_runtime.py"
        )
        assert plan.path_map[
            "skills/producer/fixtures/runtime-case.json"
        ] == "skills/producer/_rtx/fixtures/runtime-case.json"

    def test_moved_test_path_arithmetic_targets_child_and_repository(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        test_path = (
            repository.root
            / "skills/producer/tests/test_runtime.py"
        )
        test_path.write_text(
            "from pathlib import Path\n\n"
            "MODULE_ROOT = Path(__file__).resolve().parents[1]\n"
            "REPO_SRC = MODULE_ROOT.parents[1] / \"src\"\n"
            "RUNTIME = MODULE_ROOT / \"_rtx\" / \"runtime.py\"\n"
            "PAYLOADS = Path(__file__).resolve().parents[1] / \"bin\"\n"
            "def fake_repo():\n"
            "    fake_skill = repo_root / \"skills\" / \"producer\"\n"
            "    return fake_skill / \"bin\"\n",
            encoding="utf-8",
        )
        payload = repository.root / "skills/producer/bin/launcher"
        payload.parent.mkdir()
        payload.write_text("#!/bin/sh\n", encoding="utf-8")
        runtime_blueprint = (
            repository.root
            / "skills/producer/blueprints/runtime.yaml"
        )
        runtime = _read_yaml(runtime_blueprint)
        runtime["content"] = [
            pattern[:-1] + r"|bin/.*)"
            for pattern in runtime["content"]
        ]
        _write_yaml(runtime_blueprint, runtime)
        module_blueprint = repository.root / "skills/producer/blueprint.yaml"
        module = _read_yaml(module_blueprint)
        module["content"] = [
            pattern[:-1] + r"|bin/.*)"
            for pattern in module["content"]
        ]
        _write_yaml(module_blueprint, module)
        _commit(repository, "moved test path arithmetic")

        plan = _api().build_nested_module_migration(repository.root)
        migrated = plan.planned_files[
            "skills/producer/_rtx/tests/test_runtime.py"
        ].decode("utf-8")

        assert (
            "MODULE_ROOT = Path(__file__).resolve().parents[1]"
            in migrated
        )
        assert 'REPO_SRC = MODULE_ROOT.parents[2] / "src"' in migrated
        assert 'RUNTIME = MODULE_ROOT / "runtime.py"' in migrated
        assert (
            'PAYLOADS = Path(__file__).resolve().parents[1] / "assets/bin"'
            in migrated
        )
        assert 'return fake_skill / "_rtx/assets/bin"' in migrated

    def test_moved_test_rewrites_dotted_local_package_import(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        package = producer_root / "_rtx/_local_package"
        package.mkdir()
        package.joinpath("__init__.py").write_text("", encoding="utf-8")
        package.joinpath("_backend.py").write_text(
            "VALUE = 'local'\n",
            encoding="utf-8",
        )
        test_path = producer_root / "tests/test_runtime.py"
        test_path.write_text(
            "from _local_package._backend import VALUE\n",
            encoding="utf-8",
        )
        source_path = producer_root / "blueprints/runtime.yaml"
        source = _read_yaml(source_path)
        source["content"] = [
            pattern[:-1] + r"|_rtx/_local_package/.*)"
            for pattern in source["content"]
        ]
        _write_yaml(source_path, source)
        _commit(repository, "dotted local package import")

        plan = _api().build_nested_module_migration(repository.root)

        migrated = plan.planned_files[
            "skills/producer/_rtx/tests/test_runtime.py"
        ]
        assert (
            b"from .._local_package._backend import VALUE"
            in migrated
        )

    def test_unmodeled_module_only_executable_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        marker = producer_root / "blueprint.yaml"
        module = _read_yaml(marker)
        module["content"].append(r"check\-triage\-status\.sh")
        _write_yaml(marker, module)

        runtime_marker = producer_root / "blueprints/runtime.yaml"
        runtime = _read_yaml(runtime_marker)
        runtime["content"] = [
            pattern.replace("state/.*", r"state/cache\.json")
            for pattern in runtime["content"]
        ]
        migration_blueprint = _read_yaml(
            PROJECT_ROOT
            / "src/officina/common/blueprints/nested-module-migration.yaml"
        )
        runtime_contract = migration_blueprint["interfaces"][
            "common.source.nested-module-migration.interface.python-api"
        ]["contract"]
        runtime_contract["direct_io"]["reads"][0]["path"] = (
            "$module/state/status.json"
        )
        runtime["interfaces"][
            "producer.source.runtime.interface.status"
        ]["contract"] = runtime_contract
        _write_yaml(runtime_marker, runtime)

        executable = producer_root / "check-triage-status.sh"
        executable.write_text(
            "#!/bin/sh\ncat state/status.json\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        state = producer_root / "state/status.json"
        state.write_text('{"status":"ready"}\n', encoding="utf-8")
        _commit(repository, "module-only runtime assets")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match=(
                r"unclassified_files=.*"
                r"skills/producer/check-triage-status\.sh"
            ),
        ):
            _api().build_nested_module_migration(repository.root)

    def test_ambiguous_module_only_asset_fails_with_unclassified_inventory(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        marker = producer_root / "blueprint.yaml"
        module = _read_yaml(marker)
        module["content"].append(r"runtime\.bin")
        _write_yaml(marker, module)
        producer_root.joinpath("runtime.bin").write_bytes(b"ambiguous\n")
        _commit(repository, "ambiguous module-only asset")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match=(
                r"unclassified_files=.*"
                r"skills/producer/runtime\.bin"
            ),
        ):
            _api().build_nested_module_migration(repository.root)

    def test_module_only_auxiliary_target_collision_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        module_path = producer_root / "blueprint.yaml"
        module = _read_yaml(module_path)
        module["content"] = [
            (
                r"(?:SKILL\.md|_rtx/.*|tests/.*|state/.*|"
                r"fixtures/runtime-case\.json)"
            )
        ]
        _write_yaml(module_path, module)
        source = producer_root / "fixtures/runtime-case.json"
        source.parent.mkdir()
        source.write_text('{"source":true}\n', encoding="utf-8")
        target = producer_root / "_rtx/fixtures/runtime-case.json"
        target.parent.mkdir()
        target.write_text('{"collision":true}\n', encoding="utf-8")
        _commit(repository, "module-only auxiliary collision")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="collision",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_ambiguous_moved_implementation_path_is_rejected(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        (producer_root / "bin").mkdir()
        (producer_root / "bin/launcher").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        runtime_path = producer_root / "_rtx/runtime.py"
        runtime_path.write_text(
            'MESSAGE = "copy skills/producer/bin then continue"\n',
            encoding="utf-8",
        )
        runtime_blueprint_path = producer_root / "blueprints/runtime.yaml"
        runtime = _read_yaml(runtime_blueprint_path)
        runtime["content"] = [
            r"(?:_rtx/(?:__init__\.py|runtime\.py|helper\.py)|"
            r"tests/.*|state/.*|bin/.*)"
        ]
        _write_yaml(runtime_blueprint_path, runtime)
        producer_path = producer_root / "blueprint.yaml"
        producer = _read_yaml(producer_path)
        producer["content"] = [
            r"(?:SKILL\.md|_rtx/.*|tests/.*|state/.*|bin/.*)"
        ]
        _write_yaml(producer_path, producer)
        _commit(repository, "ambiguous moved implementation path")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="ambiguous.*implementation path|cannot safely rebase",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_embedded_repository_path_in_docstring_is_rebased(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        (producer_root / "bin").mkdir()
        (producer_root / "bin/launcher").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        runtime_path = producer_root / "_rtx/runtime.py"
        runtime_path.write_text(
            '"""Run <repo>/skills/producer/bin/launcher directly."""\n',
            encoding="utf-8",
        )
        runtime_blueprint_path = producer_root / "blueprints/runtime.yaml"
        runtime = _read_yaml(runtime_blueprint_path)
        runtime["content"] = [
            r"(?:_rtx/(?:__init__\.py|runtime\.py|helper\.py)|"
            r"tests/.*|state/.*|bin/.*)"
        ]
        _write_yaml(runtime_blueprint_path, runtime)
        producer_path = producer_root / "blueprint.yaml"
        producer = _read_yaml(producer_path)
        producer["content"] = [
            r"(?:SKILL\.md|_rtx/.*|tests/.*|state/.*|bin/.*)"
        ]
        _write_yaml(producer_path, producer)
        _commit(repository, "repository path in docstring")

        plan = _api().build_nested_module_migration(repository.root)

        migrated = plan.planned_files[
            "skills/producer/_rtx/runtime.py"
        ]
        assert (
            b"<repo>/skills/producer/_rtx/assets/bin/launcher"
            in migrated
        )

    def test_exact_repository_skill_predicate_rejects_partial_combinations(
        self, tmp_path: Path
    ) -> None:
        for partial in (
            "missing-skill",
            "missing-blueprint",
            "mismatched-id",
            "missing-discovery",
            "nested-discoverable-skill",
        ):
            repository = _fixture_repository(tmp_path / partial)
            guide = repository.root / "skills" / "guide"
            if partial == "missing-skill":
                (guide / "SKILL.md").unlink()
            elif partial == "missing-blueprint":
                (guide / "blueprint.yaml").unlink()
            elif partial == "mismatched-id":
                blueprint = _read_yaml(guide / "blueprint.yaml")
                blueprint["id"] = "other-guide"
                _write_yaml(guide / "blueprint.yaml", blueprint)
            elif partial == "missing-discovery":
                blueprint = _read_yaml(guide / "blueprint.yaml")
                blueprint.pop("discovery")
                _write_yaml(guide / "blueprint.yaml", blueprint)
            else:
                nested = repository.root / "skills" / "container" / "guide"
                nested.parent.mkdir()
                guide.rename(nested)
            _commit(repository, partial)

            with pytest.raises(
                _api().NestedModuleMigrationError,
                match="partial repository-managed skill|top-level skills",
            ):
                _api().build_nested_module_migration(repository.root)

    def test_host_system_tree_is_excluded_from_every_plan_surface(
        self, tmp_path: Path
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )
        rendered = plan.render_manifest()

        assert b"skills/.system" not in rendered
        assert not any(
            path.startswith("skills/.system/")
            for path in plan.planned_files
        )
        assert not any(
            "skills/.system/" in path for path in plan.file_hash_map
        )

    def test_complete_file_hashes_cover_every_planned_output(
        self, tmp_path: Path
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )

        assert set(plan.file_hash_map) == set(plan.planned_files)
        assert set(plan.file_mode_map) == set(plan.planned_files)
        for path, content in plan.planned_files.items():
            assert plan.file_hash_map[path] == (
                "sha256:" + hashlib.sha256(content).hexdigest()
            )
            assert plan.file_mode_map[path] in {0o644, 0o755}

    def test_ambiguous_source_ownership_is_rejected(self, tmp_path: Path) -> None:
        repository = _fixture_repository(tmp_path)
        path = repository.root / "skills/producer/blueprints/gateway.yaml"
        source = _read_yaml(path)
        source["content"] = [".*"]
        _write_yaml(path, source)
        _commit(repository, "ambiguous source ownership")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="ambiguous ownership|overlapping content",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_unresolved_internal_import_is_rejected(self, tmp_path: Path) -> None:
        repository = _fixture_repository(tmp_path)
        runtime = repository.root / "skills/producer/_rtx/runtime.py"
        runtime.write_text(
            "from _rtx.missing import VALUE\n",
            encoding="utf-8",
        )
        _commit(repository, "unresolved import")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="unresolved.*_rtx|import",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_private_or_unknown_facade_target_is_rejected(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        path = repository.root / "skills/producer/blueprint.yaml"
        blueprint = _read_yaml(path)
        blueprint["exports"]["producer.interface.execute"][
            "source_interface"
        ] = "producer.source.runtime.interface.private"
        _write_yaml(path, blueprint)
        _commit(repository, "private facade target")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="private facade target|unknown source interface",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_overlapping_authority_is_rejected(self, tmp_path: Path) -> None:
        repository = _fixture_repository(tmp_path)
        shared = {
            "match": "exact",
            "path": "$HOME/.cache/nested-migration-fixture",
            "allowed_readers": [],
            "reason": "Deliberate collision.",
        }
        for relative in (
            "skills/producer/blueprint.yaml",
            "skills/guide/blueprint.yaml",
        ):
            path = repository.root / relative
            blueprint = _read_yaml(path)
            blueprint["authority"]["owns_filesystem"] = [shared]
            _write_yaml(path, blueprint)
        _commit(repository, "overlapping authority")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="overlapping authority|filesystem ownership",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_unclassified_authority_claim_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        path = repository.root / "skills/producer/blueprint.yaml"
        blueprint = _read_yaml(path)
        blueprint["authority"]["owns_filesystem"] = [
            {
                "match": "exact",
                "path": "$HOME/.cache/unclassified-authority",
                "allowed_readers": ["consumer.interface.read"],
                "reason": "No parent or child source establishes this owner.",
            }
        ]
        _write_yaml(path, blueprint)
        _commit(repository, "unclassified authority")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="cannot determine authority disposition",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_skill_certifier_bootstrap_authority_remains_on_parent(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        certifier_root = repository.root / "skills/skill-certifier"
        producer_root.rename(certifier_root)
        for path in sorted(repository.root.rglob("*")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "producer" in text:
                path.write_text(
                    text.replace("producer", "skill-certifier"),
                    encoding="utf-8",
                )
        blueprint_path = certifier_root / "blueprint.yaml"
        blueprint = _read_yaml(blueprint_path)
        parent_claims = [
            {
                "allowed_readers": [
                    "skill-drift.interface.drift-status"
                ],
                "match": "regex",
                "path": (
                    r"^\.certificates/public-keys/"
                    r"(active-key-id|[0-9a-f]{64}\.pub)$"
                ),
                "reason": (
                    "Retained Ed25519 public keys are read-only verification "
                    "material for certificate consumers."
                ),
            },
            {
                "allowed_readers": [
                    "skill-drift.interface.drift-status"
                ],
                "match": "regex",
                "path": r"^\.certificates/[^/]+\.jsonl$",
                "reason": (
                    "The certifier is the sole writer of append-only node "
                    "certificate histories."
                ),
            },
        ]
        blueprint["authority"]["owns_filesystem"] = parent_claims
        _write_yaml(blueprint_path, blueprint)
        _commit(repository, "live-shaped certifier bootstrap authority")

        plan = _api().build_nested_module_migration(repository.root)

        assert plan.authority_map["skill-certifier"]["owns_filesystem"] == (
            parent_claims
        )
        assert (
            plan.authority_map["skill-certifier-rtx"]["owns_filesystem"]
            == []
        )

    def test_corrupt_certificate_history_aborts_before_planning(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        history = (
            repository.root
            / "skills"
            / "producer"
            / ".certificates"
            / "producer.source.runtime.jsonl"
        )
        history.parent.mkdir()
        history.write_bytes(b'{"not":"a canonical signed v1 envelope"}\n')
        _commit(repository, "corrupt v1 history")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="certificate history|signature|canonical",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_certificate_history_symlink_is_rejected_before_dry_run(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        history = _write_signed_history(
            repository,
            module_root="skills/producer",
            node_id="producer.source.runtime",
            subjects=[
                {
                    "id": "producer.source.runtime",
                    "node_type": "behavioral_source",
                    "version": 5,
                    "blueprint_path": (
                        "skills/producer/blueprints/runtime.yaml"
                    ),
                    "gateway_path": "skills/producer/_rtx/runtime.py",
                }
            ],
        )
        external = tmp_path / "external-history.jsonl"
        history.replace(external)
        history.symlink_to(external)
        _commit(repository, "escaping certificate history symlink")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="certificate history.*regular|symlink|confined",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_validator_relocation_symlink_is_rejected_before_dry_run(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        validator = (
            repository.root
            / "skills"
            / "skill-maker"
            / "validators"
            / "probe.py"
        )
        external = tmp_path / "external-validator.py"
        external.write_text("SECRET = 'outside repository'\n", encoding="utf-8")
        validator.unlink()
        validator.symlink_to(external)
        _commit(repository, "escaping validator symlink")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="validator.*regular|symlink|confined",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_every_signed_history_entry_must_match_its_v4_subject_and_path(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        expected = {
            "id": "producer.source.runtime",
            "node_type": "behavioral_source",
            "version": 5,
            "blueprint_path": "skills/producer/blueprints/runtime.yaml",
            "gateway_path": "skills/producer/_rtx/runtime.py",
        }
        wrong_path = {
            **expected,
            "blueprint_path": "skills/producer/blueprints/caller.yaml",
        }
        _write_signed_history(
            repository,
            module_root="skills/producer",
            node_id="producer.source.runtime",
            subjects=[expected, wrong_path],
        )
        _commit(repository, "signed history with a mismatched second subject")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="unexpected historical subject|subject.*path",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_certifier_v5_closure_histories_archive_in_dependency_first_order(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        repository.root.joinpath(".gitignore").write_text(
            "**/.certificates/\n",
            encoding="utf-8",
        )
        consumer_marker = repository.root / "modules/consumer/blueprint.yaml"
        consumer = _read_yaml(consumer_marker)
        consumer["exports"]["consumer.interface.read"]["access"][
            "allowed_callers"
        ].append("skill-certifier")
        _write_yaml(consumer_marker, consumer)

        certifier_root = repository.root / "skills/skill-certifier"
        certifier_root.joinpath("_rtx").mkdir(parents=True)
        certifier_root.joinpath("blueprints").mkdir()
        certifier_root.joinpath("SKILL.md").write_text(
            "# Skill certifier\n", encoding="utf-8"
        )
        certifier_root.joinpath("_rtx/certifier.py").write_text(
            "def certify():\n    return True\n", encoding="utf-8"
        )
        _write_yaml(
            certifier_root / "blueprint.yaml",
            {
                "schema_version": 4,
                "node_type": "module",
                "id": "skill-certifier",
                "version": 4,
                "description": "Fixture certifier.",
                "category": "skill-making-development-assistant",
                "role": "meta-skill",
                "kind": "certifier",
                "gateway": {
                    "path": "SKILL.md",
                    "language": "Markdown",
                },
                "content": [r"(?:SKILL\.md|_rtx/.*)"],
                "discovery": {"mechanism": "skill"},
                "authority": {"owns_filesystem": []},
                "sources": {
                    "skill-certifier.source.certifier": {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/certifier.yaml",
                        }
                    }
                },
                "exports": {},
            },
        )
        _write_yaml(
            certifier_root / "blueprints/certifier.yaml",
            {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": "skill-certifier.source.certifier",
                "version": 2,
                "description": "Fixture certifier runtime.",
                "gateway": {
                    "path": "_rtx/certifier.py",
                    "language": "Python>=3.11",
                },
                "content": [r"_rtx/certifier\.py"],
                "dependencies": [],
                "contract_references": [
                    {
                        "base": "repository-root",
                        "path": "modules/consumer/README.md",
                    }
                ],
                "uses_interfaces": [],
                "interfaces": {},
            },
        )
        _write_signed_history(
            repository,
            module_root="modules/consumer",
            node_id="consumer.source.read",
            subjects=[
                {
                    "id": "consumer.source.read",
                    "node_type": "behavioral_source",
                    "version": 3,
                    "blueprint_path": (
                        "modules/consumer/blueprints/read.yaml"
                    ),
                    "gateway_path": "modules/consumer/README.md",
                }
            ],
        )
        parent_history = _write_signed_history(
            repository,
            module_root="skills/skill-certifier",
            node_id="skill-certifier",
            subjects=[
                {
                    "id": "skill-certifier",
                    "node_type": "module",
                    "version": 4,
                    "blueprint_path": "skills/skill-certifier/blueprint.yaml",
                    "gateway_path": "skills/skill-certifier/SKILL.md",
                }
            ],
        )
        _write_signed_history(
            repository,
            module_root="skills/skill-certifier",
            node_id="skill-certifier.source.certifier",
            subjects=[
                {
                    "id": "skill-certifier.source.certifier",
                    "node_type": "behavioral_source",
                    "version": 2,
                    "blueprint_path": (
                        "skills/skill-certifier/blueprints/certifier.yaml"
                    ),
                    "gateway_path": (
                        "skills/skill-certifier/_rtx/certifier.py"
                    ),
                }
            ],
        )
        _commit(repository, "certifier closure histories")

        plan = _api().build_nested_module_migration(repository.root)
        histories = dict(plan.history_map)
        consumer_history = histories["consumer.source.read"]
        certifier_history = histories["skill-certifier.source.certifier"]

        assert consumer_history["disposition"] == "archive-and-restart-v2"
        assert certifier_history["disposition"] == "archive-and-restart-v2"
        assert (
            consumer_history["certifier_postorder_index"]
            < certifier_history["certifier_postorder_index"]
        )
        parent_relative = parent_history.relative_to(repository.root).as_posix()
        parent_archive = (
            "references/certification-history/v4-cutover/"
            + parent_relative
        )
        complete_hash = (
            "sha256:" + hashlib.sha256(parent_history.read_bytes()).hexdigest()
        )
        state_by_path = {
            operation["path"]: operation
            for operation in plan.to_manifest()["state_operations"]
        }
        assert state_by_path == {
            (
                "modules/consumer/.certificates/"
                "consumer.source.read.jsonl"
            ): {
                "action": "remove-active-at-authorized-cutover",
                "archive": (
                    "references/certification-history/v4-cutover/"
                    "modules/consumer/.certificates/"
                    "consumer.source.read.jsonl"
                ),
                "complete_file_hash": histories["consumer.source.read"][
                    "complete_file_hash"
                ],
                "path": (
                    "modules/consumer/.certificates/"
                    "consumer.source.read.jsonl"
                ),
            },
            parent_relative: {
                "action": "remove-active-at-authorized-cutover",
                "archive": parent_archive,
                "complete_file_hash": complete_hash,
                "path": parent_relative,
            },
            (
                "skills/skill-certifier/.certificates/"
                "skill-certifier.source.certifier.jsonl"
            ): {
                "action": "remove-active-at-authorized-cutover",
                "archive": (
                    "references/certification-history/v4-cutover/"
                    "skills/skill-certifier/.certificates/"
                    "skill-certifier.source.certifier.jsonl"
                ),
                "complete_file_hash": histories[
                    "skill-certifier.source.certifier"
                ]["complete_file_hash"],
                "path": (
                    "skills/skill-certifier/.certificates/"
                    "skill-certifier.source.certifier.jsonl"
                ),
            },
        }

    def test_existing_generated_target_is_a_collision(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        collision = repository.root / "skills/guide/_rtx/blueprint.yaml"
        collision.parent.mkdir()
        collision.write_text("foreign: bytes\n", encoding="utf-8")
        _commit(repository, "preexisting generated target")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="collision|already exists",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_materialization_refuses_candidate_overwrite(
        self, tmp_path: Path
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )
        candidate = tmp_path / "candidate"
        candidate.mkdir()
        (candidate / "keep.txt").write_text("do not overwrite\n", encoding="utf-8")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="candidate.*exists|overwrite",
        ):
            plan.materialize(candidate)
        assert (candidate / "keep.txt").read_text(encoding="utf-8") == (
            "do not overwrite\n"
        )

    @pytest.mark.parametrize("inside_git", [False, True])
    def test_materialization_rejects_output_inside_source_repository(
        self,
        tmp_path: Path,
        inside_git: bool,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        plan = _api().build_nested_module_migration(repository.root)
        candidate = (
            repository.root / ".git" / "nested-module-candidate"
            if inside_git
            else repository.root / "nested-module-candidate"
        )

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="outside.*source|inside.*source|candidate.*source",
        ):
            plan.materialize(candidate)

        assert not candidate.exists()

    def test_materialization_rejects_symlinked_output_ancestor(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        plan = _api().build_nested_module_migration(repository.root)
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="ancestor|symlink|unsafe",
        ):
            plan.materialize(linked_parent / "candidate")

        assert not (real_parent / "candidate").exists()

    def test_materialization_replaces_input_schema_with_converter_owned_v5(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        schema = (
            repository.root
            / "references"
            / "blueprint"
            / "module.schema.json"
        )
        schema.write_text("{not-json\n", encoding="utf-8")
        _commit(repository, "corrupt candidate-owned v5 schema")
        plan = _api().build_nested_module_migration(repository.root)
        candidate = tmp_path / "candidate"

        plan.materialize(candidate)

        assert (
            candidate / "references" / "blueprint" / "module.schema.json"
        ).read_bytes() == (
            PROJECT_ROOT
            / "references"
            / "blueprint"
            / "migrations"
            / "v5"
            / "module.schema.json"
        ).read_bytes()

    @pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
    def test_planning_rejects_tracked_bytes_hidden_from_git_status(
        self,
        tmp_path: Path,
        index_flag: str,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        relative = "skills/guide/SKILL.md"
        repository.git("update-index", index_flag, relative)
        (repository.root / relative).write_text(
            "# Hidden uncommitted instructions\n",
            encoding="utf-8",
        )
        assert repository.git("status", "--porcelain=v1", "-z").stdout == b""

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="HEAD|tracked.*(bytes|mode)|source.*commit",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_candidate_exposes_exact_git_cutover_evidence(
        self,
        tmp_path: Path,
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )
        candidate = plan.materialize(tmp_path / "candidate")

        expected_paths = {
            operation.path
            for operation in plan.operations
        } | {
            operation.source_path
            for operation in plan.operations
            if operation.source_path is not None
        }
        assert set(candidate.cutover_paths) == expected_paths
        assert candidate.cutover_manifest
        assert {
            change.path for change in candidate.cutover_manifest
        } == expected_paths

    def test_cli_candidate_requires_exact_reviewed_manifest_binding(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        script = PROJECT_ROOT / "scripts" / "migrate-blueprints-v5.py"
        candidate = tmp_path / "candidate"
        command = [
            sys.executable,
            str(script),
            "--candidate",
            str(candidate),
            "--repo-root",
            str(repository.root),
        ]

        missing = subprocess.run(command, check=False, capture_output=True)

        assert missing.returncode == 2
        assert not candidate.exists()

        plan = _api().build_nested_module_migration(repository.root)
        reviewed_hash = hashlib.sha256(plan.render_manifest()).hexdigest()
        for source_commit, manifest_hash in (
            ("0" * len(plan.source_commit), reviewed_hash),
            (plan.source_commit, "0" * 64),
        ):
            rejected = subprocess.run(
                [
                    *command,
                    "--expected-source-commit",
                    source_commit,
                    "--expected-manifest-sha256",
                    manifest_hash,
                ],
                check=False,
                capture_output=True,
            )
            assert rejected.returncode == 2
            assert not candidate.exists()

        accepted = subprocess.run(
            [
                *command,
                "--expected-source-commit",
                plan.source_commit,
                "--expected-manifest-sha256",
                reviewed_hash,
            ],
            check=False,
            capture_output=True,
        )
        assert accepted.returncode == 0, accepted.stderr.decode()
        payload = json.loads(accepted.stdout)
        assert payload["manifest_sha256"] == reviewed_hash

    def test_materialized_candidate_is_idempotent_and_matches_dry_run(
        self, tmp_path: Path
    ) -> None:
        repository = _fixture_repository(tmp_path)
        probe = repository.root / "skills/skill-maker/validators/probe.py"
        probe.chmod(0o755)
        _commit(repository, "preserve executable validator mode")
        plan = _api().build_nested_module_migration(repository.root)
        dry_run = plan.render_manifest()

        candidate = plan.materialize(tmp_path / "candidate")

        assert candidate.manifest_bytes == dry_run
        assert candidate.commit
        assert (
            candidate.root / "validators/skill/probe.py"
        ).stat().st_mode & 0o777 == 0o755
        second = _api().build_nested_module_migration(candidate.root)
        assert second.is_noop
        assert second.operations == ()
        assert _manifest_section(second, "certificate_inputs") == dict(
            second.certificate_input_hashes
        )
        assert repository.git("status", "--porcelain=v1", "-z").stdout == b""

    def test_planning_reads_tracked_inputs_only_from_the_captured_commit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        api = _api()
        original_verify = api._verify_tracked_inputs_match_head
        original_read = api._read_confined_regular
        verified_live_source = False

        def verify_then_guard(*args: Any, **kwargs: Any) -> None:
            nonlocal verified_live_source
            original_verify(*args, **kwargs)
            if Path(args[0]).resolve() == repository.root.resolve():
                verified_live_source = True

        def reject_late_live_read(
            root: Path,
            relative: Path,
            *,
            context: str,
        ) -> bytes:
            if (
                verified_live_source
                and Path(root).resolve() == repository.root.resolve()
                and ".certificates" not in relative.parts
            ):
                raise AssertionError(
                    f"planning reread mutable worktree input: {relative}"
                )
            return original_read(root, relative, context=context)

        monkeypatch.setattr(
            api,
            "_verify_tracked_inputs_match_head",
            verify_then_guard,
        )
        monkeypatch.setattr(
            api,
            "_read_confined_regular",
            reject_late_live_read,
        )

        plan = api.build_nested_module_migration(repository.root)

        assert plan.source_commit == repository.git(
            "rev-parse", "HEAD"
        ).stdout.decode().strip()

    def test_candidate_commit_is_deterministic_for_one_reviewed_plan(
        self,
        tmp_path: Path,
    ) -> None:
        plan = _api().build_nested_module_migration(
            _fixture_repository(tmp_path).root
        )

        first = plan.materialize(tmp_path / "candidate-a")
        second = plan.materialize(tmp_path / "candidate-b")

        assert first.commit == second.commit
        assert first.manifest_bytes == second.manifest_bytes

    def test_tracked_v1_history_is_archived_and_git_deleted_without_state_op(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        subject = {
            "id": "producer.source.runtime",
            "node_type": "behavioral_source",
            "version": 5,
            "blueprint_path": "skills/producer/blueprints/runtime.yaml",
            "gateway_path": "skills/producer/_rtx/runtime.py",
        }
        history = _write_signed_history(
            repository,
            module_root="skills/producer",
            node_id="producer.source.runtime",
            subjects=[subject],
        )
        _commit(repository, "track v1 certificate history")

        plan = _api().build_nested_module_migration(repository.root)

        relative = history.relative_to(repository.root).as_posix()
        assert plan.history_map["producer.source.runtime"][
            "disposition"
        ] == "archive-and-restart-v2"
        assert any(
            operation.action == "delete"
            and operation.path.as_posix() == relative
            for operation in plan.operations
        )
        assert not any(
            operation["path"] == relative
            for operation in plan.to_manifest()["state_operations"]
        )
        candidate = plan.materialize(tmp_path / "candidate")
        assert not (candidate.root / relative).exists()
        archive = candidate.root / plan.history_map[
            "producer.source.runtime"
        ]["archive"]
        assert archive.read_bytes() == history.read_bytes()

    def test_ignored_signed_histories_are_bound_archived_and_not_git_deleted(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        ignore = repository.root / ".gitignore"
        ignore.write_text("**/.certificates/\n", encoding="utf-8")
        _commit(repository, "ignore live certificate state")
        subject = {
            "id": "producer.source.runtime",
            "node_type": "behavioral_source",
            "version": 5,
            "blueprint_path": "skills/producer/blueprints/runtime.yaml",
            "gateway_path": "skills/producer/_rtx/runtime.py",
        }
        history = _write_signed_history(
            repository,
            module_root="skills/producer",
            node_id="producer.source.runtime",
            subjects=[subject],
        )
        assert repository.git("status", "--porcelain=v1", "-z").stdout == b""

        plan = _api().build_nested_module_migration(repository.root)

        relative = history.relative_to(repository.root).as_posix()
        assert relative in plan.certificate_input_hashes
        assert plan.history_map["producer.source.runtime"][
            "disposition"
        ] == "archive-and-restart-v2"
        assert not any(
            operation.action == "delete"
            and operation.path.as_posix() == relative
            for operation in plan.operations
        )
        candidate = plan.materialize(tmp_path / "candidate")
        archive = candidate.root / plan.history_map[
            "producer.source.runtime"
        ]["archive"]
        assert archive.read_bytes() == history.read_bytes()

    def test_non_markdown_skill_source_moves_even_when_not_already_under_rtx(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        producer_root = repository.root / "skills/producer"
        (producer_root / "jobs.yaml").write_text(
            "jobs: []\n",
            encoding="utf-8",
        )
        _write_yaml(
            producer_root / "blueprints/jobs-config.yaml",
            {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": "producer.source.jobs-config",
                "version": 2,
                "description": "Runtime job configuration.",
                "gateway": {"path": "jobs.yaml", "language": "YAML"},
                "content": [r"jobs\.yaml"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {},
            },
        )
        module_path = producer_root / "blueprint.yaml"
        module = _read_yaml(module_path)
        module["content"] = [
            r"(?:SKILL\.md|jobs\.yaml|_rtx/.*|tests/.*|state/.*)"
        ]
        module["sources"]["producer.source.jobs-config"] = {
            "blueprint": {
                "base": "module-root",
                "path": "blueprints/jobs-config.yaml",
            }
        }
        _write_yaml(module_path, module)
        _commit(repository, "non-markdown runtime source")

        plan = _api().build_nested_module_migration(repository.root)

        assert plan.identity_map["producer.source.jobs-config"] == (
            "producer-rtx.source.jobs-config"
        )
        assert (
            plan.path_map["skills/producer/jobs.yaml"]
            == "skills/producer/_rtx/jobs.yaml"
        )

    def test_unaliased_absolute_rtx_import_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        runtime = repository.root / "skills/producer/_rtx/runtime.py"
        runtime.write_text(
            "import _rtx.helper\nVALUE = _rtx.helper.VALUE\n",
            encoding="utf-8",
        )
        _commit(repository, "unaliased absolute runtime import")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="unaliased absolute import|bound name",
        ):
            _api().build_nested_module_migration(repository.root)

    @pytest.mark.parametrize("statement", ["import _rtx\n", "import _rtx as runtime\n"])
    def test_absolute_rtx_package_import_fails_closed(
        self,
        tmp_path: Path,
        statement: str,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        runtime = repository.root / "skills/producer/_rtx/runtime.py"
        runtime.write_text(statement, encoding="utf-8")
        _commit(repository, "absolute runtime package import")

        with pytest.raises(
            _api().NestedModuleMigrationError,
            match="absolute package import.*cannot preserve",
        ):
            _api().build_nested_module_migration(repository.root)

    def test_module_only_test_parent_path_is_rebased_after_move(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        test_path = repository.root / "skills/producer/tests/test_runtime.py"
        test_path.write_text(
            "from pathlib import Path\n"
            'RTX = Path(__file__).parent.parent / "_rtx"\n',
            encoding="utf-8",
        )
        _commit(repository, "runtime test path expression")

        plan = _api().build_nested_module_migration(repository.root)

        migrated = plan.planned_files[
            "skills/producer/_rtx/tests/test_runtime.py"
        ]
        assert b"RTX = Path(__file__).parent.parent\n" in migrated

    def test_git_index_mode_is_authoritative_when_filesystem_mode_is_unreliable(
        self,
        tmp_path: Path,
    ) -> None:
        repository = _fixture_repository(tmp_path)
        runtime = repository.root / "skills/producer/_rtx/runtime.py"
        runtime.chmod(0o755)
        _commit(repository, "executable runtime")
        repository.git("config", "core.filemode", "false")
        runtime.chmod(0o644)
        assert repository.git("status", "--porcelain=v1", "-z").stdout == b""

        plan = _api().build_nested_module_migration(repository.root)

        assert plan.file_mode_map[
            "skills/producer/_rtx/runtime.py"
        ] == 0o755
