"""Behavioral tests for the thin GitHub Actions repository-check transport."""

from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import officina.repository.checks.remote as remote


def test_generic_remote_transport_contains_no_platform_specific_topology() -> None:
    """Catch platform-specific matrix policy leaking into the shared transport."""

    source = Path(remote.__file__).read_text(encoding="utf-8")
    assert "macos-latest" not in source
    assert "windows-latest" not in source
    assert "Windows matrix" not in source


def test_gh_process_timeout_is_machine_classified(monkeypatch, tmp_path: Path) -> None:
    def expire(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(("gh", "auth", "status"), 20)

    monkeypatch.setattr(remote.subprocess, "run", expire)
    status = remote.main(
        [
            "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
            "--output-dir", str(tmp_path), "--timeout", "30",
        ],
        gh=remote.GhClient(tmp_path),
    )
    assert status == 2


class FakeGh:
    """Return one deterministic workflow lifecycle without network access."""

    def __init__(
        self,
        *,
        conclusion: str = "failure",
        remote_sha: str = "a" * 40,
        repository: str = "owner/repository",
        probe_os: str = "windows-latest",
        probe_task: str = "tests:shared",
        run_status: str = "completed",
        updated_at: str = "2026-08-11T00:01:00Z",
        request_id: str | None = None,
    ):
        self.calls: list[tuple[str, ...]] = []
        self.conclusion = conclusion
        self.remote_sha = remote_sha
        self.repository = repository
        self.probe_os = probe_os
        self.probe_task = probe_task
        self.run_status = run_status
        self.updated_at = updated_at
        self.request_id = request_id

    def run(self, arguments: tuple[str, ...]):
        self.calls.append(arguments)
        if arguments[:2] == ("auth", "status"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if arguments[:2] == ("repo", "view"):
            return SimpleNamespace(returncode=0, stdout=f"{self.repository}\n", stderr="")
        if arguments[:2] == ("workflow", "view"):
            return SimpleNamespace(returncode=0, stdout="Python Tests\n", stderr="")
        if arguments[0] == "api":
            return SimpleNamespace(returncode=0, stdout=f"{self.remote_sha}\n", stderr="")
        if arguments[:2] == ("workflow", "run"):
            self.request_id = next(
                value.partition("request_id=")[2]
                for value in arguments
                if value.startswith("request_id=")
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if arguments[:2] == ("run", "list"):
            assert self.request_id is not None
            request_id = self.request_id
            payload = [
                {
                    "databaseId": 123,
                    "headSha": "a" * 40,
                    "status": "queued",
                    "conclusion": "",
                    "displayTitle": f"Python Tests / {request_id}",
                    "url": "https://github.example/runs/123",
                    "createdAt": "2026-08-11T00:00:00Z",
                }
            ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if arguments[:2] == ("run", "view"):
            payload = {
                "databaseId": 123,
                "status": self.run_status,
                "conclusion": self.conclusion,
                "event": "workflow_dispatch",
                "workflowName": "Python Tests",
                "headBranch": "repair",
                "headSha": "a" * 40,
                "url": "https://github.example/runs/123",
                "displayTitle": f"Python Tests / {self.request_id}",
                "updatedAt": self.updated_at,
                "jobs": [
                    {
                        "name": "test (ubuntu-latest, combined)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/1",
                    },
                    {
                        "name": "test (macos-latest, validators)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/4",
                    },
                    {
                        "name": "test (macos-latest, tests:shared)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/8",
                    },
                    {
                        "name": "test (macos-latest, tests:performance)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/9",
                    },
                    {
                        "name": "test (windows-latest, validators)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/5",
                    },
                    {
                        "name": "test (windows-latest, tests:shared)",
                        "status": "completed",
                        "conclusion": self.conclusion,
                        "url": "https://github.example/jobs/2",
                    },
                    {
                        "name": "test (windows-latest, tests:performance)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/6",
                    },
                    {
                        "name": "test (windows-latest, tests:browser)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/7",
                    },
                    {
                        "name": f"probe ({self.probe_os}, {self.probe_task})",
                        "status": "completed",
                        "conclusion": self.conclusion,
                        "url": "https://github.example/jobs/3",
                    },
                ],
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if arguments[:2] == ("run", "download"):
            output = Path(arguments[arguments.index("--dir") + 1])
            probe = any(
                value == "mode=probe"
                for call in self.calls
                for value in call
            )
            artifact_name = (
                "repo-checks-probe-123"
                if probe
                else "repo-checks-windows-tests-shared-123"
            )
            artifact = output / artifact_name
            artifact.mkdir(parents=True)
            (artifact / "timing.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "files": [
                            {
                                "task_id": "tests:shared",
                                "path": "tests/test_broken.py",
                                "failed": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected gh call: {arguments!r}")


class UncertainDispatchGh(FakeGh):
    """Model a dispatch whose process result is unknown after GitHub accepted it."""

    def run(self, arguments: tuple[str, ...]):
        result = super().run(arguments)
        if arguments[:2] == ("workflow", "run"):
            return SimpleNamespace(returncode=1, stdout="", stderr="transport lost")
        return result


def test_remote_matrix_dispatches_collects_and_reports_every_job(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch transport that loses red matrix elements or failed-file evidence."""

    gh = FakeGh()
    status = remote.main(
        [
            "matrix",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--output-dir",
            str(tmp_path),
            "--timeout",
            "30",
        ],
        gh=gh,
        sleep=lambda _seconds: None,
    )

    assert status == 1
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 1
    assert report["mode"] == "matrix"
    assert report["expected_sha"] == "a" * 40
    assert report["overall_green"] is False
    assert [(item["os"], item["task"]) for item in report["elements"]] == [
        ("ubuntu-latest", "combined"),
        ("macos-latest", "validators"),
        ("macos-latest", "tests:shared"),
        ("macos-latest", "tests:performance"),
        ("windows-latest", "validators"),
        ("windows-latest", "tests:shared"),
        ("windows-latest", "tests:performance"),
        ("windows-latest", "tests:browser"),
    ]
    assert report["elements"][5]["failed_selectors"] == [
        "tests/test_broken.py"
    ]
    assert json.loads((tmp_path / "run-report.json").read_text(encoding="utf-8")) == report
    dispatch = next(call for call in gh.calls if call[:2] == ("workflow", "run"))
    assert "mode=matrix" in dispatch
    assert "expected_sha=" + "a" * 40 in dispatch
    assert not any("token" in value.casefold() for call in gh.calls for value in call)


def test_remote_matrix_context_resumes_one_dispatch_to_terminal_report(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch resumable setup leaking credentials or overwriting run evidence."""

    context = tmp_path / "ci-session"
    gh = FakeGh()
    arguments = [
        "matrix",
        "--ref",
        "repair",
        "--expected-sha",
        "a" * 40,
        "--context",
        str(context),
        "--timeout",
        "30",
    ]
    status = remote.main(
        arguments,
        gh=gh,
        sleep=lambda _seconds: None,
    )

    assert status == 0
    pending = json.loads(capsys.readouterr().out)
    assert pending["state"] == "pending"
    assert pending["overall_green"] is False
    assert len([call for call in gh.calls if call[:2] == ("workflow", "run")]) == 1
    assert not any(call[:2] == ("run", "view") for call in gh.calls)

    status = remote.main(
        arguments,
        gh=gh,
        sleep=lambda _seconds: None,
    )

    assert status == 1
    report = json.loads(capsys.readouterr().out)
    assert len([call for call in gh.calls if call[:2] == ("workflow", "run")]) == 1
    assert len([call for call in gh.calls if call[:2] == ("run", "view")]) == 1
    assert report["request_id"] == pending["request_id"]
    setup = json.loads((context / "context.json").read_text(encoding="utf-8"))
    request_root = context / "requests" / pending["request_key"]
    persisted = request_root / "run-report.json"
    assert json.loads(persisted.read_text(encoding="utf-8")) == report
    assert (request_root / "intent.json").is_file()
    assert (request_root / "dispatch-attempted.json").is_file()
    assert (request_root / "correlation.json").is_file()
    assert (request_root / "terminal.json").is_file()

    replay_status = remote.main(
        arguments,
        gh=gh,
        sleep=lambda _seconds: None,
    )
    assert replay_status == 1
    assert json.loads(capsys.readouterr().out) == report
    assert len([call for call in gh.calls if call[:2] == ("workflow", "run")]) == 1
    assert len([call for call in gh.calls if call[:2] == ("run", "view")]) == 1

    assert setup == {
        "schema_version": 1,
        "repository": "owner/repository",
        "workflow": "python-tests.yml",
    }
    assert not (context / "run-report.json").exists()
    serialized = json.dumps(setup).casefold()
    assert "token" not in serialized
    assert "credential" not in serialized


def test_remote_matrix_context_rejects_timeout_change_without_redispatch(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    gh = FakeGh()
    base = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context),
    ]
    assert remote.main([*base, "--timeout", "30"], gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()

    assert remote.main([*base, "--timeout", "31"], gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "context_timeout_mismatch"
    assert len([call for call in gh.calls if call[:2] == ("workflow", "run")]) == 1


def test_remote_matrix_context_recovers_uncertain_dispatch_without_redispatch(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    gh = UncertainDispatchGh()
    arguments = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]

    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "dispatch_uncertain"
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "pending"
    assert len([call for call in gh.calls if call[:2] == ("workflow", "run")]) == 1


def test_concurrent_identical_matrix_context_calls_dispatch_once(tmp_path: Path) -> None:
    context = tmp_path / "ci-session"
    gh = FakeGh()
    parsed = remote.build_parser().parse_args([
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ])

    def invoke() -> dict[str, object]:
        return remote.run(parsed, gh=gh, sleep=lambda _: None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _: invoke(), range(2)))

    assert len([call for call in gh.calls if call[:2] == ("workflow", "run")]) == 1
    assert len({str(report["request_id"]) for report in reports}) == 1


def test_matrix_context_rejects_symlinked_request_state(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    context.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (context / "requests").symlink_to(outside, target_is_directory=True)

    status = remote.main(
        [
            "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
            "--context", str(context), "--timeout", "30",
        ],
        gh=FakeGh(),
        sleep=lambda _: None,
    )
    assert status == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_context"


def test_uncorrelated_matrix_request_expires_to_stable_terminal(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    class InvisibleGh(FakeGh):
        def run(self, arguments: tuple[str, ...]):
            result = super().run(arguments)
            if arguments[:2] == ("run", "list"):
                return SimpleNamespace(returncode=0, stdout="[]", stderr="")
            return result

    context = tmp_path / "ci-session"
    gh = InvisibleGh()
    arguments = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "1",
    ]
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()
    time.sleep(1.05)
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 2
    terminal = json.loads(capsys.readouterr().out)
    assert terminal["state"] == "timed_out"
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out) == terminal


def test_final_correlation_observation_wins_over_local_deadline(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    class DelayedVisibilityGh(FakeGh):
        list_count = 0

        def run(self, arguments: tuple[str, ...]):
            result = super().run(arguments)
            if arguments[:2] == ("run", "list"):
                self.list_count += 1
                if self.list_count == 1:
                    return SimpleNamespace(returncode=0, stdout="[]", stderr="")
            return result

    context = tmp_path / "ci-session"
    gh = DelayedVisibilityGh()
    argv = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "1",
    ]
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()
    time.sleep(1.05)
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "pending"
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "completed"


def test_completion_after_persisted_deadline_is_timed_out(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    gh = FakeGh(updated_at="2099-01-01T00:00:00Z")
    arguments = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "timed_out"


def test_malformed_terminal_receipt_fails_closed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    gh = FakeGh()
    arguments = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 0
    pending = json.loads(capsys.readouterr().out)
    terminal_path = context / "requests" / pending["request_key"] / "terminal.json"
    terminal_path.write_text('{"schema_version":1,"state":"completed","overall_green":true}\n')

    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_context"


def test_matrix_context_lock_contention_returns_bounded_busy(
    capsys: pytest.CaptureFixture[str], monkeypatch, tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    context.mkdir()
    parsed = remote.build_parser().parse_args([
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ])
    request_key, _identity = remote._request_identity(parsed, "owner/repository")
    request_root = context / "requests" / request_key
    remote.ensure_private_directory(request_root, allowed_root=context)
    ticks = iter((0.0, 6.0))
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: None)

    with remote.exclusive_file_lock(request_root / ".lock", allowed_root=context):
        status = remote.main(
            [
                "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
                "--context", str(context), "--timeout", "30",
            ],
            gh=FakeGh(),
            sleep=lambda _: None,
        )

    assert status == 0
    busy = json.loads(capsys.readouterr().out)
    assert busy["state"] == "pending"
    assert busy["reason"] == "request_busy"
    assert busy["overall_green"] is False


def test_matrix_context_resumes_after_fresh_client_process(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    arguments = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(arguments, gh=FakeGh(), sleep=lambda _: None) == 0
    pending = json.loads(capsys.readouterr().out)

    restarted = FakeGh(request_id=str(pending["request_id"]))
    assert remote.main(arguments, gh=restarted, sleep=lambda _: None) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "completed"
    assert not any(call[:2] == ("workflow", "run") for call in restarted.calls)


def test_terminal_receipt_precedes_conflicting_derived_report(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    gh = FakeGh()
    arguments = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 0
    pending = json.loads(capsys.readouterr().out)
    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 1
    terminal = json.loads(capsys.readouterr().out)
    report_path = context / "requests" / pending["request_key"] / "run-report.json"
    report_path.write_text('{"schema_version":1,"conclusion":"green","overall_green":true}\n')

    assert remote.main(arguments, gh=gh, sleep=lambda _: None) == 1
    assert json.loads(capsys.readouterr().out) == terminal


def test_terminal_receipt_must_match_correlation(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    gh = FakeGh()
    argv = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    pending = json.loads(capsys.readouterr().out)
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 1
    capsys.readouterr()
    terminal_path = context / "requests" / pending["request_key"] / "terminal.json"
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["run_id"] = 999
    terminal_path.write_text(json.dumps(terminal) + "\n", encoding="utf-8")

    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_context"


def test_green_terminal_rejects_contradictory_failure_evidence(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    class GreenGh(FakeGh):
        def run(self, arguments: tuple[str, ...]):
            if arguments[:2] == ("run", "download"):
                self.calls.append(arguments)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return super().run(arguments)

    context = tmp_path / "ci-session"
    gh = GreenGh(conclusion="success")
    argv = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    pending = json.loads(capsys.readouterr().out)
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()
    terminal_path = context / "requests" / pending["request_key"] / "terminal.json"
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["elements"][0]["failed_by_task"] = {"tests:shared": ["tests/test_x.py"]}
    terminal["elements"][0]["failed_selectors"] = ["tests/test_x.py"]
    terminal_path.write_text(json.dumps(terminal) + "\n", encoding="utf-8")

    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_context"


def test_duplicate_matrix_job_identity_fails_closed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    class DuplicateJobGh(FakeGh):
        def run(self, arguments: tuple[str, ...]):
            result = super().run(arguments)
            if arguments[:2] == ("run", "view"):
                payload = json.loads(result.stdout)
                payload["jobs"].append(dict(payload["jobs"][0]))
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            return result

    context = tmp_path / "ci-session"
    gh = DuplicateJobGh(conclusion="success")
    argv = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "incomplete_matrix"


@pytest.mark.parametrize("workflow_conclusion", ["failure", "skipped"])
def test_workflow_level_non_success_prevents_green_matrix(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, workflow_conclusion: str
) -> None:
    class AuxiliaryFailureGh(FakeGh):
        def run(self, arguments: tuple[str, ...]):
            if arguments[:2] == ("run", "download"):
                self.calls.append(arguments)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            result = super().run(arguments)
            if arguments[:2] == ("run", "view"):
                payload = json.loads(result.stdout)
                payload["conclusion"] = workflow_conclusion
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            return result

    context = tmp_path / "ci-session"
    gh = AuxiliaryFailureGh(conclusion="success")
    argv = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["workflow_conclusion"] == workflow_conclusion
    assert report["conclusion"] == "red"
    assert report["overall_green"] is False


def test_concurrent_terminal_observers_converge_on_one_collection(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    gh = FakeGh()
    argv = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()
    parsed = remote.build_parser().parse_args(argv)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(
            lambda _: remote.run(parsed, gh=gh, sleep=lambda _seconds: None),
            range(2),
        ))
    assert reports[0] == reports[1]
    assert len([call for call in gh.calls if call[:2] == ("run", "view")]) == 1
    assert len([call for call in gh.calls if call[:2] == ("run", "download")]) == 1


def test_missing_completion_timestamp_fails_closed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    context = tmp_path / "ci-session"
    gh = FakeGh(updated_at="")
    argv = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    capsys.readouterr()
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "poll_failed"


@pytest.mark.parametrize(
    ("receipt", "field", "value"),
    [
        ("intent.json", "schema_version", 99),
        ("dispatch-attempted.json", "request_id", "ci-fabricated0000"),
        ("correlation.json", "run_id", "123"),
    ],
)
def test_malformed_request_receipts_fail_closed(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    receipt: str,
    field: str,
    value: object,
) -> None:
    context = tmp_path / receipt
    gh = FakeGh()
    argv = [
        "matrix", "--ref", "repair", "--expected-sha", "a" * 40,
        "--context", str(context), "--timeout", "30",
    ]
    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 0
    pending = json.loads(capsys.readouterr().out)
    path = context / "requests" / pending["request_key"] / receipt
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert remote.main(argv, gh=gh, sleep=lambda _: None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_context"


def test_remote_context_survives_restart_and_appends_immutable_reports(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch a new runner process recreating setup or replacing prior evidence."""

    context = tmp_path / "ci-session"
    arguments = [
        "probe",
        "--ref",
        "repair",
        "--expected-sha",
        "a" * 40,
        "--os",
        "windows-latest",
        "--task",
        "tests:shared",
        "--selector",
        "tests/test_broken.py",
        "--context",
        str(context),
    ]

    assert remote.main(arguments, gh=FakeGh(), sleep=lambda _seconds: None) == 1
    first = json.loads(capsys.readouterr().out)
    original_setup = (context / "context.json").read_bytes()

    assert remote.main(arguments, gh=FakeGh(), sleep=lambda _seconds: None) == 1
    second = json.loads(capsys.readouterr().out)

    assert first["request_id"] != second["request_id"]
    assert (context / "context.json").read_bytes() == original_setup
    reports = sorted((context / "runs").glob("*/run-report.json"))
    assert len(reports) == 2
    assert {json.loads(path.read_text(encoding="utf-8"))["request_id"] for path in reports} == {
        first["request_id"],
        second["request_id"],
    }


def test_remote_context_revalidates_live_auth_and_rejects_repository_mismatch(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch persisted setup bypassing auth or silently switching repositories."""

    context = tmp_path / "ci-session"
    context.mkdir()
    (context / "context.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "owner/repository",
                "workflow": "python-tests.yml",
            }
        ),
        encoding="utf-8",
    )
    gh = FakeGh(repository="other/repository")
    status = remote.main(
        [
            "matrix",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--context",
            str(context),
        ],
        gh=gh,
        sleep=lambda _seconds: None,
    )

    assert status == 2
    assert json.loads(capsys.readouterr().out)["error"] == "context_mismatch"
    assert gh.calls[0][:2] == ("auth", "status")
    assert not any(call[:2] == ("workflow", "run") for call in gh.calls)


def test_remote_probe_replays_failed_files_from_a_prior_matrix_report(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch report replay that broadens from failed files to unrelated tests."""

    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "owner/repository",
                "elements": [
                    {
                        "os": "windows-latest",
                        "task": "tests:shared",
                        "conclusion": "failure",
                        "failed_selectors": [
                            "tests/test_broken.py",
                            "tests/test_other.py::test_case",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    gh = FakeGh(conclusion="success")

    status = remote.main(
        [
            "probe",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--os",
            "windows-latest",
            "--task",
            "tests:shared",
            "--from-report",
            str(source),
            "--output-dir",
            str(tmp_path / "probe"),
        ],
        gh=gh,
        sleep=lambda _seconds: None,
    )

    assert status == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "probe"
    assert report["overall_green"] is False
    dispatch = next(call for call in gh.calls if call[:2] == ("workflow", "run"))
    assert "mode=probe" in dispatch
    assert (
        'selector=["tests/test_broken.py","tests/test_other.py::test_case"]'
        in dispatch
    )


@pytest.mark.parametrize(
    ("selector_arguments", "expected_field"),
    [
        (
            ["--selector", "tests/test_broken.py"],
            'selector=["tests/test_broken.py"]',
        ),
        (
            [
                "--selector",
                "tests/test_broken.py",
                "--selector",
                "tests/test_other.py::test_case",
            ],
            'selector=["tests/test_broken.py","tests/test_other.py::test_case"]',
        ),
    ],
)
def test_remote_probe_preserves_direct_selector_cardinality_in_dispatch(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    selector_arguments: list[str],
    expected_field: str,
) -> None:
    """Catch direct selectors changing shape or collapsing to the final value."""

    gh = FakeGh(conclusion="success")
    status = remote.main(
        [
            "probe",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--os",
            "windows-latest",
            "--task",
            "tests:shared",
            *selector_arguments,
            "--output-dir",
            str(tmp_path),
        ],
        gh=gh,
        sleep=lambda _seconds: None,
    )

    assert status == 0
    report = json.loads(capsys.readouterr().out)
    assert report["workflow"] == "python-tests.yml"
    assert report["requested_selectors"] == selector_arguments[1::2]
    dispatch = next(call for call in gh.calls if call[:2] == ("workflow", "run"))
    assert expected_field in dispatch


def test_exact_sha_mismatch_fails_before_dispatch(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch remote execution against a branch head other than the requested SHA."""

    gh = FakeGh(remote_sha="b" * 40)
    status = remote.main(
        [
            "matrix",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--output-dir",
            str(tmp_path),
        ],
        gh=gh,
        sleep=lambda _seconds: None,
    )

    assert status == 2
    assert json.loads(capsys.readouterr().out)["error"] == "candidate_sha_mismatch"
    assert not any(call[:2] == ("workflow", "run") for call in gh.calls)


def test_invalid_remote_arguments_emit_one_machine_error(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch argparse exits that leave callers without a JSON diagnostic."""

    status = remote.main(
        [
            "matrix",
            "--ref",
            "repair",
            "--output-dir",
            str(tmp_path),
        ],
        gh=FakeGh(),
    )

    captured = capsys.readouterr()
    assert status == 2
    assert json.loads(captured.out) == {
        "schema_version": 1,
        "error": "invalid_arguments",
    }
    assert "usage:" not in captured.err


def test_probe_rejects_a_job_outside_the_requested_element(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch correlation that accepts a probe for the wrong operating system."""

    status = remote.main(
        [
            "probe",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--os",
            "windows-latest",
            "--task",
            "tests:shared",
            "--whole-element",
            "--output-dir",
            str(tmp_path),
        ],
        gh=FakeGh(conclusion="success", probe_os="ubuntu-latest"),
        sleep=lambda _seconds: None,
    )

    assert status == 2
    assert json.loads(capsys.readouterr().out)["error"] == "unexpected_probe_element"


def test_probe_accepts_the_complete_combined_matrix_element(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch a whole-element interface that cannot reproduce combined CI jobs."""

    status = remote.main(
        [
            "probe",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--os",
            "macos-latest",
            "--task",
            "combined",
            "--whole-element",
            "--output-dir",
            str(tmp_path),
        ],
        gh=FakeGh(
            conclusion="success",
            probe_os="macos-latest",
            probe_task="combined",
        ),
        sleep=lambda _seconds: None,
    )

    assert status == 0
    report = json.loads(capsys.readouterr().out)
    assert report["elements"][0]["task"] == "combined"


def test_from_report_rejects_an_unclassified_empty_failure_set(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch empty selector evidence being silently broadened to a whole task."""

    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "owner/repository",
                "elements": [
                    {
                        "os": "windows-latest",
                        "task": "tests:shared",
                        "conclusion": "failure",
                        "failed_selectors": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = remote.main(
        [
            "probe",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--os",
            "windows-latest",
            "--task",
            "tests:shared",
            "--from-report",
            str(source),
            "--output-dir",
            str(tmp_path / "output"),
        ],
        gh=FakeGh(),
        sleep=lambda _seconds: None,
    )

    assert status == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_report"


def test_combined_from_report_is_rejected_before_dispatch(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch component selectors being replayed through an unselectable combined task."""

    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    gh = FakeGh()
    status = remote.main(
        [
            "probe",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--os",
            "macos-latest",
            "--task",
            "combined",
            "--from-report",
            str(source),
            "--output-dir",
            str(tmp_path / "output"),
        ],
        gh=gh,
    )

    assert status == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_selector"
    assert not any(call[:2] == ("workflow", "run") for call in gh.calls)


def test_artifact_failures_preserve_task_ownership_across_timing_files(
    tmp_path: Path,
) -> None:
    """Catch sentinel and native failures being overwritten inside one job artifact."""

    artifact = tmp_path / "repo-checks-macos-combined-123"
    artifact.mkdir()
    for filename, task, path in (
        ("timing.json", "tests:shared", "tests/test_a.py"),
        ("portability.json", "tests:portability", "tests/test_b.py"),
        (
            "native-keyring.json",
            "native:keyring",
            "tests/test_officina_secret_store.py",
        ),
    ):
        (artifact / filename).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "files": [
                        {"task_id": task, "path": path, "failed": 1}
                    ],
                }
            ),
            encoding="utf-8",
        )

    assert remote._artifact_failures(tmp_path) == {
        artifact.name: {
            "native:keyring": ["tests/test_officina_secret_store.py"],
            "tests:portability": ["tests/test_b.py"],
            "tests:shared": ["tests/test_a.py"],
        }
    }


def test_remote_run_ignores_stale_artifacts_in_a_reused_output_directory(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Catch a new run reporting selectors left by an older run."""

    stale = tmp_path / "artifacts" / "repo-checks-probe-99"
    stale.mkdir(parents=True)
    (stale / "timing.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": [
                    {
                        "task_id": "tests:shared",
                        "path": "tests/test_stale.py",
                        "failed": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = remote.main(
        [
            "probe",
            "--ref",
            "repair",
            "--expected-sha",
            "a" * 40,
            "--os",
            "windows-latest",
            "--task",
            "tests:shared",
            "--whole-element",
            "--output-dir",
            str(tmp_path),
        ],
        gh=FakeGh(),
        sleep=lambda _seconds: None,
    )

    assert status == 1
    report = json.loads(capsys.readouterr().out)
    assert report["elements"][0]["failed_selectors"] == [
        "tests/test_broken.py"
    ]
