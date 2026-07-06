#!/usr/bin/env python3
"""assistant_desktop_notify.py — cross-platform desktop notifications with a
logging fallback.

Purpose:
    A single Pythonic replacement for ad-hoc `notify-send` bash calls used by
    cron jobs / systemd timers / recurring tasks. Tries the best mechanism
    available on the current OS, and ALWAYS logs what it did (cron/systemd
    jobs usually run without a GUI session, so the popup may silently fail —
    the log explains why).

Supported platforms:
    - Linux: notify-send (with best-effort GUI env recovery for cron/systemd
      contexts lacking DISPLAY/DBUS_SESSION_BUS_ADDRESS), falling back to
      `logger` (syslog/journal) if that fails.
    - macOS: osascript `display notification`.
    - Windows: `win10toast` if installed, else a PowerShell
      System.Windows.Forms balloon-tip fallback.

Lives in recurring-tasks/scripts/ (this skill opts out of the repo's
cross-platform validator via `cross_platform: false` in blueprint.yaml,
since dispatching per-OS commands is the intentional point of this tool).
Other scripts in this skill (e.g. healthcheck.py) call it as a sibling
script; it has no dependency on being installed or on PATH.

Usage as a CLI:
    # plain title/body notification
    assistant_desktop_notify.py --title "Title" --body "Body text" [--urgency critical|normal|low]

    # rclone_notify.sh-compatible legacy form (see main()'s docstring)
    assistant_desktop_notify.py "<job>" "<log-path>"

Usage as a library:
    from assistant_desktop_notify import notify
    notify("Title", "Body text", urgency="critical")

Exit code is always 0 (never cascade a notification failure into the
caller's exit status); use the return value of notify() to check success.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _default_log_path() -> Path:
    env_path = os.environ.get("ASSISTANT_NOTIFY_LOG")
    if env_path:
        return Path(env_path)
    home_log = Path.home() / ".local" / "share" / "assistant-notify" / "notify.log"
    return home_log


def _get_log_path() -> Path:
    path = _default_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a"):
            pass
        return path
    except OSError:
        fallback = Path(f"/tmp/assistant_notify_{os.getuid() if hasattr(os, 'getuid') else 'user'}.log")
        try:
            fallback.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return fallback


def _log(path: Path, *lines: str) -> None:
    try:
        with open(path, "a") as f:
            for line in lines:
                f.write(line + "\n")
    except OSError:
        pass


def _read_env_from_pid(pid: str, key: str) -> str:
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            data = f.read()
    except OSError:
        return ""
    for entry in data.split(b"\0"):
        if entry.startswith(key.encode() + b"="):
            return entry.split(b"=", 1)[1].decode(errors="replace")
    return ""


def _pgrep(name: str) -> list[str]:
    try:
        out = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-x", name],
            capture_output=True, text=True, timeout=3,
        )
        return [p for p in out.stdout.split() if p]
    except (OSError, subprocess.SubprocessError):
        return []


def _ensure_linux_gui_env(log_path: Path) -> None:
    """Best-effort: recover DISPLAY / DBUS_SESSION_BUS_ADDRESS when running
    under cron/systemd, which lack an interactive GUI session context."""
    uid = os.getuid()

    if not os.environ.get("XDG_RUNTIME_DIR"):
        candidate = f"/run/user/{uid}"
        if os.path.isdir(candidate):
            os.environ["XDG_RUNTIME_DIR"] = candidate

    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
        bus_sock = os.path.join(runtime_dir, "bus") if runtime_dir else ""
        if bus_sock and os.path.exists(bus_sock):
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_sock}"

    for pname in (
        "systemd", "gnome-session-binary", "gnome-session",
        "plasmashell", "kwin_wayland", "kwin_x11", "Xorg", "Xwayland",
    ):
        pids = _pgrep(pname)
        if not pids:
            continue
        pid = pids[0]

        if not os.environ.get("DISPLAY"):
            val = _read_env_from_pid(pid, "DISPLAY")
            if val:
                os.environ["DISPLAY"] = val

        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            val = _read_env_from_pid(pid, "DBUS_SESSION_BUS_ADDRESS")
            if val:
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = val

        if os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            break

    if not os.environ.get("DISPLAY"):
        x11_dir = Path("/tmp/.X11-unix")
        if x11_dir.is_dir():
            socks = sorted(x11_dir.glob("X*"))
            chosen = None
            for s in socks:
                try:
                    if s.stat().st_uid == uid:
                        chosen = s
                        break
                except OSError:
                    continue
            if chosen is None and socks:
                chosen = socks[0]
            if chosen is not None:
                os.environ["DISPLAY"] = ":" + chosen.name[1:]


def _notify_linux(title: str, body: str, urgency: str, log_path: Path) -> bool:
    _ensure_linux_gui_env(log_path)
    _log(
        log_path,
        f"env DISPLAY=\"{os.environ.get('DISPLAY', '')}\" "
        f"XDG_RUNTIME_DIR=\"{os.environ.get('XDG_RUNTIME_DIR', '')}\" "
        f"DBUS_SESSION_BUS_ADDRESS=\"{os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')}\"",
    )

    if shutil_which("notify-send"):
        try:
            result = subprocess.run(
                ["notify-send", f"--urgency={urgency}", title, body],
                capture_output=True, text=True, timeout=5,
            )
            _log(log_path, f"notify-send rc={result.returncode} stderr={result.stderr.strip()!r}")
            if result.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError) as exc:
            _log(log_path, f"notify-send raised: {exc!r}")
    else:
        _log(log_path, "notify-send not found on PATH")

    if shutil_which("logger"):
        try:
            subprocess.run(
                ["logger", "-t", "assistant-notify", f"{title}: {body}"],
                capture_output=True, timeout=5,
            )
            _log(log_path, "fell back to logger (syslog/journal)")
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def _notify_macos(title: str, body: str, urgency: str, log_path: Path) -> bool:
    script = (
        f'display notification {_osa_quote(body)} with title {_osa_quote(title)}'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        _log(log_path, f"osascript rc={result.returncode} stderr={result.stderr.strip()!r}")
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        _log(log_path, f"osascript raised: {exc!r}")
        return False


def _osa_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _notify_windows(title: str, body: str, urgency: str, log_path: Path) -> bool:
    try:
        from win10toast import ToastNotifier  # type: ignore

        ToastNotifier().show_toast(title, body, duration=8, threaded=True)
        _log(log_path, "win10toast: shown")
        return True
    except Exception as exc:  # noqa: BLE001 - best effort, log and fall through
        _log(log_path, f"win10toast unavailable/failed: {exc!r}")

    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        f"$n.ShowBalloonTip(8000, {_ps_quote(title)}, {_ps_quote(body)}, "
        "[System.Windows.Forms.ToolTipIcon]::Info)"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10,
        )
        _log(log_path, f"powershell balloon rc={result.returncode} stderr={result.stderr.strip()!r}")
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        _log(log_path, f"powershell balloon raised: {exc!r}")
        return False


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def shutil_which(cmd: str) -> str | None:
    import shutil

    return shutil.which(cmd)


def notify(title: str, body: str, urgency: str = "normal", log_path: Path | None = None) -> bool:
    """Send a desktop notification, best-effort, on the current platform.

    Always returns a bool (True on apparent success) and never raises.
    Every attempt is appended to the notify log regardless of outcome.
    """
    log_path = log_path or _get_log_path()
    timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    _log(log_path, "-----", f"[{timestamp}] title={title!r} urgency={urgency}")

    system = platform.system()
    try:
        if system == "Linux":
            ok = _notify_linux(title, body, urgency, log_path)
        elif system == "Darwin":
            ok = _notify_macos(title, body, urgency, log_path)
        elif system == "Windows":
            ok = _notify_windows(title, body, urgency, log_path)
        else:
            _log(log_path, f"unsupported platform: {system!r}")
            ok = False
    except Exception as exc:  # noqa: BLE001 - notification must never crash caller
        _log(log_path, f"unexpected error: {exc!r}")
        ok = False

    _log(log_path, f"result ok={ok}")
    return ok


def build_legacy_message(job: str, log: str) -> str:
    """Reproduce the message body the old rclone_notify.sh bash script built:
    job name, timestamp, and the last 5 error/fatal lines from the log (or a
    note that none were found / the log is missing)."""
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(sep=" ", timespec="seconds")
    msg = f"Job: {job}\nTime: {timestamp}"

    log_path = Path(log) if log else None
    if log_path and log_path.is_file():
        try:
            lines = log_path.read_text(errors="replace").splitlines()
        except OSError:
            lines = []
        matches = [ln for ln in lines if "error" in ln.lower() or "fatal" in ln.lower()]
        last_errs = matches[-5:]
        if last_errs:
            msg += "\n\nLast errors:\n" + "\n".join(last_errs)
        else:
            msg += "\n\nNo error lines found in log."
    else:
        msg += f"\n\nLog file missing/unreadable: {log}"
    return msg


def main(argv: list[str] | None = None) -> int:
    """CLI interface — mimics the old rclone_notify.sh contract so it's a
    drop-in replacement:

        rclone_notify.sh "<JobName>" "<PathToRcloneLog>"

    becomes:

        assistant_desktop_notify.py "<JobName>" "<PathToRcloneLog>"

    Same positional args (job name, log path — both optional, defaulting to
    "unknown" / no log), same critical-by-default urgency, same "Job/Time/
    Last errors" message built from the log, and always exits 0.

    For a plain title/body notification (e.g. from Python callers), import
    and call notify(title, body, urgency=...) directly instead of going
    through this CLI.
    """
    parser = argparse.ArgumentParser(
        description="Send a cross-platform desktop notification (rclone_notify.sh-compatible CLI)."
    )
    parser.add_argument("job", nargs="?", default="unknown", help="Job name (legacy: $1)")
    parser.add_argument("log", nargs="?", default="", help="Path to the job's log file (legacy: $2)")
    parser.add_argument("--title", default="rclone bisync FAILED", help="Notification title")
    parser.add_argument(
        "--body", default=None,
        help="Explicit message body. Overrides the job/log-based legacy message "
             "(for plain title/body notifications, e.g. from other callers).",
    )
    parser.add_argument("--urgency", choices=["low", "normal", "critical"], default="critical")
    parser.add_argument("--log-path", dest="notify_log", type=Path, default=None,
                         help="Override where this tool logs its own actions (not the job log)")
    args = parser.parse_args(argv)

    body = args.body if args.body is not None else build_legacy_message(args.job, args.log)
    notify(args.title, body, urgency=args.urgency, log_path=args.notify_log)
    return 0  # never cascade failures into the caller's exit code


if __name__ == "__main__":
    sys.exit(main())
