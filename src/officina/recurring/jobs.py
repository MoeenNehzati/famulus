from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence


_JOB_NAME = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?\Z")
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul", "clock$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_JOB_FIELDS = {"name", "description", "command", "backend", "schedule", "enabled", "success"}
_SUCCESS_FIELDS = {"require_inner_status", "ignore_exit_codes", "ignore_exit_log_patterns"}


def validate_job_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or _JOB_NAME.fullmatch(value) is None
        or value.casefold() in _RESERVED_NAMES
    ):
        raise ValueError(f"job name is not a portable canonical identifier: {value!r}")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"job {field} must be a non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"job {field} must not contain control characters")
    return value


def validate_job(job: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(job, Mapping) or set(job) - _JOB_FIELDS:
        raise ValueError("recurring job has unexpected fields")
    selected = dict(job)
    selected["name"] = validate_job_name(selected.get("name"))
    selected["command"] = _text(selected.get("command"), field="command")
    selected["schedule"] = _text(selected.get("schedule"), field="schedule")
    if "description" in selected:
        selected["description"] = _text(selected["description"], field="description")
    if not isinstance(selected.get("enabled"), bool):
        raise ValueError("job enabled must be boolean")
    if selected.get("backend") not in (None, "claude", "codex"):
        raise ValueError("job backend must be claude or codex")
    success = selected.get("success")
    if success is not None:
        if not isinstance(success, Mapping) or set(success) - _SUCCESS_FIELDS:
            raise ValueError("job success policy has unexpected fields")
        inner = success.get("require_inner_status")
        if inner is not None and inner != "ok":
            raise ValueError("job success require_inner_status must be ok")
        codes = success.get("ignore_exit_codes", [])
        if not isinstance(codes, list) or any(isinstance(code, bool) or not isinstance(code, int) for code in codes):
            raise ValueError("job success ignore_exit_codes must contain integers")
        patterns = success.get("ignore_exit_log_patterns", [])
        if not isinstance(patterns, list):
            raise ValueError("job success ignore_exit_log_patterns must be a list")
        for pattern in patterns:
            _text(pattern, field="success pattern")
    return selected


def validate_jobs_payload(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, Mapping) or set(payload) != {"jobs"}:
        raise ValueError("canonical jobs file must contain only a jobs list")
    jobs = payload.get("jobs")
    if not isinstance(jobs, Sequence) or isinstance(jobs, (str, bytes)):
        raise ValueError("canonical jobs file has no valid jobs list")
    selected = [validate_job(job) for job in jobs]
    names = [str(job["name"]) for job in selected]
    if len(names) != len(set(names)):
        raise ValueError("canonical jobs file contains duplicate job names")
    return selected


def confined_child(root: Path, name: str) -> Path:
    selected = root / name
    resolved_root = root.resolve(strict=False)
    resolved = selected.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"recurring path escapes its owned root: {selected}") from exc
    return selected


__all__ = ["confined_child", "validate_job", "validate_job_name", "validate_jobs_payload"]
