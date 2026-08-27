from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from .. import _semantic_audit_scheduler as scheduler


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _dag(repository: Path) -> dict[str, object]:
    return {
        "schema_version": "officina.certification-dependency-dag/v1",
        "repository": str(repository.resolve()),
        "nodes": [
            {
                "id": "a",
                "kind": "interface",
                "owner_node_id": "b",
                "dependencies": [],
            },
            {
                "id": "b",
                "kind": "behavioral-source",
                "owner_node_id": None,
                "dependencies": ["a"],
            },
            {
                "id": "c",
                "kind": "module",
                "owner_node_id": None,
                "dependencies": ["b"],
            },
        ],
    }


def _report(task_id: str, verdict: str = "pass") -> dict[str, object]:
    return {
        "schema_version": "skill-certifier.semantic-audit-result/v1",
        "task_id": task_id,
        "verdict": verdict,
        "summary": "checked",
        "evidence": ["evidence"],
        "consumed_dependencies": [],
        "findings": [] if verdict == "pass" else ["failed"],
    }


def test_claim_enforces_dependency_order_and_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(scheduler, "RUN_ROOT", run_root)
    dag_file = tmp_path / "dag.json"
    drift_file = tmp_path / "drift.json"
    _write_json(dag_file, _dag(tmp_path))
    _write_json(drift_file, {"stale_vertices": ["a", "b", "c"]})
    prefix = run_root / "run"

    scheduler.initialize(prefix, dag_file, drift_file)
    first = scheduler.claim(prefix, 2)
    assert [item["task_id"] for item in first["selected"]] == ["a"]

    report_file = tmp_path / "a-report.json"
    _write_json(report_file, _report("a"))
    scheduler.complete(prefix, "a", report_file)
    second = scheduler.claim(prefix, 2)
    assert [item["task_id"] for item in second["selected"]] == ["b"]
    packet = json.loads(Path(second["selected"][0]["input_file"]).read_text())
    assert packet["repository"] == str(tmp_path.resolve())
    assert packet["dependency_reports"][0]["task_id"] == "a"


def test_concurrent_capacity_is_accounted_from_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(scheduler, "RUN_ROOT", run_root)
    dag = _dag(tmp_path)
    dag["nodes"] = [
        {"id": "a", "kind": "module", "owner_node_id": None, "dependencies": []},
        {"id": "b", "kind": "module", "owner_node_id": None, "dependencies": []},
        {"id": "c", "kind": "module", "owner_node_id": None, "dependencies": []},
    ]
    dag_file = tmp_path / "dag.json"
    drift_file = tmp_path / "drift.json"
    _write_json(dag_file, dag)
    _write_json(drift_file, {"stale_vertices": ["a", "b", "c"]})
    prefix = run_root / "run"
    scheduler.initialize(prefix, dag_file, drift_file)

    assert len(scheduler.claim(prefix, 2)["selected"]) == 2
    assert scheduler.claim(prefix, 2)["selected"] == []


def test_simultaneous_claims_are_disjoint_and_share_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(scheduler, "RUN_ROOT", run_root)
    dag = _dag(tmp_path)
    dag["nodes"] = [
        {"id": "a", "kind": "module", "owner_node_id": None, "dependencies": []},
        {"id": "b", "kind": "module", "owner_node_id": None, "dependencies": []},
        {"id": "c", "kind": "module", "owner_node_id": None, "dependencies": []},
    ]
    dag_file = tmp_path / "dag.json"
    drift_file = tmp_path / "drift.json"
    _write_json(dag_file, dag)
    _write_json(drift_file, {"stale_vertices": ["a", "b", "c"]})
    prefix = run_root / "run"
    scheduler.initialize(prefix, dag_file, drift_file)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _unused: scheduler.claim(prefix, 2), range(2)))

    selected = [
        item["task_id"]
        for result in results
        for item in result["selected"]
    ]
    assert len(selected) == 2
    assert len(set(selected)) == 2
    assert scheduler.status(prefix)["in_progress_count"] == 2


def test_reject_report_fails_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(scheduler, "RUN_ROOT", run_root)
    dag = _dag(tmp_path)
    dag["nodes"] = [dag["nodes"][2]]
    dag["nodes"][0]["dependencies"] = []
    dag_file = tmp_path / "dag.json"
    drift_file = tmp_path / "drift.json"
    report_file = tmp_path / "report.json"
    _write_json(dag_file, dag)
    _write_json(drift_file, {"stale_vertices": ["c"]})
    _write_json(report_file, _report("c", "reject"))
    prefix = run_root / "run"
    scheduler.initialize(prefix, dag_file, drift_file)
    scheduler.claim(prefix, 1)

    result = scheduler.complete(prefix, "c", report_file)
    assert result["status"] == "failed"
    assert scheduler.status(prefix)["in_progress"] == []


@pytest.mark.parametrize("field", ["evidence", "findings"])
def test_complete_rejects_non_array_report_collections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(scheduler, "RUN_ROOT", run_root)
    dag = _dag(tmp_path)
    dag["nodes"] = [dag["nodes"][2]]
    dag["nodes"][0]["dependencies"] = []
    dag_file = tmp_path / "dag.json"
    drift_file = tmp_path / "drift.json"
    report_file = tmp_path / "report.json"
    report = _report("c")
    report[field] = "not-an-array"
    _write_json(dag_file, dag)
    _write_json(drift_file, {"stale_vertices": ["c"]})
    _write_json(report_file, report)
    prefix = run_root / "run"
    scheduler.initialize(prefix, dag_file, drift_file)
    scheduler.claim(prefix, 1)

    result = scheduler.complete(prefix, "c", report_file)

    assert result["status"] == "failed"
    assert result["reason"] == "semantic audit evidence/findings must be arrays"


def test_prefix_suffix_is_preserved_in_owned_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(scheduler, "RUN_ROOT", run_root)
    dag_file = tmp_path / "dag.json"
    drift_file = tmp_path / "drift.json"
    _write_json(dag_file, _dag(tmp_path))
    _write_json(drift_file, {"stale_vertices": []})

    scheduler.initialize(run_root / "run.v1", dag_file, drift_file)

    assert (run_root / "run.v1.dag.json").is_file()
    assert (run_root / "run.v1.state.json").is_file()
    assert not (run_root / "run.dag.json").exists()


def test_fail_requires_an_active_in_progress_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(scheduler, "RUN_ROOT", run_root)
    dag_file = tmp_path / "dag.json"
    drift_file = tmp_path / "drift.json"
    _write_json(dag_file, _dag(tmp_path))
    _write_json(drift_file, {"stale_vertices": ["a", "b", "c"]})
    prefix = run_root / "run"
    scheduler.initialize(prefix, dag_file, drift_file)

    with pytest.raises(scheduler.SchedulerError, match="not in progress"):
        scheduler.fail(prefix, "b", "worker lost")

    assert scheduler.status(prefix)["status"] == "active"
