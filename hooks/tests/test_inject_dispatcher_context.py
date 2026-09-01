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
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[2] / "llmhooks" / "inject_dispatcher_context.py"
_REPO_ROOT = _HOOK.parents[1]
sys.path.insert(0, str(_HOOK.parents[1]))
_spec = importlib.util.spec_from_file_location("llmhooks.inject_dispatcher_context", _HOOK)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


_DISPATCHER_CONTEXT_MARKERS = [
    "## Skill interfaces",
    "`famulus` MCP server",
    "invocation metadata and `Arguments JSON`",
    "do not invoke private scripts directly",
    "`Instruction Interfaces` are LLM-readable instructions",
]


def _assert_dispatcher_context(text: str) -> None:
    missing = [marker for marker in _DISPATCHER_CONTEXT_MARKERS if marker not in text]
    assert missing == []
    assert "dispatcher --caller-skill" not in text
    assert "Officina" not in text
    assert len(text) <= 750


def test_dispatcher_context_defers_availability_check_until_first_use() -> None:
    """Break caught: SessionStart triggers an eager Famulus MCP probe."""
    text = _mod.DISPATCHER_CORE

    assert "At session start" not in text
    assert "only when an executable interface is needed" in text


def test_both_plugin_manifests_register_the_shared_hook_file() -> None:
    """Break caught: one packaged host silently stops loading shared hooks."""
    for manifest_path in (
        _REPO_ROOT / ".claude-plugin" / "plugin.json",
        _REPO_ROOT / ".codex-plugin" / "plugin.json",
    ):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["hooks"] == "./hooks/hooks.json"


@pytest.mark.parametrize("plugin_root_variable", ["CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"])
def test_packaged_hook_command_runs_without_separate_args(
    tmp_path: Path, plugin_root_variable: str
) -> None:
    """Break caught: Codex ignores a separate args field for command hooks."""
    payload = json.loads((_REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    hook = payload["hooks"]["SessionStart"][0]["hooks"][0]
    python_bin = tmp_path / "bin"
    python_bin.mkdir()
    (python_bin / "python").symlink_to(sys.executable)

    result = subprocess.run(
        hook["command"],
        shell=True,
        input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
        text=True,
        capture_output=True,
        env={plugin_root_variable: str(_REPO_ROOT), "PATH": str(python_bin)},
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])


def test_background_profile_declares_common_python_and_shared_hook() -> None:
    """Break caught: background SessionStart still selects a host-specific Python."""
    payload = json.loads(
        (_REPO_ROOT / "profiles" / "background_run_claude_setting.json").read_text(
            encoding="utf-8"
        )
    )
    hook = payload["hooks"]["SessionStart"][0]["hooks"][0]

    assert hook["command"] == "python"
    assert hook["args"] == [
        "${FAMULUS_LAUNCHER_RESOURCES}/llmhooks/inject_dispatcher_context.py",
        "--claude",
    ]


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
        assert binding.argv[0] == "python"
        assert binding.argv[-1] == "--codex"


class TestOutputs:
    def test_global_guidance_is_bounded_and_ignores_local_vocabulary(self):
        text = _mod.DISPATCHER_CORE
        assert len(_mod.DISPATCHER_CORE) <= 500
        assert len(text) <= 750
        assert "--stdin" not in text
        assert "provider-skill" not in text
        assert "tmp" not in text
        assert "retry" not in text

    def test_global_guidance_does_not_depend_on_vocabulary(self):
        text = _mod.DISPATCHER_CORE
        assert "--stdin" not in text
        assert "provider-skill" not in text

    @pytest.mark.skipif(sys.platform != "linux", reason="native Linux evidence")
    def test_linux_launches_shared_hook_with_literal_python_and_space_path(self, tmp_path: Path):
        plugin = tmp_path / "plugin root with spaces"
        shutil.copytree(_REPO_ROOT / "llmhooks", plugin / "llmhooks")
        python_bin = tmp_path / "bin"
        python_bin.mkdir()
        (python_bin / "python").symlink_to(sys.executable)
        result = subprocess.run(
            ["python", str(plugin / "llmhooks" / "inject_dispatcher_context.py"), "--claude"],
            input="{}",
            text=True,
            capture_output=True,
            check=True,
            env={"PATH": str(python_bin)},
        )
        assert json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def test_codex_output_is_nested_hook_specific_output(self):
        hook = _mod.InjectDispatcherContextHook()
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
        result = hook.build(_mod.HookInput(host="cursor", event_name="SessionStart", source="startup", raw={}))
        output = hook.cursor_output(
            _mod.HookInput(host="cursor", event_name="SessionStart", source="startup", raw={}),
            result,
        )
        assert "additional_context" in output
        _assert_dispatcher_context(output["additional_context"])

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

    def test_codex_entrypoint_emits_valid_json_with_nested_output(self):
        result = self._run_script(
            "--codex",
            stdin_obj={"hook_event_name": "SessionStart", "source": "startup"},
        )
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])

    def test_claude_entrypoint_is_stable_under_noisy_env(self):
        result = self._run_script(
            "--claude",
            stdin_obj={"hook_event_name": "SessionStart", "source": "startup"},
            env_overrides={"CLAUDECODE": "", "CLAUDE_PLUGIN_ROOT": "", "COPILOT_CLI": "1"},
        )
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        _assert_dispatcher_context(output["hookSpecificOutput"]["additionalContext"])

    def test_missing_platform_selector_exits_nonzero(self):
        result = self._run_script(check=False)
        assert result.returncode != 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
