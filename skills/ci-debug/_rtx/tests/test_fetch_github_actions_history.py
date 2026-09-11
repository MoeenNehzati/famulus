"""Bounded offline tests for the GitHub Actions history collector."""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


RTX = Path(__file__).resolve().parents[1]


def load():
    if str(RTX) not in sys.path:
        sys.path.insert(0, str(RTX))
    spec = importlib.util.spec_from_file_location("history_fetch_under_test", RTX / "_fetch_github_actions_history.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        for name, value in entries.items():
            output.writestr(name, value)
    return stream.getvalue()


def test_archive_rejects_traversal_and_malformed_utf8() -> None:
    module = load()
    members, gaps = module._safe_archive_members(archive({
        "../escape.txt": b"no",
        "bad.txt": b"\xff",
        "good.txt": b"tests/test_a.py::test_a\n",
    }))
    assert [name for name, _ in members] == ["00002-good.txt"]
    assert {item["code"] for item in gaps} == {"unsafe_entry", "malformed_utf8"}
    with pytest.raises(module.HistoryError) as raised:
        module._safe_archive_members(b"not a zip")
    assert raised.value.code == "malformed_archive"


def test_retry_is_bounded_and_preserves_fixed_shell_free_argv(monkeypatch, tmp_path: Path) -> None:
    module = load()
    calls = []
    results = [
        subprocess.CompletedProcess([], 1, b"", b"HTTP 503"),
        subprocess.CompletedProcess([], 1, b"", b"HTTP 503"),
        subprocess.CompletedProcess([], 0, b"{}", b""),
    ]

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return results.pop(0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda _delay: None)
    budget = module.Budget(30)
    assert module.GhClient(tmp_path, budget).json("repos/o/r") == {}
    assert len(calls) == 3
    assert budget.retries == 2
    assert all(call[1]["shell"] is False for call in calls)
    assert all(call[0][:4] == ["gh", "api", "--method", "GET"] for call in calls)
    assert "--field" not in calls[0][0]


def test_retry_after_header_is_honored_without_entering_payload(monkeypatch, tmp_path: Path) -> None:
    module = load()
    delays = []
    results = [
        subprocess.CompletedProcess([], 1, b"HTTP/2 429\r\nretry-after: 0\r\n\r\n", b""),
        subprocess.CompletedProcess([], 0, b"HTTP/2 200\r\ncontent-type: application/json\r\n\r\n{}", b""),
    ]
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: results.pop(0))
    monkeypatch.setattr(module.time, "sleep", delays.append)
    assert module.GhClient(tmp_path, module.Budget(30)).json("repos/o/r") == {}
    assert delays == [0.0]


def test_retry_after_over_backoff_cap_stops_without_sleep(monkeypatch, tmp_path: Path) -> None:
    module = load()
    delays = []
    result = subprocess.CompletedProcess(
        [], 1, b"HTTP/2 429\r\nretry-after: 31\r\n\r\n", b""
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(module.time, "sleep", delays.append)
    with pytest.raises(module.HistoryError) as raised:
        module.GhClient(tmp_path, module.Budget(60)).json("repos/o/r")
    assert raised.value.code == "rate_limit"
    assert delays == []


def test_timeout_and_transport_failures_retry_at_most_twice(monkeypatch, tmp_path: Path) -> None:
    module = load()
    calls = []
    results = [
        subprocess.TimeoutExpired(["gh", "api"], 1),
        subprocess.CompletedProcess([], 1, b"", b"network unavailable"),
        subprocess.CompletedProcess([], 0, b"{}", b""),
    ]

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        result = results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda _delay: None)
    budget = module.Budget(30)
    assert module.GhClient(tmp_path, budget).json("repos/o/r") == {}
    assert len(calls) == 3
    assert budget.retries == 2


@pytest.mark.parametrize("stderr", [
    b"error connecting to api.github.com: dial tcp: socket: operation not permitted",
    b"unknown option: --bad-configuration",
])
def test_statusless_policy_and_configuration_failures_do_not_retry(monkeypatch, tmp_path: Path, stderr: bytes) -> None:
    module = load()
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        return subprocess.CompletedProcess([], 1, b"", stderr)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(module.HistoryError) as raised:
        module.GhClient(tmp_path, module.Budget(30)).json("repos/o/r")
    assert raised.value.code == "transport_failure"
    assert len(calls) == 1


def test_stderr_rate_limit_words_do_not_prove_rate_limit(monkeypatch, tmp_path: Path) -> None:
    module = load()
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        return subprocess.CompletedProcess([], 1, b"", b"HTTP 403: rate limit exceeded")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(module.HistoryError) as raised:
        module.GhClient(tmp_path, module.Budget(30)).json("repos/o/r")
    assert raised.value.code == "forbidden"
    assert len(calls) == 1


def test_ordinary_403_is_not_retried(monkeypatch, tmp_path: Path) -> None:
    module = load()
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        return subprocess.CompletedProcess([], 1, b"HTTP/2 403\r\nx-ratelimit-remaining: 10\r\n\r\n", b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(module.HistoryError) as raised:
        module.GhClient(tmp_path, module.Budget(30)).json("repos/o/r")
    assert raised.value.code == "forbidden"
    assert len(calls) == 1


def test_attempt_gap_makes_failure_evidence_incomplete() -> None:
    module = load()

    class Client:
        def json(self, endpoint, fields=()):
            if endpoint.endswith("/attempts/1"):
                return {"run_attempt": 1, "status": "completed", "conclusion": "failure", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:01Z"}
            return {"jobs": []}

        def binary(self, _endpoint):
            return archive({"bad.txt": b"\xff"})

    run = {"id": 8, "run_number": 9, "run_attempt": 1, "created_at": "2026-01-01T00:00:00Z", "head_sha": "abc"}
    normalized, _members, gaps = module._collect_run(Client(), "o/r", run)
    assert gaps
    assert normalized["failure_evidence_complete"] is False


def test_collection_preserves_github_step_ordinals() -> None:
    module = load()

    class Client:
        def json(self, endpoint, fields=()):
            if endpoint.endswith("/attempts/1"):
                return {"run_attempt": 1, "status": "completed", "conclusion": "success"}
            return {"jobs": [{
                "id": 12,
                "name": "tests",
                "labels": ["ubuntu-latest"],
                "steps": [{"number": 7, "name": "pytest", "status": "completed", "conclusion": "success"}],
            }]}

    run = {"id": 8, "run_number": 9, "run_attempt": 1, "head_sha": "abc"}
    normalized, _members, gaps = module._collect_run(Client(), "o/r", run)
    assert gaps == []
    assert normalized["attempts"][0]["jobs"][0]["steps"][0]["step_ordinal"] == 7


@pytest.mark.parametrize("run_attempt", [None, "1", 0])
def test_missing_or_invalid_run_attempt_is_explicitly_censored(run_attempt) -> None:
    module = load()

    class Client:
        def json(self, *_args, **_kwargs):
            raise AssertionError("attempt endpoint must not be guessed")

    run = {"id": 8, "run_number": 9, "run_attempt": run_attempt, "created_at": "2026-01-01T00:00:00Z", "head_sha": "abc"}
    normalized, members, gaps = module._collect_run(Client(), "o/r", run)
    assert members == {}
    assert normalized["attempts"] == []
    assert normalized["attempt_listing_complete"] is False
    assert [item["code"] for item in gaps] == ["run_attempt_unavailable"]


def test_mismatched_attempt_detail_is_not_accepted() -> None:
    module = load()

    class Client:
        def json(self, endpoint, fields=()):
            if endpoint.endswith("/attempts/1"):
                return {"run_attempt": 2, "status": "completed", "conclusion": "success"}
            raise AssertionError(endpoint)

    normalized, _members, gaps = module._collect_run(Client(), "o/r", {"id": 8, "run_number": 9, "run_attempt": 1})
    assert normalized["attempts"] == []
    assert normalized["attempt_listing_complete"] is False
    assert [item["code"] for item in gaps] == ["run_attempt_mismatch"]


def test_repository_identity_accepts_https_and_ssh(monkeypatch, tmp_path: Path) -> None:
    module = load()
    monkeypatch.setattr(module, "_git", lambda *_args, **_kwargs: "https://github.com/acme/repo.git\n")
    assert module._repository_identity(tmp_path) == ("github.com", "acme/repo")
    monkeypatch.setattr(module, "_git", lambda *_args, **_kwargs: "git@github.com:acme/repo.git\n")
    assert module._repository_identity(tmp_path) == ("github.com", "acme/repo")
    monkeypatch.setattr(module, "_git", lambda *_args, **_kwargs: "https://github.example/acme/repo.git\n")
    with pytest.raises(module.HistoryError) as raised:
        module._repository_identity(tmp_path)
    assert raised.value.code == "repository_mismatch"


def test_raw_fields_keep_at_prefixed_filter_literal(monkeypatch, tmp_path: Path) -> None:
    module = load()
    captured = {}

    def fake_run(argv, **_kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, b"{}", b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.GhClient(tmp_path, module.Budget(10)).json("endpoint", (("branch", "@secret"),))
    assert captured["argv"][-2:] == ["--raw-field", "branch=@secret"]


def test_later_page_failure_preserves_prior_page(monkeypatch) -> None:
    module = load()

    class Client:
        def json(self, _endpoint, fields=()):
            page = dict(fields)["page"]
            if page == "1":
                return {"items": [{}] * 100}
            raise module.HistoryError("transport_failure", "stopped")

    items, complete, stop = module._pages(Client(), "endpoint", "items", (), allow_partial=True)
    assert len(items) == 100
    assert complete is False
    assert stop == "transport_failure"


def test_malformed_page_items_are_retained_as_incomplete_coverage() -> None:
    module = load()

    class Client:
        def json(self, _endpoint, fields=()):
            return {"items": [{"id": 1}, "not-an-object"]}

    items, complete, stop = module._pages(Client(), "endpoint", "items", (), allow_partial=True)
    assert items == [{"id": 1}]
    assert complete is False
    assert stop == "malformed_item"


def test_malformed_run_index_item_retains_its_position() -> None:
    module = load()

    class Client:
        def json(self, _endpoint, fields=()):
            return {"items": [{"id": 3}, "not-an-object", {"id": 1}]}

    items, complete, stop = module._pages(Client(), "endpoint", "items", (), allow_partial=True, retain_positions=True)
    assert items == [
        {"id": 3, "_run_index_position": 0},
        {"_run_index_gap": True, "_run_index_position": 1},
        {"id": 1, "_run_index_position": 2},
    ]
    assert complete is False
    assert stop == "malformed_item"


def test_git_evidence_records_non_ancestry_and_all_merge_parents(monkeypatch, tmp_path: Path) -> None:
    module = load()
    observed = {"head_sha": "failing"}
    episode = {
        "episode_id": "1..3",
        "preceding_green_sha": "green",
        "incidence": [{"canonical_test_id": "tests/test_a.py::test_a", "first_observation": observed}],
    }
    monkeypatch.setattr(module, "build_episodes", lambda *_args: {"episodes": [episode]})
    monkeypatch.setattr(module, "_git", lambda *_args, **_kwargs: "failing parent-one parent-two\n")
    monkeypatch.setattr(module, "_is_ancestor", lambda *_args: False)
    monkeypatch.setattr(module, "_comparison", lambda _repo, baseline, _failing, _canonical, _budget=None: {"state": baseline})
    evidence, gaps = module._git_evidence(tmp_path, [])
    item = evidence["comparisons"][0]
    assert gaps == []
    assert item["all_parent_shas"] == ["parent-one", "parent-two"]
    assert item["preceding_green"] == {"state": "non_ancestry"}
    assert item["first_parent"] == {"state": "parent-one"}


def test_git_evidence_publishes_explicit_partial_rows_after_deadline(monkeypatch, tmp_path: Path) -> None:
    module = load()
    episodes = [{
        "episode_id": "1..3",
        "preceding_green_sha": "green",
        "incidence": [
            {"canonical_test_id": "tests/test_a.py::test_a", "first_observation": {"head_sha": "failing"}},
            {"canonical_test_id": "tests/test_b.py::test_b", "first_observation": {"head_sha": "later"}},
        ],
    }]
    monkeypatch.setattr(module, "build_episodes", lambda *_args: {"episodes": episodes})
    monkeypatch.setattr(
        module,
        "_git",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.HistoryError("total_deadline", "collection deadline reached")
        ),
    )
    evidence, gaps = module._git_evidence(tmp_path, [], module.Budget(30))
    assert len(gaps) == 1
    assert gaps[0]["code"] == "total_deadline"
    assert len(evidence["comparisons"]) == 2
    assert all(
        item["preceding_green"] == {"state": "evidence_incomplete", "reason": "total_deadline"}
        and item["first_parent"] == {"state": "evidence_incomplete", "reason": "total_deadline"}
        for item in evidence["comparisons"]
    )


def test_worker_cap_fails_before_collection(tmp_path: Path) -> None:
    module = load()
    with pytest.raises(SystemExit):
        module.main([
            "--repo-root", str(tmp_path),
            "--workflow", "ci.yml",
            "--branch", "main",
            "--snapshot-dir", str(tmp_path / "snapshot"),
            "--workers", "33",
        ])


def test_provenance_never_calls_unavailable_baseline_introduced(monkeypatch, tmp_path: Path) -> None:
    module = load()
    canonical = "tests/test_a.py::test_a"
    source = "def test_a():\n    assert True\n"
    monkeypatch.setattr(module, "_commit_available", lambda *_args: True)
    monkeypatch.setattr(
        module,
        "_git_show",
        lambda _repo, sha, _path, _budget=None: ("available", source)
        if sha == "failing"
        else ("decode_failure", None),
    )
    assert module._comparison(tmp_path, "baseline", "failing", canonical)["state"] == "decode_failure"


def test_provenance_requires_complete_diff_before_introduced(monkeypatch, tmp_path: Path) -> None:
    module = load()
    canonical = "tests/test_a.py::test_a"
    source = "def test_a():\n    assert True\n"
    monkeypatch.setattr(module, "_commit_available", lambda *_args: True)
    monkeypatch.setattr(
        module,
        "_git_show",
        lambda _repo, sha, _path, _budget=None: ("available", source)
        if sha == "failing"
        else ("absent_blob", None),
    )
    monkeypatch.setattr(module, "_git", lambda *_args, **_kwargs: (_ for _ in ()).throw(module.HistoryError("git_unavailable", "no diff")))
    assert module._comparison(tmp_path, "baseline", "failing", canonical)["state"] == "diff_failure"


def test_provenance_introduced_requires_proven_absence(monkeypatch, tmp_path: Path) -> None:
    module = load()
    canonical = "tests/test_a.py::test_a"
    source = "def test_a():\n    assert True\n"
    monkeypatch.setattr(module, "_commit_available", lambda *_args: True)
    monkeypatch.setattr(
        module,
        "_git_show",
        lambda _repo, sha, _path, _budget=None: ("available", source)
        if sha == "failing"
        else ("absent_blob", None),
    )
    monkeypatch.setattr(module, "_git", lambda *_args, **_kwargs: "tests/test_a.py\n")
    assert module._comparison(tmp_path, "baseline", "failing", canonical)["state"] == "introduced"
