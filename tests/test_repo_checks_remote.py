"""Behavioral tests for the thin GitHub Actions repository-check transport."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from officina.repo_checks import remote


def test_generic_remote_transport_contains_no_platform_specific_topology() -> None:
    """Catch platform-specific matrix policy leaking into the shared transport."""

    source = Path(remote.__file__).read_text(encoding="utf-8")
    assert "macos-latest" not in source
    assert "windows-latest" not in source
    assert "Windows matrix" not in source


class FakeGh:
    """Return one deterministic workflow lifecycle without network access."""

    def __init__(
        self,
        *,
        conclusion: str = "failure",
        remote_sha: str = "a" * 40,
        probe_os: str = "windows-latest",
        probe_task: str = "tests:shared",
    ):
        self.calls: list[tuple[str, ...]] = []
        self.conclusion = conclusion
        self.remote_sha = remote_sha
        self.probe_os = probe_os
        self.probe_task = probe_task

    def run(self, arguments: tuple[str, ...]):
        self.calls.append(arguments)
        if arguments[:2] == ("auth", "status"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if arguments[:2] == ("repo", "view"):
            return SimpleNamespace(returncode=0, stdout="owner/repository\n", stderr="")
        if arguments[:2] == ("workflow", "view"):
            return SimpleNamespace(returncode=0, stdout="Python Tests\n", stderr="")
        if arguments[0] == "api":
            return SimpleNamespace(returncode=0, stdout=f"{self.remote_sha}\n", stderr="")
        if arguments[:2] == ("workflow", "run"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if arguments[:2] == ("run", "list"):
            request_id = next(
                value.partition("request_id=")[2]
                for call in self.calls
                for value in call
                if value.startswith("request_id=")
            )
            payload = [
                {
                    "databaseId": 123,
                    "headSha": "a" * 40,
                    "status": "queued",
                    "conclusion": "",
                    "displayTitle": request_id,
                    "url": "https://github.example/runs/123",
                    "createdAt": "2026-08-11T00:00:00Z",
                }
            ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if arguments[:2] == ("run", "view"):
            payload = {
                "status": "completed",
                "conclusion": self.conclusion,
                "headSha": "a" * 40,
                "url": "https://github.example/runs/123",
                "displayTitle": "request",
                "jobs": [
                    {
                        "name": "test (ubuntu-latest, combined)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/1",
                    },
                    {
                        "name": "test (macos-latest, combined)",
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.example/jobs/4",
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
        ("macos-latest", "combined"),
        ("windows-latest", "validators"),
        ("windows-latest", "tests:shared"),
        ("windows-latest", "tests:performance"),
    ]
    assert report["elements"][3]["failed_selectors"] == [
        "tests/test_broken.py"
    ]
    assert json.loads((tmp_path / "run-report.json").read_text(encoding="utf-8")) == report
    dispatch = next(call for call in gh.calls if call[:2] == ("workflow", "run"))
    assert "mode=matrix" in dispatch
    assert "expected_sha=" + "a" * 40 in dispatch
    assert not any("token" in value.casefold() for call in gh.calls for value in call)


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
    json.loads(capsys.readouterr().out)
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
