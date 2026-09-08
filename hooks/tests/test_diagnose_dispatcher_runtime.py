"""Tests for the startup-only dispatcher runtime diagnostic hook."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _REPO_ROOT / "llmhooks" / "diagnose_dispatcher_runtime.py"
sys.path.insert(0, str(_REPO_ROOT))


def _load_hook_module():
    assert _HOOK.is_file()
    spec = importlib.util.spec_from_file_location(
        "llmhooks.diagnose_dispatcher_runtime", _HOOK
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_missing_runtime_reuses_launcher_diagnosis(tmp_path: Path) -> None:
    module = _load_hook_module()
    data_home = tmp_path / "data"

    diagnosis = module.diagnose_dispatcher_runtime(
        environment={"XDG_DATA_HOME": str(data_home)}, home=tmp_path
    )

    if sys.platform == "darwin":
        expected_root = tmp_path / "Library" / "Application Support" / "Famulus"
    elif sys.platform == "win32":
        expected_root = tmp_path / "AppData" / "Local" / "Famulus"
    else:
        expected_root = data_home / "famulus"
    expected_python = expected_root / "dispatcher-runtime" / "venv" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    assert diagnosis == (
        "error: Famulus MCP startup's dedicated dispatcher runtime is missing at "
        f"{expected_python}.\n"
        "Possible clues:\n"
        "- Use the `bootstrap-dispatcher-runtime` skill's core setup route.\n"
    )


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_launcher_diagnosis_is_emitted_verbatim_for_each_host(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    module = _load_hook_module()
    diagnosis = "error: exact launcher diagnosis\n"
    monkeypatch.setattr(
        module, "diagnose_dispatcher_runtime", lambda: diagnosis
    )
    hook = module.DiagnoseDispatcherRuntimeHook()
    hook_input = module.HookInput(
        host=host, event_name="SessionStart", source="startup", raw={}
    )

    output = hook.emit(hook_input, hook.build(hook_input))

    assert output == {
        "systemMessage": diagnosis,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": diagnosis,
        }
    }


def test_healthy_runtime_emits_no_additional_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_hook_module()
    monkeypatch.setattr(module, "diagnose_dispatcher_runtime", lambda: None)
    hook = module.DiagnoseDispatcherRuntimeHook()
    hook_input = module.HookInput(
        host="codex", event_name="SessionStart", source="startup", raw={}
    )

    output = hook.emit(hook_input, hook.build(hook_input))

    assert output == {"hookSpecificOutput": {"hookEventName": "SessionStart"}}


def test_binding_is_startup_only_for_codex_and_claude() -> None:
    hook = _load_hook_module().DiagnoseDispatcherRuntimeHook()

    for host in ("codex", "claude"):
        binding = hook.install_binding(host, "/repo/llmhooks/diagnose_dispatcher_runtime.py")
        assert binding.event == "SessionStart"
        assert binding.matcher == "startup"
        assert binding.argv[-1] == f"--{host}"


def test_registry_contains_runtime_diagnostic_for_both_hosts() -> None:
    from llmhooks.registry import hooks_for_host

    for host in ("codex", "claude"):
        names = {registered.hook_class.hook_name for registered in hooks_for_host(host)}
        assert "diagnose-dispatcher-runtime" in names


def test_background_profile_registers_startup_only_diagnostic() -> None:
    payload = json.loads(
        (_REPO_ROOT / "profiles" / "background_run_claude_setting.json").read_text(
            encoding="utf-8"
        )
    )
    registration = next(
        item
        for item in payload["hooks"]["SessionStart"]
        if item["matcher"] == "startup"
    )
    hook = registration["hooks"][0]

    assert hook["command"] == "python"
    assert hook["args"] == [
        "${FAMULUS_LAUNCHER_RESOURCES}/llmhooks/diagnose_dispatcher_runtime.py",
        "--claude",
    ]


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux hook command")
@pytest.mark.parametrize("plugin_root_variable", ["PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"])
def test_packaged_registration_selects_each_host_with_minimal_environment(
    tmp_path: Path, plugin_root_variable: str
) -> None:
    payload = json.loads(
        (_REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    registration = next(
        item
        for item in payload["hooks"]["SessionStart"]
        if item["matcher"] == "startup"
    )
    command = registration["hooks"][0]["command"]
    python_bin = tmp_path / "bin"
    python_bin.mkdir()
    (python_bin / "python").symlink_to(sys.executable)
    data_home = tmp_path / "data"

    result = subprocess.run(
        command,
        shell=True,
        input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
        text=True,
        capture_output=True,
        env={
            plugin_root_variable: str(_REPO_ROOT),
            "PATH": str(python_bin),
            "HOME": str(tmp_path),
            "XDG_DATA_HOME": str(data_home),
        },
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "dedicated dispatcher runtime is missing" in output[
        "hookSpecificOutput"
    ]["additionalContext"]


def test_missing_platform_selector_exits_nonzero() -> None:
    result = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="{}",
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
