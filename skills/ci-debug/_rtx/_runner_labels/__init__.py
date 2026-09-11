"""Expose runner-label classification through one generic package seam."""

from ._linux_macos_windows import classify_runner_labels

__all__ = ["classify_runner_labels"]
