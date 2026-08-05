"""Record one pytest process without a divergent collection pass.

The benchmark harness activates this module as a pytest plugin and supplies
``OFFICINA_PYTEST_EXECUTION_REPORT``.  The value is a file-name template that
may contain ``{pid}``, allowing independently launched pytest groups to inherit
one environment while still producing distinct reports.  Every fact is
captured from pytest's live hooks: post-deselection collection, deselection
events, collection reports, and setup/call/teardown test reports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

import pytest


_STARTED_AT_NS = time.time_ns()
_COLLECTION_FINISHED = False
_SELECTED_NODEIDS: list[str] = []
_DESELECTED_NODEIDS: list[str] = []
_COLLECTION_REPORTS: list[dict[str, object]] = []
_TEST_REPORTS: list[dict[str, object]] = []


def _longrepr_text(longrepr: object) -> str | None:
    """Normalize a pytest long representation into optional readable text.

    Intent
    ------
    Preserve the human-readable reason from tuple and object report forms.

    Rationale
    ---------
    JSON reports require text while pytest exposes several long-representation shapes.

    Pseudocode
    ----------
    - if longrepr is none:
      - return none
    - if longrepr is a location tuple:
      - return its message element as text
    - return longrepr as text

    Wraps
    -----
    - none
    """
    if longrepr is None:
        return None
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Capture exact selected node IDs when live collection finishes.

    Intent
    ------
    Mark collection complete and record pytest's final post-deselection item list.

    Rationale
    ---------
    Live hook evidence avoids a divergent second collection pass in the benchmark.

    Pseudocode
    ----------
    - set collection_finished = true
    - set selected_nodeids = node identifiers from session items
    - return

    Wraps
    -----
    - none
    """
    global _COLLECTION_FINISHED
    _COLLECTION_FINISHED = True
    _SELECTED_NODEIDS[:] = [item.nodeid for item in session.items]


def pytest_deselected(items: list[pytest.Item]) -> None:
    """Capture node IDs removed by pytest or another live plugin.

    Intent
    ------
    Extend the execution record with every reported deselection event.

    Rationale
    ---------
    Deselected tests must remain distinguishable from tests that were never discovered.

    Pseudocode
    ----------
    - set deselected_nodeids = existing values plus node identifiers from items
    - return

    Wraps
    -----
    - none
    """
    _DESELECTED_NODEIDS.extend(item.nodeid for item in items)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Capture nonpassing collector outcomes from the live run.

    Intent
    ------
    Record skipped and failed collection reports with normalized explanations.

    Rationale
    ---------
    A completed pytest process can still omit tests because collection failed.

    Pseudocode
    ----------
    - if the collection report passed:
      - return
    - set collection_report = node outcome and normalized reason
    - set collection_reports = existing reports plus collection_report
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._longrepr_text:
      why:
        constructs: "Builds the normalized explanation stored in the collection report."
    """
    if report.outcome == "passed":
        return
    _COLLECTION_REPORTS.append(
        {
            "nodeid": report.nodeid,
            "outcome": report.outcome,
            "longrepr": _longrepr_text(report.longrepr),
        }
    )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Capture each setup, call, and teardown outcome with its duration.

    Intent
    ------
    Append one structured record for every live pytest test-phase report.

    Rationale
    ---------
    Phase-level records expose failures and timing without rerunning selected tests.

    Pseudocode
    ----------
    - set test_report = node phase outcome duration and optional skip reason
    - set test_reports = existing reports plus test_report
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._longrepr_text:
      why:
        transforms: "Normalizes the reason attached to a skipped test phase."
    """
    _TEST_REPORTS.append(
        {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "duration_seconds": report.duration,
            "skip_reason": (
                _longrepr_text(report.longrepr)
                if report.outcome == "skipped"
                else None
            ),
        }
    )


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int | pytest.ExitCode
) -> None:
    """Persist the execution record when pytest finishes its session.

    Intent
    ------
    Write collection and test-phase evidence to the configured per-process JSON path.

    Rationale
    ---------
    A PID-expanded template lets independently launched groups share configuration
    without overwriting one another; absence of the variable disables the plugin.

    Pseudocode
    ----------
    - set output_template = execution-report environment value
    - if output_template is absent:
      - return
    - set output = PID-expanded output path
    - set payload = session timing status collection and test reports
    - set output_state = persisted formatted execution payload
    - return

    Wraps
    -----
    - none
    """
    template = os.environ.get("OFFICINA_PYTEST_EXECUTION_REPORT")
    if not template:
        return
    output = Path(template.format(pid=os.getpid()))
    output.parent.mkdir(parents=True, exist_ok=True)
    numeric_exitstatus = int(exitstatus)
    collection_errors = [
        row for row in _COLLECTION_REPORTS if row["outcome"] == "failed"
    ]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "pid": os.getpid(),
        "started_at_ns": _STARTED_AT_NS,
        "finished_at_ns": time.time_ns(),
        "complete": True,
        "cancelled": numeric_exitstatus == int(pytest.ExitCode.INTERRUPTED),
        "exitstatus": numeric_exitstatus,
        "collection": {
            "finished": _COLLECTION_FINISHED,
            "selected_nodeids": _SELECTED_NODEIDS,
            "deselected_nodeids": _DESELECTED_NODEIDS,
            "reports": _COLLECTION_REPORTS,
            "errors": collection_errors,
        },
        "test_reports": _TEST_REPORTS,
    }
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
