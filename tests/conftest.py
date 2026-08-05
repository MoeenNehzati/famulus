"""Shared test scaffolding for the top-level `tests/` suite.

Currently holds the fake-`uv`-subprocess helper used by both
`test_officina_managed_runtime.py` (unit coverage of
`build_candidate_release`'s internal call shapes/ordering) and
`test_install_lifecycle.py` (acceptance-level coverage of
`build_candidate_release` + `runtime_pointer` composing together): both
files need to simulate real `uv venv`/`uv pip install`/`uv python dir`
behavior without a real `uv` binary or network access, and previously each
kept its own near-identical copy of this fake.
"""
from __future__ import annotations

from pathlib import Path


class FakeCompletedProcess:
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

    Mimics real `uv` behavior: `uv venv` creates the interpreter on disk as
    a side effect, `uv pip install` does not (it requires the interpreter
    to already exist at `--python`), and `uv python dir` reports a trusted
    interpreter-store directory. Records every invoked argv (as a list, so
    later mutation of `cmd` by the caller can't retroactively change what
    was recorded) in `calls` so tests can assert on exactly what was run.

    This is what caught the original `build_candidate_release` bug, where
    an earlier fake only created `python_bin` during the (mocked)
    pip-install call -- real `uv pip install` never does that, so the real
    happy path always failed.

    ``windows=True`` makes the simulated `uv venv` create the interpreter
    at ``Scripts/python.exe`` instead of ``bin/python``, matching real
    `uv`'s Windows venv layout -- used to test the Windows branch of
    `managed_runtime._venv_python_bin` end-to-end through
    `build_candidate_release` without a real Windows host.
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
        elif cmd[1:3] == ["python", "dir"]:
            return FakeCompletedProcess(stdout=str(trusted_python_dir) + "\n")
        elif cmd[1] == "build":
            artifact_dir = Path(cmd[cmd.index("--out-dir") + 1])
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "famulus_officina-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        return FakeCompletedProcess()

    return fake_run
