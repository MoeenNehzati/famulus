#!/usr/bin/env python3
"""Compatibility wrapper for precommit-suite benchmarks."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_harness() -> ModuleType:
    path = Path(__file__).with_name("benchmark-test-suite.py")
    spec = importlib.util.spec_from_file_location("benchmark_test_suite", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_jobs() -> int:
    """Expose the generalized harness's historical default worker budget."""
    return _load_harness().default_jobs()


def main(argv: list[str] | None = None) -> int:
    """Delegate the legacy command line to the fixed precommit suite."""
    arguments = sys.argv[1:] if argv is None else argv
    return _load_harness().main([*arguments, "--suite", "precommit"])


if __name__ == "__main__":
    raise SystemExit(main())
