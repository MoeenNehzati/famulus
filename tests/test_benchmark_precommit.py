from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark-precommit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("benchmark_precommit", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wrapper_delegates_precommit_arguments_to_general_harness(monkeypatch) -> None:
    benchmark = load_module()
    calls = []

    class Harness:
        @staticmethod
        def main(argv):
            calls.append(argv)
            return 0

    monkeypatch.setattr(benchmark, "_load_harness", lambda: Harness)

    assert benchmark.main(["--repo", ".", "--output", "out.json", "--runs", "1", "--cache", "warm", "--jobs", "2"]) == 0
    assert calls == [["--repo", ".", "--output", "out.json", "--runs", "1", "--cache", "warm", "--jobs", "2", "--suite", "precommit"]]


def test_wrapper_uses_process_arguments_when_none_given(monkeypatch) -> None:
    benchmark = load_module()
    calls = []

    class Harness:
        @staticmethod
        def main(argv):
            calls.append(argv)
            return 0

    monkeypatch.setattr(benchmark, "_load_harness", lambda: Harness)
    monkeypatch.setattr(sys, "argv", ["benchmark-precommit.py", "--help"])

    assert benchmark.main() == 0
    assert calls == [["--help", "--suite", "precommit"]]
