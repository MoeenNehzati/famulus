from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import install


def test_plugin_mode_skips_dev_link(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: calls.append(("dev_link", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))

    install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    names = [name for name, _ in calls]
    assert names == ["scaffold", "launchers"]


def test_dev_mode_requires_repo_path_non_interactively(tmp_path, monkeypatch):
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: None)
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: None)
    monkeypatch.setattr(install.launchers, "run", lambda **kw: None)

    import pytest
    with pytest.raises(SystemExit):
        install.run(
            home=tmp_path, dry_run=True, non_interactive=True,
            dev_mode=True, repo_path=None, agents=[], default_llm="claude",
        )


def test_dev_mode_with_repo_path_chains_dev_link(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.dev_link, "run", lambda **kw: calls.append(("dev_link", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))
    repo_path = tmp_path / "myrepo"

    install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=True, repo_path=repo_path, agents=["assistant"], default_llm="codex",
    )

    names = [name for name, _ in calls]
    assert names == ["scaffold", "dev_link", "launchers"]
    dev_link_kwargs = dict(calls[1][1])
    assert dev_link_kwargs["repo_root"] == repo_path
    launchers_kwargs = dict(calls[2][1])
    assert launchers_kwargs["agents"] == ["assistant"]
    assert launchers_kwargs["default_llm"] == "codex"


def test_plugin_mode_uses_auto_derived_repo_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install.scaffold, "run", lambda **kw: calls.append(("scaffold", kw)))
    monkeypatch.setattr(install.launchers, "run", lambda **kw: calls.append(("launchers", kw)))

    install.run(
        home=tmp_path, dry_run=True, non_interactive=True,
        dev_mode=False, agents=[], default_llm="claude",
    )

    scaffold_kwargs = dict(calls[0][1])
    # Auto-derived from install.py's own location: <repo>/skills/install-assistant-tools/scripts/install.py
    expected_repo_root = Path(install.__file__).resolve().parents[3]
    assert scaffold_kwargs["repo_root"] == expected_repo_root
