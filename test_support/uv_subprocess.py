"""Fakes for managed-runtime tests that exercise `uv` subprocess calls."""
from __future__ import annotations

import json
from pathlib import Path


class FakeCompletedProcess:
    """Small completed-process stand-in with the fields callers inspect."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str | bytes = "",
        stderr: str | bytes = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def fake_uv_subprocess_run(calls: list, *, trusted_python_dir: Path, windows: bool = False):
    """Build a fake `subprocess.run` standing in for `uv`.

    It preserves the side effects that the managed-runtime contract relies on:
    ``uv venv`` creates the selected interpreter, ``uv pip install`` requires
    that interpreter already exist, and ``uv python dir`` reports the trusted
    interpreter store. Each received command is copied into ``calls`` before
    returning so assertions cannot be changed by later mutation of the
    caller's argv list.

    ``windows=True`` selects the real Windows venv layout
    (``Scripts/python.exe``); the default models POSIX ``bin/python``. The
    fake deliberately creates the venv only for the ``venv`` command, which
    prevents tests from accidentally treating dependency installation as venv
    provisioning.
    """

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "git":
            repo_root = Path(cmd[cmd.index("-C") + 1]).resolve()
            return FakeCompletedProcess(
                stdout=f"{repo_root}\n{'a' * 40}\n".encode("utf-8")
            )
        if cmd[1] == "venv":
            venv_dir = Path(cmd[-1])
            if windows:
                (venv_dir / "Scripts").mkdir(parents=True, exist_ok=True)
                (venv_dir / "Scripts" / "python.exe").write_text("")
            else:
                (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
                (venv_dir / "bin" / "python").write_text("#!/bin/sh\n")
        elif len(cmd) >= 4 and cmd[1:3] == ["-I", "-c"] and "platform.python_version" in cmd[3]:
            return FakeCompletedProcess(
                stdout=json.dumps(
                    {
                        "implementation": "cpython",
                        "version": "3.11.15",
                        "build": ["main", "Aug 13 2026 00:00:00"],
                        "compiler": "GCC 13.3.0",
                        "platform": "Linux-6.0-x86_64",
                        "cache_tag": "cpython-311",
                        "executable": cmd[0],
                    }
                )
                + "\n"
            )
        elif cmd[1:3] == ["python", "dir"]:
            return FakeCompletedProcess(stdout=str(trusted_python_dir) + "\n")
        elif cmd[1] == "build":
            artifact_dir = Path(cmd[cmd.index("--out-dir") + 1])
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "famulus_officina-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        return FakeCompletedProcess()

    return fake_run
