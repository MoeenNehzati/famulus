from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/bin"))

import _agent_launch


def test_parse_agent_md_extracts_description_and_prompt(tmp_path):
    repo_root = tmp_path / "repo"
    (repo_root / "agents").mkdir(parents=True)
    (repo_root / "agents" / "assistant.md").write_text(
        "---\n"
        "name: assistant\n"
        "description: Personal assistant for task management.\n"
        "---\n"
        "\n"
        "# Personal Assistant\n"
        "\n"
        "You are a personal assistant.\n",
        encoding="utf-8",
    )

    description, prompt = _agent_launch._parse_agent_md(repo_root, "assistant")

    assert description == "Personal assistant for task management."
    assert prompt.startswith("# Personal Assistant")
    assert "You are a personal assistant." in prompt


def test_parse_agent_md_handles_missing_frontmatter(tmp_path):
    repo_root = tmp_path / "repo"
    (repo_root / "agents").mkdir(parents=True)
    (repo_root / "agents" / "bare.md").write_text("Just a plain prompt.\n", encoding="utf-8")

    description, prompt = _agent_launch._parse_agent_md(repo_root, "bare")

    assert description == ""
    assert prompt == "Just a plain prompt."


def test_repo_root_matches_repository_containing_runtime_test():
    expected = Path(__file__).resolve().parents[4]
    assert _agent_launch._repo_root() == expected


def test_worker_dir_uses_ai_env_var_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("AI", str(tmp_path / "live-checkout"))

    result = _agent_launch._worker_dir("assistant")

    assert result == tmp_path / "live-checkout" / "workers" / "assistant"


def test_worker_dir_falls_back_to_famulus_paths_when_ai_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("AI", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = _agent_launch._worker_dir("assistant")

    assert "Documents" not in str(result)
    assert result != _agent_launch._repo_root() / "workers" / "assistant"


def _codex_command(monkeypatch, tmp_path, agent="assistant"):
    """Return the argv the codex backend would exec, without execing it."""
    monkeypatch.setenv("AI", str(tmp_path))
    captured: list[list[str]] = []
    monkeypatch.setattr(
        _agent_launch.os, "execvp", lambda _file, argv: captured.append(argv)
    )
    monkeypatch.setattr(sys, "platform", "linux")
    _agent_launch.launch(agent=agent, default_backend="codex", args=["--local", "exec"])
    return captured[0]


def test_codex_backend_supplies_the_agent_instructions_at_launch(monkeypatch, tmp_path):
    """The instructions path must be resolved now, not stored at install time.

    A path baked into the profile config is a cache with no invalidation: when
    the repo moved, Codex kept resolving the old location, failed to read the
    file, and every scheduled job died seconds after starting. Nothing could
    repair it, because the installer treats an existing profile copy as
    machine-local state.

    The claude backend already resolves its agent definition at launch. This
    makes codex symmetric, which is what removes the stale-path failure mode
    rather than detecting it later.
    """
    argv = _codex_command(monkeypatch, tmp_path)

    expected = f"model_instructions_file={tmp_path / 'agents' / 'assistant.md'}"
    assert "-c" in argv
    assert expected in argv
    # The override must precede the profile it is overriding.
    assert argv.index("-c") < argv.index("--profile")


def test_codex_instructions_override_survives_a_repo_that_moved(monkeypatch, tmp_path):
    """Whatever a previously-installed profile config says is irrelevant."""
    moved = tmp_path / "new-location"
    (moved / "agents").mkdir(parents=True)
    monkeypatch.setenv("AI", str(moved))
    captured: list[list[str]] = []
    monkeypatch.setattr(
        _agent_launch.os, "execvp", lambda _file, argv: captured.append(argv)
    )
    monkeypatch.setattr(sys, "platform", "linux")

    _agent_launch.launch(agent="collab", default_backend="codex", args=["--local"])

    assert f"model_instructions_file={moved / 'agents' / 'collab.md'}" in captured[0]


def test_codex_profile_settings_are_passed_explicitly(tmp_path):
    """`--profile` is not enough, and its failure is silent.

    Codex accepts an unknown profile without warning and falls back to the
    global config, and no [profiles.*] section has ever been written here, so
    profiles/<agent>.config.toml never took effect. A scheduled run therefore
    inherited whatever the machine's global model and effort happened to be --
    the opposite of the isolation a per-agent profile is supposed to give.
    """
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "profiles" / "background_run.config.toml").write_text(
        'model_instructions_file = "agents/background_run.md"\n'
        'model = "some-capable-model"\n'
        'model_reasoning_effort = "high"\n',
        encoding="utf-8",
    )

    argv = _agent_launch._codex_profile_overrides(repo_root, "background_run")

    assert argv.count("-c") == 2
    assert "model=some-capable-model" in argv
    assert "model_reasoning_effort=high" in argv
    # Passed separately as an absolute path; the relative value in the file
    # would resolve against $CODEX_HOME and point at nothing.
    assert not any(a.startswith("model_instructions_file=") for a in argv)


def test_other_agents_profiles_are_left_inert(tmp_path):
    """Switching the interactive agents over is a separate migration: their
    profiles name models that may no longer exist, so activating them as a
    side effect of fixing the scheduler could break them."""
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "profiles" / "collab.config.toml").write_text(
        'model = "some-older-model"\n', encoding="utf-8"
    )

    assert _agent_launch._codex_profile_overrides(repo_root, "collab") == []


def test_missing_profile_file_is_not_fatal(tmp_path):
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)

    assert _agent_launch._codex_profile_overrides(repo_root, "background_run") == []
    assert _agent_launch._codex_profile_overrides(None, "background_run") == []
