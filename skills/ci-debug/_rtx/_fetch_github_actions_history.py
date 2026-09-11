"""Collect bounded GitHub Actions history into a private immutable snapshot."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._ci_history import HistoryError, build_episodes, canonical_json_bytes, extract_pytest_nodes, gap, publish_tree, require_outside, reserve_private_root, sha256_bytes, utc_timestamp
except ImportError:
    from _ci_history import HistoryError, build_episodes, canonical_json_bytes, extract_pytest_nodes, gap, publish_tree, require_outside, reserve_private_root, sha256_bytes, utc_timestamp


API_HEADER = "X-GitHub-Api-Version: 2022-11-28"
TRANSIENT_HTTP = {429, 500, 502, 503, 504}
MAX_REQUESTS = 5000
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_ENTRY = 64 * 1024 * 1024
MAX_DOWNLOAD = 512 * 1024 * 1024
MAX_RETAINED = 1024 * 1024 * 1024
MAX_ENTRIES = 10_000
HTTP_CODE = re.compile(r"HTTP\s+(\d{3})", re.IGNORECASE)


class Budget:
    """Track shared request, retry, time, download, and retention limits.

    Intent
    ------
    Enforce one bounded resource envelope across concurrent collection tasks.

    Rationale
    ---------
    A shared lock makes limit accounting race-safe.

    Pseudocode
    ----------
    - set operation = `initialize deadline, lock, and resource counters`

    Wraps
    -----
    - none
    """

    def __init__(self, timeout: int) -> None:
        """Initialize counters under a monotonic deadline.

        Intent
        ------
        Bind resource accounting to one timeout window.

        Rationale
        ---------
        Concurrent requests require shared lifetime state.

        Pseudocode
        ----------
        - set operation = `compute deadline from timeout`
        - set operation = `initialize lock and counters`

        Wraps
        -----
        - none
        """
        self.deadline = time.monotonic() + timeout
        self.lock = threading.Lock()
        self.requests = 0
        self.retries = 0
        self.downloaded = 0
        self.retained = 0

    def reserve(self) -> float:
        """Reserve one request and return its bounded timeout.

        Intent
        ------
        Admit an API call only while request and time budgets remain.

        Rationale
        ---------
        Per-call timeouts cannot exceed the total deadline.

        Pseudocode
        ----------
        - set operation = `reject exhausted request or time budget`
        - set operation = `increment request count`
        - return capped remaining seconds

        Wraps
        -----
        - none
        """
        with self.lock:
            if self.requests >= MAX_REQUESTS:
                raise HistoryError("request_cap", "GitHub request cap reached")
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise HistoryError("total_deadline", "collection deadline reached")
            self.requests += 1
            return min(30.0, remaining)

    def operation_timeout(self) -> float:
        """Return a bounded timeout for one non-API operation.

        Intent
        ------
        Keep local Git work within the collector's total deadline.

        Rationale
        ---------
        Network and local subprocesses share the same bounded transaction.

        Pseudocode
        ----------
        - set operation = `compute remaining deadline seconds`
        - set operation = `reject exhausted budget`
        - return capped remaining seconds

        Wraps
        -----
        - none
        """
        with self.lock:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise HistoryError("total_deadline", "collection deadline reached")
            return min(30.0, remaining)

    def add_retry(self) -> None:
        """Record one admitted retry.

        Intent
        ------
        Preserve retry usage in snapshot metadata.

        Rationale
        ---------
        Retry counts reveal transient API trouble in an otherwise successful collection.

        Pseudocode
        ----------
        - set operation = `increment retry count under lock`

        Wraps
        -----
        - none
        """
        with self.lock:
            self.retries += 1

    def add_download(self, amount: int) -> None:
        """Add downloaded bytes while enforcing the global cap.

        Intent
        ------
        Bound API response bytes across worker threads.

        Rationale
        ---------
        Many small responses can collectively exhaust resources.

        Pseudocode
        ----------
        - set operation = `reject addition beyond download cap`
        - set operation = `increment downloaded bytes under lock`

        Wraps
        -----
        - none
        """
        with self.lock:
            if self.downloaded + amount > MAX_DOWNLOAD:
                raise HistoryError("download_cap", "collection download cap reached")
            self.downloaded += amount

    def add_retained(self, amount: int) -> None:
        """Add retained log bytes while enforcing the global cap.

        Intent
        ------
        Bound extracted evidence stored in the snapshot.

        Rationale
        ---------
        Decompressed archives can exceed download size substantially.

        Pseudocode
        ----------
        - set operation = `reject addition beyond retention cap`
        - set operation = `increment retained bytes under lock`

        Wraps
        -----
        - none
        """
        with self.lock:
            if self.retained + amount > MAX_RETAINED:
                raise HistoryError("retained_cap", "collection retained-data cap reached")
            self.retained += amount


class GhClient:
    """Run fixed shell-free GitHub API calls under one bounded budget.

    Intent
    ------
    Provide JSON and binary reads through a constrained GitHub CLI boundary.

    Rationale
    ---------
    Centralized request construction keeps retry and byte accounting consistent.

    Pseudocode
    ----------
    - set operation = `retain repository root and shared budget`
    - set operation = `execute fixed GitHub API commands for requested endpoints`

    Wraps
    -----
    - none
    """

    def __init__(self, repo_root: Path, budget: Budget) -> None:
        """Bind a GitHub client to one repository and budget.

        Intent
        ------
        Retain the execution directory and shared accounting.

        Rationale
        ---------
        Every request must use the same authenticated repository context.

        Pseudocode
        ----------
        - set client_context = `repository root and shared budget`

        Wraps
        -----
        - none
        """
        self.repo_root = repo_root
        self.budget = budget

    def _call(self, endpoint: str, fields: tuple[tuple[str, str], ...] = ()) -> bytes:
        """Execute one bounded GitHub API GET with limited retries.

        Intent
        ------
        Return response bytes or raise a stable transport classification.

        Rationale
        ---------
        Only transient server and rate-limit responses warrant backoff.

        Pseudocode
        ----------
        - set operation = `construct fixed GitHub API arguments`
        - set operation = `execute up to three budgeted attempts`
        - return and account successful bytes
        - set operation = `retry transient failures or raise classified error`

        Wraps
        -----
        - none
        """
        arguments = ["gh", "api", "--method", "GET", "--include", "-H", API_HEADER, endpoint]
        for key, value in fields:
            arguments.extend(("--raw-field", f"{key}={value}"))
        for attempt in range(3):
            timeout = self.budget.reserve()
            try:
                result = subprocess.run(
                    arguments,
                    cwd=self.repo_root,
                    capture_output=True,
                    check=False,
                    shell=False,
                    timeout=timeout,
                )
            except FileNotFoundError as exc:
                raise HistoryError("missing_gh", "GitHub CLI is unavailable") from exc
            except subprocess.TimeoutExpired as exc:
                if attempt >= 2:
                    raise HistoryError("request_timeout", "GitHub API request timed out") from exc
                self.budget.add_retry()
                remaining = self.budget.deadline - time.monotonic()
                delay = min(float(2**attempt), 30.0)
                if delay >= remaining:
                    raise HistoryError("request_timeout", "retry exceeds collection deadline") from exc
                time.sleep(delay)
                continue
            response = result.stdout
            header_text = ""
            if response.startswith(b"HTTP/"):
                separator = b"\r\n\r\n" if b"\r\n\r\n" in response else b"\n\n"
                header_bytes, separator_found, body = response.partition(separator)
                if separator_found:
                    header_text = header_bytes.decode("ascii", errors="replace")
                    response = body
            header_status = re.search(r"^HTTP/\S+\s+(\d{3})", header_text, re.MULTILINE)
            header_fields = {
                key.casefold(): value.strip()
                for line in header_text.splitlines()[1:]
                if ":" in line
                for key, value in [line.split(":", 1)]
            }
            if result.returncode == 0:
                self.budget.add_download(len(response))
                return response
            message = result.stderr.decode("utf-8", errors="replace")
            match = HTTP_CODE.search(message)
            status = int(header_status.group(1)) if header_status else None
            stderr_status = int(match.group(1)) if match else None
            if status is None:
                status = stderr_status
            rate_limited = status == 403 and (
                header_fields.get("x-ratelimit-remaining") == "0"
                or "retry-after" in header_fields
            )
            lowered_message = message.casefold()
            policy_failure = any(term in lowered_message for term in (
                "operation not permitted", "permission denied", "sandbox",
            ))
            transient_transport = status is None and not policy_failure and any(
                term in lowered_message for term in (
                    "network unavailable", "network is unreachable",
                    "connection reset", "connection refused", "connection timed out",
                    "could not resolve host", "temporary failure in name resolution",
                    "tls handshake timeout", "unexpected eof",
                )
            )
            if attempt < 2 and (status in TRANSIENT_HTTP or rate_limited or transient_transport):
                self.budget.add_retry()
                remaining = self.budget.deadline - time.monotonic()
                retry_after = header_fields.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else min(float(2**attempt), 30.0)
                if delay > 30.0:
                    code = "rate_limit" if status == 429 or rate_limited else "transport_failure"
                    raise HistoryError(code, "Retry-After exceeds bounded backoff")
                if delay >= remaining:
                    code = "rate_limit" if status == 429 or rate_limited else "transport_failure"
                    raise HistoryError(code, "retry exceeds collection deadline")
                time.sleep(delay)
                continue
            code = {
                401: "unauthenticated", 403: "rate_limit" if rate_limited else "forbidden",
                404: "not_found", 429: "rate_limit",
            }.get(status, "unauthenticated" if result.returncode == 4 else "transport_failure")
            raise HistoryError(code, "GitHub API operation failed")
        raise HistoryError("transport_failure", "GitHub API operation failed")

    def json(self, endpoint: str, fields: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
        """Fetch and decode one GitHub JSON object.

        Intent
        ------
        Convert response bytes into an object-shaped API payload.

        Rationale
        ---------
        Malformed encoding and shape share one stable failure boundary.

        Pseudocode
        ----------
        - set operation = `fetch endpoint bytes`
        - set operation = `decode UTF-8 JSON`
        - set operation = `require object shape and return object`

        Wraps
        -----
        - none
        """
        try:
            value = json.loads(self._call(endpoint, fields).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise HistoryError("malformed_response", "GitHub returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise HistoryError("malformed_response", "GitHub returned an unexpected JSON shape")
        return value

    def binary(self, endpoint: str) -> bytes:
        """Fetch uninterpreted bytes from one GitHub endpoint.

        Intent
        ------
        Preserve compressed log archives exactly as downloaded.

        Rationale
        ---------
        Archive validation operates on original binary content.

        Pseudocode
        ----------
        - return bounded endpoint response bytes

        Wraps
        -----
        - none
        """
        return self._call(endpoint)


def _git(repo: Path, *arguments: str, binary: bool = False, budget: Budget | None = None) -> bytes | str:
    """Run one bounded shell-free Git command in the evidence repository.

    Intent
    ------
    Return exact bytes or surrogate-preserving text for local Git evidence.

    Rationale
    ---------
    Stable execution errors isolate Git availability from analysis semantics.

    Pseudocode
    ----------
    - set operation = `execute Git with fixed directory and timeout`
    - set operation = `classify unsuccessful execution`
    - return bytes or decoded text

    Wraps
    -----
    - none
    """
    try:
        result = subprocess.run(
            ("git", *arguments), cwd=repo, capture_output=True, check=False,
            shell=False, timeout=budget.operation_timeout() if budget is not None else 30,
        )
    except FileNotFoundError as exc:
        raise HistoryError("missing_git", "Git is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise HistoryError("git_timeout", "Git operation timed out") from exc
    if result.returncode != 0:
        raise HistoryError("git_unavailable", "Git repository evidence is unavailable")
    return result.stdout if binary else result.stdout.decode("utf-8", errors="surrogateescape")


def _is_ancestor(repo: Path, baseline: str, descendant: str, budget: Budget | None = None) -> bool | None:
    """Check Git ancestry with an explicit unknown outcome.

    Intent
    ------
    Distinguish ancestry, non-ancestry, and unavailable evidence.

    Rationale
    ---------
    Missing tools or commits must not imply a negative result.

    Pseudocode
    ----------
    - set operation = `run merge-base ancestry check`
    - return true or false for defined statuses
    - return missing for execution failures

    Wraps
    -----
    - none
    """
    try:
        result = subprocess.run(
            ("git", "merge-base", "--is-ancestor", baseline, descendant),
            cwd=repo,
            capture_output=True,
            check=False,
            shell=False,
            timeout=budget.operation_timeout() if budget is not None else 30,
        )
    except HistoryError:
        raise
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _repository_identity(repo: Path, budget: Budget | None = None) -> tuple[str, str]:
    """Derive a supported GitHub repository identity from origin.

    Intent
    ------
    Bind remote API reads to the local configured origin.

    Rationale
    ---------
    Explicit parsing prevents analysis of an unrelated repository.

    CallsFromRepo
    -------------
      ._git:
        why:
          reads: "Reads the exact configured origin URL from the local repository."

    Pseudocode
    ----------
    - set origin = `parsed SSH or URL origin`
    - set operation = `validate GitHub host and owner-repository path`
    - return normalized identity

    Wraps
    -----
    - none
    """
    raw = str(_git(repo, "remote", "get-url", "origin", budget=budget)).strip()
    if raw.startswith("git@") and ":" in raw:
        host, path = raw[4:].split(":", 1)
    else:
        parsed = urlparse(raw)
        host, path = parsed.hostname or "", parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if host.casefold() != "github.com" or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        raise HistoryError("repository_mismatch", "origin is not a supported GitHub repository URL")
    return "github.com", path


def _worktrees(repo: Path, budget: Budget | None = None) -> list[Path]:
    """List paths of every worktree for the repository.

    Intent
    ------
    Supply forbidden roots for private snapshot placement.

    Rationale
    ---------
    Evidence output must stay outside all linked checkouts.

    CallsFromRepo
    -------------
      ._git:
        why:
          reads: "Reads the porcelain worktree registry before output placement."

    Pseudocode
    ----------
    - set worktree_records = `porcelain listing`
    - return paths from worktree records

    Wraps
    -----
    - none
    """
    output = str(_git(repo, "worktree", "list", "--porcelain", budget=budget))
    return [Path(line[9:]) for line in output.splitlines() if line.startswith("worktree ")]


def _pages(client: GhClient, endpoint: str, key: str, fields: tuple[tuple[str, str], ...], *, allow_partial: bool = False, retain_positions: bool = False) -> tuple[list[dict], bool, str | None]:
    """Collect up to ten pages from a keyed GitHub listing.

    Intent
    ------
    Return object records, completeness, and an optional stop reason.

    Rationale
    ---------
    A fixed ceiling bounds API use while exposing partial evidence.

    Pseudocode
    ----------
    - set operation = `request pages with fixed page size`
    - set operation = `retain object entries after shape validation`
    - set operation = `stop on exhaustion, partial failure, or search ceiling`

    Wraps
    -----
    - none
    """
    values: list[dict] = []
    shape_stop: str | None = None
    for page in range(1, 11):
        try:
            payload = client.json(endpoint, (*fields, ("per_page", "100"), ("page", str(page))))
        except HistoryError as exc:
            if allow_partial and values:
                return values, False, exc.code
            raise
        items = payload.get(key)
        if not isinstance(items, list):
            if allow_partial and values:
                return values, False, "malformed_response"
            raise HistoryError("malformed_response", f"GitHub response lacks {key}")
        valid_items = []
        malformed_found = False
        for offset, item in enumerate(items):
            position = (page - 1) * 100 + offset
            if isinstance(item, dict):
                valid_items.append({**item, "_run_index_position": position} if retain_positions else item)
            elif retain_positions:
                malformed_found = True
                valid_items.append({"_run_index_gap": True, "_run_index_position": position})
            else:
                malformed_found = True
        if malformed_found:
            shape_stop = "malformed_item"
        values.extend(valid_items)
        if len(items) < 100:
            return values, shape_stop is None, shape_stop
    return values, False, shape_stop or "search_ceiling"


def _resolve_workflow(client: GhClient, repository: str, selector: str) -> dict[str, Any]:
    """Resolve an exact selector to one GitHub workflow object.

    Intent
    ------
    Accept IDs and paths directly or require one display-name match.

    Rationale
    ---------
    Ambiguous names must not select an unintended workflow.

    InstantiationsFromRepo
    ----------------------
      ._pages:
        why:
          transforms: "Carries complete workflow enumeration into exact-name selection."

    Pseudocode
    ----------
    - set operation = `fetch direct ID or path selector`
    - set operation = `otherwise enumerate workflows`
    - set operation = `require and return one exact name match`
    - workflows = _pages(client)
    - return workflows

    Wraps
    -----
    - none
    """
    endpoint = f"repos/{repository}/actions/workflows/{quote(selector, safe='')}"
    if selector.isdigit() or "/" in selector or selector.endswith((".yml", ".yaml")):
        return client.json(endpoint)
    workflows, complete, _stop = _pages(client, f"repos/{repository}/actions/workflows", "workflows", ())
    if not complete:
        raise HistoryError("workflow_truncated", "workflow enumeration was truncated")
    matches = [item for item in workflows if item.get("name") == selector]
    if len(matches) != 1:
        raise HistoryError("workflow_ambiguous", "workflow display name must match exactly once")
    return matches[0]


def _safe_archive_members(data: bytes, *, budget: Budget | None = None) -> tuple[list[tuple[str, bytes]], list[dict[str, Any]]]:
    """Extract safe UTF-8 log members from a bounded ZIP archive.

    Intent
    ------
    Retain regular relative logs and classify rejected entries.

    Rationale
    ---------
    Traversal, special files, malformed text, and growth are untrusted risks.

    Pseudocode
    ----------
    - set operation = `reject oversized or malformed archive`
    - set operation = `validate entry count, path, mode, and size`
    - set operation = `account retained bytes and require UTF-8`
    - return stable members and gaps

    Wraps
    -----
    - none
    """
    if budget is not None:
        budget.operation_timeout()
    if len(data) > MAX_ARCHIVE:
        raise HistoryError("archive_size", "log archive exceeds compressed size cap")
    retained: list[tuple[str, bytes]] = []
    gaps: list[dict[str, Any]] = []
    total = 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HistoryError("malformed_archive", "attempt log archive is not a ZIP file") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise HistoryError("archive_entries", "attempt log archive has too many entries")
        for index, info in enumerate(infos):
            if budget is not None:
                budget.operation_timeout()
            path = PurePosixPath(info.filename.replace("\\", "/"))
            mode = (info.external_attr >> 16) & 0o170000
            if path.is_absolute() or ".." in path.parts or mode not in {0, 0o100000} or info.is_dir():
                gaps.append(gap("archive-entry", info.filename, "extract", "unsafe_entry", "entry rejected"))
                continue
            if info.file_size > MAX_ENTRY or total + info.file_size > MAX_RETAINED:
                gaps.append(gap("archive-entry", info.filename, "extract", "entry_size", "entry rejected by size cap"))
                continue
            try:
                raw = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                gaps.append(gap("archive-entry", info.filename, "extract", "archive_read_failure", type(exc).__name__))
                continue
            total += len(raw)
            if budget is not None:
                budget.add_retained(len(raw))
            try:
                raw.decode("utf-8", errors="strict")
            except UnicodeError:
                gaps.append(gap("archive-entry", info.filename, "decode", "malformed_utf8", "entry is not UTF-8"))
                continue
            retained.append((f"{index:05d}-{path.name}", raw))
    return retained, gaps


def _normalize_attempt(run: dict[str, Any], attempt: dict[str, Any], expected_number: int) -> dict[str, Any]:
    """Normalize run and attempt metadata into the snapshot schema.

    Intent
    ------
    Retain canonical identifiers, timestamps, status, and empty collections.

    Rationale
    ---------
    Reports consume one stable attempt shape independent of API extras.

    Pseudocode
    ----------
    - set operation = `copy identifiers and normalize timestamps`
    - set operation = `mark whether creation starts the first attempt`
    - set operation = `initialize evidence collections`

    Wraps
    -----
    - none
    """
    attempt_number = attempt.get("run_attempt")
    if not isinstance(attempt_number, int) or isinstance(attempt_number, bool) or attempt_number < 1:
        raise HistoryError("run_attempt_unavailable", "attempt detail lacks a positive integer run_attempt")
    if attempt_number != expected_number:
        raise HistoryError("run_attempt_mismatch", "attempt detail run_attempt does not match the requested attempt")
    return {
        "run_id": int(run["id"]), "run_number": int(run["run_number"]),
        "attempt_number": attempt_number,
        "created_at": utc_timestamp(attempt.get("created_at")),
        "run_started_at": utc_timestamp(attempt.get("run_started_at")),
        "updated_at": utc_timestamp(attempt.get("updated_at")),
        "start_origin_proven": attempt_number == 1,
        "status": attempt.get("status"), "conclusion": attempt.get("conclusion"),
        "jobs": [], "jobs_listing_complete": False, "occurrences": [], "log_paths": [],
        "failure_log_expected": False, "failure_log_available": False,
    }


def _collect_run(client: GhClient, repository: str, run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes], list[dict[str, Any]]]:
    """Collect all attempts, jobs, and failed logs for one logical run.

    Intent
    ------
    Produce a normalized run, immutable members, and classified gaps.

    Rationale
    ---------
    Attempt and failure-log completeness must remain visible.

    InstantiationsFromRepo
    ----------------------
      ._normalize_attempt:
        why:
          transforms: "Carries normalized attempt metadata into each logical run record."
      ._pages:
        why:
          transforms: "Carries paginated jobs into attempt timing and status evidence."
      ._safe_archive_members:
        why:
          transforms: "Carries validated log members and extraction gaps into the snapshot."

    Pseudocode
    ----------
    - set operation = `enumerate and normalize expected attempts`
    - set operation = `collect jobs and timing boundaries`
    - set operation = `inspect failed-attempt logs and parsed occurrences`
    - return run, members, and gaps
    - attempt = _normalize_attempt(run)
    - jobs = _pages(client)
    - logs = _safe_archive_members(archive)
    - return attempt
    - return jobs
    - return logs

    Wraps
    -----
    - none
    """
    run_id = int(run["id"])
    attempts: list[dict[str, Any]] = []
    members: dict[str, bytes] = {}
    gaps: list[dict[str, Any]] = []
    latest_attempt = run.get("run_attempt")
    if not isinstance(latest_attempt, int) or isinstance(latest_attempt, bool) or latest_attempt < 1:
        gaps.append(gap("logical-run", str(run_id), "enumerate-attempts", "run_attempt_unavailable", "run index lacks a positive integer run_attempt"))
        return ({
            "run_id": run_id, "run_number": int(run["run_number"]),
            "created_at": utc_timestamp(run.get("created_at")), "event": run.get("event"),
            "status": run.get("status"), "conclusion": run.get("conclusion"),
            "head_sha": run.get("head_sha"), "head_branch": run.get("head_branch"),
            "html_url": run.get("html_url"), "attempt_listing_complete": False,
            "expected_attempt_count": None, "failure_evidence_complete": False,
            "attempts": [],
        }, members, gaps)
    listing_complete = True
    for number in range(1, latest_attempt + 1):
        try:
            detail = client.json(f"repos/{repository}/actions/runs/{run_id}/attempts/{number}")
            normalized = _normalize_attempt(run, detail, number)
            jobs, jobs_complete, jobs_stop = _pages(client, f"repos/{repository}/actions/runs/{run_id}/attempts/{number}/jobs", "jobs", (), allow_partial=True)
            if not jobs_complete:
                gaps.append(gap("attempt", f"{run_id}:{number}", "jobs", jobs_stop or "pagination_truncated", "job pagination incomplete"))
            normalized["jobs_listing_complete"] = jobs_complete
            for job in sorted(jobs, key=lambda item: int(item.get("id", 0))):
                normalized["jobs"].append({
                    "job_id": int(job.get("id", 0)), "name": str(job.get("name", "")),
                    "status": job.get("status"), "conclusion": job.get("conclusion"),
                    "started_at": utc_timestamp(job.get("started_at")), "completed_at": utc_timestamp(job.get("completed_at")),
                    "labels": sorted(str(label) for label in job.get("labels", [])),
                    "steps": [{"step_ordinal": int(step["number"]), "name": str(step.get("name", "")), "status": step.get("status"), "conclusion": step.get("conclusion"), "started_at": utc_timestamp(step.get("started_at")), "completed_at": utc_timestamp(step.get("completed_at"))} for step in job.get("steps", [])],
                })
            if detail.get("conclusion") != "success" and detail.get("status") == "completed":
                normalized["failure_log_expected"] = True
                try:
                    archive = client.binary(f"repos/{repository}/actions/runs/{run_id}/attempts/{number}/logs")
                    log_members, archive_gaps = _safe_archive_members(archive, budget=getattr(client, "budget", None))
                    gaps.extend(archive_gaps)
                    archive_path = f"failed-logs/{run_id}/{number}/archive.zip"
                    members[archive_path] = archive
                    normalized["archive_path"] = archive_path
                    normalized["archive_sha256"] = sha256_bytes(archive)
                    normalized["failure_log_available"] = not archive_gaps
                    for filename, raw in log_members:
                        relative = f"failed-logs/{run_id}/{number}/{filename}"
                        members[relative] = raw
                        normalized["log_paths"].append(relative)
                        source = {"run_id": run_id, "run_number": int(run["run_number"]), "attempt_number": number, "job_id": 0, "job_identity_state": "unknown", "step_ordinal": -1, "step_identity_state": "unknown", "log_path": relative, "head_sha": run.get("head_sha")}
                        found, parse_gaps = extract_pytest_nodes(raw.decode("utf-8"), source=source)
                        normalized["occurrences"].extend(found)
                        gaps.extend(parse_gaps)
                except HistoryError as exc:
                    gaps.append(gap("attempt", f"{run_id}:{number}", "logs", exc.code, str(exc)))
            attempts.append(normalized)
        except HistoryError as exc:
            listing_complete = False
            gaps.append(gap("attempt", f"{run_id}:{number}", "collect", exc.code, str(exc)))
    return ({
        "run_id": run_id, "run_number": int(run["run_number"]),
        "created_at": utc_timestamp(run.get("created_at")), "event": run.get("event"),
        "status": run.get("status"), "conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"), "head_branch": run.get("head_branch"),
        "html_url": run.get("html_url"), "attempt_listing_complete": listing_complete,
        "expected_attempt_count": latest_attempt,
        "failure_evidence_complete": listing_complete and not gaps,
        "attempts": attempts,
    }, members, gaps)


def _git_show(repo: Path, sha: str, path: str, budget: Budget | None = None) -> tuple[str, str | None]:
    """Read a UTF-8 file at a commit with a typed evidence state.

    Intent
    ------
    Obtain exact historical Python source for comparison.

    Rationale
    ---------
    Separating absence from read and decode failures prevents false provenance.

    CallsFromRepo
    -------------
      ._git:
        why:
          reads: "Reads the exact historical blob bytes from the requested commit."

    Pseudocode
    ----------
    - set listing = `exact commit path listing`
    - return absent blob when the path is not listed
    - set blob = `commit path bytes`
    - return source or a typed read failure

    Wraps
    -----
    - none
    """
    try:
        listing = str(
            _git(repo, "ls-tree", "--name-only", sha, "--", path, budget=budget)
        ).splitlines()
        if path not in listing:
            return "absent_blob", None
        raw = bytes(_git(repo, "show", f"{sha}:{path}", binary=True, budget=budget))
    except HistoryError as exc:
        if exc.code == "total_deadline":
            raise
        return "git_failure", None
    try:
        return "available", raw.decode("utf-8")
    except UnicodeError:
        return "decode_failure", None


def _definitions(source: str, path: str) -> dict[str, str]:
    """Index pytest definitions by canonical ID and AST digest.

    Intent
    ------
    Represent tests independently from formatting and source locations.

    Rationale
    ---------
    Structural digests support edit, introduction, and move classification.

    Pseudocode
    ----------
    - set operation = `parse source or return empty index`
    - set operation = `visit nested definition scopes`
    - set operation = `hash normalized test-function syntax`
    - return definition index

    Wraps
    -----
    - none
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    found: dict[str, str] = {}

    def visit(body: list[ast.stmt], scopes: list[str]) -> None:
        """Recursively index test definitions in one syntax-tree body.

        Intent
        ------
        Traverse nested scopes while building canonical test IDs.

        Rationale
        ---------
        Pytest tests may be methods or nested definitions.

        Pseudocode
        ----------
        - set operation = `inspect class and function statements`
        - set operation = `extend scope path and hash tests`
        - set operation = `recurse into definition bodies`

        Wraps
        -----
        - none
        """
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                current = [*scopes, node.name]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    found["::".join((path, *current))] = sha256_bytes(ast.dump(node, include_attributes=False).encode("utf-8"))
                visit(node.body, current)

    visit(tree.body, [])
    return found


def _commit_available(repo: Path, sha: str, budget: Budget | None = None) -> bool:
    """Return whether an exact Git commit is locally available.

    Intent
    ------
    Gate comparisons on proven commit evidence.

    Rationale
    ---------
    Shallow repositories must yield missing evidence, not false provenance.

    CallsFromRepo
    -------------
      ._git:
        why:
          validates: "Asks Git to verify the exact commit object locally."

    Pseudocode
    ----------
    - set operation = `ask Git to verify commit object`
    - return false on Git failure
    - return true otherwise

    Wraps
    -----
    - none
    """
    try:
        _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", budget=budget)
    except HistoryError as exc:
        if exc.code == "total_deadline":
            raise
        return False
    return True


def _definitions_checked(source: str, path: str) -> dict[str, str] | None:
    """Index definitions while preserving invalid-syntax evidence.

    Intent
    ------
    Distinguish parse failure from a valid file with no tests.

    Rationale
    ---------
    Provenance must not conflate malformed evidence with absence.

    InstantiationsFromRepo
    ----------------------
      ._definitions:
        why:
          transforms: "Carries validated source into a structural test-definition index."

    Pseudocode
    ----------
    - set operation = `validate source syntax`
    - return missing on syntax error
    - return structural definition index
    - definitions = _definitions(source)
    - return definitions

    Wraps
    -----
    - none
    """
    try:
        ast.parse(source)
    except SyntaxError:
        return None
    return _definitions(source, path)


def _comparison(repo: Path, baseline: str | None, failing: str | None, canonical: str, budget: Budget | None = None) -> dict[str, Any]:
    """Classify a test definition between two commits.

    Intent
    ------
    Identify unchanged, edited, introduced, moved, or unavailable provenance.

    Rationale
    ---------
    Structural evidence is more reliable than names alone.

    CallsFromRepo
    -------------
      ._commit_available:
        why:
          validates: "Checks that both comparison commits exist locally."
      ._definitions_checked:
        why:
          validates: "Checks syntax before comparing same-path definition digests."
      ._git:
        why:
          reads: "Lists Python paths changed between the comparison commits."

    InstantiationsFromRepo
    ----------------------
      ._definitions_checked:
        why:
          transforms: "Carries syntax-validated definition indexes into digest comparison."
      ._git_show:
        why:
          transforms: "Carries historical source text into definition parsing."

    Pseudocode
    ----------
    - set operation = `require commits and failing definition`
    - set operation = `compare same-path structural digests`
    - set operation = `search changed paths for baseline digest`
    - return classified provenance
    - baseline_defs = _definitions_checked(source)
    - failing_source = _git_show(repo)
    - return baseline_defs
    - return failing_source

    Wraps
    -----
    - none
    """
    if not baseline or not failing:
        return {"state": "missing_commit"}
    if not _commit_available(repo, baseline, budget) or not _commit_available(repo, failing, budget):
        return {"state": "missing_commit"}
    path = canonical.split("::", 1)[0]
    failing_state, failing_source = _git_show(repo, failing, path, budget)
    if failing_source is None:
        return {"state": failing_state, "failing_path": path}
    failing_defs = _definitions_checked(failing_source, path)
    if failing_defs is None:
        return {"state": "parse_failure", "failing_path": path}
    target = failing_defs.get(canonical)
    if target is None:
        return {"state": "not_found", "baseline_path": path, "failing_path": path}
    baseline_state, baseline_source = _git_show(repo, baseline, path, budget)
    if baseline_state not in {"available", "absent_blob"}:
        return {"state": baseline_state, "baseline_path": path, "failing_path": path}
    baseline_defs = _definitions_checked(baseline_source, path) if baseline_source is not None else {}
    if baseline_defs is None:
        return {"state": "parse_failure", "baseline_path": path, "failing_path": path}
    prior = baseline_defs.get(canonical)
    if prior is not None:
        return {"state": "pre_existing_unchanged" if prior == target else "edited", "baseline_path": path, "failing_path": path, "baseline_digest": prior, "failing_digest": target}
    try:
        changed = str(_git(repo, "diff", "--name-only", baseline, failing, "--", "*.py", budget=budget)).splitlines()
    except HistoryError as exc:
        if exc.code == "total_deadline":
            raise
        return {"state": "diff_failure", "baseline_path": path, "failing_path": path}
    matches: list[str] = []
    for candidate_path in sorted(set(changed)):
        source_state, source = _git_show(repo, baseline, candidate_path, budget)
        if source_state == "absent_blob":
            continue
        if source is None:
            return {"state": "candidate_evidence_incomplete", "baseline_path": candidate_path, "failing_path": path}
        candidate_definitions = _definitions_checked(source, candidate_path)
        if candidate_definitions is None:
            return {"state": "candidate_parse_failure", "baseline_path": candidate_path, "failing_path": path}
        matches.extend(key for key, value in candidate_definitions.items() if value == target)
    if len(matches) == 1:
        return {"state": "renamed_or_moved", "baseline_test_id": matches[0], "failing_test_id": canonical, "baseline_path": matches[0].split("::", 1)[0], "failing_path": path, "failing_digest": target}
    return {"state": "ambiguous" if len(matches) > 1 else "introduced", "baseline_path": path, "failing_path": path, "failing_digest": target}


def _git_evidence(repo: Path, runs: list[dict[str, Any]], budget: Budget | None = None, sequence_gaps: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Freeze dual-baseline Git provenance for episode incidence.

    Intent
    ------
    Compare first failures to preceding greens and first parents.

    Rationale
    ---------
    Branch progression and immediate-parent context answer different questions.

    CallsFromRepo
    -------------
      ._comparison:
        why:
          computes: "Classifies each observed test against its two Git baselines."
      ._git:
        why:
          reads: "Reads all parent commit identifiers for each first failure."
      ._is_ancestor:
        why:
          validates: "Proves whether the preceding green belongs to failing ancestry."

    InstantiationsFromRepo
    ----------------------
      ._comparison:
        why:
          transforms: "Carries both baseline classifications into frozen provenance evidence."

    Pseudocode
    ----------
    - set operation = `build complete failure episodes`
    - set operation = `find parents and verify ancestry`
    - set operation = `classify both provenance baselines`
    - return comparisons and gaps
    - comparison = _comparison(repo)
    - return comparison

    Wraps
    -----
    - none
    """
    comparisons: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    deadline_code: str | None = None
    for episode in build_episodes(runs, sequence_gaps)["episodes"]:
        for incidence in episode["incidence"]:
            observation = incidence["first_observation"]
            failing = observation.get("head_sha")
            parent = None
            parent_shas: list[str] = []
            preceding = episode.get("preceding_green_sha")
            if deadline_code is None:
                try:
                    if failing:
                        parents = str(_git(repo, "rev-list", "--parents", "-n", "1", failing, budget=budget)).split()
                        parent_shas = parents[1:]
                        parent = parent_shas[0] if parent_shas else None
                    ancestry = _is_ancestor(repo, preceding, failing, budget) if preceding and failing else None
                    if ancestry is True:
                        baseline_a = _comparison(repo, preceding, failing, incidence["canonical_test_id"], budget)
                    elif ancestry is False:
                        baseline_a = {"state": "non_ancestry"}
                    else:
                        baseline_a = {"state": "missing_commit"}
                    baseline_b = _comparison(repo, parent, failing, incidence["canonical_test_id"], budget) if parent else {"state": "absent_parent"}
                except HistoryError as exc:
                    if exc.code != "total_deadline":
                        gaps.append(gap("commit", str(failing or episode["episode_id"]), "provenance", exc.code, str(exc)))
                    else:
                        deadline_code = exc.code
                        gaps.append(gap("collection", str(failing or episode["episode_id"]), "git-evidence", exc.code, str(exc)))
                    baseline_a = {"state": "evidence_incomplete", "reason": exc.code}
                    baseline_b = {"state": "evidence_incomplete", "reason": exc.code}
            else:
                baseline_a = {"state": "evidence_incomplete", "reason": deadline_code}
                baseline_b = {"state": "evidence_incomplete", "reason": deadline_code}
            comparisons.append({"episode_id": episode["episode_id"], "canonical_test_id": incidence["canonical_test_id"], "first_failing_sha": failing, "all_parent_shas": parent_shas, "preceding_green": baseline_a, "first_parent": baseline_b})
    return {"schema_version": 1, "comparisons": comparisons}, gaps


def main(argv: list[str] | None = None) -> int:
    """Collect bounded CI and Git evidence into a private snapshot.

    Intent
    ------
    Implement historical GitHub Actions evidence acquisition.

    Rationale
    ---------
    Validation, concurrency, and immutable publication form one transaction.

    CallsFromRepo
    -------------
      ._worktrees:
        why:
          reads: "Enumerates every forbidden repository checkout before output placement."

    InstantiationsFromRepo
    ----------------------
      .Budget:
        why:
          constructs: "Creates the shared resource and deadline accounting envelope."
      .GhClient:
        why:
          constructs: "Creates the constrained authenticated GitHub API client."
      ._git_evidence:
        why:
          transforms: "Carries frozen provenance comparisons into snapshot members."
      ._pages:
        why:
          transforms: "Carries paginated workflow runs into bounded selection."
      ._repository_identity:
        why:
          transforms: "Carries the validated origin identity into API paths."
      ._resolve_workflow:
        why:
          transforms: "Carries the exact workflow object into run enumeration."

    Pseudocode
    ----------
    - set operation = `parse and validate collection arguments`
    - set operation = `bind origin to repository and workflow`
    - set operation = `collect selected runs concurrently`
    - set operation = `freeze provenance and publish snapshot`
    - set operation = `print success or classified failure receipt`
    - budget = Budget(timeout)
    - client = GhClient(repo, budget)
    - provenance = _git_evidence(repo)
    - indexed = _pages(client)
    - identity = _repository_identity(repo)
    - workflow = _resolve_workflow(client)
    - return provenance
    - return indexed
    - return identity
    - return workflow

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(prog="fetch-github-actions-history")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--event", default="push")
    parser.add_argument("--since")
    parser.add_argument("--run-limit", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if args.run_limit < 1 or args.timeout < 1 or not 1 <= args.workers <= 32:
        parser.error("limits and timeout must be positive; workers must be between 1 and 32")
    root: Path | None = None
    started = time.monotonic()
    try:
        repo = Path(args.repo_root).resolve(strict=True)
        budget = Budget(args.timeout)
        if not (repo / ".git").exists():
            raise HistoryError("invalid_repository", "repo-root is not a Git worktree")
        destination = Path(args.snapshot_dir)
        require_outside(destination, _worktrees(repo, budget))
        host, repository = _repository_identity(repo, budget)
        client = GhClient(repo, budget)
        remote = client.json(f"repos/{repository}")
        if remote.get("full_name") != repository:
            raise HistoryError("repository_mismatch", "authenticated API repository differs from origin")
        workflow = _resolve_workflow(client, repository, args.workflow)
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since else None
        if since is not None:
            if since.tzinfo is None:
                raise HistoryError("invalid_since", "since must include a timezone")
            since = since.astimezone(UTC)
        fields = (("branch", args.branch), ("event", args.event))
        if args.since:
            fields = (*fields, ("created", f">={utc_timestamp(args.since)}"))
        indexed_items, index_complete, index_stop = _pages(client, f"repos/{repository}/actions/workflows/{workflow['id']}/runs", "workflow_runs", fields, allow_partial=True, retain_positions=True)
        indexed = [item for item in indexed_items if not item.get("_run_index_gap")]
        selected = []
        for run in indexed:
            created = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
            if since is None or created >= since:
                selected.append(run)
        selected.sort(key=lambda item: (int(item.get("run_number", 0)), int(item.get("id", 0))), reverse=True)
        selected = selected[: args.run_limit]
        selection_gaps: list[dict[str, Any]] = []
        detail_request_ceiling = sum(run["run_attempt"] * 12 for run in selected if isinstance(run.get("run_attempt"), int) and not isinstance(run.get("run_attempt"), bool) and run["run_attempt"] > 0)
        request_plan_ceiling = budget.requests + detail_request_ceiling
        while selected and request_plan_ceiling > MAX_REQUESTS:
            selected.pop()
            detail_request_ceiling = sum(run["run_attempt"] * 12 for run in selected if isinstance(run.get("run_attempt"), int) and not isinstance(run.get("run_attempt"), bool) and run["run_attempt"] > 0)
            request_plan_ceiling = budget.requests + detail_request_ceiling
        if len(selected) < min(len(indexed), args.run_limit):
            selection_gaps.append(gap("run-index", str(workflow["id"]), "plan", "request_plan_cap", "older selected runs excluded by derived request ceiling"))
        selected_ids = {item.get("id") for item in selected}
        sequence_gaps: list[dict[str, Any]] = []
        for offset, item in enumerate(indexed_items):
            if not item.get("_run_index_gap"):
                continue
            newer = next((candidate.get("id") for candidate in reversed(indexed_items[:offset]) if not candidate.get("_run_index_gap")), None)
            older = next((candidate.get("id") for candidate in indexed_items[offset + 1:] if not candidate.get("_run_index_gap")), None)
            newer = newer if newer in selected_ids else None
            older = older if older in selected_ids else None
            if newer is not None or older is not None:
                sequence_gaps.append({
                    "position": item["_run_index_position"],
                    "newer_run_id": newer,
                    "older_run_id": older,
                    "reason": "malformed_run_index_item",
                })
        collected: list[dict[str, Any]] = []
        members: dict[str, bytes] = {}
        gaps: list[dict[str, Any]] = list(selection_gaps)
        if not index_complete:
            gaps.append(gap("run-index", str(workflow["id"]), "paginate", index_stop or "pagination_truncated", "workflow run pagination incomplete"))
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_collect_run, client, repository, run): run for run in selected}
            for future in as_completed(futures):
                run = futures[future]
                try:
                    normalized, run_members, run_gaps = future.result()
                    collected.append(normalized)
                    members.update(run_members)
                    gaps.extend(run_gaps)
                except HistoryError as exc:
                    gaps.append(gap("logical-run", str(run.get("id")), "collect", exc.code, str(exc)))
                    collected.append({
                        "run_id": int(run.get("id", 0)),
                        "run_number": int(run.get("run_number", 0)),
                        "created_at": utc_timestamp(run.get("created_at")),
                        "event": run.get("event"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "head_sha": run.get("head_sha"),
                        "head_branch": run.get("head_branch"),
                        "html_url": run.get("html_url"),
                        "attempt_listing_complete": False,
                        "failure_evidence_complete": False,
                        "attempts": [],
                        "collection_gap": exc.code,
                    })
        collected.sort(key=lambda item: (item["run_number"], item["run_id"]))
        git_evidence, git_gaps = _git_evidence(repo, collected, budget, sequence_gaps)
        gaps.extend(git_gaps)
        members["runs.json"] = canonical_json_bytes({"schema_version": 1, "runs": collected, "sequence_gaps": sequence_gaps})
        members["git-evidence.json"] = canonical_json_bytes(git_evidence)
        try:
            budget.operation_timeout()
        except HistoryError as exc:
            gaps.append(gap("collection", str(workflow["id"]), "deadline", exc.code, str(exc)))
        root = reserve_private_root(destination)
        state = "collected" if not gaps else "collected-with-gaps"
        publication = publish_tree(root, manifest_fields={
            "collector_version": 1, "collected_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "repository_host": host, "repository": repository,
            "workflow_id": int(workflow["id"]), "workflow_name": workflow.get("name"), "workflow_path": workflow.get("path"), "workflow_state": workflow.get("state"),
            "branch": args.branch, "event": args.event, "since": args.since, "run_limit": args.run_limit,
            "selected_run_ids": [run["run_id"] for run in collected],
            "selected_attempt_ids": [[run["run_id"], attempt["attempt_number"]] for run in collected for attempt in run["attempts"]],
            "request_count": budget.requests, "retry_count": budget.retries, "downloaded_bytes": budget.downloaded,
            "request_plan_ceiling": request_plan_ceiling,
            "retained_bytes": budget.retained,
            "requested_since": utc_timestamp(args.since) if args.since else None,
            "actual_oldest_created_at": min((run["created_at"] for run in collected if run.get("created_at")), default=None),
            "actual_newest_created_at": max((run["created_at"] for run in collected if run.get("created_at")), default=None),
            "selection_stop_reason": "request_plan_cap" if selection_gaps else "run_limit" if len(selected) == args.run_limit and len(indexed) > args.run_limit else "proven_exhaustion" if index_complete else index_stop or "pagination_incomplete",
            "elapsed_ms": round((time.monotonic() - started) * 1000), "workers": args.workers,
        }, members=members, outcome=state, gaps=gaps)
        print(json.dumps({"schema_version": 1, "state": state, "snapshot_digest": publication["snapshot_digest"], "snapshot_dir": str(root), "selected_run_count": len(collected), "selected_attempt_count": sum(len(run["attempts"]) for run in collected), "gap_count": len(gaps)}, sort_keys=True))
        return 0
    except (HistoryError, KeyError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, HistoryError) else "collection_failed"
        print(json.dumps({"schema_version": 1, "state": "failed", "error": code, "incomplete_path": str(root) if root else None}, sort_keys=True))
        print(str(exc), file=sys.stderr)
        return 2


class Interface(PythonArgvMachineInterface):
    """Expose bounded read-only GitHub Actions history collection.

    Intent
    ------
    Present the collector through the Python machine interface protocol.

    Rationale
    ---------
    A narrow adapter preserves its argument and status contract.

    Pseudocode
    ----------
    - set program_name = `collector`
    - set operation = `forward interface arguments to collector command`

    Wraps
    -----
    - none
    """

    prog = "fetch-github-actions-history"

    def run(self, argv: list[str]) -> int:
        """Forward machine-interface arguments to the collector.

        Intent
        ------
        Preserve collector parsing, output, and exit status.

        Rationale
        ---------
        Dispatcher adaptation should not duplicate collection logic.

        Pseudocode
        ----------
        - return collector status for input arguments

        Wraps
        -----
        main -> preprocess: pass argv unchanged; postprocess: return status unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
