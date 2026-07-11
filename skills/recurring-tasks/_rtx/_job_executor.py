#!/usr/bin/env python3
"""Execute one recurring task job without invoking a shell."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).parent.parent
REPO_ROOT = SKILL_DIR.parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

LOG_DIR = SKILL_DIR / "logs"
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def _strip_matching_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def parse_command(command: str, *, platform: str = sys.platform) -> tuple[dict[str, str], list[str]]:
    """Parse a job command string into leading environment assignments and argv."""
    tokens = shlex.split(command, posix=(platform != "win32"))
    if platform == "win32":
        tokens = [_strip_matching_quotes(token) for token in tokens]
    env: dict[str, str] = {}
    index = 0
    for token in tokens:
        if not ENV_ASSIGNMENT_RE.fullmatch(token):
            break
        key, value = token.split("=", 1)
        env[key] = value
        index += 1
    argv = tokens[index:]
    if not argv:
        raise ValueError("job command did not contain an executable")
    return env, argv


def load_job(jobs_file: Path, job_name: str) -> dict:
    with jobs_file.open(encoding="utf-8") as f:
        jobs = (yaml.safe_load(f) or {}).get("jobs", [])
    for job in jobs:
        if job.get("name") == job_name:
            return job
    raise ValueError(f"Job not found: {job_name}")


def resolve_executable(argv: list[str], env: dict[str, str], *, platform: str = sys.platform) -> list[str]:
    """Resolve Windows launcher shims such as invoke-skill.bat without a shell."""
    if platform != "win32" or not argv:
        return argv
    resolved = shutil.which(argv[0], path=env.get("PATH"))
    if not resolved:
        return argv
    return [resolved, *argv[1:]]


def run_job(*, jobs_file: Path, job_name: str, log_dir: Path = LOG_DIR) -> int:
    job = load_job(jobs_file, job_name)
    command = str(job["command"]).replace("{skill_dir}", str(SKILL_DIR))
    env_overrides, argv = parse_command(command)

    log_file = log_dir / job_name / "run.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **env_overrides}
    resolved_argv = resolve_executable(argv, env)
    with log_file.open("a", encoding="utf-8") as log:
        result = subprocess.run(
            resolved_argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
    return result.returncode


class Interface(PythonArgvMachineInterface):
    prog = "job_executor.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-file", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        return run_job(jobs_file=args.jobs_file, job_name=args.job)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
