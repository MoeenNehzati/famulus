"""Tests for the cross-host dispatcher-context hook entrypoint.

These tests validate the hook payload contract for explicit --codex /
--claude / --cursor entrypoints and the shared install metadata exposed by the
hook class. They do not prove that a host attached the hook to a session. That
requires host-observed hook telemetry; the plugin install shard currently has
that for Claude via hook_started/hook_response events, but not for Codex.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


_HOOK = Path(__file__).resolve().parents[2] / "llmhooks" / "inject_dispatcher_context.py"
_REPO_ROOT = _HOOK.parents[1]
sys.path.insert(0, str(_HOOK.parents[1]))
_spec = importlib.util.spec_from_file_location("llmhooks.inject_dispatcher_context", _HOOK)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


_DISPATCHER_CONTEXT_MARKERS = [
    "## Skill System — Module Boundaries",
    "Do not invoke these scripts directly.",
    "dispatcher --caller-skill <caller> <callee> <interface-id> [args...]",
    "Use --dry-run to preview without executing.",
    "`skill-maker` skill",
]


def _assert_dispatcher_context(text: str) -> None:
    missing = [marker for marker in _DISPATCHER_CONTEXT_MARKERS if marker not in text]
    assert missing == []


def _available(*, cli: bool = True, pkg: bool = True):
    missing = []
    if not cli:
        missing.append("dispatcher CLI not on PATH")
    if not pkg:
        missing.append("script_dispatcher Python package not importable")
    return patch.object(_mod, "dispatcher_available", return_value=(not missing, missing))


def _env_with_generated_dispatcher(tmp_path: Path) -> dict[str, str]:
    installer_dir = _REPO_ROOT / "skills" / "install-assistant-tools" / "_rtx"
    installer_dir_str = str(installer_dir)
    inserted = installer_dir_str not in sys.path
    if inserted:
        sys.path.insert(0, installer_dir_str)
    try:
        from _install_launcher import platform_launcher_installer
    finally:
        if inserted:
            sys.path.remove(installer_dir_str)

    bin_dir = tmp_path / "bin"
    result = platform_launcher_installer().install_dispatcher_launcher(
        _REPO_ROOT,
        bin_dir,
        dry_run=False,
        manifest=None,
    )
    assert not result.blocks_install(), result.reason
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


class TestHookMetadata:
    def test_shared_binding_metadata_is_exposed(self):
        hook = _mod.InjectDispatcherContextHook()
        assert hook.event == "SessionStart"
        assert hook.matcher == "startup|clear|compact"
        assert hook.resolved_event("codex") == "SessionStart"
        assert hook.resolved_event("claude") == "SessionStart"
        assert hook.resolved_matcher("codex") == "startup|clear|compact"

    def test_install_binding_uses_explicit_platform_flag(self):
        hook = _mod.InjectDispatcherContextHook()
        binding = hook.install_binding("codex", "/repo/llmhooks/inject_dispatcher_context.py")
        assert binding.event == "SessionStart"
        assert binding.matcher == "startup|clear|compact"
        assert binding.argv[-1] == "--codex"


class TestOutputs:
    def test_codex_output_is_nested_hook_specific_output(self):
        hook = _mod.InjectDispatcherContextHook()
        with _available():
            result = hook.build(_mod.HookInput(host="codex", event_name="SessionStart", source="startup", raw={}))
        output = hook.codex_output(
            _mod.HookInput(host="codex", event_name="SessionStart", source="startup", raw={}),
            result,
        )
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])

    def test_claude_output_matches_same_nested_shape(self):
        hook = _mod.InjectDispatcherContextHook()
        with _available():
            result = hook.build(_mod.HookInput(host="claude", event_name="SessionStart", source="startup", raw={}))
        output = hook.claude_output(
            _mod.HookInput(host="claude", event_name="SessionStart", source="startup", raw={}),
            result,
        )
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])

    def test_cursor_output_uses_snake_case(self):
        hook = _mod.InjectDispatcherContextHook()
        with _available():
            result = hook.build(_mod.HookInput(host="cursor", event_name="SessionStart", source="startup", raw={}))
        output = hook.cursor_output(
            _mod.HookInput(host="cursor", event_name="SessionStart", source="startup", raw={}),
            result,
        )
        assert "additional_context" in output
        _assert_dispatcher_context(output["additional_context"])

    def test_missing_dispatcher_emits_system_message(self):
        hook = _mod.InjectDispatcherContextHook()
        with _available(cli=False, pkg=True):
            result = hook.build(_mod.HookInput(host="codex", event_name="SessionStart", source="startup", raw={}))
        output = hook.codex_output(
            _mod.HookInput(host="codex", event_name="SessionStart", source="startup", raw={}),
            result,
        )
        assert "systemMessage" in output["hookSpecificOutput"]
        assert "dispatcher CLI" in output["hookSpecificOutput"]["systemMessage"]


class TestEntryPoint:
    def _run_script(
        self,
        *args: str,
        stdin_obj: dict | None = None,
        env_base: dict[str, str] | None = None,
        env_overrides: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(env_base) if env_base is not None else os.environ.copy()
        env["PYTHONPATH"] = str(_HOOK.parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
        if env_overrides:
            env.update(env_overrides)
        result = subprocess.run(
            [sys.executable, str(_HOOK), *args],
            input=json.dumps(stdin_obj) if stdin_obj is not None else "",
            capture_output=True,
            text=True,
            env=env,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"script exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def test_codex_entrypoint_emits_valid_json_with_nested_output(self, tmp_path):
        result = self._run_script(
            "--codex",
            stdin_obj={"hook_event_name": "SessionStart", "source": "startup"},
            env_base=_env_with_generated_dispatcher(tmp_path),
        )
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])

    def test_claude_entrypoint_is_stable_under_noisy_env(self, tmp_path):
        result = self._run_script(
            "--claude",
            stdin_obj={"hook_event_name": "SessionStart", "source": "startup"},
            env_base=_env_with_generated_dispatcher(tmp_path),
            env_overrides={"CLAUDECODE": "", "CLAUDE_PLUGIN_ROOT": "", "COPILOT_CLI": "1"},
        )
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])

    def test_missing_platform_selector_exits_nonzero(self):
        result = self._run_script(check=False)
        assert result.returncode != 0


class TestPluginShim:
    _SHIM = Path(__file__).resolve().parents[1] / "inject_dispatcher_context.py"

    def _run_shim(self, env_overrides: dict[str, str], tmp_path: Path) -> dict:
        env = _env_with_generated_dispatcher(tmp_path)
        env["PYTHONPATH"] = str(self._SHIM.parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
        env.update(env_overrides)
        result = subprocess.run(
            [sys.executable, str(self._SHIM)],
            input='{"hook_event_name":"SessionStart","source":"startup"}',
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_plugin_root_selects_codex_shape_without_explicit_flag(self, tmp_path):
        output = self._run_shim({"PLUGIN_ROOT": "/tmp/plugin", "CLAUDE_PLUGIN_ROOT": ""}, tmp_path)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])

    def test_claude_plugin_root_selects_claude_shape_without_explicit_flag(self, tmp_path):
        output = self._run_shim({"PLUGIN_ROOT": "", "CLAUDE_PLUGIN_ROOT": "/tmp/plugin"}, tmp_path)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
