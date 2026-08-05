from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark-precommit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("benchmark_precommit", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


EXPECTED_PHASES = [
    "settings-generation",
    "documentation-generation",
    "preview-generation",
    "gitleaks",
    "validators",
    "python-tests",
]
EXPECTED_GROUPS = ["group-1", "group-2"]


def complete_inputs() -> tuple[dict, dict, list[dict], dict]:
    gate = {
        "complete": True,
        "phases": [
            {
                "phase_id": phase_id,
                "returncode": 0,
                "status": "passed",
                "wall_seconds": 1.0,
            }
            for phase_id in EXPECTED_PHASES
        ],
    }
    groups = {
        "complete": True,
        "groups": [
            {
                "group_id": group_id,
                "returncode": 0,
                "wall_seconds": 0.5,
            }
            for group_id in EXPECTED_GROUPS
        ],
    }
    executions = [
        {
            "group_id": group_id,
            "complete": True,
            "cancelled": False,
            "exitstatus": 0,
            "collection": {
                "finished": True,
                "selected_nodeids": [f"tests/{group_id}.py::test_ok"],
                "deselected_nodeids": [],
                "errors": [],
            },
            "test_reports": [
                {
                    "nodeid": f"tests/{group_id}.py::test_ok",
                    "when": "call",
                    "outcome": "passed",
                    "duration_seconds": 0.01,
                    "skip_reason": None,
                }
            ],
        }
        for group_id in EXPECTED_GROUPS
    ]
    capabilities = {
        "chrome": {"required": True, "available": True, "reason": None},
        "network": {"required": True, "available": True, "reason": None},
        "uv": {"required": True, "available": True, "reason": None},
        "installer": {"required": False, "available": False, "reason": "excluded"},
    }
    return gate, groups, executions, capabilities


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing-phase", "configured phase set mismatch"),
        ("duplicate-phase", "configured phase set mismatch"),
        ("missing-group", "configured group set mismatch"),
        ("duplicate-group", "configured group set mismatch"),
        ("cancelled", "cancelled"),
        ("missing-collection", "lacks collection events"),
    ],
)
def test_incomplete_or_ambiguous_runs_are_rejected(
    mutation: str, reason: str
) -> None:
    """Removing or duplicating executed work must reject performance evidence."""
    benchmark = load_module()
    gate, groups, executions, capabilities = complete_inputs()
    if mutation == "missing-phase":
        gate["phases"].pop()
    elif mutation == "duplicate-phase":
        gate["phases"].append(deepcopy(gate["phases"][0]))
    elif mutation == "missing-group":
        groups["groups"].pop()
        executions.pop()
    elif mutation == "duplicate-group":
        groups["groups"].append(deepcopy(groups["groups"][0]))
    elif mutation == "cancelled":
        executions[0]["cancelled"] = True
    elif mutation == "missing-collection":
        executions[0]["collection"]["finished"] = False

    assessment = benchmark.assess_run(
        gate_report=gate,
        group_report=groups,
        execution_reports=executions,
        expected_phase_ids=EXPECTED_PHASES,
        expected_group_ids=EXPECTED_GROUPS,
        capabilities=capabilities,
        gate_returncode=0,
    )

    assert assessment["classification"] == "rejected"
    assert any(reason in item for item in assessment["reasons"])


def test_complete_failures_are_diagnostic_but_not_acceptance_evidence() -> None:
    """Ordinary failures may time a full topology but may not certify acceptance."""
    benchmark = load_module()
    gate, groups, executions, capabilities = complete_inputs()
    gate["phases"][-1]["returncode"] = 1
    gate["phases"][-1]["status"] = "failed"
    groups["groups"][0]["returncode"] = 1
    executions[0]["exitstatus"] = 1

    assessment = benchmark.assess_run(
        gate_report=gate,
        group_report=groups,
        execution_reports=executions,
        expected_phase_ids=EXPECTED_PHASES,
        expected_group_ids=EXPECTED_GROUPS,
        capabilities=capabilities,
        gate_returncode=1,
    )

    assert assessment["classification"] == "diagnostic"
    assert assessment["complete"] is True
    assert assessment["acceptance_usable"] is False


def test_capability_limited_pass_is_not_acceptance_evidence() -> None:
    """A missing required capability must remain separate from passing evidence."""
    benchmark = load_module()
    gate, groups, executions, capabilities = complete_inputs()
    capabilities["chrome"]["available"] = False
    capabilities["chrome"]["reason"] = "headless launch failed"

    assessment = benchmark.assess_run(
        gate_report=gate,
        group_report=groups,
        execution_reports=executions,
        expected_phase_ids=EXPECTED_PHASES,
        expected_group_ids=EXPECTED_GROUPS,
        capabilities=capabilities,
        gate_returncode=0,
    )

    assert assessment["classification"] == "diagnostic"
    assert "required capability unavailable: chrome" in assessment["reasons"]


def test_capability_correct_complete_pass_is_acceptance_evidence() -> None:
    benchmark = load_module()
    gate, groups, executions, capabilities = complete_inputs()

    assessment = benchmark.assess_run(
        gate_report=gate,
        group_report=groups,
        execution_reports=executions,
        expected_phase_ids=EXPECTED_PHASES,
        expected_group_ids=EXPECTED_GROUPS,
        capabilities=capabilities,
        gate_returncode=0,
    )

    assert assessment == {
        "complete": True,
        "acceptance_usable": True,
        "classification": "acceptance",
        "reasons": [],
    }


def test_resource_samples_are_attributed_to_phase_window() -> None:
    """Phase rows must expose sampled CPU and peak RSS, not wall time alone."""
    benchmark = load_module()
    samples = [
        {
            "elapsed_seconds": 0.25,
            "interval_seconds": 0.25,
            "cpu_seconds": 0.20,
            "effective_cores": 0.8,
            "rss_kb": 100.0,
        },
        {
            "elapsed_seconds": 0.75,
            "interval_seconds": 0.50,
            "cpu_seconds": 0.60,
            "effective_cores": 1.2,
            "rss_kb": 700.0,
        },
        {
            "elapsed_seconds": 1.25,
            "interval_seconds": 0.50,
            "cpu_seconds": 0.10,
            "effective_cores": 0.2,
            "rss_kb": 300.0,
        },
    ]

    result = benchmark.attribute_window(samples, start=0.2, wall_seconds=0.8)

    assert result == {
        "sampled_cpu_seconds": 0.8,
        "average_effective_cores": 1.0,
        "peak_effective_cores": 1.2,
        "peak_sampled_tree_rss_kb": 700.0,
        "sample_count": 2,
    }


def test_material_selection_covers_eighty_percent_plus_resource_outliers() -> None:
    """Profiling scope must be the measured critical path plus resource outliers."""
    benchmark = load_module()
    rows = [
        {"id": "large", "wall_seconds": 60.0, "average_effective_cores": 0.9, "peak_sampled_tree_rss_kb": 100},
        {"id": "medium", "wall_seconds": 25.0, "average_effective_cores": 0.9, "peak_sampled_tree_rss_kb": 100},
        {"id": "parallel", "wall_seconds": 5.0, "average_effective_cores": 1.2, "peak_sampled_tree_rss_kb": 100},
        {"id": "memory", "wall_seconds": 4.0, "average_effective_cores": 0.3, "peak_sampled_tree_rss_kb": 600 * 1024},
        {"id": "small", "wall_seconds": 6.0, "average_effective_cores": 0.3, "peak_sampled_tree_rss_kb": 100},
    ]

    selected = benchmark.select_material_rows(rows)

    assert [row["id"] for row in selected] == [
        "large",
        "medium",
        "parallel",
        "memory",
    ]


@pytest.mark.parametrize(
    ("wall", "cpu", "expected"),
    [(5.0, 0.0, "proceed"), (0.0, 5.0, "proceed"), (4.99, 4.99, "defer")],
)
def test_decision_thresholds_are_exact(
    wall: float, cpu: float, expected: str
) -> None:
    benchmark = load_module()

    assert benchmark.classify_optimization(
        component_wall_seconds=wall,
        complete_warm_wall_seconds=100.0,
        component_cpu_seconds=cpu,
        measured=True,
    ) == expected
    assert benchmark.classify_optimization(
        component_wall_seconds=wall,
        complete_warm_wall_seconds=100.0,
        component_cpu_seconds=cpu,
        measured=False,
    ) == "unmeasured"
