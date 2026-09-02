"""Dispatch and collect exact-SHA repository checks through GitHub Actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from urllib.parse import quote

from officina.common.atomic_files import (
    AtomicLockUnavailable,
    AtomicWriteError,
    atomic_create_bytes,
    ensure_private_directory,
    exclusive_file_lock,
    read_regular_file_bytes,
)
from officina.repository.checks.remote_macos_windows import EXPECTED_MATRIX, WINDOWS_RUNNER


WORKFLOW = "python-tests.yml"
WORKFLOW_NAME = "Python Tests"
WORKFLOW_RUN_TITLE_PREFIX = "Python Tests / "
SUPPORTED_OSES = {item[0] for item in EXPECTED_MATRIX}
SUPPORTED_TASKS = {
    "combined",
    "native:keyring",
    "native:scheduler",
    "validators",
    "tests:shared",
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

        try:
            return subprocess.run(
                ("gh", *arguments),
                cwd=self.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                check=False,
                shell=False,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return SimpleNamespace(returncode=124, stdout="", stderr=str(exc))


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


def _correlate_once(
    gh: GhClient,
    *,
    repository: str,
    ref: str,
    expected_sha: str,
    request_id: str,
) -> dict[str, object] | None:
    """Observe at most one run-list snapshot for a durable request identity."""

    result = _checked(
        gh,
        (
            "run", "list", "--repo", repository, "--workflow", WORKFLOW,
            "--event", "workflow_dispatch", "--branch", ref, "--limit", "1000",
            "--json", "databaseId,headSha,status,conclusion,displayTitle,url,createdAt",
        ),
        "correlation_failed",
    )
    try:
        runs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RemoteError("correlation_failed", "GitHub run list is invalid") from exc
    if not isinstance(runs, list) or any(not isinstance(item, dict) for item in runs):
        raise RemoteError("correlation_failed", "GitHub run list is invalid")
    matches = [
        item for item in runs
        if str(item.get("headSha", "")).casefold() == expected_sha.casefold()
        and item.get("displayTitle") == f"{WORKFLOW_RUN_TITLE_PREFIX}{request_id}"
    ]
    if len(matches) > 1:
        raise RemoteError("correlation_failed", "multiple matching workflow runs found")
    return matches[0] if matches else None


def _json_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _read_json(path: Path, *, root: Path) -> dict[str, object] | None:
    try:
        raw = read_regular_file_bytes(path, allowed_root=root)
    except FileNotFoundError:
        return None
    except AtomicWriteError as exc:
        raise RemoteError("invalid_context", "debug request state is unavailable") from exc
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteError("invalid_context", "debug request state is invalid") from exc
    if not isinstance(value, dict):
        raise RemoteError("invalid_context", "debug request state is invalid")
    return value


def _create_json(path: Path, payload: dict[str, object], *, root: Path) -> bool:
    try:
        return atomic_create_bytes(path, _json_bytes(payload), allowed_root=root, mode=0o600)
    except AtomicWriteError as exc:
        raise RemoteError("invalid_context", "debug request state is unavailable") from exc


def _ensure_context_directory(path: Path, *, root: Path) -> None:
    try:
        ensure_private_directory(path, allowed_root=root)
    except AtomicWriteError as exc:
        raise RemoteError("invalid_context", "debug request directory is unavailable") from exc


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RemoteError("invalid_context", "debug request timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RemoteError("invalid_context", "debug request timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise RemoteError("invalid_context", "debug request timestamp is invalid")
    return parsed


def _validate_intent(
    intent: dict[str, object],
    *,
    identity: dict[str, object],
    timeout: int,
) -> None:
    request_id = intent.get("request_id")
    if (
        intent.get("schema_version") != 1
        or intent.get("identity") != identity
        or intent.get("timeout_seconds") != timeout
        or not isinstance(request_id, str)
        or re.fullmatch(r"ci-[0-9a-f]{16}", request_id) is None
    ):
        raise RemoteError("invalid_context", "debug request intent is invalid")
    created = _timestamp(intent.get("created_at"))
    deadline = _timestamp(intent.get("deadline_at"))
    if deadline != created + timedelta(seconds=timeout):
        raise RemoteError("invalid_context", "debug request deadline is invalid")


def _validate_correlation(
    correlation: dict[str, object],
    *,
    intent: dict[str, object],
) -> None:
    run_id = correlation.get("run_id")
    if (
        correlation.get("schema_version") != 1
        or correlation.get("request_id") != intent["request_id"]
        or not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id < 1
        or not isinstance(correlation.get("run_url"), str)
    ):
        raise RemoteError("invalid_context", "workflow correlation receipt is invalid")


def _validate_terminal(
    terminal: dict[str, object],
    *,
    intent: dict[str, object],
    request_key: str,
    correlation: dict[str, object] | None,
) -> None:
    state = terminal.get("state")
    if (
        state not in {"completed", "timed_out", "blocked"}
        or terminal.get("request_id") != intent["request_id"]
        or terminal.get("request_key") != request_key
        or not isinstance(terminal.get("overall_green"), bool)
    ):
        raise RemoteError("invalid_context", "terminal request receipt is invalid")
    if state == "completed":
        identity = intent["identity"]
        assert isinstance(identity, dict)
        if (
            terminal.get("schema_version") != 1
            or terminal.get("repository") != identity["repository"]
            or terminal.get("workflow") != identity["workflow"]
            or terminal.get("ref") != identity["ref"]
            or terminal.get("expected_sha") != identity["expected_sha"]
            or terminal.get("mode") != "matrix"
            or terminal.get("workflow_conclusion") not in {
                "success", "failure", "cancelled", "timed_out", "action_required",
                "neutral", "skipped", "stale", "startup_failure",
            }
            or terminal.get("conclusion") not in {"green", "red"}
            or terminal.get("overall_green") != (terminal.get("conclusion") == "green")
            or not isinstance(terminal.get("run_id"), int)
            or isinstance(terminal.get("run_id"), bool)
            or terminal.get("run_id", 0) < 1
            or not isinstance(terminal.get("run_url"), str)
            or not terminal.get("run_url")
            or terminal.get("requested_selectors") != []
            or correlation is None
            or terminal.get("run_id") != correlation.get("run_id")
            or terminal.get("run_url") != correlation.get("run_url")
        ):
            raise RemoteError("invalid_context", "terminal matrix report is invalid")
        elements = terminal.get("elements")
        if not isinstance(elements, list) or len(elements) != len(EXPECTED_MATRIX):
            raise RemoteError("invalid_context", "terminal matrix report is invalid")
        identities = []
        for element in elements:
            if not isinstance(element, dict) or element.get("conclusion") not in {
                "success", "failure", "cancelled", "timed_out", "skipped",
                "action_required", "neutral", "stale", "startup_failure",
            }:
                raise RemoteError("invalid_context", "terminal matrix report is invalid")
            failed_by_task = element.get("failed_by_task")
            failed_selectors = element.get("failed_selectors")
            if (
                not isinstance(element.get("url"), str)
                or not element.get("url")
                or not isinstance(failed_by_task, dict)
                or any(
                    not isinstance(task, str)
                    or not isinstance(selectors, list)
                    or any(not isinstance(selector, str) for selector in selectors)
                    or selectors != sorted(set(selectors))
                    for task, selectors in failed_by_task.items()
                )
                or not isinstance(failed_selectors, list)
                or any(not isinstance(selector, str) for selector in failed_selectors)
                or failed_selectors != sorted(set(failed_selectors))
            ):
                raise RemoteError("invalid_context", "terminal matrix report is invalid")
            merged_selectors = sorted({
                selector
                for selectors in failed_by_task.values()
                for selector in selectors
            })
            if merged_selectors != failed_selectors:
                raise RemoteError("invalid_context", "terminal matrix report is invalid")
            if element.get("conclusion") == "success" and (
                failed_by_task or failed_selectors
            ):
                raise RemoteError("invalid_context", "terminal matrix report is invalid")
            expected_scope = (
                "none"
                if element.get("conclusion") == "success"
                else "selectors" if failed_selectors else "task"
            )
            if element.get("failure_scope") != expected_scope:
                raise RemoteError("invalid_context", "terminal matrix report is invalid")
            identities.append((element.get("os"), element.get("task")))
        if identities != list(EXPECTED_MATRIX):
            raise RemoteError("invalid_context", "terminal matrix report is invalid")
        all_green = all(element.get("conclusion") == "success" for element in elements)
        expected_green = all_green and terminal.get("workflow_conclusion") == "success"
        if terminal.get("overall_green") != expected_green:
            raise RemoteError("invalid_context", "terminal matrix report is invalid")
    else:
        run_id = terminal.get("run_id")
        if (
            terminal.get("schema_version") != 2
            or terminal.get("overall_green") is not False
            or terminal.get("deadline_at") != intent["deadline_at"]
            or not (
                run_id is None
                or (
                    isinstance(run_id, int)
                    and not isinstance(run_id, bool)
                    and run_id > 0
                )
            )
        ):
            raise RemoteError("invalid_context", "terminal request receipt is invalid")
        expected_run_id = correlation.get("run_id") if correlation is not None else None
        if run_id != expected_run_id:
            raise RemoteError("invalid_context", "terminal request receipt is invalid")
        if state == "blocked" and (
            terminal.get("reason") != "run_unavailable_at_deadline"
            or correlation is None
        ):
            raise RemoteError("invalid_context", "terminal request receipt is invalid")


def _request_identity(args: argparse.Namespace, repository: str) -> tuple[str, dict[str, object]]:
    identity: dict[str, object] = {
        "repository": repository,
        "workflow": WORKFLOW,
        "mode": "matrix",
        "ref": args.ref,
        "expected_sha": args.expected_sha.lower(),
    }
    return hashlib.sha256(_json_bytes(identity)).hexdigest(), identity


def _pending(
    intent: dict[str, object],
    request_key: str,
    correlation: dict[str, object] | None,
) -> dict[str, object]:
    identity = intent["identity"]
    assert isinstance(identity, dict)
    return {
        "schema_version": 2,
        "state": "pending",
        "overall_green": False,
        "request_key": request_key,
        "request_id": intent["request_id"],
        "ref": identity["ref"],
        "expected_sha": identity["expected_sha"],
        "deadline_at": intent["deadline_at"],
        "run_id": correlation.get("run_id") if correlation else None,
        "run_url": correlation.get("run_url") if correlation else None,
    }


@contextmanager
def _bounded_request_lock(path: Path, *, root: Path) -> Iterator[None]:
    """Acquire one request lock without ever waiting behind the MCP deadline."""

    deadline = time.monotonic() + 5
    while True:
        try:
            with exclusive_file_lock(path, allowed_root=root, blocking=False):
                yield
                return
        except AtomicLockUnavailable as exc:
            if time.monotonic() >= deadline:
                raise RemoteError("request_busy", "debug request is being advanced elsewhere") from exc
            time.sleep(0.05)
        except AtomicWriteError as exc:
            raise RemoteError("invalid_context", "debug request lock is unavailable") from exc


def _run_context_matrix(
    args: argparse.Namespace,
    *,
    gh: GhClient,
    repository: str,
    context_root: Path,
) -> dict[str, object]:
    request_key, _identity = _request_identity(args, repository)
    request_root = context_root / "requests" / request_key
    _ensure_context_directory(request_root, root=context_root)
    try:
        with _bounded_request_lock(request_root / ".lock", root=context_root):
            return _advance_context_matrix_locked(
                args,
                gh=gh,
                repository=repository,
                context_root=context_root,
            )
    except RemoteError as exc:
        if exc.code != "request_busy":
            raise
        return {
            "schema_version": 2,
            "state": "pending",
            "reason": "request_busy",
            "overall_green": False,
            "request_key": request_key,
            "ref": args.ref,
            "expected_sha": args.expected_sha.lower(),
        }


def _advance_context_matrix_locked(
    args: argparse.Namespace,
    *,
    gh: GhClient,
    repository: str,
    context_root: Path,
) -> dict[str, object]:
    """Advance one durable exact-SHA matrix request by one remote observation."""

    request_key, identity = _request_identity(args, repository)
    request_root = context_root / "requests" / request_key
    _ensure_context_directory(request_root, root=context_root)
    intent_path = request_root / "intent.json"
    timeout = args.timeout or 7200
    intent = _read_json(intent_path, root=context_root)
    if intent is None:
        created = datetime.now(UTC)
        proposed = {
            "schema_version": 1,
            "request_id": f"ci-{secrets.token_hex(8)}",
            "identity": identity,
            "timeout_seconds": timeout,
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "deadline_at": (created + timedelta(seconds=timeout)).isoformat().replace("+00:00", "Z"),
        }
        _create_json(intent_path, proposed, root=context_root)
        intent = _read_json(intent_path, root=context_root)
    if intent is None or intent.get("identity") != identity:
        raise RemoteError("context_mismatch", "debug request identity does not match")
    if intent.get("timeout_seconds") != timeout:
        raise RemoteError("context_timeout_mismatch", "debug request timeout is immutable")
    _validate_intent(intent, identity=identity, timeout=timeout)
    correlation_path = request_root / "correlation.json"
    correlation = _read_json(correlation_path, root=context_root)
    if correlation is not None:
        _validate_correlation(correlation, intent=intent)
    terminal = _read_json(request_root / "terminal.json", root=context_root)
    if terminal is not None:
        _validate_terminal(
            terminal,
            intent=intent,
            request_key=request_key,
            correlation=correlation,
        )
        return terminal
    dispatch_path = request_root / "dispatch-attempted.json"
    dispatch = _read_json(dispatch_path, root=context_root)
    if dispatch is not None and dispatch != {
        "schema_version": 1,
        "request_id": intent["request_id"],
    }:
        raise RemoteError("invalid_context", "dispatch receipt is invalid")
    won_dispatch = dispatch is None and _create_json(
        dispatch_path,
        {"schema_version": 1, "request_id": intent["request_id"]},
        root=context_root,
    )

    if won_dispatch:
        _checked(
            gh,
            (
                "workflow", "run", WORKFLOW, "--repo", repository, "--ref", args.ref,
                *_dispatch_fields(args, str(intent["request_id"]), []),
            ),
            "dispatch_uncertain",
        )

    if correlation is None:
        observed = _correlate_once(
            gh,
            repository=repository,
            ref=args.ref,
            expected_sha=args.expected_sha,
            request_id=str(intent["request_id"]),
        )
        if observed is None:
            deadline = _timestamp(intent["deadline_at"])
            if datetime.now(UTC) >= deadline:
                timeout_report = {
                    "schema_version": 2,
                    "state": "timed_out",
                    "overall_green": False,
                    "request_key": request_key,
                    "request_id": intent["request_id"],
                    "run_id": None,
                    "deadline_at": intent["deadline_at"],
                }
                _create_json(request_root / "terminal.json", timeout_report, root=context_root)
                return _read_json(request_root / "terminal.json", root=context_root) or timeout_report
            return _pending(intent, request_key, None)
        proposed_correlation = {
            "schema_version": 1,
            "run_id": observed.get("databaseId"),
            "run_url": observed.get("url"),
            "request_id": intent["request_id"],
        }
        if (
            not isinstance(proposed_correlation["run_id"], int)
            or isinstance(proposed_correlation["run_id"], bool)
            or proposed_correlation["run_id"] < 1
            or not isinstance(proposed_correlation["run_url"], str)
        ):
            raise RemoteError("correlation_failed", "matching workflow run identity is invalid")
        _create_json(correlation_path, proposed_correlation, root=context_root)
        correlation = _read_json(correlation_path, root=context_root)
        if correlation != proposed_correlation:
            raise RemoteError("context_conflict", "workflow correlation conflicts with persisted evidence")
        _validate_correlation(correlation, intent=intent)
        return _pending(intent, request_key, correlation)

    _validate_correlation(correlation, intent=intent)

    deadline = _timestamp(intent["deadline_at"])
    try:
        result = _checked(
            gh,
            (
                "run", "view", str(correlation["run_id"]), "--repo", repository,
                "--json", "databaseId,status,conclusion,jobs,url,headBranch,headSha,displayTitle,event,workflowName,updatedAt",
            ),
            "poll_failed",
        )
    except RemoteError as exc:
        if exc.code != "poll_failed" or datetime.now(UTC) < deadline:
            raise
        blocked = {
            "schema_version": 2,
            "state": "blocked",
            "reason": "run_unavailable_at_deadline",
            "overall_green": False,
            "request_key": request_key,
            "request_id": intent["request_id"],
            "run_id": correlation["run_id"],
            "deadline_at": intent["deadline_at"],
        }
        _create_json(request_root / "terminal.json", blocked, root=context_root)
        return _read_json(request_root / "terminal.json", root=context_root) or blocked
    try:
        observed_run = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RemoteError("poll_failed", "GitHub run state is invalid") from exc
    if not isinstance(observed_run, dict):
        raise RemoteError("poll_failed", "GitHub run state is invalid")
    observations_root = request_root / "observations"
    _ensure_context_directory(observations_root, root=context_root)
    _create_json(
        observations_root / f"{secrets.token_hex(8)}.json",
        {
            "schema_version": 1,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "run_id": observed_run.get("databaseId"),
            "status": observed_run.get("status"),
            "conclusion": observed_run.get("conclusion"),
            "updated_at": observed_run.get("updatedAt"),
        },
        root=context_root,
    )
    if str(observed_run.get("headSha", "")).casefold() != args.expected_sha.casefold():
        raise RemoteError("candidate_sha_mismatch", "workflow run tested another commit")
    if observed_run.get("displayTitle") != f"{WORKFLOW_RUN_TITLE_PREFIX}{intent['request_id']}":
        raise RemoteError("correlation_failed", "workflow run title does not match request identity")
    if observed_run.get("databaseId") != correlation["run_id"]:
        raise RemoteError("correlation_failed", "workflow run ID does not match persisted correlation")
    if observed_run.get("event") != "workflow_dispatch":
        raise RemoteError("correlation_failed", "workflow run event does not match dispatch")
    if observed_run.get("workflowName") != WORKFLOW_NAME:
        raise RemoteError("correlation_failed", "workflow run identity does not match request")
    if observed_run.get("headBranch") != args.ref:
        raise RemoteError("correlation_failed", "workflow run ref does not match request identity")
    status = observed_run.get("status")
    if status != "completed":
        if status not in {"queued", "in_progress", "waiting", "pending", "requested"}:
            raise RemoteError("poll_failed", "workflow run status is invalid")
        if datetime.now(UTC) >= deadline:
            timeout_report = {
                "schema_version": 2,
                "state": "timed_out",
                "overall_green": False,
                "request_key": request_key,
                "request_id": intent["request_id"],
                "run_id": correlation["run_id"],
                "deadline_at": intent["deadline_at"],
            }
            _create_json(request_root / "terminal.json", timeout_report, root=context_root)
            return _read_json(request_root / "terminal.json", root=context_root) or timeout_report
        return _pending(intent, request_key, correlation)
    try:
        completed_at = datetime.fromisoformat(str(observed_run["updatedAt"]).replace("Z", "+00:00"))
        if completed_at.tzinfo is None:
            raise ValueError("timestamp has no timezone")
    except (KeyError, TypeError, ValueError) as exc:
        raise RemoteError("poll_failed", "workflow completion timestamp is invalid") from exc
    if completed_at > deadline:
        timeout_report = {
            "schema_version": 2,
            "state": "timed_out",
            "overall_green": False,
            "request_key": request_key,
            "request_id": intent["request_id"],
            "run_id": correlation["run_id"],
            "deadline_at": intent["deadline_at"],
        }
        _create_json(request_root / "terminal.json", timeout_report, root=context_root)
        return _read_json(request_root / "terminal.json", root=context_root) or timeout_report

    artifact_root = request_root / "artifacts" / secrets.token_hex(8)
    _ensure_context_directory(artifact_root, root=context_root)
    download = gh.run((
        "run", "download", str(correlation["run_id"]), "--repo", repository,
        "--dir", str(artifact_root),
    ))
    if download.returncode != 0:
        print("warning: repository-check artifacts were unavailable", file=sys.stderr)
    report = _build_report(
        args=args,
        repository=repository,
        request_id=str(intent["request_id"]),
        run_id=int(correlation["run_id"]),
        run=observed_run,
        failures=_artifact_failures(artifact_root),
        requested_selectors=[],
    )
    report["state"] = "completed"
    report["request_key"] = request_key
    _validate_terminal(
        report,
        intent=intent,
        request_key=request_key,
        correlation=correlation,
    )
    _create_json(request_root / "terminal.json", report, root=context_root)
    terminal = _read_json(request_root / "terminal.json", root=context_root)
    if terminal != report:
        raise RemoteError("context_conflict", "terminal report conflicts with persisted evidence")
    _create_json(request_root / "run-report.json", terminal, root=context_root)
    persisted = _read_json(request_root / "run-report.json", root=context_root)
    if persisted != terminal:
        raise RemoteError("context_conflict", "terminal report conflicts with persisted evidence")
    return terminal


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
        if (
            len(elements) != len(EXPECTED_MATRIX)
            or set(by_identity) != set(EXPECTED_MATRIX)
        ):
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
    green = (
        bool(elements)
        and all(item["conclusion"] == "success" for item in elements)
        and run.get("conclusion") == "success"
    )
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
        "workflow_conclusion": run.get("conclusion"),
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
    if args.remote_command == "matrix" and args.context:
        return _run_context_matrix(
            args,
            gh=gh,
            repository=repository,
            context_root=output_root,
        )
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
    if report.get("state") == "pending":
        return 0
    if report.get("state") == "timed_out":
        return 2
    if report.get("state") == "blocked":
        return 2
    return 0 if report["conclusion"] == "green" else 1
