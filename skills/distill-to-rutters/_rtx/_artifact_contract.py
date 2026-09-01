"""Deterministic artifact validation and routing for distill-to-rutters."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import jsonschema
import yaml

from ._runtime_compatibility import (
    RuntimeCompatibilityError,
    probe_runtime_compatibility,
)


SCHEMA_VERSION = "distill-to-rutters/v1"
STAGE_ORDER = (
    "breakdown",
    "assign-rutters",
    "extract-evolutions",
    "validate-logic",
    "design-implementation",
    "implement",
    "finalize",
    "verify",
)
STAGE_ARTIFACTS = {
    "breakdown": "01_breakdown.md",
    "assign-rutters": "02_rutter_assignment.md",
    "extract-evolutions": "03_evolutions_and_transitions.md",
    "validate-logic": "04_logic_validation.md",
    "design-implementation": "05_implementation_design.md",
    "implement": "06_implementation_report.md",
    "finalize": "07_entrypoint.md",
    "verify": "08_verification.md",
}
STAGE_CONTRACTS = {
    "breakdown": {
        "body_schema": "breakdown/v1",
        "schema_file": "breakdown-body.schema.json",
        "success": "breakdown-ready",
        "outcomes": {"breakdown-ready", "breakdown-gap", "partial", "failed"},
    },
    "assign-rutters": {
        "body_schema": "assignment/v1",
        "schema_file": "assignment-body.schema.json",
        "success": "assignment-ready",
        "outcomes": {"assignment-ready", "assignment-gap", "partial", "failed"},
    },
    "extract-evolutions": {
        "body_schema": "graph/v1",
        "schema_file": "graph-body.schema.json",
        "success": "graph-ready",
        "outcomes": {"graph-ready", "graph-gap", "partial", "failed"},
    },
    "validate-logic": {
        "body_schema": "logic-validation/v1",
        "schema_file": "logic-validation-body.schema.json",
        "success": "logic-captured",
        "outcomes": {"logic-captured", "logic-gap", "partial", "failed"},
    },
    "design-implementation": {
        "body_schema": "implementation-design/v1",
        "schema_file": "implementation-design-body.schema.json",
        "success": "design-ready",
        "outcomes": {
            "design-ready",
            "design-gap",
            "design-blocked",
            "partial",
            "failed",
        },
    },
    "implement": {
        "body_schema": "implementation-report/v1",
        "schema_file": "implementation-report-body.schema.json",
        "success": "implemented",
        "outcomes": {
            "implemented",
            "implementation-gap",
            "implementation-blocked",
            "partial",
            "failed",
        },
    },
    "finalize": {
        "body_schema": "entrypoint/v1",
        "schema_file": "entrypoint-body.schema.json",
        "success": "entrypoint-ready",
        "outcomes": {"entrypoint-ready", "entrypoint-gap", "partial", "failed"},
    },
    "verify": {
        "body_schema": "verification/v1",
        "schema_file": "verification-body.schema.json",
        "success": "verified",
        "outcomes": {
            "verified",
            "verification-failed",
            "verification-blocked",
            "partial",
        },
    },
}
_SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "references"
_ENVELOPE_RE = re.compile(
    r"\A```ya?ml[ \t]*\r?\n(?P<yaml>.*?)\r?\n```[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)
_BODY_RE = re.compile(
    r"^```distill-contract[ \t]*\r?\n(?P<yaml>.*?)\r?\n```[ \t]*\r?$",
    re.DOTALL | re.MULTILINE,
)


class ArtifactContractError(ValueError):
    """Raised when an artifact cannot be parsed safely."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ArtifactContractError("mapping keys must be scalar") from exc
        if duplicate:
            raise ArtifactContractError(f"duplicate key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


@dataclass(frozen=True)
class Prerequisite:
    kind: str
    path: str
    sha256: str
    stage: str | None = None
    schema_version: str | None = None


@dataclass(frozen=True)
class ArtifactEnvelope:
    schema_version: str
    stage: str
    outcome: str
    prerequisites: tuple[Prerequisite, ...]
    body_schema: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    envelope: ArtifactEnvelope | None
    errors: tuple[str, ...]


@dataclass(frozen=True)
class FreshnessResult:
    current: bool
    earliest_stale_prerequisite: str | None
    owning_stage: str | None
    error: str | None


@dataclass(frozen=True)
class RouteDecision:
    status: str
    artifact_digest: str | None
    outcome: str | None
    authorized_route: str | None
    earliest_stale_prerequisite: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "artifact_digest": self.artifact_digest,
            "outcome": self.outcome,
            "authorized_route": self.authorized_route,
            "earliest_stale_prerequisite": self.earliest_stale_prerequisite,
        }


class _SnapshotSession:
    """Read every consulted file once, then detect mutation before acceptance."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self._bytes: dict[Path, bytes] = {}
        self._owners: dict[Path, tuple[int, str, str]] = {}

    def _remember_owner(self, path: Path, stage: str, label: str) -> None:
        rank = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 0
        owner = (rank, label, stage)
        current = self._owners.get(path)
        if current is None or owner < current:
            self._owners[path] = owner

    def _read(self, resolved: Path, stage: str, label: str) -> bytes:
        self._remember_owner(resolved, stage, label)
        if resolved not in self._bytes:
            try:
                self._bytes[resolved] = resolved.read_bytes()
            except OSError as exc:
                raise ArtifactContractError(f"cannot read {label}: {exc}") from exc
        return self._bytes[resolved]

    def read_repository(self, path: Path, stage: str, label: str) -> bytes:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ArtifactContractError(f"cannot resolve {label}: {exc}") from exc
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactContractError(f"{label} escapes repository") from exc
        if not resolved.is_file():
            raise ArtifactContractError(f"{label} is not a regular file")
        return self._read(resolved, stage, label)

    def read_trusted(self, path: Path, stage: str, label: str) -> bytes:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ArtifactContractError(f"cannot resolve {label}: {exc}") from exc
        if not resolved.is_file():
            raise ArtifactContractError(f"{label} is not a regular file")
        return self._read(resolved, stage, label)

    def digest(self, path: Path) -> str:
        resolved = path.resolve(strict=True)
        return hashlib.sha256(self._bytes[resolved]).hexdigest()

    def final_changes(self) -> tuple[tuple[int, str, str], ...]:
        changed: list[tuple[int, str, str]] = []
        for path, original in self._bytes.items():
            try:
                current = path.read_bytes()
            except OSError:
                current = None
            if current != original:
                changed.append(self._owners[path])
        return tuple(sorted(changed))


def sha256_file(path: str | Path) -> str:
    """Return SHA-256 over the file's exact bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_yaml(text: str, *, label: str) -> Mapping[str, Any]:
    try:
        loaded = yaml.load(text, Loader=_UniqueKeyLoader)
    except ArtifactContractError:
        raise
    except yaml.YAMLError as exc:
        raise ArtifactContractError(f"malformed {label} YAML: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise ArtifactContractError(f"{label} must be a YAML mapping")
    return loaded


def _load_schema(
    name: str,
    session: _SnapshotSession | None = None,
    stage: str = "breakdown",
) -> Mapping[str, Any]:
    try:
        path = _SCHEMA_ROOT / name
        raw = (
            session.read_trusted(path, stage, f"schema {name}")
            if session is not None
            else path.read_bytes()
        )
        loaded = json.loads(raw.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactContractError(f"cannot load schema {name}: {exc}") from exc
    except UnicodeError as exc:
        raise ArtifactContractError(f"cannot load schema {name}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise ArtifactContractError(f"schema {name} must be an object")
    return loaded


def _load_yaml_file(
    path: Path,
    *,
    label: str,
    session: _SnapshotSession | None = None,
    stage: str = "breakdown",
) -> Mapping[str, Any]:
    try:
        raw = (
            session.read_repository(path, stage, label)
            if session is not None
            else path.read_bytes()
        )
        return _load_yaml(raw.decode("utf-8"), label=label)
    except (OSError, UnicodeError) as exc:
        raise ArtifactContractError(f"cannot load {label}: {exc}") from exc


def _resolve_module_blueprint(
    repository_root: Path,
    module_root: Path,
    relative_path: str,
) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ArtifactContractError(
            "module-root blueprint path must be relative without parent traversal"
        )
    resolved_repository = repository_root.resolve(strict=True)
    resolved_module = module_root.resolve(strict=True)
    try:
        resolved_module.relative_to(resolved_repository)
    except ValueError as exc:
        raise ArtifactContractError(
            "public Rutter module root escapes artifact repository"
        ) from exc
    try:
        resolved = (resolved_module / candidate).resolve(strict=True)
    except OSError as exc:
        raise ArtifactContractError(
            f"cannot resolve module-root blueprint path {relative_path}: {exc}"
        ) from exc
    try:
        resolved.relative_to(resolved_module)
    except ValueError as exc:
        raise ArtifactContractError(
            "module-root blueprint path escapes public Rutter module root"
        ) from exc
    if not resolved.is_file():
        raise ArtifactContractError(
            f"module-root blueprint path is not a file: {relative_path}"
        )
    return resolved


def _public_runtime_contract(
    repository_root: Path,
    capability: str,
    version: int,
    session: _SnapshotSession | None = None,
    stage: str = "validate-logic",
) -> tuple[frozenset[str], Mapping[str, Any] | None]:
    """Resolve one exported Rutter interface against this exact checkout."""

    resolved_repository = repository_root.resolve(strict=True)
    public_rutter_root = repository_root / "src/officina/rutter"
    try:
        root_blueprint_path = (public_rutter_root / "blueprint.yaml").resolve(
            strict=True
        )
    except OSError as exc:
        raise ArtifactContractError(
            f"cannot resolve public Rutter root blueprint: {exc}"
        ) from exc
    try:
        root_blueprint_path.relative_to(resolved_repository)
    except ValueError as exc:
        raise ArtifactContractError(
            "public Rutter root blueprint escapes artifact repository"
        ) from exc
    if not root_blueprint_path.is_file():
        raise ArtifactContractError(
            "public Rutter root blueprint is not a regular file"
        )
    root = _load_yaml_file(
        root_blueprint_path,
        label="public Rutter blueprint",
        session=session,
        stage=stage,
    )
    exports = root.get("exports")
    export = exports.get(capability) if isinstance(exports, Mapping) else None
    if not isinstance(export, Mapping):
        raise ArtifactContractError(
            f"{capability}@{version} is not a current public runtime capability"
        )
    source_interface = export.get("source_interface")
    if not isinstance(source_interface, str) or ".interface." not in source_interface:
        raise ArtifactContractError(
            f"{capability}@{version} is not a current public runtime capability"
        )
    source_id = source_interface.split(".interface.", 1)[0]
    sources = root.get("sources")
    source = sources.get(source_id) if isinstance(sources, Mapping) else None
    blueprint = source.get("blueprint") if isinstance(source, Mapping) else None
    if (
        not isinstance(blueprint, Mapping)
        or blueprint.get("base") != "module-root"
        or not isinstance(blueprint.get("path"), str)
    ):
        raise ArtifactContractError(
            f"{capability}@{version} is not a current public runtime capability"
        )
    source_blueprint_path = _resolve_module_blueprint(
        repository_root,
        public_rutter_root,
        str(blueprint["path"]),
    )
    source_blueprint = _load_yaml_file(
        source_blueprint_path,
        label=f"source blueprint for {capability}",
        session=session,
        stage=stage,
    )
    interfaces = source_blueprint.get("interfaces")
    interface = (
        interfaces.get(source_interface) if isinstance(interfaces, Mapping) else None
    )
    if not isinstance(interface, Mapping) or interface.get("version") != version:
        raise ArtifactContractError(
            f"{capability}@{version} is not a current public runtime capability"
        )
    contract = interface.get("contract")
    arguments = contract.get("arguments") if isinstance(contract, Mapping) else None
    operation = arguments.get("operation") if isinstance(arguments, Mapping) else None
    operation_type = operation.get("type") if isinstance(operation, Mapping) else None
    values = operation_type.get("values") if isinstance(operation_type, Mapping) else None
    operations = (
        frozenset(
            item["value"]
            for item in values
            if isinstance(item, Mapping) and isinstance(item.get("value"), str)
        )
        if isinstance(values, list)
        else frozenset()
    )
    semantic = (
        contract.get("semantic_enforcement")
        if isinstance(contract, Mapping)
        else None
    )
    return operations, semantic if isinstance(semantic, Mapping) else None


_COORDINATOR_RULE_GROUPS = (
    "starts",
    "dependencies",
    "joins",
    "aggregate_results",
    "partial_failure",
    "retries",
    "cancellation",
    "failure_propagation",
    "authorization",
    "release",
)


def _validate_coordinator_ownership(
    assignment_body: Mapping[str, Any],
    evolutions: Mapping[str, Mapping[str, Any]],
    transition_sources: set[str],
) -> None:
    orchestration = assignment_body.get("orchestration")
    if not isinstance(orchestration, Mapping) or orchestration.get("mode") != "coordinated":
        return
    coordinator = orchestration.get("coordinator_rutter_id")
    assert isinstance(coordinator, str)
    for group in _COORDINATOR_RULE_GROUPS:
        rules = orchestration.get(group, ())
        assert isinstance(rules, list)
        for rule in rules:
            assert isinstance(rule, Mapping)
            obligation_id = str(rule["obligation_id"])
            owner = f"{coordinator}/{rule['owning_transition']}"
            evolution = evolutions.get(owner)
            obligation_ids = (
                evolution.get("obligation_ids", ())
                if isinstance(evolution, Mapping)
                else ()
            )
            if (
                evolution is None
                or obligation_id not in obligation_ids
                or owner not in transition_sources
            ):
                raise ArtifactContractError(
                    f"assignment coordinator obligation {obligation_id} "
                    f"requires graph owner {owner}"
                )


def _public_binding_sections(
    capability: str,
    runtime_version: int,
    binding_version: int,
    semantic: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    required = {
        "request": {"operation", "owner_ref", "evidence_ref"},
        "validation": {
            "operation",
            "input_ref",
            "evidence_ref",
            "validator_binding_ref",
            "output_ref",
        },
        "transition": {
            "operation",
            "input_ref",
            "evidence_ref",
            "owning_evolution_ref",
            "authority_ref",
            "successor_ref",
        },
    }
    if semantic is None or semantic.get("version") != binding_version:
        raise ArtifactContractError(
            f"{capability}@{runtime_version} does not expose semantic enforcement "
            f"bindings at version {binding_version}"
        )
    sections: dict[str, Mapping[str, Any]] = {}
    for name, fields in required.items():
        section = semantic.get(name)
        if not isinstance(section, Mapping) or any(
            not isinstance(section.get(field), str) or not section.get(field)
            for field in fields
        ):
            raise ArtifactContractError(
                f"{capability}@{runtime_version} does not expose semantic "
                f"enforcement bindings at version {binding_version}"
            )
        sections[name] = section
    rejection_codes = sections["validation"].get("rejection_codes")
    if (
        not isinstance(rejection_codes, list)
        or not rejection_codes
        or any(not isinstance(code, str) or not code for code in rejection_codes)
        or len(rejection_codes) != len(set(rejection_codes))
    ):
        raise ArtifactContractError(
            f"{capability}@{runtime_version} does not expose semantic "
            f"enforcement bindings at version {binding_version}"
        )
    return sections["request"], sections["validation"], sections["transition"]


def _require_public_binding(
    artifact_section: Mapping[str, Any],
    public_section: Mapping[str, Any],
    field: str,
    label: str,
) -> None:
    claimed = artifact_section.get(field)
    exposed = public_section[field]
    if claimed != exposed:
        raise ArtifactContractError(
            f"{label} {field} {claimed} does not match public binding {exposed}"
        )


def _approved_prerequisite_body(
    root: Path,
    envelope: ArtifactEnvelope,
    *,
    stage: str,
    success_outcome: str,
    session: _SnapshotSession,
) -> tuple[ArtifactEnvelope, Mapping[str, Any]]:
    prerequisites = [
        prerequisite
        for prerequisite in envelope.prerequisites
        if prerequisite.kind == "artifact" and prerequisite.stage == stage
    ]
    if len(prerequisites) != 1:
        raise ArtifactContractError(
            f"logic validation requires exactly one {stage} prerequisite"
        )
    artifact_path = _resolve_prerequisite(root, prerequisites[0].path)
    raw = session.read_repository(
        artifact_path,
        stage,
        prerequisites[0].path,
    )
    if hashlib.sha256(raw).hexdigest() != prerequisites[0].sha256:
        raise ArtifactContractError(
            f"logic validation {stage} prerequisite digest is stale"
        )
    _, _, prerequisite_envelope, body = _basic_artifact(
        artifact_path,
        stage,
        session,
    )
    if prerequisite_envelope.outcome != success_outcome:
        raise ArtifactContractError(
            f"logic-captured requires a {success_outcome} prerequisite"
        )
    return prerequisite_envelope, body


def _logic_inputs(
    root: Path,
    envelope: ArtifactEnvelope,
    session: _SnapshotSession,
) -> tuple[Mapping[str, Any], Mapping[str, Any], set[str], list[str]]:
    graph_envelope, graph_body = _approved_prerequisite_body(
        root,
        envelope,
        stage="extract-evolutions",
        success_outcome="graph-ready",
        session=session,
    )
    assignment_envelope, assignment_body = _approved_prerequisite_body(
        root,
        graph_envelope,
        stage="assign-rutters",
        success_outcome="assignment-ready",
        session=session,
    )
    _, breakdown_body = _approved_prerequisite_body(
        root,
        assignment_envelope,
        stage="breakdown",
        success_outcome="breakdown-ready",
        session=session,
    )
    normative_ids = [
        item.get("obligation_id")
        for item in breakdown_body.get("context_closure", ())
        if isinstance(item, Mapping)
        and item.get("authority") == "normative"
        and isinstance(item.get("obligation_id"), str)
    ]
    duplicate_normative_ids = sorted(
        obligation_id
        for obligation_id in set(normative_ids)
        if normative_ids.count(obligation_id) > 1
    )
    return graph_body, assignment_body, set(normative_ids), duplicate_normative_ids


def _validate_logic_capture(
    root: Path,
    envelope: ArtifactEnvelope,
    body: Mapping[str, Any],
    session: _SnapshotSession,
) -> None:
    """Reject success claims not enforced by the current public Rutter API."""

    (
        graph_body,
        assignment_body,
        normative_ids,
        duplicate_normative_ids,
    ) = _logic_inputs(root, envelope, session)
    evolutions: dict[str, Mapping[str, Any]] = {}
    obligation_owners: dict[str, list[str]] = {}
    transition_sources: set[str] = set()
    for rutter in graph_body.get("rutters", ()):
        if not isinstance(rutter, Mapping):
            continue
        rutter_id = rutter.get("rutter_id")
        if not isinstance(rutter_id, str):
            continue
        for evolution in rutter.get("evolutions", ()):
            if not isinstance(evolution, Mapping):
                continue
            evolution_id = evolution.get("evolution_id")
            if not isinstance(evolution_id, str):
                continue
            owner = f"{rutter_id}/{evolution_id}"
            if owner in evolutions:
                raise ArtifactContractError(
                    f"duplicate owning evolution {owner} in graph"
                )
            evolutions[owner] = evolution
            for obligation_id in evolution.get("obligation_ids", ()):
                if isinstance(obligation_id, str):
                    obligation_owners.setdefault(obligation_id, []).append(owner)
        for transition in rutter.get("transitions", ()):
            if not isinstance(transition, Mapping):
                continue
            source = transition.get("from")
            if isinstance(source, str):
                transition_sources.add(f"{rutter_id}/{source}")

    _validate_coordinator_ownership(
        assignment_body,
        evolutions,
        transition_sources,
    )

    rows = body.get("enforcement_matrix", ())
    assert isinstance(rows, list)
    row_ids = [
        row.get("obligation_id")
        for row in rows
        if isinstance(row, Mapping)
        and isinstance(row.get("obligation_id"), str)
    ]
    graph_ids = set(obligation_owners)
    duplicate_ids = sorted(
        obligation_id
        for obligation_id in set(row_ids)
        if row_ids.count(obligation_id) > 1
    )
    if set(row_ids) != graph_ids or duplicate_ids or len(row_ids) != len(rows):
        missing = sorted(graph_ids - set(row_ids))
        extra = sorted(set(row_ids) - graph_ids)
        raise ArtifactContractError(
            "enforcement matrix must cover graph obligations exactly: "
            f"missing={missing}, extra={extra}, duplicates={duplicate_ids}"
        )
    if (
        set(row_ids) != normative_ids
        or duplicate_normative_ids
        or duplicate_ids
        or len(row_ids) != len(rows)
    ):
        missing = sorted(normative_ids - set(row_ids))
        extra = sorted(set(row_ids) - normative_ids)
        raise ArtifactContractError(
            "enforcement matrix must cover normative obligations exactly: "
            f"missing={missing}, extra={extra}, "
            f"matrix_duplicates={duplicate_ids}, "
            f"closure_duplicates={duplicate_normative_ids}"
        )
    ambiguous = sorted(
        obligation_id
        for obligation_id, owners in obligation_owners.items()
        if len(owners) != 1
    )
    if ambiguous:
        raise ArtifactContractError(
            f"graph obligations must have one owning evolution: {ambiguous}"
        )

    for row in rows:
        assert isinstance(row, Mapping)
        obligation_id = str(row["obligation_id"])
        if "capability_gap" in row:
            raise ArtifactContractError(
                f"logic-captured cannot include capability_gap for {obligation_id}"
            )
        owner_ref = str(row["owning_evolution"])
        evolution = evolutions.get(owner_ref)
        if evolution is None:
            raise ArtifactContractError(
                f"owning evolution {owner_ref} was not found in the graph"
            )
        if owner_ref != obligation_owners[obligation_id][0]:
            raise ArtifactContractError(
                f"obligation {obligation_id} is not owned by evolution {owner_ref}"
            )
        decision_owner = evolution.get("decision_owner")
        if row.get("original_decision_owner") != decision_owner:
            raise ArtifactContractError(
                f"obligation {obligation_id} changes original decision owner"
            )
        automation = row.get("automation_permission")
        if decision_owner == "rutter":
            if automation != "deterministic":
                raise ArtifactContractError(
                    f"Rutter-owned obligation {obligation_id} must use deterministic automation"
                )
        elif automation != "request-owner-decision":
            raise ArtifactContractError(
                f"{decision_owner}-owned obligation {obligation_id} must use "
                "request-owner-decision"
            )

        capability = str(row["public_runtime_capability"])
        version = int(row["public_runtime_version"])
        operations, semantic = _public_runtime_contract(
            root,
            capability,
            version,
            session,
        )
        if row.get("capability_verified") is not True:
            raise ArtifactContractError(
                "logic-captured requires capability_verified=true for every row"
            )

        mechanism = row.get("exact_mechanism")
        assert isinstance(mechanism, Mapping)
        enforcement_class = mechanism.get("enforcement_class")
        if enforcement_class != "rutter-state-transition":
            raise ArtifactContractError(
                f"enforcement class {enforcement_class} is not a public runtime mechanism"
            )
        request = mechanism.get("request")
        validation = mechanism.get("validation")
        transition = mechanism.get("transition")
        assert isinstance(validation, Mapping)
        assert isinstance(transition, Mapping)
        claimed_operations = [
            request.get("operation") if isinstance(request, Mapping) else None,
            validation.get("operation"),
            transition.get("operation"),
        ]
        for operation in claimed_operations:
            if isinstance(operation, str) and operation not in operations:
                raise ArtifactContractError(
                    f"operation {operation} is absent from {capability}@{version}"
                )

        validator_ref = validation.get("validator_ref")
        if validation.get("operation") != "validate" or not isinstance(
            validator_ref, str
        ):
            raise ArtifactContractError(
                f"obligation {obligation_id} requires validation operation and validator_ref"
            )
        if validator_ref != evolution.get("validator"):
            raise ArtifactContractError(
                f"validator_ref {validator_ref} does not match owning evolution "
                f"validator {evolution.get('validator')}"
            )
        if transition.get("operation") != "advance":
            raise ArtifactContractError(
                f"obligation {obligation_id} requires exclusive owning-evolution transition authority"
            )
        authority = transition.get("authority")
        if authority != "owning-rutter-evolution":
            raise ArtifactContractError(
                f"transition authority {authority} is not owning-rutter-evolution"
            )
        if decision_owner == "rutter":
            if request is not None:
                raise ArtifactContractError(
                    f"Rutter-owned obligation {obligation_id} cannot fabricate an owner request"
                )
        elif not isinstance(request, Mapping):
            raise ArtifactContractError(
                f"{decision_owner}-owned obligation {obligation_id} requires a Rutter request"
            )
        elif request.get("operation") != "get-status":
            raise ArtifactContractError(
                f"{decision_owner}-owned obligation {obligation_id} requires "
                "Rutter-requested owner evidence"
            )
        elif request.get("owner") != decision_owner:
            raise ArtifactContractError(
                f"request owner {request.get('owner')} does not match {decision_owner}"
            )

        public_request, public_validation, public_transition = (
            _public_binding_sections(
                capability,
                version,
                int(row["public_binding_contract_version"]),
                semantic,
            )
        )
        if isinstance(request, Mapping):
            for field in ("operation", "owner_ref", "evidence_ref"):
                _require_public_binding(request, public_request, field, "request")
        for field in (
            "operation",
            "input_ref",
            "evidence_ref",
            "validator_binding_ref",
            "output_ref",
        ):
            _require_public_binding(
                validation,
                public_validation,
                field,
                "validation",
            )
        for field in (
            "operation",
            "input_ref",
            "evidence_ref",
            "owning_evolution_ref",
            "authority_ref",
            "successor_ref",
        ):
            _require_public_binding(
                transition,
                public_transition,
                field,
                "transition",
            )

        observable = row["observable_evidence"]
        positive = row["positive_trace"]
        negative = row["negative_trace"]
        assert isinstance(observable, Mapping)
        assert isinstance(positive, Mapping)
        assert isinstance(negative, Mapping)
        for field in ("operation", "evidence_ref", "output_ref"):
            _require_public_binding(
                observable,
                public_validation,
                field,
                "observable",
            )
        for field in ("operation", "input_ref", "evidence_ref"):
            _require_public_binding(
                positive,
                public_transition,
                field,
                "positive trace",
            )
            _require_public_binding(
                negative,
                public_validation,
                field,
                "negative trace",
            )

        expected_positive = positive["expected"]
        expected_negative = negative["expected"]
        assert isinstance(expected_positive, Mapping)
        assert isinstance(expected_negative, Mapping)
        if expected_positive.get("kind") != "successor":
            raise ArtifactContractError("positive trace must expect a successor")
        if expected_negative.get("kind") != "rejection":
            raise ArtifactContractError("negative trace must expect rejection")
        positive_result_ref = expected_positive.get("result_ref")
        if positive_result_ref != public_transition["successor_ref"]:
            raise ArtifactContractError(
                f"positive trace result_ref {positive_result_ref} does not match "
                f"public binding {public_transition['successor_ref']}"
            )
        rejection_code = expected_negative.get("rejection_code")
        if rejection_code not in public_validation["rejection_codes"]:
            raise ArtifactContractError(
                f"negative trace rejection_code {rejection_code} is not declared "
                "by public validation binding"
            )
        owner_rutter_id, local_evolution_id = owner_ref.split("/", 1)
        approved_successors = {
            transition_row.get("outcome"): transition_row.get("to")
            for rutter in graph_body.get("rutters", ())
            if isinstance(rutter, Mapping)
            and rutter.get("rutter_id") == owner_rutter_id
            for transition_row in rutter.get("transitions", ())
            if isinstance(transition_row, Mapping)
            and transition_row.get("from") == local_evolution_id
        }
        outcome = positive.get("outcome")
        approved_successor = approved_successors.get(outcome)
        if not isinstance(approved_successor, str):
            raise ArtifactContractError(
                f"positive trace outcome {outcome} is not declared by owning evolution"
            )
        claimed_successor = expected_positive.get("state")
        if claimed_successor != approved_successor:
            raise ArtifactContractError(
                f"positive trace successor {claimed_successor} is not the approved "
                f"successor {approved_successor}"
            )


def _schema_errors(
    instance: Mapping[str, Any],
    schema_name: str,
    session: _SnapshotSession | None = None,
    stage: str = "breakdown",
) -> tuple[str, ...]:
    validator = jsonschema.Draft202012Validator(
        _load_schema(schema_name, session, stage)
    )
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    rendered: list[str] = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path)
        rendered.append(f"{location}: {error.message}" if location else error.message)
    return tuple(rendered)


def _repository_root(path: str | Path) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    start = lexical if lexical.is_dir() else lexical.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate.resolve()
    raise ArtifactContractError(f"path is not inside a repository: {lexical}")


def _resolve_inside_repository(path: str | Path) -> tuple[Path, Path]:
    root = _repository_root(path)
    resolved = Path(path).absolute().resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ArtifactContractError(f"path escapes repository: {path}") from exc
    if not resolved.is_file():
        raise ArtifactContractError(f"path is not a regular file: {path}")
    return root, resolved


def _resolve_prerequisite(root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or not relative_path or ".." in candidate.parts:
        raise ArtifactContractError(
            f"prerequisite path must be repository-relative: {relative_path}"
        )
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ArtifactContractError(
            f"prerequisite path escapes repository: {relative_path}"
        ) from exc
    return resolved


def _parse_document(
    path: str | Path,
    session: _SnapshotSession | None = None,
    stage: str = "breakdown",
) -> tuple[Path, Path, Mapping[str, Any], Mapping[str, Any]]:
    root, resolved = _resolve_inside_repository(path)
    active = session or _SnapshotSession(root)
    try:
        text = active.read_repository(
            resolved,
            stage,
            _relative(root, resolved),
        ).decode("utf-8")
    except UnicodeError as exc:
        raise ArtifactContractError(f"artifact is not UTF-8: {path}") from exc
    envelope_match = _ENVELOPE_RE.match(text)
    if envelope_match is None:
        raise ArtifactContractError("artifact must begin with one fenced YAML envelope")
    body_matches = list(_BODY_RE.finditer(text))
    if not body_matches:
        raise ArtifactContractError("artifact must contain one distill-contract block")
    if len(body_matches) != 1:
        raise ArtifactContractError("duplicate distill-contract blocks")
    envelope_data = _load_yaml(envelope_match.group("yaml"), label="envelope")
    body_data = _load_yaml(body_matches[0].group("yaml"), label="distill-contract")
    return root, resolved, envelope_data, body_data


def _envelope_from_mapping(
    data: Mapping[str, Any],
    session: _SnapshotSession | None = None,
    stage: str = "breakdown",
) -> ArtifactEnvelope:
    errors = _schema_errors(
        data,
        "artifact-envelope.schema.json",
        session,
        stage,
    )
    if errors:
        raise ArtifactContractError("; ".join(errors))
    prerequisites = tuple(
        Prerequisite(
            kind=item["kind"],
            path=item["path"],
            sha256=item["sha256"],
            stage=item.get("stage"),
            schema_version=item.get("schema_version"),
        )
        for item in data["prerequisites"]
    )
    return ArtifactEnvelope(
        schema_version=data["schema_version"],
        stage=data["stage"],
        outcome=data["outcome"],
        prerequisites=prerequisites,
        body_schema=data["body_schema"],
    )


def parse_envelope(path: str | Path) -> ArtifactEnvelope:
    """Parse and structurally validate the leading artifact envelope."""
    root, _, envelope_data, _ = _parse_document(path)
    return _envelope_from_mapping(envelope_data, _SnapshotSession(root))


def _workflow_paths(
    root: Path,
    artifact: Path,
    stage: str,
    envelope: ArtifactEnvelope,
) -> tuple[Path, Path, Path]:
    workspace = artifact.parent
    suffix = "_distillation"
    if not workspace.name.endswith(suffix) or workspace.name == suffix:
        raise ArtifactContractError(
            "artifact must use the exact <source-stem>_distillation source-stem workspace"
        )
    source_stem = workspace.name[: -len(suffix)]
    expected_source = (workspace.parent / f"{source_stem}.md").resolve(strict=False)
    expected_candidate = (
        workspace.parent / f"{source_stem}_distilled.md"
    ).resolve(strict=False)
    for expected, label in (
        (expected_source, "root source"),
        (expected_candidate, "deliverable"),
    ):
        try:
            expected.relative_to(root)
        except ValueError as exc:
            raise ArtifactContractError(f"workflow {label} escapes repository") from exc

    sources = [item for item in envelope.prerequisites if item.kind == "source"]
    artifacts = [item for item in envelope.prerequisites if item.kind == "artifact"]
    deliverables = [
        item for item in envelope.prerequisites if item.kind == "deliverable"
    ]
    if stage == "breakdown":
        if len(sources) != 1 or artifacts or deliverables:
            raise ArtifactContractError(
                "breakdown must name exactly one root source prerequisite"
            )
        source_path = _resolve_prerequisite(root, sources[0].path)
        if source_path != expected_source:
            raise ArtifactContractError(
                "breakdown root source does not match its source-stem workspace"
            )
    else:
        preceding_stage = STAGE_ORDER[_stage_rank(stage) - 1]
        predecessors = [item for item in artifacts if item.stage == preceding_stage]
        expected_predecessor = workspace / STAGE_ARTIFACTS[preceding_stage]
        if len(artifacts) != 1 or len(predecessors) != 1:
            raise ArtifactContractError(
                f"artifact must name exactly one immediate preceding stage {preceding_stage}"
            )
        if sources:
            raise ArtifactContractError(
                "post-breakdown artifacts must use the common root source chain"
            )
        predecessor_path = _resolve_prerequisite(root, predecessors[0].path)
        if predecessor_path != expected_predecessor:
            raise ArtifactContractError(
                "artifact rejects cross-run predecessor splicing"
            )

    if stage in {"finalize", "verify"}:
        if len(deliverables) != 1:
            raise ArtifactContractError(
                f"{stage} entrypoint predecessor candidate requires exactly one contained deliverable leaf"
            )
        if _resolve_prerequisite(root, deliverables[0].path) != expected_candidate:
            raise ArtifactContractError(
                f"{stage} deliverable leaf does not match the source-stem candidate"
            )
    elif deliverables:
        raise ArtifactContractError(
            "deliverable leaves are allowed only for finalize and verify"
        )
    return workspace, expected_source, expected_candidate


def _basic_artifact(
    path: str | Path,
    expected_stage: str,
    session: _SnapshotSession,
) -> tuple[Path, Path, ArtifactEnvelope, Mapping[str, Any]]:
    root, resolved_artifact, envelope_data, body_data = _parse_document(
        path,
        session,
        expected_stage,
    )
    if root != session.root:
        raise ArtifactContractError("artifact chain crosses repository roots")
    if expected_stage not in STAGE_CONTRACTS:
        raise ArtifactContractError(f"unknown expected stage: {expected_stage}")
    envelope = _envelope_from_mapping(envelope_data, session, expected_stage)
    expected_name = STAGE_ARTIFACTS[expected_stage]
    if resolved_artifact.name != expected_name:
        raise ArtifactContractError(
            f"stage {expected_stage} requires artifact basename {expected_name}, "
            f"found {resolved_artifact.name}"
        )
    if envelope.stage != expected_stage:
        raise ArtifactContractError(
            f"expected stage {expected_stage}, found {envelope.stage}"
        )
    contract = STAGE_CONTRACTS[expected_stage]
    if envelope.outcome not in contract["outcomes"]:
        raise ArtifactContractError(
            f"unknown outcome {envelope.outcome} for stage {expected_stage}"
        )
    if envelope.body_schema != contract["body_schema"]:
        raise ArtifactContractError(
            f"body schema {envelope.body_schema} does not match stage {expected_stage}"
        )
    body_errors = _schema_errors(
        body_data,
        str(contract["schema_file"]),
        session,
        expected_stage,
    )
    if body_errors:
        raise ArtifactContractError("; ".join(body_errors))
    seen_paths: set[Path] = set()
    for prerequisite in envelope.prerequisites:
        target = _resolve_prerequisite(root, prerequisite.path)
        if target in seen_paths:
            raise ArtifactContractError(
                f"duplicate prerequisite path: {prerequisite.path}"
            )
        seen_paths.add(target)
        if prerequisite.kind == "artifact":
            if prerequisite.stage not in STAGE_CONTRACTS:
                raise ArtifactContractError(
                    f"unknown prerequisite stage: {prerequisite.stage}"
                )
            if prerequisite.schema_version != SCHEMA_VERSION:
                raise ArtifactContractError(
                    "artifact prerequisite schema_version does not match"
                )
            assert prerequisite.stage is not None
            if _stage_rank(prerequisite.stage) >= _stage_rank(expected_stage):
                raise ArtifactContractError(
                    "artifact prerequisite must name an earlier stage: "
                    f"{prerequisite.path}"
                )
    _workflow_paths(root, resolved_artifact, expected_stage, envelope)
    return root, resolved_artifact, envelope, body_data


def _predecessor_body(
    root: Path,
    envelope: ArtifactEnvelope,
    stage: str,
    session: _SnapshotSession,
) -> tuple[Path, ArtifactEnvelope, Mapping[str, Any]]:
    matches = [
        item
        for item in envelope.prerequisites
        if item.kind == "artifact" and item.stage == stage
    ]
    if len(matches) != 1:
        raise ArtifactContractError(f"requires exactly one {stage} predecessor")
    predecessor = matches[0]
    target = _resolve_prerequisite(root, predecessor.path)
    raw = session.read_repository(target, stage, predecessor.path)
    if hashlib.sha256(raw).hexdigest() != predecessor.sha256:
        raise ArtifactContractError(f"{stage} predecessor digest is stale")
    _, _, predecessor_envelope, predecessor_body = _basic_artifact(
        target,
        stage,
        session,
    )
    return target, predecessor_envelope, predecessor_body


def _validate_breakdown(
    root: Path,
    envelope: ArtifactEnvelope,
    body: Mapping[str, Any],
    session: _SnapshotSession,
) -> None:
    rows = body["context_closure"]
    assert isinstance(rows, list)
    by_path: dict[str, Mapping[str, Any]] = {}
    obligation_ids: list[str] = []
    for row in rows:
        assert isinstance(row, Mapping)
        path = str(row["path"])
        if path in by_path:
            raise ArtifactContractError(f"duplicate context path: {path}")
        by_path[path] = row
        obligation_ids.append(str(row["obligation_id"]))
        target = _resolve_prerequisite(root, path)
        if row["availability"] == "present":
            raw = session.read_repository(target, "breakdown", path)
            if hashlib.sha256(raw).hexdigest() != row["digest"]:
                raise ArtifactContractError(f"context digest is stale: {path}")
    if len(obligation_ids) != len(set(obligation_ids)):
        raise ArtifactContractError("breakdown context obligation IDs must be unique")
    for row in rows:
        assert isinstance(row, Mapping)
        if row.get("provenance") != "generated projection" or row.get(
            "authority"
        ) != "normative":
            continue
        governing_path = row.get("governing_source")
        governing = by_path.get(str(governing_path))
        if not (
            isinstance(governing, Mapping)
            and governing.get("provenance") == "source"
            and governing.get("authority") == "normative"
            and governing.get("availability") == "present"
            and governing.get("resolution") == "resolved"
        ):
            raise ArtifactContractError(
                "generated normative governing source must be included as a resolved source context row"
            )
    if envelope.outcome == "breakdown-ready":
        incomplete = [
            str(row["path"])
            for row in rows
            if row.get("availability") != "present"
            or row.get("resolution") != "resolved"
        ]
        if incomplete or body["conflicts"]:
            raise ArtifactContractError(
                "breakdown-ready forbids missing, unreadable, unresolved, conflict rows, and conflicts"
            )
        normative = {
            str(row["obligation_id"])
            for row in rows
            if row.get("authority") == "normative"
        }
        part_ids = [
            str(part["part_id"])
            for part in body["parts"]
            if isinstance(part, Mapping)
        ]
        covered = [
            str(obligation_id)
            for part in body["parts"]
            if isinstance(part, Mapping)
            for obligation_id in part["obligation_ids"]
        ]
        if (
            len(part_ids) != len(set(part_ids))
            or set(covered) != normative
            or len(covered) != len(normative)
        ):
            raise ArtifactContractError(
                "breakdown-ready parts must uniquely cover normative obligations"
            )


def _validate_assignment(
    root: Path,
    envelope: ArtifactEnvelope,
    body: Mapping[str, Any],
    session: _SnapshotSession,
) -> None:
    if envelope.outcome != "assignment-ready":
        return
    _, predecessor, breakdown = _predecessor_body(
        root,
        envelope,
        "breakdown",
        session,
    )
    if predecessor.outcome != "breakdown-ready":
        raise ArtifactContractError(
            "assignment-ready requires a breakdown-ready predecessor"
        )
    part_rows = {
        str(part["part_id"]): part
        for part in breakdown["parts"]
        if isinstance(part, Mapping)
    }
    assignment_ids = [
        str(row["part_id"])
        for row in body["assignments"]
        if isinstance(row, Mapping)
    ]
    if (
        len(part_rows) != len(breakdown["parts"])
        or len(assignment_ids) != len(set(assignment_ids))
        or set(assignment_ids) != set(part_rows)
    ):
        raise ArtifactContractError(
            "assignment-ready assignments must equal breakdown parts exactly once"
        )
    voyage_ids: list[str] = []
    for row in body["assignments"]:
        assert isinstance(row, Mapping)
        voyage_ids.append(str(row["voyage_id"]))
        part = part_rows[str(row["part_id"])]
        if row["inseparability"]["status"] != part["independence"]:
            raise ArtifactContractError(
                "assignment-ready inseparability must match the breakdown part"
            )
    if len(voyage_ids) != len(set(voyage_ids)):
        raise ArtifactContractError("assignment-ready Voyage IDs must be unique")


def _graph_index(
    body: Mapping[str, Any],
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    set[str],
]:
    rutters: dict[str, Mapping[str, Any]] = {}
    evolutions: dict[str, Mapping[str, Any]] = {}
    transition_sources: set[str] = set()
    for rutter in body["rutters"]:
        assert isinstance(rutter, Mapping)
        rutter_id = str(rutter["rutter_id"])
        if rutter_id in rutters:
            raise ArtifactContractError(
                f"graph successor contract has duplicate Rutter {rutter_id}"
            )
        rutters[rutter_id] = rutter
        local: dict[str, Mapping[str, Any]] = {}
        for evolution in rutter["evolutions"]:
            assert isinstance(evolution, Mapping)
            evolution_id = str(evolution["evolution_id"])
            if evolution_id in local:
                raise ArtifactContractError(
                    f"graph successor contract has duplicate evolution {rutter_id}/{evolution_id}"
                )
            local[evolution_id] = evolution
            evolutions[f"{rutter_id}/{evolution_id}"] = evolution
        if rutter["initial_evolution"] not in local:
            raise ArtifactContractError(
                f"graph successor initial evolution is invalid for {rutter_id}"
            )
        terminals = set(rutter["terminal_results"])
        indexed: dict[tuple[str, str], list[str]] = {}
        for transition in rutter["transitions"]:
            assert isinstance(transition, Mapping)
            source = str(transition["from"])
            outcome = str(transition["outcome"])
            target = str(transition["to"])
            if source not in local:
                raise ArtifactContractError(
                    f"graph successor source {rutter_id}/{source} is not an evolution"
                )
            if target not in local and target not in terminals:
                raise ArtifactContractError(
                    f"graph successor target {rutter_id}/{target} is invalid"
                )
            indexed.setdefault((source, outcome), []).append(target)
            transition_sources.add(f"{rutter_id}/{source}")
        declared = {
            (evolution_id, str(outcome))
            for evolution_id, evolution in local.items()
            for outcome in evolution["outcomes"]
        }
        if set(indexed) != declared or any(len(targets) != 1 for targets in indexed.values()):
            raise ArtifactContractError(
                f"graph successor contract requires exactly one successor for every declared outcome in {rutter_id}"
            )
    return rutters, evolutions, transition_sources


def _validate_graph(
    root: Path,
    envelope: ArtifactEnvelope,
    body: Mapping[str, Any],
    session: _SnapshotSession,
) -> None:
    if envelope.outcome != "graph-ready":
        return
    rutters, evolutions, transition_sources = _graph_index(body)
    _, assignment_envelope, assignment = _predecessor_body(
        root,
        envelope,
        "assign-rutters",
        session,
    )
    if assignment_envelope.outcome != "assignment-ready":
        raise ArtifactContractError(
            "graph-ready requires an assignment-ready predecessor"
        )
    _, breakdown_envelope, breakdown = _predecessor_body(
        root,
        assignment_envelope,
        "breakdown",
        session,
    )
    if breakdown_envelope.outcome != "breakdown-ready":
        raise ArtifactContractError(
            "assignment graph foreign-key closure requires breakdown-ready"
        )
    parts = {
        str(part["part_id"]): part
        for part in breakdown["parts"]
        if isinstance(part, Mapping)
    }
    assignments = [
        row for row in assignment["assignments"] if isinstance(row, Mapping)
    ]
    orchestration = assignment["orchestration"]
    assert isinstance(orchestration, Mapping)
    expected_rutters = {str(row["rutter_definition_id"]) for row in assignments}
    coordinator = orchestration.get("coordinator_rutter_id")
    if isinstance(coordinator, str):
        expected_rutters.add(coordinator)
    if set(rutters) != expected_rutters:
        raise ArtifactContractError(
            "assignment graph foreign-key closure has mismatched Rutter IDs"
        )
    expected_voyages: dict[str, set[str]] = {rutter_id: set() for rutter_id in rutters}
    expected_obligations: dict[str, set[str]] = {
        rutter_id: set() for rutter_id in rutters
    }
    known_voyages: set[str] = set()
    for row in assignments:
        rutter_id = str(row["rutter_definition_id"])
        voyage_id = str(row["voyage_id"])
        known_voyages.add(voyage_id)
        expected_voyages[rutter_id].add(voyage_id)
        part = parts.get(str(row["part_id"]))
        if not isinstance(part, Mapping):
            raise ArtifactContractError(
                "assignment graph foreign-key closure references an unknown part"
            )
        expected_obligations[rutter_id].update(str(item) for item in part["obligation_ids"])
        for workflow in row["independent_workflows"]:
            assert isinstance(workflow, Mapping)
            if workflow["voyage_id"] not in known_voyages and not any(
                other["voyage_id"] == workflow["voyage_id"] for other in assignments
            ):
                raise ArtifactContractError(
                    "assignment graph foreign-key closure references an unknown Voyage workflow"
                )
            if str(workflow["join_transition"]) not in transition_sources:
                raise ArtifactContractError(
                    "assignment graph foreign-key closure references an unknown workflow join transition"
                )
    if isinstance(coordinator, str):
        for group in _COORDINATOR_RULE_GROUPS:
            for rule in orchestration[group]:
                assert isinstance(rule, Mapping)
                expected_obligations[coordinator].add(str(rule["obligation_id"]))
    for rutter_id, rutter in rutters.items():
        if set(rutter["voyage_ids"]) != expected_voyages[rutter_id]:
            raise ArtifactContractError(
                "assignment graph foreign-key closure has mismatched Voyage IDs"
            )
        obligation_rows = [
            str(obligation_id)
            for evolution in rutter["evolutions"]
            if isinstance(evolution, Mapping)
            for obligation_id in evolution["obligation_ids"]
        ]
        actual_obligations = set(obligation_rows)
        if (
            actual_obligations != expected_obligations[rutter_id]
            or len(obligation_rows) != len(actual_obligations)
        ):
            raise ArtifactContractError(
                "assignment graph foreign-key closure has mismatched obligation ownership"
            )
    _validate_coordinator_ownership(assignment, evolutions, transition_sources)


def _validate_design(
    root: Path,
    envelope: ArtifactEnvelope,
    body: Mapping[str, Any],
    session: _SnapshotSession,
) -> None:
    if envelope.outcome != "design-ready":
        return
    if any(row.get("status") != "available" for row in body["public_interface_design"]):
        raise ArtifactContractError(
            "design-ready requires every public interface design row to be available"
        )

    def runtime_reader(path: Path) -> bytes:
        try:
            label = path.resolve(strict=False).relative_to(root).as_posix()
        except ValueError:
            label = str(path)
        return session.read_repository(path, "design-implementation", label)

    try:
        with tempfile.TemporaryDirectory(prefix="distill-runtime-probe-") as scratch:
            result = probe_runtime_compatibility(
                root,
                Path(scratch),
                runtime_reader,
            )
    except (RuntimeCompatibilityError, OSError) as exc:
        raise ArtifactContractError(
            f"design-ready requires the live production runtime compatibility probe: {exc}"
        ) from exc
    if result["outcome"] != "design-ready":
        raise ArtifactContractError(
            "design-ready requires the live production runtime compatibility probe to return design-ready"
        )


def _validate_implemented(envelope: ArtifactEnvelope, body: Mapping[str, Any]) -> None:
    if envelope.outcome == "implemented" and any(
        row.get("status") != "implemented" for row in body["implementation_trace_map"]
    ):
        raise ArtifactContractError(
            "implemented requires every implementation trace row to be implemented"
        )


def _deliverable_leaf(envelope: ArtifactEnvelope) -> Prerequisite:
    leaves = [item for item in envelope.prerequisites if item.kind == "deliverable"]
    if len(leaves) != 1:
        raise ArtifactContractError("success requires exactly one contained deliverable leaf")
    return leaves[0]


def _validate_finalize(
    root: Path,
    envelope: ArtifactEnvelope,
    body: Mapping[str, Any],
    session: _SnapshotSession,
) -> None:
    if envelope.outcome != "entrypoint-ready":
        return
    leaf = _deliverable_leaf(envelope)
    binding = body["entrypoint_binding"]
    assert isinstance(binding, Mapping)
    if (
        binding["candidate_path"] != leaf.path
        or binding["candidate_sha256"] != leaf.sha256
    ):
        raise ArtifactContractError(
            "entrypoint-ready body must equal its contained deliverable leaf"
        )
    raw = session.read_repository(
        _resolve_prerequisite(root, leaf.path),
        "finalize",
        leaf.path,
    )
    if hashlib.sha256(raw).hexdigest() != leaf.sha256:
        raise ArtifactContractError("entrypoint-ready deliverable leaf digest is stale")
    if binding["source_outcome"] != "implemented" or binding[
        "gateway_interpretation"
    ] != "accepted":
        raise ArtifactContractError(
            "entrypoint-ready requires an implemented accepted source outcome"
        )


def _validate_verify(
    root: Path,
    envelope: ArtifactEnvelope,
    body: Mapping[str, Any],
    session: _SnapshotSession,
) -> None:
    if envelope.outcome != "verified":
        return
    leaf = _deliverable_leaf(envelope)
    _, entrypoint_envelope, entrypoint = _predecessor_body(
        root,
        envelope,
        "finalize",
        session,
    )
    entrypoint_binding = entrypoint["entrypoint_binding"]
    assert isinstance(entrypoint_binding, Mapping)
    if entrypoint_envelope.outcome != "entrypoint-ready" or any(
        (
            body["candidate_path"] != entrypoint_binding["candidate_path"],
            body["candidate_sha256"] != entrypoint_binding["candidate_sha256"],
            body["candidate_path"] != leaf.path,
            body["candidate_sha256"] != leaf.sha256,
        )
    ):
        raise ArtifactContractError(
            "verified candidate must equal the approved entrypoint predecessor and contained deliverable leaf"
        )
    raw = session.read_repository(
        _resolve_prerequisite(root, leaf.path),
        "finalize",
        leaf.path,
    )
    if hashlib.sha256(raw).hexdigest() != leaf.sha256:
        raise ArtifactContractError("verified candidate digest is stale")
    if any(row.get("result") != "passed" for row in body["verification_evidence"]):
        raise ArtifactContractError(
            "verified requires all verification checks to have passed"
        )


def _validate_artifact_internal(
    path: str | Path,
    expected_stage: str,
    session: _SnapshotSession,
    active: set[Path] | None = None,
) -> tuple[ArtifactEnvelope, Mapping[str, Any]]:
    root, resolved, envelope, body = _basic_artifact(path, expected_stage, session)
    stack = active if active is not None else set()
    if resolved in stack:
        raise ArtifactContractError(
            f"artifact prerequisite cycle at {_relative(root, resolved)}"
        )
    stack.add(resolved)
    try:
        if expected_stage == "breakdown":
            _validate_breakdown(root, envelope, body, session)
        elif expected_stage == "assign-rutters":
            _validate_assignment(root, envelope, body, session)
        elif expected_stage == "extract-evolutions":
            _validate_graph(root, envelope, body, session)
        elif expected_stage == "validate-logic" and envelope.outcome == "logic-captured":
            _validate_logic_capture(root, envelope, body, session)
        elif expected_stage == "design-implementation":
            _validate_design(root, envelope, body, session)
        elif expected_stage == "implement":
            _validate_implemented(envelope, body)
        elif expected_stage == "finalize":
            _validate_finalize(root, envelope, body, session)
        elif expected_stage == "verify":
            _validate_verify(root, envelope, body, session)

        if envelope.outcome == STAGE_CONTRACTS[expected_stage]["success"] and _stage_rank(
            expected_stage
        ) > 0:
            preceding_stage = STAGE_ORDER[_stage_rank(expected_stage) - 1]
            predecessor_path, predecessor_envelope, _ = _predecessor_body(
                root,
                envelope,
                preceding_stage,
                session,
            )
            if predecessor_envelope.outcome != STAGE_CONTRACTS[preceding_stage][
                "success"
            ]:
                raise ArtifactContractError(
                    f"{envelope.outcome} requires a {STAGE_CONTRACTS[preceding_stage]['success']} predecessor"
                )
            _validate_artifact_internal(
                predecessor_path,
                preceding_stage,
                session,
                stack,
            )
        return envelope, body
    finally:
        stack.remove(resolved)


def validate_artifact(path: str | Path, expected_stage: str) -> ValidationResult:
    """Validate identity, typed success semantics, and cross-stage foreign keys."""
    try:
        root, _ = _resolve_inside_repository(path)
        session = _SnapshotSession(root)
        envelope, _ = _validate_artifact_internal(path, expected_stage, session)
        changes = session.final_changes()
        if changes:
            _, changed_path, _ = changes[0]
            raise ArtifactContractError(
                f"consulted path changed during validation: {changed_path}"
            )
        return ValidationResult(True, envelope, ())
    except (ArtifactContractError, OSError) as exc:
        return ValidationResult(False, None, (str(exc),))


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _stage_rank(stage: str) -> int:
    return STAGE_ORDER.index(stage)


def _freshness_with_session(
    path: str | Path,
    session: _SnapshotSession,
) -> FreshnessResult:
    root, resolved = _resolve_inside_repository(path)
    stale: list[tuple[int, str, str]] = []
    states: dict[Path, int] = {}

    def note_stale(path_label: str, owner: str) -> None:
        stale.append((_stage_rank(owner), path_label, owner))

    def snapshot_prerequisite(
        target: Path,
        prerequisite: Prerequisite,
        owner: str,
    ) -> bytes | None:
        try:
            raw = session.read_repository(target, owner, prerequisite.path)
        except ArtifactContractError as exc:
            if "escapes repository" in str(exc):
                raise
            note_stale(prerequisite.path, owner)
            return None
        if hashlib.sha256(raw).hexdigest() != prerequisite.sha256:
            note_stale(prerequisite.path, owner)
            return None
        return raw

    def walk(artifact_path: Path, expected_stage: str | None = None) -> None:
        state = states.get(artifact_path, 0)
        if state == 1:
            raise ArtifactContractError(
                f"artifact prerequisite cycle at {_relative(root, artifact_path)}"
            )
        if state == 2:
            return
        states[artifact_path] = 1
        try:
            if expected_stage is None:
                _, _, envelope_data, _ = _parse_document(
                    artifact_path,
                    session,
                )
                discovered = _envelope_from_mapping(envelope_data, session)
                stage = discovered.stage
            else:
                stage = expected_stage
            _, _, envelope, body = _basic_artifact(
                artifact_path,
                stage,
                session,
            )
            for prerequisite in envelope.prerequisites:
                target = _resolve_prerequisite(root, prerequisite.path)
                owner = (
                    prerequisite.stage
                    if prerequisite.kind == "artifact"
                    else "finalize"
                    if prerequisite.kind == "deliverable"
                    else "breakdown"
                )
                assert owner is not None
                raw = snapshot_prerequisite(target, prerequisite, owner)
                if raw is None:
                    continue
                if prerequisite.kind == "artifact":
                    assert prerequisite.stage is not None
                    walk(target, prerequisite.stage)
            if envelope.stage == "breakdown":
                for row in body["context_closure"]:
                    assert isinstance(row, Mapping)
                    if row["availability"] != "present":
                        continue
                    context_path = str(row["path"])
                    target = _resolve_prerequisite(root, context_path)
                    try:
                        raw = session.read_repository(
                            target,
                            "breakdown",
                            context_path,
                        )
                    except ArtifactContractError as exc:
                        if "escapes repository" in str(exc):
                            raise
                        note_stale(context_path, "breakdown")
                        continue
                    if hashlib.sha256(raw).hexdigest() != row["digest"]:
                        note_stale(context_path, "breakdown")
        finally:
            states[artifact_path] = 2

    try:
        walk(resolved)
        if stale:
            _, earliest_path, owner = min(stale, key=lambda item: (item[0], item[1]))
            return FreshnessResult(False, earliest_path, owner, None)
        return FreshnessResult(True, None, None, None)
    except (ArtifactContractError, OSError) as exc:
        return FreshnessResult(False, None, None, str(exc))


def check_freshness(path: str | Path) -> FreshnessResult:
    """Snapshot recursive explicit and implicit dependencies and compare raw digests."""
    try:
        root, _ = _resolve_inside_repository(path)
        session = _SnapshotSession(root)
        result = _freshness_with_session(path, session)
        if not result.current or result.error is not None:
            return result
        changes = session.final_changes()
        if changes:
            _, changed_path, changed_owner = changes[0]
            return FreshnessResult(
                False,
                changed_path,
                changed_owner,
                None,
            )
        return result
    except (ArtifactContractError, OSError) as exc:
        return FreshnessResult(False, None, None, str(exc))


def _route_status(stage: str, outcome: str) -> tuple[str, str | None]:
    contract = STAGE_CONTRACTS[stage]
    if outcome == contract["success"]:
        index = _stage_rank(stage)
        return "accepted", STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None
    if outcome.endswith("-gap"):
        return "gap", stage
    if outcome == "partial":
        return "partial", None
    if outcome.endswith("-blocked"):
        return "blocked", None
    return "failed", None


def decide_route(
    stage: str,
    outcome: str | None,
    approval_digest: str,
    user_decision: str,
    artifact_path: str | Path,
) -> RouteDecision:
    """Authorize a route from one immutable invocation-wide file snapshot."""
    if user_decision not in {"approve", "reject"}:
        return RouteDecision("failed", None, outcome or None, None, None)
    try:
        root, resolved = _resolve_inside_repository(artifact_path)
        session = _SnapshotSession(root)
        owner = stage if stage in STAGE_CONTRACTS else "breakdown"
        raw = session.read_repository(resolved, owner, _relative(root, resolved))
        digest = hashlib.sha256(raw).hexdigest()
    except (ArtifactContractError, OSError):
        return RouteDecision("failed", None, outcome or None, None, None)

    if stage == "source-preflight":
        relative = _relative(root, resolved)
        if resolved.suffix.lower() != ".md" or outcome != "source-ready":
            return RouteDecision("failed", digest, outcome or None, None, None)
        if approval_digest and approval_digest != digest:
            return RouteDecision("stale", digest, outcome, "breakdown", relative)
        if user_decision == "reject":
            return RouteDecision("rejected", digest, outcome, None, None)
        changes = session.final_changes()
        if changes:
            _, changed_path, changed_owner = changes[0]
            return RouteDecision(
                "stale",
                digest,
                outcome,
                changed_owner,
                changed_path,
            )
        return RouteDecision("accepted", digest, outcome, "breakdown", None)

    if stage not in STAGE_CONTRACTS:
        return RouteDecision("failed", digest, outcome or None, None, None)
    if approval_digest != digest:
        return RouteDecision(
            "stale",
            digest,
            outcome or None,
            stage,
            _relative(root, resolved),
        )

    freshness = _freshness_with_session(resolved, session)
    if freshness.error is not None:
        return RouteDecision("failed", digest, outcome, None, None)
    if not freshness.current:
        assert freshness.earliest_stale_prerequisite is not None
        assert freshness.owning_stage is not None
        return RouteDecision(
            "stale",
            digest,
            outcome or None,
            freshness.owning_stage,
            freshness.earliest_stale_prerequisite,
        )

    try:
        envelope, _ = _validate_artifact_internal(resolved, stage, session)
    except (ArtifactContractError, OSError):
        return RouteDecision("failed", digest, outcome or None, None, None)
    actual_outcome = envelope.outcome
    if outcome and outcome != actual_outcome:
        return RouteDecision("failed", digest, actual_outcome, None, None)
    if user_decision == "reject":
        return RouteDecision("rejected", digest, actual_outcome, stage, None)

    status, authorized_route = _route_status(stage, actual_outcome)
    if status == "accepted":
        changes = session.final_changes()
        if changes:
            _, changed_path, changed_owner = changes[0]
            return RouteDecision(
                "stale",
                digest,
                actual_outcome,
                changed_owner,
                changed_path,
            )
    return RouteDecision(status, digest, actual_outcome, authorized_route, None)
