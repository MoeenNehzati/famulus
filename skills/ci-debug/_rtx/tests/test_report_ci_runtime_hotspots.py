"""Offline end-to-end tests for the runtime-hotspot reporter."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


RTX = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    if str(RTX) not in sys.path:
        sys.path.insert(0, str(RTX))
    spec = importlib.util.spec_from_file_location(name, RTX / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_reporter_publishes_same_snapshot_identity(tmp_path: Path, capsys) -> None:
    core = load("report_runtime_core", "_ci_history.py")
    coordinator = tmp_path / "analysis"
    coordinator.mkdir(mode=0o700)
    source = core.reserve_private_root(coordinator / "snapshot")
    core.publish_tree(source, manifest_fields={"kind": "fixture"}, members={"runs.json": core.canonical_json_bytes({"schema_version": 1, "runs": []})}, outcome="collected", gaps=[])
    module = load("report_runtime_main", "_report_ci_runtime_hotspots.py")
    destination = coordinator / "runtime-hotspots"
    assert module.main(["--snapshot-dir", str(source), "--report-dir", str(destination)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    manifest, members = core.load_published_tree(destination)
    assert receipt["snapshot_digest"] == manifest["source_snapshot_digest"]
    assert set(members) == {"runtime-hotspots.json", "runtime-hotspots.csv", "runtime-hotspots.html"}


def test_rows_emit_runner_classification_and_evidence_basis() -> None:
    module = load("report_runtime_rows", "_report_ci_runtime_hotspots.py")
    report = {
        "metrics": {},
        "job_groups": [{
            "job_name": "tests",
            "labels": ["ubuntu-latest"],
            "runner_hosting_classification": "hosted",
            "runner_os_classification": "linux",
            "runner_classification_basis": {
                "source": "github_job_labels",
                "labels": ["ubuntu-latest"],
                "hosting_matches": [{"label": "ubuntu-latest", "classification": "hosted"}],
                "os_matches": [{"label": "ubuntu-latest", "classification": "linux"}],
            },
            "n": 1,
        }],
        "step_groups": [],
    }
    row = module._rows(report)[0]
    assert row["runner_hosting_classification"] == "hosted"
    assert row["runner_os_classification"] == "linux"
    assert json.loads(row["runner_classification_basis"])["labels"] == ["ubuntu-latest"]
