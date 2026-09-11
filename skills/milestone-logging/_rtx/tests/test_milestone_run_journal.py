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
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

import pytest
import yaml

from officina.blueprints.graph import load_repository_blueprint_graph


ROOT = Path(__file__).resolve().parents[4]
RUNTIME = Path(__file__).resolve().parents[1]
MILESTONE = RUNTIME / "_milestone_writer.py"
TIMELINE = RUNTIME / "_agent_timeline.py"
BLUEPRINTS = RUNTIME / "blueprints"
WRITER_PROGRAM = (
    "import importlib.util, sys; "
    f"spec = importlib.util.spec_from_file_location('milestone_writer', {str(MILESTONE)!r}); "
    "module = importlib.util.module_from_spec(spec); "
    "spec.loader.exec_module(module); "
    "raise SystemExit(module.main(sys.argv[1], sys.argv[2:], prog=sys.argv[1]))"
)
TIMELINE_PROGRAM = (
    "import importlib.util, sys; "
    f"spec = importlib.util.spec_from_file_location('agent_timeline', {str(TIMELINE)!r}); "
    "module = importlib.util.module_from_spec(spec); "
    "spec.loader.exec_module(module); "
    "raise SystemExit(module.main(sys.argv[1], sys.argv[2:], prog=sys.argv[1]))"
)


def call(
    script: Path,
    *args: str,
    logs: Path,
    session: str = "sess-a",
    thread: str | None = None,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    output_encoding: str | None = None,
    operation: str = "record-progress",
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
    program = WRITER_PROGRAM if script == MILESTONE else TIMELINE_PROGRAM if script == TIMELINE else None
    command = [sys.executable, "-c", program, operation, *args] if program else [sys.executable, str(script), *args]
    return subprocess.run(
        command,
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


def write_json_line(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _load_module(name: str, source: Path):
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_interface_module(package_name: str, source: Path):
    """Load a private package exactly enough to exercise its real adapters."""
    package = types.ModuleType(package_name)
    package.__path__ = [str(RUNTIME)]
    sys.modules[package_name] = package
    return _load_module(f"{package_name}.{source.stem}", source)


def load_writer_interface_module():
    return _load_interface_module(
        "milestone_writer_interface_under_test", RUNTIME / "_milestone_interface.py"
    )


def load_timeline_interface_module():
    return _load_interface_module(
        "timeline_interface_under_test", RUNTIME / "_timeline_interface.py"
    )


def timeline_interface(monkeypatch: pytest.MonkeyPatch, logs: Path):
    interface = load_timeline_interface_module()
    monkeypatch.setattr(sys.modules["timeline_interface_under_test._agent_timeline"], "LOGS", logs)
    return interface


def test_writer_adapters_select_their_fixed_operations(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catch a raw argv gateway that leaves operation selection to callers."""
    interface = load_writer_interface_module()
    writer = sys.modules["milestone_writer_interface_under_test._milestone_writer"]
    monkeypatch.setattr(writer, "LOGS", tmp_path / "logs")

    assert interface.RecordProgress().run(["work", "--role", "worker"]) == 0
    assert interface.RecordCompletion().run(["finished", "--role", "worker"]) == 0
    assert interface.SessionPath().run([]) == 0
    assert interface.RunPath().run(["nightly-01"]) == 0
    capsys.readouterr()


@pytest.mark.parametrize(
    ("entry", "arguments", "program_name"),
    [
        ("RecordProgress", ["work"], "record-progress"),
        ("RecordProgress", ["work", "--role", ""], "record-progress"),
        ("RecordCompletion", ["finished"], "record-completion"),
        ("RecordCompletion", ["finished", "--role", ""], "record-completion"),
    ],
)
def test_recording_adapters_require_role_with_their_own_program_name(
    capsys: pytest.CaptureFixture[str], entry: str, arguments: list[str], program_name: str
) -> None:
    """Catch parser errors attributed to the generic machine-interface runner."""
    interface = load_writer_interface_module()

    assert getattr(interface, entry)().run(arguments) == 2
    assert capsys.readouterr().err.startswith(f"{program_name}:")


def load_writer_module():
    return _load_module("milestone_writer_under_test", MILESTONE)


class _FixedDatetime:
    @classmethod
    def now(cls):
        return cls()

    def astimezone(self) -> datetime:
        return datetime(
            2026, 9, 9, 8, 34, 56, tzinfo=timezone(timedelta(hours=-4))
        )


def _configured_writer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, thread: str | None):
    writer, logs = load_writer_module(), tmp_path / "logs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setattr(writer, "LOGS", logs)
    monkeypatch.setattr(writer, "datetime", _FixedDatetime)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv("CODEX_THREAD_ID", thread) if thread else monkeypatch.delenv(
        "CODEX_THREAD_ID", raising=False
    )
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.chdir(cwd)
    return writer, logs, cwd


def _typed_progress_arguments(*, run: str | None = None) -> list[str]:
    arguments = [
        "work", "previous", "--role", "worker", "--event", "task", "--step", "2",
        "--task", "extract", "--state", "started", "--attempt", "1", "--evidence",
        "out/a.json", "--evidence", "out/b.json",
    ]
    if run is not None:
        arguments += ["--run", run]
    return arguments


def test_typed_without_run_is_retained_in_the_session_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catch typed state being rejected or discarded outside a durable run."""
    writer, logs, cwd = _configured_writer(monkeypatch, tmp_path, thread=None)

    assert writer.main("record-progress", _typed_progress_arguments(), prog="record-progress") == 0

    assert records(session_files(logs)[0]) == [{
        "ts": "2026-09-09T08:34:56-04:00", "role": "worker", "cwd": str(cwd),
        "doing": "work", "prev": "previous", "event": "task", "step": 2,
        "task": "extract", "state": "started", "attempt": 1,
        "evidence": ["out/a.json", "out/b.json"],
    }]


def test_additive_run_mirror_adds_identity_without_changing_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catch run mirroring that mutates session data or reserializes a copy."""
    writer, logs, cwd = _configured_writer(monkeypatch, tmp_path, thread="agent-a")

    assert writer.main("record-progress", _typed_progress_arguments(), prog="record-progress") == 0
    baseline = records(session_files(logs)[0])[0]
    assert writer.main(
        "record-progress", _typed_progress_arguments(run="nightly-01"), prog="record-progress"
    ) == 0

    lines = session_files(logs)[0].read_bytes().splitlines(keepends=True)
    mirrored = json.loads(lines[1])
    assert {key: mirrored[key] for key in baseline} == baseline
    assert set(mirrored) - set(baseline) == {"run", "session", "agent"}
    assert (mirrored["run"], mirrored["session"], mirrored["agent"]) == (
        "nightly-01", "sess-a", "agent-a"
    )
    assert (logs / "runs" / "nightly-01.jsonl").read_bytes() == lines[1]


def _json_value_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _json_text_at_quota(quota: int) -> str:
    value = "\x01🙂"
    return value + "a" * (quota - _json_value_size(value))


class _QuotaDatetime:
    quota = 48

    @classmethod
    def now(cls):
        return cls()

    def astimezone(self):
        return self

    def isoformat(self, *, timespec: str) -> str:
        return _json_text_at_quota(self.quota)

    def strftime(self, _format: str) -> str:
        return "2026-09-09"


def _evidence_at_quota(quota: int) -> list[str]:
    return [_json_text_at_quota(198)] * 6 + [_json_text_at_quota(quota - 1202)]


def _scalar_status(writer, field: str, value: object, monkeypatch: pytest.MonkeyPatch) -> int:
    operation, args = "record-progress", ["work", "previous", "--role", "worker"]
    if field == "result":
        operation, args = "record-completion", [str(value), "--role", "worker"]
    elif field in {"doing", "prev", "role"}:
        args[{"doing": 0, "prev": 1, "role": 3}[field]] = str(value)
    elif field == "cwd":
        monkeypatch.setattr(writer.os, "getcwd", lambda: str(value))
    elif field == "timestamp":
        _QuotaDatetime.quota = int(value); monkeypatch.setattr(writer, "datetime", _QuotaDatetime)
    elif field in {"session", "agent"}:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID" if field == "session" else "CODEX_THREAD_ID", str(value))
        args += ["--run", "nightly-01"]
    else:
        args += [f"--{field}", str(value)]
    return writer.main(operation, args, prog=operation)


def test_maximum_shaped_record_retains_every_value_within_line_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writer, logs, _cwd = _configured_writer(monkeypatch, tmp_path, thread="agent-a")
    monkeypatch.setattr(
        writer,
        "log_path",
        lambda _session, _agent: logs / "2026-09-09" / "max.session.jsonl",
    )
    monkeypatch.setattr(writer, "datetime", _QuotaDatetime); _QuotaDatetime.quota = 48
    monkeypatch.setattr(writer.os, "getcwd", lambda: _json_text_at_quota(512))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _json_text_at_quota(128))
    monkeypatch.setenv("CODEX_THREAD_ID", _json_text_at_quota(128))
    evidence, integer = _evidence_at_quota(1400), 10 ** 23
    args = [
        _json_text_at_quota(220), _json_text_at_quota(220), "--role", _json_text_at_quota(220),
        "--run", "r" * 64, "--event", _json_text_at_quota(80), "--step", str(integer),
        "--task", _json_text_at_quota(128), "--state", _json_text_at_quota(64),
        "--attempt", str(integer), *(part for item in evidence for part in ("--evidence", item)),
    ]
    assert writer.main("record-progress", args, prog="record-progress") == 0
    line = (logs / "runs" / ("r" * 64 + ".jsonl")).read_bytes()
    record = json.loads(line)
    expected_sizes = writer._VALUE_LIMITS
    assert set(record) == set(expected_sizes) and all(_json_value_size(record[key]) == size for key, size in expected_sizes.items())
    assert (record["doing"], record["prev"], record["role"]) == (_json_text_at_quota(220),) * 3
    assert (record["run"], record["event"], record["task"], record["state"]) == ("r" * 64, _json_text_at_quota(80), _json_text_at_quota(128), _json_text_at_quota(64))
    assert (record["step"], record["attempt"], record["evidence"], record["ts"], record["cwd"], record["session"], record["agent"]) == (integer, integer, evidence, _json_text_at_quota(48), _json_text_at_quota(512), _json_text_at_quota(128), _json_text_at_quota(128))
    assert len(line) <= writer.LINE_BUDGET
    assert _scalar_status(writer, "result", _json_text_at_quota(220), monkeypatch) == 0
    assert any(item["prev"] == _json_text_at_quota(220) for path in session_files(logs) for item in records(path))


@pytest.mark.parametrize(
    ("field", "quota"),
    [("doing", 220), ("result", 220), ("prev", 220), ("role", 220), ("cwd", 512),
     ("event", 80), ("task", 128), ("state", 64), ("step", 24), ("attempt", 24),
     ("session", 128), ("agent", 128), ("run", 66), ("timestamp", 48)],
)
def test_each_scalar_rejects_one_json_byte_over(
    field: str, quota: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writer, _logs, _cwd = _configured_writer(monkeypatch, tmp_path, thread="agent-a")
    value = quota + 1 if field == "timestamp" else _json_text_at_quota(quota + 1)
    value = 10 ** quota if field in {"step", "attempt"} else value
    if field == "run":
        value = "r" * 65
    assert _scalar_status(writer, field, value, monkeypatch) == 2


@pytest.mark.parametrize("evidence", [["small"] * 21, [_json_text_at_quota(221)]])
def test_evidence_limits_are_independently_reachable(
    evidence: list[str], tmp_path: Path
) -> None:
    result = call(
        MILESTONE, "work", "--role", "worker",
        *(part for item in evidence for part in ("--evidence", item)), logs=tmp_path / "logs",
    )
    assert result.returncode == 2
    assert "--evidence" in result.stderr
    assert not list((tmp_path / "logs").rglob("*.jsonl"))


def test_aggregate_evidence_accepts_1400_json_bytes_and_rejects_1401(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    for quota, expected in ((1400, 0), (1401, 2)):
        evidence = _evidence_at_quota(quota)
        result = call(MILESTONE, "work", "--role", "worker",
                      *(part for item in evidence for part in ("--evidence", item)), logs=logs)
        assert result.returncode == expected
    assert records(session_files(logs)[0])[0]["evidence"] == _evidence_at_quota(1400)


def test_session_path_is_exact_and_does_not_append(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writer, logs, _cwd = _configured_writer(monkeypatch, tmp_path, thread="agent-a")
    assert writer.main("session-path", [], prog="session-path") == 0
    assert Path(capsys.readouterr().out.strip()) == logs / "2026-09-09" / "sess-a.agent-a.jsonl"
    assert not logs.exists()


def _direct_io(blueprint: str, interface: str) -> dict:
    document = yaml.safe_load((BLUEPRINTS / blueprint).read_text(encoding="utf-8"))
    return document["interfaces"][interface]["contract"]["direct_io"]


def test_blueprint_graph_exposes_additive_milestone_operations() -> None:
    graph = load_repository_blueprint_graph(ROOT)
    specs = {
        "record-progress": ("rtx-milestone-writer", "RecordProgress", 1, 2, ["--role"]), "record-completion": ("rtx-milestone-writer", "RecordCompletion", 1, 1, ["--role"]),
        "session-path": ("rtx-milestone-writer", "SessionPath", 0, 0, []), "run-path": ("rtx-milestone-writer", "RunPath", 1, 1, []),
        "list-sessions": ("rtx-agent-timeline", "ListSessions", 0, 0, []), "show-latest-session": ("rtx-agent-timeline", "ShowLatestSession", 0, 0, []),
        "show-session": ("rtx-agent-timeline", "ShowSession", 1, 1, []), "show-run": ("rtx-agent-timeline", "ShowRun", 1, 1, []), "read-run-json": ("rtx-agent-timeline", "ReadRunJson", 1, 1, []),
    }
    exports = {key: value for key, value in graph.exports.items() if key.startswith("milestone-logging._rtx.interface.")}
    assert set(exports) == {f"milestone-logging._rtx.interface.{name}" for name in specs}
    for name, (source, entry, low, high, required) in specs.items():
        export = exports[f"milestone-logging._rtx.interface.{name}"]
        assert export.source_interface_id == f"milestone-logging._rtx.source.{source}.interface.{name}"
        pattern = export.declaration["process_binding"]["patterns"][0]
        assert export.declaration["process_binding"]["entry"] == entry
        assert (pattern["min_positionals"], pattern["max_positionals"], pattern.get("required_flags", [])) == (low, high, required)
        assert not {"--done", "--path", "--list", "--json"} & set(pattern.get("allowed_flags", []))
        assert export.declaration["contract"]["execution"]["state_effect"] == ("mutating" if name.startswith("record-") else "read-only")


def test_writer_contract_declares_the_selected_log_root() -> None:
    direct_io = _direct_io(
        "rtx-milestone-writer.yaml",
        "milestone-logging._rtx.source.rtx-milestone-writer.interface.record-progress",
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
        "milestone-logging._rtx.source.rtx-agent-timeline.interface.show-latest-session",
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
    assert reads["read-4"]["path"] == "<selected-milestone-log-root>/dispatch/**"
    assert "trace_id" in reads["read-4"]["reason"]


def test_timeline_adapters_select_their_fixed_operations(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catch a raw argv gateway that lets callers select a timeline operation."""
    logs = tmp_path / "logs"
    day = logs / "2026-08-22"
    day.mkdir(parents=True)
    write_json_line(
        day / "visible.session.jsonl",
        {"ts": "2026-08-22T09:00:00+00:00", "role": "reviewer", "cwd": "/workspace",
         "doing": "start", "prev": "previous"},
    )
    latest = day / "latest.session.jsonl"
    write_json_line(latest, {"ts": "2026-08-22T09:01:00+00:00", "role": "reviewer", "cwd": "/workspace", "doing": "latest", "prev": "start"})
    os.utime(day / "visible.session.jsonl", (1_800_000_001, 1_800_000_001))
    os.utime(latest, (1_800_000_002, 1_800_000_002))
    (logs / "runs").mkdir()
    write_json_line(
        logs / "runs" / "nightly-01.jsonl",
        {"ts": "2026-08-22T09:01:00+00:00", "role": "reviewer", "cwd": "/workspace",
         "doing": "run event", "prev": "", "run": "nightly-01",
         "session": "hidden-session", "agent": "session"},
    )

    interface = load_timeline_interface_module()
    timeline = sys.modules["timeline_interface_under_test._agent_timeline"]
    monkeypatch.setattr(timeline, "LOGS", logs)

    assert interface.ListSessions().run([]) == 0
    assert "visible  (1 log file)" in capsys.readouterr().out
    assert interface.ShowLatestSession().run([]) == 0
    assert capsys.readouterr().out.startswith("session latest\n")
    assert interface.ShowSession().run(["visible"]) == 0
    assert capsys.readouterr().out.startswith("session visible\n")
    assert interface.ShowRun().run(["nightly-01"]) == 0
    assert capsys.readouterr().out.startswith("run nightly-01\n")
    assert interface.ReadRunJson().run(["nightly-01"]) == 0
    assert json.loads(capsys.readouterr().out)["run"] == "nightly-01"


@pytest.mark.parametrize(
    ("entry", "program_name"),
    [
        ("ListSessions", "list-sessions"),
        ("ShowLatestSession", "show-latest-session"),
        ("ShowSession", "show-session"),
        ("ShowRun", "show-run"),
        ("ReadRunJson", "read-run-json"),
    ],
)
@pytest.mark.parametrize("old_flag", ["--list", "--run", "--json"])
def test_old_timeline_flag_errors_name_the_adapter_program(
    capsys: pytest.CaptureFixture[str], entry: str, program_name: str, old_flag: str
) -> None:
    """Catch a compatibility flag accepted by a replacement reader interface."""
    interface = load_timeline_interface_module()

    assert getattr(interface, entry)().run([old_flag]) == 2
    assert capsys.readouterr().err.startswith(f"{program_name}:")


def test_timeline_slow_is_opt_in_and_preserves_typed_event_order(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catch default slow marking, filtering, or loss of typed session fields."""
    logs = tmp_path / "logs"
    day = logs / "2026-08-22"
    day.mkdir(parents=True)
    for timestamp, doing, state in (
        ("2026-08-22T09:00:00+00:00", "first event", "started"),
        ("2026-08-22T09:00:12+00:00", "second event", "succeeded"),
    ):
        with (day / "slow.session.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": timestamp, "role": "worker", "cwd": "/workspace",
                "doing": doing, "prev": "before", "event": "task", "step": 7,
                "task": "extract", "state": state, "attempt": 2,
                "evidence": ["out/result.json"]}) + "\n")
    interface = timeline_interface(monkeypatch, logs)
    assert interface.ShowSession().run(["slow"]) == 0
    plain = capsys.readouterr().out
    assert "[slow]" not in plain
    assert all(value in plain for value in ("event=task", "step=7", "task=extract", "state=started", "attempt=2", "evidence: out/result.json"))
    assert interface.ShowSession().run(["slow", "--slow", "10"]) == 0
    marked = capsys.readouterr().out
    assert marked.count("[slow]") == 1
    assert [line.replace(" [slow]", "") for line in marked.splitlines() if " > " in line] == [line for line in plain.splitlines() if " > " in line]


@pytest.mark.parametrize(("entry", "arguments", "program_name"), [
    ("ShowSession", ["session"], "show-session"),
    ("ShowLatestSession", [], "show-latest-session"),
])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_slow_is_rejected_by_session_adapters(
    capsys: pytest.CaptureFixture[str], entry: str, arguments: list[str], program_name: str, value: str
) -> None:
    """Catch zero, non-finite, or negative slow thresholds accepted as meaningful."""
    assert getattr(load_timeline_interface_module(), entry)().run([*arguments, "--slow", value]) == 2
    assert capsys.readouterr().err.startswith(f"{program_name}:")


def test_list_sessions_labels_log_files_not_agents(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catch the path count being presented as a count of agents."""
    logs = tmp_path / "logs"
    day = logs / "2026-08-22"
    day.mkdir(parents=True)
    for agent in ("first", "second"):
        write_json_line(day / f"visible.{agent}.jsonl", {"ts": "2026-08-22T09:00:00+00:00"})
    assert timeline_interface(monkeypatch, logs).ListSessions().run([]) == 0
    shown = capsys.readouterr().out
    assert "(2 log files)" in shown and "agent" not in shown


def test_codex_timeline_needs_no_milestones_and_joins_exact_trace(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    interface = load_timeline_interface_module()
    timeline = sys.modules["timeline_interface_under_test._agent_timeline"]
    logs, sessions = tmp_path / "logs", tmp_path / "sessions"
    trace_id, turn_id = "a" * 32, "turn-1"
    session = "12345678-1234-1234-1234-123456789abc"
    trace = logs / "dispatch" / "2026-09-11" / trace_id
    trace.mkdir(parents=True)
    spans = [
        ("1" * 32, None, "process", 0, 2_000_000_000, "root.interface.run"),
        ("2" * 32, "1" * 32, "interface_body", 200_000_000, 1_500_000_000, None),
        ("3" * 32, "2" * 32, "process", 500_000_000, 800_000_000, "child.interface.run"),
        ("4" * 32, "3" * 32, "interface_body", 600_000_000, 400_000_000, None),
    ]
    for span_id, parent, layer, started, duration, target in spans:
        row = {"schema": 1, "layer": layer, "trace_id": trace_id, "span_id": span_id,
               "parent_span_id": parent, "wall_started_ns": 1, "monotonic_started_ns": started,
               "duration_ns": duration, "outcome": "success"}
        if target:
            row.update(caller="root", interface=target, exit_code=0)
        (trace / f"{span_id}.json").write_text(json.dumps(row) + "\n")
    duplicate = dict(row, span_id="5" * 32, parent_span_id=None)
    (trace / "duplicate-root.json").write_text(json.dumps(duplicate) + "\n")
    duplicate["parent_span_id"] = duplicate["span_id"]
    (trace / "duplicate-cycle.json").write_text(json.dumps(duplicate) + "\n")
    rollout = sessions / "2026" / "09" / "11" / f"rollout-test-{session}.jsonl"
    rollout.parent.mkdir(parents=True)
    meta = {"turn_id": turn_id}
    records = [
        ("2026-09-11T12:00:00Z", "turn_context", {"turn_id": turn_id}),
        ("2026-09-11T12:00:00Z", "response_item", {"type": "message", "role": "user", "internal_chat_message_metadata_passthrough": meta}),
        ("2026-09-11T12:00:01Z", "response_item", {"type": "function_call", "call_id": "call-1", "name": "invoke", "internal_chat_message_metadata_passthrough": meta}),
        ("2026-09-11T12:00:03Z", "response_item", {"type": "function_call_output", "call_id": "call-1", "output": json.dumps({"content": [{"text": json.dumps({"trace_id": "b" * 32})}], "structuredContent": {"result": {"trace_id": trace_id}}}), "internal_chat_message_metadata_passthrough": meta}),
        ("2026-09-11T12:00:03.2Z", "response_item", {"type": "message", "role": "assistant", "phase": "commentary", "internal_chat_message_metadata_passthrough": meta}),
        ("2026-09-11T12:00:04Z", "response_item", {"type": "message", "role": "assistant", "phase": "final_answer", "internal_chat_message_metadata_passthrough": meta}),
    ]
    with rollout.open("w") as handle:
        for timestamp, kind, payload in records:
            handle.write(json.dumps({"timestamp": timestamp, "type": kind, "payload": payload}) + "\n")
    monkeypatch.setattr(timeline, "LOGS", logs)
    monkeypatch.setattr(timeline, "CODEX_SESSIONS", sessions)

    assert interface.ListSessions().run([]) == 0
    assert f"{session}  (Codex transcript)" in capsys.readouterr().out
    assert interface.ShowSession().run([session]) == 0
    shown = capsys.readouterr().out
    assert all(text in shown for text in ("decision 1.000s", "Famulus call 2.000s",
        "root.interface.run 2.000s (self 0.500s)", "child.interface.run 0.800s (self 0.400s)",
        "response creation 1.000s"))


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
    call(
        MILESTONE, "--run", "unicode-run", "--role", "worker", "json payload 🙂", logs=logs
    )

    plain_legacy = call(
        TIMELINE,
        "sess-a",
        logs=logs,
        env_overrides={"PYTHONIOENCODING": "cp1252"},
        operation="show-session",
    )
    utf8 = call(
        TIMELINE,
        "unicode-run",
        logs=logs,
        env_overrides={"PYTHONIOENCODING": "utf-8"},
        output_encoding="utf-8",
        operation="read-run-json",
    )
    legacy = call(
        TIMELINE,
        "unicode-run",
        logs=logs,
        env_overrides={"PYTHONIOENCODING": "cp1252"},
        operation="read-run-json",
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
        "--role",
        "worker",
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
        "--role",
        "worker",
        "--event",
        "run-end",
        "--step",
        "9",
        "finish",
        logs=logs,
        session="sess-b",
        thread="thread-9",
        operation="record-completion",
    )

    dumped = call(TIMELINE, "nightly-01", logs=logs, operation="read-run-json")
    shown = call(TIMELINE, "nightly-01", logs=logs, operation="show-run")

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
    shown = call(MILESTONE, "nightly-01", logs=logs, operation="run-path")

    assert shown.returncode == 0, shown.stderr
    assert Path(shown.stdout.strip()) == logs / "runs" / "nightly-01.jsonl"


def test_typed_fields_without_a_run_are_retained(tmp_path: Path) -> None:
    """Structured data remains useful in the session log without a run journal."""
    logs = tmp_path / "logs"
    recorded = call(
        MILESTONE, "--event", "run-start", "--role", "worker", "begin", logs=logs
    )

    assert recorded.returncode == 0, recorded.stderr
    assert records(session_files(logs)[0])[0]["event"] == "run-start"


def test_writer_rejects_run_id_traversal(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    refused = call(
        MILESTONE, "--run", "../escape", "--role", "worker", "begin", logs=logs
    )

    assert refused.returncode != 0
    assert "unsafe run id" in refused.stderr
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
    refused = call(TIMELINE, "../escape", logs=logs, operation="read-run-json")

    assert refused.returncode != 0


def test_missing_run_is_reported_rather_than_rendered_empty(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    (logs / "runs").mkdir(parents=True)
    missing = call(TIMELINE, "never-ran", logs=logs, operation="read-run-json")

    assert missing.returncode != 0
    assert "never-ran" in missing.stderr


# ── malformed input ──────────────────────────────────────────────────────────

# ── concurrency ──────────────────────────────────────────────────────────────


def test_concurrent_writers_leave_the_journal_valid_jsonl(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    count = 8
    phase_timeout = 30.0
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
        deadline = float(os.environ["SYNC_DEADLINE"])
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("append barrier was not released")
            time.sleep(0.01)
    append_line(target, line)

writer._append_line = synchronized_append
raise SystemExit(writer.main("record-progress", sys.argv[1:], prog="record-progress"))
"""
    processes: list[subprocess.Popen[str]] = []
    readiness_deadline = time.monotonic() + phase_timeout
    for index in range(count):
        env = dict(os.environ)
        env.update(
            ASSISTANT_LOGS=str(logs),
            CLAUDE_CODE_SESSION_ID=f"sess-{index % 3}",
            MILESTONE_SOURCE=str(MILESTONE),
            SYNC_ID=f"writer-{index}",
            SYNC_READY=str(ready),
            SYNC_RELEASE=str(release),
            SYNC_DEADLINE=str(readiness_deadline),
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
                    "--role",
                    "worker",
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
        expected_ready = {f"writer-{index}" for index in range(count)}
        while {path.name for path in ready.iterdir()} != expected_ready:
            if time.monotonic() >= readiness_deadline or any(
                process.poll() is not None for process in processes
            ):
                break
            time.sleep(0.01)
        observed_ready = {path.name for path in ready.iterdir()}
        early_exits = []
        for index, process in enumerate(processes):
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                early_exits.append(
                    {
                        "writer": index,
                        "session": f"sess-{index % 3}",
                        "returncode": process.returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
        assert observed_ready == expected_ready, {
            "ready": sorted(observed_ready),
            "early_exits": early_exits,
        }
        assert all(process.poll() is None for process in processes)
        release.touch()
        completion_deadline = time.monotonic() + phase_timeout
        for process in processes:
            remaining = max(0.1, completion_deadline - time.monotonic())
            _stdout, stderr = process.communicate(timeout=remaining)
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


def test_evidence_and_other_over_budget_fields_are_rejected_without_truncation(
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

    assert written.returncode == 2
    assert "exceeds" in written.stderr
    assert not list(logs.rglob("*.jsonl"))
