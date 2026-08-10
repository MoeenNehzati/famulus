"""Opt-in fixture setup timing probe for benchmark attribution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest


def _filename_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", value)


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """Append a fixture setup record when the caller enabled the probe."""
    start = time.monotonic()
    outcome = yield
    directory = os.environ.get("OFFICINA_FIXTURE_PROBE_DIR")
    if not directory:
        return
    run_id = os.environ["OFFICINA_FIXTURE_PROBE_RUN_ID"]
    task_id = os.environ.get("OFFICINA_FIXTURE_PROBE_TASK_ID", "")
    workerinput = getattr(request.config, "workerinput", {})
    worker_id = workerinput.get("workerid", "master")
    record = {
        "run_id": run_id, "task_id": task_id, "worker_id": worker_id, "process_id": os.getpid(),
        "fixture": fixturedef.argname, "scope": fixturedef.scope, "node_id": request.node.nodeid,
        "elapsed_seconds": time.monotonic() - start, "outcome": "error" if outcome.excinfo else "passed",
    }
    task_name = f"{_filename_component(task_id)}-{hashlib.sha256(task_id.encode()).hexdigest()[:12]}"
    filename = f"{_filename_component(run_id)}-{task_name}-{os.getpid()}-{_filename_component(worker_id)}.jsonl"
    target = Path(directory) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
