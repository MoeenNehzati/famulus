"""Opt-in integration tests against installed Claude Code and Codex clients.

These tests exercise real client behavior and are intentionally excluded from
ordinary unit-test runs. Enable them explicitly with
``LLM_WAKEUP_RUN_CLIENT_TESTS=1``. Each client performs one minimal model turn
so its host records contain current quota information.
"""

from __future__ import annotations

import json
import os
import pty
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from officina.wakeup.claude_codex_sessions import (
    find_session_log,
    latest_rate_limit,
)
from officina.wakeup.claude_codex_usage import UsageSnapshot, read_codex_usage


RUN_CLIENT_TESTS = os.environ.get("LLM_WAKEUP_RUN_CLIENT_TESTS") == "1"
CLIENT_TEST = pytest.mark.skipif(
    not RUN_CLIENT_TESTS,
    reason="set LLM_WAKEUP_RUN_CLIENT_TESTS=1 to invoke real LLM clients",
)


def _assert_quota_snapshot(snapshot: UsageSnapshot) -> None:
    """Validate fields needed by automatic scheduling, not provider trivia."""

    assert 0.0 <= snapshot.used_percentage <= 100.0
    assert snapshot.resets_at > 0
    assert snapshot.session_id
    assert snapshot.window


def _read_saved_snapshots(root: Path) -> list[UsageSnapshot]:
    directory = root / "usage-snapshots"
    return [
        UsageSnapshot(**json.loads(path.read_text()))
        for path in directory.glob("*.json")
    ] if directory.exists() else []


@CLIENT_TEST
@pytest.mark.skipif(os.name == "nt", reason="Claude PTY probe requires Unix pty")
def test_claude_client_emits_parseable_quota_status(
    tmp_path: Path,
) -> None:
    """Launch Claude TUI and verify its real status payload reaches storage."""

    executable = shutil.which("claude")
    if executable is None:
        pytest.skip("claude executable is not installed")
    state = tmp_path / "state"
    environment = os.environ.copy()
    environment["LLM_WAKEUP_HOME"] = str(state)
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [executable],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env=environment,
        start_new_session=True,
    )
    os.close(slave)
    output = bytearray()
    started_at = time.monotonic()
    deadline = started_at + 120
    snapshots: list[UsageSnapshot] = []
    prompt_sent = False
    limit_message_seen = False
    exit_sent = False
    try:
        while time.monotonic() < deadline:
            snapshots = _read_saved_snapshots(state)
            if snapshots:
                break
            ready, _, _ = select.select([master], [], [], 0.25)
            if ready:
                try:
                    output.extend(os.read(master, 65_536))
                except OSError:
                    break
                limit_message_seen = all(
                    token in output for token in (b"hit", b"limit", b"resets")
                )
                if limit_message_seen:
                    break
            if not prompt_sent and time.monotonic() - started_at >= 2:
                os.write(master, b"Reply exactly OK and do not use tools.\r")
                prompt_sent = True
            if process.poll() is not None:
                break
        if not limit_message_seen and not exit_sent and process.poll() is None:
            os.write(master, b"/exit\r")
            exit_sent = True
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)

    if snapshots:
        assert {snapshot.provider for snapshot in snapshots} == {"claude"}
        for snapshot in snapshots:
            _assert_quota_snapshot(snapshot)
    else:
        assert limit_message_seen, (
            "Claude produced neither quota snapshots nor a reset message. "
            "Terminal output tail: "
            + output.decode("utf-8", errors="replace")[-1000:]
        )
        detected = latest_rate_limit()
        assert detected.provider == "claude"
        assert detected.reset_at.timestamp() > time.time()


@CLIENT_TEST
def test_codex_client_writes_parseable_quota_record(tmp_path: Path) -> None:
    """Run one real Codex turn and parse quota data from that exact session."""

    executable = shutil.which("codex")
    if executable is None:
        pytest.skip("codex executable is not installed")
    result = subprocess.run(
        [
            executable,
            "exec",
            "--json",
            "Reply with exactly the word OK and do not use tools.",
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    events = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    session_id = next(
        (
            str(event["thread_id"])
            for event in events
            if event.get("type") == "thread.started" and event.get("thread_id")
        ),
        "",
    )
    assert session_id, f"Codex emitted no thread id: {result.stderr[-1000:]}"
    transcript = find_session_log("codex", session_id)
    assert transcript is not None, f"no transcript found for Codex session {session_id}"

    snapshots = read_codex_usage(transcript, session_id)

    assert snapshots, (
        f"Codex session {session_id} wrote no parseable rate-limit record; "
        f"exit={result.returncode} stderr={result.stderr[-1000:]}"
    )
    assert {snapshot.provider for snapshot in snapshots} == {"codex"}
    for snapshot in snapshots:
        _assert_quota_snapshot(snapshot)
