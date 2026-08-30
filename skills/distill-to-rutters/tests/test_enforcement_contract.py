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
_ARTIFACT_RE = re.compile(
    r"\A```yaml\s*\n(?P<envelope>.*?)\n```\s*\n\s*"
    r"```distill-contract\s*\n(?P<body>.*?)\n```\s*\Z",
    re.DOTALL,
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


def _read_artifact(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    match = _ARTIFACT_RE.match(path.read_text(encoding="utf-8"))
    assert match is not None
    envelope = yaml.safe_load(match.group("envelope"))
    body = yaml.safe_load(match.group("body"))
    assert isinstance(envelope, dict)
    assert isinstance(body, dict)
    return envelope, body


def _rewrite_artifact(
    path: Path,
    envelope: dict[str, Any],
    body: dict[str, Any],
) -> None:
    prerequisites = envelope["prerequisites"]
    assert isinstance(prerequisites, list) and len(prerequisites) == 1
    _write_artifact(
        path,
        stage=envelope["stage"],
        outcome=envelope["outcome"],
        body_schema=envelope["body_schema"],
        prerequisite=prerequisites[0],
        body=body,
    )


def _expose_semantic_binding_contract(repository: Path) -> None:
    engine = repository / "src/officina/rutter/blueprints/engine.yaml"
    document = yaml.safe_load(engine.read_text(encoding="utf-8"))
    interface = document["interfaces"][
        "rutter.source.engine.interface.bound-operations"
    ]
    interface["contract"]["semantic_enforcement"] = _semantic_binding_contract()
    engine.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _semantic_binding_contract() -> dict[str, Any]:
    return {
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


def _captured_bodies(
    fixture_name: str = "good.md",
) -> tuple[dict[str, Any], dict[str, Any]]:
    data = copy.deepcopy(_fixture(fixture_name))
    for row in data["logic"]["enforcement_matrix"]:
        if "capability_gap" not in row:
            continue
        row["capability_verified"] = True
        row["exact_mechanism"]["enforcement_class"] = "rutter-state-transition"
        del row["capability_gap"]
    return data["graph"], data["logic"]


def _assignment_body() -> dict[str, Any]:
    return {
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


def _validate_capture_body(
    monkeypatch: pytest.MonkeyPatch,
    *,
    graph: dict[str, Any],
    logic: dict[str, Any],
) -> None:
    """Exercise semantic capture after authoritative schema validation."""
    contract = _load_contract()
    session = contract._SnapshotSession(REPOSITORY_ROOT)
    assert (
        contract._schema_errors(
            graph,
            "graph-body.schema.json",
            session,
            "extract-evolutions",
        )
        == ()
    )
    assert (
        contract._schema_errors(
            logic,
            "logic-validation-body.schema.json",
            session,
            "validate-logic",
        )
        == ()
    )
    normative_ids = {row["obligation_id"] for row in logic["enforcement_matrix"]}
    monkeypatch.setattr(
        contract,
        "_logic_inputs",
        lambda *_args: (graph, _assignment_body(), normative_ids, []),
    )
    monkeypatch.setattr(
        contract,
        "_public_runtime_contract",
        lambda *_args, **_kwargs: (
            frozenset({"get-status", "validate", "advance"}),
            _semantic_binding_contract(),
        ),
    )
    envelope = contract.ArtifactEnvelope(
        schema_version="distill-to-rutters/v1",
        stage="validate-logic",
        outcome="logic-captured",
        prerequisites=(),
        body_schema="logic-validation/v1",
    )
    contract._validate_logic_capture(
        REPOSITORY_ROOT,
        envelope,
        logic,
        session,
    )


def _minimal_public_runtime_tree(root: Path) -> Path:
    repository = root / "repo"
    module = repository / "src/officina/rutter"
    (module / "blueprints").mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / "src/officina/rutter/blueprint.yaml",
        module / "blueprint.yaml",
    )
    shutil.copy2(
        REPOSITORY_ROOT / "src/officina/rutter/blueprints/engine.yaml",
        module / "blueprints/engine.yaml",
    )
    return repository


@pytest.fixture(scope="session")
def repository_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("distill-enforcement-repository-template")
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
    return root


@pytest.fixture(scope="session")
def artifact_templates(
    tmp_path_factory: pytest.TempPathFactory,
    repository_template: Path,
):
    root = tmp_path_factory.mktemp("distill-enforcement-artifact-templates")
    templates: dict[tuple[str, str | None, bool], Path] = {}

    def build(
        fixture_name: str,
        *,
        logic_outcome: str | None,
        expose_binding_contract: bool,
    ) -> Path:
        key = (fixture_name, logic_outcome, expose_binding_contract)
        if key not in templates:
            template = root / f"repo-{len(templates)}"
            shutil.copytree(
                repository_template,
                template,
                copy_function=shutil.copy2,
                symlinks=True,
            )
            _populate_artifacts(
                template,
                fixture_name,
                logic_outcome=logic_outcome,
                expose_binding_contract=expose_binding_contract,
            )
            templates[key] = template
        return templates[key]

    return build


@pytest.fixture
def artifacts(tmp_path: Path, artifact_templates):
    counter = 0

    def build(
        fixture_name: str,
        *,
        logic_outcome: str | None = None,
        expose_binding_contract: bool = False,
        mutate_logic=None,
    ) -> tuple[Path, Path]:
        nonlocal counter
        root = tmp_path / f"repo-{counter}"
        counter += 1
        template = artifact_templates(
            fixture_name,
            logic_outcome=logic_outcome,
            expose_binding_contract=expose_binding_contract,
        )
        shutil.copytree(
            template,
            root,
            copy_function=shutil.copy2,
            symlinks=True,
        )
        graph = root / "source_distillation/03_evolutions_and_transitions.md"
        logic = root / "source_distillation/04_logic_validation.md"

        if mutate_logic is not None:
            logic_envelope, logic_body = _read_artifact(logic)
            mutate_logic(logic_body)
            _rewrite_artifact(logic, logic_envelope, logic_body)

        return graph, logic

    return build


def _populate_artifacts(
    root: Path,
    fixture_name: str,
    *,
    logic_outcome: str | None = None,
    expose_binding_contract: bool = False,
) -> tuple[Path, Path]:
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
            row["exact_mechanism"]["enforcement_class"] = "rutter-state-transition"
            del row["capability_gap"]
    normative_ids = data.get("normative_obligations") or [
        row["obligation_id"] for row in data["logic"]["enforcement_matrix"]
    ]
    assignment_body = data.get("assignment") or _assignment_body()

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
                    "independence": assignment_body["assignments"][0]["inseparability"][
                        "status"
                    ],
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
    artifacts,
) -> None:
    """Operation vocabulary alone must not prove semantic enforcement bindings."""
    contract = _load_contract()
    graph, logic = artifacts("good.md")

    graph_result = contract.validate_artifact(graph, "extract-evolutions")
    logic_result = contract.validate_artifact(logic, "validate-logic")

    assert graph_result.valid is True, (
        "current-public-api-graph",
        graph_result.errors,
    )
    assert logic_result.valid is True, (
        "current-public-api-logic-gap",
        "truthful-non-enforceable-claim",
        logic_result.errors,
    )

    _, captured = artifacts(
        "good.md",
        logic_outcome="logic-captured",
    )
    captured_result = contract.validate_artifact(captured, "validate-logic")

    assert captured_result.valid is False, "current-public-api-captured-refusal"
    assert any(
        "does not expose semantic enforcement bindings" in error
        for error in captured_result.errors
    )


def test_public_binding_contract_scenarios_remain_isolated(
    artifacts,
) -> None:
    """Public success, operation, and export scenarios keep adapter ownership."""
    contract = _load_contract()

    _, success = artifacts(
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
    )
    success_result = contract.validate_artifact(success, "validate-logic")
    assert success_result.valid is True, (
        "versioned-public-binding-contract",
        success_result.errors,
    )

    def invent_operation(logic: dict[str, Any]) -> None:
        logic["enforcement_matrix"][0]["exact_mechanism"]["validation"][
            "operation"
        ] = "submit"

    _, invented = artifacts(
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
        mutate_logic=invent_operation,
    )
    invented_result = contract.validate_artifact(invented, "validate-logic")
    assert invented_result.valid is False, "live-interface-operation"
    assert any(
        "operation submit is absent from rutter.interface.bound-operations@6" in error
        for error in invented_result.errors
    ), "live-interface-operation"

    _, missing_export = artifacts(
        "good.md",
        logic_outcome="logic-captured",
        expose_binding_contract=True,
    )
    public_blueprint = missing_export.parents[1] / "src/officina/rutter/blueprint.yaml"
    runtime = yaml.safe_load(public_blueprint.read_text(encoding="utf-8"))
    del runtime["exports"]["rutter.interface.bound-operations"]
    public_blueprint.write_text(
        yaml.safe_dump(runtime, sort_keys=False),
        encoding="utf-8",
    )
    export_result = contract.validate_artifact(missing_export, "validate-logic")
    assert export_result.valid is False, "artifact-repository-public-api"
    assert any(
        "rutter.interface.bound-operations@6 is not a current public runtime capability"
        in error
        for error in export_result.errors
    ), "artifact-repository-public-api"


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
    artifacts,
    fixture_name: str,
    expected_error: str,
    expose_binding_contract: bool,
) -> None:
    """Each false success claim must fail for its named semantic gap."""
    contract = _load_contract()
    graph, logic = artifacts(
        fixture_name,
        expose_binding_contract=expose_binding_contract,
    )

    graph_result = contract.validate_artifact(graph, "extract-evolutions")
    logic_result = contract.validate_artifact(logic, "validate-logic")

    assert graph_result.valid is True, graph_result.errors
    assert logic_result.valid is False
    assert any(expected_error in error for error in logic_result.errors)


def test_graph_ready_rejects_an_unowned_coordinator_obligation(
    artifacts,
) -> None:
    """Coordinator foreign-key gaps belong to graph validation, not logic prose."""
    contract = _load_contract()
    graph, _ = artifacts(
        "unowned-coordinator.md",
        expose_binding_contract=True,
    )

    result = contract.validate_artifact(graph, "extract-evolutions")

    assert result.valid is False
    assert result.errors == (
        "assignment graph foreign-key closure has mismatched obligation ownership",
    )


def test_logic_captured_requires_exactly_one_row_for_every_graph_obligation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping a normative graph obligation must make logic-captured impossible."""
    graph, logic = _captured_bodies()
    logic["enforcement_matrix"] = logic["enforcement_matrix"][:1]
    contract = _load_contract()
    with pytest.raises(contract.ArtifactContractError) as raised:
        _validate_capture_body(monkeypatch, graph=graph, logic=logic)
    assert any(
        "enforcement matrix must cover graph obligations exactly" in error
        for error in (str(raised.value),)
    )


def test_logic_captured_cannot_omit_an_obligation_from_graph_and_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The approved normative closure, not the proposed graph, defines completeness."""
    graph, logic = _captured_bodies()
    rutter = graph["rutters"][0]
    rutter["evolutions"] = rutter["evolutions"][:1]
    rutter["transitions"] = rutter["transitions"][:2]
    logic["enforcement_matrix"] = logic["enforcement_matrix"][:1]
    contract = _load_contract()
    session = contract._SnapshotSession(REPOSITORY_ROOT)
    assert (
        contract._schema_errors(
            graph, "graph-body.schema.json", session, "extract-evolutions"
        )
        == ()
    )
    assert (
        contract._schema_errors(
            logic, "logic-validation-body.schema.json", session, "validate-logic"
        )
        == ()
    )
    monkeypatch.setattr(
        contract,
        "_logic_inputs",
        lambda *_args: (
            graph,
            _assignment_body(),
            {"obl-deterministic", "obl-approval"},
            [],
        ),
    )
    monkeypatch.setattr(
        contract,
        "_public_runtime_contract",
        lambda *_args, **_kwargs: (
            frozenset({"get-status", "validate", "advance"}),
            _semantic_binding_contract(),
        ),
    )
    envelope = contract.ArtifactEnvelope(
        "distill-to-rutters/v1",
        "validate-logic",
        "logic-captured",
        (),
        "logic-validation/v1",
    )
    with pytest.raises(contract.ArtifactContractError) as raised:
        contract._validate_logic_capture(REPOSITORY_ROOT, envelope, logic, session)
    assert any(
        "enforcement matrix must cover normative obligations exactly" in error
        for error in (str(raised.value),)
    )


def test_logic_captured_rejects_constraints_that_are_not_runtime_mechanisms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renaming prose or shape as enforcement must not authorize logic-captured."""
    for enforcement_class in (
        "prompt-only",
        "operation-name-only",
        "wrapper-constraint",
        "schema-only",
    ):

        graph, logic = _captured_bodies()
        logic["enforcement_matrix"][0]["exact_mechanism"][
            "enforcement_class"
        ] = enforcement_class
        contract = _load_contract()
        with pytest.raises(contract.ArtifactContractError) as raised:
            _validate_capture_body(monkeypatch, graph=graph, logic=logic)
        assert any(
            f"enforcement class {enforcement_class} is not a public runtime mechanism"
            in error
            for error in (str(raised.value),)
        ), enforcement_class


def test_truthful_gap_fixture_records_the_absent_public_contract() -> None:
    """The current-public fixture must not contradict its logic-gap outcome."""
    rows = _fixture("good.md")["logic"]["enforcement_matrix"]

    for row in rows:
        assert row["capability_verified"] is False
        assert row["exact_mechanism"]["enforcement_class"] == "operation-name-only"
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A captured-success row cannot retain a declaration that it is a gap."""
    graph, logic = _captured_bodies()
    logic["enforcement_matrix"][0]["capability_gap"] = {
        "absent_binding_contract": "semantic_enforcement@1",
        "exact_repair": "Expose the missing public binding contract.",
    }
    contract = _load_contract()
    with pytest.raises(contract.ArtifactContractError) as raised:
        _validate_capture_body(monkeypatch, graph=graph, logic=logic)
    assert any(
        "logic-captured cannot include capability_gap" in error
        for error in (str(raised.value),)
    )


def test_actor_binding_components_are_checked_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each owner/evidence/validator/transition binding must fail independently."""
    cases = (
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
    )
    for case, expected_error in cases:

        graph, logic = _captured_bodies()
        row = logic["enforcement_matrix"][1]
        if case == "request-owner":
            row["exact_mechanism"]["request"]["owner"] = "llm"
        elif case == "evidence-ref":
            row["exact_mechanism"]["request"]["evidence_ref"] = "input.claim"
        elif case == "validator":
            row["exact_mechanism"]["validation"]["validator_ref"] = "validate_other"
        else:
            row["exact_mechanism"]["transition"]["authority"] = "wrapper"
        contract = _load_contract()
        with pytest.raises(contract.ArtifactContractError) as raised:
            _validate_capture_body(monkeypatch, graph=graph, logic=logic)
        assert expected_error in str(raised.value), case


def test_each_non_rutter_owner_is_preserved_by_the_public_binding_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared branch must preserve each supported non-Rutter owner class."""
    for actor in ("human", "llm", "external"):
        graph, logic = _captured_bodies()
        graph["rutters"][0]["evolutions"][1]["decision_owner"] = actor
        row = logic["enforcement_matrix"][1]
        row["original_decision_owner"] = actor
        row["exact_mechanism"]["request"]["owner"] = actor
        _validate_capture_body(monkeypatch, graph=graph, logic=logic)


def test_each_non_rutter_owner_class_rejects_missing_rutter_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every non-Rutter owner class must retain its requested decision boundary."""
    for actor in ("human", "llm", "external"):
        graph, logic = _captured_bodies()
        graph["rutters"][0]["evolutions"][1]["decision_owner"] = actor
        row = logic["enforcement_matrix"][1]
        row["original_decision_owner"] = actor
        row["exact_mechanism"]["request"] = None
        contract = _load_contract()
        with pytest.raises(contract.ArtifactContractError) as raised:
            _validate_capture_body(monkeypatch, graph=graph, logic=logic)
        assert any(
            f"{actor}-owned obligation obl-approval requires a Rutter request" in error
            for error in (str(raised.value),)
        ), actor


def test_structured_traces_must_match_capability_and_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured traces are consistency claims, not unconstrained prose."""
    cases = (
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
    )
    for case, expected_error in cases:

        graph, logic = _captured_bodies()
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
        contract = _load_contract()
        with pytest.raises(contract.ArtifactContractError) as raised:
            _validate_capture_body(monkeypatch, graph=graph, logic=logic)
        assert expected_error in str(raised.value), case


def test_public_source_blueprint_paths_reject_unconfined_lexical_paths(
    tmp_path: Path,
) -> None:
    """A public export cannot redirect validation outside module-root syntax."""
    contract = _load_contract()
    for path_kind in ("absolute", "parent"):
        repository = _minimal_public_runtime_tree(tmp_path / path_kind)
        public_root = repository / "src/officina/rutter"
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

        with pytest.raises(contract.ArtifactContractError) as raised:
            contract._public_runtime_contract(
                repository,
                "rutter.interface.bound-operations",
                6,
            )
        assert any(
            "module-root blueprint path must be relative without parent traversal"
            in error
            for error in (str(raised.value),)
        ), path_kind


def test_public_source_blueprint_path_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    """A relative locator cannot escape module root through a symlink."""
    contract = _load_contract()
    repository = _minimal_public_runtime_tree(tmp_path)
    public_root = repository / "src/officina/rutter"
    outside = repository / "outside-engine.yaml"
    shutil.copy2(public_root / "blueprints/engine.yaml", outside)
    (public_root / "escape.yaml").symlink_to(outside)
    root_blueprint = public_root / "blueprint.yaml"
    document = yaml.safe_load(root_blueprint.read_text(encoding="utf-8"))
    document["sources"]["rutter.source.engine"]["blueprint"]["path"] = "escape.yaml"
    root_blueprint.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(contract.ArtifactContractError) as raised:
        contract._public_runtime_contract(
            repository,
            "rutter.interface.bound-operations",
            6,
        )
    assert any(
        "module-root blueprint path escapes public Rutter module root" in error
        for error in (str(raised.value),)
    )


def test_public_root_blueprint_path_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    """The root public blueprint must be contained before any bytes are read."""
    contract = _load_contract()
    repository = _minimal_public_runtime_tree(tmp_path / "inside")
    root_blueprint = repository / "src/officina/rutter/blueprint.yaml"
    outside = tmp_path / "outside-root-blueprint.yaml"
    shutil.copy2(root_blueprint, outside)
    root_blueprint.unlink()
    root_blueprint.symlink_to(outside)

    with pytest.raises(contract.ArtifactContractError) as raised:
        contract._public_runtime_contract(
            repository,
            "rutter.interface.bound-operations",
            6,
        )
    assert any(
        "public Rutter root blueprint escapes artifact repository" in error
        for error in (str(raised.value),)
    )
