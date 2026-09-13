"""Best-effort timing traces for Dispatcher process and interface boundaries."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

from officina.common.atomic_files import atomic_create_bytes, ensure_private_directory


TRACE_ENV = "FAMULUS_TRACE_ID"
PARENT_ENV = "FAMULUS_PARENT_SPAN_ID"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_trace: ContextVar[str | None] = ContextVar("dispatch_trace", default=None)
_parent: ContextVar[str | None] = ContextVar("dispatch_parent_span", default=None)


def _valid(value: object) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


@contextmanager
def invocation_trace() -> Iterator[str]:
    """Create one trace identity for a public non-dry-run invocation."""

    trace_id = uuid4().hex
    trace_token, parent_token = _trace.set(trace_id), _parent.set(None)
    try:
        yield trace_id
    finally:
        _parent.reset(parent_token)
        _trace.reset(trace_token)


def trace_environment() -> dict[str, str]:
    """Return only validated correlation values for a child process."""

    trace_id, parent_id = _valid(_trace.get()), _valid(_parent.get())
    if trace_id is None:
        return {}
    return {TRACE_ENV: trace_id} | ({PARENT_ENV: parent_id} if parent_id else {})


def trace_process(function: Callable) -> Callable:
    """Trace the shared resolved-process executor without changing its signature."""

    @wraps(function)
    def traced(resolved, *args, **kwargs):
        with span("process", caller=resolved.caller_module_id, interface=resolved.target) as finish:
            resolved.env.update(trace_environment())
            result = function(resolved, *args, **kwargs)
            finish(result.returncode)
            return result
    return traced


def _write(record: dict[str, object]) -> None:
    try:
        root = Path(os.environ.get("ASSISTANT_LOGS") or Path.home() / ".assistant-logs").expanduser().resolve()
        day = datetime.fromtimestamp(record["wall_started_ns"] / 1_000_000_000, timezone.utc).strftime("%Y-%m-%d")
        directory = root / "dispatch" / day / str(record["trace_id"])
        ensure_private_directory(directory, allowed_root=root)
        atomic_create_bytes(
            directory / f"{record['span_id']}.json",
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            allowed_root=root,
            mode=0o600,
        )
    except Exception:
        pass


@contextmanager
def span(
    layer: str,
    *,
    caller: str | None = None,
    interface: str | None = None,
) -> Iterator[Callable[[int], None]]:
    """Activate and persist one allowlisted completion span when tracing is active."""

    local_trace, inherited_parent = _valid(_trace.get()), os.environ.get(PARENT_ENV)
    trace_id = local_trace or _valid(os.environ.get(TRACE_ENV))
    parent_id = (_valid(_parent.get()) if local_trace else _valid(inherited_parent))
    if local_trace is None and inherited_parent is not None and parent_id is None:
        trace_id = None
    if trace_id is None:
        yield lambda _code: None
        return
    span_id = uuid4().hex
    wall_started_ns, started_ns = time.time_ns(), time.perf_counter_ns()
    trace_token, parent_token = _trace.set(trace_id), _parent.set(span_id)
    exit_code: list[int] = []
    outcome = "raised"
    try:
        yield exit_code.append
        outcome = ("success" if not exit_code or exit_code[-1] == 0 else
                   "killed" if caller is not None and exit_code[-1] < 0 else "nonzero")
    except BaseException as exc:
        outcome = "timeout" if getattr(exc, "code", None) == "dispatcher.execution_timeout" else "raised"
        raise
    finally:
        record: dict[str, object] = {
            "schema": 1, "layer": layer, "trace_id": trace_id,
            "span_id": span_id, "parent_span_id": parent_id,
            "wall_started_ns": wall_started_ns,
            "monotonic_started_ns": started_ns,
            "duration_ns": time.perf_counter_ns() - started_ns,
            "outcome": outcome,
        }
        if caller is not None:
            record.update(caller=caller, interface=interface)
        if exit_code and caller is not None:
            record["exit_code"] = exit_code[-1]
        _parent.reset(parent_token)
        _trace.reset(trace_token)
        _write(record)
