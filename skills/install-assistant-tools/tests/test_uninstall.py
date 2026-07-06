"""Tests for uninstall.py — reversal of install side effects.

The installed state is constructed manually in sandboxed temp dirs (symlinks
into the real repo, rc files with managed blocks, hook entries), then
uninstall.py is invoked with explicit paths. No pip/systemd side effects:
tests pass --no-pip and a sandbox home.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from install_test_utils import REPO_ROOT, can_create_symlink, python_test_env, run_command

UNINSTALL = REPO_ROOT / "skills" / "install-assistant-tools" / "scripts" / "uninstall.py"

BLOCK_BEGIN = "# >>> assistant-tools >>>"
BLOCK_END = "# <<< assistant-tools <<<"
HOOKS_BLOCK_BEGIN = "# >>> skill-system-hooks >>>"
HOOKS_BLOCK_END = "# <<< skill-system-hooks <<<"

pytestmark = pytest.mark.skipif(not can_create_symlink(), reason="symlinks unavailable")


def make_installed_state(root: Path) -> dict[str, Path]:
    """Build a realistic installed layout under root, linked to the real repo."""
    home = root / "home"
    claude_home = home / ".claude"
    codex_home = home / ".codex"
    bin_dir = home / "Documents" / "scripts" / "bin"
    for d in (claude_home, codex_home, bin_dir):
        d.mkdir(parents=True)

    # Claude/Codex symlinks into repo
    (claude_home / "skills").symlink_to(REPO_ROOT / "skills")
    (claude_home / "references").symlink_to(REPO_ROOT / "references")
    (claude_home / "agents").symlink_to(REPO_ROOT / "agents")
    (claude_home / "CLAUDE.md").symlink_to(REPO_ROOT / "CLAUDE.md")
    (codex_home / "skills").symlink_to(REPO_ROOT / "skills")
    (codex_home / "AGENTS.md").symlink_to(REPO_ROOT / "CLAUDE.md")

    # profile links (legacy install style: symlinks)
    profiles = sorted((REPO_ROOT / "profiles").glob("*.config.toml"))
    profile = profiles[0]
    (claude_home / profile.name).symlink_to(profile)
    (codex_home / profile.name).symlink_to(profile)

    # profile copies (current install style: copied, may carry local state)
    profile_copy = profiles[1]
    for home_dir in (claude_home, codex_home):
        (home_dir / profile_copy.name).write_text(
            profile_copy.read_text(encoding="utf-8") + "\n# machine-local state\n",
            encoding="utf-8",
        )

    # a user-owned config that matches the profile glob but has no repo
    # counterpart — must NOT be removed
    (claude_home / "personal.config.toml").write_text("mine\n", encoding="utf-8")

    # a user-owned symlink that must NOT be removed (points outside repo)
    foreign_target = root / "foreign"
    foreign_target.mkdir()
    (claude_home / "foreign-link").symlink_to(foreign_target)

    # bin symlinks
    src_bin = REPO_ROOT / "skills" / "install-assistant-tools" / "bin"
    (bin_dir / "assistant").symlink_to(src_bin / "assistant")
    (bin_dir / "tw").symlink_to(src_bin / "tmux-workspace")

    # user shell rc with managed block sandwiched between user lines
    shell_rc = home / ".bashrc"
    shell_rc.write_text(
        "# user line before\n"
        f"{BLOCK_BEGIN}\n"
        'export PATH="managed:$PATH"\n'
        f"{BLOCK_END}\n"
        "# user line after\n",
        encoding="utf-8",
    )

    # codex config.toml with managed hooks block plus user config
    (codex_home / "config.toml").write_text(
        'model = "user-choice"\n'
        f"{HOOKS_BLOCK_BEGIN}\n"
        "[[hooks.session_start]]\n"
        f"{HOOKS_BLOCK_END}\n"
        "sandbox = true\n",
        encoding="utf-8",
    )

    # claude settings.local.json: one managed hook entry + one user entry
    managed_cmd = f"python3 {REPO_ROOT}/llmhooks/inject_dispatcher_context.py --claude"
    settings = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": managed_cmd}]},
                {"hooks": [{"type": "command", "command": "echo user-hook"}]},
            ]
        },
        "permissions": {"allow": ["Bash(ls:*)"]},
    }
    (claude_home / "settings.local.json").write_text(
        json.dumps(settings, indent=2), encoding="utf-8"
    )

    # ai-agent env file + cloud-files / g-calendar config (purge targets)
    env_dir = home / ".config" / "environment.d"
    env_dir.mkdir(parents=True)
    (env_dir / "20-ai-agent.conf").write_text("AI_AGENT_COMMAND_TEMPLATE=x\n")
    for svc in ("cloud-files", "g-calendar"):
        d = home / ".config" / svc
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}", encoding="utf-8")

    return {
        "home": home,
        "claude_home": claude_home,
        "codex_home": codex_home,
        "bin_dir": bin_dir,
        "shell_rc": shell_rc,
    }


def run_uninstall(paths: dict[str, Path], *extra: str, check: bool = True):
    cmd = [
        sys.executable,
        str(UNINSTALL),
        "--home", str(paths["home"]),
        "--claude-home", str(paths["claude_home"]),
        "--codex-home", str(paths["codex_home"]),
        "--bin-dir", str(paths["bin_dir"]),
        "--shell-rc", str(paths["shell_rc"]),
        "--no-system-shell-rc",
        "--no-pip",
        "--no-git-hooks",
        *extra,
    ]
    env = python_test_env(paths["home"].parent)
    env["HOME"] = str(paths["home"])
    return run_command(cmd, env=env, check=check)


@pytest.fixture()
def installed(tmp_path: Path) -> dict[str, Path]:
    return make_installed_state(tmp_path)


def test_removes_repo_symlinks_from_homes(installed):
    run_uninstall(installed)
    for name in ("skills", "references", "agents", "CLAUDE.md"):
        assert not (installed["claude_home"] / name).is_symlink(), name
    assert not (installed["codex_home"] / "skills").is_symlink()
    assert not (installed["codex_home"] / "AGENTS.md").is_symlink()


def test_removes_profile_links(installed):
    run_uninstall(installed)
    # symlinked (legacy) and copied (current) profiles both removed;
    # the user's own config with no repo counterpart is preserved
    assert list(installed["claude_home"].glob("*.config.toml")) == [
        installed["claude_home"] / "personal.config.toml"
    ]
    assert not list(installed["codex_home"].glob("*.config.toml"))


def test_preserves_user_config_matching_glob(installed):
    run_uninstall(installed)
    assert (installed["claude_home"] / "personal.config.toml").read_text(
        encoding="utf-8"
    ) == "mine\n"


def test_preserves_foreign_symlink(installed):
    run_uninstall(installed)
    assert (installed["claude_home"] / "foreign-link").is_symlink()


def test_removes_bin_links(installed):
    run_uninstall(installed)
    assert not (installed["bin_dir"] / "assistant").exists()
    assert not (installed["bin_dir"] / "tw").exists()


def test_strips_rc_block_preserving_user_lines(installed):
    run_uninstall(installed)
    text = installed["shell_rc"].read_text(encoding="utf-8")
    assert BLOCK_BEGIN not in text and BLOCK_END not in text
    assert "# user line before" in text and "# user line after" in text


def test_strips_codex_hooks_block_preserving_user_config(installed):
    run_uninstall(installed)
    text = (installed["codex_home"] / "config.toml").read_text(encoding="utf-8")
    assert HOOKS_BLOCK_BEGIN not in text
    assert 'model = "user-choice"' in text and "sandbox = true" in text


def test_removes_managed_claude_hook_preserving_user_hook(installed):
    run_uninstall(installed)
    settings = json.loads(
        (installed["claude_home"] / "settings.local.json").read_text(encoding="utf-8")
    )
    commands = [
        hook["command"]
        for entries in settings.get("hooks", {}).values()
        for entry in entries
        for hook in entry.get("hooks", [])
    ]
    assert "echo user-hook" in commands
    assert not any("inject_dispatcher_context" in c for c in commands)
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}


def test_removes_ai_agent_env_file(installed):
    run_uninstall(installed)
    assert not (installed["home"] / ".config" / "environment.d" / "20-ai-agent.conf").exists()


def test_leaves_credentials_by_default(installed):
    run_uninstall(installed)
    assert (installed["home"] / ".config" / "cloud-files" / "config.json").exists()
    assert (installed["home"] / ".config" / "g-calendar" / "config.json").exists()


def test_purge_removes_credentials(installed):
    run_uninstall(installed, "--purge")
    assert not (installed["home"] / ".config" / "cloud-files").exists()
    assert not (installed["home"] / ".config" / "g-calendar").exists()


def test_dry_run_changes_nothing(installed):
    result = run_uninstall(installed, "--dry-run")
    assert (installed["claude_home"] / "skills").is_symlink()
    assert (installed["bin_dir"] / "assistant").is_symlink()
    assert BLOCK_BEGIN in installed["shell_rc"].read_text(encoding="utf-8")
    assert "Would" in result.stdout


def test_report_and_exit_code_on_failure(installed):
    # Point shell-rc at an unwritable location to force a reported failure.
    ro_dir = installed["home"] / "ro"
    ro_dir.mkdir()
    rc = ro_dir / "rc"
    rc.write_text(f"{BLOCK_BEGIN}\nx\n{BLOCK_END}\n", encoding="utf-8")
    os.chmod(rc, 0o444)
    os.chmod(ro_dir, 0o555)
    try:
        paths = dict(installed)
        paths["shell_rc"] = rc
        result = run_uninstall(paths, check=False)
        assert result.returncode != 0
        assert "FAILED" in result.stdout
    finally:
        os.chmod(ro_dir, 0o755)
        os.chmod(rc, 0o644)


def test_report_lists_actions(installed):
    result = run_uninstall(installed)
    assert result.returncode == 0
    out = result.stdout
    assert "removed" in out.lower()
    # nothing failed in the happy path
    assert "FAILED" not in out
