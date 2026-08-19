from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "_github_issue.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from .. import _github_issue as issue


def _write_config(root: Path, body: str) -> Path:
    """Write one complete repository configuration carrying the given feedback table."""
    (root / "skills").mkdir(exist_ok=True)
    config = root / "officina.toml"
    config.write_text(
        'schema_version = 1\n\n[modules]\nroots = ["skills"]\n\n' + body,
        encoding="utf-8",
    )
    return config


def test_feedback_address_reads_the_owning_repository_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(
        tmp_path,
        '[feedback]\nemail = "someone@example.com"\ngithub_repo = "owner/name"\n',
    )
    monkeypatch.setattr(issue, "_repository_root", lambda: tmp_path)
    assert issue.feedback_address() == "someone@example.com"


def test_feedback_address_is_absent_when_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, '[feedback]\ngithub_repo = "owner/name"\n')
    monkeypatch.setattr(issue, "_repository_root", lambda: tmp_path)
    assert issue.feedback_address() is None


def test_feedback_repository_reads_the_owning_repository_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, '[feedback]\ngithub_repo = "owner/name"\n')
    monkeypatch.setattr(issue, "_repository_root", lambda: tmp_path)
    assert issue.feedback_repository() == "owner/name"


def test_feedback_repository_rejects_a_missing_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, '[feedback]\nemail = "someone@example.com"\n')
    monkeypatch.setattr(issue, "_repository_root", lambda: tmp_path)
    with pytest.raises(SystemExit):
        issue.feedback_repository()


def test_feedback_repository_rejects_a_malformed_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, '[feedback]\ngithub_repo = "not-a-repo"\n')
    monkeypatch.setattr(issue, "_repository_root", lambda: tmp_path)
    with pytest.raises(SystemExit):
        issue.feedback_repository()


def test_route_is_command_when_the_command_is_installed_and_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(issue, "_which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        issue,
        "_run",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="github.com\n  Logged in to github.com account octocat (keyring)\n",
            stderr="",
        ),
    )
    route = issue.delivery_route()
    assert route["route"] == "command"
    assert route["command_installed"] is True
    assert route["command_authenticated"] is True
    assert route["account"] == "octocat"
    assert route["remediation"] is None


def test_route_falls_back_to_url_when_the_command_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(issue, "_which", lambda name: None)
    route = issue.delivery_route()
    assert route["route"] == "url"
    assert route["command_installed"] is False
    assert route["command_authenticated"] is False
    assert route["account"] is None
    assert route["remediation"] == issue.NOT_INSTALLED_REMEDIATION
    assert "https://cli.github.com/" in route["remediation"]


def test_route_falls_back_to_url_when_the_command_is_not_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(issue, "_which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        issue,
        "_run",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="not logged in\n"
        ),
    )
    route = issue.delivery_route()
    assert route["route"] == "url"
    assert route["command_authenticated"] is False
    assert route["remediation"] == issue.NOT_AUTHENTICATED_REMEDIATION
    assert route["remediation"] != issue.NOT_INSTALLED_REMEDIATION


def test_prefilled_url_encodes_the_title_and_body() -> None:
    result = issue.prefilled_url("owner/name", "a title", "a body & more")
    assert result["url"].startswith("https://github.com/owner/name/issues/new?")
    assert "title=a+title" in result["url"]
    assert "body=a+body+%26+more" in result["url"]
    assert result["body_included"] is True


def test_prefilled_url_drops_an_oversized_body() -> None:
    result = issue.prefilled_url("owner/name", "a title", "x" * (issue.MAX_URL_LENGTH + 1))
    assert result["body_included"] is False
    assert "body=" not in result["url"]
    assert len(result["url"]) <= issue.MAX_URL_LENGTH


def test_file_issue_invokes_the_command_with_the_report_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.md"
    report.write_text("body text", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=0, stdout="https://github.com/owner/name/issues/7\n", stderr=""
        )

    monkeypatch.setattr(issue, "_which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(issue, "_run", fake_run)
    result = issue.file_issue("owner/name", "a title", report, authenticated=True)

    assert result == {
        "route": "command",
        "url": "https://github.com/owner/name/issues/7",
        "repository": "owner/name",
        "body_included": True,
        "remediation": None,
    }
    assert calls[0][:2] == ["gh", "issue"]
    assert "--repo" in calls[0] and "owner/name" in calls[0]
    assert "--body-file" in calls[0] and str(report) in calls[0]


def test_file_issue_does_not_retry_a_failed_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.md"
    report.write_text("body text", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="rate limited\n")

    monkeypatch.setattr(issue, "_which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(issue, "_run", fake_run)
    with pytest.raises(SystemExit):
        issue.file_issue("owner/name", "a title", report, authenticated=True)
    assert len(calls) == 1


def test_file_issue_returns_a_url_when_the_command_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.md"
    report.write_text("body text", encoding="utf-8")
    monkeypatch.setattr(issue, "_which", lambda name: None)
    monkeypatch.setattr(
        issue,
        "_run",
        lambda argv, **kwargs: pytest.fail("the command must not run when unavailable"),
    )
    result = issue.file_issue("owner/name", "a title", report, authenticated=False)
    assert result["route"] == "url"
    assert result["url"].startswith("https://github.com/owner/name/issues/new?")
    assert result["remediation"] == issue.NOT_INSTALLED_REMEDIATION


def test_check_route_interface_writes_one_json_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path, '[feedback]\ngithub_repo = "owner/name"\n')
    monkeypatch.setattr(issue, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(issue, "_which", lambda name: None)
    interface = issue.CheckRoute()
    exit_code = interface.run(interface.build_parser().parse_args([]))
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["repository"] == "owner/name"
    assert payload["route"] == "url"
    assert payload["email"] is None


def test_file_issue_interface_writes_one_json_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path, '[feedback]\ngithub_repo = "owner/name"\n')
    report = tmp_path / "report.md"
    report.write_text("body text", encoding="utf-8")
    monkeypatch.setattr(issue, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(issue, "_which", lambda name: None)
    interface = issue.FileIssue()
    args = interface.build_parser().parse_args(
        ["--title", "a title", "--body-file", str(report)]
    )
    exit_code = interface.run(args)
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["route"] == "url"
    assert payload["repository"] == "owner/name"
    assert payload["remediation"] == issue.NOT_INSTALLED_REMEDIATION


def test_file_issue_interface_rejects_a_missing_report_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, '[feedback]\ngithub_repo = "owner/name"\n')
    monkeypatch.setattr(issue, "_repository_root", lambda: tmp_path)
    interface = issue.FileIssue()
    args = interface.build_parser().parse_args(
        ["--title", "a title", "--body-file", str(tmp_path / "absent.md")]
    )
    with pytest.raises(SystemExit):
        interface.run(args)
