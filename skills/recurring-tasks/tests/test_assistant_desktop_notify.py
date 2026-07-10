#!/usr/bin/env python3
"""Tests for assistant_desktop_notify.py: cross-platform notification dispatch,
the rclone_notify.sh-compatible legacy message builder, and the CLI."""
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).parent.parent
SCRIPT = SKILL_DIR / "_rtx" / "_assistant_desktop_notify.py"


def _load():
    spec = importlib.util.spec_from_file_location("assistant_desktop_notify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── build_legacy_message ───────────────────────────────────────────────────────

def test_legacy_message_includes_last_errors():
    mod = _load()
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("starting sync\ninfo line\nERROR: could not connect\nfatal: disk full\n")
        path = f.name
    try:
        msg = mod.build_legacy_message("phd-backup", path)
        assert "Job: phd-backup" in msg
        assert "Last errors:" in msg
        assert "ERROR: could not connect" in msg
        assert "fatal: disk full" in msg
        assert "info line" not in msg
        print("PASS: last errors extracted from log")
    finally:
        Path(path).unlink()


def test_legacy_message_caps_at_five_errors():
    mod = _load()
    lines = [f"error: problem {i}" for i in range(8)]
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("\n".join(lines) + "\n")
        path = f.name
    try:
        msg = mod.build_legacy_message("job", path)
        # Only the last 5 should appear
        for i in range(3):
            assert f"problem {i}" not in msg
        for i in range(3, 8):
            assert f"problem {i}" in msg
        print("PASS: caps at last 5 error lines")
    finally:
        Path(path).unlink()


def test_legacy_message_no_errors_found():
    mod = _load()
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("all good\nnothing to see here\n")
        path = f.name
    try:
        msg = mod.build_legacy_message("job", path)
        assert "No error lines found in log." in msg
        print("PASS: reports no errors found")
    finally:
        Path(path).unlink()


def test_legacy_message_missing_log():
    mod = _load()
    msg = mod.build_legacy_message("job", "/no/such/file.log")
    assert "Log file missing/unreadable: /no/such/file.log" in msg
    print("PASS: reports missing log file")


def test_legacy_message_empty_log_arg():
    mod = _load()
    msg = mod.build_legacy_message("job", "")
    assert "Log file missing/unreadable:" in msg
    print("PASS: empty log path treated as missing")


# ── notify() platform dispatch ─────────────────────────────────────────────────

def test_notify_dispatches_to_linux():
    mod = _load()
    with mock.patch.object(mod.platform, "system", return_value="Linux"), \
         mock.patch.object(mod, "_notify_linux", return_value=True) as m:
        assert mod.notify("t", "b") is True
        m.assert_called_once()
        assert m.call_args[0][0] == "t"
        assert m.call_args[0][1] == "b"
    print("PASS: dispatches to _notify_linux on Linux")


def test_notify_dispatches_to_macos():
    mod = _load()
    with mock.patch.object(mod.platform, "system", return_value="Darwin"), \
         mock.patch.object(mod, "_notify_macos", return_value=True) as m:
        assert mod.notify("t", "b") is True
        m.assert_called_once()
    print("PASS: dispatches to _notify_macos on Darwin")


def test_notify_dispatches_to_windows():
    mod = _load()
    with mock.patch.object(mod.platform, "system", return_value="Windows"), \
         mock.patch.object(mod, "_notify_windows", return_value=True) as m:
        assert mod.notify("t", "b") is True
        m.assert_called_once()
    print("PASS: dispatches to _notify_windows on Windows")


def test_notify_unsupported_platform_returns_false():
    mod = _load()
    with mock.patch.object(mod.platform, "system", return_value="Plan9"):
        assert mod.notify("t", "b") is False
    print("PASS: unsupported platform returns False")


def test_notify_never_raises_on_internal_exception():
    mod = _load()
    with mock.patch.object(mod.platform, "system", return_value="Linux"), \
         mock.patch.object(mod, "_notify_linux", side_effect=RuntimeError("boom")):
        assert mod.notify("t", "b") is False
    print("PASS: notify() swallows unexpected exceptions and returns False")


def test_notify_logs_every_attempt():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "notify.log"
        with mock.patch.object(mod.platform, "system", return_value="Linux"), \
             mock.patch.object(mod, "_notify_linux", return_value=True):
            mod.notify("my title", "my body", urgency="critical", log_path=log_path)
        content = log_path.read_text()
        assert "title='my title'" in content
        assert "urgency=critical" in content
        assert "result ok=True" in content
    print("PASS: notify() logs every attempt regardless of outcome")


# ── _notify_linux: notify-send / logger fallback chain ─────────────────────────

def test_notify_linux_success_via_notify_send():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "notify.log"
        with mock.patch.object(mod, "shutil_which", return_value="/usr/bin/notify-send"), \
             mock.patch.object(mod, "_ensure_linux_gui_env"), \
             mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            assert mod._notify_linux("t", "b", "critical", log_path) is True
    print("PASS: _notify_linux succeeds via notify-send")


def test_notify_linux_falls_back_to_logger_on_notify_send_failure():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "notify.log"

        def which_side_effect(cmd):
            return f"/usr/bin/{cmd}"

        with mock.patch.object(mod, "shutil_which", side_effect=which_side_effect), \
             mock.patch.object(mod, "_ensure_linux_gui_env"), \
             mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="denied")
            ok = mod._notify_linux("t", "b", "critical", log_path)
        assert ok is False
        # notify-send attempted, then logger fallback attempted
        commands = [call.args[0][0] for call in run.call_args_list]
        assert "notify-send" in commands
        assert "logger" in commands
        assert "fell back to logger (syslog/journal)" in log_path.read_text()
    print("PASS: _notify_linux falls back to logger when notify-send fails")


def test_notify_linux_missing_notify_send_goes_straight_to_logger():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "notify.log"

        def which_side_effect(cmd):
            return None if cmd == "notify-send" else f"/usr/bin/{cmd}"

        with mock.patch.object(mod, "shutil_which", side_effect=which_side_effect), \
             mock.patch.object(mod, "_ensure_linux_gui_env"), \
             mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            ok = mod._notify_linux("t", "b", "critical", log_path)
        assert ok is False
        assert "notify-send not found on PATH" in log_path.read_text()
    print("PASS: _notify_linux skips straight to logger when notify-send is absent")


def test_notify_linux_no_tools_available_returns_false_without_raising():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "notify.log"
        with mock.patch.object(mod, "shutil_which", return_value=None), \
             mock.patch.object(mod, "_ensure_linux_gui_env"):
            ok = mod._notify_linux("t", "b", "critical", log_path)
        assert ok is False
    print("PASS: _notify_linux returns False cleanly when neither tool is present")


# ── _notify_macos ──────────────────────────────────────────────────────────────

def test_notify_macos_success():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "notify.log"
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            assert mod._notify_macos("t", "b", "critical", log_path) is True
            assert run.call_args[0][0][0] == "osascript"
    print("PASS: _notify_macos succeeds via osascript")


def test_notify_macos_failure():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "notify.log"
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
            assert mod._notify_macos("t", "b", "critical", log_path) is False
    print("PASS: _notify_macos reports failure on non-zero exit")


def test_osa_quote_escapes_quotes_and_backslashes():
    mod = _load()
    assert mod._osa_quote('He said "hi"') == '"He said \\"hi\\""'
    assert mod._osa_quote("back\\slash") == '"back\\\\slash"'
    print("PASS: _osa_quote escapes quotes and backslashes")


# ── _notify_windows ─────────────────────────────────────────────────────────────

def test_notify_windows_falls_back_to_powershell_when_win10toast_missing():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "notify.log"
        # win10toast is not installed in this environment, so the `import` inside
        # _notify_windows will naturally raise ImportError and fall through.
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            ok = mod._notify_windows("t", "b", "critical", log_path)
        assert ok is True
        assert run.call_args[0][0][0] == "powershell"
        assert "win10toast unavailable/failed" in log_path.read_text()
    print("PASS: _notify_windows falls back to PowerShell balloon when win10toast is unavailable")


def test_ps_quote_escapes_single_quotes():
    mod = _load()
    assert mod._ps_quote("it's a test") == "'it''s a test'"
    print("PASS: _ps_quote doubles single quotes for PowerShell")


# ── CLI (main) ──────────────────────────────────────────────────────────────────

def test_cli_legacy_form_builds_message_and_defaults_critical():
    mod = _load()
    with mock.patch.object(mod, "notify", return_value=True) as m:
        rc = mod.main(["my-job", "/no/such/log.log"])
    assert rc == 0
    m.assert_called_once()
    args, kwargs = m.call_args
    assert args[0] == "rclone bisync FAILED"  # default --title
    assert "Job: my-job" in args[1]
    assert "Log file missing/unreadable" in args[1]
    assert kwargs["urgency"] == "critical"
    print("PASS: CLI legacy form builds message from job/log and defaults to critical")


def test_cli_title_body_form_overrides_legacy_message():
    mod = _load()
    with mock.patch.object(mod, "notify", return_value=True) as m:
        rc = mod.main(["--title", "Recurring Tasks", "--body", "All checks passed", "--urgency", "low"])
    assert rc == 0
    args, kwargs = m.call_args
    assert args[0] == "Recurring Tasks"
    assert args[1] == "All checks passed"
    assert kwargs["urgency"] == "low"
    print("PASS: CLI --title/--body form bypasses legacy message building")


def test_cli_always_returns_zero_even_if_notify_fails():
    mod = _load()
    with mock.patch.object(mod, "notify", return_value=False):
        rc = mod.main(["job", "/no/such/log.log"])
    assert rc == 0
    print("PASS: CLI always returns 0 regardless of notify() outcome")


def test_cli_rejects_invalid_urgency():
    mod = _load()
    try:
        mod.main(["job", "", "--urgency", "extreme"])
        assert False, "should have raised SystemExit for invalid --urgency choice"
    except SystemExit as e:
        assert e.code != 0
    print("PASS: CLI rejects an invalid --urgency choice")


def test_cli_defaults_job_and_log_when_omitted():
    mod = _load()
    with mock.patch.object(mod, "notify", return_value=True) as m:
        mod.main([])
    args, _ = m.call_args
    assert "Job: unknown" in args[1]
    print("PASS: CLI defaults job to 'unknown' and log to empty when omitted")


if __name__ == "__main__":
    test_legacy_message_includes_last_errors()
    test_legacy_message_caps_at_five_errors()
    test_legacy_message_no_errors_found()
    test_legacy_message_missing_log()
    test_legacy_message_empty_log_arg()
    test_notify_dispatches_to_linux()
    test_notify_dispatches_to_macos()
    test_notify_dispatches_to_windows()
    test_notify_unsupported_platform_returns_false()
    test_notify_never_raises_on_internal_exception()
    test_notify_logs_every_attempt()
    test_notify_linux_success_via_notify_send()
    test_notify_linux_falls_back_to_logger_on_notify_send_failure()
    test_notify_linux_missing_notify_send_goes_straight_to_logger()
    test_notify_linux_no_tools_available_returns_false_without_raising()
    test_notify_macos_success()
    test_notify_macos_failure()
    test_osa_quote_escapes_quotes_and_backslashes()
    test_notify_windows_falls_back_to_powershell_when_win10toast_missing()
    test_ps_quote_escapes_single_quotes()
    test_cli_legacy_form_builds_message_and_defaults_critical()
    test_cli_title_body_form_overrides_legacy_message()
    test_cli_always_returns_zero_even_if_notify_fails()
    test_cli_rejects_invalid_urgency()
    test_cli_defaults_job_and_log_when_omitted()
    print("\nAll tests passed.")
