"""Tests for the install manifest: recording at install time, replay at uninstall.

The manifest is the source of truth for uninstall. Key property: uninstall
removes exactly what install recorded — including symlinks pointing at a
*stale* root (e.g. an old plugin-cache version dir), which the heuristic
fallback cannot know about.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from install_test_utils import REPO_ROOT, can_create_symlink, python_test_env, run_command

SCRIPTS = REPO_ROOT / "skills" / "install-assistant-tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from install_manifest import Manifest, manifest_path  # noqa: E402

UNINSTALL = SCRIPTS / "uninstall.py"

pytestmark = pytest.mark.skipif(not can_create_symlink(), reason="symlinks unavailable")


# ── Manifest unit tests ───────────────────────────────────────────────────────

def test_manifest_round_trip(tmp_path: Path):
    path = tmp_path / "manifest.json"
    m = Manifest(path)
    m.record("symlink", path=str(tmp_path / "a"), target=str(tmp_path / "b"))
    m.record("file", path=str(tmp_path / "c"))
    m.save()
    loaded = Manifest(path)
    assert len(loaded.entries) == 2
    assert loaded.entries[0]["kind"] == "symlink"


def test_manifest_dedupes_on_kind_and_path(tmp_path: Path):
    m = Manifest(tmp_path / "manifest.json")
    m.record("symlink", path="/x", target="/old")
    m.record("symlink", path="/x", target="/new")
    assert len(m.entries) == 1
    assert m.entries[0]["target"] == "/new"


def test_manifest_path_is_under_home_state(tmp_path: Path):
    p = manifest_path(tmp_path)
    assert p == tmp_path / ".local" / "state" / "assistant-tools" / "install-manifest.json"


# ── Install-side recording ────────────────────────────────────────────────────

def _make_repo_for_manifest_tests(tmp_path: Path) -> Path:
    """Throwaway repo with the .githooks/llmhooks layout dev_link.run() needs.

    In-process run() MUST get a throwaway repo_root: dev_link now also writes
    into the repo (git hooksPath) and imports llmhooks from it — the default
    (or the real live repo) must never be used here.
    """
    import subprocess

    repo = tmp_path / "repo"
    (repo / "skills").mkdir(parents=True)
    (repo / "references").mkdir()
    (repo / "agents").mkdir()
    (repo / ".githooks").mkdir()
    (repo / "llmhooks").mkdir()
    (repo / "llmhooks" / "registry.py").write_text(
        "def hooks_for_host(host):\n    return []\n", encoding="utf-8"
    )
    (repo / "CLAUDE.md").write_text("repo instructions\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_setup_symlinks_records_links(tmp_path: Path):
    import dev_link

    repo = _make_repo_for_manifest_tests(tmp_path)
    claude_home = tmp_path / ".claude"
    manifest = Manifest(tmp_path / "manifest.json")
    saved_path = list(sys.path)
    saved_llmhooks = {
        name: mod for name, mod in sys.modules.items()
        if name == "llmhooks" or name.startswith("llmhooks.")
    }
    try:
        dev_link.run(
            repo_root=repo,
            home=tmp_path,
            claude_home=claude_home,
            do_claude=True,
            do_codex=False,
            dry_run=False,
            manifest=manifest,
        )
    finally:
        sys.path[:] = saved_path
        for name in [n for n in sys.modules if n == "llmhooks" or n.startswith("llmhooks.")]:
            del sys.modules[name]
        sys.modules.update(saved_llmhooks)
    recorded = {e["path"] for e in manifest.entries if e["kind"] == "symlink"}
    assert str(claude_home / "skills") in recorded
    assert str(claude_home / "CLAUDE.md") in recorded


def test_setup_symlinks_dry_run_records_nothing(tmp_path: Path):
    import dev_link

    repo = _make_repo_for_manifest_tests(tmp_path)
    manifest = Manifest(tmp_path / "manifest.json")
    saved_path = list(sys.path)
    saved_llmhooks = {
        name: mod for name, mod in sys.modules.items()
        if name == "llmhooks" or name.startswith("llmhooks.")
    }
    try:
        dev_link.run(
            repo_root=repo,
            home=tmp_path,
            claude_home=tmp_path / ".claude",
            do_claude=True,
            do_codex=False,
            dry_run=True,
            manifest=manifest,
        )
    finally:
        sys.path[:] = saved_path
        for name in [n for n in sys.modules if n == "llmhooks" or n.startswith("llmhooks.")]:
            del sys.modules[name]
        sys.modules.update(saved_llmhooks)
    assert manifest.entries == []


def test_rc_block_recorded(tmp_path: Path):
    import setup_tools

    rc = tmp_path / ".bashrc"
    manifest = Manifest(tmp_path / "manifest.json")
    setup_tools.ensure_rc_block(
        rc, tmp_path / "bin", "claude", REPO_ROOT, "user", False, manifest=manifest
    )
    blocks = [e for e in manifest.entries if e["kind"] == "marker_block"]
    assert any(e["path"] == str(rc) for e in blocks)


# ── Uninstall replay ──────────────────────────────────────────────────────────

def run_uninstall_with_home(home: Path, *extra: str, check: bool = True):
    env = python_test_env(home.parent)
    env["HOME"] = str(home)
    cmd = [
        sys.executable, str(UNINSTALL),
        "--home", str(home),
        "--claude-home", str(home / ".claude"),
        "--codex-home", str(home / ".codex"),
        "--bin-dir", str(home / "bin"),
        "--shell-rc", str(home / ".bashrc"),
        "--no-system-shell-rc", "--no-pip", "--no-git-hooks",
        *extra,
    ]
    return run_command(cmd, env=env, check=check)


def test_uninstall_replays_manifest_removing_stale_root_symlink(tmp_path: Path):
    """The drift case: link points at an old plugin-cache dir, not the current repo."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    old_root = tmp_path / "plugins-cache" / "old-version"
    old_root.mkdir(parents=True)
    (old_root / "skills").mkdir()
    link = home / ".claude" / "skills"
    link.symlink_to(old_root / "skills")

    m = Manifest(manifest_path(home))
    m.record("symlink", path=str(link), target=str(old_root / "skills"))
    m.save()

    run_uninstall_with_home(home)
    assert not link.is_symlink()


def test_uninstall_replay_skips_retargeted_symlink(tmp_path: Path):
    """A link the user re-pointed elsewhere since install must be preserved."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    users_dir = tmp_path / "users-own"
    users_dir.mkdir()
    link = home / ".claude" / "skills"
    link.symlink_to(users_dir)

    m = Manifest(manifest_path(home))
    m.record("symlink", path=str(link), target=str(tmp_path / "somewhere-else"))
    m.save()

    run_uninstall_with_home(home)
    assert link.is_symlink()


def test_uninstall_removes_manifest_after_clean_run(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    target = tmp_path / "t"
    target.mkdir()
    link = home / ".claude" / "skills"
    link.symlink_to(target)

    m = Manifest(manifest_path(home))
    m.record("symlink", path=str(link), target=str(target))
    m.save()

    run_uninstall_with_home(home)
    assert not manifest_path(home).exists()


def test_uninstall_keeps_failed_entries_in_manifest(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    ro_dir = home / "ro"
    ro_dir.mkdir()
    rc = ro_dir / "rc"
    rc.write_text("# >>> assistant-tools >>>\nx\n# <<< assistant-tools <<<\n")
    import os
    os.chmod(rc, 0o444)
    os.chmod(ro_dir, 0o555)

    m = Manifest(manifest_path(home))
    m.record(
        "marker_block", path=str(rc),
        begin="# >>> assistant-tools >>>", end="# <<< assistant-tools <<<",
    )
    m.save()

    try:
        result = run_uninstall_with_home(home, check=False)
        assert result.returncode != 0
        remaining = json.loads(manifest_path(home).read_text())
        assert any(e["path"] == str(rc) for e in remaining["entries"])
    finally:
        os.chmod(ro_dir, 0o755)
        os.chmod(rc, 0o644)


def test_full_install_writes_manifest(tmp_path: Path):
    """setup_tools.run records its side effects in the home-scoped manifest.

    Hook installation (json_hook_commands) has moved to dev_link.py — see
    test_dev_link.py / test_setup_tools_hooks.py for that coverage. This
    test only covers what setup_tools.run() itself still does.
    """
    import subprocess

    import setup_tools

    # In-process run() MUST get a throwaway repo_root: several install steps
    # write into the repo (recurring-tasks env.sh, git hooksPath, worker
    # dirs), and the default would mutate the live checkout.
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    (repo / ".githooks" / "pre-commit").write_text("#!/bin/bash\n", encoding="utf-8")
    (repo / "profiles").mkdir()
    (repo / "llmhooks").mkdir()
    (repo / "llmhooks" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "llmhooks" / "registry.py").write_text(
        "def hooks_for_host(host):\n    return []\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    home = tmp_path / "home"
    home.mkdir()
    # run() imports llmhooks from the fake repo; snapshot import state so the
    # stub doesn't stay cached and poison later tests in the same process.
    saved_path = list(sys.path)
    saved_llmhooks = {
        name: mod for name, mod in sys.modules.items()
        if name == "llmhooks" or name.startswith("llmhooks.")
    }
    try:
        setup_tools.run(
            home=home,
            bin_dir=home / "bin",
            shell_rc=home / ".bashrc",
            claude_home=home / ".claude",
            codex_home=home / ".codex",
            default_llm="claude",
            update_system_shell_rc=False,
            dry_run=False,
            install_packages=False,
            repo_root=repo,
        )
    finally:
        sys.path[:] = saved_path
        for name in [n for n in sys.modules if n == "llmhooks" or n.startswith("llmhooks.")]:
            del sys.modules[name]
        sys.modules.update(saved_llmhooks)
    mpath = manifest_path(home)
    assert mpath.exists()
    entries = json.loads(mpath.read_text())["entries"]
    kinds = {e["kind"] for e in entries}
    assert "symlink" in kinds
    assert "marker_block" in kinds
