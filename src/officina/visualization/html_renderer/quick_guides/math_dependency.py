from __future__ import annotations

from .default import DEFAULT_QUICK_GUIDE


MATH_DEPENDENCY_QUICK_GUIDE = (
    DEFAULT_QUICK_GUIDE
    .replace_step(
        "read-graph",
        title="Read mathematical dependencies",
        body="Follow arrows from prerequisites toward the results they support.",
    )
    .replace_step(
        "trace-ancestors",
        title="Trace prerequisites",
        body="Select a theorem, then use this control to add its prerequisites.",
    )
)

__all__ = ["MATH_DEPENDENCY_QUICK_GUIDE"]
