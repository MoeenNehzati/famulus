"""Offline end-to-end tests for the failure-episode reporter."""

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


def snapshot(tmp_path: Path) -> tuple[object, Path]:
    core = load("report_failure_core", "_ci_history.py")
    root = tmp_path / "analysis"
    root.mkdir(mode=0o700)
    target = core.reserve_private_root(root / "snapshot")
    runs = {"schema_version": 1, "runs": []}
    provenance = {"schema_version": 1, "comparisons": []}
    core.publish_tree(target, manifest_fields={"kind": "fixture"}, members={"runs.json": core.canonical_json_bytes(runs), "git-evidence.json": core.canonical_json_bytes(provenance)}, outcome="collected", gaps=[])
    return core, target


def test_failure_reporter_is_offline_and_publishes_valid_tree(tmp_path: Path, capsys) -> None:
    core, source = snapshot(tmp_path)
    module = load("report_failure_main", "_report_test_failures_between_green_runs.py")
    destination = source.parent / "failure-episodes"
    assert module.main(["--snapshot-dir", str(source), "--report-dir", str(destination)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["state"] == "reported"
    manifest, members = core.load_published_tree(destination)
    assert manifest["source_snapshot_digest"] == receipt["snapshot_digest"]
    assert set(members) == {"failure-episodes.json", "failure-episodes.csv", "failure-episodes.html"}


def test_failure_reporter_rejects_destination_outside_coordinator(tmp_path: Path, capsys) -> None:
    _core, source = snapshot(tmp_path)
    module = load("report_failure_destination", "_report_test_failures_between_green_runs.py")
    assert module.main(["--snapshot-dir", str(source), "--report-dir", str(tmp_path / "elsewhere")]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_report_destination"
