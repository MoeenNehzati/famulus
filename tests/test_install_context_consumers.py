from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MILESTONE_WRITER = ROOT / "skills" / "milestone-logging" / "_rtx" / "_milestone_writer.py"
AGENT_TIMELINE = ROOT / "skills" / "milestone-logging" / "_rtx" / "_agent_timeline.py"


def _load_source(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_package(name: str, relative: str):
    """Load one skill-private package under a unique name.

    Every skill names its runtime package `_rtx`, so importing one by that
    name binds whichever skill is imported first for the whole process. A
    repository-level test cannot use the relative imports the package's own
    modules use, so it selects the package by path instead.
    """
    root = ROOT / relative
    spec = importlib.util.spec_from_file_location(
        name, root / "__init__.py", submodule_search_locations=[str(root)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_WAKEUP = _load_package("llm_wakeup_rtx", "skills/llm-wakeup/_rtx")
policies = importlib.import_module("llm_wakeup_rtx._wakeup_policies")
store = importlib.import_module("llm_wakeup_rtx._wakeup_store")
ClaudeAdapter = importlib.import_module("llm_wakeup_rtx._wakeup_providers._provider_claude").ClaudeAdapter
CodexAdapter = importlib.import_module("llm_wakeup_rtx._wakeup_providers._provider_codex").CodexAdapter


DEVELOPMENT_ACTIVATION = _load_source(
    "task11_consumer_development_activation",
    "skills/dev-activation/_rtx/_development_activation.py",
)


def _development_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    checkout = tmp_path / "checkout with spaces"
    checkout.mkdir()
    host = tmp_path / "host"
    host.mkdir()
    base = {
        "HOME": str(host),
        "PATH": os.environ.get("PATH", ""),
        "DISPLAY": ":17",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/17/bus",
    }
    return (
        DEVELOPMENT_ACTIVATION.build_activation_environment(
            checkout, environ=base, platform=sys.platform
        ),
        checkout,
    )


def test_milestone_follows_checkout_home_and_process_override(tmp_path: Path) -> None:
    """Catch fallback to the host home after general runtime deletion."""
    env, checkout = _development_env(tmp_path)
    env.update({"CODEX_SESSION_ID": "consumer-test", "CODEX_THREAD_ID": "thread"})
    result = subprocess.run(
        [sys.executable, str(MILESTONE_WRITER), "--path"],
        env=env,
        cwd=checkout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    assert Path(result.stdout.strip()).is_relative_to(checkout / ".famulus")

    override = tmp_path / "process-only-logs"
    env["ASSISTANT_LOGS"] = str(override)
    result = subprocess.run(
        [sys.executable, str(MILESTONE_WRITER), "--path"],
        env=env,
        cwd=checkout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    assert Path(result.stdout.strip()).is_relative_to(override)


def test_agent_timeline_reads_only_checkout_selected_logs(tmp_path: Path) -> None:
    env, checkout = _development_env(tmp_path)
    env["ASSISTANT_LOGS"] = str(checkout / ".famulus" / "assistant-logs")
    selected = Path(env["ASSISTANT_LOGS"]) / "2026-08-22"
    selected.mkdir(parents=True)
    (selected / "selected.session.jsonl").write_text("{}\n", encoding="utf-8")
    host = tmp_path / "host" / ".assistant-logs" / "2026-08-22"
    host.mkdir(parents=True)
    (host / "host.session.jsonl").write_text("{}\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(AGENT_TIMELINE), "--list"],
        env=env,
        cwd=checkout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )

    assert "selected" in result.stdout
    assert "host" not in result.stdout


def test_wakeup_state_and_provider_discovery_use_checkout_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, checkout = _development_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LLM_WAKEUP_HOME", raising=False)
    monkeypatch.delenv("LLM_WAKEUP_CODEX_DIR", raising=False)
    monkeypatch.delenv("LLM_WAKEUP_CLAUDE_DIR", raising=False)

    assert store.data_dir().is_relative_to(checkout / ".famulus")
    assert policies._read_policies() == {}
    assert CodexAdapter().transcript_root() == Path(env["CODEX_HOME"]) / "sessions"
    assert ClaudeAdapter().transcript_root() == Path(env["CLAUDE_CONFIG_DIR"]) / "projects"


def test_handoff_parsers_use_selected_assistant_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _checkout = _development_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    codex = _load_source(
        "task11_handoff_codex",
        "skills/find-handoff-candidates/_rtx/_codex_parser.py",
    )
    claude = _load_source(
        "task11_handoff_claude",
        "skills/find-handoff-candidates/_rtx/_claude_parser.py",
    )

    assert Path(codex.CodexParser().home_dir()) == Path(env["CODEX_HOME"])
    assert Path(claude.ClaudeParser().home_dir()) == Path(env["CLAUDE_CONFIG_DIR"])
