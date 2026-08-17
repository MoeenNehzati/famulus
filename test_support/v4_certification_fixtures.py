"""Construct small committed version-4 repositories for certification tests.

The helpers centralize deterministic blueprint, provenance, certificate, and
in-memory signing-key setup so certification tests exercise the same artifact
shape without repeating repository construction.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

import yaml

from officina.certification.hashing import (
    CANONICAL_NODE_HASH_POLICY,
    CERTIFIER_INTERFACE_ID,
    compute_certification_basis_hash,
    compute_node_hash_states,
    derive_certifier_identity,
    expected_certifier_checks,
    resolve_certification_basis_paths,
)
from officina.certification.records import (
    canonical_certificate_envelope_bytes,
    load_or_create_certificate_signing_key,
    sign_certificate_payload,
)
from officina.blueprints.graph import load_repository_blueprint_graph
from officina.certification.view import certificate_log_path
from officina.git.provenance import (
    pin_blueprint_v4_mechanical_commit,
    pin_blueprint_v4_source_overlay_commit,
)
from test_support.git_repository import GitTestRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCHEMA_ROOT = (
    PROJECT_ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v4"
)
SOURCE_CERTIFICATION_ROOT = PROJECT_ROOT / "references" / "certification"
CHECKS = expected_certifier_checks(expected_schema_version=4)


class MemorySecretBackend:
    """Store test signing secrets in process memory.

    Intent
    ------
    Provide the minimal secret-backend protocol needed by certificate fixtures.

    Rationale
    ---------
    Test certificates need real signing behavior without accessing a host keyring.

    Pseudocode
    ----------
    - set name = memory
    - set secrets = namespace and key pairs mapped to secret strings

    Wraps
    -----
    - none
    """

    name = "memory"

    def __init__(self) -> None:
        """Initialize an empty secret map.

        Intent
        ------
        Give each fixture an isolated secret store.

        Rationale
        ---------
        Reusing secrets across tests would make certificate setup order-dependent.

        Pseudocode
        ----------
        - set values = empty mapping

        Wraps
        -----
        - none
        """
        self.values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, secret: str) -> None:
        """Store one secret under a namespace and key.

        Intent
        ------
        Implement the write operation of the test secret-backend protocol.

        Rationale
        ---------
        Signing-key creation persists private material through this interface.

        Pseudocode
        ----------
        - set stored_secret = secret at namespace and key

        Wraps
        -----
        - none
        """
        self.values[(namespace, key)] = secret

    def lookup(self, namespace: str, key: str) -> str | None:
        """Return a stored secret or none.

        Intent
        ------
        Implement the read operation of the test secret-backend protocol.

        Rationale
        ---------
        Certificate helpers must retrieve the private key they previously stored.

        Pseudocode
        ----------
        - return value at namespace and key or none

        Wraps
        -----
        - none
        """
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        """Remove a stored secret and report whether it existed.

        Intent
        ------
        Implement the delete operation of the test secret-backend protocol.

        Rationale
        ---------
        Tests need the same lifecycle surface as a persistent secret backend.

        Pseudocode
        ----------
        - set removed_secret = value removed at namespace and key when present
        - return whether a value was removed

        Wraps
        -----
        - none
        """
        return self.values.pop((namespace, key), None) is not None


def write_yaml(path: Path, value: object) -> None:
    """Write one fixture value as stable YAML.

    Intent
    ------
    Materialize readable blueprint inputs for test repositories.

    Rationale
    ---------
    A shared writer keeps directory creation and YAML ordering consistent.

    Pseudocode
    ----------
    - set parent_directory = created parent directories
    - set yaml_artifact = insertion-ordered YAML serialization

    Wraps
    -----
    - none
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def contract() -> dict[str, object]:
    """Return the minimal valid interface contract used by fixture modules.

    Intent
    ------
    Supply blueprint fixtures with one deterministic public interface contract.

    Rationale
    ---------
    Certification tests need schema-valid nodes but do not need behavioral variety.

    Pseudocode
    ----------
    - return one unattended read-only string-output contract

    Wraps
    -----
    - none
    """
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
    """Write one behavioral-source module and its exported interface.

    Intent
    ------
    Add a schema-valid skill node to a version-4 fixture repository.

    Rationale
    ---------
    Central construction keeps node relationships identical across tests.

    Pseudocode
    ----------
    - set module_directory = created module directory and gateway instructions
    - set source_blueprint = serialized behavioral source blueprint
    - set module_blueprint = serialized module blueprint exporting source interface

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .contract:
      why:
        computes: "Supplies the source interface contract."
    .write_yaml:
      why:
        computes: "Serializes both blueprint mappings."
    """
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


def materialize_v4_repository(
    root: Path,
    *,
    extra_modules: tuple[str, ...] = (),
) -> str:
    """Create and pin a committed v4 repository without graph computation.

    Intent
    ------
    Materialize the shared committed input state for certification fixtures.

    Rationale
    ---------
    Mechanical and source-overlay provenance must reference the exact commit
    containing the fixture blueprints.

    Pseudocode
    ----------
    - set repository = created or initialized test repository
    - set schema_inputs = copied version-4 schemas and certification policy
    - set fixture_modules = required and requested fixture modules
    - set commit = committed mechanical candidate
    - set provenance = mechanical and source-overlay pins at commit
    - return commit

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._write_module:
      why:
        computes: "Materializes each fixture skill module."
    officina.git.provenance.pin_blueprint_v4_mechanical_commit:
      why:
        computes: "Pins mechanical provenance to the fixture commit."
    officina.git.provenance.pin_blueprint_v4_source_overlay_commit:
      why:
        computes: "Pins source-overlay provenance to the fixture commit."
    """
    repository = (
        GitTestRepository.initialize_existing_empty(root)
        if root.is_dir()
        else GitTestRepository.create(root)
    )
    schema_root = root / "references" / "blueprint"
    shutil.copytree(SOURCE_SCHEMA_ROOT, schema_root)
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
    repository.git("add", ".")
    repository.git(
        "commit",
        "-qm",
        "materialize mechanical v4 blueprint candidate",
    )
    commit = repository.git("rev-parse", "HEAD").stdout.decode("ascii").strip()
    pin_blueprint_v4_mechanical_commit(root, commit)
    pin_blueprint_v4_source_overlay_commit(root, commit)
    return commit


def create_v4_repository(
    root: Path,
    *,
    extra_modules: tuple[str, ...] = (),
):
    """Create a committed version-4 repository and compute its graph state.

    Intent
    ------
    Return the canonical graph, node hashes, and source commit used by tests.

    Rationale
    ---------
    Callers that do not need signed certificates should stop after graph setup.

    Pseudocode
    ----------
    - commit = materialize_v4_repository(root)
    - graph = load_repository_blueprint_graph(root)
    - basis_paths = resolve_certification_basis_paths(root)
    - set basis_hash = computed certification basis hash
    - states = compute_node_hash_states(graph)
    - return graph, states, and commit

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .materialize_v4_repository:
      why:
        constructs: "Creates and pins the committed fixture repository."
    officina.blueprints.graph.load_repository_blueprint_graph:
      why:
        constructs: "Loads the fixture's canonical blueprint graph."
    officina.certification.hashing.compute_node_hash_states:
      why:
        constructs: "Computes node states for certificate payloads."
    officina.certification.hashing.resolve_certification_basis_paths:
      why:
        constructs: "Resolves files included in the certification basis."

    CallsFromRepo
    -------------
    officina.certification.hashing.compute_certification_basis_hash:
      why:
        computes: "Hashes the fixture certification basis."
    """
    commit = materialize_v4_repository(root, extra_modules=extra_modules)
    schema_root = root / "references" / "blueprint"
    graph = load_repository_blueprint_graph(
        root,
        schema_root=schema_root,
        expected_schema_version=4,
    )
    basis_paths = resolve_certification_basis_paths(
        root,
        expected_schema_version=4,
    )
    states = compute_node_hash_states(
        graph,
        repo_root=root,
        policy_path=root / CANONICAL_NODE_HASH_POLICY,
        certification_basis_hash=compute_certification_basis_hash(
            root,
            expected_schema_version=4,
        ),
        certification_basis_paths=basis_paths,
    )
    return graph, states, commit


def postorder(graph: object) -> tuple[str, ...]:
    """Return certification graph node identifiers in dependency postorder.

    Intent
    ------
    Order certificate creation so every dependency is certified first.

    Rationale
    ---------
    Certificate payloads refer to dependency hashes and must follow graph order.

    Pseudocode
    ----------
    - set children = each node mapped to certification children
    - set ordered = unvisited children before each parent
    - return ordered node identifiers

    Wraps
    -----
    - none
    """
    children = {node_id: [] for node_id in graph.nodes}
    for edge in graph.certification_edges:
        children[edge.source_node_id].append(edge.target_node_id)
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        """Append one node after recursively visiting its children.

        Intent
        ------
        Perform the depth-first step of fixture postordering.

        Rationale
        ---------
        A visited set avoids repeats when dependencies converge.

        Pseudocode
        ----------
        - return when node was visited
        - set visited = visited plus node
        - for child in sorted_children:
          - set child_order = recursive visit result
        - set ordered = ordered plus node

        Wraps
        -----
        - none
        """
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
    """Build one deterministic unsigned certificate payload.

    Intent
    ------
    Translate a fixture graph node and hash state into certificate data.

    Rationale
    ---------
    Central payload construction keeps certificate records comparable across tests.

    Pseudocode
    ----------
    - set node = graph node for node identifier
    - set node_state = hash state for node identifier
    - certifier = derive_certifier_identity(graph)
    - return deterministic certificate payload from node, state, commit, and key

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.certification.hashing.derive_certifier_identity:
      why:
        constructs: "Derives the certifier identity carried in the payload."
    """
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
    """Write signed certificate envelopes for one graph node.

    Intent
    ------
    Materialize the certificate log consumed by drift and certification tests.

    Rationale
    ---------
    Canonical envelope serialization preserves production log framing.

    Pseudocode
    ----------
    - set path = certificate log path for node
    - set parent_directory = created certificate-log directory
    - set log_artifact = canonical envelope bytes with newline framing

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.certification.records.canonical_certificate_envelope_bytes:
      why:
        computes: "Serializes each signed log entry canonically."
    InstantiationsFromRepo
    ----------------------
    officina.certification.view.certificate_log_path:
      why:
        constructs: "Resolves the node's canonical certificate log path."
    """
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
    """Create a version-4 repository with a valid signed certificate log.

    Intent
    ------
    Return all repository, graph, signing, and key objects needed by tests.

    Rationale
    ---------
    One fixture builder prevents repeated expensive and error-prone setup logic.

    Pseudocode
    ----------
    - repository_fixture = create_v4_repository(root)
    - set public_key_directory = created public-key directory
    - backend = MemorySecretBackend()
    - key = load_or_create_certificate_signing_key(public_key_directory)
    - for node in postordered_nodes:
      - set signed_certificate = signed deterministic payload
      - set certificate_log = written signed certificate
    - return fixture objects

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .payload:
      why:
        computes: "Builds each unsigned certificate payload."
    .postorder:
      why:
        computes: "Orders nodes after their dependencies."
    .write_log:
      why:
        computes: "Writes each node's signed certificate log."
    officina.certification.records.sign_certificate_payload:
      why:
        computes: "Signs each deterministic certificate payload."

    InstantiationsFromRepo
    ----------------------
    .create_v4_repository:
      why:
        constructs: "Builds the committed repository and graph state."
    .MemorySecretBackend:
      why:
        constructs: "Provides isolated secret storage for the fixture key."
    officina.certification.records.load_or_create_certificate_signing_key:
      why:
        constructs: "Creates the fixture signing identity and key material."
    """
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
