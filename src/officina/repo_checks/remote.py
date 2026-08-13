"""Dispatch and collect exact-SHA repository checks through GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from officina.repo_checks.remote_macos_windows import EXPECTED_MATRIX, WINDOWS_RUNNER


WORKFLOW = "python-tests.yml"
SUPPORTED_OSES = {item[0] for item in EXPECTED_MATRIX}
SUPPORTED_TASKS = {
    "combined",
    "native:keyring",
    "native:scheduler",
    "validators",
    "tests:shared",
    "tests:install",
    "tests:browser",
    "tests:docstrings",
    "tests:portability",
    "tests:performance",
}
JOB_NAME = re.compile(r"^(?:test|probe) \(([^,]+), (.+)\)$")
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RemoteError(RuntimeError):
    """A safe, machine-classified remote validation or transport failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RemoteArgumentParser(argparse.ArgumentParser):
    """Convert parser failures into the remote interface's JSON error path."""

    def error(self, _message: str) -> None:
        raise RemoteError("invalid_arguments", "invalid remote arguments")


class GhClient:
    """Execute fixed-shape GitHub CLI argv without a shell."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = Path(cwd).resolve()

    def run(self, arguments: tuple[str, ...]):
        """Return one captured GitHub CLI result with strict UTF-8 decoding.

        Intent
        ------
        Centralize the sole external process boundary used by remote checks.

        Rationale
        ---------
        Captured argv calls make correlation testable and prevent shell
        interpolation of refs, selectors, or paths.

        Pseudocode
        ----------
        - invoke gh plus the supplied immutable arguments in the repository
        - capture stdout and stderr as strict UTF-8 text
        - return the completed process without raising on its exit status

        Wraps
        -----
        subprocess.run -> preprocess: prefix gh and set the repository cwd; postprocess: return the completed process; fixed_arguments: shell false, captured strict UTF-8 text, check false
        """

        return subprocess.run(
            ("gh", *arguments),
            cwd=self.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            shell=False,
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the remote matrix and probe command grammar."""

    parser = RemoteArgumentParser(
        prog="repo_checks.py remote",
        description="Run repository checks through GitHub Actions.",
    )
    commands = parser.add_subparsers(dest="remote_command", required=True)
    matrix = commands.add_parser("matrix", help="Run the complete CI matrix.")
    probe = commands.add_parser("probe", help="Run one selected matrix element.")
    for command in (matrix, probe):
        command.add_argument("--ref", required=True)
        command.add_argument("--expected-sha", required=True)
        destination = command.add_mutually_exclusive_group(required=True)
        destination.add_argument("--output-dir")
        destination.add_argument("--context")
        command.add_argument("--timeout", type=int)
    probe.add_argument("--os", required=True)
    probe.add_argument("--task", required=True)
    selection = probe.add_mutually_exclusive_group(required=True)
    selection.add_argument("--selector", action="append")
    selection.add_argument("--from-report")
    selection.add_argument("--whole-element", action="store_true")
    probe.add_argument("--jobs", type=int)
    probe.add_argument("--profile", choices=("default", "serial"), default="default")
    return parser


def _checked(gh: GhClient, arguments: tuple[str, ...], code: str):
    result = gh.run(arguments)
    if result.returncode != 0:
        raise RemoteError(code, "GitHub CLI operation failed")
    return result


def _validate_arguments(args: argparse.Namespace) -> None:
    if not args.ref or any(ord(character) < 32 for character in args.ref):
        raise RemoteError("invalid_ref", "ref must be non-empty and printable")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.expected_sha):
        raise RemoteError("invalid_expected_sha", "expected SHA must be 40 hexadecimal characters")
    if args.timeout is not None and args.timeout < 1:
        raise RemoteError("invalid_timeout", "timeout must be positive")
    if args.remote_command == "probe":
        if args.os not in SUPPORTED_OSES:
            raise RemoteError("invalid_os", "unsupported probe operating system")
        if args.task not in SUPPORTED_TASKS:
            raise RemoteError("invalid_task", "unsupported probe task")
        if args.task == "combined" and args.os == WINDOWS_RUNNER:
            raise RemoteError(
                "invalid_task",
                "combined is not supported for the requested operating system",
            )
        if args.selector and args.task == "validators":
            raise RemoteError("invalid_selector", "pytest selectors cannot target validators")
        if args.selector and args.task == "combined":
            raise RemoteError("invalid_selector", "combined supports whole-element probes only")
        if args.from_report and args.task == "combined":
            raise RemoteError(
                "invalid_selector",
                "combined report replay cannot preserve component ownership",
            )
        if args.jobs is not None and args.jobs < 1:
            raise RemoteError("invalid_jobs", "jobs must be positive")


def _output_root(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        raise RemoteError("invalid_output_dir", "output directory cannot be a symlink")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate.resolve()


def _context_setup(context_root: Path, repository: str) -> None:
    """Create or validate the stable, non-secret identity of one debug session.

    Intent
    ------
    Bind a reusable local context to exactly one GitHub repository and workflow.

    Rationale
    ---------
    Stable setup avoids reconstruction after an agent or process restart, while
    exact equality and fresh caller-side authentication prevent cached context
    from becoming authority or silently crossing repository boundaries.

    Pseudocode
    ----------
    - set expected setup = schema, repository identity, and workflow
    - if context setup is absent:
      - write a private temporary JSON file
      - atomically hard-link it into place without replacing another creator
    - load the established regular file
    - if its complete value differs from expected setup:
      - raise context mismatch

    Security
    --------
    The setup contains no token, authentication output, remote URL, environment,
    ref, or SHA. Authentication and candidate identity remain live checks.
    """

    destination = context_root / "context.json"
    expected = {
        "schema_version": 1,
        "repository": repository,
        "workflow": WORKFLOW,
    }
    if not destination.exists():
        temporary = context_root / f".context-{secrets.token_hex(8)}.tmp"
        temporary.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RemoteError(
                "invalid_context", "debug context setup is unavailable"
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)
    if destination.is_symlink() or not destination.is_file():
        raise RemoteError("invalid_context", "debug context setup is unavailable")
    try:
        actual = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteError("invalid_context", "debug context setup is invalid") from exc
    if actual != expected:
        raise RemoteError(
            "context_mismatch",
            "debug context belongs to another repository or workflow",
        )


def _repository(gh: GhClient) -> str:
    _checked(gh, ("auth", "status"), "authentication_failed")
    result = _checked(
        gh,
        ("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"),
        "repository_unavailable",
    )
    repository = result.stdout.strip()
    if not REPOSITORY_NAME.fullmatch(repository):
        raise RemoteError("repository_unavailable", "GitHub repository identity is invalid")
    return repository


def _load_replay(path: str, repository: str, os_name: str, task: str) -> list[str]:
    report_path = Path(path)
    if not report_path.is_file() or report_path.is_symlink():
        raise RemoteError("invalid_report", "source report is unavailable")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteError("invalid_report", "source report is not valid JSON") from exc
    if payload.get("schema_version") != 1:
        raise RemoteError("invalid_report", "source report schema is unsupported")
    source_repository = payload.get("repository")
    if source_repository is not None and source_repository != repository:
        raise RemoteError("invalid_report", "source report belongs to another repository")
    matching = [
        element
        for element in payload.get("elements", [])
        if isinstance(element, dict)
        and element.get("os") == os_name
        and element.get("task") in {task, "combined"}
    ]
    if not matching:
        raise RemoteError("invalid_report", "source report has no matching matrix element")
    selectors: list[str] = []
    explicit_whole_task = False
    for element in matching:
        failed_by_task = element.get("failed_by_task")
        if isinstance(failed_by_task, dict):
            raw_selectors = failed_by_task.get(task, [])
        elif element.get("task") == task:
            raw_selectors = element.get("failed_selectors", [])
        else:
            raw_selectors = []
        if not isinstance(raw_selectors, list):
            raise RemoteError("invalid_report", "source report failure scope is invalid")
        for selector in raw_selectors:
            if isinstance(selector, str) and selector not in selectors:
                selectors.append(selector)
        if element.get("task") == task and element.get("failure_scope") == "task":
            explicit_whole_task = True
    if selectors:
        return selectors
    if explicit_whole_task:
        return []
    raise RemoteError("invalid_report", "source report has no classified failures for the task")


def _dispatch_fields(args: argparse.Namespace, request_id: str, selectors: list[str]) -> tuple[str, ...]:
    fields = [
        "--raw-field",
        f"mode={args.remote_command}",
        "--raw-field",
        f"request_id={request_id}",
        "--raw-field",
        f"expected_sha={args.expected_sha.lower()}",
    ]
    if args.remote_command == "probe":
        jobs = 1 if args.profile == "serial" else (args.jobs or 4)
        fields.extend(
            [
                "--raw-field",
                f"os={args.os}",
                "--raw-field",
                f"task={args.task}",
                "--raw-field",
                f"selector={json.dumps(selectors, separators=(',', ':'))}",
                "--raw-field",
                f"jobs={jobs}",
                "--raw-field",
                f"profile={args.profile}",
            ]
        )
    return tuple(fields)


def _correlate(
    gh: GhClient,
    *,
    repository: str,
    ref: str,
    expected_sha: str,
    request_id: str,
    sleep: Callable[[float], None],
) -> dict[str, object]:
    for _attempt in range(15):
        result = _checked(
            gh,
            (
                "run",
                "list",
                "--repo",
                repository,
                "--workflow",
                WORKFLOW,
                "--event",
                "workflow_dispatch",
                "--branch",
                ref,
                "--limit",
                "20",
                "--json",
                "databaseId,headSha,status,conclusion,displayTitle,url,createdAt",
            ),
            "correlation_failed",
        )
        try:
            runs = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RemoteError("correlation_failed", "GitHub run list is invalid") from exc
        matches = [
            run
            for run in runs
            if run.get("headSha", "").casefold() == expected_sha.casefold()
            and request_id in str(run.get("displayTitle", ""))
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RemoteError("correlation_failed", "multiple matching workflow runs found")
        sleep(2)
    raise RemoteError("correlation_failed", "workflow run did not become visible")


def _poll(
    gh: GhClient,
    *,
    repository: str,
    run_id: int,
    expected_sha: str,
    timeout: int,
    sleep: Callable[[float], None],
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        result = _checked(
            gh,
            (
                "run",
                "view",
                str(run_id),
                "--repo",
                repository,
                "--json",
                "status,conclusion,jobs,url,headSha,displayTitle",
            ),
            "poll_failed",
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RemoteError("poll_failed", "GitHub run state is invalid") from exc
        if str(payload.get("headSha", "")).casefold() != expected_sha.casefold():
            raise RemoteError("candidate_sha_mismatch", "workflow run tested another commit")
        if payload.get("status") == "completed":
            return payload
        print(f"remote run {run_id} status={payload.get('status', 'unknown')}", file=sys.stderr)
        sleep(5)
    raise RemoteError("remote_timeout", "workflow run did not complete before timeout")


def _artifact_failures(artifact_root: Path) -> dict[str, dict[str, list[str]]]:
    failures: dict[str, dict[str, set[str]]] = {}
    for report_path in sorted(artifact_root.glob("**/*.json")):
        if report_path.is_symlink():
            continue
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        relative = report_path.relative_to(artifact_root)
        artifact_name = relative.parts[0] if len(relative.parts) > 1 else report_path.parent.name
        for record in payload.get("files", []):
            if not isinstance(record, dict):
                continue
            task_id = record.get("task_id")
            selector = record.get("path")
            failed = record.get("failed", 0)
            if (
                isinstance(task_id, str)
                and isinstance(selector, str)
                and isinstance(failed, int)
                and not isinstance(failed, bool)
                and failed > 0
            ):
                failures.setdefault(artifact_name, {}).setdefault(task_id, set()).add(selector)
    return {
        artifact: {
            task_id: sorted(selectors)
            for task_id, selectors in sorted(by_task.items())
        }
        for artifact, by_task in sorted(failures.items())
    }


def _selectors_for_element(
    failures: dict[str, dict[str, list[str]]],
    *,
    mode: str,
    os_name: str,
    task: str,
) -> dict[str, list[str]]:
    if mode == "probe":
        candidates = [values for key, values in failures.items() if "probe" in key]
    else:
        os_slug = os_name.removesuffix("-latest")
        task_slug = task.replace(":", "-")
        marker = f"{os_slug}-{task_slug}"
        candidates = [values for key, values in failures.items() if marker in key]
    merged: dict[str, set[str]] = {}
    for by_task in candidates:
        for task_id, selectors in by_task.items():
            merged.setdefault(task_id, set()).update(selectors)
    return {
        task_id: sorted(selectors)
        for task_id, selectors in sorted(merged.items())
    }


def _build_report(
    *,
    args: argparse.Namespace,
    repository: str,
    request_id: str,
    run_id: int,
    run: dict[str, object],
    failures: dict[str, dict[str, list[str]]],
    requested_selectors: Sequence[str],
) -> dict[str, object]:
    wanted_prefix = "test" if args.remote_command == "matrix" else "probe"
    elements: list[dict[str, object]] = []
    for job in run.get("jobs", []):
        if not isinstance(job, dict) or not str(job.get("name", "")).startswith(wanted_prefix):
            continue
        match = JOB_NAME.fullmatch(str(job.get("name", "")))
        if match is None:
            continue
        os_name, task = match.groups()
        failed_by_task = _selectors_for_element(
            failures,
            mode=args.remote_command,
            os_name=os_name,
            task=task,
        )
        failed_selectors = sorted(
            {
                selector
                for selectors in failed_by_task.values()
                for selector in selectors
            }
        )
        conclusion = job.get("conclusion")
        elements.append(
            {
                "os": os_name,
                "task": task,
                "conclusion": conclusion,
                "url": job.get("url"),
                "failed_by_task": failed_by_task,
                "failed_selectors": failed_selectors,
                "failure_scope": (
                    "none"
                    if conclusion == "success"
                    else "selectors" if failed_selectors else "task"
                ),
            }
        )
    if args.remote_command == "matrix":
        by_identity = {(item["os"], item["task"]): item for item in elements}
        if set(by_identity) != set(EXPECTED_MATRIX):
            raise RemoteError("incomplete_matrix", "workflow report is missing required matrix elements")
        elements = [by_identity[identity] for identity in EXPECTED_MATRIX]
    elif len(elements) != 1 or (
        elements[0]["os"],
        elements[0]["task"],
    ) != (args.os, args.task):
        raise RemoteError(
            "unexpected_probe_element",
            "workflow report does not match the requested probe element",
        )
    green = bool(elements) and all(item["conclusion"] == "success" for item in elements)
    return {
        "schema_version": 1,
        "mode": args.remote_command,
        "request_id": request_id,
        "repository": repository,
        "workflow": WORKFLOW,
        "ref": args.ref,
        "expected_sha": args.expected_sha.lower(),
        "requested_selectors": list(requested_selectors),
        "run_id": run_id,
        "run_url": run.get("url"),
        "conclusion": "green" if green else "red",
        "overall_green": green if args.remote_command == "matrix" else False,
        "elements": elements,
    }


def _write_report(
    output_root: Path,
    report: dict[str, object],
    *,
    persistent_context: bool,
) -> None:
    """Atomically persist one report in compatibility or immutable-session form.

    A persistent context receives a request-owned directory, so independent
    matrix elements cannot overwrite each other's evidence. Legacy output mode
    retains the established latest-report path for callers outside CI-debug.
    """
    if persistent_context:
        run_root = output_root / "runs" / str(report["request_id"])
        try:
            run_root.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise RemoteError(
                "invalid_context", "current-run report directory is unavailable"
            ) from exc
        destination = run_root / "run-report.json"
    else:
        destination = output_root / "run-report.json"
    temporary = output_root / f".run-report-{report['request_id']}.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def run(args: argparse.Namespace, *, gh: GhClient, sleep: Callable[[float], None]) -> dict[str, object]:
    """Execute one validated dispatch, correlation, poll, and collection cycle."""

    _validate_arguments(args)
    output_root = _output_root(args.context or args.output_dir)
    repository = _repository(gh)
    _checked(
        gh,
        ("workflow", "view", WORKFLOW, "--repo", repository),
        "workflow_unavailable",
    )
    if args.context:
        _context_setup(output_root, repository)
    remote_sha = _checked(
        gh,
        (
            "api",
            f"repos/{repository}/commits/{quote(args.ref, safe='')}",
            "--jq",
            ".sha",
        ),
        "ref_unavailable",
    ).stdout.strip()
    if remote_sha.casefold() != args.expected_sha.casefold():
        raise RemoteError("candidate_sha_mismatch", "remote ref does not match expected SHA")
    selectors: list[str] = []
    if args.remote_command == "probe" and args.selector:
        selectors = args.selector
    if args.remote_command == "probe" and args.from_report:
        selectors = _load_replay(args.from_report, repository, args.os, args.task)
    if args.remote_command == "probe" and args.task == "combined" and selectors:
        raise RemoteError("invalid_selector", "combined supports whole-element probes only")
    request_id = f"ci-{secrets.token_hex(8)}"
    _checked(
        gh,
        (
            "workflow",
            "run",
            WORKFLOW,
            "--repo",
            repository,
            "--ref",
            args.ref,
            *_dispatch_fields(args, request_id, selectors),
        ),
        "dispatch_failed",
    )
    correlated = _correlate(
        gh,
        repository=repository,
        ref=args.ref,
        expected_sha=args.expected_sha,
        request_id=request_id,
        sleep=sleep,
    )
    run_id = int(correlated["databaseId"])
    print(f"remote run {run_id} correlated", file=sys.stderr)
    completed = _poll(
        gh,
        repository=repository,
        run_id=run_id,
        expected_sha=args.expected_sha,
        timeout=args.timeout or (7200 if args.remote_command == "matrix" else 1800),
        sleep=sleep,
    )
    artifact_parent = output_root / "artifacts"
    artifact_root = artifact_parent / request_id
    try:
        artifact_root.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise RemoteError(
            "invalid_output_dir",
            "current-run artifact directory is unavailable",
        ) from exc
    download = gh.run(
        (
            "run",
            "download",
            str(run_id),
            "--repo",
            repository,
            "--dir",
            str(artifact_root),
        )
    )
    if download.returncode != 0:
        print("warning: repository-check artifacts were unavailable", file=sys.stderr)
    report = _build_report(
        args=args,
        repository=repository,
        request_id=request_id,
        run_id=run_id,
        run=completed,
        failures=_artifact_failures(artifact_root),
        requested_selectors=selectors,
    )
    _write_report(output_root, report, persistent_context=bool(args.context))
    return report


def main(
    argv: Sequence[str] | None = None,
    *,
    gh: GhClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run one remote request and emit exactly one machine JSON result."""

    try:
        args = build_parser().parse_args(argv)
        report = run(args, gh=gh or GhClient(Path.cwd()), sleep=sleep)
    except RemoteError as exc:
        print(json.dumps({"schema_version": 1, "error": exc.code}))
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report))
    return 0 if report["conclusion"] == "green" else 1
