#!/usr/bin/env python3
"""Tests for the crontab injection/removal logic in test-job.py."""
import tempfile, os
from pathlib import Path

# Import the helpers directly — test-job.py exposes inject_test_block / remove_test_block
import importlib.util, sys

SCRIPT = Path(__file__).parent / "test-job.py"
spec = importlib.util.spec_from_file_location("test_job", SCRIPT)

TEST_BEGIN = "# --- claude-recurring TEST BEGIN (temporary) ---"
TEST_END   = "# --- claude-recurring TEST END ---"

def load_module():
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_inject_adds_test_block():
    mod = load_module()
    existing = "MAILTO=\"\"\n15 * * * * /some/script.sh\n"
    result = mod.inject_test_block(existing, "59 23", "/usr/bin/echo hi", "/tmp/test.log")
    assert TEST_BEGIN in result
    assert TEST_END in result
    assert "echo hi" in result
    assert "/some/script.sh" in result  # existing preserved
    print("PASS: inject adds test block, preserves existing")

def test_remove_strips_test_block():
    mod = load_module()
    with_block = f"MAILTO=\"\"\n{TEST_BEGIN}\n* * * * * echo hi >> /tmp/t.log 2>&1\n{TEST_END}\n# after\n"
    result = mod.remove_test_block(with_block)
    assert TEST_BEGIN not in result
    assert TEST_END not in result
    assert "# after" in result
    assert "MAILTO" in result
    print("PASS: remove strips test block, preserves surrounding content")

def test_remove_idempotent_when_no_block():
    mod = load_module()
    clean = "MAILTO=\"\"\n15 * * * * /some/script.sh\n"
    result = mod.remove_test_block(clean)
    assert result == clean
    print("PASS: remove is idempotent when no test block exists")

if __name__ == "__main__":
    test_inject_adds_test_block()
    test_remove_strips_test_block()
    test_remove_idempotent_when_no_block()
    print("\nAll tests passed.")
