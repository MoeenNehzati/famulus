"""Test the pure scheduler retained inside the certification Voyage."""

import pytest

from .. import _semantic_audit_scheduler as scheduler


def test_ready_tasks_enforces_prerequisites_and_outstanding_capacity():
    nodes = {
        "a": {"dependencies": []},
        "b": {"dependencies": []},
        "source": {"dependencies": ["a", "b"]},
    }
    required = set(nodes)
    assert scheduler.ready_tasks(nodes, required, set(), set(), 1) == ["a"]
    assert scheduler.ready_tasks(nodes, required, set(), {"a"}, 1) == []
    assert scheduler.ready_tasks(nodes, required, {"a"}, {"a"}, 1) == ["b"]
    assert scheduler.ready_tasks(nodes, required, {"a", "b"}, {"a", "b"}, 1) == ["source"]
    with pytest.raises(scheduler.SchedulerError):
        scheduler.ready_tasks(nodes, required, set(), set(), True)


def test_report_validation_uses_canonical_schema_and_assignment():
    report = {
        "schema_version": "node-certify.semantic-audit-result/v1",
        "task_id": "run:task", "verdict": "pass", "summary": "checked",
        "evidence": ["owned inputs"], "consumed_dependencies": [], "findings": [],
    }
    scheduler._validate_report(report, "run:task")
    for invalid in (None, [], {**report, "extra": True}, {**report, "findings": ["failure"]}):
        with pytest.raises(scheduler.SchedulerError):
            scheduler._validate_report(invalid, "run:task")
    with pytest.raises(scheduler.SchedulerError, match="task mismatch"):
        scheduler._validate_report(report, "different-run:task")
