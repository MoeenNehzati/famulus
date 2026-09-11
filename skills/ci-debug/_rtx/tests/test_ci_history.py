"""Deterministic semantic and publication tests for CI history analysis."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


RTX = Path(__file__).resolve().parents[1]


def load():
    if str(RTX) not in sys.path:
        sys.path.insert(0, str(RTX))
    spec = importlib.util.spec_from_file_location("ci_history_under_test", RTX / "_ci_history.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def attempt(number: int, conclusion: str | None, *, status: str = "completed", occurrences=()):
    return {
        "attempt_number": number,
        "status": status,
        "conclusion": conclusion,
        "occurrences": list(occurrences),
        "jobs": [],
    }


def run(number: int, conclusion: str | None, *, complete: bool = True, evidence: bool = True, occurrences=()):
    return {
        "run_id": number,
        "run_number": number,
        "head_sha": f"sha-{number}",
        "expected_attempt_count": 1,
        "attempt_listing_complete": complete,
        "failure_evidence_complete": evidence,
        "attempts": [attempt(1, conclusion, occurrences=occurrences)] if conclusion is not None else [],
    }


def occurrence(test_id: str, run_number: int):
    return {
        "canonical": test_id,
        "exact": test_id + "[case]",
        "run_id": run_number,
        "run_number": run_number,
        "attempt_number": 1,
        "job_id": 9,
        "step_ordinal": 2,
        "log_path": "log.txt",
        "source_offset": 4,
    }


def test_latest_attempt_is_authoritative_and_nonterminal_censors() -> None:
    module = load()
    rerun = run(2, "failure")
    rerun["attempts"].append(attempt(2, "success"))
    rerun["expected_attempt_count"] = 2
    unknown = run(3, "success")
    unknown["attempts"][-1] = attempt(2, None, status="in_progress")
    unknown["expected_attempt_count"] = 2
    built = module.build_episodes([run(1, "success"), rerun, unknown, run(4, "success")])
    assert built["episodes"] == []
    assert any(item["side"] == "interior" and 3 in item["run_ids"] for item in built["censored_spans"])


def test_missing_or_mismatched_attempt_identity_is_not_authoritative() -> None:
    module = load()
    missing = run(2, "failure")
    missing.pop("expected_attempt_count")
    mismatched = run(3, "failure")
    mismatched["expected_attempt_count"] = 2
    built = module.build_episodes([run(1, "success"), missing, mismatched, run(4, "success")])
    assert built["episodes"] == []
    assert {run_id for span in built["censored_spans"] for run_id in span["run_ids"]} >= {1, 2, 3}


def test_run_index_sequence_gap_prevents_episode_bridge() -> None:
    module = load()
    report = module.build_failure_report({
        "runs": [run(1, "success"), run(2, "failure"), run(3, "success")],
        "sequence_gaps": [{"older_run_id": 2, "newer_run_id": 3, "reason": "malformed_run_index_item"}],
    }, {"comparisons": []})
    assert report["episodes"] == []
    assert any(span["reason"] == "run_index_evidence_gap" and span["run_ids"] == [1, 2] for span in report["censored_spans"])


def test_run_index_boundary_gaps_have_correct_censor_side() -> None:
    module = load()
    built = module.build_episodes([run(2, "success")], [
        {"older_run_id": None, "newer_run_id": 2},
        {"older_run_id": 2, "newer_run_id": None},
    ])
    sides = {span["side"] for span in built["censored_spans"] if span["reason"] == "run_index_evidence_gap"}
    assert sides == {"leading", "trailing"}


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([run(1, "failure"), run(2, "success")], [1]),
        ([run(1, "failure"), run(2, "failure")], [1, 2]),
        ([run(1, "success"), run(2, None, complete=False), run(3, "failure"), run(4, "success")], [1, 2]),
    ],
)
def test_censored_spans_retain_their_runs(runs, expected) -> None:
    module = load()
    built = module.build_episodes(runs)
    retained = [run_id for span in built["censored_spans"] for run_id in span["run_ids"]]
    assert all(run_id in retained for run_id in expected)
    if any(not item["attempt_listing_complete"] for item in runs):
        assert built["episodes"] == []


def test_recurrence_uncertainty_is_independent_of_discovery_order() -> None:
    module = load()
    test_id = "tests/test_a.py::test_a"
    snapshot = {
        "runs": [
            run(1, "success"),
            run(2, "failure", evidence=False),
            run(3, "success"),
            run(4, "failure", occurrences=[occurrence(test_id, 4)]),
            run(5, "success"),
        ]
    }
    record = module.build_failure_report(snapshot, {"comparisons": []})["tests"][0]
    assert (record["k"], record["u"], record["E"]) == (1, 1, 2)


def test_runtime_uses_all_attempts_and_only_complete_jobs_for_envelope() -> None:
    module = load()
    jobs = [
        {"name": "job", "labels": ["linux"], "started_at": "2026-01-01T00:00:00Z", "completed_at": None, "steps": []},
        {"name": "job", "labels": ["linux"], "started_at": "2026-01-01T00:01:00Z", "completed_at": "2026-01-01T00:01:02Z", "steps": []},
        {"name": "job", "labels": ["linux"], "started_at": None, "completed_at": "2026-01-01T00:10:00Z", "steps": []},
    ]
    item = run(1, "success")
    item["attempts"] = [
        {**attempt(1, "failure"), "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:01Z", "run_started_at": None, "start_origin_proven": False, "jobs": jobs},
        {**attempt(2, "success"), "created_at": "2026-01-01T00:02:00Z", "updated_at": "2026-01-01T00:02:03Z", "run_started_at": None, "start_origin_proven": False, "jobs": []},
    ]
    report = module.build_runtime_report({"runs": [item]})
    assert report["metrics"]["api_update_span_ms"]["total_ms"] == 4000
    assert report["metrics"]["observed_job_execution_envelope_ms"]["total_ms"] == 2000
    assert report["job_groups"][0]["missing"] == 2


def test_runtime_preserves_step_ordinal_and_classifies_runner_from_label_evidence() -> None:
    module = load()
    item = run(1, "success")
    item["attempts"] = [{
        **attempt(1, "success"),
        "created_at": None,
        "updated_at": None,
        "run_started_at": None,
        "start_origin_proven": False,
        "jobs": [{
            "name": " tests ",
            "labels": ["ubuntu-latest", "x64"],
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:00:02Z",
            "steps": [{
                "step_ordinal": 7,
                "name": " pytest ",
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:00:01Z",
            }],
        }],
    }]
    report = module.build_runtime_report({"runs": [item]})
    job = report["job_groups"][0]
    step = report["step_groups"][0]
    assert step["step_ordinal"] == 7
    assert (job["runner_hosting_classification"], job["runner_os_classification"]) == ("hosted", "linux")
    assert job["runner_classification_basis"] == {
        "source": "github_job_labels",
        "labels": ["ubuntu-latest", "x64"],
        "hosting_matches": [{"label": "ubuntu-latest", "classification": "hosted"}],
        "os_matches": [{"label": "ubuntu-latest", "classification": "linux"}],
    }
    assert step["runner_classification_basis"] == job["runner_classification_basis"]


def test_publication_rejects_tampering_and_undeclared_files(tmp_path: Path) -> None:
    module = load()
    root = module.reserve_private_root(tmp_path / "published")
    module.publish_tree(root, manifest_fields={"kind": "fixture"}, members={"runs.json": b"{}\n"}, outcome="collected", gaps=[])
    module.load_published_tree(root)
    (root / "extra.txt").write_text("surprise", encoding="utf-8")
    with pytest.raises(module.HistoryError, match="undeclared"):
        module.load_published_tree(root)


def test_publication_rejects_unsafe_member_shapes_and_modes(tmp_path: Path) -> None:
    module = load()
    root = module.reserve_private_root(tmp_path / "published")
    module.publish_tree(root, manifest_fields={"kind": "fixture"}, members={"runs.json": b"{}\n"}, outcome="collected", gaps=[])
    (root / "runs.json").chmod(0o644)
    with pytest.raises(module.HistoryError, match="not private"):
        module.load_published_tree(root)
    other = module.reserve_private_root(tmp_path / "other")
    with pytest.raises(module.HistoryError) as raised:
        module.publish_tree(other, manifest_fields={}, members={"C:\\escape.txt": b"x"}, outcome="collected", gaps=[])
    assert raised.value.code == "invalid_member"


def test_publication_rejects_digest_consistent_non_object_member(tmp_path: Path) -> None:
    module = load()
    root = module.reserve_private_root(tmp_path / "published")
    module.publish_tree(root, manifest_fields={"kind": "fixture"}, members={"runs.json": b"{}\n"}, outcome="collected", gaps=[])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["members"] = ["bad"]
    without_digest = {key: value for key, value in manifest.items() if key != "snapshot_digest"}
    manifest["snapshot_digest"] = module.sha256_bytes(module.canonical_json_bytes(without_digest))
    manifest_bytes = module.canonical_json_bytes(manifest)
    publication = json.loads((root / "publication.json").read_text(encoding="utf-8"))
    publication["snapshot_digest"] = manifest["snapshot_digest"]
    publication["manifest_sha256"] = module.sha256_bytes(manifest_bytes)
    (root / "manifest.json").write_bytes(manifest_bytes)
    (root / "publication.json").write_bytes(module.canonical_json_bytes(publication))
    with pytest.raises(module.HistoryError, match="must be an object"):
        module.load_published_tree(root)


def test_publication_rejects_symlinked_root_and_public_modes(tmp_path: Path) -> None:
    module = load()
    root = module.reserve_private_root(tmp_path / "published")
    module.publish_tree(root, manifest_fields={"kind": "fixture"}, members={"runs.json": b"{}\n"}, outcome="collected", gaps=[])
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(module.HistoryError):
        module.load_published_tree(alias)
    (root / "manifest.json").chmod(0o644)
    with pytest.raises(module.HistoryError, match="not private"):
        module.load_published_tree(root)


def test_nested_publication_parent_swap_cannot_redirect_creation(
    tmp_path: Path, monkeypatch
) -> None:
    module = load()
    root = module.reserve_private_root(tmp_path / "published")
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    real_ensure = module.ensure_private_directory

    def swap_before_confined_create(path: Path, *, allowed_root: Path) -> None:
        parent = root / "nested"
        assert Path(path) == parent
        assert not parent.exists()
        try:
            parent.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            # famulus-skip: category=capability-unavailable; reason=symlink creation unavailable; alternate=confined primitive unit coverage
            pytest.skip("symlinks unavailable")
        real_ensure(path, allowed_root=allowed_root)

    monkeypatch.setattr(module, "ensure_private_directory", swap_before_confined_create)
    with pytest.raises(module.HistoryError) as raised:
        module.publish_tree(
            root,
            manifest_fields={"kind": "fixture"},
            members={"nested/runs.json": b"private\n"},
            outcome="collected",
            gaps=[],
        )

    assert raised.value.code == "invalid_member"
    assert not (outside / "runs.json").exists()


def test_member_swap_between_path_check_and_read_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    module = load()
    root = module.reserve_private_root(tmp_path / "published")
    module.publish_tree(
        root,
        manifest_fields={"kind": "fixture"},
        members={"runs.json": b"trusted\n"},
        outcome="collected",
        gaps=[],
    )
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"trusted\n")
    target = root / "runs.json"
    retained = root / "retained.json"
    real_read = module.read_regular_file_bytes
    swapped = False

    def swap_before_confined_read(path: Path, *, allowed_root: Path) -> bytes:
        nonlocal swapped
        if Path(path) == target and not swapped:
            swapped = True
            target.rename(retained)
            try:
                target.symlink_to(outside)
            except (NotImplementedError, OSError):
                retained.rename(target)
                # famulus-skip: category=capability-unavailable; reason=symlink creation unavailable; alternate=confined primitive unit coverage
                pytest.skip("symlinks unavailable")
        return real_read(path, allowed_root=allowed_root)

    monkeypatch.setattr(module, "read_regular_file_bytes", swap_before_confined_read)
    with pytest.raises(module.HistoryError) as raised:
        module.load_published_tree(root)

    assert raised.value.code == "invalid_member"
    assert os.path.islink(target)


def test_parser_and_serializers_reject_or_escape_hostile_values() -> None:
    module = load()
    assert module.parse_pytest_node("FAILED tests\\test_a.py::TestA::test_x[a[b]c]")["canonical"] == "tests/test_a.py::TestA::test_x"
    assert module.parse_pytest_node("[gw0] FAILED tests/test_a.py::test_x")["canonical"] == "tests/test_a.py::test_x"
    assert module.parse_pytest_node("../tests/test_a.py::test_x")["reason"] == "invalid_path"
    assert module.parse_pytest_node("C:\\tests\\test_a.py::test_x")["reason"] == "invalid_path"
    assert module.csv_bytes(["value"], [{"value": " =cmd"}]).decode().splitlines()[1].startswith("' ")
    assert b"&lt;script&gt;" in module.html_bytes("title", ["value"], [{"value": "<script>"}])


def test_episode_keeps_all_occurrences_but_counts_one_incidence() -> None:
    module = load()
    test_id = "tests/test_a.py::test_a"
    first = occurrence(test_id, 2)
    second = {**first, "job_id": 10, "exact": test_id + "[other]"}
    built = module.build_episodes([
        run(1, "success"),
        run(2, "failure", occurrences=[second, first, first]),
        run(3, "success"),
    ])
    episode = built["episodes"][0]
    assert len(episode["incidence"]) == 1
    assert len(episode["occurrences"]) == 2
    assert episode["incidence"][0]["first_observation"]["job_id"] == 9
