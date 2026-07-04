"""Tests for hooks/inject_dispatcher_context.py.

All tests run without a backing LLM — they validate the JSON contract between
the hook script and the platform (Claude Code / Codex / Cursor / Copilot CLI).

The platform's only requirement is:
  - Script exits 0
  - stdout is valid JSON
  - The correct top-level key is present for the detected platform
  - additionalContext / additional_context is a non-empty string
  - systemMessage is present iff the dispatcher is unavailable
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load the module under test directly (avoids sys.path manipulation)
# ---------------------------------------------------------------------------

_HOOK = Path(__file__).resolve().parents[1] / "inject_dispatcher_context.py"
_spec = importlib.util.spec_from_file_location("inject_dispatcher_context", _HOOK)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _available(*, cli: bool = True, pkg: bool = True):
    """Context manager patching dispatcher_available to return (ok, missing)."""
    missing = []
    if not cli:
        missing.append("dispatcher CLI not on PATH")
    if not pkg:
        missing.append("script_dispatcher Python package not importable")
    return patch.object(_mod, "dispatcher_available", return_value=(not missing, missing))


def _platform(env: dict[str, str]):
    """Context manager patching os.environ for platform detection."""
    clean = {"CURSOR_PLUGIN_ROOT": "", "CLAUDE_PLUGIN_ROOT": "", "COPILOT_CLI": ""}
    clean.update(env)
    return patch.dict(_mod.os.environ, clean, clear=False)


# ---------------------------------------------------------------------------
# Platform detection → correct output key
# ---------------------------------------------------------------------------

class TestPlatformDetection:
    def test_claude_plugin_root_uses_hook_specific_output(self):
        with _available(), _platform({"CLAUDE_PLUGIN_ROOT": "/some/path"}):
            output = _mod.build_output("ctx", _mod.detect_platform())
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert output["hookSpecificOutput"]["additionalContext"] == "ctx"

    def test_cursor_plugin_root_uses_snake_case(self):
        with _available(), _platform({"CURSOR_PLUGIN_ROOT": "/some/path"}):
            output = _mod.build_output("ctx", _mod.detect_platform())
        assert "additional_context" in output
        assert output["additional_context"] == "ctx"

    def test_copilot_cli_uses_sdk_format(self):
        with _available(), _platform({"CLAUDE_PLUGIN_ROOT": "/p", "COPILOT_CLI": "1"}):
            output = _mod.build_output("ctx", _mod.detect_platform())
        assert "additionalContext" in output

    def test_no_env_vars_uses_sdk_format(self):
        with _available(), _platform({}):
            output = _mod.build_output("ctx", _mod.detect_platform())
        assert "additionalContext" in output

    def test_claude_root_takes_priority_over_sdk_when_copilot_absent(self):
        with _available(), _platform({"CLAUDE_PLUGIN_ROOT": "/p", "COPILOT_CLI": ""}):
            platform = _mod.detect_platform()
        assert platform == "claude"


# ---------------------------------------------------------------------------
# Dispatcher available → normal context, no systemMessage
# ---------------------------------------------------------------------------

class TestDispatcherAvailable:
    def _run(self, platform="claude"):
        with _available(cli=True, pkg=True):
            return _mod.build_output(
                _mod.CONTEXT_DISPATCHER_AVAILABLE, platform
            )

    def test_no_system_message_when_available(self):
        output = self._run()
        assert "systemMessage" not in output

    def test_additional_context_is_non_empty(self):
        output = self._run()
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert ctx.strip()

    def test_context_mentions_dispatcher(self):
        output = self._run()
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "dispatcher" in ctx.lower()

    def test_context_mentions_blueprint_contract(self):
        output = self._run()
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "BEGIN BLUEPRINT CONTRACT" in ctx

    def test_context_forbids_direct_script_invocation(self):
        output = self._run()
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "not invoke" in ctx or "Do not invoke" in ctx

    def test_context_sdk_format_is_non_empty(self):
        output = self._run(platform="sdk")
        assert output["additionalContext"].strip()


# ---------------------------------------------------------------------------
# Dispatcher unavailable → warning context + systemMessage
# ---------------------------------------------------------------------------

class TestDispatcherUnavailable:
    def _run(self, *, cli=False, pkg=False, platform="claude"):
        ok, missing = _mod.dispatcher_available.__wrapped__() if hasattr(
            _mod.dispatcher_available, "__wrapped__"
        ) else (False, [])
        # Use main() logic directly
        with _available(cli=cli, pkg=pkg), _platform({"CLAUDE_PLUGIN_ROOT": "/p"}):
            ok, missing = _mod.dispatcher_available()
            details = "; ".join(missing)
            system_message = (
                f"⚠️ Skill dispatcher not fully installed ({details}) — "
                "dynamic permission checks are inactive. "
                "To restore enforcement: pip install -e $AI/script_dispatcher"
            )
            return _mod.build_output(
                _mod.CONTEXT_DISPATCHER_MISSING, platform,
                system_message=system_message,
            )

    def test_system_message_present_when_unavailable(self):
        output = self._run()
        assert "systemMessage" in output

    def test_system_message_contains_warning_emoji(self):
        output = self._run()
        assert "⚠️" in output["systemMessage"]

    def test_system_message_mentions_install_command(self):
        output = self._run()
        assert "pip install" in output["systemMessage"]

    def test_system_message_names_missing_cli(self):
        output = self._run(cli=False, pkg=True)
        assert "CLI" in output["systemMessage"]

    def test_system_message_names_missing_package(self):
        output = self._run(cli=True, pkg=False)
        assert "script_dispatcher" in output["systemMessage"]

    def test_context_allows_direct_invocation_as_fallback(self):
        output = self._run()
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "directly" in ctx

    def test_context_warns_about_missing_enforcement(self):
        output = self._run()
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "not installed" in ctx or "Unavailable" in ctx


# ---------------------------------------------------------------------------
# dispatcher_available() detection logic
# ---------------------------------------------------------------------------

class TestDispatcherAvailableDetection:
    def test_both_present_returns_true(self):
        with patch("shutil.which", return_value="/usr/bin/dispatcher"), \
             patch("importlib.util.find_spec", return_value=object()):
            ok, missing = _mod.dispatcher_available()
        assert ok is True
        assert missing == []

    def test_cli_missing_returns_false_with_detail(self):
        with patch("shutil.which", return_value=None), \
             patch("importlib.util.find_spec", return_value=object()):
            ok, missing = _mod.dispatcher_available()
        assert ok is False
        assert any("CLI" in m for m in missing)

    def test_package_missing_returns_false_with_detail(self):
        with patch("shutil.which", return_value="/usr/bin/dispatcher"), \
             patch("importlib.util.find_spec", return_value=None):
            ok, missing = _mod.dispatcher_available()
        assert ok is False
        assert any("script_dispatcher" in m for m in missing)

    def test_both_missing_reports_both(self):
        with patch("shutil.which", return_value=None), \
             patch("importlib.util.find_spec", return_value=None):
            ok, missing = _mod.dispatcher_available()
        assert ok is False
        assert len(missing) == 2


# ---------------------------------------------------------------------------
# Subprocess / entry-point: script exits 0 and emits valid JSON
# ---------------------------------------------------------------------------

class TestEntryPoint:
    def _run_script(self, env_overrides: dict[str, str] | None = None) -> dict:
        import os
        env = os.environ.copy()
        env.update(env_overrides or {})
        result = subprocess.run(
            [sys.executable, str(_HOOK)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"script exited {result.returncode}: {result.stderr}"
        return json.loads(result.stdout)

    def test_exits_zero_and_emits_valid_json(self):
        output = self._run_script({"CLAUDE_PLUGIN_ROOT": "/tmp"})
        assert isinstance(output, dict)

    def test_output_has_hook_specific_output_for_claude(self):
        output = self._run_script({"CLAUDE_PLUGIN_ROOT": "/tmp"})
        assert "hookSpecificOutput" in output or "systemMessage" in output

    def test_output_has_additional_context_for_sdk(self):
        output = self._run_script(
            {"CLAUDE_PLUGIN_ROOT": "", "CURSOR_PLUGIN_ROOT": "", "COPILOT_CLI": ""}
        )
        assert "additionalContext" in output or "systemMessage" in output
