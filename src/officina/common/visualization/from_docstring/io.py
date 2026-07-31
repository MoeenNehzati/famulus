#!/usr/bin/env python3
"""IO helpers for docstring graph extraction artifacts."""

from __future__ import annotations

import json
from pathlib import Path


def write_dependency_json(document: dict[str, object], target_path: Path) -> Path:
    """Write dependency JSON in canonical form."""
    target_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return target_path


def default_out_dir(target: Path) -> Path:
    """Compute default output directory."""
    if target.is_file():
        return target.parent / "graphs"
    return target / "graphs"


def gather_modules(target: Path) -> list[Path]:
    """Collect a single module target."""
    return [target]


def gather_modules_in_directory(target: Path, include_tests: bool) -> list[Path]:
    """Collect Python modules under a directory."""
    modules: list[Path] = []
    for path in sorted(target.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        if path.name.startswith("."):
            continue
        if "graphs" in path.parts:
            continue
        if not include_tests and path.name.startswith("test_"):
            continue
        if ".git" in path.parts:
            continue
        modules.append(path)
    return modules


__all__ = [
    "default_out_dir",
    "gather_modules",
    "gather_modules_in_directory",
    "write_dependency_json",
]
