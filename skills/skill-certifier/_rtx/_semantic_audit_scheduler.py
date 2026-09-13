"""Pure readiness and report validation reused by certification machine evolutions."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError


class SchedulerError(ValueError):
    """An audit task or result does not satisfy the deterministic contract."""


_REPORT_SCHEMA = json.loads(
    (Path(__file__).resolve().parent / "schemas" / "semantic-audit-result.schema.json").read_text()
)
_REPORT_VALIDATOR = Draft202012Validator(_REPORT_SCHEMA)


def ready_tasks(nodes, required, audited, assigned, capacity):
    """Choose dependency-ready tasks without owning a second workflow state."""

    if type(capacity) is not int or not 0 <= capacity <= 64:
        raise SchedulerError("capacity must be an integer between 0 and 64")
    slots = max(0, capacity - len(assigned - audited))
    return [
        target for target in sorted(required - audited - assigned)
        if all(dependency in audited or dependency not in required
               for dependency in nodes[target]["dependencies"])
    ][:slots]


def _validate_report(report: object, task_id: str) -> None:
    """Validate canonical worker JSON and its machine-assigned identity."""

    try:
        _REPORT_VALIDATOR.validate(report)
    except ValidationError as error:
        raise SchedulerError("invalid semantic audit report: " + error.message) from error
    if report["task_id"] != task_id:
        raise SchedulerError("semantic audit task mismatch")
