import json
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]

STAGES = {
    "breakdown": "01_breakdown.md",
    "assign-rutters": "02_rutter_assignment.md",
    "extract-evolutions": "03_evolutions_and_transitions.md",
    "validate-logic": "04_logic_validation.md",
    "design-implementation": "05_implementation_design.md",
    "implement": "06_implementation_report.md",
    "finalize": "07_entrypoint.md",
    "verify": "08_verification.md",
}

FIXED_ARTIFACTS = tuple(STAGES.items())

STAGE_OUTCOMES = {
    "breakdown": {"breakdown-ready", "breakdown-gap", "partial", "failed"},
    "assign-rutters": {"assignment-ready", "assignment-gap", "partial", "failed"},
    "extract-evolutions": {"graph-ready", "graph-gap", "partial", "failed"},
    "validate-logic": {"logic-captured", "logic-gap", "partial", "failed"},
    "design-implementation": {
        "design-ready",
        "design-gap",
        "design-blocked",
        "partial",
        "failed",
    },
    "implement": {
        "implemented",
        "implementation-gap",
        "implementation-blocked",
        "partial",
        "failed",
    },
    "finalize": {"entrypoint-ready", "entrypoint-gap", "partial", "failed"},
    "verify": {
        "verified",
        "verification-failed",
        "verification-blocked",
        "partial",
    },
}

ARTIFACT_INTERFACE = {
    "interface": "distill-to-rutters._rtx.interface.validate-and-route",
    "version": 1,
}


def _load_yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _source(root: dict, source_id: str) -> dict:
    locator = root["sources"][source_id]["blueprint"]
    assert locator["base"] == "module-root"
    source = _load_yaml(SKILL_ROOT / locator["path"])
    assert source["id"] == source_id
    return source


def test_module_exposes_one_gateway_and_registers_each_stage() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")

    assert root["schema_version"] == 6
    assert root["id"] == "distill-to-rutters"
    assert root["children"] == {"_rtx": {}}
    assert root["namespace_exports"]["_rtx"]["version"] == 1
    assert root["namespace_exports"]["_rtx"]["surface"]["only"] == {
        ARTIFACT_INTERFACE["interface"]: 1
    }
    assert root["namespace_exports"]["_rtx"]["interface_access"][
        ARTIFACT_INTERFACE["interface"]
    ] == {"allow_all_modules": False, "allowed_callers": ["distill-to-rutters"]}
    assert set(root["exports"]) == {"distill-to-rutters.interface.default"}
    assert set(root["sources"]) == {
        "distill-to-rutters.source.gateway",
        *(f"distill-to-rutters.source.{stage}" for stage in STAGES),
    }


def test_gateway_routes_to_every_stage_without_exporting_stage_interfaces() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    gateway = _source(root, "distill-to-rutters.source.gateway")
    interface = gateway["interfaces"][
        "distill-to-rutters.source.gateway.interface.default"
    ]

    assert gateway["gateway"] == {"language": "Markdown", "path": "SKILL.md"}
    assert gateway["content"] == [r"SKILL\.md"]
    expected_uses = [ARTIFACT_INTERFACE] + [
        {
            "interface": f"distill-to-rutters.source.{stage}.interface.{stage}",
            "version": 1,
        }
        for stage in STAGES
    ]
    assert gateway["uses_interfaces"] == expected_uses
    assert interface["uses_interfaces"] == expected_uses
    assert interface["contract"]["interaction"]["unattended_outcome"] == (
        "approval-required"
    )


def test_each_stage_owns_one_markdown_interface_and_approval_gate() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")

    for stage, artifact in STAGES.items():
        source_id = f"distill-to-rutters.source.{stage}"
        source = _source(root, source_id)
        interface_id = f"{source_id}.interface.{stage}"
        interface = source["interfaces"][interface_id]
        instruction_path = f"instructions/{stage}.md"

        assert source["schema_version"] == 6
        assert source["node_type"] == "behavioral_source"
        assert source["gateway"] == {
            "language": "Markdown",
            "path": instruction_path,
        }
        expected_content = instruction_path.replace("-", r"\-").replace(
            ".", r"\."
        )
        assert source["content"] == [expected_content]
        expected_uses = (
            [{"interface": "using-compass.interface.default", "version": 5}]
            if stage == "design-implementation"
            else []
        )
        assert source["uses_interfaces"] == expected_uses
        assert interface["uses_interfaces"] == expected_uses
        contract = interface["contract"]
        assert contract["execution"]["state_effect"] == "mutating"
        assert contract["interaction"]["mode"] == "interactive"
        assert contract["interaction"]["unattended_outcome"] == (
            "approval-required"
        )
        assert any(
            artifact in output["description"] for output in contract["outputs"]
        )
        declared_outcomes = {outcome["id"] for outcome in contract["outcomes"]}
        assert STAGE_OUTCOMES[stage] <= declared_outcomes


def test_gateway_is_the_only_source_that_can_route_between_stages() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")

    for stage in STAGES:
        source = _source(root, f"distill-to-rutters.source.{stage}")
        assert source["dependencies"] == []
        assert not any(
            use["interface"].startswith("distill-to-rutters.source.")
            for use in source["uses_interfaces"]
        )


def test_implementation_contract_names_the_two_required_runtime_modules() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    source = _source(root, "distill-to-rutters.source.implement")
    interface = source["interfaces"][
        "distill-to-rutters.source.implement.interface.implement"
    ]
    repository_write = next(
        item
        for item in interface["contract"]["direct_io"]["writes"]
        if item["id"] == "repository-write"
    )

    assert repository_write["path_match"] == "glob"
    assert repository_write["path"].startswith("<owning-skill>/_rtx/")
    assert "voyage_dispenser.py" in repository_write["path"]
    assert "voyage_dispenser_support.py" in repository_write["path"]


def test_gateway_keeps_all_artifacts_in_repo_and_does_not_advance_a_gap() -> None:
    gateway = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "must remain inside the repository" in gateway
    assert "source-preflight" in gateway
    assert "Only `accepted`" in gateway
    for status in ("gap", "rejected", "stale"):
        assert f"`{status}`" in gateway
    assert gateway.index("| `implement` | `finalize` |") < gateway.index(
        "| `finalize` | `verify` |"
    )


def test_authored_router_requires_fixed_artifact_names_in_safe_order() -> None:
    """An old or invented filename cannot carry otherwise valid approval bytes."""
    gateway = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    for index, (stage, artifact) in enumerate(FIXED_ARTIFACTS, start=1):
        row = f"| {index} | `{stage}` | `{artifact}` |"
        assert row in gateway
    assert "Unknown or old filenames cannot authorize routing" in gateway
    assert gateway.index("`06_implementation_report.md`") < gateway.index(
        "`07_entrypoint.md`"
    ) < gateway.index("`08_verification.md`")


def test_final_artifact_and_public_private_boundary_are_explicit() -> None:
    finalize = (SKILL_ROOT / "instructions/finalize.md").read_text(
        encoding="utf-8"
    )
    assert "07_entrypoint.md" in finalize
    verify = (SKILL_ROOT / "instructions/verify.md").read_text(encoding="utf-8")
    assert "08_verification.md" in verify

    public_markdown = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            SKILL_ROOT / "SKILL.md",
            *sorted((SKILL_ROOT / "instructions").glob("*.md")),
        ]
    )
    assert "STATUS.md" not in public_markdown
    assert "voyage_dispenser.py" not in public_markdown
    assert "voyage_dispenser_support.py" not in public_markdown
    assert "/_rtx/" not in public_markdown


def test_every_stage_instruction_declares_its_typed_artifact_protocol() -> None:
    body_schemas = {
        "breakdown": "breakdown/v1",
        "assign-rutters": "assignment/v1",
        "extract-evolutions": "graph/v1",
        "validate-logic": "logic-validation/v1",
        "design-implementation": "implementation-design/v1",
        "implement": "implementation-report/v1",
        "finalize": "entrypoint/v1",
        "verify": "verification/v1",
    }

    for stage in STAGES:
        instruction = (SKILL_ROOT / f"instructions/{stage}.md").read_text(
            encoding="utf-8"
        )
        assert "schema_version: distill-to-rutters/v1" in instruction
        assert f"stage: {stage}" in instruction
        assert f"body_schema: {body_schemas[stage]}" in instruction
        for outcome in STAGE_OUTCOMES[stage]:
            assert f"`{outcome}`" in instruction
        assert "Do not compute or embed this artifact's own digest" in instruction
        assert "`(path, digest, outcome)`" in instruction


def test_breakdown_contract_requires_a_recursive_normative_context_closure() -> None:
    """Removing recursive closure or conflict gates must block decomposition."""
    breakdown = (SKILL_ROOT / "instructions/breakdown.md").read_text(
        encoding="utf-8"
    )
    blueprint = _load_yaml(
        SKILL_ROOT / "blueprints/instructions-breakdown.yaml"
    )
    schema = json.loads(
        (SKILL_ROOT / "references/breakdown-body.schema.json").read_text(
            encoding="utf-8"
        )
    )

    for requirement in (
        "fixed point",
        "visited identities",
        "instruction, schema, standard, template, asset, and interface",
        "source authority",
        "governing source",
        "breakdown-gap",
        "obligation ID derived from its behavioral identity",
        "normative reference",
        "availability",
        "digest: null",
        "unreadable",
    ):
        assert requirement in breakdown

    reads = blueprint["interfaces"][
        "distill-to-rutters.source.breakdown.interface.breakdown"
    ]["contract"]["direct_io"]["reads"]
    assert any("behavior-defining reference closure" in item["content"] for item in reads)

    closure = schema["properties"]["context_closure"]["items"]
    assert set(
        (
            "obligation_id",
            "path",
            "availability",
            "digest",
            "authority",
            "provenance",
            "why_behavior_defining",
            "resolution",
        )
    ) <= set(closure["required"])
    assert closure["properties"]["authority"]["enum"] == [
        "normative",
        "informative",
    ]
    assert closure["properties"]["provenance"]["enum"] == [
        "source",
        "generated projection",
    ]
    assert closure["properties"]["availability"]["enum"] == [
        "present",
        "missing",
        "unreadable",
    ]
    assert "governing_source" in closure["properties"]


def test_assignment_contract_keeps_decomposition_and_orchestration_with_rutters() -> None:
    """A dispenser cannot turn completed work into an authorized transition."""
    breakdown = " ".join(
        (SKILL_ROOT / "instructions/breakdown.md").read_text(
            encoding="utf-8"
        ).split()
    )
    assignment = " ".join(
        (SKILL_ROOT / "instructions/assign-rutters.md").read_text(
            encoding="utf-8"
        ).split()
    )
    schema = json.loads(
        (SKILL_ROOT / "references/assignment-body.schema.json").read_text(
            encoding="utf-8"
        )
    )

    for requirement in (
        "not behaviorally independent",
        "one Rutter",
        "hidden mutable exchange",
    ):
        assert requirement in breakdown

    for requirement in (
        "state and transition semantics are identical",
        "starts, dependencies, joins, aggregate results, partial failure, retries, cancellation, failure propagation, authorization, and release",
        "may mechanically execute an authorized action",
        "may not choose ordering, branching, retry, cancellation, join, or release policy",
        "Final-result validation does not substitute for transition authorization",
        "every cross-part obligation",
        "before advancement",
    ):
        assert requirement in assignment

    assignment_fields = schema["properties"]["assignments"]["items"]
    orchestration_fields = schema["properties"]["orchestration"]
    assert {"inseparability", "independent_workflows"} <= set(
        assignment_fields["required"]
    )
    assert "joins" in orchestration_fields["required"]
    assert {"partial_failure", "retry_owner"} <= set(
        orchestration_fields["required"]
    )
    assert "join_transition" in assignment_fields["properties"][
        "independent_workflows"
    ]["items"]["required"]


def test_context_closure_fixtures_represent_required_edges_without_claiming_an_oracle() -> None:
    """These are Layer-1 inputs; Task 5 owns fixture-specific behavior checks."""
    fixtures = SKILL_ROOT / "tests/fixtures/context-closure"
    texts = {
        name: (fixtures / name).read_text(encoding="utf-8")
        for name in (
            "root.md",
            "chain.md",
            "cycle.md",
            "generated-normative.md",
            "conflict.md",
        )
    }

    assert "[chain.md]" in texts["root.md"]
    assert "[missing.md]" in texts["root.md"]
    assert "[unreadable.md]" in texts["root.md"]
    assert "[cycle.md]" in texts["chain.md"]
    assert "[root.md]" in texts["cycle.md"]
    assert "generated projection" in texts["generated-normative.md"]
    assert "Governing source: [root.md]" in texts["generated-normative.md"]
    assert "normative" in texts["conflict.md"]
    assert "contradicts" in texts["conflict.md"]
