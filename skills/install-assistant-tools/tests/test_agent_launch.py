from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))

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


def test_repo_root_resolves_three_levels_above_bin_file():
    # <repo>/skills/install-assistant-tools/bin/_agent_launch.py
    expected = Path(_agent_launch.__file__).resolve().parents[3]
    assert _agent_launch._repo_root() == expected
