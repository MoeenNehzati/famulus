from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ROOT = Path(__file__).resolve().parent / "fixtures/scenarios"
PACKAGE_NAME = "_distill_to_rutters_rtx"
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

_ORACLE_SPEC = importlib.util.spec_from_file_location(
    "_distill_to_rutters_scenario_oracle",
    Path(__file__).resolve().parent / "_scenario_oracle.py",
)
assert _ORACLE_SPEC is not None and _ORACLE_SPEC.loader is not None
_ORACLE = importlib.util.module_from_spec(_ORACLE_SPEC)
_ORACLE_SPEC.loader.exec_module(_ORACLE)
evaluate_scenario = _ORACLE.evaluate_scenario
load_scenario_contract = _ORACLE.load_scenario_contract
ScenarioOracleError = _ORACLE.ScenarioOracleError


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


def _require_scenario_file(scenario: str, name: str) -> Path:
    path = SCENARIO_ROOT / scenario / name
    assert path.is_file(), f"scenario fixture is not implemented: {path}"
    return path


def _write_artifact(
    path: Path,
    *,
    stage: str,
    outcome: str,
    body_schema: str,
    prerequisites: list[dict[str, str]],
    body: dict[str, Any],
) -> None:
    envelope = {
        "schema_version": "distill-to-rutters/v1",
        "stage": stage,
        "outcome": outcome,
        "prerequisites": prerequisites,
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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_scenario_bundle(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        "# Test mutation\n\n```distill-scenario-contract\n"
        + yaml.safe_dump(document, sort_keys=False)
        + "```\n",
        encoding="utf-8",
    )


def test_layer_1_keeps_routing_fields_stage_order_and_honest_boundary() -> None:
    """Removing a gate or overstating fixture evidence must fail the contract."""
    gateway = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    verify = (SKILL_ROOT / "instructions/verify.md").read_text(encoding="utf-8")
    route_rows = re.findall(
        r"^\| `([^`]+)` \| `([^`]+)` \|$",
        gateway,
        flags=re.MULTILINE,
    )

    assert route_rows == [
        (stage, STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else "verify")
        for index, stage in enumerate(STAGE_ORDER[:-1])
    ]
    for field in (
        "artifact path",
        "expected stage",
        "user-approved digest",
        "explicit decision",
        "earliest_stale_prerequisite",
    ):
        assert field in gateway
    assert "Do not invoke a private filesystem implementation path" in " ".join(
        gateway.split()
    )

    normalized_verify = " ".join(verify.split())
    for statement in (
        "structural instruction contracts",
        "production artifact parser, digest chain, outcome registry, and route decision",
        "fixture-specific obligation and trace oracles",
        "do not execute a Rutter",
        "do not prove arbitrary semantic equivalence",
        "live agent/user comparison",
        "separate from pytest",
    ):
        assert statement in normalized_verify

    public_markdown = gateway + verify
    assert "_artifact_contract.py" not in public_markdown
    assert "_scenario_oracle.py" not in public_markdown


def test_root_blueprint_owns_the_task_1_through_5_acceptance_closure() -> None:
    """Dropping a helper, schema, fixture, or test from ownership must fail."""
    root = yaml.safe_load((SKILL_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))
    patterns = root["content"]
    required = {
        "tests/_scenario_oracle.py",
        "tests/test_artifact_contract.py",
        "tests/test_distill_to_rutters_routing.py",
        "tests/test_enforcement_contract.py",
        "tests/test_distillation_scenarios.py",
        *(
            f"references/{name}-body.schema.json"
            for name in (
                "breakdown",
                "assignment",
                "graph",
                "logic-validation",
                "implementation-design",
                "implementation-report",
                "entrypoint",
                "verification",
            )
        ),
        "references/artifact-envelope.schema.json",
        *(
            f"tests/fixtures/context-closure/{name}"
            for name in (
                "root.md",
                "chain.md",
                "cycle.md",
                "generated-normative.md",
                "conflict.md",
            )
        ),
        *(
            f"tests/fixtures/enforcement/{name}"
            for name in (
                "good.md",
                "missing-validator.md",
                "automated-judgment.md",
                "unowned-coordinator.md",
                "unavailable-capability.md",
            )
        ),
        *(
            f"tests/fixtures/scenarios/{scenario}/{name}"
            for scenario, names in {
                "inseparable": (
                    "source.md",
                    "good-contract.md",
                    "missing-validator.md",
                    "oracle.yaml",
                ),
                "multipart": (
                    "source.md",
                    "good-contract.md",
                    "missing-join.md",
                    "oracle.yaml",
                ),
                "judgment": (
                    "source.md",
                    "good-contract.md",
                    "automated-judgment.md",
                    "oracle.yaml",
                ),
            }.items()
            for name in names
        ),
    }

    assert {
        path
        for path in required
        if not any(re.fullmatch(pattern, path) for pattern in patterns)
    } == set()

    assert root["children"] == {"_rtx": {}}
    runtime = yaml.safe_load(
        (SKILL_ROOT / "_rtx/blueprint.yaml").read_text(encoding="utf-8")
    )
    runtime_patterns = runtime["content"]
    assert {
        path
        for path in (
            "__init__.py",
            "_artifact_contract.py",
            "_artifact_contract_interface.py",
        )
        if not any(
            re.fullmatch(pattern, path) for pattern in runtime_patterns
        )
    } == set()


def test_layer_2_exercises_parser_digest_chain_outcome_registry_and_route(
    tmp_path: Path,
) -> None:
    """A real scenario must bridge to accepted breakdown/assignment artifacts."""
    contract = _load_contract()
    source_fixture = _require_scenario_file("multipart", "source.md")
    scenario_fixture = _require_scenario_file("multipart", "good-contract.md")
    scenario = load_scenario_contract(scenario_fixture)

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    source = repository / "source.md"
    source.write_bytes(source_fixture.read_bytes())
    workspace = repository / "source_distillation"
    workspace.mkdir()
    context_closure = []
    parts = []
    assignment_rows = scenario["assignment"]["assignments"]
    for index, row in enumerate(assignment_rows):
        context = source if index == 0 else repository / f"part-{index}.md"
        if index:
            context.write_text(
                f"# Context for {row['part_id']}\n",
                encoding="utf-8",
            )
        obligation_id = f"obl-{row['part_id']}"
        context_closure.append(
            {
                "obligation_id": obligation_id,
                "path": context.relative_to(repository).as_posix(),
                "availability": "present",
                "digest": _digest(context),
                "authority": "normative",
                "provenance": "source",
                "why_behavior_defining": "Defines one assigned part.",
                "resolution": "resolved",
            }
        )
        parts.append(
            {
                "part_id": row["part_id"],
                "obligation_ids": [obligation_id],
                "independence": row["inseparability"]["status"],
                "reason": row["inseparability"]["reason"],
            }
        )
    breakdown = workspace / "01_breakdown.md"
    _write_artifact(
        breakdown,
        stage="breakdown",
        outcome="breakdown-ready",
        body_schema="breakdown/v1",
        prerequisites=[
            {"kind": "source", "path": "source.md", "sha256": _digest(source)}
        ],
        body={
            "context_closure": context_closure,
            "conflicts": [],
            "parts": parts,
        },
    )
    assignment = workspace / "02_rutter_assignment.md"
    _write_artifact(
        assignment,
        stage="assign-rutters",
        outcome="assignment-ready",
        body_schema="assignment/v1",
        prerequisites=[
            {
                "kind": "artifact",
                "path": breakdown.relative_to(repository).as_posix(),
                "sha256": _digest(breakdown),
                "stage": "breakdown",
                "schema_version": "distill-to-rutters/v1",
            }
        ],
        body=dict(scenario["assignment"]),
    )

    envelope = contract.parse_envelope(assignment)
    assert envelope.stage == "assign-rutters"
    assert envelope.outcome == "assignment-ready"
    assert contract.sha256_file(assignment) == _digest(assignment)
    assert contract.validate_artifact(assignment, "assign-rutters").valid is True
    assert contract.check_freshness(assignment).current is True
    accepted = contract.decide_route(
        "assign-rutters",
        "assignment-ready",
        _digest(assignment),
        "approve",
        assignment,
    )
    assert accepted.as_dict() == {
        "status": "accepted",
        "artifact_digest": _digest(assignment),
        "outcome": "assignment-ready",
        "authorized_route": "extract-evolutions",
        "earliest_stale_prerequisite": None,
    }


@pytest.mark.parametrize("scenario", ("inseparable", "multipart", "judgment"))
def test_layer_3_good_contracts_match_independent_fixture_oracles(
    scenario: str,
) -> None:
    """A changed obligation owner, validator, join, or trace must be observable."""
    contract = _require_scenario_file(scenario, "good-contract.md")
    oracle = _require_scenario_file(scenario, "oracle.yaml")

    assert evaluate_scenario(contract, oracle) == ()


@pytest.mark.parametrize(
    ("scenario", "mutation", "expected_findings"),
    (
        (
            "inseparable",
            "missing-validator.md",
            (
                "obligation obl-review.logic_validator expected "
                "'validate_review', found missing",
            ),
        ),
        (
            "multipart",
            "missing-join.md",
            ("orchestration.joins is missing obligation obl-join",),
        ),
        (
            "judgment",
            "automated-judgment.md",
            (
                "obligation obl-approval.automation_permission expected 'request-owner-decision', found 'deterministic'",
                (
                    "obligation obl-approval.request_owner expected "
                    "'human', found missing"
                ),
            ),
        ),
    ),
)
def test_layer_3_mutations_fail_for_the_named_fixture_fact(
    scenario: str,
    mutation: str,
    expected_findings: tuple[str, ...],
) -> None:
    """Mutation expectations are literal and are not derived by production code."""
    contract = _require_scenario_file(scenario, mutation)
    oracle = _require_scenario_file(scenario, "oracle.yaml")

    findings = evaluate_scenario(contract, oracle)

    assert findings == expected_findings


def test_layer_3_rejects_an_unexpected_assignment(tmp_path: Path) -> None:
    """An extra assignment cannot hide outside the expected assignment set."""
    good = load_scenario_contract(
        _require_scenario_file("inseparable", "good-contract.md")
    )
    mutated = copy.deepcopy(good)
    extra = copy.deepcopy(mutated["assignment"]["assignments"][0])
    extra.update({"part_id": "part-extra", "voyage_id": "voyage-extra"})
    mutated["assignment"]["assignments"].append(extra)
    path = tmp_path / "extra-assignment.md"
    _write_scenario_bundle(path, mutated)

    assert evaluate_scenario(
        path,
        _require_scenario_file("inseparable", "oracle.yaml"),
    ) == ("assignments unexpected=['part-extra']",)


def test_layer_3_rejects_an_unexpected_obligation(tmp_path: Path) -> None:
    """A graph/matrix pair cannot add an obligation absent from the oracle."""
    good = load_scenario_contract(
        _require_scenario_file("inseparable", "good-contract.md")
    )
    mutated = copy.deepcopy(good)
    rutter = mutated["graph"]["rutters"][0]
    evolution = copy.deepcopy(rutter["evolutions"][0])
    evolution.update(
        {
            "evolution_id": "extra",
            "obligation_ids": ["obl-extra"],
            "validator": "validate_extra",
        }
    )
    rutter["evolutions"].append(evolution)
    rutter["transitions"].extend(
        [
            {"from": "extra", "outcome": "accepted", "to": "complete"},
            {"from": "extra", "outcome": "malformed", "to": "failed"},
        ]
    )
    row = copy.deepcopy(mutated["logic"]["enforcement_matrix"][0])
    row["obligation_id"] = "obl-extra"
    row["owning_evolution"] = "review/extra"
    row["exact_mechanism"]["validation"]["validator_ref"] = "validate_extra"
    mutated["logic"]["enforcement_matrix"].append(row)
    path = tmp_path / "extra-obligation.md"
    _write_scenario_bundle(path, mutated)

    assert evaluate_scenario(
        path,
        _require_scenario_file("inseparable", "oracle.yaml"),
    ) == (
        "assignment part-review graph evolutions expected ['inspect'], "
        "found ['extra', 'inspect']",
        "Rutter review evolutions expected ['inspect'], found ['extra', 'inspect']",
        "graph obligations unexpected=['obl-extra']",
        "logic obligations unexpected=['obl-extra']",
    )


def test_layer_3_requires_reciprocal_independent_workflows(
    tmp_path: Path,
) -> None:
    """Deleting one worker's reciprocal declaration must invalidate multipart."""
    good = load_scenario_contract(
        _require_scenario_file("multipart", "good-contract.md")
    )
    mutated = copy.deepcopy(good)
    mutated["assignment"]["assignments"][1]["independent_workflows"] = []
    path = tmp_path / "one-way-workflows.md"
    _write_scenario_bundle(path, mutated)

    assert evaluate_scenario(
        path,
        _require_scenario_file("multipart", "oracle.yaml"),
    ) == (
        "assignment part-right.independent_workflows expected "
        "{'voyage-left': 'coordinate'}, found {}",
    )


COORDINATOR_GROUPS = (
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


def test_layer_3_requires_exact_coordinator_rule_groups(tmp_path: Path) -> None:
    """Every coordinator group must contain exactly its hand-authored row."""
    good = load_scenario_contract(
        _require_scenario_file("multipart", "good-contract.md")
    )
    missing = copy.deepcopy(good)
    unexpected = copy.deepcopy(good)
    for group in COORDINATOR_GROUPS:
        missing["assignment"]["orchestration"][group] = []
        unexpected["assignment"]["orchestration"][group].append(
            {
                "obligation_id": "obl-orphan",
                "owning_transition": "coordinate",
                "evidence": "This row has no fixture-oracle obligation.",
            }
        )

    oracle = _require_scenario_file("multipart", "oracle.yaml")
    labeled_findings = []
    for label, document in (("missing", missing), ("unexpected", unexpected)):
        path = tmp_path / f"{label}-coordinator-groups.md"
        _write_scenario_bundle(path, document)
        findings = evaluate_scenario(path, oracle)
        labeled_findings.extend(
            (f"{label}-{group}", finding)
            for group, finding in zip(COORDINATOR_GROUPS, findings, strict=True)
        )

    assert tuple(labeled_findings) == tuple(
        [
            (
                f"missing-{group}",
                f"orchestration.{group} is missing obligation obl-join",
            )
            for group in COORDINATOR_GROUPS
        ]
        + [
            (
                f"unexpected-{group}",
                f"orchestration.{group} has unexpected obligations ['obl-orphan']",
            )
            for group in COORDINATOR_GROUPS
        ]
    )


def test_layer_3_rejects_a_coordinator_rule_mismatch(tmp_path: Path) -> None:
    """A present row with the wrong transition is not coordinator ownership."""
    good = load_scenario_contract(
        _require_scenario_file("multipart", "good-contract.md")
    )
    mutated = copy.deepcopy(good)
    mutated["assignment"]["orchestration"]["dependencies"][0][
        "owning_transition"
    ] = "dispenser-join"
    path = tmp_path / "wrong-coordinator-owner.md"
    _write_scenario_bundle(path, mutated)

    assert evaluate_scenario(
        path,
        _require_scenario_file("multipart", "oracle.yaml"),
    ) == (
        "orchestration.dependencies.obl-join.owning_transition expected "
        "'coordinate', found 'dispenser-join'",
    )


def test_layer_3_closes_assignment_rutters_against_graph_structure(
    tmp_path: Path,
) -> None:
    """Assignments cannot name a Rutter absent from the graph bundle."""
    good = load_scenario_contract(
        _require_scenario_file("multipart", "good-contract.md")
    )
    mutated = copy.deepcopy(good)
    worker = next(
        (
            rutter
            for rutter in mutated["graph"]["rutters"]
            if rutter["rutter_id"] == "fetch-worker"
        ),
        None,
    )
    assert worker is not None, "positive multipart fixture must define fetch-worker"
    worker["rutter_id"] = "renamed-worker"
    path = tmp_path / "unclosed-rutter.md"
    _write_scenario_bundle(path, mutated)

    assert evaluate_scenario(
        path,
        _require_scenario_file("multipart", "oracle.yaml"),
    ) == (
        "assignment part-left rutter_definition_id 'fetch-worker' has no graph Rutter",
        "assignment part-right rutter_definition_id 'fetch-worker' has no graph Rutter",
        "Rutter fetch-worker is missing from graph",
        "obligation obl-fetch.owning_evolution expected 'fetch-worker/fetch', "
        "found 'renamed-worker/fetch'",
    )


def test_layer_3_rejects_self_asserted_verified_capture(tmp_path: Path) -> None:
    """Artifact strings cannot convert the absent public contract into proof."""
    good = load_scenario_contract(
        _require_scenario_file("inseparable", "good-contract.md")
    )
    mutated = copy.deepcopy(good)
    row = mutated["logic"]["enforcement_matrix"][0]
    row["capability_verified"] = True
    row["exact_mechanism"]["enforcement_class"] = "rutter-state-transition"
    del row["capability_gap"]
    path = tmp_path / "self-asserted-capture.md"
    _write_scenario_bundle(path, mutated)

    assert evaluate_scenario(
        path,
        _require_scenario_file("inseparable", "oracle.yaml"),
    ) == (
        "obligation obl-review.capability_verified expected False, found True",
        "obligation obl-review.enforcement_class expected 'operation-name-only', "
        "found 'rutter-state-transition'",
        "obligation obl-review.absent_binding_contract expected "
        "'rutter.interface.bound-operations semantic_enforcement@1', found missing",
        "obligation obl-review.exact_repair expected "
        "'Expose versioned request, validation, transition, successor, and "
        "rejection-code bindings.', found missing",
    )


@pytest.mark.parametrize(
    ("blocks", "expected"),
    (
        (0, "scenario fixture must contain exactly one contract block, found 0"),
        (2, "scenario fixture must contain exactly one contract block, found 2"),
    ),
)
def test_fixture_parser_requires_exactly_one_contract_block(
    tmp_path: Path,
    blocks: int,
    expected: str,
) -> None:
    """Zero or duplicate blocks cannot silently select fixture facts."""
    block = "```distill-scenario-contract\nscenario: empty\n```\n"
    path = tmp_path / f"{blocks}-blocks.md"
    path.write_text("# Scenario\n\n" + block * blocks, encoding="utf-8")

    with pytest.raises(ScenarioOracleError, match=re.escape(expected)):
        load_scenario_contract(path)


def test_scenario_bundles_use_the_real_stage_body_contracts() -> None:
    """Fixture bundles use production schemas but are not represented as artifacts."""
    scenario_cases = (
        ("inseparable-good", "inseparable", "good-contract.md"),
        ("inseparable-missing-validator", "inseparable", "missing-validator.md"),
        ("multipart-good", "multipart", "good-contract.md"),
        ("multipart-missing-join", "multipart", "missing-join.md"),
        ("judgment-good", "judgment", "good-contract.md"),
        ("judgment-automated", "judgment", "automated-judgment.md"),
    )
    schema_files = {
        "assignment": "assignment-body.schema.json",
        "graph": "graph-body.schema.json",
        "logic": "logic-validation-body.schema.json",
    }
    validators = {
        contract_name: jsonschema.Draft202012Validator(
            json.loads(
                (SKILL_ROOT / "references" / schema_file).read_text(encoding="utf-8")
            )
        )
        for contract_name, schema_file in schema_files.items()
    }

    for label, scenario, name in scenario_cases:
        document = load_scenario_contract(_require_scenario_file(scenario, name))
        for contract_name, validator in validators.items():
            errors = sorted(
                validator.iter_errors(document[contract_name]),
                key=lambda error: tuple(str(part) for part in error.absolute_path),
            )
            assert errors == [], (label, contract_name, errors)
