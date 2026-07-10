#!/usr/bin/env python3
"""Behavior tests for the per-host parser files and their __init__.py aggregation."""
import importlib
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent


def _load(name):
    for module_name in list(sys.modules):
        if module_name == "_rtx" or module_name.startswith("_rtx."):
            sys.modules.pop(module_name, None)
    sys.path.insert(0, str(SKILL_DIR))
    try:
        return importlib.import_module(f"_rtx._{name}")
    finally:
        sys.path.pop(0)


def test_claude_parser_home_dir_respects_env_override(monkeypatch):
    mod = _load("claude_parser")
    monkeypatch.setenv("CLAUDE_HOME", "/tmp/fake-claude-home")
    parser = mod.ClaudeParser()
    assert parser.home_dir() == "/tmp/fake-claude-home"


def test_claude_parser_home_dir_default(monkeypatch):
    mod = _load("claude_parser")
    monkeypatch.delenv("CLAUDE_HOME", raising=False)
    parser = mod.ClaudeParser()
    assert parser.home_dir().endswith("/.claude")


def test_claude_parser_extract_project_and_session_id():
    mod = _load("claude_parser")
    parser = mod.ClaudeParser()
    assert parser.extract_project({"cwd": "/home/x/project"}) == "/home/x/project"
    assert parser.extract_project({}) is None
    assert parser.extract_session_id("/some/path/abc123.jsonl", None) == "abc123"
    assert parser.resume_hint("abc123") == "/resume abc123"


def test_codex_parser_home_dir_respects_env_override(monkeypatch):
    mod = _load("codex_parser")
    monkeypatch.setenv("CODEX_HOME", "/tmp/fake-codex-home")
    parser = mod.CodexParser()
    assert parser.home_dir() == "/tmp/fake-codex-home"


def test_codex_parser_home_dir_default(monkeypatch):
    mod = _load("codex_parser")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    parser = mod.CodexParser()
    assert parser.home_dir().endswith("/.codex")


def test_codex_parser_extract_project_is_nested_under_payload():
    mod = _load("codex_parser")
    parser = mod.CodexParser()
    assert parser.extract_project({"payload": {"cwd": "/home/x/project"}}) == "/home/x/project"
    assert parser.extract_project({"cwd": "/home/x/project"}) is None  # not nested -> not found
    assert parser.extract_project({}) is None


def test_codex_parser_extract_session_id_prefers_payload_then_falls_back():
    mod = _load("codex_parser")
    parser = mod.CodexParser()
    first_obj = {"payload": {"session_id": "abc-123"}}
    assert parser.extract_session_id("/some/path/rollout-xyz.jsonl", first_obj) == "abc-123"

    first_obj_id_only = {"payload": {"id": "def-456"}}
    assert parser.extract_session_id("/some/path/rollout-xyz.jsonl", first_obj_id_only) == "def-456"

    assert parser.extract_session_id("/some/path/rollout-xyz.jsonl", None) == "rollout-xyz"
    assert parser.extract_session_id("/some/path/rollout-xyz.jsonl", {}) == "rollout-xyz"


def test_codex_parser_resume_hint_has_no_leading_slash():
    mod = _load("codex_parser")
    parser = mod.CodexParser()
    assert parser.resume_hint("abc123") == "resume abc123"


def test_init_aggregates_both_parsers_with_distinct_ids():
    for module_name in list(sys.modules):
        if module_name == "_rtx" or module_name.startswith("_rtx."):
            sys.modules.pop(module_name, None)
    sys.path.insert(0, str(SKILL_DIR))
    try:
        mod = importlib.import_module("_rtx")
    finally:
        sys.path.pop(0)

    assert len(mod.parsers) == 2
    ids = sorted(p.id for p in mod.parsers)
    assert ids == ["claude", "codex"]
    # Each parser exposes the shared interface scan.py relies on.
    for p in mod.parsers:
        assert hasattr(p, "home_dir")
        assert hasattr(p, "list_session_files")
        assert hasattr(p, "extract_project")
        assert hasattr(p, "extract_session_id")
        assert hasattr(p, "resume_hint")
        assert hasattr(p, "opaque_field")
        assert hasattr(p, "default_threshold")


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
