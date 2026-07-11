#!/usr/bin/env python3
"""Create a temporary skill and recurring job, run it through the real backend,
verify output, then restore the original schedule."""
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from argparse import ArgumentParser
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).parent.parent
SCRIPTS = SKILL_DIR / 'scripts'
DEFAULT_JOBS = SKILL_DIR / 'jobs.yaml'
LOG_DIR = SKILL_DIR / 'logs'
AI_ROOT = SKILL_DIR.parent.parent
CODEX_HOME = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
PREFIX = 'ai-'
TIMEOUT_SEC = 360


def load_jobs(path: Path) -> dict:
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault('jobs', [])
    return data


def write_jobs(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def create_temp_skill(skill_root: Path, skill_name: str, marker: str) -> Path:
    skill_path = skill_root / skill_name
    if skill_path.exists():
        raise RuntimeError(f'temp skill path already exists: {skill_path}')
    skill_path.mkdir(parents=True)
    skill_path.joinpath('SKILL.md').write_text(
        "---\n"
        f"name: {skill_name}\n"
        "description: Temporary recurring-tasks selftest skill.\n"
        "---\n\n"
        "Print exactly:\n\n"
        f"{marker}\n"
    )
    return skill_path


def build_temp_job(job_name: str, skill_name: str, backend: str) -> dict:
    uid = os.getuid()
    bus = f'/run/user/{uid}/bus'
    return {
        'name': job_name,
        'description': f'Temporary recurring-tasks selftest via {backend}',
        'command': (
            f'DBUS_SESSION_BUS_ADDRESS=unix:path={bus} '
            f'PATH=$HOME/.local/bin:$PATH '
            f'ASSISTANT_DEFAULT={backend} '
            f'{{skill_dir}}/scripts/run-skill.sh {skill_name}'
        ),
        'schedule': '*/15 * * * *',
        'enabled': True,
    }


def run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=capture,
    )


def ensure_timer_active(job_name: str) -> None:
    timer = f'{PREFIX}{job_name}.timer'
    result = run(['systemctl', '--user', 'is-active', timer], check=False, capture=True)
    if result.returncode != 0 or result.stdout.strip() != 'active':
        raise RuntimeError(f'{timer} is not active: stdout={result.stdout!r} stderr={result.stderr!r}')


def run_service_and_verify(job_name: str, marker: str) -> None:
    service = f'{PREFIX}{job_name}.service'
    log_path = LOG_DIR / job_name / 'run.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    before = log_path.stat().st_size if log_path.exists() else 0

    started = time.time()
    try:
        result = subprocess.run(
            ['systemctl', '--user', 'start', '--wait', service],
            timeout=TIMEOUT_SEC,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
        )
    except subprocess.TimeoutExpired as exc:
        subprocess.run(
            ['systemctl', '--user', 'stop', service],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        raise RuntimeError(f'{service} timed out after {TIMEOUT_SEC}s') from exc

    elapsed = time.time() - started
    print(f'Service exited after {elapsed:.1f}s (exit code {result.returncode})')
    if result.returncode != 0:
        journal = subprocess.run(
            ['journalctl', '--user', '-u', service, '--since', '-10min', '--no-pager'],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        tail = log_path.read_text()[-4000:] if log_path.exists() else ''
        raise RuntimeError(
            f'{service} failed with code {result.returncode}\n'
            f'stdout={result.stdout}\n'
            f'stderr={result.stderr}\n'
            f'log_tail={tail}\n'
            f'journal={journal.stdout}'
        )

    after = log_path.stat().st_size if log_path.exists() else 0
    if after <= before:
        raise RuntimeError(f'{service} succeeded but produced no new log output')

    lines = [line.strip() for line in log_path.read_text().splitlines() if line.strip()]
    if marker not in lines:
        raise RuntimeError(f'expected marker {marker!r} in {log_path}, got tail: {lines[-20:]}')


def cleanup(job_name: str, temp_skill_path: Path | None, keep_artifacts: bool) -> None:
    if not keep_artifacts:
        if temp_skill_path is not None:
            shutil.rmtree(temp_skill_path, ignore_errors=True)
        shutil.rmtree(LOG_DIR / job_name, ignore_errors=True)


def main() -> None:
    p = ArgumentParser()
    p.add_argument('--backend', choices=['claude', 'codex'], required=True)
    p.add_argument('--keep-artifacts', action='store_true')
    args = p.parse_args()

    suffix = uuid.uuid4().hex[:8]
    job_name = f'recurring-selftest-{args.backend}-{suffix}'
    skill_name = job_name
    marker = f'selftest-ok-{args.backend}-{suffix}'

    if args.backend == 'codex':
        skill_root = CODEX_HOME / 'skills'
    else:
        skill_root = AI_ROOT / 'skills'

    temp_skill_path = create_temp_skill(skill_root, skill_name, marker)
    print(f'Created temp skill: {temp_skill_path}')

    baseline = load_jobs(DEFAULT_JOBS)
    temp_jobs = {'jobs': list(baseline['jobs']) + [build_temp_job(job_name, skill_name, args.backend)]}

    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        temp_jobs_path = Path(f.name)
    try:
        write_jobs(temp_jobs_path, temp_jobs)

        print(f'Syncing temp job: {job_name}')
        run(['python3', str(SCRIPTS / '_unit_writer.py'), '--jobs-file', str(temp_jobs_path)])
        ensure_timer_active(job_name)
        run_service_and_verify(job_name, marker)
        print(f'PASS: live {args.backend} selftest succeeded for {job_name}')
    finally:
        try:
            print('Restoring original jobs.yaml schedule...')
            run(['python3', str(SCRIPTS / '_unit_writer.py'), '--jobs-file', str(DEFAULT_JOBS)], check=False)
        finally:
            cleanup(job_name, temp_skill_path, args.keep_artifacts)
            temp_jobs_path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
