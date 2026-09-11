"""Structural tests for the deliberately small CI-debug instruction layer."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from officina.blueprints.graph import RepositoryBlueprintGraph, resolve_export


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "ci-debug"
QUALIFY = SKILL / "instructions" / "qualify-ci.md"


def test_module_exposes_separate_public_routes_and_machine_calls() -> None:
    module = yaml.safe_load((SKILL / "blueprint.yaml").read_text(encoding="utf-8"))
    assert module["discovery"]["catalog"] == {
        "domain": "software-development",
        "topics": ["repository-workflow", "task-automation", "assistant-assurance"],
        "visibility": "listed",
    }
    assert set(module["exports"]) == {
        "ci-debug.interface.default",
        "ci-debug.interface.analyze-ci",
        "ci-debug.interface.qualify-ci",
        "ci-debug.interface.repair-element",
    }
    assert module["namespace_exports"]["_rtx"]["surface"]["only"] == {
        "ci-debug._rtx.interface.fetch-github-actions-history": 1,
        "ci-debug._rtx.interface.report-test-failures-between-green-runs": 1,
        "ci-debug._rtx.interface.report-ci-runtime-hotspots": 1,
        "ci-debug._rtx.interface.run-ci": 2,
        "ci-debug._rtx.interface.run-targeted-tests": 1,
    }


def test_gateway_is_only_a_route_selector() -> None:
    text = " ".join(
        (SKILL / "SKILL.md").read_text(encoding="utf-8").lower().split()
    )
    for phrase in ("historical failures", "exact-sha qualification", "explicitly assigned matrix element", "mixed historical-analysis"):
        assert phrase in text
    assert "while its report is red" not in text
    assert "git worktree" not in text
    assert "monetary pricing is unsupported" in text
    assert "controlled benchmark" in text


def test_analysis_route_uses_three_independently_published_sibling_directories() -> None:
    text = " ".join(
        (SKILL / "instructions" / "analyze-ci.md")
        .read_text(encoding="utf-8")
        .lower()
        .split()
    )
    for phrase in (
        "refuse an existing destination",
        "three new child paths",
        "fetch-github-actions-history",
        "report-test-failures-between-green-runs",
        "report-ci-runtime-hotspots",
        "same published snapshot",
        "source snapshot digest mismatch",
        "do not create a root manifest or root publication marker",
    ):
        assert phrase in text


def test_gateway_public_contract_qualifies_local_branch_and_optionally_pushes() -> None:
    gateway = yaml.safe_load(
        (SKILL / "blueprints" / "instructions-qualify-ci.yaml").read_text(encoding="utf-8")
    )
    interface = gateway["interfaces"]["ci-debug.source.instructions-qualify-ci.interface.qualify-ci"]
    contract = interface["contract"]

    assert interface["version"] == 2
    assert contract["arguments"] == {
        "request": {
            "description": "CI request, repair constraints, and available evidence.",
            "required": True,
            "sensitivity": "user-private",
            "type": {"kind": "string", "sensitivity": "user-private"},
        },
        "branch": {
            "description": "Local branch to qualify; defaults to master.",
            "required": False,
            "default": "master",
            "sensitivity": "user-private",
            "type": {"kind": "string", "sensitivity": "user-private"},
        },
        "push": {
            "description": (
                "Whether to publish the qualified target to origin; omitted intent "
                "is resolved from the request or by asking."
            ),
            "required": False,
            "sensitivity": "public",
            "type": {"kind": "boolean", "sensitivity": "public"},
        },
    }
    assert [item["id"] for item in contract["outcomes"]] == [
        "green-local",
        "green-pushed",
        "blocked",
        "failed",
    ]
    assert [
        (item["class"], item.get("effects", []), item["outputs"])
        for item in contract["outcomes"]
    ] == [
        ("success", ["repository-change"], ["response"]),
        (
            "success",
            ["repository-change", "remote-target-change"],
            ["response"],
        ),
        (
            "partial",
            ["repository-change", "remote-target-change"],
            ["response"],
        ),
        ("error", [], ["response"]),
    ]
    assert all(item["caller_action"] for item in contract["outcomes"])
    effects = {
        item["id"]: item for item in contract["execution"]["effects"]
    }
    assert effects["repository-change"]["may_occur_in_outcomes"] == [
        "green-local",
        "green-pushed",
        "blocked",
    ]
    remote_effect = effects["remote-target-change"]
    assert remote_effect["may_occur_in_outcomes"] == ["green-pushed", "blocked"]
    assert remote_effect["action"] == "update"
    assert remote_effect["direct_io_ref"] == "remote-target-ref"
    assert remote_effect["value_source"] == {
        "kind": "output",
        "output_ref": "response",
    }
    remote_io = {
        item["id"]: item for item in contract["direct_io"]["network"]
    }["remote-target-ref"]
    assert remote_io["system"] == "github-origin"
    assert remote_io["medium"] == "network"
    assert remote_io["access"] == "read-write"
    warnings = " ".join(
        next(iter(item.values())) for item in contract["caller_warnings"]
    ).lower()
    assert "temporary remote candidate" in warnings
    assert "push is false" in warnings
    assert "verification becomes unavailable" in warnings
    assert "branch=master" in interface["usage"]
    assert "push=<true|false|omitted>" in interface["usage"]
    assert "structured" in interface["description"].lower()


def test_public_export_resolves_qualification_version_two(
    ordinary_repository_graph: RepositoryBlueprintGraph,
) -> None:
    _, source, export = resolve_export(
        ordinary_repository_graph,
        "ci-debug.interface.qualify-ci",
        2,
    )

    assert source.node_id == "ci-debug.source.instructions-qualify-ci"
    assert export.source_interface_id == (
        "ci-debug.source.instructions-qualify-ci.interface.qualify-ci"
    )


def test_gateway_has_numbered_isolated_qualification_algorithm() -> None:
    raw = QUALIFY.read_text(encoding="utf-8")
    assert re.findall(r"^## ([0-9])\. ", raw, flags=re.MULTILINE) == list(
        "0123456789"
    )
    text = " ".join(raw.lower().split())

    for phrase in (
        "structured arguments take precedence",
        "before any git mutation or remote ci dispatch",
        "defaults to `master`",
        "ask whether to stop with a green local branch or also push",
        "new collision-resistant candidate branch",
        "isolated worktree",
        "existing local and remote branches unchanged during qualification",
        "outside every temporary worktree",
        "same context to every full-matrix, targeted-test, and repair-element invocation",
        "never owns credentials",
        "revalidate authentication, repository identity, ref, and exact sha",
        "retire superseded runs",
        "record a capacity blocker",
        "completed matrix-element reports and logs",
        "route completed failures and stalled elements into the failure ledger",
        "bounded parallel",
        "one collision-resistant repair branch and worktree per element",
        "exact current candidate sha",
        "pass both the assigned branch and worktree",
        "sequential fallback",
        "integrate accepted patches sequentially",
        "record its exact sha",
        "smallest selectors needed to detect integration interactions",
        "run each whole affected element",
        "pending, red, targeted-green, and whole-element-green are nonterminal",
        "report-only prevention review",
        "fast-forward-only",
        "ask the user for guidance",
        "ordinary, non-force push",
        "verify that `origin/<branch>` equals the exact green sha",
        "when `push` is false",
        "invocation-owned temporary resources",
        "candidate and repair worktrees",
        "preserve the candidate, context, reports, and recovery coordinates",
    ):
        assert phrase in text


def test_gateway_sections_guard_promotion_and_terminal_evidence() -> None:
    raw = QUALIFY.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^## ([0-9])\. ", raw, flags=re.MULTILINE))
    sections = {
        match.group(1): " ".join(
            raw[
                match.start() : matches[index + 1].start()
                if index + 1 < len(matches)
                else len(raw)
            ]
            .lower()
            .split()
        )
        for index, match in enumerate(matches)
    }

    assert "before any git mutation or remote ci dispatch" in sections["0"]
    assert "failed" in sections["1"] and "before" in sections["1"]
    assert "ci-debug/" in sections["2"]
    assert "explicitly approves" in sections["6"]
    for phrase in (
        "current tip must be an ancestor",
        "checked out in exactly one worktree",
        "no in-progress git operation",
        "staged or unstaged tracked changes",
        "no untracked collision",
        "fast-forward-only merge",
        "compare-and-swap ref update",
        "checked out in multiple worktrees",
    ):
        assert phrase in sections["7"]
    for phrase in (
        "repository identity",
        "target branch",
        "starting local and remote shas",
        "exact green sha",
        "candidate ref",
        "ci run",
        "report location",
        "publication status",
        "cleanup disposition",
        "expected tips",
    ):
        assert phrase in sections["9"]


def test_gateway_contract_routes_post_effect_failures_to_blocked() -> None:
    gateway = yaml.safe_load(
        (SKILL / "blueprints" / "instructions-qualify-ci.yaml").read_text(encoding="utf-8")
    )
    interface = gateway["interfaces"]["ci-debug.source.instructions-qualify-ci.interface.qualify-ci"]
    contract = interface["contract"]
    outcomes = {item["id"]: item for item in contract["outcomes"]}
    uncertain = contract["execution"]["mutation_safety"][
        "on_uncertain_completion"
    ]["verify_then_decide"].lower()

    assert "before any side effect" in outcomes["failed"]["caller_action"].lower()
    assert "after any side effect" in outcomes["blocked"]["caller_action"].lower()
    assert "push may have succeeded" in uncertain
    assert "live remote verification" in uncertain


def test_gateway_probes_integrated_candidate_before_full_matrix() -> None:
    text = " ".join(
        QUALIFY.read_text(encoding="utf-8").lower().split()
    )

    assert "exact integrated candidate" in text
    assert "before the next complete matrix" in text
    assert "use `ci-debug._rtx.interface.run-targeted-tests`" in text
    assert "only after every affected matrix element is green" in text


def test_gateway_gates_and_batches_each_remote_wave() -> None:
    raw = QUALIFY.read_text(encoding="utf-8")
    section_4 = " ".join(
        raw[raw.index("## 4.") : raw.index("## 5.")].lower().split()
    )
    section_5 = " ".join(
        raw[raw.index("## 5.") : raw.index("## 6.")].lower().split()
    )
    text = " ".join(raw.lower().split())

    assert "before every complete matrix and remote probe batch" in text
    assert section_4.index("refresh") < section_4.index("run-ci")
    assert section_5.index("refresh") < section_5.index("run-targeted-tests")
    assert "normalized failure signature" in section_5
    for component in ("category", "normalized message", "terminal project frame"):
        assert component in section_5
    for variable in ("temporary roots", "run ids", "timestamps", "durations"):
        assert variable in section_5
    assert "one repair owner" in section_5
    assert "validation ledger entry" in section_5
    assert "clustering is only a scheduling hint" in section_5
    assert "--selectors-json" in section_5
    assert "one complete run of every affected element" in section_5
    for counter in (
        "elapsed wall time",
        "drift-check count",
        "targeted request count",
        "whole-element count",
        "full-matrix count",
        "repair rounds",
        "repeated unchanged failure signatures",
    ):
        assert counter in text


def test_gateway_retires_obsolete_runs_and_recovers_completed_job_evidence() -> None:
    text = " ".join(
        QUALIFY.read_text(encoding="utf-8").lower().split()
    )

    assert "retire superseded runs before dispatching replacement work" in text
    assert "completed matrix-element reports and logs" in text
    assert "as soon as an already-authorized ci surface exposes them" in text
    assert "do not wait for the enclosing matrix" in text


def test_repair_route_contains_the_targeted_inner_loop() -> None:
    text = " ".join(
        (SKILL / "instructions" / "repair-element.md")
        .read_text(encoding="utf-8")
        .lower()
        .split()
    )
    for phrase in (
        "while failures remain",
        "smallest failure-containing selector set",
        "reuse that context for every targeted invocation",
        "patch only evidence-backed paths",
        "use `ci-debug._rtx.interface.run-targeted-tests`",
        "replace only the probed ledger entries",
        "retain every unprobed failure",
        "executed every requested selector",
        "same set repeats",
        "whole matrix element",
        "without integration or cleanup",
    ):
        assert phrase in text
    assert "overall ci green" in text


def test_repair_route_treats_stalls_as_bounded_failure_classes() -> None:
    text = " ".join(
        (SKILL / "instructions" / "repair-element.md")
        .read_text(encoding="utf-8")
        .lower()
        .split()
    )

    assert "treat a stall as a failure class" in text
    assert "explicit wall-clock bound" in text
    assert "process tree" in text
    assert "smallest runnable selector" in text


def test_instruction_sources_use_only_the_interface_they_need() -> None:
    gateway = yaml.safe_load(
        (SKILL / "blueprints" / "gateway.yaml").read_text(encoding="utf-8")
    )
    analyze = yaml.safe_load(
        (SKILL / "blueprints" / "instructions-analyze-ci.yaml").read_text(
            encoding="utf-8"
        )
    )
    qualify = yaml.safe_load(
        (SKILL / "blueprints" / "instructions-qualify-ci.yaml").read_text(
            encoding="utf-8"
        )
    )
    repair = yaml.safe_load(
        (SKILL / "blueprints" / "instructions-repair-element.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert gateway["uses_interfaces"] == [
        {
            "interface": "ci-debug.source.instructions-analyze-ci.interface.analyze-ci",
            "version": 1,
        },
        {
            "interface": "ci-debug.source.instructions-qualify-ci.interface.qualify-ci",
            "version": 2,
        },
        {
            "interface": "ci-debug.source.instructions-repair-element.interface.repair-element",
            "version": 1,
        },
        {"interface": "ci-debug._rtx.interface.fetch-github-actions-history", "version": 1},
        {"interface": "ci-debug._rtx.interface.report-test-failures-between-green-runs", "version": 1},
        {"interface": "ci-debug._rtx.interface.report-ci-runtime-hotspots", "version": 1},
        {"interface": "ci-debug._rtx.interface.run-ci", "version": 2},
        {"interface": "ci-debug._rtx.interface.run-targeted-tests", "version": 1},
        {"interface": "git-workflow.interface.default", "version": 1},
    ]
    assert analyze["uses_interfaces"] == [
        {"interface": "ci-debug._rtx.interface.fetch-github-actions-history", "version": 1},
        {"interface": "ci-debug._rtx.interface.report-test-failures-between-green-runs", "version": 1},
        {"interface": "ci-debug._rtx.interface.report-ci-runtime-hotspots", "version": 1},
    ]
    assert qualify["uses_interfaces"] == [
        {"interface": "ci-debug._rtx.interface.run-ci", "version": 2},
        {"interface": "ci-debug._rtx.interface.run-targeted-tests", "version": 1},
        {
            "interface": "ci-debug.source.instructions-repair-element.interface.repair-element",
            "version": 1,
        },
        {"interface": "git-workflow.interface.default", "version": 1},
    ]
    assert repair["uses_interfaces"] == [
        {"interface": "ci-debug._rtx.interface.run-targeted-tests", "version": 1},
        {"interface": "git-workflow.interface.default", "version": 1},
    ]
