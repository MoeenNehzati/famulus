#!/usr/bin/env python3
"""Run every local pre-commit phase and report ordinary failures together."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


_GIT_REPOSITORY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


@dataclass(frozen=True)
class PhaseResult:
    """Record the outcome or configuration state of one pre-commit phase.

    Intent
    ------
    Carry one phase command, status, return code, and elapsed wall time.

    Rationale
    ---------
    A stable record lets the gate report every configured phase uniformly,
    including phases that are absent rather than successful or failed.

    Pseudocode
    ----------
    - set phase_result = phase identity command return code duration and status
    - return phase_result

    Wraps
    -----
    - none
    """

    phase_id: str
    command: list[str]
    returncode: int | None
    wall_seconds: float
    status: str


def _phase_environment() -> dict[str, str]:
    """Build a phase environment without ambient Git repository routing.

    Intent
    ------
    Preserve ordinary process configuration while removing variables that bind
    nested commands to the outer committing repository.

    Rationale
    ---------
    Pre-commit hooks may export Git routing state. Child tests and validators must
    discover repositories from their own working directories to remain isolated.

    Pseudocode
    ----------
    - set environment = process environment copy
    - for variable in Git repository routing names:
      - set environment = environment without variable
    - return environment

    Wraps
    -----
    - none
    """

    environment = os.environ.copy()
    for name in _GIT_REPOSITORY_ENV:
        environment.pop(name, None)
    return environment


def _write_report(
    report_path: Path | None, *, complete: bool, phases: list[PhaseResult]
) -> None:
    """Persist the gate result when the caller requested a report.

    Intent
    ------
    Serialize gate completeness and ordered phase outcomes as readable JSON.

    Rationale
    ---------
    The report distinguishes an exhaustive run from an interrupted run while
    allowing interactive callers to disable report generation with ``None``.

    Pseudocode
    ----------
    - if report_path is none:
      - return
    - set report_payload = completeness plus serialized phase results
    - set report_parent = created parent directory for report_path
    - set report_state = persisted formatted report payload

    Wraps
    -----
    - none
    """
    if report_path is None:
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {"complete": complete, "phases": [asdict(phase) for phase in phases]},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_phase(
    repo_root: Path, phase_id: str, command: list[str]
) -> PhaseResult:
    """Execute one ordinary phase and return its measured outcome.

    Intent
    ------
    Run a phase command from the repository root and capture status and wall time.

    Rationale
    ---------
    Central execution keeps phase accounting consistent. A missing ``gitleaks``
    executable is represented as an ordinary failed phase so the gate can still
    report later phases; other missing executables remain configuration errors.

    Pseudocode
    ----------
    - set started = monotonic current time
    - set phase_environment = process environment without outer Git routing
    - set completed = command executed from repo_root with phase_environment
    - if gitleaks is missing:
      - set phase_result = failed result with return code 127
      - return phase_result
    - set phase_result = command return code elapsed time and derived status
    - return phase_result

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._phase_environment:
      why:
        computes: "Supplies an ambient-isolated environment for the phase subprocess."

    InstantiationsFromRepo
    ----------------------
    .PhaseResult:
      why:
        constructs: "Builds the measured outcome returned for the executed phase."
    """
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=_phase_environment(),
        )
    except FileNotFoundError:
        if command[0] != "gitleaks":
            raise
        print(
            "error: gitleaks not installed; refusing to commit without secret scan.",
            file=sys.stderr,
        )
        return PhaseResult(
            phase_id=phase_id,
            command=command,
            returncode=127,
            wall_seconds=time.perf_counter() - started,
            status="failed",
        )
    return PhaseResult(
        phase_id=phase_id,
        command=command,
        returncode=completed.returncode,
        wall_seconds=time.perf_counter() - started,
        status="passed" if completed.returncode == 0 else "failed",
    )


def _stage(repo_root: Path, paths: list[str]) -> None:
    """Restage generated tracked files or raise when Git cannot add them.

    Intent
    ------
    Add an explicit list of regenerated artifacts to the repository index.

    Rationale
    ---------
    Generation phases intentionally update derived tracked files; explicit paths
    prevent the gate from staging unrelated working-tree changes.

    Pseudocode
    ----------
    - set completed = Git add for the explicit paths in repo_root
    - if completed failed:
      - raise generated-artifact staging error
    - return

    Wraps
    -----
    - none
    """
    completed = subprocess.run(["git", "add", *paths], cwd=repo_root)
    if completed.returncode:
        raise RuntimeError("could not stage generated pre-commit artifacts")


def _profiles_changed(repo_root: Path) -> bool:
    """Return whether settings generation changed the tracked profile table.

    Intent
    ------
    Inspect ``PROFILES.md`` for an unstaged difference after generation.

    Rationale
    ---------
    The gate should restage the generated table only when its content changed,
    while treating Git inspection failures as infrastructure errors.

    Pseudocode
    ----------
    - set completed = Git quiet diff for PROFILES.md in repo_root
    - if completed reports no difference:
      - return false
    - if completed reports a difference:
      - return true
    - raise Git index inspection error

    Wraps
    -----
    - none
    """
    completed = subprocess.run(
        ["git", "diff", "--quiet", "PROFILES.md"], cwd=repo_root
    )
    if completed.returncode == 0:
        return False
    if completed.returncode == 1:
        return True
    raise RuntimeError("could not read the Git index for generated profiles")


def _optional_phase(
    repo_root: Path,
    phases: list[PhaseResult],
    *,
    phase_id: str,
    command: list[str],
    relative_script: str,
) -> PhaseResult | None:
    """Run an optional phase or record that it is not configured.

    Intent
    ------
    Append exactly one outcome for a generator whose script may be absent.

    Rationale
    ---------
    An absent optional generator is configuration state, not a successful run;
    recording it explicitly preserves truthful phase coverage in the report.

    Pseudocode
    ----------
    - if relative_script is absent:
      - set phase_result = not-configured result
      - set phases = phases plus phase_result
      - return none
    - set phase_result = measured command execution
    - set phases = phases plus phase_result
    - return phase_result

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .PhaseResult:
      why:
        constructs: "Builds the explicit not-configured outcome for an absent optional phase."
    ._run_phase:
      why:
        constructs: "Builds the measured outcome for a configured optional phase."
    """
    if not (repo_root / relative_script).is_file():
        phases.append(
            PhaseResult(
                phase_id=phase_id,
                command=command,
                returncode=None,
                wall_seconds=0.0,
                status="not-configured",
            )
        )
        return None
    result = _run_phase(repo_root, phase_id, command)
    phases.append(result)
    return result


def _is_incomplete_group_report(report_path: Path) -> bool:
    """Return whether the child pytest runner stopped before completing groups.

    Intent
    ------
    Read the child report's explicit completeness marker when a report exists.

    Rationale
    ---------
    A missing report can precede child startup and is handled by the phase return
    code, whereas malformed or unreadable reports must fail closed.

    Pseudocode
    ----------
    - set report = JSON decoded from report_path
    - if report_path is absent:
      - return false
    - if report_path is unreadable or malformed:
      - raise group-report error
    - return whether report completeness is explicitly false

    Wraps
    -----
    - none
    """
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("could not read Python group report") from exc
    return report.get("complete") is False


def run_precommit_gate(repo_root: Path, report_path: Path | None) -> int:
    """Run every configured pre-commit phase and accumulate ordinary failures.

    Intent
    ------
    Execute generation, secret scanning, validation, and Python-test phases once
    each, preserving their order and recording whether the run completed.

    Rationale
    ---------
    Continuing after ordinary nonzero results exposes all actionable failures in
    one pass. Interruptions and infrastructure errors remain exceptional because
    their remaining phase results would not be meaningful.

    Pseudocode
    ----------
    - set phases = empty ordered result list
    - if repo_root is invalid:
      - raise repository configuration error
    - set group_report = child report path
    - set phases = phases plus optional generation and mandatory scan outcomes
    - set python_result = exhaustive Python test-group outcome
    - set phases = phases plus python_result
    - if the child report is incomplete:
      - set report_state = persisted incomplete gate state
      - return failure
    - if interruption or infrastructure error occurs:
      - set report_state = persisted incomplete gate state
      - raise original error
    - set report_state = persisted complete gate state
    - return failure when any configured phase failed, otherwise success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_incomplete_group_report:
      why:
        validates: "Checks whether the Python child stopped before reporting every execution group."
    ._profiles_changed:
      why:
        computes: "Determines whether the generated profile table needs restaging."
    ._stage:
      why:
        writes: "Restages only the generated artifacts owned by successful generation phases."
    ._write_report:
      why:
        writes: "Persists complete or incomplete gate state at each terminal boundary."

    InstantiationsFromRepo
    ----------------------
    ._optional_phase:
      why:
        constructs: "Builds each optional generation-phase outcome and appends it to the ordered results."
    ._run_phase:
      why:
        constructs: "Builds the measured outcomes for mandatory gate phases."
    """
    phases: list[PhaseResult] = []
    if not repo_root.is_dir():
        _write_report(report_path, complete=False, phases=phases)
        raise RuntimeError(f"repository root does not exist: {repo_root}")
    if not (repo_root / ".git").exists():
        _write_report(report_path, complete=False, phases=phases)
        raise RuntimeError(f"repository Git metadata is missing: {repo_root}")
    group_report = (
        report_path.with_name(f"{report_path.stem}-python-groups.json")
        if report_path is not None
        else repo_root / "_build" / "precommit-python-groups.json"
    )
    try:
        settings = _optional_phase(
            repo_root,
            phases,
            phase_id="settings-generation",
            command=["bash", "scripts/generate-settings-table.sh"],
            relative_script="scripts/generate-settings-table.sh",
        )
        if settings is not None and settings.returncode == 0 and _profiles_changed(repo_root):
            print("✓ Updated PROFILES.md based on latest config files")
            _stage(repo_root, ["PROFILES.md"])

        documentation = _optional_phase(
            repo_root,
            phases,
            phase_id="documentation-generation",
            command=["python3", "scripts/generate-doc-artifacts.py"],
            relative_script="scripts/generate-doc-artifacts.py",
        )
        if documentation is not None and documentation.returncode == 0:
            _stage(
                repo_root,
                [
                    "docs/skills.md",
                    "docs/user/general.md",
                    "docs/user/research.md",
                    "docs/user/system.md",
                    "docs/contributors/README.md",
                ],
            )

        preview = _optional_phase(
            repo_root,
            phases,
            phase_id="preview-generation",
            command=["python3", "scripts/generate-previews.py", "--target", "readme"],
            relative_script="scripts/generate-previews.py",
        )
        if preview is not None and preview.returncode == 0:
            print("✓ Regenerated local README preview in _build/README-preview.html")

        phases.append(
            _run_phase(
                repo_root,
                "gitleaks",
                ["gitleaks", "protect", "--staged", "--redact"],
            )
        )
        phases.append(
            _run_phase(
                repo_root,
                "validators",
                ["python3", "validators/runner.py"],
            )
        )
        python_tests = _run_phase(
            repo_root,
            "python-tests",
            [
                "python3",
                "scripts/run-python-tests.py",
                "--suite",
                "precommit",
                "--keep-going",
                "--report",
                str(group_report),
            ],
        )
        phases.append(python_tests)
        if _is_incomplete_group_report(group_report):
            _write_report(report_path, complete=False, phases=phases)
            return 1
    except KeyboardInterrupt:
        _write_report(report_path, complete=False, phases=phases)
        raise
    except (OSError, RuntimeError):
        _write_report(report_path, complete=False, phases=phases)
        raise

    _write_report(report_path, complete=True, phases=phases)
    return 1 if any(phase.returncode not in (0, None) for phase in phases) else 0


def main() -> int:
    """Run the complete gate from its repository-owned script location.

    Intent
    ------
    Resolve the repository and default report paths for command-line execution.

    Rationale
    ---------
    Keeping path derivation at the entry point makes the reusable gate function
    explicit about its inputs and independent of the caller's current directory.

    Pseudocode
    ----------
    - set repo_root = parent of the script directory
    - set exit_code = complete gate result with the default report path
    - return exit_code

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .run_precommit_gate:
      why:
        constructs: "Builds the process exit code from the complete repository gate result."
    """
    repo_root = Path(__file__).resolve().parent.parent
    return run_precommit_gate(
        repo_root, repo_root / "_build" / "precommit-gate-report.json"
    )


if __name__ == "__main__":
    raise SystemExit(main())
