"""A run journal that outlives the assistant session that started it.

An overnight job is logged by a succession of assistant sessions: the first one
ends, a later one resumes the work, and each writes under its own session and
thread id. These tests pin the durable half of milestone logging -- one journal
per run, addressed by a caller-supplied run id -- and the guarantee that adding
it changed nothing for callers that do not pass one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
MILESTONE = SCRIPTS / "milestone.py"
TIMELINE = SCRIPTS / "agent-timeline.py"


def call(script: Path, *args: str, logs: Path, session: str = "sess-a", thread: str | None = None,
         cwd: Path | None = None, env_overrides: dict[str, str] | None = None,
         output_encoding: str | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke a helper the way a shell on PATH would, with a private log root."""
    env = dict(os.environ)
    env["ASSISTANT_LOGS"] = str(logs)
    env["CLAUDE_CODE_SESSION_ID"] = session
    env.pop("CODEX_SESSION_ID", None)
    if thread is None:
        env.pop("CODEX_THREAD_ID", None)
    else:
        env["CODEX_THREAD_ID"] = thread
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(script), *args],
        env=env, capture_output=True, text=True, encoding=output_encoding,
        cwd=str(cwd or SCRIPTS.parent),
    )


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def session_files(logs: Path) -> list[Path]:
    """Day-partitioned session logs, excluding the run journals beside them."""
    return sorted(p for p in logs.glob("*/*.jsonl") if p.parent.name != "runs")


# ── backward compatibility ───────────────────────────────────────────────────

def test_plain_call_still_writes_only_the_original_fields(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    done = call(MILESTONE, "--role", "auditor", "read the loader", "found the entry point", logs=logs)

    assert done.returncode == 0, done.stderr
    written = session_files(logs)
    assert len(written) == 1
    rec = records(written[0])[0]
    assert set(rec) == {"ts", "role", "cwd", "doing", "prev"}
    assert (rec["role"], rec["doing"], rec["prev"]) == ("auditor", "read the loader", "found the entry point")


def test_plain_call_writes_no_run_journal(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    call(MILESTONE, "no run here", logs=logs)

    assert not (logs / "runs").exists()


def test_records_written_before_run_journals_existed_stay_readable(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    old = logs / "2026-01-02"
    old.mkdir(parents=True)
    (old / "sess-old.session.jsonl").write_text(
        json.dumps({"ts": "2026-01-02T09:00:00+00:00", "role": "r", "cwd": "/w",
                    "doing": "old shape", "prev": ""}) + "\n",
        encoding="utf-8",
    )

    shown = call(TIMELINE, "sess-old", logs=logs)

    assert shown.returncode == 0, shown.stderr
    assert "old shape" in shown.stdout


def test_timeline_escapes_non_cp1252_plain_text_on_a_legacy_console(
    tmp_path: Path,
) -> None:
    """Plain output stays readable when dynamic text exceeds the encoding."""
    logs = tmp_path / "logs"
    call(MILESTONE, "portable output 🙂", logs=logs)

    shown = call(
        TIMELINE,
        "sess-a",
        logs=logs,
        env_overrides={"PYTHONIOENCODING": "cp1252"},
    )

    assert shown.returncode == 0, shown.stderr
    assert "portable output" in shown.stdout
    assert r"\U0001f642" in shown.stdout


def test_timeline_json_preserves_unicode_with_utf8_and_legacy_output(
    tmp_path: Path,
) -> None:
    """JSON escapes only when required and remains semantically lossless."""
    logs = tmp_path / "logs"
    call(MILESTONE, "--run", "unicode-run", "json payload 🙂", logs=logs)

    utf8 = call(
        TIMELINE,
        "--run",
        "unicode-run",
        "--json",
        logs=logs,
        env_overrides={"PYTHONIOENCODING": "utf-8"},
        output_encoding="utf-8",
    )
    legacy = call(
        TIMELINE,
        "--run",
        "unicode-run",
        "--json",
        logs=logs,
        env_overrides={"PYTHONIOENCODING": "cp1252"},
    )

    assert utf8.returncode == 0, utf8.stderr
    assert "🙂" in utf8.stdout
    assert legacy.returncode == 0, legacy.stderr
    assert json.loads(legacy.stdout)["events"][0]["doing"] == "json payload 🙂"


def test_listing_sessions_ignores_run_journals(tmp_path: Path) -> None:
    """A run journal is not a session; `--list` must not offer it as one."""
    logs = tmp_path / "logs"
    call(MILESTONE, "plain", logs=logs, session="sess-a")
    call(MILESTONE, "--run", "nightly-01", "with a run", logs=logs, session="sess-b")

    listed = call(TIMELINE, "--list", logs=logs)

    assert listed.returncode == 0, listed.stderr
    assert "sess-a" in listed.stdout
    assert "sess-b" in listed.stdout
    assert "nightly-01" not in listed.stdout


# ── the durable journal ──────────────────────────────────────────────────────

def test_run_events_also_land_in_the_session_log(tmp_path: Path) -> None:
    """The run journal is added alongside the session log, not carved out of it."""
    logs = tmp_path / "logs"
    call(MILESTONE, "--run", "nightly-01", "start the sweep", logs=logs)

    rec = records(session_files(logs)[0])[0]
    assert rec["run"] == "nightly-01"
    assert rec["doing"] == "start the sweep"


def test_one_run_is_reconstructed_across_two_assistant_sessions(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    call(MILESTONE, "--run", "nightly-01", "--event", "run-start", "begin", logs=logs, session="sess-a")
    call(MILESTONE, "--run", "nightly-01", "--step", "3", "third step", logs=logs,
         session="sess-b", thread="thread-9")

    dumped = call(TIMELINE, "--run", "nightly-01", "--json", logs=logs)

    assert dumped.returncode == 0, dumped.stderr
    run = json.loads(dumped.stdout)
    assert [e["doing"] for e in run["events"]] == ["begin", "third step"]
    assert [e["session"] for e in run["events"]] == ["sess-a", "sess-b"]
    assert [e["agent"] for e in run["events"]] == ["session", "thread-9"]
    assert run["sessions"] == ["sess-a", "sess-b"]


def test_task_lifecycle_reconstructs_from_typed_fields_only(tmp_path: Path) -> None:
    """Start, failure, retry, skip and completion are read off typed keys."""
    logs = tmp_path / "logs"
    steps = [
        ("--event", "run-start", "--step", "1"),
        ("--event", "task", "--task", "extract", "--state", "started", "--attempt", "1"),
        ("--event", "task", "--task", "extract", "--state", "failed", "--attempt", "1"),
        ("--event", "task", "--task", "extract", "--state", "started", "--attempt", "2"),
        ("--event", "task", "--task", "extract", "--state", "succeeded", "--attempt", "2",
         "--evidence", "out/extract.json"),
        ("--event", "task", "--task", "render", "--state", "skipped"),
        ("--event", "run-end", "--step", "9"),
    ]
    for index, flags in enumerate(steps):
        call(MILESTONE, "--run", "nightly-01", *flags, f"piece {index}", logs=logs)

    run = json.loads(call(TIMELINE, "--run", "nightly-01", "--json", logs=logs).stdout)

    typed = [(e.get("event"), e.get("task"), e.get("state"), e.get("attempt")) for e in run["events"]]
    assert typed == [
        ("run-start", None, None, None),
        ("task", "extract", "started", 1),
        ("task", "extract", "failed", 1),
        ("task", "extract", "started", 2),
        ("task", "extract", "succeeded", 2),
        ("task", "render", "skipped", None),
        ("run-end", None, None, None),
    ]
    assert run["events"][0]["step"] == 1
    assert run["events"][4]["evidence"] == ["out/extract.json"]


def test_absent_typed_fields_are_omitted_from_the_record(tmp_path: Path) -> None:
    """Nothing is invented: a field not passed leaves no key behind."""
    logs = tmp_path / "logs"
    call(MILESTONE, "--run", "nightly-01", "--event", "run-start", "begin", logs=logs)

    rec = records(logs / "runs" / "nightly-01.jsonl")[0]
    assert "task" not in rec and "state" not in rec and "attempt" not in rec and "evidence" not in rec


def test_run_journal_path_is_printable(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    shown = call(MILESTONE, "--run", "nightly-01", "--path", logs=logs)

    assert shown.returncode == 0, shown.stderr
    assert Path(shown.stdout.strip()) == logs / "runs" / "nightly-01.jsonl"


def test_typed_fields_without_a_run_are_refused(tmp_path: Path) -> None:
    """Structured data with no run id is unrecoverable; say so rather than drop it."""
    logs = tmp_path / "logs"
    refused = call(MILESTONE, "--event", "run-start", "begin", logs=logs)

    assert refused.returncode != 0
    assert "--run" in refused.stderr
    assert not session_files(logs)


@pytest.mark.parametrize(
    "unsafe",
    ["../escape", "runs/../../etc", "a/b", "", ".", "..", "-leading-dash", "x" * 65, "sp ace", "tab\tid"],
)
def test_unsafe_run_identifiers_are_rejected(tmp_path: Path, unsafe: str) -> None:
    logs = tmp_path / "logs"
    refused = call(MILESTONE, "--run", unsafe, "begin", logs=logs)

    assert refused.returncode != 0, f"accepted unsafe run id: {unsafe!r}"
    assert not list(logs.rglob("*.jsonl"))


@pytest.mark.parametrize("unsafe", ["../escape", "a/b", "x" * 65])
def test_the_reader_rejects_unsafe_run_identifiers_too(tmp_path: Path, unsafe: str) -> None:
    logs = tmp_path / "logs"
    (logs / "runs").mkdir(parents=True)
    refused = call(TIMELINE, "--run", unsafe, "--json", logs=logs)

    assert refused.returncode != 0, f"accepted unsafe run id: {unsafe!r}"


def test_missing_run_is_reported_rather_than_rendered_empty(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    (logs / "runs").mkdir(parents=True)
    missing = call(TIMELINE, "--run", "never-ran", "--json", logs=logs)

    assert missing.returncode != 0
    assert "never-ran" in missing.stderr


# ── malformed input ──────────────────────────────────────────────────────────

def test_malformed_journal_lines_are_reported_not_silently_dropped(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    call(MILESTONE, "--run", "nightly-01", "--event", "run-start", "begin", logs=logs)
    journal = logs / "runs" / "nightly-01.jsonl"
    with journal.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
        handle.write(json.dumps(["a list, not a record"]) + "\n")
    call(MILESTONE, "--run", "nightly-01", "--event", "run-end", "finish", logs=logs)

    run = json.loads(call(TIMELINE, "--run", "nightly-01", "--json", logs=logs).stdout)

    assert [e["doing"] for e in run["events"]] == ["begin", "finish"]
    assert [m["line"] for m in run["malformed"]] == [2, 3]


def test_malformed_lines_are_visible_when_the_run_is_rendered(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    call(MILESTONE, "--run", "nightly-01", "begin", logs=logs)
    with (logs / "runs" / "nightly-01.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")

    shown = call(TIMELINE, "--run", "nightly-01", logs=logs)

    assert shown.returncode == 0, shown.stderr
    assert "malformed" in shown.stdout.lower()
    assert "line 2" in shown.stdout


# ── concurrency ──────────────────────────────────────────────────────────────

def test_concurrent_writers_leave_the_journal_valid_jsonl(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    count = 24

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda index: call(MILESTONE, "--run", "nightly-01", "--task", f"t{index}",
                               f"piece {index}", logs=logs, session=f"sess-{index % 3}"),
            range(count),
        ))

    assert all(r.returncode == 0 for r in results), [r.stderr for r in results if r.returncode]
    written = records(logs / "runs" / "nightly-01.jsonl")
    assert len(written) == count
    assert {rec["task"] for rec in written} == {f"t{index}" for index in range(count)}


def test_a_record_stays_inside_the_single_write_budget(tmp_path: Path) -> None:
    """Interleave safety rests on one line, one write, well under 4KB.

    Every free-text field is individually capped, but a repeatable `--evidence`
    is not: twenty long paths push the record past the budget the whole
    concurrency guarantee is stated in terms of.
    """
    logs = tmp_path / "logs"
    evidence = []
    for _ in range(20):
        evidence += ["--evidence", "e" * 200]
    written = call(MILESTONE, "--run", "nightly-01", *evidence, "x" * 200, "y" * 200, logs=logs)

    assert written.returncode == 0, written.stderr
    line = (logs / "runs" / "nightly-01.jsonl").read_bytes()
    assert len(line) < 4096, f"record is {len(line)} bytes"


def test_dropping_evidence_to_fit_is_recorded_not_silent(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    evidence = []
    for index in range(20):
        evidence += ["--evidence", f"{index}-" + "e" * 198]
    call(MILESTONE, "--run", "nightly-01", *evidence, "x" * 200, "y" * 200, logs=logs)

    rec = records(logs / "runs" / "nightly-01.jsonl")[0]
    assert rec["evidence_dropped"] == 20 - len(rec["evidence"])
    assert rec["evidence"][0].startswith("0-")  # the earliest paths are the kept ones


def test_ordinary_evidence_is_kept_whole(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    call(MILESTONE, "--run", "nightly-01", "--evidence", "out/a.json", "--evidence", "out/b.json",
         "done", "", logs=logs)

    rec = records(logs / "runs" / "nightly-01.jsonl")[0]
    assert rec["evidence"] == ["out/a.json", "out/b.json"]
    assert "evidence_dropped" not in rec


def test_the_budget_holds_for_multibyte_text(tmp_path: Path) -> None:
    """The budget is counted in bytes, so a fully non-ASCII record still fits.

    Pins the choice of budget rather than a new behavior: every capped field
    filled with three-byte characters must still leave the record under 4KB.
    """
    logs = tmp_path / "logs"
    wide = "漢" * 200
    evidence = []
    for _ in range(20):
        evidence += ["--evidence", "証" * 200]
    written = call(
        MILESTONE, "--run", "r" * 64, "--role", wide, "--event", "事" * 60,
        "--task", "務" * 100, "--state", "態" * 40, "--step", "999", "--attempt", "999",
        *evidence, wide, wide, logs=logs, session="s" * 64,
    )

    assert written.returncode == 0, written.stderr
    line = (logs / "runs" / ("r" * 64 + ".jsonl")).read_bytes()
    assert len(line) < 4096, f"record is {len(line)} bytes"
