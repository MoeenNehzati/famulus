#!/usr/bin/env python3
"""Certificate issuer for blueprint-backed modules."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from officina.certification.hashing import (
    CANONICAL_NODE_HASH_POLICY,
    CERTIFIER_CHECK_REGISTRY,
    CertificationHashError,
    NodeHashState,
    certification_target_postorder,
    compute_node_hash_states,
    compute_certification_basis_hash,
    certifier_check_registry,
    derive_certifier_identity,
    expected_certifier_checks,
    map_route_smoke_dependencies,
    normalize_node_checks,
    resolve_certification_basis_paths,
    route_smoke_trace_signature,
)
from officina.certification.records import (
    CertificateSigningKey,
    certificate_public_key_root,
    canonical_certificate_envelope_bytes,
    certificate_entry_hash,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    provision_certificate_signing_material,
    sign_certificate_payload,
)
from officina.common.atomic_files import (
    AtomicWriteError,
    atomic_compare_and_append_bytes,
    atomic_replace_bytes,
    read_regular_file_bytes,
)
from officina.blueprints.graph import (
    BlueprintGraphError,
    BlueprintNode,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.certification.view import (
    CertificateCurrentnessView,
    certificate_log_path,
    evaluate_certificate_currentness,
)
from officina.git.provenance import (
    CommitReadiness,
    GitMaterializationError,
    GitSnapshot,
    blueprint_v4_mechanical_commit as blueprint_mechanical_commit,
    capture_git_snapshot,
    materialize_git_commit,
    run_git,
    snapshot_head_matches,
)
from officina.common.repository_paths import (
    RepositoryPathError,
    repository_relative_path,
)
from officina.blueprints.pooled import (
    PooledReviewValidationError,
    pooled_review_path,
    render_pooled_review,
)
from officina.blueprints.process_binding import gateway_language_name
from officina.runtime.python_machine_interface import (
    PythonArgvMachineInterface,
    PythonProcessTarget,
    PythonProcessTargetError,
    PythonRouteSmokeTraceError,
    logical_python_package_name,
    trace_python_route_smoke_dependencies_batch,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_SCHEMA_VERSION = 1
RouteSmokeAuditResult = tuple[
    tuple[
        str,
        PythonProcessTarget,
        tuple[tuple[str, str, str | None], ...],
    ],
    ...,
]
_REGULAR_GIT_FILE_MODES = {"100644", "100755"}


class CertificationError(RuntimeError):
    """CertificationError marks certification-stop failures raised by this issuer.

    Intent
    ------
    Carry deliberate rejection messages through a dedicated exception type so CLI handling can distinguish certification denials from unexpected crashes.

    Rationale
    ---------
    Repository, provenance, hash, signing, and policy gates all reject by message; the shared type keeps those denials catchable without weakening the individual gate text.

    Pseudocode
    ----------
    - raise certification_rejection_message

    Wraps
    -----
    - none
    """


@dataclass(frozen=True)
class CertificationResult:
    """CertificationResult records the certificates written by the private issuer.

    Intent
    ------
    Store the source module, reviewed commit, module root, and per-node outcomes returned by the certificate writer.

    Rationale
    ---------
    The public API needs one immutable value that can be rendered as text or JSON without re-reading logs after issuance.

    Pseudocode
    ----------
    - set result_fields = module source module_root nodes
    - return issued_certificate_record

    Wraps
    -----
    - none
    """

    node_ids: tuple[str, ...]
    source_commit: str


@dataclass(frozen=True)
class GateEvidence:
    """GateEvidence freezes the evidence used by certification gate checks.

    Intent
    ------
    Group node hash, source commit, manifest, dependency hashes, basis digest, and certifier identity into one gate input.

    Rationale
    ---------
    Deterministic and semantic gates must compare the same evidence bundle that later enters the signed certificate payload.

    Pseudocode
    ----------
    - set snapshot_fields = node_hash source_commit manifests identity
    - return snapshot_fields

    Wraps
    -----
    - none
    """

    node_id: str
    node_hash: str
    source_commit: str
    input_manifest: tuple[Mapping[str, object], ...]
    dependencies: tuple[Mapping[str, object], ...]
    certification_basis_hash: str
    certifier_identity: Mapping[str, object]


@dataclass(frozen=True)
class CompletenessFinding:
    """CompletenessFinding names one missing certification disclosure.

    Intent
    ------
    Record the node, blueprint path, subject, field, and message for a completeness gap.

    Rationale
    ---------
    Completeness auditing reports all missing review material at once, so each finding needs enough location data for a human to repair the blueprint.

    Pseudocode
    ----------
    - set finding_fields = node_id subject field message
    - return finding_fields

    Wraps
    -----
    - none
    """

    subject_id: str
    blueprint_path: Path
    field: str
    message: str


REQUIRED_CONTRACT_SECTIONS = (
    "arguments",
    "preconditions",
    "interaction",
    "caller_warnings",
    "outputs",
    "outcomes",
    "execution",
    "helpers",
    "direct_io",
)


def certification_completeness_findings(
    graph: RepositoryBlueprintGraph,
) -> tuple[CompletenessFinding, ...]:
    """List missing signing disclosures in a repository graph.

    Intent
    ------
    Scan nodes and skill interfaces for descriptions, required contract sections, verification rows, and endpoint disclosures before signing.

    Rationale
    ---------
    The pre-signing gate returns a complete tuple of repair targets instead of hiding later missing fields behind the first failure.

    Pseudocode
    ----------
    - set findings = empty_collection
    - for node in graph_nodes:
      - set findings = findings_with_node_gaps
    - return findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CompletenessFinding:
      why:
        constructs: "Each finding is a carried certification-completeness product identifying subject, blueprint path, field, and remediation message."
    """

    findings: list[CompletenessFinding] = []
    for node_id, node in sorted(graph.nodes.items()):
        description = node.declaration.get("description")
        if not isinstance(description, str) or not description.strip():
            findings.append(
                CompletenessFinding(
                    node_id,
                    node.blueprint_path,
                    "description",
                    "module and behavioral-source descriptions are mandatory before signing",
                )
            )
        if node.node_type != "behavioral_source":
            continue
        interfaces = node.declaration.get("interfaces", {})
        if not isinstance(interfaces, Mapping):
            continue
        for interface_id, interface in sorted(interfaces.items()):
            if not isinstance(interface_id, str) or not isinstance(interface, Mapping):
                continue
            interface_description = interface.get("description")
            if (
                not isinstance(interface_description, str)
                or not interface_description.strip()
            ):
                findings.append(
                    CompletenessFinding(
                        interface_id,
                        node.blueprint_path,
                        "description",
                        "interface description is mandatory before signing",
                    )
                )
            contract = interface.get("contract")
            for section in REQUIRED_CONTRACT_SECTIONS:
                if not isinstance(contract, Mapping) or section not in contract:
                    findings.append(
                        CompletenessFinding(
                            interface_id,
                            node.blueprint_path,
                            f"contract.{section}",
                            f"contract section {section} is mandatory before signing",
                        )
                    )
            if isinstance(contract, Mapping):
                execution = contract.get("execution")
                verification = (
                    execution.get("verification")
                    if isinstance(execution, Mapping)
                    else None
                )
                if isinstance(execution, Mapping) and (
                    not isinstance(verification, list) or not verification
                ):
                    findings.append(
                        CompletenessFinding(
                            interface_id,
                            node.blueprint_path,
                            "contract.execution.verification",
                            "final execution verification must be nonempty before signing",
                        )
                    )
                direct_io = contract.get("direct_io")
                network = (
                    direct_io.get("network")
                    if isinstance(direct_io, Mapping)
                    else None
                )
                if isinstance(network, list):
                    for index, entry in enumerate(network):
                        if isinstance(entry, Mapping) and not (
                            isinstance(entry.get("endpoint"), str)
                            and entry["endpoint"].strip()
                        ):
                            findings.append(
                                CompletenessFinding(
                                    interface_id,
                                    node.blueprint_path,
                                    f"contract.direct_io.network[{index}].endpoint",
                                    "network endpoint must be complete before signing",
                                )
                            )
    return tuple(findings)


def protected_review_projection(
    graph: RepositoryBlueprintGraph,
) -> dict[str, object]:
    """Extract graph fields protected during semantic review.

    Intent
    ------
    Build a comparable projection of module ids, node kinds, gateway paths, dependencies, and interface contracts.

    Rationale
    ---------
    Semantic migration review may alter blueprint wording, but it must not silently change executable wiring or protected dependency structure.

    Pseudocode
    ----------
    - set projection = protected_node_and_interface_fields
    - return projection

    Wraps
    -----
    - none
    """

    projected: dict[str, object] = {}
    for node_id, node in sorted(graph.nodes.items()):
        declaration = node.declaration
        record: dict[str, object] = {
            "node_type": node.node_type,
            "version": node.version,
            "gateway": deepcopy(declaration.get("gateway")),
            "content": deepcopy(declaration.get("content")),
        }
        if node.node_type == "module":
            record.update(
                {
                    "authority": deepcopy(declaration.get("authority")),
                    "sources": deepcopy(declaration.get("sources")),
                    "exports": deepcopy(declaration.get("exports")),
                    "discovery": deepcopy(declaration.get("discovery")),
                }
            )
        else:
            interfaces = declaration.get("interfaces", {})
            record.update(
                {
                    "dependencies": deepcopy(declaration.get("dependencies")),
                    "uses_interfaces": deepcopy(
                        declaration.get("uses_interfaces")
                    ),
                    "platform_support": deepcopy(
                        declaration.get("platform_support")
                    ),
                    "runtime_dependencies": deepcopy(
                        declaration.get("runtime_dependencies")
                    ),
                    "interfaces": {
                        interface_id: {
                            "version": interface.get("version"),
                            "process_binding": deepcopy(
                                interface.get("process_binding")
                            ),
                        }
                        for interface_id, interface in sorted(interfaces.items())
                        if isinstance(interface_id, str)
                        and isinstance(interface, Mapping)
                    },
                }
            )
        projected[node_id] = record
    helper_edges = tuple(
        {
            "source_export_id": edge.source_export_id,
            "local_helper_id": edge.local_helper_id,
            "target_interface_id": edge.target_interface_id,
            "target_version": edge.target_version,
            "binding": deepcopy(edge.binding),
        }
        for edge in sorted(
            graph.helper_edges,
            key=lambda item: (
                item.source_export_id,
                item.local_helper_id,
                item.target_interface_id,
                item.target_version,
            ),
        )
    )
    return {"nodes": projected, "helper_edges": helper_edges}


def _hash_bytes(value: bytes) -> str:
    """_hash_bytes returns the canonical SHA-256 label for byte evidence.

    Intent
    ------
    Convert an already-read byte payload into the prefixed digest string used in manifests and deterministic comparisons.

    Rationale
    ---------
    Keeping the prefixing rule in one helper prevents certificate evidence from mixing raw hexadecimal hashes with canonical digest labels.

    Pseudocode
    ----------
    - set digest = hashed_bytes
    - return prefixed_digest

    Wraps
    -----
    - none
    """
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _expected_file_hashes(
    snapshot: GitSnapshot | None,
    paths: Sequence[Path],
) -> dict[str, str]:
    """_expected_file_hashes computes digest expectations for regular files.

    Intent
    ------
    Walk candidate paths inside a Git snapshot and return repo-relative SHA-256 labels for files that still exist as regular files.

    Rationale
    ---------
    Commit-readiness checks compare expected and observed input hashes, so this helper filters missing, escaping, and non-file paths before hashing.

    Pseudocode
    ----------
    - set expected = empty_mapping
    - for path in input_paths:
      - set expected = expected_with_regular_file_digest
    - return expected

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._hash_bytes:
      why:
        serializes: "The helper digest becomes the expected hash label for tracked file evidence."
    """

    if snapshot is None:
        return {}
    expected: dict[str, str] = {}
    for path in paths:
        absolute = Path(os.path.abspath(path))
        try:
            relative = absolute.relative_to(snapshot.repo_root).as_posix()
            metadata = path.lstat()
        except (FileNotFoundError, ValueError):
            continue
        if stat.S_ISREG(metadata.st_mode):
            expected[relative] = _hash_bytes(path.read_bytes())
    return expected


class CommitReadinessInspector:
    """Compare tracked worktree inputs with one captured commit.

    Intent
    ------
    Own the configuration and coordinated observations for one batched commit-readiness decision.

    Rationale
    ---------
    Tree, index, blob, and worktree evidence must use one snapshot while keeping Git process count independent of input count.

    Pseudocode
    ----------
    - set readiness_inspector = snapshot paths expected_hashes atomic_policy
    - return readiness_inspector

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        snapshot: GitSnapshot | None,
        input_paths: Sequence[Path],
        expected_hashes: Mapping[str, str],
        *,
        allow_non_atomic: bool = False,
    ) -> None:
        """Initialize one immutable readiness-observation configuration.

        Intent
        ------
        Store the snapshot, input paths, expected hashes, and atomic-read policy used by inspection.

        Rationale
        ---------
        Keeping these values together prevents one readiness decision from mixing repository observations.

        Pseudocode
        ----------
        - set inspector_state = snapshot input_paths expected_hashes atomic_policy

        Wraps
        -----
        - none
        """
        self._snapshot = snapshot
        self._input_paths = tuple(input_paths)
        self._expected_hashes = dict(expected_hashes)
        self._allow_non_atomic = allow_non_atomic

    def _normalize_paths(self, reasons: set[str]) -> tuple[str, ...]:
        """Normalize input paths and record containment failures.

        Intent
        ------
        Produce a sorted unique repository-relative path set while retaining the canonical outside-repository reason.

        Rationale
        ---------
        Stable ordering and canonical containment are prerequisites for deterministic readiness evidence.

        Pseudocode
        ----------
        - set relative_paths = normalized_contained_inputs
        - set reasons = reasons plus containment_failures
        - return relative_paths

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        officina.common.repository_paths.repository_relative_path:
          why:
            transforms: "Normalizes each candidate path before the inspector stores it in the query set."
        """
        if self._snapshot is None:
            return ()
        relative_paths: set[str] = set()
        for path in self._input_paths:
            try:
                relative_paths.add(
                    repository_relative_path(path, self._snapshot.repo_root).as_posix()
                )
            except RepositoryPathError:
                reasons.add("input-outside-repository")
        return tuple(sorted(relative_paths))

    def _commit_entries(
        self,
        relative_paths: Sequence[str],
    ) -> dict[str, tuple[str, str]] | None:
        """Load requested commit modes and object IDs in one tree query.

        Intent
        ------
        Filter the captured commit's complete recursive tree to the exact requested input paths and map each matching blob to its mode and object ID.

        Rationale
        ---------
        A fixed-size Git command avoids host command-line limits, while set membership keeps filtering linear in the returned tree size.

        Pseudocode
        ----------
        - set requested_paths = exact_input_path_set
        - set tree_result = complete_recursive_commit_tree_query
        - if tree_result failed:
          - return unavailable
        - set commit_entries = requested_parsed_blob_entries
        - return commit_entries

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.git.provenance.run_git:
          why:
            constructs: "Produces the batched tree bytes parsed into returned commit entries."
        """
        assert self._snapshot is not None
        if not relative_paths:
            return {}
        requested_paths = set(relative_paths)
        result = run_git(
            self._snapshot.repo_root,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            self._snapshot.commit,
            check=False,
        )
        if result.returncode != 0:
            return None
        entries: dict[str, tuple[str, str]] = {}
        for record in result.stdout.rstrip(b"\0").split(b"\0"):
            metadata, separator, raw_path = record.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3 or fields[1] != b"blob":
                continue
            try:
                relative_path = os.fsdecode(raw_path)
                mode = fields[0].decode("ascii")
                object_id = fields[2].decode("ascii")
            except UnicodeError:
                continue
            if relative_path in requested_paths:
                entries[relative_path] = (mode, object_id)
        return entries

    def _index_entries(
        self,
        relative_paths: Sequence[str],
    ) -> dict[str, tuple[tuple[str, str, str], ...]] | None:
        """Load requested index modes, object IDs, and stages in one query.

        Intent
        ------
        Filter the complete index to the exact requested repository-relative inputs and group every matching entry by path.

        Rationale
        ---------
        A fixed-size Git command avoids host command-line limits and still retains conflict-stage evidence for requested paths.

        Pseudocode
        ----------
        - set requested_paths = exact_input_path_set
        - set index_result = complete_index_query
        - if index_result failed:
          - return unavailable
        - set grouped_entries = requested_index_records_by_path
        - return grouped_entries_without_malformed_paths

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.git.provenance.run_git:
          why:
            constructs: "Produces the batched index bytes parsed into returned grouped entries."
        """
        assert self._snapshot is not None
        if not relative_paths:
            return {}
        requested_paths = set(relative_paths)
        result = run_git(
            self._snapshot.repo_root,
            "ls-files",
            "--stage",
            "-z",
            check=False,
        )
        if result.returncode != 0:
            return None
        grouped: dict[str, list[tuple[str, str, str]]] = {}
        invalid: set[str] = set()
        for record in result.stdout.rstrip(b"\0").split(b"\0"):
            metadata, separator, raw_path = record.partition(b"\t")
            try:
                relative_path = os.fsdecode(raw_path)
            except UnicodeError:
                continue
            if relative_path not in requested_paths:
                continue
            fields = metadata.split()
            if not separator or len(fields) != 3:
                invalid.add(relative_path)
                continue
            try:
                entry = tuple(field.decode("ascii") for field in fields)
            except UnicodeError:
                invalid.add(relative_path)
                continue
            grouped.setdefault(relative_path, []).append(entry)
        return {
            path: tuple(entries)
            for path, entries in grouped.items()
            if path not in invalid
        }

    def _commit_blobs(self, object_ids: Sequence[str]) -> dict[str, bytes] | None:
        """Read unique commit blobs through one size-delimited batch.

        Intent
        ------
        Return exact blob bytes keyed by object ID for every requested commit object that Git can read, or report that the batch query failed.

        Rationale
        ---------
        Size-delimited parsing preserves arbitrary binary content while bounding Git process count.

        Pseudocode
        ----------
        - set batch_result = commit_blob_batch_query
        - set blobs = size_delimited_payloads_by_object_id
        - return blobs

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.git.provenance.run_git:
          why:
            constructs: "Produces size-delimited batch output parsed into returned blob bytes."
        """
        assert self._snapshot is not None
        ordered_ids = tuple(sorted(set(object_ids)))
        if not ordered_ids:
            return {}
        result = run_git(
            self._snapshot.repo_root,
            "cat-file",
            "--batch",
            check=False,
            input_bytes=b"".join(
                object_id.encode("ascii") + b"\n" for object_id in ordered_ids
            ),
        )
        if result.returncode != 0:
            return None
        blobs: dict[str, bytes] = {}
        output = result.stdout
        offset = 0
        for requested_id in ordered_ids:
            header_end = output.find(b"\n", offset)
            if header_end < 0:
                break
            header = output[offset:header_end].split()
            offset = header_end + 1
            if len(header) != 3 or header[1] != b"blob":
                continue
            try:
                returned_id = header[0].decode("ascii")
                size = int(header[2])
            except (UnicodeError, ValueError):
                break
            payload_end = offset + size
            if payload_end >= len(output) or output[payload_end : payload_end + 1] != b"\n":
                break
            payload = output[offset:payload_end]
            offset = payload_end + 1
            if returned_id == requested_id:
                blobs[requested_id] = payload
        return blobs

    @staticmethod
    def _descriptor_safe_open_supported() -> bool:
        """Return whether no-follow directory-relative reads are available.

        Intent
        ------
        Detect the operating-system primitives required for confined POSIX worktree reads.

        Rationale
        ---------
        Readiness must fail closed rather than silently use a path-following fallback.

        Pseudocode
        ----------
        - return platform_supports_no_follow_directory_reads

        Wraps
        -----
        - none
        """
        return (
            os.name == "posix"
            and hasattr(os, "O_NOFOLLOW")
            and os.open in os.supports_dir_fd
        )

    def _read_worktree_file(
        self,
        relative_path: str,
    ) -> tuple[bytes | None, str | None, str | None]:
        """Read one worktree file without following path substitutions.

        Intent
        ------
        Return confined file bytes, the authoritative worktree mode when available, and any fail-closed reason.

        Rationale
        ---------
        Git metadata batching must not weaken the existing descriptor-safe worktree boundary.

        Pseudocode
        ----------
        - set worktree_handle = confined_regular_file_handle
        - set worktree_bytes = bytes_read_from_handle
        - return worktree_bytes worktree_mode failure_reason

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            constructs: "Produces confined native-platform bytes returned when descriptor-relative POSIX reads do not apply."
        """
        assert self._snapshot is not None
        if os.name == "nt":
            try:
                data = read_regular_file_bytes(
                    self._snapshot.repo_root / relative_path,
                    allowed_root=self._snapshot.repo_root,
                    allow_non_atomic=self._allow_non_atomic,
                )
            except (AtomicWriteError, FileNotFoundError, OSError):
                return None, None, "unsafe-worktree-input"
            return data, None, None
        if not self._descriptor_safe_open_supported():
            return None, None, "descriptor-safe-open-unavailable"

        directory_fd = -1
        final_fd = -1
        file_flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
        )
        directory_flags = file_flags | getattr(os, "O_DIRECTORY", 0)
        try:
            directory_fd = os.open(self._snapshot.repo_root, directory_flags)
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                return None, None, "unsafe-worktree-input"
            parts = Path(relative_path).parts
            for component in parts[:-1]:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                    os.close(next_fd)
                    return None, None, "unsafe-worktree-input"
                os.close(directory_fd)
                directory_fd = next_fd
            final_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
            metadata = os.fstat(final_fd)
            if not stat.S_ISREG(metadata.st_mode):
                return None, None, "unsafe-worktree-input"
            chunks: list[bytes] = []
            while chunk := os.read(final_fd, 1024 * 1024):
                chunks.append(chunk)
            mode = "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
            return b"".join(chunks), mode, None
        except OSError:
            return None, None, "unsafe-worktree-input"
        finally:
            if final_fd >= 0:
                os.close(final_fd)
            if directory_fd >= 0:
                os.close(directory_fd)

    def inspect(self) -> CommitReadiness:
        """Return deterministic readiness evidence for the owned input set.

        Intent
        ------
        Compare commit, index, worktree, mode, and expected-hash evidence for every normalized input while naming unavailable metadata sources directly.

        Rationale
        ---------
        Certification needs the canonical per-path decisions with bounded Git process growth.

        Pseudocode
        ----------
        - set git_evidence = fixed-command tree index and blob observations
        - if a metadata query failed:
          - return failed readiness naming that query
        - set reasons = deterministic_per_path_comparison_findings
        - return readiness_from_reasons_and_snapshot

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._hash_bytes:
          why:
            computes: "Compares worktree bytes with the expected canonical digest without carrying the helper result forward."

        InstantiationsFromRepo
        ----------------------
        officina.git.provenance.CommitReadiness:
          why:
            constructs: "Carries the final source evidence and ordered findings to the repository freeze guard."
        """

        if self._snapshot is None:
            return CommitReadiness(False, None, ("not-a-git-repository",))
        reasons: set[str] = set()
        relative_paths = self._normalize_paths(reasons)
        try:
            commit_entries = self._commit_entries(relative_paths)
            index_entries = self._index_entries(relative_paths)
        except OSError:
            reasons.update(f"git-unavailable:{path}" for path in relative_paths)
            return CommitReadiness(False, None, tuple(sorted(reasons)))
        if commit_entries is None:
            reasons.add("git-tree-query-failed")
        if index_entries is None:
            reasons.add("git-index-query-failed")
        if commit_entries is None or index_entries is None:
            return CommitReadiness(False, None, tuple(sorted(reasons)))

        try:
            commit_blobs = self._commit_blobs(
                tuple(object_id for _mode, object_id in commit_entries.values())
            )
        except OSError:
            commit_blobs = {}
            blob_query_unavailable = True
        else:
            blob_query_unavailable = False
        if commit_blobs is None:
            reasons.add("git-blob-query-failed")
            return CommitReadiness(False, None, tuple(sorted(reasons)))

        for relative_path in relative_paths:
            commit_entry = commit_entries.get(relative_path)
            if commit_entry is None:
                reasons.add(f"not-tracked-at-commit:{relative_path}")
                continue
            commit_mode, commit_object_id = commit_entry
            if commit_mode not in _REGULAR_GIT_FILE_MODES:
                reasons.add(f"unsupported-commit-mode:{relative_path}")
                continue
            entries = index_entries.get(relative_path)
            if not entries:
                reasons.add(f"missing-index-entry:{relative_path}")
                continue
            if any(stage != "0" for _mode, _object_id, stage in entries):
                reasons.add(f"nonzero-index-stage:{relative_path}")
                continue
            if len(entries) != 1:
                reasons.add(f"invalid-index-entry:{relative_path}")
                continue
            index_mode, index_object_id, _stage = entries[0]
            if index_mode not in _REGULAR_GIT_FILE_MODES:
                reasons.add(f"unsupported-index-mode:{relative_path}")
                continue
            if index_mode != commit_mode:
                reasons.add(f"index-mode-differs-from-commit:{relative_path}")
                continue
            if index_object_id != commit_object_id:
                reasons.add(f"index-differs-from-commit:{relative_path}")
                continue
            commit_bytes = commit_blobs.get(commit_object_id)
            if commit_bytes is None:
                reason = (
                    "git-unavailable"
                    if blob_query_unavailable
                    else "unreadable-commit-blob"
                )
                reasons.add(f"{reason}:{relative_path}")
                continue
            worktree_bytes, worktree_mode, worktree_reason = self._read_worktree_file(
                relative_path
            )
            if worktree_reason is not None:
                reasons.add(f"{worktree_reason}:{relative_path}")
                continue
            if worktree_bytes is None:
                reasons.add(f"unsafe-worktree-input:{relative_path}")
                continue
            if worktree_mode is not None and worktree_mode != commit_mode:
                reasons.add(f"worktree-mode-differs-from-commit:{relative_path}")
                continue
            if worktree_bytes != commit_bytes:
                reasons.add(f"worktree-differs-from-commit:{relative_path}")
                continue
            expected_hash = self._expected_hashes.get(relative_path)
            if expected_hash is not None and _hash_bytes(worktree_bytes) != expected_hash:
                reasons.add(f"expected-hash-mismatch:{relative_path}")

        ordered_reasons = tuple(sorted(reasons))
        source = {
            "vcs": "git",
            "commit": self._snapshot.commit,
            "input_paths": list(relative_paths),
        }
        return CommitReadiness(
            stamp_worthy=not ordered_reasons,
            source=source if not ordered_reasons else None,
            reasons=ordered_reasons,
        )


def _python_route_smoke_trace_specs(
    graph: RepositoryBlueprintGraph,
    certification_node_ids: Sequence[str],
) -> tuple[tuple[str, str, PythonProcessTarget], ...]:
    """_python_route_smoke_trace_specs selects Python process bindings for route-smoke tracing.

    Intent
    ------
    Walk requested behavioral-source nodes, reject unknown ids, ignore non-Python gateways, and emit unique process targets with interface ids.

    Rationale
    ---------
    Route-smoke auditing needs executable Python targets rather than raw blueprint fragments; this helper preserves ownership while normalizing gateway bindings.

    Pseudocode
    ----------
    - raise %officina.certification.hashing.CertificationHashError(invalid_route_smoke_subject)
    - set selected = requested_behavioral_sources
    - for interface in selected_interfaces:
      - if gateway_language_is_python:
        - set selected = selected_with_process_target
    - return selected

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.blueprints.process_binding.gateway_language_name:
      why:
        computes: "Classifies the gateway language so only Python process bindings enter route-smoke tracing."

    InstantiationsFromRepo
    ----------------------
    officina.certification.hashing.CertificationHashError:
      why:
        raises: "Rejected route-smoke inputs leave the helper as a typed hash-policy error."
    officina.runtime.python_machine_interface.PythonProcessTarget:
      why:
        constructs: "The returned trace spec carries this process target into dependency tracing."
    officina.runtime.python_machine_interface.logical_python_package_name:
      why:
        transforms: "The module id becomes logical package evidence stored on each constructed process target."
    """

    selected: set[str] = set()
    for node_id in certification_node_ids:
        if node_id not in graph.nodes:
            raise CertificationHashError(
                f"unknown route-smoke certification node: {node_id}"
            )
        selected.add(node_id)
    specifications: dict[tuple[str, PythonProcessTarget], str] = {}
    for node_id, node in sorted(graph.nodes.items()):
        if node_id not in selected:
            continue
        if node.node_type != "behavioral_source" or node.gateway_path is None:
            continue
        gateway = node.declaration.get("gateway")
        language = gateway.get("language") if isinstance(gateway, Mapping) else None
        if not isinstance(language, str) or gateway_language_name(language) != "Python":
            continue
        interfaces = node.declaration.get("interfaces")
        if not isinstance(interfaces, Mapping):
            continue
        try:
            gateway_path = node.gateway_path.relative_to(
                node.module_root
            ).as_posix()
        except ValueError as exc:
            raise CertificationHashError(
                f"{node_id}: Python gateway must remain inside its module"
            ) from exc
        for interface_id, declaration in sorted(interfaces.items()):
            binding = (
                declaration.get("process_binding")
                if isinstance(declaration, Mapping)
                else None
            )
            entry = binding.get("entry") if isinstance(binding, Mapping) else None
            if (
                isinstance(binding, Mapping)
                and binding.get("kind") == "process"
                and isinstance(entry, str)
            ):
                try:
                    logical_package = None
                    logical_entrypoint = None
                    if graph.schema_version == 5:
                        module_id = graph.source_modules[node_id]
                        logical_package = logical_python_package_name(module_id)
                        path = Path(gateway_path)
                        physical_parts = (
                            path.parent.parts
                            if path.name == "__init__.py"
                            else (*path.parent.parts, path.stem)
                        )
                        suffix = ".".join(
                            part
                            for part in physical_parts
                            if part not in {"", "."}
                        )
                        logical_entrypoint = (
                            logical_package
                            if not suffix
                            else f"{logical_package}.{suffix}"
                        )
                    python_target = PythonProcessTarget(
                        Path(gateway_path),
                        entry,
                        logical_package=logical_package,
                        logical_entrypoint=logical_entrypoint,
                    )
                except PythonProcessTargetError as exc:
                    raise CertificationHashError(
                        f"{node_id}: invalid Python process target: {exc}"
                    ) from exc
                specifications.setdefault(
                    (node_id, python_target),
                    str(interface_id),
                )
    return tuple(
        (node_id, interface_id, python_target)
        for (node_id, python_target), interface_id in sorted(
            specifications.items(),
            key=lambda item: (
                item[0][0],
                item[0][1].gateway_path.as_posix(),
                item[0][1].process_entry,
            ),
        )
    )


class RouteSmokeAuditor:
    """Own route tracing and the two-observation stability gate.

    Intent
    ------
    Coordinate static target preparation, independent runtime traces, dependency mapping, and stability comparison.

    Rationale
    ---------
    Route evidence shares configuration and cached specifications but must retain two separate runtime observations.

    Pseudocode
    ----------
    - set route_auditor = graph states basis targets schema
    - return route_auditor

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        graph: RepositoryBlueprintGraph,
        states: Mapping[str, NodeHashState],
        *,
        repo_root: Path,
        certification_basis_paths: Sequence[Path],
        certification_node_ids: Sequence[str],
        schema_root: Path | None = None,
    ) -> None:
        """Initialize route-smoke configuration without executing a trace.

        Intent
        ------
        Store graph evidence, repository paths, target scope, and schema selection for later observations.

        Rationale
        ---------
        Construction remains effect-free so trace failures occur inside the certification error boundary.

        Pseudocode
        ----------
        - set auditor_state = graph states repository basis targets schema

        Wraps
        -----
        - none
        """
        self._graph = graph
        self._states = states
        self._repo_root = repo_root
        self._certification_basis_paths = tuple(certification_basis_paths)
        self._certification_node_ids = tuple(certification_node_ids)
        self._schema_root = schema_root
        self._trace_specs: tuple[tuple[str, str, PythonProcessTarget], ...] | None = None

    def prepare_trace_specs(
        self,
    ) -> tuple[tuple[str, str, PythonProcessTarget], ...]:
        """Build validated static process targets once for both observations.

        Intent
        ------
        Cache the deterministic process-target specifications shared by both independent traces.

        Rationale
        ---------
        Graph walking need not repeat because trace independence concerns runtime loading, not static target construction.

        Pseudocode
        ----------
        - if trace_specs_are_absent:
          - set trace_specs = validated_process_targets
        - return trace_specs

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._python_route_smoke_trace_specs:
          why:
            constructs: "Produces the validated target tuple cached and returned for both runtime observations."
        """

        if self._trace_specs is None:
            self._trace_specs = _python_route_smoke_trace_specs(
                self._graph,
                self._certification_node_ids,
            )
        return self._trace_specs

    def trace_dependencies(self) -> RouteSmokeAuditResult:
        """Run one independent route-smoke dependency observation.

        Intent
        ------
        Execute every prepared process target and map loaded files to canonical certification dependencies.

        Rationale
        ---------
        Each call must observe runtime imports anew while emitting stable comparable signatures.

        Pseudocode
        ----------
        - set traces = independently_loaded_process_dependencies
        - set mappings = canonical_dependencies_for_each_trace
        - return signatures_from_mappings

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.runtime.python_machine_interface.trace_python_route_smoke_dependencies_batch:
          why:
            constructs: "Produces the independently observed loaded paths carried into dependency mapping."
        officina.certification.hashing.map_route_smoke_dependencies:
          why:
            transforms: "Produces canonical dependency mappings carried into each returned signature."
        officina.certification.hashing.route_smoke_trace_signature:
          why:
            serializes: "Produces the deterministic dependency signature carried in the audit result."
        officina.certification.hashing.CertificationHashError:
          why:
            raises: "Carries route tracing failures to the session stability boundary."
        """

        root = Path(self._repo_root).resolve()
        trace_specs = self.prepare_trace_specs()
        specifications = tuple(
            (self._graph.nodes[node_id].module_root, python_target)
            for node_id, _interface_id, python_target in trace_specs
        )
        try:
            trace_options = {}
            if self._graph.schema_version == 5:
                trace_options = {
                    "expected_schema_version": 5,
                    "schema_root": (
                        Path(self._schema_root)
                        if self._schema_root is not None
                        else root / "references" / "blueprint" / "v5"
                    ),
                }
            traces = trace_python_route_smoke_dependencies_batch(
                root,
                specifications,
                **trace_options,
            )
        except (PythonRouteSmokeTraceError, ValueError) as exc:
            raise CertificationHashError(str(exc)) from exc
        results: list[
            tuple[
                str,
                PythonProcessTarget,
                tuple[tuple[str, str, str | None], ...],
            ]
        ] = []
        for node_id, _interface_id, python_target in trace_specs:
            key = (self._graph.nodes[node_id].module_root.resolve(), python_target)
            mappings = map_route_smoke_dependencies(
                self._graph,
                self._states,
                source_node_id=node_id,
                loaded_paths=traces[key],
                certification_basis_paths=self._certification_basis_paths,
                repo_root=root,
            )
            results.append(
                (
                    node_id,
                    python_target,
                    route_smoke_trace_signature(mappings),
                )
            )
        return tuple(results)

    def require_stable_dependencies(self) -> RouteSmokeAuditResult:
        """Require two independently traced dependency observations to agree.

        Intent
        ------
        Execute route tracing twice, convert policy failures, and reject any signature mismatch.

        Rationale
        ---------
        Stable runtime dependency evidence is required before certificate logs may be opened.

        Pseudocode
        ----------
        - set initial = first_independent_trace
        - set repeated = second_independent_trace
        - raise %.CertificationError(trace_failure_or_mismatch)
        - return initial

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Converts hash-policy failures and trace mismatches into a certification denial."
        """

        try:
            initial = self.trace_dependencies()
            repeated = self.trace_dependencies()
        except CertificationHashError as exc:
            raise CertificationError(str(exc)) from exc
        if repeated != initial:
            raise CertificationError(
                "route-smoke dependency audit changed during certification"
            )
        return initial


def _build_certificate_payload(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, object],
    node_id: str,
    *,
    source_commit: str,
    key_id: str,
    previous_entry_hash: str | None,
    certifier_identity: Mapping[str, object],
    checks: Sequence[Mapping[str, object]],
    certified_at: str,
    expected_schema_version: int = 4,
) -> dict[str, object]:
    """_build_certificate_payload builds the dictionary signed as a node certificate.

    Intent
    ------
    Gather subject paths, node-hash state, certifier identity, checks, key metadata, previous-entry linkage, and timestamp into one payload.

    Rationale
    ---------
    Signing code should receive a stable repository-relative payload shape, with missing gateway paths rejected before envelope serialization.

    Pseudocode
    ----------
    - raise %.CertificationError(missing_gateway_path)
    - set payload_paths = repository_relative_subject_paths
    - return certificate_payload_mapping

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.repository_paths.repository_relative_path:
      why:
        transforms: "Converts subject blueprint and gateway paths into repository-relative payload strings."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "A certificate subject without a gateway path leaves as a typed certifier rejection."
    """
    node = graph.nodes[node_id]
    state = states[node_id]
    if node.gateway_path is None:
        raise CertificationError(f"{node_id}: certificate subject requires a gateway path")
    return {
        "certificate_schema_version": (
            2 if expected_schema_version == 5 else 1
        ),
        "subject": {
            "id": node.node_id,
            "node_type": node.node_type,
            "version": node.version,
            "blueprint_path": repository_relative_path(
                node.blueprint_path,
                repo_root,
            ).as_posix(),
            "gateway_path": repository_relative_path(
                node.gateway_path,
                repo_root,
            ).as_posix(),
        },
        "node_hash": state.node_hash,
        "source_commit": source_commit,
        "input_manifest": [dict(entry) for entry in state.input_manifest],
        "dependencies": [dict(entry) for entry in state.dependency_hashes],
        "certification_basis_hash": state.certification_basis_hash,
        "certifier": dict(certifier_identity),
        "checks": [dict(check) for check in checks],
        "key_id": key_id,
        "previous_entry_hash": previous_entry_hash,
        "certified_at": certified_at,
    }


def _build_gate_evidence(
    node_id: str,
    state: object,
    *,
    source_commit: str,
    certifier_identity: Mapping[str, object],
) -> GateEvidence:
    """Build the evidence view consumed by certification gates.

    Intent
    ------
    Extract one node state, basis hash, source commit, manifests, dependencies, and certifier identity for deterministic gate evaluation.

    Rationale
    ---------
    Gate helpers should not reach back into the whole graph when checking one node; this snapshot gives them the bounded evidence they need.

    Pseudocode
    ----------
        - raise %.CertificationError(missing_gate_state)
        - set snapshot = node_gate_evidence
        - return snapshot

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Missing node state or basis evidence rejects the snapshot before deterministic gates can read it."
    .GateEvidence:
      why:
        constructs: "The snapshot object packages one node evidence bundle for deterministic comparisons."
    """
    node_hash = getattr(state, "node_hash", None)
    basis_hash = getattr(state, "certification_basis_hash", None)
    if not isinstance(node_hash, str) or not isinstance(basis_hash, str):
        raise CertificationError(f"{node_id}: canonical gate snapshot is unavailable")
    return GateEvidence(
        node_id=node_id,
        node_hash=node_hash,
        source_commit=source_commit,
        input_manifest=tuple(
            dict(entry) for entry in getattr(state, "input_manifest", ())
        ),
        dependencies=tuple(
            dict(entry) for entry in getattr(state, "dependency_hashes", ())
        ),
        certification_basis_hash=basis_hash,
        certifier_identity=dict(certifier_identity),
    )


def _passed_check(
    gate_name: str,
    *,
    expected_schema_version: int = 4,
) -> dict[str, object]:
    """_passed_check creates a passed check record from the certifier registry.

    Intent
    ------
    Resolve a gate name against the schema-versioned registry and return the normalized passed-check mapping.

    Rationale
    ---------
    Certificate payloads store check ids and versions, not ad hoc labels, so missing registry entries must reject before signing.

    Pseudocode
    ----------
    - set registry = versioned_check_registry
    - raise %.CertificationError(missing_gate)
    - return passed_check_mapping

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.certification.hashing.certifier_check_registry:
      why:
        validates: "Selects the versioned check registry used to authorize the requested gate name."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "A missing gate name leaves this helper as a typed certifier rejection."
    """
    registry = (
        CERTIFIER_CHECK_REGISTRY
        if expected_schema_version == 4
        else certifier_check_registry(expected_schema_version)
    )
    try:
        check_id, version = registry[gate_name]
    except KeyError as exc:
        raise CertificationError(f"{gate_name} gate is unavailable") from exc
    return {
        "id": check_id,
        "version": version,
        "passed": True,
        "findings": [],
    }


def _run_deterministic_check(
    snapshot: GateEvidence,
    *,
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    expected_schema_version: int = 4,
) -> dict[str, object]:
    """Validate one node against deterministic certification evidence.

    Intent
    ------
    Compare recomputed node hashes, input manifests, dependency hashes, basis digest, and certifier identity for a gate snapshot.

    Rationale
    ---------
    A certificate is meaningful only if the deterministic evidence at signing time equals the reviewed evidence captured in the node state.

    Pseudocode
    ----------
        - set expected = reviewed_gate_snapshot
        - if observed_evidence_differs:
          - raise %.CertificationError(deterministic_mismatch)
        - return passed_check

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .certification_completeness_findings:
      why:
        orchestrates: "The completeness scan confirms schema disclosures before deterministic evidence is accepted."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Hash, manifest, dependency, basis, or identity mismatches reject the deterministic gate."
    ._passed_check:
      why:
        constructs: "The passed-check row records the successful gate name and registry version."
    ._build_gate_evidence:
      why:
        constructs: "The reconstructed snapshot provides the observed evidence compared against the reviewed snapshot."
    """

    node = graph.nodes.get(snapshot.node_id)
    state = states.get(snapshot.node_id)
    if node is None or not isinstance(state, NodeHashState):
        raise CertificationError(f"{snapshot.node_id}: deterministic state is unavailable")
    reconstructed = _build_gate_evidence(
        snapshot.node_id,
        state,
        source_commit=snapshot.source_commit,
        certifier_identity=snapshot.certifier_identity,
    )
    if reconstructed != snapshot:
        raise CertificationError(f"{snapshot.node_id}: deterministic snapshot changed")
    if any(
        finding.subject_id in {node.node_id, *node.declaration.get("interfaces", {})}
        for finding in certification_completeness_findings(graph)
    ):
        raise CertificationError(f"{snapshot.node_id}: deterministic completeness failed")
    return _passed_check(
        "deterministic",
        expected_schema_version=expected_schema_version,
    )


def _semantic_attestation_check(
    snapshot: GateEvidence,
    *,
    reviewed_commit: str,
    expected_schema_version: int = 4,
) -> dict[str, object]:
    """_semantic_attestation_check creates the semantic-review check row when migration review is required.

    Intent
    ------
    Run semantic-attestation replay for migration certificates and return the corresponding passed gate record.

    Rationale
    ---------
    The migration path allows reviewed blueprint edits only when replay proves the protected projection stayed stable.

    Pseudocode
    ----------
        - if migration_review_required:
          - set attestation = semantic_replay_result
        - return semantic_check_row

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Failed semantic replay rejects migration-review certification before a passed check is emitted."
    ._passed_check:
      why:
        constructs: "The passed-check row records the successful gate name and registry version."
    """

    if not reviewed_commit or snapshot.source_commit != reviewed_commit:
        raise CertificationError(f"{snapshot.node_id}: semantic review does not match HEAD")
    return _passed_check(
        "semantic-review",
        expected_schema_version=expected_schema_version,
    )


def _blueprint_paths(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
) -> set[Path]:
    """_blueprint_paths collects blueprint paths contained in a graph.

    Intent
    ------
    Convert every graph node blueprint path into a repository-relative path set.

    Rationale
    ---------
    Semantic-attestation diff checks need a compact allowlist of blueprint files so unrelated reviewed changes are rejected.

    Pseudocode
    ----------
        - set paths = empty_set
        - for node in graph_nodes:
          - set paths = paths_with_repo_relative_blueprint
        - return paths

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.repository_paths.repository_relative_path:
      why:
        constructs: "The path normalizer converts blueprint locations into the allowlist used by semantic attestation."
    .CertificationError:
      why:
        raises: "Blueprint paths that escape the repository reject the semantic-attestation allowlist."
    """
    try:
        return {
            repository_relative_path(node.blueprint_path, repo_root)
            for node in graph.nodes.values()
        }
    except RepositoryPathError as exc:
        raise CertificationError("v4 blueprint path escapes its repository") from exc


def _materialize_local_inputs(
    source_root: Path,
    target_root: Path,
    states: Mapping[str, NodeHashState],
    *,
    allow_non_atomic: bool,
) -> None:
    """Copy untracked inputs into a reconstructed commit tree.

    Intent
    ------
    Find local manifest entries, reject unsafe paths or collisions, read each byte payload, and write it under the temporary mechanical tree.

    Rationale
    ---------
    Semantic-attestation replay must restore declared local evidence exactly while preserving repository-boundary and atomic-write guarantees.

    Pseudocode
    ----------
    - raise %.CertificationError(unsafe_local_input)
    - set copied_inputs = declared_local_manifest_entries
    - for input_path in copied_inputs:
      - set copied_inputs = copied_inputs_with_materialized_bytes
    - return copied_inputs

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.atomic_files.atomic_replace_bytes:
      why:
        writes: "Writes each declared local input into the reconstructed tree after path and collision checks."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Unsafe or unreadable local-input materialization leaves as a typed certifier rejection."
    officina.common.atomic_files.read_regular_file_bytes:
      why:
        serializes: "The copied byte payload is read once and carried into the bounded atomic write."
    """

    relative_paths = {
        Path(entry["path"])
        for state in states.values()
        for entry in state.input_manifest
        if entry.get("git_provenance") != "tracked"
        and isinstance(entry.get("path"), str)
    }
    for relative in sorted(relative_paths):
        if relative.is_absolute() or ".." in relative.parts:
            raise CertificationError("local v4 input escapes the candidate repository")
        current = target_root
        for part in relative.parts[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise CertificationError(f"unsafe local v4 input parent: {relative.as_posix()}")
        target = target_root / relative
        if target.exists() or target.is_symlink():
            raise CertificationError(f"local v4 input collides with commit: {relative.as_posix()}")
        try:
            data = read_regular_file_bytes(
                source_root / relative,
                allowed_root=source_root,
                allow_non_atomic=allow_non_atomic,
            )
            atomic_replace_bytes(
                target,
                data,
                allowed_root=target_root,
                mode=0o600,
                allow_non_atomic=allow_non_atomic,
            )
        except (AtomicWriteError, OSError) as exc:
            raise CertificationError(
                f"cannot materialize local v4 input: {relative.as_posix()}"
            ) from exc


def _validate_semantic_attestation(
    repo_root: Path,
    reviewed_graph: RepositoryBlueprintGraph,
    reviewed_states: Mapping[str, NodeHashState],
    *,
    mechanical_commit: str,
    reviewed_commit: str,
    allow_non_atomic: bool,
) -> None:
    """Replay the mechanical baseline behind a reviewed migration commit.

    Intent
    ------
    Materialize the mechanical commit, restore local inputs, load its graph, verify ancestry, restrict changed files, and compare protected projections.

    Rationale
    ---------
    The semantic-review certificate depends on proving that review commits changed only allowed blueprint text and not executable protected structure.

    Pseudocode
    ----------
    - set mechanical_tree = materialized_mechanical_commit
    - set mechanical_graph = loaded_mechanical_blueprint_graph
    - set changed_paths = reviewed_diff_paths
    - if protected_projection_changed:
      - raise %.CertificationError(semantic_attestation_failed)
    - return attestation_passed

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._materialize_local_inputs:
      why:
        writes: "Restores declared local inputs into the temporary mechanical tree before graph loading."
    ._blueprint_paths:
      why:
        computes: "Builds the allowed blueprint-path set used to reject unrelated reviewed-file changes."
    .protected_review_projection:
      why:
        validates: "Compares protected graph projections after path-level review checks pass."
    officina.git.provenance.materialize_git_commit:
      why:
        writes: "Expands the mechanical commit into the temporary attestation workspace."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Any failed replay, ancestry, diff, or projection check leaves as a typed certifier rejection."
    officina.blueprints.graph.load_repository_blueprint_graph:
      why:
        constructs: "The replayed graph provides the baseline projection compared against the reviewed graph."
    officina.git.provenance.run_git:
      why:
        constructs: "Ancestry and changed-path command results are carried into attestation branch decisions."
    """

    with tempfile.TemporaryDirectory(prefix="v4-mechanical-commit-") as raw_root:
        mechanical_root = Path(raw_root)
        try:
            materialize_git_commit(
                repo_root,
                mechanical_commit,
                mechanical_root,
                allow_non_atomic=allow_non_atomic,
            )
            _materialize_local_inputs(
                repo_root,
                mechanical_root,
                reviewed_states,
                allow_non_atomic=allow_non_atomic,
            )
            mechanical_graph = load_repository_blueprint_graph(
                mechanical_root,
                schema_root=mechanical_root / "references" / "blueprint",
                expected_schema_version=4,
            )
        except (GitMaterializationError, CertificationHashError, OSError, ValueError) as exc:
            raise CertificationError(f"mechanical commit cannot be reconstructed: {exc}") from exc
        if not mechanical_graph.nodes or any(
            node.declaration.get("schema_version") != 4
            for node in mechanical_graph.nodes.values()
        ):
            raise CertificationError("mechanical commit does not contain an all-v4 graph")
        ancestry = run_git(
            repo_root,
            "merge-base",
            "--is-ancestor",
            mechanical_commit,
            reviewed_commit,
            check=False,
        )
        if ancestry.returncode != 0:
            raise CertificationError("mechanical commit is not an ancestor of reviewed commit")
        changed = run_git(
            repo_root,
            "diff",
            "--name-only",
            "--no-renames",
            "--no-ext-diff",
            "-z",
            mechanical_commit,
            reviewed_commit,
            "--",
            check=False,
        )
        if changed.returncode != 0:
            raise CertificationError("cannot compare mechanical and reviewed commits")
        changed_paths = {
            Path(os.fsdecode(raw_path))
            for raw_path in changed.stdout.rstrip(b"\0").split(b"\0")
            if raw_path
        }
        allowed_paths = _blueprint_paths(
            mechanical_graph, mechanical_root
        ) | _blueprint_paths(reviewed_graph, repo_root)
        unexpected = sorted(changed_paths - allowed_paths)
        if unexpected:
            raise CertificationError(
                "semantic review may change only blueprint files: "
                + ", ".join(path.as_posix() for path in unexpected)
            )
        if protected_review_projection(mechanical_graph) != protected_review_projection(
            reviewed_graph
        ):
            raise CertificationError("semantic review changed the protected projection")


def _verify_executing_candidate_certifier(
    root: Path,
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
) -> None:
    """_verify_executing_candidate_certifier proves the running certifier belongs to the candidate graph.

    Intent
    ------
    Locate this file inside the candidate root, find its behavioral-source owner, enforce module ownership, and compare its digest to the manifest.

    Rationale
    ---------
    Self-certification is valid only when the process issuing certificates is itself the source file represented by the candidate node hash.

    Pseudocode
    ----------
    - set executing_relative = repository_relative_certifier_path
    - if owner_or_digest_mismatch:
      - raise %.CertificationError(candidate_certifier_mismatch)
    - return ownership_verified

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.repository_paths.repository_relative_path:
      why:
        transforms: "Normalizes the executing certifier path before matching it against manifest entries."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Path escapes, missing ownership, wrong module ownership, and digest mismatch leave as typed rejections."
    """
    executing = Path(__file__).resolve()
    try:
        executing_relative = repository_relative_path(executing, root).as_posix()
    except RepositoryPathError as exc:
        raise CertificationError("executing certifier bytes are outside the candidate") from exc
    owners = [
        node_id
        for node_id, node in graph.nodes.items()
        if node.node_type == "behavioral_source"
        and node.gateway_path is not None
        and node.gateway_path.resolve() == executing
    ]
    if len(owners) != 1:
        raise CertificationError("executing certifier bytes have no unique candidate owner")
    expected_owner = {
        5: "skill-certifier-rtx",
        6: "skill-certifier._rtx",
    }.get(graph.schema_version)
    if (
        expected_owner is not None
        and graph.source_modules.get(owners[0]) != expected_owner
    ):
        raise CertificationError(
            f"executing v{graph.schema_version} certifier source must belong to "
            f"{expected_owner}"
        )
    executing_digest = "sha256:" + hashlib.sha256(executing.read_bytes()).hexdigest()
    owner_state = states.get(owners[0])
    if not isinstance(owner_state, NodeHashState) or not any(
        entry.get("path") == executing_relative
        and entry.get("digest") == executing_digest
        for entry in owner_state.input_manifest
    ):
        raise CertificationError("executing certifier bytes do not match the candidate manifest")


@dataclass(frozen=True)
class RepositoryEvidence:
    """Store one coherent repository evidence derivation.

    Intent
    ------
    Group graph, node states, certification basis, and certifier identity from one observation.

    Rationale
    ---------
    Initial and final evidence must compare as complete values without mixing fields across observations.

    Pseudocode
    ----------
    - set repository_evidence = graph states basis identity
    - return repository_evidence

    Wraps
    -----
    - none
    """

    graph: RepositoryBlueprintGraph
    states: Mapping[str, NodeHashState]
    basis_hash: str
    basis_paths: tuple[Path, ...]
    certifier_identity: Mapping[str, object]


class RepositoryEvidenceLoader:
    """Derive complete evidence under one repository policy.

    Intent
    ------
    Own stable derivation configuration while producing uncached graph, hash, basis, and identity observations.

    Rationale
    ---------
    A stateful configuration owner prevents argument drift while retaining independent initial and final loads.

    Pseudocode
    ----------
    - set evidence_loader = repository schema policy snapshot options
    - return evidence_loader

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        schema_root: Path,
        policy_path: Path,
        snapshot: GitSnapshot,
        expected_schema_version: int,
        allow_non_atomic: bool,
        require_candidate_execution: bool,
    ) -> None:
        """Initialize stable repository evidence derivation configuration.

        Intent
        ------
        Store repository, schema, policy, snapshot, atomicity, and candidate-execution settings.

        Rationale
        ---------
        Both evidence observations must use identical policy inputs without sharing derived results.

        Pseudocode
        ----------
        - set loader_state = repository schema policy snapshot options

        Wraps
        -----
        - none
        """
        self._repo_root = repo_root
        self._schema_root = schema_root
        self._policy_path = policy_path
        self._snapshot = snapshot
        self._expected_schema_version = expected_schema_version
        self._allow_non_atomic = allow_non_atomic
        self._require_candidate_execution = require_candidate_execution

    def load(self) -> RepositoryEvidence:
        """Load and validate one complete repository evidence observation.

        Intent
        ------
        Derive a closed graph, completeness decision, certification basis, node states, and certifier identity together.

        Rationale
        ---------
        Signing and final comparison require every evidence component to come from the same uncached observation.

        Pseudocode
        ----------
        - set graph = validated_repository_graph
        - set basis = certification_basis_paths_and_hash
        - set states = canonical_node_hash_states
        - set identity = certifier_identity_from_states
        - return %.RepositoryEvidence(graph states basis identity)

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._verify_executing_candidate_certifier:
          why:
            validates: "Checks candidate execution ownership after the complete state is derived."

        InstantiationsFromRepo
        ----------------------
        officina.blueprints.graph.load_repository_blueprint_graph:
          why:
            constructs: "Produces the closed graph carried throughout the returned evidence."
        .certification_completeness_findings:
          why:
            constructs: "Produces completeness findings inspected before hash evidence is accepted."
        officina.certification.hashing.resolve_certification_basis_paths:
          why:
            transforms: "Produces basis paths carried into hashing and returned evidence."
        officina.certification.hashing.compute_certification_basis_hash:
          why:
            serializes: "Produces the basis digest carried into node-state derivation and returned evidence."
        officina.certification.hashing.compute_node_hash_states:
          why:
            constructs: "Produces canonical states carried into identity derivation and returned evidence."
        officina.certification.hashing.derive_certifier_identity:
          why:
            constructs: "Produces certifier identity carried in the returned evidence."
        .CertificationError:
          why:
            raises: "Carries graph, completeness, hash, and identity rejections to the certification boundary."
        .RepositoryEvidence:
          why:
            constructs: "Carries the coherent derivation returned to the session."
        """

        try:
            graph = load_repository_blueprint_graph(
                self._repo_root,
                schema_root=self._schema_root,
                expected_schema_version=self._expected_schema_version,
            )
            if not graph.nodes or any(
                node.declaration.get("schema_version")
                != self._expected_schema_version
                for node in graph.nodes.values()
            ):
                raise CertificationError(
                    "private certificate writer accepts only a closed "
                    f"all-v{self._expected_schema_version} repository"
                )
            completeness = certification_completeness_findings(graph)
            if completeness:
                first = completeness[0]
                raise CertificationError(
                    f"v{self._expected_schema_version} certification completeness failed: "
                    f"{first.subject_id}:{first.field} "
                    f"({len(completeness)} finding(s))"
                )
            basis_paths = resolve_certification_basis_paths(
                self._repo_root,
                expected_schema_version=self._expected_schema_version,
                allow_non_atomic=self._allow_non_atomic,
            )
            basis_hash = compute_certification_basis_hash(
                self._repo_root,
                expected_schema_version=self._expected_schema_version,
                allow_non_atomic=self._allow_non_atomic,
            )
            states = compute_node_hash_states(
                graph,
                repo_root=self._repo_root,
                policy_path=self._policy_path,
                certification_basis_hash=basis_hash,
                certification_basis_paths=basis_paths,
                allow_non_atomic=self._allow_non_atomic,
            )
            certifier_identity = derive_certifier_identity(
                graph,
                states,
                self._snapshot.commit,
            )
            if self._require_candidate_execution:
                _verify_executing_candidate_certifier(
                    self._repo_root,
                    graph,
                    states,
                )
        except CertificationHashError as exc:
            raise CertificationError(str(exc)) from exc
        return RepositoryEvidence(
            graph=graph,
            states=states,
            basis_hash=basis_hash,
            basis_paths=basis_paths,
            certifier_identity=certifier_identity,
        )


class RepositoryFreezeGuard:
    """Own repository observations that keep certificate appends race-safe.

    Intent
    ------
    Coordinate status, commit readiness, local claims, tracked bytes and modes, and generated-output allowances.

    Rationale
    ---------
    These observations share lifecycle state and must remain ordered around every append.

    Pseudocode
    ----------
    - set freeze_guard = repository snapshot atomic_policy
    - return freeze_guard

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        snapshot: GitSnapshot,
        allow_non_atomic: bool,
    ) -> None:
        """Initialize repository freeze state before its first observation.

        Intent
        ------
        Store repository identity and initialize empty claim and allowance collections.

        Rationale
        ---------
        One lifecycle owner must retain initial status records for every later append gate.

        Pseudocode
        ----------
        - set guard_state = repository snapshot empty_claims empty_allowances

        Wraps
        -----
        - none
        """
        self._repo_root = repo_root
        self._snapshot = snapshot
        self._allow_non_atomic = allow_non_atomic
        self._initial_untracked_records: set[bytes] = set()
        self._tracked_paths: tuple[Path, ...] = ()
        self._local_claims: dict[str, str] = {}
        self._tracked_claims: dict[Path, tuple[str, bool]] = {}
        self._public_key_relative: Path | None = None
        self._pooled_review_relatives: set[Path] = set()

    def _porcelain_status_records(self, phase: str) -> tuple[bytes, ...]:
        """Return byte-preserving repository status records for one phase.

        Intent
        ------
        Query tracked and untracked status without decoding filenames before policy checks.

        Rationale
        ---------
        Raw records preserve unusual path evidence and support exact preexisting-file comparison.

        Pseudocode
        ----------
        - set status_result = porcelain_status_query
        - raise %.CertificationError(status_unavailable)
        - return nonempty_status_records

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.git.provenance.run_git:
          why:
            constructs: "Produces raw status bytes parsed into the returned record tuple."
        .CertificationError:
          why:
            raises: "Carries unavailable status evidence to the certification boundary."
        """
        status = run_git(
            self._repo_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            check=False,
        )
        if status.returncode != 0:
            raise CertificationError(f"repository status is unavailable {phase}")
        return tuple(
            record
            for record in status.stdout.rstrip(b"\0").split(b"\0")
            if record
        )

    def capture_initial_state(self) -> None:
        """Record preexisting untracked files and reject tracked dirtiness.

        Intent
        ------
        Establish the exact untracked baseline tolerated during later certificate writes.

        Rationale
        ---------
        Generated outputs may be added, but unrelated preexisting files must neither appear nor disappear.

        Pseudocode
        ----------
        - set initial_records = validated_untracked_status_records
        - raise %.CertificationError(tracked_or_undecodable_status)
        - set guard_initial_state = initial_records

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Carries invalid initial repository state to the certification boundary."
        """

        records: set[bytes] = set()
        for record in self._porcelain_status_records("before certification"):
            if not record.startswith(b"?? "):
                raise CertificationError(
                    "tracked repository state changed before certification"
                )
            try:
                os.fsdecode(record[3:])
            except UnicodeError as exc:
                raise CertificationError(
                    "untracked repository state changed before certification"
                ) from exc
            records.add(record)
        self._initial_untracked_records = records

    def configure_inputs(
        self,
        tracked_paths: Sequence[Path],
        local_claims: Mapping[str, str],
    ) -> None:
        """Bind the complete tracked and local input set for later phases.

        Intent
        ------
        Store normalized tracked paths and declared local digests after graph derivation.

        Rationale
        ---------
        Later readiness and append gates must inspect the identical graph-derived input set.

        Pseudocode
        ----------
        - set guard_inputs = tracked_paths local_claims

        Wraps
        -----
        - none
        """

        self._tracked_paths = tuple(tracked_paths)
        self._local_claims = dict(local_claims)

    def require_ready_commit(
        self,
        current_snapshot: GitSnapshot,
        phase: str,
    ) -> None:
        """Require tracked inputs to match the reviewed commit and hashes.

        Intent
        ------
        Run one batched readiness observation and reject any canonical mismatch reason.

        Rationale
        ---------
        Certificate evidence must bind to the exact reviewed commit at both batch boundaries.

        Pseudocode
        ----------
        - set expected_hashes = hashes_for_current_tracked_inputs
        - set readiness = batched_commit_readiness
        - raise %.CertificationError(readiness_reasons)

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._expected_file_hashes:
          why:
            computes: "Builds expected digest claims consumed by the readiness observation."
        .CommitReadinessInspector:
          why:
            orchestrates: "Performs the bounded readiness observation whose decision is enforced locally."

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Carries tracked-input mismatch reasons to the certification boundary."
        """

        readiness = CommitReadinessInspector(
            current_snapshot,
            self._tracked_paths,
            _expected_file_hashes(current_snapshot, self._tracked_paths),
            allow_non_atomic=self._allow_non_atomic,
        ).inspect()
        if not readiness.stamp_worthy:
            raise CertificationError(
                f"tracked certification input changed {phase}: "
                + ",".join(readiness.reasons)
            )

    def require_local_inputs(self, phase: str) -> None:
        """Require every non-Git input to match its declared digest.

        Intent
        ------
        Read each local input through the confined file boundary and compare its canonical hash.

        Rationale
        ---------
        Untracked inputs lack Git provenance and therefore require direct repeated byte verification.

        Pseudocode
        ----------
        - set observed_hashes = hashes_of_confined_local_inputs
        - raise %.CertificationError(local_claim_mismatch)

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._hash_bytes:
          why:
            computes: "Compares each local payload with its declared digest."
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            serializes: "Reads confined local bytes immediately consumed by hashing."

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Carries local-input mismatch decisions to the certification boundary."
        """

        if any(
            _hash_bytes(
                read_regular_file_bytes(
                    self._repo_root / path,
                    allowed_root=self._repo_root,
                    allow_non_atomic=self._allow_non_atomic,
                )
            )
            != digest
            for path, digest in self._local_claims.items()
        ):
            raise CertificationError(f"local input changed {phase}")

    def capture_tracked_inputs(self) -> None:
        """Freeze current tracked bytes and executable modes for append gates.

        Intent
        ------
        Record a canonical byte digest and executable-bit claim for every tracked input.

        Rationale
        ---------
        Direct claims allow fast repeated checks immediately before and after each append.

        Pseudocode
        ----------
        - set tracked_claims = confined_hash_and_mode_for_each_input
        - raise %.CertificationError(unavailable_tracked_input)
        - set guard_tracked_claims = tracked_claims

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            constructs: "Produces confined bytes carried into each stored tracked claim."
        ._hash_bytes:
          why:
            serializes: "Produces the canonical digest carried in each stored tracked claim."
        .CertificationError:
          why:
            raises: "Carries unavailable tracked-input failures to the certification boundary."
        """

        claims: dict[Path, tuple[str, bool]] = {}
        for path in self._tracked_paths:
            try:
                metadata = path.lstat()
                payload = read_regular_file_bytes(
                    path,
                    allowed_root=self._repo_root,
                    allow_non_atomic=self._allow_non_atomic,
                )
            except (AtomicWriteError, OSError) as exc:
                raise CertificationError(
                    f"tracked certification input is unavailable: {path}"
                ) from exc
            claims[path] = (
                _hash_bytes(payload),
                bool(metadata.st_mode & stat.S_IXUSR),
            )
        self._tracked_claims = claims

    def configure_generated_outputs(
        self,
        *,
        public_key_root: Path,
        pooled_review_relatives: set[Path],
    ) -> None:
        """Declare generated paths tolerated by later status checks.

        Intent
        ------
        Normalize the public-key root and store the complete pooled-review output set.

        Rationale
        ---------
        Append-phase status checks must distinguish authorized outputs from unrelated untracked files.

        Pseudocode
        ----------
        - set public_key_relative = repository_relative_public_key_root
        - set pooled_review_allowances = declared_review_paths
        - raise %.CertificationError(public_key_root_outside_repository)

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.common.repository_paths.repository_relative_path:
          why:
            transforms: "Produces the normalized public-key path carried into later status checks."
        .CertificationError:
          why:
            raises: "Carries an invalid public-key output boundary to certification."
        """

        try:
            self._public_key_relative = repository_relative_path(
                Path(os.path.abspath(public_key_root)),
                self._repo_root,
            )
        except BlueprintGraphError as exc:
            raise CertificationError(
                "certificate public-key root is outside repository"
            ) from exc
        self._pooled_review_relatives = set(pooled_review_relatives)

    def _is_pooled_review_temp(self, relative: Path) -> bool:
        """Return whether a path is an authorized pooled-review temporary file.

        Intent
        ------
        Recognize atomic-write temporary names only beneath a declared final review path.

        Rationale
        ---------
        Status checks may tolerate known atomic-write intermediates without accepting arbitrary temporary files.

        Pseudocode
        ----------
        - return path_matches_declared_pooled_review_temp

        Wraps
        -----
        - none
        """
        name = relative.name
        if not name.startswith("..pooled-blueprint-review.yaml.tmp-"):
            return False
        final = relative.parent / ".pooled-blueprint-review.yaml"
        return final in self._pooled_review_relatives

    def require_frozen_inputs(self, phase: str) -> None:
        """Reject repository, index, byte, or mode drift around each append.

        Intent
        ------
        Recheck status allowances, index identity, and every stored tracked byte and mode claim.

        Rationale
        ---------
        No certificate may be appended across a repository mutation even between broader batch-boundary checks.

        Pseudocode
        ----------
        - set status = phase_status_records
        - set index = reviewed_commit_index_comparison
        - set observed_claims = current_tracked_hashes_and_modes
        - raise %.CertificationError(repository_or_input_drift)

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._hash_bytes:
          why:
            computes: "Compares current tracked bytes with stored digest claims."

        InstantiationsFromRepo
        ----------------------
        officina.git.provenance.run_git:
          why:
            constructs: "Produces index-comparison evidence enforced during the phase."
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            constructs: "Produces confined tracked bytes compared with stored claims."
        .CertificationError:
          why:
            raises: "Carries status, index, byte, and mode drift to the certification boundary."
        """

        if self._public_key_relative is None:
            raise CertificationError("certificate public-key root is outside repository")
        current_preexisting_records: set[bytes] = set()
        for record in self._porcelain_status_records(phase):
            if not record:
                continue
            if not record.startswith(b"?? "):
                raise CertificationError(f"tracked repository state changed {phase}")
            try:
                relative = Path(os.fsdecode(record[3:]))
            except UnicodeError as exc:
                raise CertificationError(
                    f"untracked repository state changed {phase}"
                ) from exc
            if record in self._initial_untracked_records:
                current_preexisting_records.add(record)
                continue
            if relative.is_relative_to(self._public_key_relative) or (
                ".certificates" in relative.parts and relative.suffix == ".jsonl"
            ) or relative.as_posix() in self._local_claims or (
                relative in self._pooled_review_relatives
            ) or self._is_pooled_review_temp(relative):
                continue
            raise CertificationError(
                f"untracked repository state changed {phase}: {relative}"
            )
        if current_preexisting_records != self._initial_untracked_records:
            raise CertificationError(f"untracked repository state changed {phase}")
        index = run_git(
            self._repo_root,
            "diff-index",
            "--cached",
            "--quiet",
            self._snapshot.commit,
            "--",
            check=False,
        )
        if index.returncode != 0:
            raise CertificationError(f"tracked certification index changed {phase}")
        for path, (expected_digest, expected_executable) in self._tracked_claims.items():
            try:
                metadata = path.lstat()
                payload = read_regular_file_bytes(
                    path,
                    allowed_root=self._repo_root,
                    allow_non_atomic=self._allow_non_atomic,
                )
            except (AtomicWriteError, OSError) as exc:
                raise CertificationError(
                    f"tracked certification input changed {phase}: {path}"
                ) from exc
            if (
                _hash_bytes(payload) != expected_digest
                or bool(metadata.st_mode & stat.S_IXUSR) != expected_executable
            ):
                raise CertificationError(
                    f"tracked certification input changed {phase}: {path}"
                )


@dataclass(frozen=True)
class IssuedCertificateBatch:
    """Store node order and normalized gate records from one append batch.

    Intent
    ------
    Carry written node IDs and their exact gate records into final currentness verification.

    Rationale
    ---------
    The session must consume append results without reaching into mutable issuer state.

    Pseudocode
    ----------
    - set issued_batch = node_ids checks_by_node
    - return issued_batch

    Wraps
    -----
    - none
    """

    node_ids: tuple[str, ...]
    checks_by_node: Mapping[str, tuple[dict[str, object], ...]]


class CertificateBatchIssuer:
    """Issue and verify an ordered append-only certificate batch.

    Intent
    ------
    Own signing material, gate records, callbacks, predecessor linkage, atomic appends, and log verification.

    Rationale
    ---------
    These effects share per-batch state and strict ordering while repository safety remains delegated to the freeze guard.

    Pseudocode
    ----------
    - set batch_issuer = graph states order key callbacks freeze_guard
    - return batch_issuer

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        graph: RepositoryBlueprintGraph,
        states: Mapping[str, NodeHashState],
        node_order: Sequence[str],
        snapshot: GitSnapshot,
        public_key_root: Path,
        signing_key: CertificateSigningKey,
        certifier_identity: Mapping[str, object],
        reviewed_commit: str,
        certified_at: str,
        expected_schema_version: int,
        allow_non_atomic: bool,
        freeze_guard: RepositoryFreezeGuard,
        before_append: object | None,
        after_append: object | None,
    ) -> None:
        """Initialize one certificate batch without opening any output log.

        Intent
        ------
        Store signing, graph, ordering, callback, and repository-safety collaborators for later issuance.

        Rationale
        ---------
        Effect-free construction keeps all append behavior explicit inside issuance methods.

        Pseudocode
        ----------
        - set issuer_state = repository graph states order snapshot key checks callbacks guard

        Wraps
        -----
        - none
        """
        self._repo_root = repo_root
        self._graph = graph
        self._states = states
        self._node_order = tuple(node_order)
        self._snapshot = snapshot
        self._public_key_root = public_key_root
        self._signing_key = signing_key
        self._certifier_identity = certifier_identity
        self._reviewed_commit = reviewed_commit
        self._certified_at = certified_at
        self._expected_schema_version = expected_schema_version
        self._allow_non_atomic = allow_non_atomic
        self._freeze_guard = freeze_guard
        self._before_append = before_append
        self._after_append = after_append
        self._checks_by_node: dict[str, tuple[dict[str, object], ...]] = {}

    def _require_output_root(self, node_id: str, log_path: Path) -> None:
        """Require a confined regular directory for one certificate log.

        Intent
        ------
        Create the node certificate directory when absent and reject symlinked or escaping roots.

        Rationale
        ---------
        Append-only writes must remain beneath the owning module through a stable directory boundary.

        Pseudocode
        ----------
        - set output_root = certificate_log_parent
        - set metadata = output_root_metadata
        - raise %.CertificationError(unsafe_output_root)

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Carries unsafe certificate output boundaries to the certification session."
        """
        certificate_root = log_path.parent
        if not certificate_root.exists():
            certificate_root.mkdir(mode=0o700)
        try:
            metadata = certificate_root.lstat()
            certificate_root.resolve().relative_to(
                self._graph.nodes[node_id].module_root.resolve()
            )
        except (OSError, ValueError) as exc:
            raise CertificationError(
                f"unsafe certificate output root: {certificate_root}"
            ) from exc
        if certificate_root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise CertificationError(
                f"unsafe certificate output root: {certificate_root}"
            )

    def _read_predecessor(self, node_id: str, log_path: Path) -> tuple[bytes | None, str | None]:
        """Read one existing log and derive its predecessor entry hash.

        Intent
        ------
        Return the exact prior log bytes and final entry hash required for compare-and-append linkage.

        Rationale
        ---------
        New envelopes must bind to both the observed byte tail and its canonical predecessor identity.

        Pseudocode
        ----------
        - set old_bytes = confined_existing_log_bytes
        - set previous_hash = hash_of_final_verified_entry
        - return old_bytes previous_hash

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            constructs: "Produces exact predecessor bytes carried into compare-and-append."
        officina.certification.records.parse_certificate_log:
          why:
            constructs: "Produces verified predecessor entries used to select the final record."
        officina.certification.records.certificate_entry_hash:
          why:
            serializes: "Produces the predecessor hash carried into the new payload."
        """
        if not log_path.exists():
            return None, None
        old_bytes = read_regular_file_bytes(
            log_path,
            allowed_root=self._graph.nodes[node_id].module_root,
            allow_non_atomic=self._allow_non_atomic,
        )
        previous_entries = parse_certificate_log(
            old_bytes,
            self._public_key_root,
            require_active_final=False,
            allow_non_atomic=self._allow_non_atomic,
        )
        return old_bytes, certificate_entry_hash(previous_entries[-1])

    def _gate_records(self, node_id: str) -> tuple[dict[str, object], ...]:
        """Build and validate normalized gate records for one node.

        Intent
        ------
        Run deterministic, route-smoke, and semantic-attestation record construction against one evidence snapshot.

        Rationale
        ---------
        Payload checks must match the schema-selected immutable registry before any append callback executes.

        Pseudocode
        ----------
        - set evidence = node_gate_evidence
        - set records = normalized_gate_records
        - raise %.CertificationError(gate_registry_mismatch)
        - return records

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._run_deterministic_check:
          why:
            validates: "Runs deterministic evidence checks while normalized records are built locally."
        ._passed_check:
          why:
            computes: "Builds the route-smoke pass record consumed immediately by normalization."
        ._semantic_attestation_check:
          why:
            computes: "Builds the schema-selected semantic record consumed immediately by normalization."
        officina.certification.hashing.expected_certifier_checks:
          why:
            validates: "Compares normalized records with the immutable selected registry."

        InstantiationsFromRepo
        ----------------------
        ._build_gate_evidence:
          why:
            constructs: "Produces the node evidence carried through every gate record."
        officina.certification.hashing.normalize_node_checks:
          why:
            transforms: "Produces the canonical check tuple returned for payload construction."
        .CertificationError:
          why:
            raises: "Carries gate-registry drift to the certification session."
        """
        evidence = _build_gate_evidence(
            node_id,
            self._states[node_id],
            source_commit=self._snapshot.commit,
            certifier_identity=self._certifier_identity,
        )
        records = normalize_node_checks(
            (
                _run_deterministic_check(
                    evidence,
                    graph=self._graph,
                    states=self._states,
                    expected_schema_version=self._expected_schema_version,
                ),
                _passed_check(
                    "route-smoke",
                    expected_schema_version=self._expected_schema_version,
                ),
                _semantic_attestation_check(
                    evidence,
                    reviewed_commit=self._reviewed_commit,
                    expected_schema_version=self._expected_schema_version,
                ),
            )
        )
        if records != expected_certifier_checks(self._expected_schema_version):
            raise CertificationError(f"{node_id}: certifier gate registry changed")
        return records

    def _require_unchanged_log(
        self,
        node_id: str,
        log_path: Path,
        old_bytes: bytes | None,
    ) -> None:
        """Require a certificate log to retain its observed predecessor bytes.

        Intent
        ------
        Compare the current log presence and bytes with the predecessor observation made before callbacks.

        Rationale
        ---------
        A callback or concurrent writer must not replace the log between predecessor parsing and append.

        Pseudocode
        ----------
        - set current_bytes = confined_log_bytes_when_present
        - raise %.CertificationError(log_changed)

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            computes: "Reads current log bytes for immediate predecessor comparison."

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Carries predecessor-log races to the certification session."
        """
        if log_path.exists():
            if old_bytes is None or read_regular_file_bytes(
                log_path,
                allowed_root=self._graph.nodes[node_id].module_root,
                allow_non_atomic=self._allow_non_atomic,
            ) != old_bytes:
                raise CertificationError("certificate log changed during certification")
        elif old_bytes is not None:
            raise CertificationError("certificate log changed during certification")

    def _append_frame(
        self,
        node_id: str,
        log_path: Path,
        old_bytes: bytes | None,
        previous_hash: str | None,
        checks: tuple[dict[str, object], ...],
    ) -> tuple[bytes, os.stat_result]:
        """Build, sign, append, and stat one certificate frame.

        Intent
        ------
        Serialize the node payload, sign its envelope, atomically append exact bytes, and capture resulting inode metadata.

        Rationale
        ---------
        Payload evidence and append expectations must remain one uninterrupted per-node operation.

        Pseudocode
        ----------
        - set payload = certificate_payload_from_current_evidence
        - set frame = signed_canonical_envelope_bytes
        - set appended_metadata = frame_appended_when_predecessor_matches
        - return frame appended_metadata

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        officina.certification.records.canonical_certificate_envelope_bytes:
          why:
            serializes: "Serializes the signed envelope immediately before framing."
        officina.common.atomic_files.atomic_compare_and_append_bytes:
          why:
            writes: "Appends the frame only while predecessor bytes remain unchanged."

        InstantiationsFromRepo
        ----------------------
        ._build_certificate_payload:
          why:
            constructs: "Produces the payload carried into signing."
        officina.certification.records.sign_certificate_payload:
          why:
            constructs: "Produces the signed envelope carried into canonical serialization."
        .CertificationError:
          why:
            raises: "Carries append races and invalid output metadata to the session."
        """
        payload = _build_certificate_payload(
            self._repo_root,
            self._graph,
            self._states,
            node_id,
            source_commit=self._snapshot.commit,
            key_id=self._signing_key.key_id,
            previous_entry_hash=previous_hash,
            certifier_identity=self._certifier_identity,
            checks=checks,
            certified_at=self._certified_at,
            expected_schema_version=self._expected_schema_version,
        )
        envelope = sign_certificate_payload(payload, self._signing_key)
        frame = canonical_certificate_envelope_bytes(envelope) + b"\n"
        try:
            atomic_compare_and_append_bytes(
                log_path,
                frame,
                expected_previous_bytes=old_bytes,
                allowed_root=self._graph.nodes[node_id].module_root,
                mode=0o600,
                allow_non_atomic=self._allow_non_atomic,
            )
        except AtomicWriteError as exc:
            raise CertificationError("certificate log changed during certification") from exc
        try:
            appended_metadata = log_path.lstat()
        except OSError as exc:
            raise CertificationError(
                "post-write certificate log is unavailable"
            ) from exc
        if not stat.S_ISREG(appended_metadata.st_mode):
            raise CertificationError("post-write certificate log is not a regular file")
        return frame, appended_metadata

    def _require_appended_frame(
        self,
        node_id: str,
        log_path: Path,
        old_bytes: bytes | None,
        frame: bytes,
        appended_metadata: os.stat_result,
    ) -> None:
        """Require the appended log inode and bytes to remain exact.

        Intent
        ------
        Compare final regular-file identity and full log content with the append result.

        Rationale
        ---------
        Successful atomic append is insufficient if a callback or concurrent process replaces the resulting log.

        Pseudocode
        ----------
        - set final_metadata = certificate_log_metadata
        - set final_bytes = confined_certificate_log_bytes
        - raise %.CertificationError(post_write_log_changed)

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            computes: "Reads final log bytes for immediate comparison with the expected frame."

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Carries post-write inode or byte changes to the session."
        """
        try:
            final_metadata = log_path.lstat()
        except OSError as exc:
            raise CertificationError("post-write certificate log changed") from exc
        if (
            not stat.S_ISREG(final_metadata.st_mode)
            or (final_metadata.st_dev, final_metadata.st_ino)
            != (appended_metadata.st_dev, appended_metadata.st_ino)
        ):
            raise CertificationError("post-write certificate log changed")
        expected_log_bytes = (old_bytes or b"") + frame
        if read_regular_file_bytes(
            log_path,
            allowed_root=self._graph.nodes[node_id].module_root,
            allow_non_atomic=self._allow_non_atomic,
        ) != expected_log_bytes:
            raise CertificationError("post-write certificate log changed")

    def issue_one(self, node_id: str) -> None:
        """Issue one node certificate with all before and after append gates.

        Intent
        ------
        Coordinate predecessor reading, gate records, callbacks, repository freezes, append, and post-write verification.

        Rationale
        ---------
        Per-node effects must retain their exact order so every race opportunity remains fail-closed.

        Pseudocode
        ----------
        - set log_state = validated_output_root_and_predecessor
        - set checks = normalized_node_gate_records
        - set append_result = signed_frame_after_pre_append_gates
        - set verified_result = append_result_after_post_append_gates
        - return verified_result

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        officina.git.provenance.snapshot_head_matches:
          why:
            validates: "Rejects HEAD drift immediately before the append."
        officina.certification.hashing.expected_certifier_checks:
          why:
            validates: "Rejects registry drift immediately after the append."

        InstantiationsFromRepo
        ----------------------
        officina.certification.view.certificate_log_path:
          why:
            constructs: "Produces the node log path carried through the complete issuance operation."
        officina.git.provenance.capture_git_snapshot:
          why:
            constructs: "Produces the post-append snapshot used to reject HEAD drift."
        .CertificationError:
          why:
            raises: "Carries HEAD and check-registry drift to the certification session."
        """

        log_path = certificate_log_path(self._graph.nodes[node_id])
        self._require_output_root(node_id, log_path)
        old_bytes, previous_hash = self._read_predecessor(node_id, log_path)
        checks = self._gate_records(node_id)
        self._checks_by_node[node_id] = checks
        if callable(self._before_append):
            self._before_append(node_id)
        if not snapshot_head_matches(self._snapshot):
            raise CertificationError("HEAD changed during certification")
        self._freeze_guard.require_frozen_inputs("before certificate append")
        self._freeze_guard.require_local_inputs("during certification")
        self._require_unchanged_log(node_id, log_path, old_bytes)
        frame, appended_metadata = self._append_frame(
            node_id,
            log_path,
            old_bytes,
            previous_hash,
            checks,
        )
        if callable(self._after_append):
            self._after_append(node_id)
        final_snapshot = capture_git_snapshot(self._repo_root)
        if (
            final_snapshot is None
            or final_snapshot.repo_root != self._repo_root
            or final_snapshot.commit != self._snapshot.commit
        ):
            raise CertificationError("HEAD changed after certificate append")
        if checks != expected_certifier_checks(self._expected_schema_version):
            raise CertificationError("certifier checks changed after certificate append")
        self._freeze_guard.require_frozen_inputs("after certificate append")
        self._freeze_guard.require_local_inputs("after certificate append")
        self._require_appended_frame(
            node_id,
            log_path,
            old_bytes,
            frame,
            appended_metadata,
        )

    def issue_all(self) -> IssuedCertificateBatch:
        """Issue every ordered node and return immutable batch evidence.

        Intent
        ------
        Execute per-node issuance in dependency order and collect written IDs and check records.

        Rationale
        ---------
        Batch ordering belongs to the issuer while final repository currentness belongs to the session.

        Pseudocode
        ----------
        - set written = nodes_issued_in_certification_order
        - return %.IssuedCertificateBatch(written checks)

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .IssuedCertificateBatch:
          why:
            constructs: "Carries written order and normalized checks into final currentness verification."
        """

        written: list[str] = []
        for node_id in self._node_order:
            self.issue_one(node_id)
            written.append(node_id)
        return IssuedCertificateBatch(tuple(written), dict(self._checks_by_node))


def _certify_repository(
    repo_root: Path,
    *,
    target_node_ids: Sequence[str],
    public_key_root: Path,
    secret_backend: object,
    reviewed_commit: str,
    certified_at: str,
    before_append: object | None = None,
    after_append: object | None = None,
    allow_non_atomic: bool = False,
    require_candidate_execution: bool = False,
    require_migration_review: bool = False,
    expected_schema_version: int = 6,
    schema_root: Path | None = None,
) -> CertificationResult:
    """Issue signed certificates for selected repository nodes.

    Intent
    ------
    Freeze repository state, derive graph hashes and certifier identity, run gates, sign each target payload, and append certificate log entries.

    Rationale
    ---------
    This private writer binds Git inputs, local evidence, key material, pooled reviews, and log tails into one exact reviewed commit before signing.

    Pseudocode
    ----------
        - raise %.CertificationError(rejected_repository_or_gate)
        - set derived = graph_hashes_identity_and_basis
        - set gate_rows = deterministic_and_route_smoke_checks
        - set envelope = signed_certificate_envelope
        - return certification_result

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validate_semantic_attestation:
      why:
        validates: "Replays the mechanical baseline before migration certificates are allowed."
    .CertificateBatchIssuer:
      why:
        orchestrates: "Runs the ordered append batch after repository inputs have been frozen."
    officina.common.atomic_files.atomic_replace_bytes:
      why:
        writes: "Publishes each generated pooled review within its module boundary."
    officina.common.atomic_files.read_regular_file_bytes:
      why:
        computes: "Checks generated pooled-review bytes immediately after publication."
    officina.certification.records.certificate_public_key_root:
      why:
        computes: "Selects whether repository-owned signing material provisioning applies."
    officina.blueprints.pooled.pooled_review_path:
      why:
        computes: "Selects generated review paths used by freeze allowances and publication."
    officina.blueprints.pooled.render_pooled_review:
      why:
        serializes: "Renders the final certificate-backed review before its bounded write."
    officina.common.repository_paths.repository_relative_path:
      why:
        transforms: "Normalizes graph input and generated-review paths for repository checks."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Carries repository, evidence, gate, signing, and final-verification denials."
    .CertificationResult:
      why:
        constructs: "Carries the written node order and reviewed commit to the public boundary."
    .RepositoryEvidenceLoader:
      why:
        constructs: "Produces independent initial and final evidence observations for comparison."
    .RepositoryFreezeGuard:
      why:
        constructs: "Carries repository claims and allowances through every append phase."
    .RouteSmokeAuditor:
      why:
        constructs: "Carries route configuration through two independent dependency traces."
    officina.certification.records.load_or_create_certificate_signing_key:
      why:
        constructs: "Produces externally rooted signing material carried into batch issuance."
    officina.certification.records.provision_certificate_signing_material:
      why:
        constructs: "Produces repository-owned signing material carried into batch issuance."
    officina.certification.hashing.certification_target_postorder:
      why:
        transforms: "Produces dependency-ordered node IDs carried into batch issuance."
    officina.certification.view.CertificateCurrentnessView:
      why:
        constructs: "Carries final current certificates into pooled-review rendering."
    officina.certification.view.evaluate_certificate_currentness:
      why:
        constructs: "Produces the final report used to verify written nodes and reviews."
    officina.git.provenance.blueprint_v4_mechanical_commit:
      why:
        constructs: "Produces the optional migration baseline used by semantic replay."
    officina.git.provenance.capture_git_snapshot:
      why:
        constructs: "Produces initial and final snapshots used to reject HEAD drift."
    officina.git.provenance.run_git:
      why:
        constructs: "Produces candidate atomicity evidence before migration review."
    """

    root = Path(repo_root).resolve()
    if expected_schema_version not in {4, 5, 6}:
        raise CertificationError(
            f"unsupported certification schema version: {expected_schema_version}"
        )
    if require_migration_review and expected_schema_version != 4:
        raise CertificationError("the frozen migration writer accepts only v4")
    if require_migration_review:
        atomic = run_git(
            root, "config", "--bool", "--get", "famulus.candidateAtomicGuarantee",
            check=False,
        )
        if atomic.returncode == 0 and atomic.stdout.strip() == b"false":
            raise CertificationError("non-atomic diagnostic candidate is non-certifiable")
        temp_root = Path(tempfile.gettempdir()).resolve()
        if not root.is_relative_to(temp_root):
            raise CertificationError(
                "private v4 certification is restricted to temporary repositories"
            )
    snapshot = capture_git_snapshot(root)
    if snapshot is None or snapshot.repo_root != root:
        raise CertificationError("v4 certification requires the exact Git repository root")
    if snapshot.commit != reviewed_commit:
        raise CertificationError("v4 certification HEAD does not match the reviewed commit")

    freeze_guard = RepositoryFreezeGuard(
        repo_root=root,
        snapshot=snapshot,
        allow_non_atomic=allow_non_atomic,
    )
    freeze_guard.capture_initial_state()

    mechanical_commit: str | None = None
    if require_migration_review:
        try:
            mechanical_commit = blueprint_mechanical_commit(root)
        except GitMaterializationError as exc:
            raise CertificationError(
                f"candidate mechanical baseline is unavailable: {exc}"
            ) from exc
    selected_schema_root = (
        Path(schema_root)
        if schema_root is not None
        else (
            root / "references" / "blueprint"
            if expected_schema_version == 6
            else root / "references" / "blueprint" / "migrations" / f"v{expected_schema_version}"
        )
    )
    policy_path = root / CANONICAL_NODE_HASH_POLICY

    evidence_loader = RepositoryEvidenceLoader(
        repo_root=root,
        schema_root=selected_schema_root,
        policy_path=policy_path,
        snapshot=snapshot,
        expected_schema_version=expected_schema_version,
        allow_non_atomic=allow_non_atomic,
        require_candidate_execution=require_candidate_execution,
    )
    evidence = evidence_loader.load()
    graph = evidence.graph
    states = evidence.states
    basis_hash = evidence.basis_hash
    basis_paths = evidence.basis_paths
    certifier_identity = evidence.certifier_identity
    if mechanical_commit is not None:
        _validate_semantic_attestation(
            root,
            graph,
            states,
            mechanical_commit=mechanical_commit,
            reviewed_commit=reviewed_commit,
            allow_non_atomic=allow_non_atomic,
        )
    try:
        expanded_target_ids = set(target_node_ids)
        for node_id in tuple(expanded_target_ids):
            node = graph.nodes.get(node_id)
            if node is not None and node.node_type == "module":
                expanded_target_ids.update(graph.module_sources[node_id])
        order = certification_target_postorder(
            graph,
            states,
            tuple(expanded_target_ids),
        )
    except CertificationHashError as exc:
        raise CertificationError(str(exc)) from exc
    route_auditor = RouteSmokeAuditor(
        graph,
        states,
        repo_root=root,
        certification_basis_paths=basis_paths,
        certification_node_ids=order,
        schema_root=selected_schema_root,
    )
    route_auditor.require_stable_dependencies()

    tracked_paths: set[Path] = {
        root / repository_relative_path(path, root)
        for path in (
            *basis_paths,
            *(node.blueprint_path for node in graph.nodes.values()),
        )
    }
    local_claims: dict[str, str] = {}
    for state in states.values():
        for entry in state.input_manifest:
            path = root / entry["path"]
            if entry["git_provenance"] == "tracked":
                tracked_paths.add(path)
            else:
                local_claims[entry["path"]] = entry["digest"]
    ordered_tracked_paths = tuple(sorted(tracked_paths))
    pooled_review_relatives = {
        repository_relative_path(
            pooled_review_path(node.module_root),
            root,
        )
        for node in graph.nodes.values()
        if node.node_type == "module"
    }

    freeze_guard.configure_inputs(ordered_tracked_paths, local_claims)
    freeze_guard.require_ready_commit(snapshot, "before certification")
    freeze_guard.require_local_inputs("before certification")
    freeze_guard.capture_tracked_inputs()
    freeze_guard.configure_generated_outputs(
        public_key_root=public_key_root,
        pooled_review_relatives=pooled_review_relatives,
    )

    if Path(public_key_root).resolve() == certificate_public_key_root(root):
        key = provision_certificate_signing_material(
            root,
            secret_backend=secret_backend,
            allow_non_atomic=allow_non_atomic,
        )
    else:
        key = load_or_create_certificate_signing_key(
            public_key_root,
            secret_backend=secret_backend,
            allow_non_atomic=allow_non_atomic,
        )
    issued_batch = CertificateBatchIssuer(
        repo_root=root,
        graph=graph,
        states=states,
        node_order=order,
        snapshot=snapshot,
        public_key_root=public_key_root,
        signing_key=key,
        certifier_identity=certifier_identity,
        reviewed_commit=reviewed_commit,
        certified_at=certified_at,
        expected_schema_version=expected_schema_version,
        allow_non_atomic=allow_non_atomic,
        freeze_guard=freeze_guard,
        before_append=before_append,
        after_append=after_append,
    ).issue_all()
    written = list(issued_batch.node_ids)
    normalized_checks = dict(issued_batch.checks_by_node)

    final_snapshot = capture_git_snapshot(root)
    if (
        final_snapshot is None
        or final_snapshot.repo_root != root
        or final_snapshot.commit != snapshot.commit
    ):
        raise CertificationError("HEAD changed after certification")
    freeze_guard.require_ready_commit(final_snapshot, "after certification")
    freeze_guard.require_local_inputs("after certification")
    final_evidence = evidence_loader.load()
    if final_evidence != evidence:
        raise CertificationError(
            "graph, dependency, basis, or local input changed during certification"
        )
    final_graph = final_evidence.graph
    final_states = final_evidence.states
    final_basis_paths = final_evidence.basis_paths
    final_certifier_identity = final_evidence.certifier_identity
    final_report = evaluate_certificate_currentness(
        final_graph,
        final_states,
        repo_root=root,
        public_key_root=public_key_root,
        source_commit=final_snapshot.commit,
        certifier_identity=final_certifier_identity,
        checks_by_node=normalized_checks,
        schema_root=selected_schema_root,
        certification_basis_paths=final_basis_paths,
        allow_non_atomic=allow_non_atomic,
    )
    for node_id in written:
        if not final_report.nodes[node_id].current:
            raise CertificationError(
                f"post-write certificate verification failed for {node_id}"
            )
    pooled_view = CertificateCurrentnessView(final_report)
    for module_id in sorted(
        node_id
        for node_id in target_node_ids
        if node_id in final_graph.nodes
        and final_graph.nodes[node_id].node_type == "module"
    ):
        module = final_graph.nodes[module_id]
        path = pooled_review_path(module.module_root)
        try:
            rendered = render_pooled_review(
                final_graph,
                pooled_view,
                root_id=module_id,
            ).encode("utf-8")
            atomic_replace_bytes(
                path,
                rendered,
                allowed_root=module.module_root,
                mode=0o600,
                allow_non_atomic=allow_non_atomic,
            )
            if read_regular_file_bytes(
                path,
                allowed_root=module.module_root,
                allow_non_atomic=allow_non_atomic,
            ) != rendered:
                raise CertificationError(
                    f"{module_id}: post-write pooled review changed"
                )
        except (
            AtomicWriteError,
            OSError,
            PooledReviewValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise CertificationError(
                f"{module_id}: pooled review write failed: {exc}"
            ) from exc
    return CertificationResult(tuple(written), snapshot.commit)


@dataclass(frozen=True)
class CommandResult:
    """CommandResult stores one mechanical command outcome.

    Intent
    ------
    Keep command name, argv, exit status, stdout, and stderr together for certification evidence rendering.

    Rationale
    ---------
    Mechanical checks are evidence even when they fail, so callers need a stable object that can report both status and captured output.

    Pseudocode
    ----------
    - set command_fields = name argv exit_code stdout stderr
    - return command_fields

    Wraps
    -----
    - none
    """
    name: str
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        """passed reports whether the command exited successfully.

        Intent
        ------
        Convert the stored process exit code into the boolean used by certification summaries.

        Rationale
        ---------
        Rendering and certification flow should not duplicate the success convention for local command evidence.

        Pseudocode
        ----------
        - set success = exit_code_is_zero
        - return success

        Wraps
        -----
        - none
        """
        return self.exit_code == 0

    def as_payload(self) -> dict[str, Any]:
        """as_payload serializes command evidence for JSON output.

        Intent
        ------
        Return the command name, argv, exit code, stdout, and stderr as primitive payload fields.

        Rationale
        ---------
        The CLI JSON path needs mechanical evidence without dataclass instances or process objects leaking into the response.

        Pseudocode
        ----------
        - set payload = command_evidence_mapping
        - return payload

        Wraps
        -----
        - none
        """
        return {
            "name": self.name,
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-4000:],
        }


@dataclass(frozen=True)
class NodeCertificationOutcome:
    """NodeCertificationOutcome records the certificate path for one node.

    Intent
    ------
    Store the certified node id and log path belonging to a module-level certification result.

    Rationale
    ---------
    Public reporting groups node outcomes by module but still needs the exact certificate log written for each node.

    Pseudocode
    ----------
    - set node_fields = node_id certificate_path
    - return node_fields

    Wraps
    -----
    - none
    """
    node_id: str
    certificate_path: Path

    def as_payload(self) -> dict[str, str]:
        """as_payload serializes one node certification outcome.

        Intent
        ------
        Return the node id and certificate path as primitive JSON-ready fields.

        Rationale
        ---------
        The CLI should expose node-level certificate locations without requiring callers to understand dataclass internals.

        Pseudocode
        ----------
        - set payload = node_certificate_mapping
        - return payload

        Wraps
        -----
        - none
        """
        return {
            "node_id": self.node_id,
            "certificate_path": self.certificate_path.as_posix(),
        }


@dataclass(frozen=True)
class CertificationOutcome:
    """CertificationOutcome groups certification results for one source module.

    Intent
    ------
    Store the module id, source name, module root, and node outcomes returned by certification.

    Rationale
    ---------
    The public API reports by module because a user target can expand into several certified node logs.

    Pseudocode
    ----------
    - set outcome_fields = module source root nodes
    - return outcome_fields

    Wraps
    -----
    - none
    """
    module: str
    source: str
    module_root: Path
    nodes: tuple[NodeCertificationOutcome, ...]

    def as_payload(self) -> dict[str, Any]:
        """as_payload serializes a module certification outcome.

        Intent
        ------
        Return module metadata and node payloads as a JSON-ready mapping.

        Rationale
        ---------
        The JSON renderer needs a stable primitive shape while text rendering can still use the typed outcome object.

        Pseudocode
        ----------
        - set node_payloads = serialized_node_outcomes
        - return module_payload

        Wraps
        -----
        - none
        """
        return {
            "module": self.module,
            "source": self.source,
            "module_root": self.module_root.as_posix(),
            "status": "certified",
            "nodes": [node.as_payload() for node in self.nodes],
        }


def run_local_command(
    name: str,
    command: list[str],
    *,
    repo_root: Path = REPO_ROOT,
) -> CommandResult:
    """run_local_command executes a named local command and captures its result.

    Intent
    ------
    Run the provided argv in the requested repository root without raising on nonzero exit, then package the captured process output.

    Rationale
    ---------
    Mechanical checks must remain visible in certification output even when they fail, so command evidence is returned rather than thrown away.

    Pseudocode
    ----------
    - set completed = completed_subprocess_result
    - return %.CommandResult(completed)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CommandResult:
      why:
        constructs: "The returned command result carries argv, exit status, stdout, and stderr into check evidence."
    """
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return CommandResult(
        name=name,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_mechanical_checks(
    repo_root: Path = REPO_ROOT,
) -> CommandResult:
    """run_mechanical_checks runs the local validators required before certification.

    Intent
    ------
    Execute the configured command checks in the reviewed repository and return their captured results.

    Rationale
    ---------
    Certification should include the validator evidence that justified issuance, not only the final certificate paths.

    Pseudocode
    ----------
        - set results = executed_mechanical_checks
        - return command_evidence

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Unavailable local validators reject mechanical evidence collection for certification."
    .run_local_command:
      why:
        constructs: "Captured command results become the mechanical evidence returned to callers."
    """

    result = run_local_command(
        "validators",
        [sys.executable, "repo_checks.py", "--suite", "validators"],
        repo_root=repo_root,
    )
    if not result.passed:
        raise CertificationError(
            f"mechanical certification checks failed: {result.name}"
        )
    return result


def resolve_reviewed_repository_targets(
    graph: RepositoryBlueprintGraph,
    requests: Sequence[str],
) -> tuple[BlueprintNode, ...]:
    """resolve_reviewed_repository_targets maps user target names to reviewed graph nodes.

    Intent
    ------
    Resolve module names, source ids, and node ids against the reviewed blueprint graph while rejecting ambiguity.

    Rationale
    ---------
    The public CLI accepts user-facing target strings, but the private writer needs concrete behavioral-source nodes.

    Pseudocode
    ----------
        - set resolved = matching_reviewed_targets
        - return resolved

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Unknown or ambiguous target names reject public certification before signing."
    """

    module_nodes = tuple(
        sorted(
            (
                node
                for node in graph.nodes.values()
                if node.node_type == "module"
            ),
            key=lambda node: (node.node_id, node.module_root.as_posix()),
        )
    )
    if not module_nodes:
        raise CertificationError("reviewed repository contains no v4 modules")
    if not requests:
        return module_nodes

    resolved: list[BlueprintNode] = []
    seen: set[tuple[str, Path]] = set()
    for request in requests:
        path_like = (
            "/" in request
            or "\\" in request
            or request.startswith((".", "~"))
        )
        if path_like:
            candidate = Path(request).expanduser().resolve()
            matches = [
                node
                for node in module_nodes
                if node.module_root.resolve() == candidate
            ]
        else:
            matches = [
                node
                for node in module_nodes
                if node.node_id == request
                or (
                    graph.schema_version == 4
                    and node.module_root.name == request
                )
            ]
        if len(matches) != 1:
            raise CertificationError(
                f"target {request!r} resolves to {len(matches)} modules "
                "in the reviewed repository"
            )
        target = matches[0]
        identity = (target.node_id, target.module_root.resolve())
        if identity not in seen:
            seen.add(identity)
            resolved.append(target)
    return tuple(resolved)


def certify(
    *,
    targets: Sequence[str],
    timestamp: str | None = None,
    reviewed_repository: Path | None = None,
    reviewed_commit: str | None = None,
    allow_non_atomic: bool = False,
) -> tuple[list[CommandResult], list[CertificationOutcome]]:
    """certify runs the public reviewed-repository certification flow.

    Intent
    ------
    Require reviewed repository inputs, load the current graph, resolve requested modules, run mechanical checks, invoke the writer, and shape outcomes.

    Rationale
    ---------
    The dispatcher-facing API keeps argument validation and report construction outside the private signing routine while preserving commit-specific evidence.

    Pseudocode
    ----------
        - raise %.CertificationError(missing_or_invalid_reviewed_input)
        - set graph = reviewed_blueprint_graph
        - set resolved = concrete_certification_targets
        - set evidence = mechanical_check_results
        - set issued = private_writer_result
        - return certification_evidence_and_outcomes

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.certification.records.certificate_public_key_root:
      why:
        computes: "Supplies the public-key root passed into the private writer."
    officina.certification.view.certificate_log_path:
      why:
        computes: "Maps each certified node to the log path reported in public outcomes."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Missing reviewed inputs, incompatible graphs, private-writer failure, or incomplete issuance leave as typed API rejections."
    .CertificationOutcome:
      why:
        constructs: "Module-level outcomes are returned to CLI rendering or JSON output."
    .NodeCertificationOutcome:
      why:
        constructs: "Node-level certificate paths are carried inside returned module outcomes."
    ._certify_repository:
      why:
        constructs: "The private writer result determines which requested nodes were actually issued."
    .resolve_reviewed_repository_targets:
      why:
        transforms: "User target requests become concrete module nodes for expansion and reporting."
    .run_mechanical_checks:
      why:
        constructs: "Validator command evidence is returned alongside certification outcomes."
    officina.blueprints.graph.load_repository_blueprint_graph:
      why:
        constructs: "The reviewed current-schema graph drives target resolution and outcome path lookup."
    """

    if reviewed_repository is None or reviewed_commit is None:
        raise CertificationError(
            "certification requires the exact LLM-reviewed repository and commit"
        )
    repository = Path(reviewed_repository).resolve()

    graph = load_repository_blueprint_graph(
        repository,
        schema_root=repository / "references" / "blueprint",
        expected_schema_version=6,
    )
    if any(
        node.declaration.get("schema_version") != 6
        for node in graph.nodes.values()
    ):
        raise CertificationError("certification accepts only an all-v6 repository")

    resolved = resolve_reviewed_repository_targets(graph, targets)

    target_nodes_by_module: dict[str, tuple[str, ...]] = {}
    requested_node_ids: set[str] = set()
    for target in resolved:
        node_ids = tuple(
            sorted(
                node_id
                for node_id, node in graph.nodes.items()
                if node.module_root.resolve() == target.module_root.resolve()
            )
        )
        if not node_ids:
            raise CertificationError(f"{target.node_id}: module owns no certifiable nodes")
        target_nodes_by_module[target.node_id] = node_ids
        requested_node_ids.update(node_ids)

    evidence = [run_mechanical_checks(repository)]
    result = _certify_repository(
        repository,
        target_node_ids=tuple(sorted(requested_node_ids)),
        public_key_root=certificate_public_key_root(repository),
        secret_backend=None,
        reviewed_commit=reviewed_commit,
        certified_at=timestamp
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        allow_non_atomic=allow_non_atomic,
        require_candidate_execution=True,
        require_migration_review=False,
        expected_schema_version=6,
        schema_root=repository / "references" / "blueprint",
    )
    written = set(result.node_ids)
    missing = requested_node_ids - written
    if missing:
        raise CertificationError(
            "certifier did not issue every requested certificate: "
            + ", ".join(sorted(missing))
        )

    outcomes = [
        CertificationOutcome(
            module=target.node_id,
            source="reviewed-repository",
            module_root=target.module_root.resolve(),
            nodes=tuple(
                NodeCertificationOutcome(
                    node_id=node_id,
                    certificate_path=certificate_log_path(graph.nodes[node_id]),
                )
                for node_id in result.node_ids
                if node_id in target_nodes_by_module[target.node_id]
            ),
        )
        for target in resolved
    ]
    return evidence, outcomes


def render_text(outcomes: Sequence[CertificationOutcome]) -> str:
    """render_text formats certification outcomes for terminal output.

    Intent
    ------
    Convert module and node outcomes into stable human-readable lines.

    Rationale
    ---------
    The CLI text path should summarize certificate locations without changing the JSON payload contract.

    Pseudocode
    ----------
    - set lines = rendered_outcome_lines
    - return lines

    Wraps
    -----
    - none
    """
    lines = [
        "# Certificate Report",
        "",
        "| Source | Module | Nodes |",
        "|---|---|---|",
    ]
    for outcome in outcomes:
        lines.append(
            f"| {outcome.source} | {outcome.module} | "
            + ", ".join(node.node_id for node in outcome.nodes)
            + " |"
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    """build_parser constructs the skill-certifier CLI parser.

    Intent
    ------
    Declare reviewed-repository, target, timestamp, JSON, and atomicity options for the command entrypoint.

    Rationale
    ---------
    Parser construction is isolated so tests and the runtime interface share the same argument contract.

    Pseudocode
    ----------
    - set parser = configured_argument_parser
    - return parser

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(
        description="Issue signed certificates for blueprint-backed modules."
    )
    parser.add_argument("command", choices=["certify"])
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--allow-non-atomic", action="store_true")
    parser.add_argument("--timestamp", help=argparse.SUPPRESS)
    parser.add_argument("--reviewed-repository", type=Path, required=True)
    parser.add_argument("--reviewed-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """main coordinates CLI parsing, certification, rendering, and exit status.

    Intent
    ------
    Parse argv, call the public certification API, render JSON or text output, and translate certification failures into process status.

    Rationale
    ---------
    The entrypoint is the boundary where structured certification exceptions become user-facing command results.

    Pseudocode
    ----------
        - set args = parsed_cli_arguments
        - set outcomes = certification_api_result
        - return process_exit_status

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .build_parser:
      why:
        orchestrates: "The parser defines the CLI argument contract consumed by the entrypoint."
    .render_text:
      why:
        serializes: "render_text formats certification evidence without becoming the returned product of main."

    InstantiationsFromRepo
    ----------------------
    .certify:
      why:
        constructs: "The certification API result supplies evidence and outcomes for CLI rendering."
    """
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        evidence, outcomes = certify(
            targets=args.targets,
            timestamp=args.timestamp,
            reviewed_repository=args.reviewed_repository,
            reviewed_commit=args.reviewed_commit,
            allow_non_atomic=args.allow_non_atomic,
        )
    except CertificationError as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "error": str(exc)},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = {
        "ok": True,
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "evidence": [item.as_payload() for item in evidence],
        "certified": [outcome.as_payload() for outcome in outcomes],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_text(outcomes), end="")
    return 0


class Interface(PythonArgvMachineInterface):
    """Interface exposes the skill-certifier argv machine boundary.

    Intent
    ------
    Provide the dispatcher-visible runtime object that forwards argv into the module CLI entrypoint.

    Rationale
    ---------
    The class keeps the interface contract explicit while leaving command semantics in the module-level parser and main routine.

    Pseudocode
    ----------
    - set interface = argv_machine_boundary
    - return interface

    Wraps
    -----
    - none
    """

    def run(self, argv: list[str]) -> int:
        """run delegates dispatcher argv to the module entrypoint.

        Intent
        ------
        Forward the received argument vector to the CLI main function and return its process status unchanged.

        Rationale
        ---------
        This method is intentionally a thin runtime adapter; documenting the wrap edge makes the handoff visible in graphs.

        Pseudocode
        ----------
        - return @.main(argv)

        Wraps
        -----
        .main -> preprocess: receives dispatcher argv without translating command semantics; postprocess: returns the module entrypoint exit status unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
