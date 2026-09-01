"""A run journal that outlives the assistant session that started it.

An overnight job is logged by a succession of assistant sessions: the first one
ends, a later one resumes the work, and each writes under its own session and
thread id. These tests pin the durable half of milestone logging -- one journal
per run, addressed by a caller-supplied run id -- and the guarantee that adding
it changed nothing for callers that do not pass one.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import time

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[4]
RUNTIME = Path(__file__).resolve().parents[1]
MILESTONE = RUNTIME / "_milestone_writer.py"
TIMELINE = RUNTIME / "_agent_timeline.py"
BLUEPRINTS = RUNTIME / "blueprints"


def call(
    script: Path,
    *args: str,
    logs: Path,
    session: str = "sess-a",
    thread: str | None = None,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    output_encoding: str | None = None,
) -> subprocess.CompletedProcess[str]:
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
        env=env,
        capture_output=True,
        text=True,
        encoding=output_encoding,
        cwd=str(cwd or ROOT),
    )


def dispatch(
    interface: str,
    *args: str,
    logs: Path,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke one public runtime interface against this exact worktree."""
    env = dict(os.environ)
    env.update(
        ASSISTANT_LOGS=str(logs),
        CLAUDE_CODE_SESSION_ID="dispatch-test",
        PYTHONPATH=str(ROOT / "src"),
    )
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "officina.dispatcher.cli",
            "--repository-config",
            str(ROOT / "officina.toml"),
            "--caller-skill",
            "milestone-logging",
            interface,
            *args,
        ],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def session_files(logs: Path) -> list[Path]:
    """Day-partitioned session logs, excluding the run journals beside them."""
    return sorted(p for p in logs.glob("*/*.jsonl") if p.parent.name != "runs")


def _direct_io(blueprint: str, interface: str) -> dict:
    document = yaml.safe_load((BLUEPRINTS / blueprint).read_text(encoding="utf-8"))
    return document["interfaces"][interface]["contract"]["direct_io"]


def test_writer_contract_declares_the_selected_log_root() -> None:
    direct_io = _direct_io(
        "rtx-milestone-writer.yaml",
        "milestone-logging._rtx.source.rtx-milestone-writer.interface.record",
    )

    assert direct_io["writes"][0]["path"] == "<selected-milestone-log-root>/**"
    assert direct_io["writes"][0]["medium"] == "local-filesystem"
    assert "ASSISTANT_LOGS" in direct_io["writes"][0]["reason"]
    assert "plugin MCP" in direct_io["writes"][0]["reason"]
    assert "logging-path" in direct_io["writes"][0]["reason"]
    assert "$HOME/.assistant-logs" in direct_io["writes"][0]["reason"]


def test_timeline_contract_declares_every_transcript_root() -> None:
    direct_io = _direct_io(
        "rtx-agent-timeline.yaml",
        "milestone-logging._rtx.source.rtx-agent-timeline.interface.timeline",
    )

    reads = {entry["id"]: entry for entry in direct_io["reads"]}
    assert reads["read-1"]["path"] == "<selected-milestone-log-root>/**"
    assert reads["read-1"]["medium"] == "local-filesystem"
    assert "ASSISTANT_LOGS" in reads["read-1"]["reason"]
    assert "plugin MCP" in reads["read-1"]["reason"]
    assert "logging-path" in reads["read-1"]["reason"]
    assert "$HOME/.assistant-logs" in reads["read-1"]["reason"]
    assert reads["read-2"]["path"] == "$HOME/.claude/projects/**"
    assert reads["read-3"]["path"] == "<selected-codex-home>/sessions/**"
    assert reads["read-3"]["medium"] == "local-filesystem"
    assert "CODEX_HOME" in reads["read-3"]["reason"]
    assert "$HOME/.codex" in reads["read-3"]["reason"]


def test_record_interface_resolves_the_selected_log_root(tmp_path: Path) -> None:
    logs = tmp_path / "logs"

    shown = dispatch(
        "milestone-logging._rtx.interface.record",
        "--path",
        logs=logs,
    )

    assert shown.returncode == 0, shown.stderr
    assert Path(shown.stdout.strip()).is_relative_to(logs)


def test_record_interface_accepts_role_and_positional_messages(tmp_path: Path) -> None:
    logs = tmp_path / "logs"

    recorded = dispatch(
        "milestone-logging._rtx.interface.record",
        "--role",
        "reviewer",
        "start",
        "previous",
        logs=logs,
    )

    assert recorded.returncode == 0, recorded.stderr
    rec = records(session_files(logs)[0])[0]
    assert (rec["role"], rec["doing"], rec["prev"]) == (
        "reviewer",
        "start",
        "previous",
    )


def test_record_dispatcher_accepts_empty_role_and_signed_step(tmp_path: Path) -> None:
    logs = tmp_path / "logs"

    dispatched = dispatch(
        "milestone-logging._rtx.interface.record",
        "--run",
        "nightly-01",
        "--role",
        "",
        "--step",
        "+1",
        "start",
        logs=logs,
    )

    assert dispatched.returncode == 0, dispatched.stderr
    rec = records(logs / "runs" / "nightly-01.jsonl")[0]
    assert (rec["role"], rec["step"]) == ("", 1)


def test_timeline_interface_lists_a_session(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    day = logs / "2026-08-22"
    day.mkdir(parents=True)
    (day / "visible.session.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-08-22T09:00:00+00:00",
                "role": "reviewer",
                "cwd": "/workspace",
                "doing": "start",
                "prev": "previous",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runs = logs / "runs"
    runs.mkdir()
    (runs / "nightly-01.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-08-22T09:01:00+00:00",
                "role": "reviewer",
                "cwd": "/workspace",
                "doing": "run event",
                "prev": "",
                "run": "nightly-01",
                "session": "hidden-session",
                "agent": "session",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    shown = dispatch(
        "milestone-logging._rtx.interface.timeline",
        "--list",
        logs=logs,
    )

    assert shown.returncode == 0, shown.stderr
    assert "visible" in shown.stdout
    assert "nightly-01" not in shown.stdout
    assert "hidden-session" not in shown.stdout


def test_timeline_interface_accepts_session_and_slow_value(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    day = logs / "2026-08-22"
    day.mkdir(parents=True)
    (day / "visible.session.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-08-22T09:00:00+00:00",
                "role": "reviewer",
                "cwd": "/workspace",
                "doing": "old shape",
                "prev": "previous",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    shown = dispatch(
        "milestone-logging._rtx.interface.timeline",
        "visible",
        "--slow",
        "2",
        logs=logs,
    )

    assert shown.returncode == 0, shown.stderr
    assert "session visible" in shown.stdout
    assert "old shape" in shown.stdout


def test_runtime_interfaces_render_help(tmp_path: Path) -> None:
    logs = tmp_path / "logs"

    record_help = dispatch(
        "milestone-logging._rtx.interface.record",
        "--help",
        logs=logs,
    )
    timeline_help = dispatch(
        "milestone-logging._rtx.interface.timeline",
        "--help",
        logs=logs,
    )
    assert record_help.returncode == 0, record_help.stderr
    assert "--role ROLE" in record_help.stdout
    assert timeline_help.returncode == 0, timeline_help.stderr
    assert "--slow SLOW" in timeline_help.stdout


# ── backward compatibility ───────────────────────────────────────────────────


def test_plain_call_still_writes_only_the_original_fields(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    done = call(
        MILESTONE,
        "--role",
        "auditor",
        "read the loader",
        "found the entry point",
        logs=logs,
    )

    assert done.returncode == 0, done.stderr
    written = session_files(logs)
    assert len(written) == 1
    rec = records(written[0])[0]
    assert set(rec) == {"ts", "role", "cwd", "doing", "prev"}
    assert (rec["role"], rec["doing"], rec["prev"]) == (
        "auditor",
        "read the loader",
        "found the entry point",
    )
    assert not (logs / "runs").exists()


def test_timeline_json_preserves_unicode_with_utf8_and_legacy_output(
    tmp_path: Path,
) -> None:
    """Plain and JSON output remain readable and lossless on legacy consoles."""
    logs = tmp_path / "logs"
    call(MILESTONE, "--run", "unicode-run", "json payload 🙂", logs=logs)

    plain_legacy = call(
        TIMELINE,
        "sess-a",
        logs=logs,
        env_overrides={"PYTHONIOENCODING": "cp1252"},
    )
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

    assert plain_legacy.returncode == 0, plain_legacy.stderr
    assert "json payload" in plain_legacy.stdout
    assert r"\U0001f642" in plain_legacy.stdout
    assert utf8.returncode == 0, utf8.stderr
    assert "🙂" in utf8.stdout
    assert legacy.returncode == 0, legacy.stderr
    assert json.loads(legacy.stdout)["events"][0]["doing"] == "json payload 🙂"


# ── the durable journal ──────────────────────────────────────────────────────


def test_run_recovery_preserves_lifecycle_damage_and_session_mirrors(
    tmp_path: Path,
) -> None:
    """Two real writes frame schema-faithful history and damaged lines."""
    logs = tmp_path / "logs"
    started = call(
        MILESTONE,
        "--run",
        "nightly-01",
        "--event",
        "run-start",
        "--step",
        "1",
        "--evidence",
        "out/a.json",
        "--evidence",
        "out/b.json",
        "begin",
        logs=logs,
        session="sess-a",
    )
    assert started.returncode == 0, started.stderr
    journal = logs / "runs" / "nightly-01.jsonl"
    base = {
        "ts": "2026-08-22T09:01:00+00:00",
        "role": "worker",
        "cwd": "/workspace",
        "prev": "",
        "run": "nightly-01",
        "session": "sess-a",
        "agent": "session",
        "event": "task",
        "task": "extract",
    }
    prepared = [
        {**base, "doing": "piece 1", "state": "started", "attempt": 1},
        {**base, "doing": "piece 2", "state": "failed", "attempt": 1},
        {**base, "doing": "piece 3", "state": "started", "attempt": 2},
        {
            **base,
            "doing": "piece 4",
            "state": "succeeded",
            "attempt": 2,
            "evidence": ["out/extract.json"],
        },
        {
            **base,
            "doing": "piece 5",
            "task": "render",
            "state": "skipped",
        },
    ]
    with journal.open("a", encoding="utf-8") as handle:
        for record in prepared:
            handle.write(json.dumps(record) + "\n")
        handle.write("{not json at all\n")
        handle.write(json.dumps(["a list, not a record"]) + "\n")
    finished = call(
        MILESTONE,
        "--run",
        "nightly-01",
        "--event",
        "run-end",
        "--step",
        "9",
        "--done",
        "finish",
        logs=logs,
        session="sess-b",
        thread="thread-9",
    )

    dumped = call(TIMELINE, "--run", "nightly-01", "--json", logs=logs)
    shown = call(TIMELINE, "--run", "nightly-01", logs=logs)

    assert finished.returncode == 0, finished.stderr
    assert dumped.returncode == 0, dumped.stderr
    run = json.loads(dumped.stdout)
    typed = [
        (e.get("event"), e.get("task"), e.get("state"), e.get("attempt"))
        for e in run["events"]
    ]
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
    assert run["events"][0]["evidence"] == ["out/a.json", "out/b.json"]
    assert "evidence_dropped" not in run["events"][0]
    assert run["events"][4]["evidence"] == ["out/extract.json"]
    first = run["events"][0]
    assert "task" not in first and "state" not in first and "attempt" not in first
    assert run["events"][-1]["doing"] == "(done)"
    assert run["events"][-1]["prev"] == "finish"
    assert run["sessions"] == ["sess-a", "sess-b"]
    assert run["agents"] == ["session", "thread-9"]
    expected_origins = [("sess-a", "session")] * 6 + [("sess-b", "thread-9")]
    assert [
        (event["session"], event["agent"]) for event in run["events"]
    ] == expected_origins
    assert [bad["line"] for bad in run["malformed"]] == [7, 8]
    mirrors = [records(path)[0] for path in session_files(logs)]
    assert [(rec["session"], rec["doing"]) for rec in mirrors] == [
        ("sess-a", "begin"),
        ("sess-b", "(done)"),
    ]
    assert shown.returncode == 0, shown.stderr
    assert "malformed" in shown.stdout.lower()
    assert "line 7" in shown.stdout
    assert "evidence: out/a.json" in shown.stdout
    rendered_origins = [
        origin
        for line in shown.stdout.splitlines()
        for origin in ("sess-a/session", "sess-b/thread-9")
        if " > " in line and origin in line
    ]
    assert rendered_origins == ["sess-a/session"] * 6 + ["sess-b/thread-9"]


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


def test_writer_rejects_run_id_traversal(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    refused = call(MILESTONE, "--run", "../escape", "begin", logs=logs)

    assert refused.returncode != 0
    assert not list(logs.rglob("*.jsonl"))


def test_run_id_grammar_classes_are_rejected_in_process() -> None:
    spec = importlib.util.spec_from_file_location(
        "milestone_writer_for_test", MILESTONE
    )
    assert spec is not None and spec.loader is not None
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    for unsafe in (
        "",
        ".",
        "..",
        "-leading-dash",
        "x" * 65,
        "sp ace",
        "tab\tid",
        "a/b",
    ):
        with pytest.raises(ValueError, match="unsafe run id"):
            writer.run_journal(unsafe)


def test_the_reader_rejects_unsafe_run_identifiers_too(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    (logs / "runs").mkdir(parents=True)
    refused = call(TIMELINE, "--run", "../escape", "--json", logs=logs)

    assert refused.returncode != 0


def test_missing_run_is_reported_rather_than_rendered_empty(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    (logs / "runs").mkdir(parents=True)
    missing = call(TIMELINE, "--run", "never-ran", "--json", logs=logs)

    assert missing.returncode != 0
    assert "never-ran" in missing.stderr


# ── malformed input ──────────────────────────────────────────────────────────

# ── concurrency ──────────────────────────────────────────────────────────────


def test_concurrent_writers_leave_the_journal_valid_jsonl(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    count = 8
    ready = tmp_path / "ready"
    ready.mkdir()
    release = tmp_path / "release"
    child = """
import importlib.util
import os
from pathlib import Path
import sys
import time

source = Path(os.environ["MILESTONE_SOURCE"])
spec = importlib.util.spec_from_file_location("synchronized_milestone_writer", source)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {source}")
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)
append_line = writer._append_line

def synchronized_append(target, line):
    if target.parent.name == "runs":
        (Path(os.environ["SYNC_READY"]) / os.environ["SYNC_ID"]).touch()
        release = Path(os.environ["SYNC_RELEASE"])
        deadline = time.monotonic() + 10
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("append barrier was not released")
            time.sleep(0.01)
    append_line(target, line)

writer._append_line = synchronized_append
raise SystemExit(writer.main(sys.argv[1:]))
"""
    processes: list[subprocess.Popen[str]] = []
    for index in range(count):
        env = dict(os.environ)
        env.update(
            ASSISTANT_LOGS=str(logs),
            CLAUDE_CODE_SESSION_ID=f"sess-{index % 3}",
            MILESTONE_SOURCE=str(MILESTONE),
            SYNC_ID=f"writer-{index}",
            SYNC_READY=str(ready),
            SYNC_RELEASE=str(release),
        )
        env.pop("CODEX_SESSION_ID", None)
        env.pop("CODEX_THREAD_ID", None)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child,
                    "--run",
                    "nightly-01",
                    "--task",
                    f"t{index}",
                    f"piece {index}",
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        )

    outputs: list[tuple[int | None, str]] = []
    try:
        deadline = time.monotonic() + 10
        expected_ready = {f"writer-{index}" for index in range(count)}
        while {path.name for path in ready.iterdir()} != expected_ready:
            if time.monotonic() >= deadline or any(
                process.poll() is not None for process in processes
            ):
                break
            time.sleep(0.01)
        assert {path.name for path in ready.iterdir()} == expected_ready
        assert all(process.poll() is None for process in processes)
        release.touch()
        for process in processes:
            _stdout, stderr = process.communicate(timeout=10)
            outputs.append((process.returncode, stderr))
    finally:
        release.touch(exist_ok=True)
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)

    assert all(returncode == 0 for returncode, _stderr in outputs), outputs
    written = records(logs / "runs" / "nightly-01.jsonl")
    assert len(written) == count
    assert {rec["task"] for rec in written} == {f"t{index}" for index in range(count)}


def test_evidence_truncation_and_multibyte_records_stay_inside_the_write_budget(
    tmp_path: Path,
) -> None:
    """One labeled multibyte record proves field caps and the byte budget."""
    logs = tmp_path / "logs"
    run_id = "r" * 64
    session_id = "s" * 64
    wide = "漢" * 201
    evidence_values = [f"{index:02d}-" + "証" * 198 for index in range(20)]
    evidence_args = []
    for value in evidence_values:
        evidence_args += ["--evidence", value]
    written = call(
        MILESTONE,
        "--run",
        run_id,
        "--role",
        wide,
        "--event",
        "事" * 61,
        "--task",
        "務" * 101,
        "--state",
        "態" * 41,
        "--step",
        "999",
        "--attempt",
        "999",
        *evidence_args,
        wide,
        wide,
        logs=logs,
        session=session_id,
    )

    assert written.returncode == 0, written.stderr
    line = (logs / "runs" / f"{run_id}.jsonl").read_bytes()
    assert len(line) <= 4096, f"record is {len(line)} bytes"
    rec = json.loads(line)
    assert (rec["run"], rec["session"]) == (run_id, session_id)
    assert rec["role"] == wide[:200]
    assert rec["doing"] == wide[:200]
    assert rec["prev"] == wide[:200]
    assert rec["event"] == ("事" * 61)[:60]
    assert rec["task"] == ("務" * 101)[:100]
    assert rec["state"] == ("態" * 41)[:40]
    assert (rec["step"], rec["attempt"]) == (999, 999)
    assert rec["evidence_dropped"] == 19
    assert len(rec["evidence"]) == 1
    assert rec["evidence"][0] == evidence_values[0][:200]
    assert rec["evidence"][0].startswith("00-")
    assert len(rec["evidence"][0]) == 200
