#!/usr/bin/env python3
"""Persist metrics-only diagnostics for one mathematical graph extraction run.

The report owner timestamps worker and deterministic-stage lifecycle events,
measures artifacts without retaining their contents, validates the complete
report, and atomically replaces ``run-diagnostics.json`` after every event.
Durations use a monotonic clock; UTC is retained only for cross-process
correlation.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Callable, Iterable, Iterator, Protocol

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._batch_ir_merger import _validator_for, write_json_atomic
except ImportError:  # pragma: no cover - direct script execution
    from _batch_ir_merger import _validator_for, write_json_atomic


SKILL_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_DIR / "run-diagnostics.schema.json"
REPORT_NAME = "run-diagnostics.json"
_HASH_CHUNK_SIZE = 1024 * 1024
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,119}$")
_DIAGNOSTIC_CATEGORIES = {"run", "worker", "stage", "validation"}
_DIAGNOSTIC_CODES = {
    "run-failed",
    "worker-failed",
    "stage-failed",
    "validation-failed",
    "timeout",
    "capacity",
    "transient",
    "diagnostics-write-failed",
}
_WORKER_ERROR_CODES = {
    "worker-failed",
    "validation-failed",
    "timeout",
    "capacity",
    "transient",
    "diagnostics-write-failed",
}
_RUN_ERROR_CODES = {
    "run-failed",
    "stage-failed",
    "validation-failed",
    "timeout",
    "capacity",
    "transient",
}
_RETRY_CODES = {
    "worker-failed",
    "validation-failed",
    "timeout",
    "capacity",
    "transient",
}
_SCHEMA_KEYWORDS = {
    "additionalProperties",
    "anyOf",
    "const",
    "dependentRequired",
    "enum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "not",
    "oneOf",
    "pattern",
    "required",
    "type",
    "uniqueItems",
}
_SCHEMA_PATH_KEYS = {
    "artifacts",
    "candidate_count",
    "candidate_ids",
    "candidates",
    "counts",
    "description",
    "diagnostics_version",
    "entities",
    "exclusions",
    "gaps",
    "inventory",
    "jobs",
    "ratios",
    "relationships",
    "run",
    "stages",
    "unresolved_resolutions",
}


class Clock(Protocol):
    """Provide the two clock domains required by durable diagnostics."""

    def utc_now(self) -> datetime:
        """Return a timezone-aware instant used only for correlation."""

    def monotonic_ns(self) -> int:
        """Return a monotonic instant used only for elapsed durations."""


class _SystemClock:
    """Adapt standard-library clocks to the narrow diagnostics protocol."""

    def utc_now(self) -> datetime:
        """Return current UTC with explicit timezone awareness."""

        return datetime.now(timezone.utc)

    def monotonic_ns(self) -> int:
        """Return the process-independent host monotonic counter."""

        return time.monotonic_ns()


def _measure_file(path: Path) -> tuple[int, str]:
    """Stream one artifact to obtain its byte size and SHA-256 digest."""

    total = 0
    digest = hashlib.sha256()
    with path.resolve().open("rb") as stream:
        while True:
            chunk = stream.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
    return total, digest.hexdigest()


def _ensure_dir(path: Path) -> None:
    """Create the selected run directory and its missing parents."""

    path.resolve().mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict:
    """Read one diagnostics object without accepting a non-object root."""

    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"diagnostics report must be a JSON object: {path}")
    return payload


def _validate_report_payload(payload: dict) -> None:
    """Validate one complete report with the skill-owned checked JSON Schema."""

    _validator_for(SCHEMA_PATH).validate(payload)


def _utc_timestamp(value: datetime) -> str:
    """Render one aware instant in portable ISO-8601 UTC form."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("diagnostics UTC clock must return timezone-aware values")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _elapsed_ms(start_ns: int, finish_ns: int) -> int:
    """Convert one nonnegative monotonic interval to whole milliseconds."""

    if finish_ns < start_ns:
        raise ValueError("diagnostics monotonic clock moved backwards")
    return (finish_ns - start_ns) // 1_000_000


def _diagnostic_record(
    code: str,
    category: str,
    error: BaseException | None = None,
) -> dict:
    """Return allowlisted failure structure and only a hash of arbitrary text."""

    if code not in _DIAGNOSTIC_CODES:
        raise ValueError(f"unsupported diagnostics error code: {code}")
    if category not in _DIAGNOSTIC_CATEGORIES:
        raise ValueError(f"unsupported diagnostics category: {category}")
    record = {"code": code, "category": category}
    if error is None:
        return record
    type_name = type(error).__name__
    if _ERROR_TYPE_RE.fullmatch(type_name):
        record["exception_type"] = type_name
    record["message_sha256"] = hashlib.sha256(
        str(error).encode("utf-8", errors="replace")
    ).hexdigest()
    keyword = getattr(error, "validator", None)
    if isinstance(keyword, str) and keyword in _SCHEMA_KEYWORDS:
        record["schema_keyword"] = keyword
    raw_path = getattr(error, "absolute_path", getattr(error, "path", None))
    if raw_path is not None:
        path = list(raw_path)
        if path and all(
            (isinstance(part, int) and part >= 0)
            or (isinstance(part, str) and part in _SCHEMA_PATH_KEYS)
            for part in path
        ):
            record["schema_path"] = path
    return record


class RunDiagnostics:
    """Own one schema-valid, atomically replaced diagnostics report.

    Instances coordinate the mutable report state and its invariants. Clock and
    filesystem collaborators are captured at construction so tests can control
    time and force write failures without changing the public event methods.
    Every mutating method prepares a deep-copied candidate report, validates and
    writes it, and updates in-memory state only after publication succeeds.
    """

    def __init__(
        self,
        run_dir: Path,
        payload: dict,
        *,
        clock: Clock,
        ensure_dir: Callable[[Path], None],
        measure_file: Callable[[Path], tuple[int, str]],
        read_payload: Callable[[Path], dict],
        validate_payload: Callable[[dict], None],
        write_payload: Callable[[dict, Path], None],
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.path = self.run_dir / REPORT_NAME
        self.payload = payload
        self._clock = clock
        self._ensure_dir = ensure_dir
        self._measure_file = measure_file
        self._read_payload = read_payload
        self._validate_payload = validate_payload
        self._write_payload = write_payload

    @classmethod
    def initialize(
        cls,
        run_dir: Path,
        *,
        entrypoint: Path,
        clock: Clock | None = None,
    ) -> "RunDiagnostics":
        """Create and publish a fresh running report for one TeX entrypoint."""

        selected_clock = clock or _SystemClock()
        resolved_run_dir = run_dir.resolve()
        started_at = _utc_timestamp(selected_clock.utc_now())
        started_ns = selected_clock.monotonic_ns()
        report = cls(
            resolved_run_dir,
            {},
            clock=selected_clock,
            ensure_dir=_ensure_dir,
            measure_file=_measure_file,
            read_payload=_read_json,
            validate_payload=_validate_report_payload,
            write_payload=write_json_atomic,
        )
        report._ensure_dir(resolved_run_dir)
        if report.path.exists():
            raise FileExistsError(f"diagnostics report already exists: {report.path}")
        resolved_entrypoint = entrypoint.resolve()
        entrypoint_bytes, entrypoint_sha256 = report._measure_file(resolved_entrypoint)
        initialized_at = _utc_timestamp(selected_clock.utc_now())
        initialized_ns = selected_clock.monotonic_ns()
        identity_material = f"{resolved_entrypoint}\0{entrypoint_sha256}\0{started_at}"
        run_id = "run-" + hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:16]
        payload = {
            "diagnostics_version": 1,
            "run": {
                "id": run_id,
                "entrypoint_path": str(resolved_entrypoint),
                "entrypoint_bytes": entrypoint_bytes,
                "entrypoint_sha256": entrypoint_sha256,
                "started_at_utc": started_at,
                "started_monotonic_ns": started_ns,
                "initialized_at_utc": initialized_at,
                "initialization_ms": _elapsed_ms(started_ns, initialized_ns),
                "status": "running",
            },
            "jobs": [],
            "stages": [],
            "artifacts": [],
            "ratios": [],
            "validation_diagnostics": [],
            "counts": {
                "jobs": 0,
                "retries": 0,
                "validation_errors": 0,
                "corrections": 0,
                "entities": 0,
                "relationships": 0,
                "exclusions": 0,
                "unresolved": 0,
                "unresolved_resolutions": 0,
                "gaps": 0,
            },
        }
        report.payload = payload
        report._publish(payload)
        return report

    @classmethod
    def open(
        cls,
        run_dir: Path,
        *,
        clock: Clock | None = None,
    ) -> "RunDiagnostics":
        """Open and validate the durable report in an existing run directory."""

        resolved_run_dir = run_dir.resolve()
        report = cls(
            resolved_run_dir,
            {},
            clock=clock or _SystemClock(),
            ensure_dir=_ensure_dir,
            measure_file=_measure_file,
            read_payload=_read_json,
            validate_payload=_validate_report_payload,
            write_payload=write_json_atomic,
        )
        payload = report._read_payload(resolved_run_dir / REPORT_NAME)
        report.payload = payload
        report._validate(payload)
        return report

    def _validate(self, payload: dict) -> None:
        """Validate a complete candidate report against the owned JSON Schema."""

        self._validate_payload(payload)

    def _publish(self, payload: dict) -> None:
        """Validate then atomically publish a complete report candidate."""

        self._validate(payload)
        self._write_payload(payload, self.path)

    def _update(
        self,
        mutation: Callable[[dict], None],
        *,
        require_running: bool = True,
    ) -> None:
        """Publish one transactional mutation and commit memory only on success."""

        if require_running and self.payload["run"]["status"] != "running":
            raise ValueError("diagnostics run is already finished")
        candidate = deepcopy(self.payload)
        mutation(candidate)
        self._publish(candidate)
        self.payload = candidate

    def _artifact_record(
        self,
        path: Path,
        *,
        kind: str,
        phase: str,
        counts: dict | None,
        artifacts: list[dict],
    ) -> tuple[dict, bool]:
        """Measure one artifact into a prose-free record with a stable local id."""

        resolved_path = path.resolve()
        byte_count, sha256 = self._measure_file(resolved_path)
        for existing in artifacts:
            if (
                existing["kind"] == kind
                and existing["path"] == str(resolved_path)
                and existing["bytes"] == byte_count
                and existing["sha256"] == sha256
            ):
                if counts:
                    existing["counts"] = self._safe_counts(counts)
                return existing, False
        ordinal = 1 + sum(item["kind"] == kind for item in artifacts)
        record = {
            "id": f"{kind}-{ordinal}",
            "kind": kind,
            "path": str(resolved_path),
            "phase": phase,
            "bytes": byte_count,
            "sha256": sha256,
        }
        if counts:
            record["counts"] = self._safe_counts(counts)
        return record, True

    @staticmethod
    def _safe_counts(counts: dict) -> dict:
        """Project caller-computed metrics onto the schema-owned integer keys."""

        allowed = {
            "files",
            "nodes",
            "edges",
            "evidence",
            "references",
            "candidates",
            "unresolved_entities",
            "relationship_hints",
            "reference_decisions",
            "hint_decisions",
            "entities",
            "relationships",
            "exclusions",
            "unresolved",
            "unresolved_resolutions",
            "gap_decisions",
            "gaps",
            "macros",
        }
        return {key: int(value) for key, value in counts.items() if key in allowed}

    def worker_queued(
        self,
        job_id: str,
        *,
        phase: str,
        model: str,
        retry_code: str | None = None,
    ) -> None:
        """Append one queued worker attempt, inferring retry ordinal by job id."""

        if retry_code is not None and retry_code not in _RETRY_CODES:
            raise ValueError(f"unsupported diagnostics retry code: {retry_code}")
        queued_at = _utc_timestamp(self._clock.utc_now())
        queued_ns = self._clock.monotonic_ns()

        def mutate(payload: dict) -> None:
            prior_attempts = [
                job for job in payload["jobs"] if job["job_id"] == job_id
            ]
            if any(
                job["status"] in {"queued", "running"}
                for job in prior_attempts
            ):
                raise ValueError(
                    f"cannot queue retry with an active prior attempt: {job_id}"
                )
            retry = len(prior_attempts)
            if retry > 0 and retry_code is None:
                raise ValueError(
                    f"diagnostics retry code is required after attempt one: {job_id}"
                )
            job = {
                "job_id": job_id,
                "phase": phase,
                "model": model,
                "retry": retry,
                "status": "queued",
                "queued_at_utc": queued_at,
                "queued_monotonic_ns": queued_ns,
            }
            if retry_code is not None:
                job["retry_code"] = retry_code
            payload["jobs"].append(job)
            payload["counts"]["jobs"] = len(payload["jobs"])
            payload["counts"]["retries"] = sum(
                item["retry"] > 0 for item in payload["jobs"]
            )

        self._update(mutate)

    def worker_started(self, job_id: str) -> None:
        """Mark the most recent queued attempt started and record its queue time."""

        started_at = _utc_timestamp(self._clock.utc_now())
        started_ns = self._clock.monotonic_ns()

        def mutate(payload: dict) -> None:
            job = self._active_job(payload, job_id, expected="queued")
            job["status"] = "running"
            job["started_at_utc"] = started_at
            job["started_monotonic_ns"] = started_ns
            job["queue_ms"] = _elapsed_ms(job["queued_monotonic_ns"], started_ns)

        self._update(mutate)

    def worker_finished(
        self,
        job_id: str,
        *,
        status: str,
        output: Path | None = None,
        error: BaseException | None = None,
        error_code: str = "worker-failed",
    ) -> None:
        """Finish the active attempt while preserving prior attempts and outputs."""

        if status not in {"success", "failure"}:
            raise ValueError("worker status must be success or failure")
        if error_code not in _WORKER_ERROR_CODES:
            raise ValueError(f"unsupported diagnostics worker error code: {error_code}")
        if status == "success" and output is None:
            raise ValueError("successful worker diagnostics requires an output artifact")
        finished_at = _utc_timestamp(self._clock.utc_now())
        finished_ns = self._clock.monotonic_ns()

        def mutate(payload: dict) -> None:
            job = self._active_job(
                payload,
                job_id,
                expected="running" if status == "success" else ("queued", "running"),
            )
            job["status"] = status
            job["finished_at_utc"] = finished_at
            if "started_monotonic_ns" in job:
                job["worker_ms"] = _elapsed_ms(job["started_monotonic_ns"], finished_ns)
            elif "queue_ms" not in job:
                job["queue_ms"] = _elapsed_ms(job["queued_monotonic_ns"], finished_ns)
            if output is not None:
                kind = (
                    "inventory-fragment"
                    if job["phase"] == "inventory"
                    else "semantic-fragment"
                )
                artifact, is_new = self._artifact_record(
                    output,
                    kind=kind,
                    phase=job["phase"],
                    counts=None,
                    artifacts=payload["artifacts"],
                )
                if is_new:
                    payload["artifacts"].append(artifact)
                job["output_artifact"] = artifact["id"]
            if status == "failure":
                category = "validation" if error_code == "validation-failed" else "worker"
                job["diagnostic"] = _diagnostic_record(
                    error_code,
                    category,
                    error,
                )
                if error_code == "validation-failed":
                    payload["counts"]["validation_errors"] += 1

        self._update(mutate)

    @staticmethod
    def _active_job(
        payload: dict,
        job_id: str,
        *,
        expected: str | tuple[str, ...],
    ) -> dict:
        """Return the newest matching active attempt in one allowed state."""

        expected_states = (expected,) if isinstance(expected, str) else expected
        for job in reversed(payload["jobs"]):
            if job["job_id"] == job_id and job["status"] in expected_states:
                return job
        allowed = ", ".join(expected_states)
        raise ValueError(f"no {allowed} diagnostics attempt for job {job_id}")

    @contextmanager
    def stage(
        self,
        operation: str,
        *,
        inputs: Iterable[Path] = (),
        outputs: Iterable[Path] = (),
        validation: bool = False,
    ) -> Iterator[None]:
        """Time one deterministic operation, persist its result, and re-raise errors."""

        started_at = _utc_timestamp(self._clock.utc_now())
        started_ns = self._clock.monotonic_ns()
        input_paths = [str(Path(path).resolve()) for path in inputs]
        output_paths = [str(Path(path).resolve()) for path in outputs]
        stage_index = len(self.payload["stages"])

        def begin(payload: dict) -> None:
            record = {
                "operation": operation,
                "category": "validation" if validation else "operation",
                "status": "running",
                "started_at_utc": started_at,
            }
            if input_paths:
                record["input_paths"] = input_paths
            if output_paths:
                record["output_paths"] = output_paths
            payload["stages"].append(record)

        self._update(begin)
        try:
            yield
        except BaseException as exc:
            finished_at = _utc_timestamp(self._clock.utc_now())
            finished_ns = self._clock.monotonic_ns()
            failure = exc

            def fail(payload: dict) -> None:
                record = payload["stages"][stage_index]
                record["status"] = "failure"
                record["finished_at_utc"] = finished_at
                record["duration_ms"] = _elapsed_ms(started_ns, finished_ns)
                record["diagnostic"] = _diagnostic_record(
                    "validation-failed" if validation else "stage-failed",
                    "validation" if validation else "stage",
                    failure,
                )
                if validation:
                    payload["counts"]["validation_errors"] += 1

            self._update(fail)
            raise
        else:
            finished_at = _utc_timestamp(self._clock.utc_now())
            finished_ns = self._clock.monotonic_ns()

            def succeed(payload: dict) -> None:
                record = payload["stages"][stage_index]
                record["status"] = "success"
                record["finished_at_utc"] = finished_at
                record["duration_ms"] = _elapsed_ms(started_ns, finished_ns)

            self._update(succeed)

    def record_artifact(
        self,
        path: Path,
        *,
        kind: str,
        phase: str,
        counts: dict | None = None,
    ) -> dict:
        """Append one byte size, SHA-256, record-count, and provenance record."""

        captured: dict = {}

        def mutate(payload: dict) -> None:
            record, is_new = self._artifact_record(
                path,
                kind=kind,
                phase=phase,
                counts=counts,
                artifacts=payload["artifacts"],
            )
            if is_new:
                payload["artifacts"].append(record)
            captured.update(record)

        self._update(mutate)
        return captured

    def record_ratio(
        self,
        kind: str,
        *,
        numerator: dict,
        denominator: dict,
        job_id: str | None = None,
        numerator_bytes: int | None = None,
        denominator_bytes: int | None = None,
        measurement_basis: str = "physical-artifact-bytes",
        stage_attempt: str = "initial",
    ) -> dict:
        """Record or deduplicate one basis-explicit byte ratio."""

        measured_numerator = (
            int(numerator["bytes"]) if numerator_bytes is None else int(numerator_bytes)
        )
        measured_denominator = (
            int(denominator["bytes"])
            if denominator_bytes is None
            else int(denominator_bytes)
        )
        if measured_numerator < 0 or measured_denominator <= 0:
            raise ValueError("diagnostics ratio denominator must be positive")
        ratio = {
            "kind": kind,
            "numerator_artifact": numerator["id"],
            "denominator_artifact": denominator["id"],
            "numerator_bytes": measured_numerator,
            "denominator_bytes": measured_denominator,
            "measurement_basis": measurement_basis,
            "stage_attempt": stage_attempt,
            "value": measured_numerator / measured_denominator,
        }
        if job_id is not None:
            ratio["job_id"] = job_id
        def mutate(payload: dict) -> None:
            identity = (
                kind,
                job_id,
                measurement_basis,
                stage_attempt,
            )
            for existing in payload["ratios"]:
                existing_identity = (
                    existing["kind"],
                    existing.get("job_id"),
                    existing["measurement_basis"],
                    existing["stage_attempt"],
                )
                if existing_identity == identity:
                    if existing != ratio:
                        raise ValueError("diagnostics ratio identity changed across retry")
                    return
            payload["ratios"].append(ratio)

        self._update(mutate)
        return ratio

    def record_validation_diagnostics(self, diagnostics: list[dict]) -> None:
        """Persist only allowlisted validation codes and structural record paths."""

        safe: list[dict] = []
        for item in diagnostics:
            code = item.get("code")
            path = item.get("path", [])
            if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", code):
                continue
            if not isinstance(path, list) or not all(
                isinstance(part, int)
                or (isinstance(part, str) and re.fullmatch(r"[a-z_][a-z0-9_]{0,79}", part))
                for part in path
            ):
                path = []
            record = {"code": code, "path": path}
            fields = item.get("fields")
            if isinstance(fields, list) and all(
                isinstance(field, str)
                and re.fullmatch(r"[a-z_][a-z0-9_]{0,79}", field)
                for field in fields
            ):
                record["fields"] = sorted(set(fields))
            safe.append(record)

        def mutate(payload: dict) -> None:
            for record in safe:
                if record not in payload["validation_diagnostics"]:
                    payload["validation_diagnostics"].append(record)

        self._update(mutate)

    def record_correction(self) -> None:
        """Increment the schema-owned localized correction aggregate."""

        self._update(
            lambda payload: payload["counts"].__setitem__(
                "corrections", payload["counts"]["corrections"] + 1
            )
        )

    def record_semantic_counts(self, semantic: dict) -> None:
        """Replace aggregate final semantic counts without retaining semantic records."""

        mapping = {
            "entities": "entities",
            "relationships": "relationships",
            "exclusions": "exclusions",
            "gaps": "gaps",
        }

        def mutate(payload: dict) -> None:
            for target, source in mapping.items():
                records = semantic.get(source, [])
                if not isinstance(records, list):
                    raise ValueError(f"semantic count source must be an array: {source}")
                payload["counts"][target] = len(records)
            resolutions = semantic.get("unresolved_resolutions", [])
            if not isinstance(resolutions, list):
                raise ValueError("semantic count source must be an array: unresolved_resolutions")
            payload["counts"]["unresolved_resolutions"] = len(resolutions)
            payload["counts"]["unresolved"] = sum(
                item.get("disposition") == "unresolved"
                for item in resolutions
                if isinstance(item, dict)
            )

        self._update(mutate)

    @staticmethod
    def _nonnegative_integer(value: object, *, field: str) -> int:
        """Normalize one bounded aggregate without accepting booleans or negatives."""

        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"iterator diagnostics {field} must be nonnegative")
        return value

    @classmethod
    def _iterator_timing_aggregate(cls, value: dict, *, field: str) -> dict:
        """Project one fixed-size timing aggregate onto its three numeric fields."""

        return {
            key: cls._nonnegative_integer(value[key], field=f"{field}.{key}")
            for key in ("samples", "total", "maximum")
        }

    def record_iterator_summary(self, summary: dict) -> None:
        """Replace iterator state detail with a fixed-size prose-free projection."""

        setup_source = summary["setup"]
        next_source = summary["next"]
        setup_timings = setup_source["internal_timings_ms"]
        next_timings = next_source["internal_timings_ms"]
        open_sequence = next_source["open_sequence"]
        setup = {
            "unit_count": self._nonnegative_integer(
                setup_source["unit_count"], field="setup.unit_count"
            ),
            "worker_count": self._nonnegative_integer(
                setup_source["worker_count"], field="setup.worker_count"
            ),
            "assigned_characters": self._nonnegative_integer(
                setup_source["assigned_characters"],
                field="setup.assigned_characters",
            ),
            "internal_timings_ms": {
                key: self._nonnegative_integer(
                    setup_timings[key], field=f"setup.internal_timings_ms.{key}"
                )
                for key in (
                    "scan",
                    "unitization",
                    "partition",
                    "database",
                    "validation",
                    "total",
                )
            },
        }
        next_summary = {
            key: self._nonnegative_integer(next_source[key], field=f"next.{key}")
            for key in (
                "calls",
                "acknowledgements",
                "wraps",
                "retries",
                "failures",
            )
        }
        next_summary["open_sequence"] = {
            key: self._nonnegative_integer(
                open_sequence[key], field=f"next.open_sequence.{key}"
            )
            for key in (
                "count",
                "unit_count",
                "character_count",
                "maximum_elapsed_ms",
            )
        }
        next_summary["internal_timings_ms"] = {
            key: self._iterator_timing_aggregate(
                next_timings[key], field=f"next.internal_timings_ms.{key}"
            )
            for key in (
                "validation",
                "transaction",
                "lookup",
                "serialization",
                "total",
            )
        }

        def mutate(payload: dict) -> None:
            prior = payload.get("iterator", {})
            prior_setup = prior.get("setup", {})
            prior_next = prior.get("next", {})
            if "controller_timings_ms" in prior_setup:
                setup["controller_timings_ms"] = deepcopy(
                    prior_setup["controller_timings_ms"]
                )
            if "controller_timings_ms" in prior_next:
                next_summary["controller_timings_ms"] = deepcopy(
                    prior_next["controller_timings_ms"]
                )
            payload["iterator"] = {
                "setup": deepcopy(setup),
                "next": deepcopy(next_summary),
            }

        self._update(mutate)

    def record_iterator_controller_timing(
        self,
        operation: str,
        *,
        process_dispatch_ms: int,
        total_ms: int,
        publication_ms: int | None = None,
    ) -> None:
        """Accumulate returned public-wrapper timing separately from iterator internal time."""

        if operation not in {"setup", "next"}:
            raise ValueError("iterator controller timing operation must be setup or next")
        if operation == "next" and publication_ms is not None:
            raise ValueError("iterator next controller timing cannot record publication_ms")
        measurements = {
            "process_dispatch": self._nonnegative_integer(
                process_dispatch_ms, field=f"{operation}.process_dispatch_ms"
            ),
            "total": self._nonnegative_integer(
                total_ms, field=f"{operation}.controller_total_ms"
            ),
        }
        if publication_ms is not None:
            measurements["publication"] = self._nonnegative_integer(
                publication_ms, field="setup.publication_ms"
            )

        def mutate(payload: dict) -> None:
            if "iterator" not in payload:
                raise ValueError(
                    "iterator summary must be recorded before controller timing"
                )
            target = payload["iterator"][operation].setdefault(
                "controller_timings_ms", {}
            )
            for name, value in measurements.items():
                aggregate = target.setdefault(
                    name, {"samples": 0, "total": 0, "maximum": 0}
                )
                aggregate["samples"] += 1
                aggregate["total"] += value
                aggregate["maximum"] = max(aggregate["maximum"], value)

        self._update(mutate)

    def finish(
        self,
        *,
        status: str,
        error: BaseException | None = None,
        error_code: str = "run-failed",
    ) -> None:
        """Record final status and total monotonic wall time for the run."""

        if status not in {"success", "failure"}:
            raise ValueError("run status must be success or failure")
        if self.payload["run"]["status"] != "running":
            raise ValueError("diagnostics run is already finished")
        if error_code not in _RUN_ERROR_CODES:
            raise ValueError(f"unsupported diagnostics run error code: {error_code}")
        if status == "success":
            if any(
                job["status"] not in {"success", "failure"}
                for job in self.payload["jobs"]
            ):
                raise ValueError(
                    "cannot finish successfully with a nonterminal worker attempt"
                )
            if any(
                stage["status"] not in {"success", "failure"}
                for stage in self.payload["stages"]
            ):
                raise ValueError(
                    "cannot finish successfully with a nonterminal deterministic stage"
                )
            latest_jobs: dict[str, str] = {}
            for job in self.payload["jobs"]:
                latest_jobs[job["job_id"]] = job["status"]
            incomplete = sorted(
                job_id for job_id, job_status in latest_jobs.items()
                if job_status != "success"
            )
            if incomplete:
                raise ValueError(
                    "cannot finish successfully with active or unrecovered jobs: "
                    + ", ".join(incomplete)
                )
        finished_at = _utc_timestamp(self._clock.utc_now())
        finished_ns = self._clock.monotonic_ns()

        def mutate(payload: dict) -> None:
            if status == "failure":
                for job in payload["jobs"]:
                    if job["status"] not in {"queued", "running"}:
                        continue
                    job["status"] = "failure"
                    job["finished_at_utc"] = finished_at
                    job["diagnostic"] = _diagnostic_record(
                        "diagnostics-write-failed", "worker"
                    )
                    if "queue_ms" not in job:
                        job["queue_ms"] = 0
                    if "started_at_utc" in job and "worker_ms" not in job:
                        job["worker_ms"] = 0
                for stage in payload["stages"]:
                    if stage["status"] != "running":
                        continue
                    stage["status"] = "failure"
                    stage["finished_at_utc"] = finished_at
                    stage["duration_ms"] = 0
                    stage["diagnostic"] = _diagnostic_record(
                        "diagnostics-write-failed", "stage"
                    )
            run = payload["run"]
            run["finished_at_utc"] = finished_at
            run["total_ms"] = _elapsed_ms(run["started_monotonic_ns"], finished_ns)
            run["status"] = status
            if status == "failure":
                category = (
                    "validation"
                    if error_code == "validation-failed"
                    else "stage" if error_code == "stage-failed" else "run"
                )
                diagnostic = _diagnostic_record(error_code, category, error)
                run["diagnostic"] = diagnostic
                run.setdefault("failure_history", []).append(
                    {
                        "finished_at_utc": finished_at,
                        "total_ms": run["total_ms"],
                        "diagnostic": deepcopy(diagnostic),
                    }
                )
            else:
                run.pop("diagnostic", None)

        self._update(mutate)

    def resume(self) -> None:
        """Explicitly resume a failed run while retaining its structured history."""

        if self.payload["run"]["status"] != "failure":
            raise ValueError("only a failed diagnostics run can be resumed")

        def mutate(payload: dict) -> None:
            run = payload["run"]
            run["status"] = "running"
            run.pop("finished_at_utc", None)
            run.pop("total_ms", None)
            run.pop("diagnostic", None)

        self._update(mutate, require_running=False)


def _summary(report: RunDiagnostics) -> dict:
    """Return a concise machine result without embedding report or artifact prose."""

    stages = report.payload["stages"]
    return {
        "diagnostics": str(report.path),
        "status": report.payload["run"]["status"],
        "jobs": report.payload["counts"]["jobs"],
        "latest_stage": (
            {
                key: stages[-1][key]
                for key in ("operation", "status", "duration_ms")
                if key in stages[-1]
            }
            if stages
            else None
        ),
    }


def main(argv: Iterable[str] | None = None) -> dict:
    """Record one diagnostics lifecycle operation from a machine interface call."""

    parser = argparse.ArgumentParser(description="Record durable graph run diagnostics.")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    initialize_parser = subparsers.add_parser("initialize")
    initialize_parser.add_argument("run_dir")
    initialize_parser.add_argument("--entrypoint", required=True)

    queued_parser = subparsers.add_parser("worker-queued")
    queued_parser.add_argument("run_dir")
    queued_parser.add_argument("job_id")
    queued_parser.add_argument("--phase", choices=("inventory", "extract"), required=True)
    queued_parser.add_argument("--model", required=True)
    queued_parser.add_argument("--retry-code", choices=sorted(_RETRY_CODES))

    started_parser = subparsers.add_parser("worker-started")
    started_parser.add_argument("run_dir")
    started_parser.add_argument("job_id")

    finished_parser = subparsers.add_parser("worker-finished")
    finished_parser.add_argument("run_dir")
    finished_parser.add_argument("job_id")
    finished_parser.add_argument("--status", choices=("success", "failure"), required=True)
    finished_parser.add_argument("--output")
    finished_parser.add_argument("--error-code", choices=sorted(_WORKER_ERROR_CODES))

    iterator_timing_parser = subparsers.add_parser("iterator-controller-timing")
    iterator_timing_parser.add_argument("run_dir")
    iterator_timing_parser.add_argument("iterator_operation", choices=("setup", "next"))
    iterator_timing_parser.add_argument("--process-dispatch-ms", required=True, type=int)
    iterator_timing_parser.add_argument("--publication-ms", type=int)
    iterator_timing_parser.add_argument("--total-ms", required=True, type=int)

    finish_parser = subparsers.add_parser("finish")
    finish_parser.add_argument("run_dir")
    finish_parser.add_argument("--status", choices=("success", "failure"), required=True)
    finish_parser.add_argument("--error-code", choices=sorted(_RUN_ERROR_CODES))
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.operation == "initialize":
        report = RunDiagnostics.initialize(
            Path(args.run_dir), entrypoint=Path(args.entrypoint)
        )
    else:
        report = RunDiagnostics.open(Path(args.run_dir))
        if args.operation == "worker-queued":
            report.worker_queued(
                args.job_id,
                phase=args.phase,
                model=args.model,
                retry_code=args.retry_code,
            )
        elif args.operation == "worker-started":
            report.worker_started(args.job_id)
        elif args.operation == "worker-finished":
            report.worker_finished(
                args.job_id,
                status=args.status,
                output=Path(args.output) if args.output else None,
                error_code=args.error_code or "worker-failed",
            )
        elif args.operation == "iterator-controller-timing":
            report.record_iterator_controller_timing(
                args.iterator_operation,
                process_dispatch_ms=args.process_dispatch_ms,
                publication_ms=args.publication_ms,
                total_ms=args.total_ms,
            )
        else:
            report.finish(
                status=args.status,
                error_code=args.error_code or "run-failed",
            )
    summary = _summary(report)
    print(json.dumps(summary, indent=2))
    return summary


class Interface(PythonArgvMachineInterface):
    """Expose diagnostics lifecycle events through the Python machine protocol."""

    prog = "run_diagnostics.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()
