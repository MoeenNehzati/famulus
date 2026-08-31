from __future__ import annotations

from pathlib import Path

import yaml

from officina.blueprints.graph import (
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.blueprints.process_binding import (
    compile_gateway_invocation,
    parse_caller_invocation,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "node-certify"
DRIFT_ROOT = REPO_ROOT / "skills" / "node-drift"

AUDIT_SOURCES = {
    "node-certify.source.audit-interface": (
        "instructions/audit-interface.md",
        "blueprints/instructions-audit-interface.yaml",
        "node-certify.source.audit-interface.interface.audit",
    ),
    "node-certify.source.audit-behavioral-source": (
        "instructions/audit-behavioral-source.md",
        "blueprints/instructions-audit-behavioral-source.yaml",
        "node-certify.source.audit-behavioral-source.interface.audit",
    ),
    "node-certify.source.audit-module": (
        "instructions/audit-module.md",
        "blueprints/instructions-audit-module.yaml",
        "node-certify.source.audit-module.interface.audit",
    ),
}


def _yaml(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_certifier_exposes_three_private_semantic_audit_sources() -> None:
    module = _yaml(SKILL_ROOT / "blueprint.yaml")

    assert module["exports"] == {}
    assert set(module["sources"]) == {
        "node-certify.source.gateway",
        *AUDIT_SOURCES,
    }

    for source_id, (instruction_path, blueprint_path, interface_id) in AUDIT_SOURCES.items():
        source = _yaml(SKILL_ROOT / blueprint_path)
        assert source["schema_version"] == 6
        assert source["node_type"] == "behavioral_source"
        assert source["id"] == source_id
        assert source["gateway"] == {
            "language": "Markdown",
            "path": instruction_path,
        }
        assert source["content"] == [instruction_path.replace(".", r"\.")]
        assert set(source["interfaces"]) == {interface_id}
        assert source["interfaces"][interface_id]["content"] == source["content"]
        assert source["interfaces"][interface_id]["uses_interfaces"] == []
        assert source["interfaces"][interface_id]["version"] == 2
        assert source["version"] == 2


def test_certifier_gateway_orchestrates_audits_without_default_interface(
    ordinary_repository_graph: RepositoryBlueprintGraph,
) -> None:
    module = _yaml(SKILL_ROOT / "blueprint.yaml")
    gateway = _yaml(SKILL_ROOT / "blueprints" / "gateway.yaml")
    certifier_runtime = _yaml(SKILL_ROOT / "_rtx" / "blueprint.yaml")
    certifier_source = _yaml(
        SKILL_ROOT / "_rtx" / "blueprints" / "rtx-certifier.yaml"
    )
    drift_module = _yaml(DRIFT_ROOT / "blueprint.yaml")
    drift_gateway = _yaml(DRIFT_ROOT / "blueprints" / "gateway.yaml")
    drift_runtime = _yaml(DRIFT_ROOT / "_rtx" / "blueprint.yaml")
    drift_source = _yaml(
        DRIFT_ROOT / "_rtx" / "blueprints" / "rtx-check-drift-state.yaml"
    )
    interface_ids = {
        interface_id
        for _, (_, _, interface_id) in AUDIT_SOURCES.items()
    }
    drift_interface = "node-drift._rtx.interface.drift-status"
    expected_uses = interface_ids | {
        drift_interface,
        "node-certify._rtx.interface.certify",
        "node-certify._rtx.interface.semantic-audit-scheduler",
        "setup-python-environment.interface.repair-selected-packages",
    }

    assert gateway["interfaces"] == {}
    assert {
        use["interface"] for use in gateway["uses_interfaces"]
    } == expected_uses
    assert module["version"] == gateway["version"] == 6
    certifier_interface = "node-certify._rtx.interface.certify"
    certifier_source_interface = (
        "node-certify._rtx.source.rtx-certifier.interface.certify"
    )
    certifier_interface_version = certifier_source["interfaces"][
        certifier_source_interface
    ]["version"]
    assert certifier_interface_version == 2
    assert certifier_source["version"] == 2
    assert certifier_runtime["version"] == 3
    assert module["namespace_exports"]["_rtx"]["version"] == 3
    assert module["namespace_exports"]["_rtx"]["surface"]["only"][
        certifier_interface
    ] == certifier_interface_version
    assert next(
        use for use in gateway["uses_interfaces"] if use["interface"] == certifier_interface
    )["version"] == certifier_interface_version
    assert drift_module["version"] == drift_gateway["version"] == 5
    drift_source_interface = (
        "node-drift._rtx.source.rtx-check-drift-state.interface.drift-status"
    )
    drift_status_version = drift_source["interfaces"][drift_source_interface][
        "version"
    ]
    assert drift_status_version == 4
    assert drift_source["version"] == drift_runtime["version"] == 4
    assert drift_module["namespace_exports"]["_rtx"]["version"] == 4
    assert drift_module["namespace_exports"]["_rtx"]["surface"]["only"][
        drift_interface
    ] == drift_status_version
    for use in (*gateway["uses_interfaces"], *drift_gateway["uses_interfaces"]):
        if use["interface"] == drift_interface:
            assert use["version"] == drift_status_version
    assert next(
        use
        for use in drift_gateway["interfaces"][
            "node-drift.source.gateway.interface.default"
        ]["uses_interfaces"]
        if use["interface"] == drift_interface
    ) == {
        "interface": drift_interface,
        "version": drift_status_version,
    }
    certifier_dependency = next(
        dependency
        for dependency in drift_gateway["dependencies"]
        if dependency["source"] == "node-certify.source.gateway"
    )
    assert certifier_dependency["version"] == gateway["version"]

    # The real graph load proves the namespace-exported drift route is authorized
    # and does not introduce a certification dependency cycle.
    graph = ordinary_repository_graph
    for interface_id, subcommand in (
        ("node-drift._rtx.interface.compute-hashes", "compute-hashes"),
        ("node-drift._rtx.interface.drift-status", "status"),
    ):
        export = graph.exports[interface_id]
        parsed = parse_caller_invocation(
            export,
            ["--repo-root", str(REPO_ROOT), "--json"],
            stdin_requested=False,
        )
        plan = compile_gateway_invocation(
            graph.nodes[export.source_node_id], export, parsed
        )

        assert plan.argv == (
            subcommand,
            "--repo-root",
            str(REPO_ROOT),
            "--json",
        )

    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "node-certify.interface.default" not in skill_text
    algorithm = skill_text.split("## Certification algorithm", 1)[1]
    normalized_algorithm = " ".join(algorithm.split())
    assert "semantic-audit-scheduler@1" in normalized_algorithm
    assert "claim --capacity k" in normalized_algorithm.lower()
    assert "one fresh subagent" in normalized_algorithm.lower()
    assert "never reuse a subagent" in normalized_algorithm.lower()
    assert "--report-file" in normalized_algorithm
    assert "skips current nodes" in normalized_algorithm
    assert "audit every interface and node" not in normalized_algorithm
    assert algorithm.index("drift-status") < algorithm.index("semantic-audit-scheduler")
    assert algorithm.index("semantic-audit-scheduler") < algorithm.index(
        "mechanical\n   `certify`"
    )
def test_drift_repository_routes_supply_their_subcommands() -> None:
    graph = load_repository_blueprint_graph(REPO_ROOT)

    for interface_id, subcommand in (
        ("node-drift._rtx.interface.compute-hashes", "compute-hashes"),
        ("node-drift._rtx.interface.drift-status", "status"),
    ):
        export = graph.exports[interface_id]
        parsed = parse_caller_invocation(
            export,
            ["--repo-root", str(REPO_ROOT), "--json"],
            stdin_requested=False,
        )
        plan = compile_gateway_invocation(
            graph.nodes[export.source_node_id], export, parsed
        )

        assert plan.argv == (
            subcommand,
            "--repo-root",
            str(REPO_ROOT),
            "--json",
        )


def test_semantic_audit_scheduler_route_preserves_operation_and_capacity() -> None:
    graph = load_repository_blueprint_graph(REPO_ROOT)
    export = graph.exports[
        "node-certify._rtx.interface.semantic-audit-scheduler"
    ]
    prefix = SKILL_ROOT / "_build" / "semantic-audit-runs" / "test"
    parsed = parse_caller_invocation(
        export,
        ["claim", str(prefix), "--capacity", "2"],
        stdin_requested=False,
    )
    plan = compile_gateway_invocation(
        graph.nodes[export.source_node_id], export, parsed
    )

    assert plan.argv == ("claim", str(prefix), "--capacity", "2")


def test_drift_status_route_preserves_dag_file() -> None:
    graph = load_repository_blueprint_graph(REPO_ROOT)
    export = graph.exports["node-drift._rtx.interface.drift-status"]
    dag_file = REPO_ROOT / "skills" / "node-certify" / "_build" / "dag.json"
    parsed = parse_caller_invocation(
        export,
        [
            "--repo-root",
            str(REPO_ROOT),
            "--json",
            "--dag-file",
            str(dag_file),
        ],
        stdin_requested=False,
    )
    plan = compile_gateway_invocation(
        graph.nodes[export.source_node_id], export, parsed
    )

    assert plan.argv == (
        "status",
        "--repo-root",
        str(REPO_ROOT),
        "--json",
        "--dag-file",
        str(dag_file),
    )
def test_drift_and_canonical_docs_describe_selective_v6_worklist() -> None:
    drift_text = (DRIFT_ROOT / "SKILL.md").read_text(encoding="utf-8")
    canonical_text = (
        REPO_ROOT / "docs" / "officina" / "certification_and_drift.md"
    ).read_text(encoding="utf-8")
    normalized_drift = " ".join(drift_text.split())
    normalized_canonical = " ".join(canonical_text.split())

    assert "version-6 repository graphs" in normalized_drift
    assert "dependency closure rooted at that module's owned nodes" in normalized_drift
    assert "exact changed file, interface, or dependency causes" in normalized_drift
    assert "all direct facet dependencies" in normalized_drift
    assert "relation and target otherwise" in normalized_drift
    assert "stale worklist" in normalized_drift
    assert "--dag-file" in normalized_drift
    assert "stale_vertices" in normalized_drift
    assert "complete neutral dependency DAG" in normalized_drift
    assert "selective bottom-up semantic review" in normalized_canonical
    assert "officina.certification-dependency-dag/v1" in normalized_canonical
    assert "semantic-audit-scheduler" in normalized_canonical
    assert "bounded pool" in normalized_canonical
    assert "needs-context" not in normalized_canonical
    assert "route smoke" in normalized_canonical
    assert "stale worklist" in normalized_canonical
    assert "all direct facet dependencies" in normalized_canonical
    assert "interface: node-certify._rtx.interface.certify version: 2" in (
        normalized_canonical
    )


def test_each_semantic_audit_instruction_has_one_bounded_job() -> None:
    interface_text = (SKILL_ROOT / "instructions" / "audit-interface.md").read_text(
        encoding="utf-8"
    )
    source_text = (
        SKILL_ROOT / "instructions" / "audit-behavioral-source.md"
    ).read_text(encoding="utf-8")
    module_text = (SKILL_ROOT / "instructions" / "audit-module.md").read_text(
        encoding="utf-8"
    )
    normalized_interface = " ".join(interface_text.split())
    normalized_source = " ".join(source_text.split())

    assert "one source interface" in interface_text
    assert "interface-owned facts" in normalized_interface
    assert "Do not recursively audit, schedule, or delegate dependencies" in normalized_interface
    assert "semantic-audit-result/v1" in interface_text
    assert 'verdict: "abort"' in interface_text
    assert "Do not audit source-wide platform support" in interface_text
    assert "non-process Markdown interface" in normalized_interface
    assert "instruction and prompt behavior" in normalized_interface
    assert "Do not sign" in interface_text
    assert "remainder content" in source_text
    assert "interface audit results" in source_text
    assert "authenticated unchanged facet evidence" in normalized_source
    assert "latest valid payload-v3 certificate" in normalized_source
    assert "not a separately signed per-facet semantic attestation" in normalized_source
    assert "Until authenticated selective reuse exists" not in source_text
    assert "Do not sign" in source_text
    assert "already-audited child nodes" in module_text
    assert "namespace" in module_text
    assert "requests expansion" not in module_text
    assert "Do not inspect child implementation content" in module_text
    assert "Do not sign" in module_text


def test_repository_docs_do_not_reference_removed_default_interface() -> None:
    docstring_guide = (REPO_ROOT / "docs" / "officina" / "docstring.md").read_text(
        encoding="utf-8"
    )

    assert "node-certify.interface.default" not in docstring_guide
    assert (
        "node-certify.source.audit-interface.interface.audit"
        in docstring_guide
    )


def test_canonical_docs_distinguish_current_inputs_from_issuance_provenance() -> None:
    text = (
        REPO_ROOT / "docs" / "officina" / "certification_and_drift.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "source commit identify the current node state" not in normalized
    assert "`source_commit` records issuance provenance" in normalized
    assert "need not equal the current HEAD" in normalized
    assert "whole-node semantic-review pass" in normalized
    assert "not an independent per-facet semantic attestation" in normalized
