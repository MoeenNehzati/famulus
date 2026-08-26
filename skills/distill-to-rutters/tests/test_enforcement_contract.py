from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures/enforcement"
PACKAGE_NAME = "_distill_to_rutters_rtx"
_FIXTURE_RE = re.compile(
    r"^```distill-enforcement-fixture\s*\n(?P<body>.*?)\n```\s*$",
    re.DOTALL | re.MULTILINE,
)


def _load_contract():
    package_dir = SKILL_ROOT / "_rtx"
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
    return importlib.import_module(f"{PACKAGE_NAME}._artifact_contract")


def _fixture(name: str) -> dict[str, Any]:
    text = (FIXTURE_ROOT / name).read_text(encoding="utf-8")
    match = _FIXTURE_RE.search(text)
    assert match is not None
    loaded = yaml.safe_load(match.group("body"))
    assert isinstance(loaded, dict)
    assert {"graph", "logic"} <= set(loaded)
    assert set(loaded) <= {
        "graph",
        "logic",
        "logic_outcome",
        "normative_obligations",
        "assignment",
    }
    return loaded


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_artifact(
    path: Path,
    *,
    stage: str,
    outcome: str,
    body_schema: str,
    prerequisite: dict[str, str],
    body: dict[str, Any],
) -> None:
    envelope = {
        "schema_version": "distill-to-rutters/v1",
        "stage": stage,
        "outcome": outcome,
        "prerequisites": [prerequisite],
        "body_schema": body_schema,
    }
    path.write_text(
        "```yaml\n"
        + yaml.safe_dump(envelope, sort_keys=False)
        + "```\n\n```distill-contract\n"
        + yaml.safe_dump(body, sort_keys=False)
        + "```\n",
        encoding="utf-8",
    )


def _expose_semantic_binding_contract(repository: Path) -> None:
    engine = repository / "src/officina/rutter/blueprints/engine.yaml"
    document = yaml.safe_load(engine.read_text(encoding="utf-8"))
    interface = document["interfaces"][
        "rutter.source.engine.interface.bound-operations"
    ]
    interface["contract"]["semantic_enforcement"] = {
        "version": 1,
        "request": {
            "operation": "get-status",
            "owner_ref": "result.requested_owner",
            "evidence_ref": "input.evidence",
        },
        "validation": {
            "operation": "validate",
            "input_ref": "input",
            "evidence_ref": "input.evidence",
            "validator_binding_ref": "binding.state.input_validator",
            "output_ref": "result",
            "rejection_codes": [
                "invalid-inspection-evidence",
                "owner-evidence-invalid",
                "validator-missing",
                "owner-not-requested",
                "invalid-join-evidence",
                "invalid-release-evidence",
            ],
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
    engine.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _artifacts(
    tmp_path: Path,
    fixture_name: str,
    *,
    logic_outcome: str | None = None,
    expose_binding_contract: bool = False,
    mutate_assignment=None,
    mutate_graph=None,
    mutate_logic=None,
) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
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
    if expose_binding_contract:
        _expose_semantic_binding_contract(root)
    data = copy.deepcopy(_fixture(fixture_name))
    selected_logic_outcome = logic_outcome or data.get(
        "logic_outcome", "logic-captured"
    )
    if selected_logic_outcome == "logic-captured":
        for row in data["logic"]["enforcement_matrix"]:
            if "capability_gap" not in row:
                continue
            row["capability_verified"] = True
            row["exact_mechanism"]["enforcement_class"] = (
                "rutter-state-transition"
            )
            del row["capability_gap"]
    normative_ids = data.get("normative_obligations") or [
        row["obligation_id"] for row in data["logic"]["enforcement_matrix"]
    ]
    assignment_body = data.get("assignment") or {
        "assignments": [
            {
                "part_id": "part-main",
                "voyage_id": "voyage-main",
                "rutter_definition_id": "review",
                "charter_fields": ["artifact"],
                "input_ids": ["source"],
                "output_ids": ["result"],
                "inseparability": {
                    "status": "inseparable",
                    "reason": "The obligations share transition state.",
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
            "retry_owner": "review",
            "cancellation": [],
            "failure_propagation": [],
            "authorization": [],
            "release": [],
        },
    }
    if mutate_assignment is not None:
        mutate_assignment(assignment_body)
    if mutate_graph is not None:
        mutate_graph(data["graph"])
    if mutate_logic is not None:
        mutate_logic(data["logic"])

    source = root / "source.md"
    source.write_text("# Normative source\n", encoding="utf-8")
    workspace = root / "source_distillation"
    workspace.mkdir()
    context_rows = []
    for index, obligation_id in enumerate(normative_ids):
        context = source if index == 0 else root / f"normative-{index}.md"
        if index:
            context.write_text(
                f"# Normative obligation {obligation_id}\n",
                encoding="utf-8",
            )
        context_rows.append(
            {
                "obligation_id": obligation_id,
                "path": context.relative_to(root).as_posix(),
                "availability": "present",
                "digest": _sha256(context),
                "authority": "normative",
                "provenance": "source",
                "why_behavior_defining": "Defines required behavior.",
                "resolution": "resolved",
            }
        )
    breakdown = workspace / "01_breakdown.md"
    _write_artifact(
        breakdown,
        stage="breakdown",
        outcome="breakdown-ready",
        body_schema="breakdown/v1",
        prerequisite={
            "kind": "source",
            "path": source.name,
            "sha256": _sha256(source),
        },
        body={
            "context_closure": context_rows,
            "conflicts": [],
            "parts": [
                {
                    "part_id": "part-main",
                    "obligation_ids": normative_ids,
                    "independence": assignment_body["assignments"][0][
                        "inseparability"
                    ]["status"],
                    "reason": "The obligations share transition state.",
                }
            ],
        },
    )
    assignment = workspace / "02_rutter_assignment.md"
    _write_artifact(
        assignment,
        stage="assign-rutters",
        outcome="assignment-ready",
        body_schema="assignment/v1",
        prerequisite={
            "kind": "artifact",
            "path": breakdown.relative_to(root).as_posix(),
            "sha256": _sha256(breakdown),
            "stage": "breakdown",
            "schema_version": "distill-to-rutters/v1",
        },
        body=assignment_body,
    )

    graph = workspace / "03_evolutions_and_transitions.md"
    _write_artifact(
        graph,
        stage="extract-evolutions",
        outcome="graph-ready",
        body_schema="graph/v1",
        prerequisite={
            "kind": "artifact",
            "path": assignment.relative_to(root).as_posix(),
            "sha256": _sha256(assignment),
            "stage": "assign-rutters",
            "schema_version": "distill-to-rutters/v1",
        },
        body=data["graph"],
    )
    logic = workspace / "04_logic_validation.md"
    _write_artifact(
        logic,
        stage="validate-logic",
        outcome=selected_logic_outcome,
        body_schema="logic-validation/v1",
        prerequisite={
            "kind": "artifact",
            "path": graph.relative_to(root).as_posix(),
            "sha256": _sha256(graph),
            "stage": "extract-evolutions",
            "schema_version": "distill-to-rutters/v1",
        },
        body=data["logic"],
    )
    return graph, logic


def test_current_public_api_forces_actor_owned_capture_to_logic_gap(
    tmp_path: Path,
) -> None:
    """Operation vocabulary alone must not prove semantic enforcement bindings."""
    contract = _load_contract()
    graph, logic = _artifacts(tmp_path, "good.md")

    graph_result = contract.validate_artifact(graph, "extract-evolutions")
    logic_result = contract.validate_artifact(logic, "validate-logic")

    assert graph_result.valid is True, graph_result.errors
    assert logic_result.valid is True, logic_result.errors

    _, captured = _artifacts(
        tmp_path / "captured",
        "good.md",
        logic_outcome="logic-captured",
    )
    captured_result = contract.validate_artifact(captured, "validate-logic")

    assert captured_result.valid is False
    assert any(
        "does not expose semantic enforcement bindings" in error
        for error in captured_result.errors
    )


def test_versioned_public_binding_contract_allows_structural_capture(
    tmp_path: Path,
) -> None:
    """Capture is possible only when the public versioned contract proves bindings."""
    contract = _load_contract()
    graph, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
    )

    graph_result = contract.validate_artifact(graph, "extract-evolutions")
    logic_result = contract.validate_artifact(logic, "validate-logic")

    assert graph_result.valid is True, graph_result.errors
    assert logic_result.valid is True, logic_result.errors


@pytest.mark.parametrize(
    ("fixture_name", "expected_error", "expose_binding_contract"),
    (
        (
            "missing-validator.md",
            "requires validation operation and validator_ref",
            True,
        ),
        ("automated-judgment.md", "must use request-owner-decision", True),
        (
            "unavailable-capability.md",
            "is not a current public runtime capability",
            False,
        ),
    ),
)
def test_logic_captured_rejects_specific_semantic_gaps(
    tmp_path: Path,
    fixture_name: str,
    expected_error: str,
    expose_binding_contract: bool,
) -> None:
    """Each false success claim must fail for its named semantic gap."""
    contract = _load_contract()
    graph, logic = _artifacts(
        tmp_path,
        fixture_name,
        expose_binding_contract=expose_binding_contract,
    )

    graph_result = contract.validate_artifact(graph, "extract-evolutions")
    logic_result = contract.validate_artifact(logic, "validate-logic")

    assert graph_result.valid is True, graph_result.errors
    assert logic_result.valid is False
    assert any(expected_error in error for error in logic_result.errors)


def test_graph_ready_rejects_an_unowned_coordinator_obligation(
    tmp_path: Path,
) -> None:
    """Coordinator foreign-key gaps belong to graph validation, not logic prose."""
    contract = _load_contract()
    graph, _ = _artifacts(
        tmp_path,
        "unowned-coordinator.md",
        expose_binding_contract=True,
    )

    result = contract.validate_artifact(graph, "extract-evolutions")

    assert result.valid is False
    assert result.errors == (
        "assignment graph foreign-key closure has mismatched obligation ownership",
    )


def test_logic_captured_requires_exactly_one_row_for_every_graph_obligation(
    tmp_path: Path,
) -> None:
    """Dropping a normative graph obligation must make logic-captured impossible."""
    contract = _load_contract()

    def drop_human_obligation(logic: dict[str, Any]) -> None:
        logic["enforcement_matrix"] = logic["enforcement_matrix"][:1]

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_logic=drop_human_obligation,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        "enforcement matrix must cover graph obligations exactly" in error
        for error in result.errors
    )


def test_logic_captured_cannot_omit_an_obligation_from_graph_and_matrix(
    tmp_path: Path,
) -> None:
    """The approved normative closure, not the proposed graph, defines completeness."""
    contract = _load_contract()

    def drop_graph_obligation(graph: dict[str, Any]) -> None:
        rutter = graph["rutters"][0]
        rutter["evolutions"] = rutter["evolutions"][:1]
        rutter["transitions"] = rutter["transitions"][:2]

    def drop_logic_obligation(logic: dict[str, Any]) -> None:
        logic["enforcement_matrix"] = logic["enforcement_matrix"][:1]

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_graph=drop_graph_obligation,
        mutate_logic=drop_logic_obligation,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        "enforcement matrix must cover normative obligations exactly" in error
        for error in result.errors
    )


@pytest.mark.parametrize(
    "enforcement_class",
    ("prompt-only", "operation-name-only", "wrapper-constraint", "schema-only"),
)
def test_logic_captured_rejects_constraints_that_are_not_runtime_mechanisms(
    tmp_path: Path,
    enforcement_class: str,
) -> None:
    """Renaming prose or shape as enforcement must not authorize logic-captured."""
    contract = _load_contract()

    def replace_mechanism(logic: dict[str, Any]) -> None:
        logic["enforcement_matrix"][0]["exact_mechanism"][
            "enforcement_class"
        ] = enforcement_class

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_logic=replace_mechanism,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        f"enforcement class {enforcement_class} is not a public runtime mechanism"
        in error
        for error in result.errors
    )


def test_logic_captured_checks_operations_exposed_by_the_live_interface(
    tmp_path: Path,
) -> None:
    """A real interface name cannot legitimize an operation it does not expose."""
    contract = _load_contract()

    def invent_operation(logic: dict[str, Any]) -> None:
        logic["enforcement_matrix"][0]["exact_mechanism"]["validation"][
            "operation"
        ] = "submit"

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_logic=invent_operation,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        "operation submit is absent from rutter.interface.bound-operations@6"
        in error
        for error in result.errors
    )


def test_capability_check_uses_the_artifact_repository_public_api(
    tmp_path: Path,
) -> None:
    """A stale helper checkout cannot overrule the artifact repository inventory."""
    contract = _load_contract()
    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
    )
    public_blueprint = logic.parents[1] / "src/officina/rutter/blueprint.yaml"
    runtime = yaml.safe_load(public_blueprint.read_text(encoding="utf-8"))
    del runtime["exports"]["rutter.interface.bound-operations"]
    public_blueprint.write_text(
        yaml.safe_dump(runtime, sort_keys=False),
        encoding="utf-8",
    )

    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        "rutter.interface.bound-operations@6 is not a current public runtime capability"
        in error
        for error in result.errors
    )


def test_logic_gap_can_truthfully_record_a_non_enforceable_claim(
    tmp_path: Path,
) -> None:
    """A detected gap remains a valid non-advancing artifact, not a false success."""
    contract = _load_contract()
    graph, logic = _artifacts(
        tmp_path,
        "good.md",
    )

    graph_result = contract.validate_artifact(graph, "extract-evolutions")
    logic_result = contract.validate_artifact(logic, "validate-logic")

    assert graph_result.valid is True, graph_result.errors
    assert logic_result.valid is True, logic_result.errors


def test_truthful_gap_fixture_records_the_absent_public_contract() -> None:
    """The current-public fixture must not contradict its logic-gap outcome."""
    rows = _fixture("good.md")["logic"]["enforcement_matrix"]

    for row in rows:
        assert row["capability_verified"] is False
        assert (
            row["exact_mechanism"]["enforcement_class"]
            == "operation-name-only"
        )
        assert row["capability_gap"] == {
            "absent_binding_contract": (
                "rutter.interface.bound-operations semantic_enforcement@1"
            ),
            "exact_repair": (
                "Add semantic_enforcement version 1 to the public "
                "bound-operations contract with structural request, "
                "validation, transition, successor, and rejection-code "
                "bindings."
            ),
        }


def test_logic_captured_rejects_any_retained_capability_gap(
    tmp_path: Path,
) -> None:
    """A captured-success row cannot retain a declaration that it is a gap."""
    contract = _load_contract()

    def retain_gap(logic: dict[str, Any]) -> None:
        logic["enforcement_matrix"][0]["capability_gap"] = {
            "absent_binding_contract": "semantic_enforcement@1",
            "exact_repair": "Expose the missing public binding contract.",
        }

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_logic=retain_gap,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        "logic-captured cannot include capability_gap" in error
        for error in result.errors
    )


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (
        ("request-owner", "request owner llm does not match human"),
        (
            "evidence-ref",
            "request evidence_ref input.claim does not match public binding input.evidence",
        ),
        (
            "validator",
            "validator_ref validate_other does not match owning evolution validator validate_human_approval",
        ),
        (
            "transition-authority",
            "transition authority wrapper is not owning-rutter-evolution",
        ),
    ),
)
def test_actor_binding_components_are_checked_independently(
    tmp_path: Path,
    case: str,
    expected_error: str,
) -> None:
    """Each owner/evidence/validator/transition binding must fail independently."""
    contract = _load_contract()

    def break_binding(logic: dict[str, Any]) -> None:
        row = logic["enforcement_matrix"][1]
        if case == "request-owner":
            row["exact_mechanism"]["request"]["owner"] = "llm"
        elif case == "evidence-ref":
            row["exact_mechanism"]["request"]["evidence_ref"] = "input.claim"
        elif case == "validator":
            row["exact_mechanism"]["validation"][
                "validator_ref"
            ] = "validate_other"
        else:
            row["exact_mechanism"]["transition"]["authority"] = "wrapper"

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_logic=break_binding,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(expected_error in error for error in result.errors)


@pytest.mark.parametrize("actor", ("human", "llm", "external"))
def test_each_non_rutter_owner_is_preserved_by_the_public_binding_contract(
    tmp_path: Path,
    actor: str,
) -> None:
    """A shared branch must preserve each supported non-Rutter owner class."""
    contract = _load_contract()

    def set_graph_owner(graph: dict[str, Any]) -> None:
        graph["rutters"][0]["evolutions"][1]["decision_owner"] = actor

    def set_logic_owner(logic: dict[str, Any]) -> None:
        row = logic["enforcement_matrix"][1]
        row["original_decision_owner"] = actor
        row["exact_mechanism"]["request"]["owner"] = actor

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_graph=set_graph_owner,
        mutate_logic=set_logic_owner,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is True, result.errors


@pytest.mark.parametrize("actor", ("human", "llm", "external"))
def test_each_non_rutter_owner_class_rejects_missing_rutter_request(
    tmp_path: Path,
    actor: str,
) -> None:
    """Every non-Rutter owner class must retain its requested decision boundary."""
    contract = _load_contract()

    def set_graph_owner(graph: dict[str, Any]) -> None:
        graph["rutters"][0]["evolutions"][1]["decision_owner"] = actor

    def remove_actor_request(logic: dict[str, Any]) -> None:
        row = logic["enforcement_matrix"][1]
        row["original_decision_owner"] = actor
        row["exact_mechanism"]["request"] = None

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_graph=set_graph_owner,
        mutate_logic=remove_actor_request,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        f"{actor}-owned obligation obl-approval requires a Rutter request" in error
        for error in result.errors
    )


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (
        (
            "successor",
            "positive trace successor failed is not the approved successor complete",
        ),
        ("negative-kind", "negative trace must expect rejection"),
        (
            "observable-evidence",
            "observable evidence_ref input.claim does not match public binding input.evidence",
        ),
        (
            "result-ref",
            "positive trace result_ref input does not match public binding result",
        ),
        (
            "rejection-code",
            "negative trace rejection_code arbitrary-rejection is not declared by public validation binding",
        ),
    ),
)
def test_structured_traces_must_match_capability_and_graph(
    tmp_path: Path,
    case: str,
    expected_error: str,
) -> None:
    """Structured traces are consistency claims, not unconstrained prose."""
    contract = _load_contract()

    def break_trace(logic: dict[str, Any]) -> None:
        row = logic["enforcement_matrix"][1]
        if case == "successor":
            row["positive_trace"]["expected"]["state"] = "failed"
        elif case == "negative-kind":
            row["negative_trace"]["expected"] = {
                "kind": "successor",
                "state": "complete",
                "result_ref": "result",
            }
        elif case == "observable-evidence":
            row["observable_evidence"]["evidence_ref"] = "input.claim"
        elif case == "result-ref":
            row["positive_trace"]["expected"]["result_ref"] = "input"
        else:
            row["negative_trace"]["expected"] = {
                "kind": "rejection",
                "rejection_code": "arbitrary-rejection",
            }

    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_logic=break_trace,
    )
    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(expected_error in error for error in result.errors)


@pytest.mark.parametrize("path_kind", ("absolute", "parent"))
def test_public_source_blueprint_paths_reject_unconfined_lexical_paths(
    tmp_path: Path,
    path_kind: str,
) -> None:
    """A public export cannot redirect validation outside module-root syntax."""
    contract = _load_contract()
    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
    )
    public_root = logic.parents[1] / "src/officina/rutter"
    root_blueprint = public_root / "blueprint.yaml"
    document = yaml.safe_load(root_blueprint.read_text(encoding="utf-8"))
    locator = document["sources"]["rutter.source.engine"]["blueprint"]
    locator["path"] = (
        str((public_root / "blueprints/engine.yaml").resolve())
        if path_kind == "absolute"
        else "blueprints/../blueprints/engine.yaml"
    )
    root_blueprint.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        "module-root blueprint path must be relative without parent traversal"
        in error
        for error in result.errors
    )


def test_public_source_blueprint_path_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    """A relative locator cannot escape module root through a symlink."""
    contract = _load_contract()
    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
    )
    public_root = logic.parents[1] / "src/officina/rutter"
    outside = logic.parents[1] / "outside-engine.yaml"
    shutil.copy2(public_root / "blueprints/engine.yaml", outside)
    (public_root / "escape.yaml").symlink_to(outside)
    root_blueprint = public_root / "blueprint.yaml"
    document = yaml.safe_load(root_blueprint.read_text(encoding="utf-8"))
    document["sources"]["rutter.source.engine"]["blueprint"]["path"] = (
        "escape.yaml"
    )
    root_blueprint.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        "module-root blueprint path escapes public Rutter module root" in error
        for error in result.errors
    )


def test_public_root_blueprint_path_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    """The root public blueprint must be contained before any bytes are read."""
    contract = _load_contract()
    _, logic = _artifacts(
        tmp_path,
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
    )
    root_blueprint = logic.parents[1] / "src/officina/rutter/blueprint.yaml"
    outside = tmp_path / "outside-root-blueprint.yaml"
    shutil.copy2(root_blueprint, outside)
    root_blueprint.unlink()
    root_blueprint.symlink_to(outside)

    result = contract.validate_artifact(logic, "validate-logic")

    assert result.valid is False
    assert any(
        "public Rutter root blueprint escapes artifact repository" in error
        for error in result.errors
    )
