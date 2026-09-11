"""Focused tests for CI runner-label classification."""

from __future__ import annotations

import sys
from pathlib import Path


RTX = Path(__file__).resolve().parents[1]
if str(RTX) not in sys.path:
    sys.path.insert(0, str(RTX))

from _runner_labels import classify_runner_labels


def test_classification_keeps_unknown_self_hosted_and_conflicts_distinct() -> None:
    unknown = classify_runner_labels(["gpu"])
    assert unknown["runner_hosting_classification"] == "unknown"
    assert unknown["runner_os_classification"] == "unknown"

    self_hosted = classify_runner_labels(["self-hosted", "Windows"])
    assert (
        self_hosted["runner_hosting_classification"],
        self_hosted["runner_os_classification"],
    ) == ("self-hosted", "windows")

    conflicting = classify_runner_labels(
        ["self-hosted", "ubuntu-latest", "Windows"]
    )
    assert (
        conflicting["runner_hosting_classification"],
        conflicting["runner_os_classification"],
    ) == ("conflicting", "conflicting")


def test_classification_reports_exact_label_evidence_basis() -> None:
    classified = classify_runner_labels(["x64", "ubuntu-latest"])
    assert classified["runner_classification_basis"] == {
        "source": "github_job_labels",
        "labels": ["ubuntu-latest", "x64"],
        "hosting_matches": [
            {"label": "ubuntu-latest", "classification": "hosted"}
        ],
        "os_matches": [
            {"label": "ubuntu-latest", "classification": "linux"}
        ],
    }
