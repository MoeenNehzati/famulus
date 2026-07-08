"""Tests for uninstall.py — manifest-based reversal of install side effects.

The installed state is produced by REALLY running the installers
(dev_link.run + scaffold.run + launchers.run) against a fake repo and
sandboxed homes, so a genuine manifest drives the uninstall — the only
supported path. A missing manifest is a hard error (tested), never a
heuristic guess.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from install_test_utils import REPO_ROOT, can_create_symlink, python_test_env, run_command

SCRIPTS = REPO_ROOT / "skills" / "install-assistant-tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from install_manifest import Manifest, manifest_path  # noqa: E402
import dev_link  # noqa: E402
import launchers  # noqa: E402
import scaffold  # noqa: E402

UNINSTALL = SCRIPTS / "uninstall.py"

BLOCK_BEGIN = "# >>> assistant-tools >>>"
BLOCK_END = "# <<< assistant-tools <<<"
HOOKS_BLOCK_BEGIN = "# >>> skill-system-hooks >>>"
HOOKS_BLOCK_END = "# <<< skill-system-hooks <<<"

# registry stub with one real binding so managed hook entries exist
_REGISTRY_STUB = """\
class _Binding:
    def __init__(self, event, argv):
        self.event = event
        self.matcher = None
        self.argv = argv


class _Hook:
    def install_binding(self, host, repo_root):
        return _Binding(
            "SessionStart" if host == "claude" else "session_start",
            ("python3", f"{repo_root}/llmhooks/stub_hook.py", f"--{host}"),
        )


def hooks_for_host(host):
    return (_Hook(),)
"""

pytestmark = pytest.mark.skipif(not can_create_symlink(), reason="symlinks unavailable")


def make_fake_repo(root: Path) -> Path:
    """Minimal fake repo: uninstall must never run against the real one
    (it removes repo-scoped artifacts like recurring-tasks env.sh)."""
    repo = root / "repo"
    for d in ("references", "agents"):
        (repo / d).mkdir(parents=True)
    (repo / "skills" / "repo-skill").mkdir(parents=True)
    (repo / "skills" / "repo-skill" / "SKILL.md").write_text("# repo skill\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("# fake\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# fake\n", encoding="utf-8")

    profiles = repo / "profiles"
    profiles.mkdir()
    (profiles / "assistant.config.toml").write_text('model = "a"\n', encoding="utf-8")
    (profiles / "collab.config.toml").write_text('model = "b"\n', encoding="utf-8")

    src_bin = repo / "skills" / "install-assistant-tools" / "bin"
    src_bin.mkdir(parents=True)
    for name in ("_agent_launch.py", "assistant", "collab", "coauthor", "tmux-workspace",
                 "assistant.bat", "collab.bat", "coauthor.bat"):
        (src_bin / name).write_text("#!/bin/bash\n", encoding="utf-8")

    rt_scripts = repo / "skills" / "recurring-tasks" / "scripts"
    rt_scripts.mkdir(parents=True)
    (rt_scripts / "env.sh").write_text("export PATH=fake:$PATH\n", encoding="utf-8")

    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "pre-commit").write_text("#!/bin/bash\n", encoding="utf-8")

    (repo / "llmhooks").mkdir()
    (repo / "llmhooks" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "llmhooks" / "registry.py").write_text(_REGISTRY_STUB, encoding="utf-8")
    (repo / "llmhooks" / "stub_hook.py").write_text("print('hi')\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def make_installed_state(root: Path) -> dict[str, Path]:
    """Real install into sandboxed homes against the fake repo."""
    repo = make_fake_repo(root)
    home = root / "home"
    claude_home = home / ".claude"
    codex_home = home / ".codex"
    bin_dir = home / "Documents" / "scripts" / "bin"
    for d in (claude_home, codex_home, bin_dir):
        d.mkdir(parents=True)

    # user-owned content that must survive uninstall untouched
    shell_rc = home / ".bashrc"
    shell_rc.write_text("# user line before\n", encoding="utf-8")
    (codex_home / "config.toml").write_text('model = "user-choice"\n', encoding="utf-8")
    user_hook_entry = {"hooks": [{"type": "command", "command": "echo user-hook"}]}
    (claude_home / "settings.local.json").write_text(
        json.dumps(
            {"hooks": {"SessionStart": [user_hook_entry]}, "permissions": {"allow": ["Bash(ls:*)"]}},
            indent=2,
        ),
        encoding="utf-8",
    )
    (claude_home / "personal.config.toml").write_text("mine\n", encoding="utf-8")
    foreign_target = root / "foreign"
    foreign_target.mkdir()
    (claude_home / "foreign-link").symlink_to(foreign_target)

    saved_path = list(sys.path)
    saved_llmhooks = {
        name: mod for name, mod in sys.modules.items()
        if name == "llmhooks" or name.startswith("llmhooks.")
    }
    try:
        with redirect_stdout(io.StringIO()):
            dev_link.run(
                home=home, repo_root=repo,
                claude_home=claude_home, codex_home=codex_home,
            )
            scaffold.run(repo_root=repo, home=home, bin_dir=bin_dir, shell_rc=shell_rc)
            launchers.run(
                repo_root=repo,
                agents=["assistant", "collab", "coauthor", "tw"],
                home=home, bin_dir=bin_dir, shell_rc=shell_rc,
                claude_home=claude_home, codex_home=codex_home,
                default_llm="claude",
            )
    finally:
        sys.path[:] = saved_path
        for name in [n for n in sys.modules if n == "llmhooks" or n.startswith("llmhooks.")]:
            del sys.modules[name]
        sys.modules.update(saved_llmhooks)

    return {
        "home": home,
        "claude_home": claude_home,
        "codex_home": codex_home,
        "bin_dir": bin_dir,
        "shell_rc": shell_rc,
        "repo": repo,
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
        # never point uninstall at the real repo: it removes repo-scoped
        # artifacts (recurring-tasks env.sh, git hooksPath)
        "--repo-root", str(paths["repo"]),
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


def test_removes_profile_copies_preserving_user_config(installed):
    # profiles are installed as copies; the user's own config (no repo
    # counterpart, not in the manifest) must survive
    assert (installed["claude_home"] / "assistant.config.toml").is_file()
    run_uninstall(installed)
    assert list(installed["claude_home"].glob("*.config.toml")) == [
        installed["claude_home"] / "personal.config.toml"
    ]
    assert not list(installed["codex_home"].glob("*.config.toml"))
    assert (installed["claude_home"] / "personal.config.toml").read_text(
        encoding="utf-8"
    ) == "mine\n"


def test_preserves_foreign_symlink(installed):
    run_uninstall(installed)
    assert (installed["claude_home"] / "foreign-link").is_symlink()


@pytest.mark.skipif(sys.platform == "win32", reason="dispatcher launcher is POSIX-only by design; Windows uses .bat wrappers + registry PATH")
def test_removes_bin_links_and_launcher(installed):
    assert (installed["bin_dir"] / "assistant").exists()
    assert (installed["bin_dir"] / "dispatcher").is_file()
    run_uninstall(installed)
    leftovers = [p.name for p in installed["bin_dir"].iterdir()]
    assert leftovers == [], f"bin dir not emptied: {leftovers}"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows installs manage PATH via registry, not shell rc")
def test_strips_rc_block_preserving_user_lines(installed):
    text = installed["shell_rc"].read_text(encoding="utf-8")
    assert BLOCK_BEGIN in text  # install really wrote the block
    run_uninstall(installed)
    text = installed["shell_rc"].read_text(encoding="utf-8")
    assert BLOCK_BEGIN not in text and BLOCK_END not in text
    assert "# user line before" in text


def test_strips_codex_hooks_block_preserving_user_config(installed):
    config = installed["codex_home"] / "config.toml"
    assert HOOKS_BLOCK_BEGIN in config.read_text(encoding="utf-8")
    run_uninstall(installed)
    text = config.read_text(encoding="utf-8")
    assert HOOKS_BLOCK_BEGIN not in text and HOOKS_BLOCK_END not in text
    assert 'model = "user-choice"' in text


def test_removes_managed_claude_hook_preserving_user_hook(installed):
    settings_file = installed["claude_home"] / "settings.local.json"
    before = json.loads(settings_file.read_text(encoding="utf-8"))
    assert len(before["hooks"]["SessionStart"]) == 2  # user + managed
    run_uninstall(installed)
    after = json.loads(settings_file.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in after.get("hooks", {}).get("SessionStart", [])
        for hook in entry.get("hooks", [])
    ]
    assert commands == ["echo user-hook"]
    assert after["permissions"] == {"allow": ["Bash(ls:*)"]}


def _seed_legacy_config_dir_entry(installed: dict[str, Path]) -> Path:
    """Simulate a pre-migration manifest entry: cloud-files config.json used
    to be written (and manifest-tracked) by install-assistant-tools itself.
    That responsibility has moved to cloud-files/scripts/ensure_oauth.py, but
    uninstall.py must still correctly purge/leave entries recorded by an
    older install for users upgrading across the migration.
    """
    config_dir = installed["home"] / ".config" / "cloud-files"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text('{"remote_llm_root": "assistant"}\n', encoding="utf-8")

    manifest = Manifest(manifest_path(installed["home"]))
    manifest.record("config_dir", path=str(config_dir), purge_only=True)
    manifest.save()
    return config_dir


def test_leaves_credentials_by_default(installed):
    config_dir = _seed_legacy_config_dir_entry(installed)
    config = config_dir / "config.json"
    assert config.exists()
    run_uninstall(installed)
    assert config.exists()


def test_purge_removes_credentials(installed):
    config_dir = _seed_legacy_config_dir_entry(installed)
    run_uninstall(installed, "--purge")
    assert not config_dir.exists()


def test_dry_run_changes_nothing(installed):
    before = {
        str(p): p.is_symlink() for p in installed["home"].rglob("*")
    }
    run_uninstall(installed, "--dry-run")
    after = {
        str(p): p.is_symlink() for p in installed["home"].rglob("*")
    }
    assert before == after


def test_report_lists_actions(installed):
    result = run_uninstall(installed)
    assert "Uninstall report:" in result.stdout
    assert "[removed]" in result.stdout


def test_missing_manifest_is_hard_error(installed):
    manifest = (
        installed["home"] / ".local" / "state" / "assistant-tools" / "install-manifest.json"
    )
    assert manifest.exists()
    manifest.unlink()  # simulate hand-deleted manifest

    result = run_uninstall(installed, check=False)
    assert result.returncode != 0
    assert "no install manifest" in result.stderr.lower()
    # and nothing was touched: installed artifacts are all still present
    assert (installed["claude_home"] / "skills").is_symlink()
    if sys.platform != "win32":  # launcher and rc block are POSIX-only
        assert (installed["bin_dir"] / "dispatcher").is_file()
        assert BLOCK_BEGIN in installed["shell_rc"].read_text(encoding="utf-8")
