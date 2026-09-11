"""Classify CI runner labels for the supported operating-system families."""

from __future__ import annotations

from typing import Any


def classify_runner_labels(labels: list[str]) -> dict[str, Any]:
    """Classify runner hosting and OS from exact GitHub job-label evidence.

    Intent
    ------
    Expose hosted, self-hosted, unknown, and conflicting evidence explicitly.

    Rationale
    ---------
    Runner display names are not structured platform evidence, while exact
    labels can support a bounded classification without causal inference.

    Pseudocode
    ----------
    - set operation = `normalize exact labels for matching`
    - set operation = `collect hosting and operating-system matches`
    - set operation = `classify absent unique and conflicting evidence`
    - return classifications with their exact matching labels

    Wraps
    -----
    - none
    """
    exact_labels = sorted(str(label) for label in labels)
    hosting_matches: list[dict[str, str]] = []
    os_matches: list[dict[str, str]] = []
    for label in exact_labels:
        normalized = label.strip().casefold()
        if normalized == "self-hosted":
            hosting_matches.append({"label": label, "classification": "self-hosted"})
        hosted_os = next(
            (
                name
                for prefix, name in (
                    ("ubuntu-", "linux"),
                    ("windows-", "windows"),
                    ("macos-", "macos"),
                )
                if normalized.startswith(prefix)
            ),
            None,
        )
        if hosted_os is not None:
            hosting_matches.append({"label": label, "classification": "hosted"})
            os_matches.append({"label": label, "classification": hosted_os})
        elif normalized in {"linux", "windows", "macos"}:
            os_matches.append({"label": label, "classification": normalized})

    hosting_values = {item["classification"] for item in hosting_matches}
    os_values = {item["classification"] for item in os_matches}
    return {
        "runner_hosting_classification": (
            next(iter(hosting_values))
            if len(hosting_values) == 1
            else "conflicting" if hosting_values else "unknown"
        ),
        "runner_os_classification": (
            next(iter(os_values))
            if len(os_values) == 1
            else "conflicting" if os_values else "unknown"
        ),
        "runner_classification_basis": {
            "source": "github_job_labels",
            "labels": exact_labels,
            "hosting_matches": hosting_matches,
            "os_matches": os_matches,
        },
    }
