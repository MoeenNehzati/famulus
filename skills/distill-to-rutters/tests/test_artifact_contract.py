from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "_distill_to_rutters_rtx"
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
STAGE_CASES = {
    "breakdown": ("breakdown-ready", "breakdown/v1", "context_closure"),
    "assign-rutters": ("assignment-ready", "assignment/v1", "assignments"),
    "extract-evolutions": ("graph-ready", "graph/v1", "rutters"),
    "validate-logic": (
        "logic-captured",
        "logic-validation/v1",
        "enforcement_matrix",
    ),
    "design-implementation": (
        "design-ready",
        "implementation-design/v1",
        "public_interface_design",
    ),
    "implement": (
        "implemented",
        "implementation-report/v1",
        "implementation_trace_map",
    ),
    "finalize": ("entrypoint-ready", "entrypoint/v1", "entrypoint_binding"),
    "verify": ("verified", "verification/v1", "verification_evidence"),
}
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
WORKSPACE_NAME = "source_distillation"
DELIVERABLE_NAME = "source_distilled.md"
OLD_ARTIFACTS = {
    "breakdown": "01.md",
    "assign-rutters": "02_assign-rutters.md",
    "extract-evolutions": "03_extract-evolutions.md",
    "validate-logic": "04_validate-logic.md",
    "design-implementation": "05_design-implementation.md",
    "implement": "06_implement.md",
    "finalize": "07_finalize.md",
    "verify": "08_verify.md",
}


def _load_module(name: str):
    package_dir = SKILL_ROOT / "_rtx"
    assert package_dir.is_dir(), "the artifact-contract runtime has not been implemented"
    if PACKAGE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            package_dir / "__init__.py",
            submodule_search_locations=[str(package_dir)],
        )
        assert spec is not None and spec.loader is not None
        package = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = package
        spec.loader.exec_module(package)
    runtime_name = {
        "artifact_contract": "_artifact_contract",
        "interface": "_artifact_contract_interface",
    }[name]
    return importlib.import_module(f"{PACKAGE_NAME}.{runtime_name}")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "source.md").write_bytes(b"# exact source\r\n")
    (root / DELIVERABLE_NAME).write_bytes(b"Use the distilled Rutter entrypoint.\n")
    public_runtime = root / "src/officina/rutter"
    public_runtime.mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / "src/officina/rutter/blueprint.yaml",
        public_runtime / "blueprint.yaml",
    )
    shutil.copytree(
        REPOSITORY_ROOT / "src/officina/rutter/blueprints",
        public_runtime / "blueprints",
    )
    engine_path = public_runtime / "blueprints/engine.yaml"
    engine = yaml.safe_load(engine_path.read_text(encoding="utf-8"))
    engine["interfaces"][
        "rutter.source.engine.interface.bound-operations"
    ]["contract"]["semantic_enforcement"] = {
        "version": 1,
        "request": {
            "operation": "get-instruction",
            "owner_ref": "result.requested_owner",
            "evidence_ref": "input.evidence",
        },
        "validation": {
            "operation": "validate",
            "input_ref": "input",
            "evidence_ref": "input.evidence",
            "validator_binding_ref": "binding.state.input_validator",
            "output_ref": "result",
            "rejection_codes": ["invalid-result"],
        },
        "transition": {
            "operation": "advance",
            "input_ref": "input",
            "evidence_ref": "input.evidence",
            "owning_evolution_ref": "binding.fix.current_state_id",
            "authority_ref": "binding.state.next_state",
            "successor_ref": "result",
        },
    }
    engine_path.write_text(
        yaml.safe_dump(engine, sort_keys=False),
        encoding="utf-8",
    )
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workspace(root: Path) -> Path:
    workspace = root / WORKSPACE_NAME
    workspace.mkdir(exist_ok=True)
    return workspace


def _valid_body(stage: str, root: Path | None = None) -> dict[str, Any]:
    source_digest = _sha256(root / "source.md") if root is not None else "a" * 64
    candidate_digest = (
        _sha256(root / DELIVERABLE_NAME) if root is not None else "b" * 64
    )
    bodies: dict[str, dict[str, Any]] = {
        "breakdown": {
            "context_closure": [
                {
                    "obligation_id": "obl-source",
                    "path": "source.md",
                    "availability": "present",
                    "digest": source_digest,
                    "authority": "normative",
                    "provenance": "source",
                    "why_behavior_defining": "Defines the requested behavior.",
                    "resolution": "resolved",
                }
            ],
            "conflicts": [],
            "parts": [
                {
                    "part_id": "part-main",
                    "obligation_ids": ["obl-source"],
                    "independence": "inseparable",
                    "reason": "The state and transition semantics are shared.",
                }
            ],
        },
        "assign-rutters": {
            "assignments": [
                {
                    "part_id": "part-main",
                    "voyage_id": "voyage-main",
                    "rutter_definition_id": "rutter-main",
                    "charter_fields": ["source_path"],
                    "input_ids": ["source"],
                    "output_ids": ["result"],
                    "inseparability": {
                        "status": "inseparable",
                        "reason": "The part shares state transitions with its peer.",
                    },
                    "independent_workflows": [],
                }
            ],
            "orchestration": {
                "mode": "single",
                "coordinator_rutter_id": None,
                "starts": [],
                "dependencies": [],
                "joins": [],
                "aggregate_results": [],
                "partial_failure": [],
                "retries": [],
                "retry_owner": "rutter-main",
                "cancellation": [],
                "failure_propagation": [],
                "authorization": [],
                "release": [],
            },
        },
        "extract-evolutions": {
            "rutters": [
                {
                    "rutter_id": "rutter-main",
                    "voyage_ids": ["voyage-main"],
                    "version": 1,
                    "initial_evolution": "inspect",
                    "charter_fields": ["source_path"],
                    "evolutions": [
                        {
                            "evolution_id": "inspect",
                            "evolution_type": "operation",
                            "obligation_ids": ["obl-source"],
                            "decision_owner": "rutter",
                            "validator": "validate_result",
                            "outcomes": ["done", "failed"],
                        }
                    ],
                    "transitions": [
                        {"from": "inspect", "outcome": "done", "to": "complete"},
                        {"from": "inspect", "outcome": "failed", "to": "failed"},
                    ],
                    "terminal_results": ["complete", "failed"],
                }
            ]
        },
        "validate-logic": {
            "enforcement_matrix": [
                {
                    "obligation_id": "obl-source",
                    "original_decision_owner": "rutter",
                    "automation_permission": "deterministic",
                    "public_runtime_capability": "rutter.interface.bound-operations",
                    "public_runtime_version": 1,
                    "public_binding_contract_version": 1,
                    "capability_verified": True,
                    "owning_evolution": "rutter-main/inspect",
                    "exact_mechanism": {
                        "enforcement_class": "rutter-state-transition",
                        "request": None,
                        "validation": {
                            "operation": "validate",
                            "input_ref": "input",
                            "evidence_ref": "input.evidence",
                            "validator_ref": "validate_result",
                            "validator_binding_ref": "binding.state.input_validator",
                            "output_ref": "result",
                        },
                        "transition": {
                            "operation": "advance",
                            "input_ref": "input",
                            "evidence_ref": "input.evidence",
                            "authority": "owning-rutter-evolution",
                            "owning_evolution_ref": "binding.fix.current_state_id",
                            "authority_ref": "binding.state.next_state",
                            "successor_ref": "result",
                        },
                    },
                    "precondition": "source is available",
                    "postcondition": "result is accepted",
                    "failure_result": "failed",
                    "observable_evidence": {
                        "operation": "validate",
                        "evidence_ref": "input.evidence",
                        "output_ref": "result",
                    },
                    "positive_trace": {
                        "operation": "advance",
                        "input_ref": "input",
                        "evidence_ref": "input.evidence",
                        "outcome": "done",
                        "expected": {
                            "kind": "successor",
                            "state": "complete",
                            "result_ref": "result",
                        },
                    },
                    "negative_trace": {
                        "operation": "validate",
                        "input_ref": "input",
                        "evidence_ref": "input.evidence",
                        "outcome": "invalid",
                        "expected": {
                            "kind": "rejection",
                            "rejection_code": "invalid-result",
                        },
                    },
                }
            ]
        },
        "design-implementation": {
            "public_interface_design": [
                {
                    "capability": "rutter-construction",
                    "interface": "rutter.interface.construct",
                    "version": 1,
                    "status": "available",
                    "evidence": "live public declaration",
                }
            ],
            "files": [
                {
                    "path": "skills/example/_rtx/voyage_dispenser.py",
                    "responsibility": "Transparent Rutter declarations.",
                }
            ],
            "verification_commands": ["pytest -q skills/example/tests"],
        },
        "implement": {
            "implementation_trace_map": [
                {
                    "obligation_id": "obl-source",
                    "design_item": "rutter-construction",
                    "implemented_symbol": "RUTTER_MAIN",
                    "path": "skills/example/_rtx/voyage_dispenser.py",
                    "evidence": "focused contract test",
                    "status": "implemented",
                }
            ],
            "changed_files": ["skills/example/_rtx/voyage_dispenser.py"],
            "limitations": [],
        },
        "finalize": {
            "entrypoint_binding": {
                "candidate_path": DELIVERABLE_NAME,
                "candidate_sha256": candidate_digest,
                "public_binding": "using-compass binding document",
                "source_outcome": "implemented",
                "gateway_interpretation": "accepted",
            }
        },
        "verify": {
            "candidate_path": DELIVERABLE_NAME,
            "candidate_sha256": candidate_digest,
            "verification_evidence": [
                {
                    "command": "pytest -q skills/example/tests",
                    "result": "passed",
                    "classification": "product",
                    "evidence": "1 passed",
                }
            ],
            "semantic_traces": [
                {
                    "obligation_id": "obl-source",
                    "positive_result": "passed",
                    "negative_result": "rejected",
                }
            ],
        },
    }
    return bodies[stage]


def _write_artifact(
    root: Path,
    *,
    name: str,
    stage: str,
    outcome: str | None = None,
    body: dict[str, Any] | None = None,
    prerequisites: list[dict[str, Any]] | None = None,
) -> Path:
    success, body_schema, _ = STAGE_CASES[stage]
    envelope = {
        "schema_version": "distill-to-rutters/v1",
        "stage": stage,
        "outcome": outcome or success,
        "prerequisites": prerequisites
        if prerequisites is not None
        else [
            {
                "kind": "source",
                "path": "source.md",
                "sha256": _sha256(root / "source.md"),
            }
        ],
        "body_schema": body_schema,
    }
    path = _workspace(root) / name
    path.write_text(
        "```yaml\n"
        + yaml.safe_dump(envelope, sort_keys=False)
        + "```\n\n"
        + "```distill-contract\n"
        + yaml.safe_dump(
            _valid_body(stage, root) if body is None else body, sort_keys=False
        )
        + "```\n",
        encoding="utf-8",
    )
    return path


def _artifact_prerequisite(root: Path, path: Path, stage: str) -> dict[str, str]:
    return {
        "kind": "artifact",
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "stage": stage,
        "schema_version": "distill-to-rutters/v1",
    }


def _write_artifact_chain(
    root: Path,
    through_stage: str,
    *,
    outcome: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    previous_stage: str | None = None
    for stage in STAGE_CASES:
        if previous_stage is None:
            prerequisites = None
        else:
            prerequisites = [
                _artifact_prerequisite(root, artifacts[previous_stage], previous_stage)
            ]
            if stage in {"finalize", "verify"}:
                prerequisites.append(
                    {
                        "kind": "deliverable",
                        "path": DELIVERABLE_NAME,
                        "sha256": _sha256(root / DELIVERABLE_NAME),
                    }
                )
        artifacts[stage] = _write_artifact(
            root,
            name=STAGE_ARTIFACTS[stage],
            stage=stage,
            outcome=outcome if stage == through_stage else None,
            body=body if stage == through_stage else None,
            prerequisites=prerequisites,
        )
        if stage == through_stage:
            return artifacts
        previous_stage = stage
    raise AssertionError(f"unknown stage: {through_stage}")


def _write_graph_chain(
    root: Path,
    *,
    breakdown_body: dict[str, Any] | None = None,
    breakdown_outcome: str = "breakdown-ready",
    assignment_body: dict[str, Any] | None = None,
    assignment_outcome: str = "assignment-ready",
    graph_body: dict[str, Any] | None = None,
) -> tuple[Path, Path, Path]:
    breakdown = _write_artifact(
        root,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        outcome=breakdown_outcome,
        body=breakdown_body,
    )
    assignment = _write_artifact(
        root,
        name=STAGE_ARTIFACTS["assign-rutters"],
        stage="assign-rutters",
        outcome=assignment_outcome,
        body=assignment_body,
        prerequisites=[_artifact_prerequisite(root, breakdown, "breakdown")],
    )
    graph = _write_artifact(
        root,
        name=STAGE_ARTIFACTS["extract-evolutions"],
        stage="extract-evolutions",
        body=graph_body,
        prerequisites=[
            _artifact_prerequisite(root, assignment, "assign-rutters")
        ],
    )
    return breakdown, assignment, graph


def test_sha256_file_hashes_exact_raw_bytes(repository: Path) -> None:
    contract = _load_module("artifact_contract")
    artifact = repository / "bytes.md"
    artifact.write_bytes(b"line one\r\nline two\n")

    assert contract.sha256_file(artifact) == (
        "af28611c8dd7cdaa70b328947a47e7236543cff6aee512d92f80132b7f8db82f"
    )


@pytest.mark.parametrize(
    "stage",
    ("breakdown", "assign-rutters", "extract-evolutions", "validate-logic"),
)
def test_validate_artifact_accepts_each_stage_in_a_complete_chain(
    repository: Path, stage: str
) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact_chain(repository, stage)[stage]

    result = contract.validate_artifact(artifact, stage)

    assert result.valid is True
    assert result.envelope.stage == stage
    assert result.errors == ()


@pytest.mark.parametrize(
    "stage",
    ("design-implementation", "implement", "finalize", "verify"),
)
def test_phase_b_success_claims_do_not_validate_on_current_blocked_runtime(
    repository: Path,
    stage: str,
) -> None:
    """Current public compatibility cannot support any downstream success claim."""
    contract = _load_module("artifact_contract")
    artifact = _write_artifact_chain(repository, stage)[stage]

    result = contract.validate_artifact(artifact, stage)

    assert result.valid is False


@pytest.mark.parametrize("stage", tuple(STAGE_CASES)[1:])
def test_validate_artifact_rejects_stage_skipping_without_immediate_predecessor(
    repository: Path, stage: str
) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS[stage],
        stage=stage,
    )

    result = contract.validate_artifact(artifact, stage)

    assert result.valid is False
    assert any("immediate preceding stage" in error for error in result.errors)


@pytest.mark.parametrize("stage", STAGE_CASES)
def test_validate_artifact_rejects_missing_required_machine_rows(
    repository: Path, stage: str
) -> None:
    contract = _load_module("artifact_contract")
    required_row = STAGE_CASES[stage][2]
    body = _valid_body(stage, repository)
    del body[required_row]
    artifact = _write_artifact_chain(repository, stage, body=body)[stage]

    result = contract.validate_artifact(artifact, stage)

    assert result.valid is False
    assert any(required_row in error for error in result.errors)


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("missing", "outcome"),
        ("duplicate", "duplicate key"),
        ("unknown-outcome", "unknown outcome"),
        ("wrong-stage", "expected stage"),
        ("duplicate-body", "duplicate distill-contract"),
    ],
)
def test_validate_artifact_rejects_malformed_envelopes_and_bodies(
    repository: Path, mutation: str, expected_error: str
) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
    )
    text = artifact.read_text(encoding="utf-8")
    if mutation == "missing":
        text = text.replace("outcome: breakdown-ready\n", "")
    elif mutation == "duplicate":
        text = text.replace(
            "outcome: breakdown-ready\n",
            "outcome: breakdown-ready\noutcome: failed\n",
        )
    elif mutation == "unknown-outcome":
        text = text.replace("breakdown-ready", "invented-result")
    elif mutation == "wrong-stage":
        text = text.replace("stage: breakdown", "stage: assign-rutters")
    elif mutation == "duplicate-body":
        text += text[text.index("```distill-contract") :]
    artifact.write_text(text, encoding="utf-8")

    result = contract.validate_artifact(artifact, "breakdown")

    assert result.valid is False
    assert any(expected_error in error for error in result.errors)


def test_validate_artifact_rejects_repository_and_symlink_escapes(
    repository: Path, tmp_path: Path
) -> None:
    contract = _load_module("artifact_contract")
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    escape = repository / "escape.md"
    escape.symlink_to(outside)
    artifact_name = STAGE_ARTIFACTS["breakdown"]
    traversal = _write_artifact(
        repository,
        name=artifact_name,
        stage="breakdown",
        prerequisites=[
            {"kind": "source", "path": "../outside.md", "sha256": _sha256(outside)}
        ],
    )
    traversal_result = contract.validate_artifact(traversal, "breakdown")
    symlink = _write_artifact(
        repository,
        name=artifact_name,
        stage="breakdown",
        prerequisites=[
            {"kind": "source", "path": "escape.md", "sha256": _sha256(outside)}
        ],
    )
    symlink_result = contract.validate_artifact(symlink, "breakdown")

    assert traversal_result.valid is False
    assert traversal_result.errors
    assert symlink_result.valid is False
    assert any("repository" in error for error in symlink_result.errors)


def test_breakdown_requires_the_exact_source_stem_workspace(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
    )
    wrong_workspace = repository / "unrelated_distillation"
    wrong_workspace.mkdir()
    moved = artifact.rename(wrong_workspace / artifact.name)

    result = contract.validate_artifact(moved, "breakdown")

    assert result.valid is False
    assert any("source-stem workspace" in error for error in result.errors)


def test_breakdown_requires_one_exact_root_source_prerequisite(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    other = repository / "other.md"
    other.write_bytes(b"other run\n")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        prerequisites=[
            {
                "kind": "source",
                "path": "source.md",
                "sha256": _sha256(repository / "source.md"),
            },
            {
                "kind": "source",
                "path": "other.md",
                "sha256": _sha256(other),
            },
        ],
    )

    result = contract.validate_artifact(artifact, "breakdown")

    assert result.valid is False
    assert any("one root source" in error for error in result.errors)


def test_assignment_rejects_cross_run_predecessor_splicing(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    breakdown = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
    )
    other_workspace = repository / "other_distillation"
    other_workspace.mkdir()
    foreign = other_workspace / breakdown.name
    foreign.write_bytes(breakdown.read_bytes())
    assignment = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["assign-rutters"],
        stage="assign-rutters",
        prerequisites=[_artifact_prerequisite(repository, foreign, "breakdown")],
    )

    result = contract.validate_artifact(assignment, "assign-rutters")

    assert result.valid is False
    assert any("cross-run" in error for error in result.errors)


def test_breakdown_generated_projection_requires_its_governing_source(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    body = _valid_body("breakdown", repository)
    body["context_closure"][0]["provenance"] = "generated projection"
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        body=body,
    )

    result = contract.validate_artifact(artifact, "breakdown")

    assert result.valid is False
    assert any("governing_source" in error for error in result.errors)


@pytest.mark.parametrize(
    ("availability", "digest", "resolution", "valid"),
    [
        ("present", "a" * 64, "resolved", True),
        ("present", "a" * 64, "conflict", True),
        ("missing", None, "unresolved", True),
        ("unreadable", None, "unresolved", True),
        ("present", None, "resolved", False),
        ("missing", "a" * 64, "unresolved", False),
        ("missing", None, "resolved", False),
        ("unreadable", "a" * 64, "unresolved", False),
        ("unreadable", None, "resolved", False),
    ],
)
def test_breakdown_context_rows_distinguish_available_and_unavailable_references(
    repository: Path,
    availability: str,
    digest: str | None,
    resolution: str,
    valid: bool,
) -> None:
    """An unavailable file has no raw-byte digest; a present one always has one."""
    contract = _load_module("artifact_contract")
    body = _valid_body("breakdown", repository)
    if digest == "a" * 64:
        digest = _sha256(repository / "source.md")
    body["context_closure"][0].update(
        availability=availability,
        digest=digest,
        resolution=resolution,
    )
    outcome = (
        "breakdown-ready"
        if availability == "present" and resolution == "resolved"
        else "breakdown-gap"
    )
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        outcome=outcome,
        body=body,
    )

    result = contract.validate_artifact(artifact, "breakdown")

    assert result.valid is valid


def test_breakdown_ready_hashes_generated_normative_context_and_governing_source(
    repository: Path,
) -> None:
    """Changing an implicit governed dependency must stale the breakdown owner."""
    contract = _load_module("artifact_contract")
    generated = repository / "generated.md"
    generated.write_bytes(b"generated normative behavior\r\n")
    body = _valid_body("breakdown", repository)
    body["context_closure"].append(
        {
            "obligation_id": "obl-generated",
            "path": "generated.md",
            "availability": "present",
            "digest": _sha256(generated),
            "authority": "normative",
            "provenance": "generated projection",
            "why_behavior_defining": "Projects a normative source rule.",
            "resolution": "resolved",
            "governing_source": "source.md",
        }
    )
    body["parts"][0]["obligation_ids"].append("obl-generated")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        body=body,
    )
    approved = _sha256(artifact)
    assert contract.validate_artifact(artifact, "breakdown").valid is True

    generated.write_bytes(b"changed generated behavior\n")
    decision = contract.decide_route(
        "breakdown", "breakdown-ready", approved, "approve", artifact
    )

    assert decision.status == "stale"
    assert decision.authorized_route == "breakdown"
    assert decision.earliest_stale_prerequisite == "generated.md"


def test_breakdown_ready_requires_generated_governing_source_in_closure(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    generated = repository / "generated.md"
    generated.write_bytes(b"generated normative behavior\n")
    body = _valid_body("breakdown", repository)
    body["context_closure"] = [
        {
            "obligation_id": "obl-generated",
            "path": "generated.md",
            "availability": "present",
            "digest": _sha256(generated),
            "authority": "normative",
            "provenance": "generated projection",
            "why_behavior_defining": "Projects a normative source rule.",
            "resolution": "resolved",
            "governing_source": "source.md",
        }
    ]
    body["parts"][0]["obligation_ids"] = ["obl-generated"]
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        body=body,
    )

    result = contract.validate_artifact(artifact, "breakdown")

    assert result.valid is False
    assert any("governing source" in error for error in result.errors)


def test_breakdown_context_path_cannot_escape_through_a_symlink(
    repository: Path,
    tmp_path: Path,
) -> None:
    contract = _load_module("artifact_contract")
    outside = tmp_path / "outside-context.md"
    outside.write_bytes(b"outside behavior\n")
    (repository / "context-link.md").symlink_to(outside)
    body = _valid_body("breakdown", repository)
    body["context_closure"][0].update(
        path="context-link.md",
        digest=_sha256(outside),
    )
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        body=body,
    )

    result = contract.validate_artifact(artifact, "breakdown")

    assert result.valid is False
    assert any("context" in error and "repository" in error for error in result.errors)


@pytest.mark.parametrize(
    "mutation",
    ("missing", "unreadable", "unresolved", "conflict-row", "conflicts"),
)
def test_breakdown_ready_rejects_every_incomplete_context_state(
    repository: Path,
    mutation: str,
) -> None:
    contract = _load_module("artifact_contract")
    body = _valid_body("breakdown", repository)
    row = body["context_closure"][0]
    if mutation in {"missing", "unreadable"}:
        row.update(availability=mutation, digest=None, resolution="unresolved")
    elif mutation == "unresolved":
        row["resolution"] = "unresolved"
    elif mutation == "conflict-row":
        row["resolution"] = "conflict"
    else:
        body["conflicts"] = [
            {
                "conflict_id": "conflict-1",
                "paths": ["source.md", "other.md"],
                "resolution": "awaiting authority decision",
            }
        ]
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        body=body,
    )

    result = contract.validate_artifact(artifact, "breakdown")

    assert result.valid is False
    assert any("breakdown-ready" in error for error in result.errors)


def test_breakdown_gap_can_record_an_unreadable_context_gap(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    body = _valid_body("breakdown", repository)
    body["context_closure"][0].update(
        availability="unreadable",
        digest=None,
        resolution="unresolved",
    )
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        outcome="breakdown-gap",
        body=body,
    )

    assert contract.validate_artifact(artifact, "breakdown").valid is True


def test_coordinated_assignment_requires_a_coordinator_rutter(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    body = _valid_body("assign-rutters", repository)
    body["orchestration"]["mode"] = "coordinated"
    artifact = _write_artifact_chain(repository, "assign-rutters", body=body)[
        "assign-rutters"
    ]

    result = contract.validate_artifact(artifact, "assign-rutters")

    assert result.valid is False
    assert any("coordinator_rutter_id" in error for error in result.errors)


@pytest.mark.parametrize(
    "container, field",
    [
        ("assignment", "inseparability"),
        ("assignment", "independent_workflows"),
        ("orchestration", "partial_failure"),
        ("orchestration", "retry_owner"),
    ],
)
def test_assignment_requires_rutter_owned_decomposition_and_failure_policy(
    repository: Path, container: str, field: str
) -> None:
    """Missing assignment ownership data cannot be repaired by result validation."""
    contract = _load_module("artifact_contract")
    body = _valid_body("assign-rutters", repository)
    target = (
        body["assignments"][0]
        if container == "assignment"
        else body["orchestration"]
    )
    target.pop(field)
    artifact = _write_artifact_chain(repository, "assign-rutters", body=body)[
        "assign-rutters"
    ]

    result = contract.validate_artifact(artifact, "assign-rutters")

    assert result.valid is False
    assert any(field in error for error in result.errors)


@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate"))
def test_assignment_ready_has_exactly_one_row_for_every_breakdown_part(
    repository: Path,
    mutation: str,
) -> None:
    contract = _load_module("artifact_contract")
    body = _valid_body("assign-rutters", repository)
    if mutation == "missing":
        body["assignments"][0]["part_id"] = "different-part"
    elif mutation == "extra":
        extra = dict(body["assignments"][0])
        extra.update(
            part_id="extra-part",
            voyage_id="voyage-extra",
            rutter_definition_id="rutter-extra",
        )
        body["assignments"].append(extra)
    else:
        body["assignments"].append(dict(body["assignments"][0]))
    artifact = _write_artifact_chain(
        repository,
        "assign-rutters",
        body=body,
    )["assign-rutters"]

    result = contract.validate_artifact(artifact, "assign-rutters")

    assert result.valid is False
    assert any("breakdown parts" in error for error in result.errors)


def test_assignment_ready_requires_a_breakdown_ready_predecessor(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    breakdown = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        outcome="breakdown-gap",
    )
    assignment = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["assign-rutters"],
        stage="assign-rutters",
        prerequisites=[_artifact_prerequisite(repository, breakdown, "breakdown")],
    )

    result = contract.validate_artifact(assignment, "assign-rutters")

    assert result.valid is False
    assert any("breakdown-ready" in error for error in result.errors)


@pytest.mark.parametrize(
    "mutation",
    ("rutter", "voyage", "workflow", "obligation", "coordinator"),
)
def test_graph_ready_closes_assignment_foreign_keys(
    repository: Path,
    mutation: str,
) -> None:
    contract = _load_module("artifact_contract")
    assignment = _valid_body("assign-rutters", repository)
    graph = _valid_body("extract-evolutions", repository)
    if mutation == "rutter":
        graph["rutters"][0]["rutter_id"] = "foreign-rutter"
    elif mutation == "voyage":
        graph["rutters"][0]["voyage_ids"] = ["foreign-voyage"]
    elif mutation == "workflow":
        assignment["assignments"][0]["independent_workflows"] = [
            {
                "voyage_id": "foreign-voyage",
                "join_transition": "rutter-main/inspect",
            }
        ]
    elif mutation == "obligation":
        graph["rutters"][0]["evolutions"][0]["obligation_ids"] = [
            "obl-foreign"
        ]
    else:
        assignment["orchestration"].update(
            mode="coordinated",
            coordinator_rutter_id="rutter-main",
            starts=[
                {
                    "obligation_id": "obl-start",
                    "owning_transition": "missing-transition",
                    "evidence": "start authorization",
                }
            ],
        )
    _, _, artifact = _write_graph_chain(
        repository,
        assignment_body=assignment,
        graph_body=graph,
    )

    result = contract.validate_artifact(artifact, "extract-evolutions")

    assert result.valid is False
    assert any("assignment graph" in error for error in result.errors)


@pytest.mark.parametrize(
    "mutation",
    ("missing-successor", "duplicate-successor", "invalid-target", "invalid-source"),
)
def test_graph_ready_requires_one_valid_successor_per_declared_outcome(
    repository: Path,
    mutation: str,
) -> None:
    contract = _load_module("artifact_contract")
    graph = _valid_body("extract-evolutions", repository)
    transitions = graph["rutters"][0]["transitions"]
    if mutation == "missing-successor":
        transitions.pop()
    elif mutation == "duplicate-successor":
        transitions.append(dict(transitions[0], to="failed"))
    elif mutation == "invalid-target":
        transitions[0]["to"] = "not-a-state-or-result"
    else:
        transitions[0]["from"] = "not-an-evolution"
    _, _, artifact = _write_graph_chain(repository, graph_body=graph)

    result = contract.validate_artifact(artifact, "extract-evolutions")

    assert result.valid is False
    assert any("successor" in error for error in result.errors)


def test_logic_captured_requires_every_public_capability_to_be_verified(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    body = _valid_body("validate-logic", repository)
    body["enforcement_matrix"][0]["capability_verified"] = False
    captured = _write_artifact_chain(repository, "validate-logic", body=body)[
        "validate-logic"
    ]
    captured_result = contract.validate_artifact(captured, "validate-logic")
    gap = _write_artifact_chain(
        repository,
        "validate-logic",
        outcome="logic-gap",
        body=body,
    )["validate-logic"]

    gap_result = contract.validate_artifact(gap, "validate-logic")

    assert captured_result.valid is False
    assert any("capability_verified" in error for error in captured_result.errors)
    assert gap_result.valid is True


@pytest.mark.parametrize("status", ("gap", "blocked", "partial", "failed"))
def test_implemented_rejects_every_nonimplemented_trace_row(
    repository: Path,
    status: str,
) -> None:
    contract = _load_module("artifact_contract")
    body = _valid_body("implement", repository)
    body["implementation_trace_map"][0]["status"] = status
    artifact = _write_artifact_chain(repository, "implement", body=body)["implement"]

    result = contract.validate_artifact(artifact, "implement")

    assert result.valid is False
    assert any("implemented" in error and "trace" in error for error in result.errors)


@pytest.mark.parametrize("mutation", ("path", "digest", "extra-leaf"))
def test_entrypoint_ready_equals_its_one_contained_deliverable_leaf(
    repository: Path,
    mutation: str,
) -> None:
    contract = _load_module("artifact_contract")
    artifacts = _write_artifact_chain(repository, "implement")
    implementation = artifacts["implement"]
    candidate = repository / DELIVERABLE_NAME
    body = _valid_body("finalize", repository)
    prerequisites: list[dict[str, Any]] = [
        _artifact_prerequisite(repository, implementation, "implement"),
        {
            "kind": "deliverable",
            "path": DELIVERABLE_NAME,
            "sha256": _sha256(candidate),
        },
    ]
    if mutation == "path":
        body["entrypoint_binding"]["candidate_path"] = "other_distilled.md"
    elif mutation == "digest":
        body["entrypoint_binding"]["candidate_sha256"] = "0" * 64
    else:
        other = repository / "other_distilled.md"
        other.write_bytes(b"other entrypoint\n")
        prerequisites.append(
            {
                "kind": "deliverable",
                "path": "other_distilled.md",
                "sha256": _sha256(other),
            }
        )
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["finalize"],
        stage="finalize",
        body=body,
        prerequisites=prerequisites,
    )

    result = contract.validate_artifact(artifact, "finalize")

    assert result.valid is False
    assert any("deliverable leaf" in error for error in result.errors)


@pytest.mark.parametrize("result_value", ("failed", "blocked"))
def test_verified_rejects_failed_or_blocked_verification_checks(
    repository: Path,
    result_value: str,
) -> None:
    contract = _load_module("artifact_contract")
    body = _valid_body("verify", repository)
    body["verification_evidence"][0]["result"] = result_value
    artifact = _write_artifact_chain(repository, "verify", body=body)["verify"]

    result = contract.validate_artifact(artifact, "verify")

    assert result.valid is False
    assert any("verified" in error and "checks" in error for error in result.errors)


@pytest.mark.parametrize("mutation", ("path", "digest", "missing-leaf"))
def test_verified_candidate_equals_approved_entrypoint_predecessor(
    repository: Path,
    mutation: str,
) -> None:
    contract = _load_module("artifact_contract")
    artifacts = _write_artifact_chain(repository, "finalize")
    entrypoint = artifacts["finalize"]
    candidate = repository / DELIVERABLE_NAME
    body = _valid_body("verify", repository)
    prerequisites: list[dict[str, Any]] = [
        _artifact_prerequisite(repository, entrypoint, "finalize"),
        {
            "kind": "deliverable",
            "path": DELIVERABLE_NAME,
            "sha256": _sha256(candidate),
        },
    ]
    if mutation == "path":
        body["candidate_path"] = "other_distilled.md"
    elif mutation == "digest":
        body["candidate_sha256"] = "0" * 64
    else:
        prerequisites.pop()
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["verify"],
        stage="verify",
        body=body,
        prerequisites=prerequisites,
    )

    result = contract.validate_artifact(artifact, "verify")

    assert result.valid is False
    assert any("entrypoint predecessor" in error for error in result.errors)


def test_freshness_reports_changed_direct_prerequisite(repository: Path) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
    )
    (repository / "source.md").write_text("changed\n", encoding="utf-8")

    result = contract.check_freshness(artifact)

    assert result.current is False
    assert result.earliest_stale_prerequisite == "source.md"
    assert result.owning_stage == "breakdown"
    assert result.error is None


def test_freshness_walks_transitive_artifacts_and_returns_earliest_stage(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    breakdown = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
    )
    assignment = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["assign-rutters"],
        stage="assign-rutters",
        prerequisites=[_artifact_prerequisite(repository, breakdown, "breakdown")],
    )
    graph = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["extract-evolutions"],
        stage="extract-evolutions",
        prerequisites=[
            _artifact_prerequisite(repository, assignment, "assign-rutters")
        ],
    )
    (repository / "source.md").write_text("changed\n", encoding="utf-8")

    result = contract.check_freshness(graph)

    assert result.current is False
    assert result.earliest_stale_prerequisite == "source.md"
    assert result.owning_stage == "breakdown"


def test_freshness_rejects_structurally_impossible_artifact_cycles(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    first = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        prerequisites=[
            {
                "kind": "artifact",
                "path": f"{WORKSPACE_NAME}/{STAGE_ARTIFACTS['assign-rutters']}",
                "sha256": "0" * 64,
                "stage": "assign-rutters",
                "schema_version": "distill-to-rutters/v1",
            }
        ],
    )
    _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["assign-rutters"],
        stage="assign-rutters",
        prerequisites=[
            {
                "kind": "artifact",
                "path": f"{WORKSPACE_NAME}/{STAGE_ARTIFACTS['breakdown']}",
                "sha256": "0" * 64,
                "stage": "breakdown",
                "schema_version": "distill-to-rutters/v1",
            }
        ],
    )

    result = contract.check_freshness(first)

    assert result.current is False
    assert result.error is not None
    assert "earlier stage" in result.error


@pytest.mark.parametrize(
    ("stage", "next_stage"),
    [
        ("breakdown", "assign-rutters"),
        ("assign-rutters", "extract-evolutions"),
        ("extract-evolutions", "validate-logic"),
        ("validate-logic", "design-implementation"),
    ],
)
def test_only_success_outcomes_advance_in_final_stage_order(
    repository: Path, stage: str, next_stage: str | None
) -> None:
    contract = _load_module("artifact_contract")
    outcome = STAGE_CASES[stage][0]
    artifact = _write_artifact_chain(repository, stage)[stage]

    decision = contract.decide_route(
        stage, outcome, _sha256(artifact), "approve", artifact
    )

    assert decision.status == "accepted"
    assert decision.authorized_route == next_stage
    assert decision.earliest_stale_prerequisite is None


@pytest.mark.parametrize(
    ("stage", "next_stage"),
    [
        ("breakdown", "assign-rutters"),
        ("assign-rutters", "extract-evolutions"),
        ("extract-evolutions", "validate-logic"),
        ("validate-logic", "design-implementation"),
    ],
)
def test_public_interface_accepts_only_each_exact_stage_basename(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    stage: str,
    next_stage: str | None,
) -> None:
    interface = _load_module("interface")
    artifact = _write_artifact_chain(repository, stage)[stage]

    status = interface.main(
        [
            "--artifact-path",
            str(artifact),
            "--expected-stage",
            stage,
            "--approved-digest",
            _sha256(artifact),
            "--user-decision",
            "approve",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert artifact.name == STAGE_ARTIFACTS[stage]
    assert status == 0
    assert payload["status"] == "accepted"
    assert payload["authorized_route"] == next_stage


@pytest.mark.parametrize("stage", STAGE_CASES)
@pytest.mark.parametrize("filename_kind", ("old", "unknown"))
def test_public_interface_rejects_old_and_unknown_stage_basenames(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    stage: str,
    filename_kind: str,
) -> None:
    interface = _load_module("interface")
    artifact = _write_artifact_chain(repository, stage)[stage]
    rejected_name = (
        OLD_ARTIFACTS[stage]
        if filename_kind == "old"
        else f"unknown-{STAGE_ARTIFACTS[stage]}"
    )
    rejected = artifact.rename(artifact.with_name(rejected_name))

    status = interface.main(
        [
            "--artifact-path",
            str(rejected),
            "--expected-stage",
            stage,
            "--approved-digest",
            _sha256(rejected),
            "--user-decision",
            "approve",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 1
    assert payload["status"] == "failed"
    assert payload["authorized_route"] is None


def test_public_interface_never_advances_design_blocked(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    interface = _load_module("interface")
    artifact = _write_artifact_chain(
        repository,
        "design-implementation",
        outcome="design-blocked",
    )["design-implementation"]

    status = interface.main(
        [
            "--artifact-path",
            str(artifact),
            "--expected-stage",
            "design-implementation",
            "--approved-digest",
            _sha256(artifact),
            "--user-decision",
            "approve",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 1
    assert payload["status"] == "blocked"
    assert payload["authorized_route"] is None


@pytest.mark.parametrize(
    ("stage", "outcome", "status", "repair"),
    [
        ("breakdown", "breakdown-gap", "gap", "breakdown"),
        ("assign-rutters", "assignment-gap", "gap", "assign-rutters"),
        ("extract-evolutions", "graph-gap", "gap", "extract-evolutions"),
        ("validate-logic", "logic-gap", "gap", "validate-logic"),
        ("design-implementation", "design-blocked", "blocked", None),
        ("implement", "implementation-blocked", "blocked", None),
        ("finalize", "entrypoint-gap", "gap", "finalize"),
        ("verify", "verification-failed", "failed", None),
        ("verify", "verification-blocked", "blocked", None),
        ("breakdown", "partial", "partial", None),
        ("breakdown", "failed", "failed", None),
    ],
)
def test_non_success_outcomes_never_advance(
    repository: Path,
    stage: str,
    outcome: str,
    status: str,
    repair: str | None,
) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact_chain(repository, stage, outcome=outcome)[stage]

    decision = contract.decide_route(
        stage, outcome, _sha256(artifact), "approve", artifact
    )

    assert decision.status == status
    assert decision.authorized_route == repair


def test_wrong_hash_and_explicit_rejection_only_authorize_repairs(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
    )

    stale = contract.decide_route(
        "breakdown", "breakdown-ready", "0" * 64, "approve", artifact
    )
    rejected = contract.decide_route(
        "breakdown", "breakdown-ready", _sha256(artifact), "reject", artifact
    )

    assert stale.status == "stale"
    assert stale.authorized_route == "breakdown"
    assert stale.earliest_stale_prerequisite == (
        f"{WORKSPACE_NAME}/{STAGE_ARTIFACTS['breakdown']}"
    )
    assert rejected.status == "rejected"
    assert rejected.authorized_route == "breakdown"


def test_rejection_with_wrong_candidate_digest_routes_to_candidate_owner(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
    )

    decision = contract.decide_route(
        "breakdown", "breakdown-ready", "0" * 64, "reject", artifact
    )

    assert decision.status == "stale"
    assert decision.authorized_route == "breakdown"
    assert decision.earliest_stale_prerequisite == (
        f"{WORKSPACE_NAME}/{STAGE_ARTIFACTS['breakdown']}"
    )


def test_rejection_with_stale_chain_routes_to_earliest_stale_owner(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    artifacts = _write_artifact_chain(repository, "validate-logic")
    artifact = artifacts["validate-logic"]
    (repository / "source.md").write_text("changed\n", encoding="utf-8")

    decision = contract.decide_route(
        "validate-logic", "logic-captured", _sha256(artifact), "reject", artifact
    )

    assert decision.status == "stale"
    assert decision.authorized_route == "breakdown"
    assert decision.earliest_stale_prerequisite == "source.md"


def test_stale_transitive_source_routes_to_earliest_owning_stage(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    logic = _write_artifact_chain(repository, "validate-logic")["validate-logic"]
    (repository / "source.md").write_text("changed\n", encoding="utf-8")

    decision = contract.decide_route(
        "validate-logic", "logic-captured", _sha256(logic), "approve", logic
    )

    assert decision.status == "stale"
    assert decision.authorized_route == "breakdown"
    assert decision.earliest_stale_prerequisite == "source.md"


def test_changed_malformed_candidate_is_stale_before_it_is_parsed(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    artifact = _write_artifact_chain(repository, "breakdown")["breakdown"]
    approved = _sha256(artifact)
    artifact.write_bytes(b"not an artifact anymore\n")

    decision = contract.decide_route(
        "breakdown", "breakdown-ready", approved, "approve", artifact
    )

    assert decision.status == "stale"
    assert decision.authorized_route == "breakdown"
    assert decision.earliest_stale_prerequisite == (
        f"{WORKSPACE_NAME}/{STAGE_ARTIFACTS['breakdown']}"
    )


def test_changed_malformed_prerequisite_is_stale_before_recursive_parse(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    artifacts = _write_artifact_chain(repository, "assign-rutters")
    assignment = artifacts["assign-rutters"]
    approved = _sha256(assignment)
    artifacts["breakdown"].write_bytes(b"malformed approved predecessor\n")

    decision = contract.decide_route(
        "assign-rutters", "assignment-ready", approved, "approve", assignment
    )

    assert decision.status == "stale"
    assert decision.authorized_route == "breakdown"
    assert decision.earliest_stale_prerequisite == (
        f"{WORKSPACE_NAME}/{STAGE_ARTIFACTS['breakdown']}"
    )


def test_concurrent_context_mutation_is_refused_by_final_rehash(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file changed after its snapshot cannot authorize advancement."""
    contract = _load_module("artifact_contract")
    artifact = _write_artifact_chain(repository, "breakdown")["breakdown"]
    approved = _sha256(artifact)
    source = (repository / "source.md").resolve()
    original_read_bytes = Path.read_bytes
    mutated = False

    def mutating_read_bytes(path: Path) -> bytes:
        nonlocal mutated
        data = original_read_bytes(path)
        if path.resolve() == source and not mutated:
            mutated = True
            path.write_bytes(b"concurrently changed source\n")
        return data

    monkeypatch.setattr(Path, "read_bytes", mutating_read_bytes)

    decision = contract.decide_route(
        "breakdown", "breakdown-ready", approved, "approve", artifact
    )

    assert mutated is True
    assert decision.status == "stale"
    assert decision.authorized_route == "breakdown"
    assert decision.earliest_stale_prerequisite == "source.md"


def test_source_preflight_can_only_bootstrap_breakdown(repository: Path) -> None:
    contract = _load_module("artifact_contract")
    source = repository / "source.md"

    decision = contract.decide_route(
        "source-preflight", "source-ready", "", "approve", source
    )

    assert decision.status == "accepted"
    assert decision.authorized_route == "breakdown"
    assert decision.artifact_digest == _sha256(source)


def test_artifact_cannot_name_a_later_stage_as_its_prerequisite(
    repository: Path,
) -> None:
    contract = _load_module("artifact_contract")
    future = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["verify"],
        stage="verify",
    )
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
        prerequisites=[_artifact_prerequisite(repository, future, "verify")],
    )

    decision = contract.decide_route(
        "breakdown", "breakdown-ready", _sha256(artifact), "approve", artifact
    )

    assert decision.status == "failed"
    assert decision.authorized_route is None


def test_interface_emits_complete_route_json(repository: Path, capsys) -> None:
    interface = _load_module("interface")
    artifact = _write_artifact(
        repository,
        name=STAGE_ARTIFACTS["breakdown"],
        stage="breakdown",
    )

    status = interface.main(
        [
            "--artifact-path",
            str(artifact),
            "--expected-stage",
            "breakdown",
            "--approved-digest",
            _sha256(artifact),
            "--user-decision",
            "approve",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload == {
        "status": "accepted",
        "artifact_digest": _sha256(artifact),
        "outcome": "breakdown-ready",
        "authorized_route": "assign-rutters",
        "earliest_stale_prerequisite": None,
    }
