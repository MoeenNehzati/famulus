from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from .records import RunRecord, write_record
from .runtime import ManagedSchedule, load_managed_schedule
from .jobs import confined_child, validate_job_name
from .native import load_jobs
from officina.launchers.agent import build_agent_command

ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
TIMEOUT_SECONDS = 3600
TIMEOUT_EXIT_CODE = -1001
SPAWN_FAILURE_EXIT_CODE = -1000
MAX_LOG_BYTES = 5 * 1024 * 1024


def parse_command(command: str, *, platform: str = sys.platform) -> tuple[dict[str, str], list[str]]:
    tokens = shlex.split(command, posix=platform != "win32")
    if platform == "win32":
        tokens = [token[1:-1] if len(token) > 1 and token[0] == token[-1] == '"' else token for token in tokens]
    environment: dict[str, str] = {}
    while tokens and ENV_ASSIGNMENT.fullmatch(tokens[0]):
        key, value = tokens.pop(0).split("=", 1)
        environment[key] = value
    if not tokens:
        raise ValueError("job command did not contain an executable")
    return environment, tokens


def _load_job(schedule: ManagedSchedule, name: str) -> dict[str, object]:
    name = validate_job_name(name)
    for job in load_jobs(schedule):
        if job.get("name") == name:
            return job
    raise ValueError(f"Job not found: {name}")


def _rotate(path: Path) -> None:
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.replace(path.with_name(path.name + ".1"))


def _exact_argv(
    schedule: ManagedSchedule,
    overrides: dict[str, str],
    argv: list[str],
    *,
    selected_backend: str,
) -> list[str]:
    command_name = Path(argv[0]).name.lower()
    if command_name in {"claude", "claude.exe", "codex", "codex.exe"}:
        backend = "claude" if command_name.startswith("claude") else "codex"
        return [str(schedule.backend_executables[backend]), *argv[1:]]
    if command_name in {"invoke-skill", "invoke-skill.exe", "invoke-skill.cmd"}:
        if len(argv) < 2:
            raise ValueError("invoke-skill job command requires a skill name")
        backend = selected_backend
        if backend not in schedule.backend_executables:
            raise ValueError("ASSISTANT_DEFAULT must select claude or codex")
        if schedule.launcher_resources is None:
            raise ValueError("active runtime pointer has no launcher resources")
        skill = argv[1]
        arguments = (
            ["--permission-mode", "bypassPermissions", "-p", f"/{skill}"]
            if backend == "claude"
            else ["exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", f"${skill}"]
        )
        built = build_agent_command(
            agent="background_run", backend=backend,
            resources=schedule.launcher_resources,
            claude_home=Path(schedule.environment["CLAUDE_CONFIG_DIR"]),
            args=[*arguments, *argv[2:]],
        )
        return [str(schedule.backend_executables[backend]), *built[1:]]
    if command_name in {"launch.py", "launch.py.exe"}:
        return [str(schedule.runtime_resolver), *argv[1:]]
    raise ValueError("job executable must select claude, codex, invoke-skill, or the fixed resolver")


def run_job(*, schedule: ManagedSchedule, job_name: str) -> int:
    job_name = validate_job_name(job_name)
    job = _load_job(schedule, job_name)
    overrides, argv = parse_command(str(job["command"]))
    if "ASSISTANT_DEFAULT" in overrides:
        raise ValueError("ASSISTANT_DEFAULT is retired; use the structured backend field")
    selected_backend = job.get("backend", schedule.default_backend)
    if selected_backend not in schedule.backend_executables:
        raise ValueError("structured backend must select claude or codex")
    argv = _exact_argv(
        schedule, overrides, argv, selected_backend=str(selected_backend)
    )
    environment = {**schedule.environment, **overrides}
    environment["FAMULUS_SCHEDULE_DESCRIPTOR"] = str(schedule.descriptor_path)
    directory = confined_child(schedule.log_root, job_name)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_file = directory / "run.log"
    _rotate(log_file)
    started = dt.datetime.now(dt.timezone.utc)
    run_id = uuid.uuid4().hex
    marker = directory / "running.json"
    marker.write_text(json.dumps({"job_name": job_name, "started_at": started.isoformat(), "run_id": run_id}) + "\n", encoding="utf-8")
    reason = ""
    with log_file.open("a", encoding="utf-8") as output:
        output.write("--- RUN START ---\n")
        output.flush()
        try:
            result = subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=environment, check=False, timeout=TIMEOUT_SECONDS)
            exit_code = result.returncode
            if exit_code:
                reason = f"process exit code {exit_code}"
        except subprocess.TimeoutExpired:
            exit_code = TIMEOUT_EXIT_CODE
            reason = f"job exceeded its {TIMEOUT_SECONDS}s timeout and was killed"
        except OSError as exc:
            exit_code = SPAWN_FAILURE_EXIT_CODE
            reason = f"failed to spawn process: {exc}"
        success = exit_code == 0
        finished = dt.datetime.now(dt.timezone.utc)
        write_record(log_root=schedule.log_root, record=RunRecord(job_name=job_name, started_at=started.isoformat(timespec="seconds"), finished_at=finished.isoformat(timespec="seconds"), process_exit_code=exit_code, success=success, reason=reason, run_id=run_id))
        output.write(f"--- RUN END (success={success}) ---\n")
    marker.unlink(missing_ok=True)
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--log-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        schedule = load_managed_schedule(runtime_root=args.runtime_root, descriptor_path=args.descriptor, log_root=args.log_root)
        return run_job(schedule=schedule, job_name=args.job)
    except Exception as exc:
        print(f"recurring executor: {exc}", file=sys.stderr)
        return 1


class Interface(PythonArgvMachineInterface):
    prog = "famulus-recurring-executor"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Interface", "main", "parse_command", "run_job"]
