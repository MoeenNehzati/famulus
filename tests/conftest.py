"""Serialize Chrome-backed tests in the top-level ``tests/`` suite."""
from __future__ import annotations

from pathlib import Path
import time

import pytest


@pytest.fixture(autouse=True)
def serialize_browser_tests(request, tmp_path_factory):
    """Serialize Chrome-backed tests across pytest-xdist workers.

    Headless Chrome allocates process descriptors and inotify watchers even
    for file-only pages. Running several browser modules concurrently can
    exhaust those host resources before the graph DOM initializes, producing
    false missing-node failures. All workers share the parent of their
    per-worker base temp directory, so an atomic directory creation provides a
    portable invocation-local lock without changing collection or coverage.
    """
    test_path = Path(str(request.node.path))
    if not test_path.name.endswith("_browser.py"):
        yield
        return

    lock = tmp_path_factory.getbasetemp().parent / "chrome-tests.lock"
    deadline = time.monotonic() + 120
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                pytest.fail("timed out waiting for the shared Chrome test lock")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.rmdir()
