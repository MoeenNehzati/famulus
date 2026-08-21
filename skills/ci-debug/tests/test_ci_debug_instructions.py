"""Structural tests for the deliberately small CI-debug instruction layer."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "ci-debug"


def test_module_exposes_only_the_two_instruction_routes_and_two_machine_calls() -> None:
    module = yaml.safe_load((SKILL / "blueprint.yaml").read_text(encoding="utf-8"))
    assert module["discovery"]["catalog"] == {
        "domain": "software-development",
        "topics": ["repository-workflow", "task-automation", "assistant-assurance"],
        "visibility": "listed",
    }
    assert set(module["exports"]) == {
        "ci-debug.interface.default",
        "ci-debug.interface.repair-element",
    }
    assert module["namespace_exports"]["_rtx"]["surface"]["only"] == {
        "ci-debug._rtx.interface.run-ci": 1,
        "ci-debug._rtx.interface.run-targeted-tests": 1,
    }


def test_gateway_contains_only_the_outer_loop() -> None:
    text = " ".join(
        (SKILL / "SKILL.md").read_text(encoding="utf-8").lower().split()
    )
    for phrase in (
        "while its report is red",
        "group failures by matrix element",
        "one non-secret debug context",
        "immutable request-scoped reports",
        "still revalidate authentication",
        "failure ledger, branch assignments, and agent state outside",
        "bounded parallel",
        "integrate accepted patches sequentially",
        "use `ci-debug._rtx.interface.run-ci` again",
        "targeted tests and whole-element tests never establish overall green",
    ):
        assert phrase in text
    assert "replace the failure set" not in text


def test_gateway_probes_integrated_candidate_before_full_matrix() -> None:
    text = " ".join(
        (SKILL / "SKILL.md").read_text(encoding="utf-8").lower().split()
    )

    assert "exact integrated candidate" in text
    assert "before the next complete matrix" in text
    assert "use `ci-debug._rtx.interface.run-targeted-tests`" in text
    assert "only after every affected matrix element is green" in text


def test_gateway_retires_obsolete_runs_and_recovers_completed_job_evidence() -> None:
    text = " ".join(
        (SKILL / "SKILL.md").read_text(encoding="utf-8").lower().split()
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
    repair = yaml.safe_load(
        (SKILL / "blueprints" / "instructions-repair-element.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert gateway["uses_interfaces"] == [
        {"interface": "ci-debug._rtx.interface.run-ci", "version": 1},
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
