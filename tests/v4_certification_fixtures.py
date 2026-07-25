from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import yaml

from officina.common.certification_hashing import (
    CANONICAL_NODE_HASH_POLICY,
    CERTIFIER_INTERFACE_ID,
    compute_certification_basis_hash,
    compute_node_hash_states,
    derive_certifier_identity,
    expected_certifier_checks,
    resolve_certification_basis_paths,
)
from officina.common.certificate_records import (
    canonical_certificate_envelope_bytes,
    load_or_create_certificate_signing_key,
    sign_certificate_payload,
)
from officina.common.blueprint_graph import load_repository_blueprint_graph
from officina.common.certification_view import certificate_log_path
from officina.common.git_provenance import (
    pin_blueprint_v4_mechanical_commit,
    pin_blueprint_v4_source_overlay_commit,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCHEMA_ROOT = PROJECT_ROOT / "references" / "blueprint"
SOURCE_CERTIFICATION_ROOT = PROJECT_ROOT / "references" / "certification"
CHECKS = expected_certifier_checks()


class MemorySecretBackend:
    name = "memory"

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, secret: str) -> None:
        self.values[(namespace, key)] = secret

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self.values.pop((namespace, key), None) is not None


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def contract() -> dict[str, object]:
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
    source_id: str,
    source_interface: str,
    export_interface: str,
) -> None:
    module = root / "skills" / module_id
    module.mkdir(parents=True)
    (module / "SKILL.md").write_text(f"{module_id} instructions.\n", encoding="utf-8")
    write_yaml(
        module / "blueprints" / "gateway.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": 1,
            "description": f"{module_id} gateway source.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {
                source_interface: {
                    "version": 1,
                    "description": "Run.",
                    "contract": contract(),
                }
            },
        },
    )
    write_yaml(
        module / "blueprint.yaml",
        {
            "schema_version": 4,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            "description": f"{module_id} module.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
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
                export_interface: {
                    "source_interface": source_interface,
                    "access": {"allow_all_modules": True, "allowed_callers": []},
                }
            },
        },
    )


def create_v4_repository(
    root: Path,
    *,
    extra_modules: tuple[str, ...] = (),
):
    schema_root = root / "references" / "blueprint"
    shutil.copytree(SOURCE_SCHEMA_ROOT, schema_root)
    migration_map = root / "docs/plans/unified-architecture-migration-map.yaml"
    migration_map.parent.mkdir(parents=True, exist_ok=True)
    migration_map.write_text(
        "declarations:\n  version_2:\n    merge_decisions: []\n",
        encoding="utf-8",
    )
    certification_root = root / "references" / "certification"
    certification_root.mkdir(parents=True)
    shutil.copy2(
        SOURCE_CERTIFICATION_ROOT / "node-hash-policy.yaml",
        certification_root / "node-hash-policy.yaml",
    )
    shutil.copy2(
        SOURCE_CERTIFICATION_ROOT / "node-hash-policy.schema.json",
        certification_root / "node-hash-policy.schema.json",
    )
    _write_module(
        root,
        "demo-skill",
        "demo-skill.source.gateway",
        "demo-skill.source.gateway.interface.run",
        "demo-skill.interface.run",
    )
    _write_module(
        root,
        "skill-certifier",
        "skill-certifier.source.gateway",
        "skill-certifier.source.gateway.interface.certify",
        CERTIFIER_INTERFACE_ID,
    )
    for module_id in extra_modules:
        _write_module(
            root,
            module_id,
            f"{module_id}.source.gateway",
            f"{module_id}.source.gateway.interface.run",
            f"{module_id}.interface.run",
        )
    manifest = (
        root
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "[\n"
        '  "references/blueprint/**/*.schema.json",\n'
        '  "references/certification/node-hash-policy.yaml",\n'
        '  "references/certification/node-hash-policy.schema.json",\n'
        '  "skills/skill-certifier/SKILL.md",\n'
        '  "skills/skill-certifier/blueprint.yaml",\n'
        '  "skills/skill-certifier/blueprints/*.yaml"\n'
        "]\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Tests"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "core.autocrlf", "false"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "commit",
            "-qm",
            "materialize mechanical v4 blueprint candidate",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    pin_blueprint_v4_mechanical_commit(root, commit)
    pin_blueprint_v4_source_overlay_commit(root, commit)
    graph = load_repository_blueprint_graph(root, schema_root=schema_root)
    basis_paths = resolve_certification_basis_paths(root)
    states = compute_node_hash_states(
        graph,
        repo_root=root,
        policy_path=root / CANONICAL_NODE_HASH_POLICY,
        certification_basis_hash=compute_certification_basis_hash(root),
        certification_basis_paths=basis_paths,
    )
    return graph, states, commit


def postorder(graph: object) -> tuple[str, ...]:
    children = {node_id: [] for node_id in graph.nodes}
    for edge in graph.certification_edges:
        children[edge.source_node_id].append(edge.target_node_id)
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        for child in sorted(children[node_id]):
            visit(child)
        ordered.append(node_id)

    for node_id in sorted(graph.nodes):
        visit(node_id)
    return tuple(ordered)


def payload(
    root: Path,
    graph: object,
    states: dict[str, object],
    node_id: str,
    commit: str,
    key_id: str,
) -> dict[str, object]:
    node = graph.nodes[node_id]
    state = states[node_id]
    return {
        "certificate_schema_version": 1,
        "subject": {
            "id": node.node_id,
            "node_type": node.node_type,
            "version": node.version,
            "blueprint_path": node.blueprint_path.relative_to(root).as_posix(),
            "gateway_path": node.gateway_path.relative_to(root).as_posix(),
        },
        "node_hash": state.node_hash,
        "source_commit": commit,
        "input_manifest": [dict(entry) for entry in state.input_manifest],
        "dependencies": [dict(entry) for entry in state.dependency_hashes],
        "certification_basis_hash": state.certification_basis_hash,
        "certifier": derive_certifier_identity(graph, states, commit),
        "checks": [deepcopy(check) for check in CHECKS],
        "key_id": key_id,
        "previous_entry_hash": None,
        "certified_at": "2026-07-20T12:00:00Z",
    }


def write_log(graph: object, node_id: str, entries: list[dict]) -> None:
    path = certificate_log_path(graph.nodes[node_id])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(canonical_certificate_envelope_bytes(entry) + b"\n" for entry in entries)
    )


def create_certified_fixture(
    root: Path,
    *,
    extra_modules: tuple[str, ...] = (),
):
    graph, states, commit = create_v4_repository(
        root, extra_modules=extra_modules
    )
    public_key_root = root / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    key = load_or_create_certificate_signing_key(public_key_root, secret_backend=backend)
    for node_id in postorder(graph):
        write_log(
            graph,
            node_id,
            [
                sign_certificate_payload(
                    payload(root, graph, states, node_id, commit, key.key_id), key
                )
            ],
        )
    return graph, states, commit, public_key_root, backend, key
