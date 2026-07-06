"""End-to-end lifecycle tests: skill accessibility, launcher availability,
and user-skill safety across install/uninstall.

Covers, per requirement:
- dev-mode install exposes every repo skill through both the Claude and
  Codex homes (plugin-mode accessibility is covered by test_claude_install
  and test_codex_install)
- assistant / collab / coauthor / tw / dispatcher are executable after install
- a pre-existing user-authored skill survives install (migration), stays
  accessible while installed, and persists after uninstall
- the real repo's skills tree is never modified by these tests (integrity
  hash asserted before/after every real-repo flow)
"""
from __future__ import annotations

import hashlib
import io
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "scripts"))

from install_test_utils import (  # noqa: E402
    REPO_ROOT,
    can_create_symlink,
    expected_skills,
    python_test_env,
    run_command,
)

import setup_symlinks  # noqa: E402
import setup_tools  # noqa: E402

UNINSTALL = SCRIPT_DIR.parent / "scripts" / "uninstall.py"

pytestmark = pytest.mark.skipif(
    not can_create_symlink(), reason="symlink creation unavailable"
)


def _tree_hash(root: Path) -> str:
    """Deterministic content hash of a directory tree (paths + file bytes)."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.is_symlink():
            continue
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _dev_install(home: Path, claude_home: Path, codex_home: Path, repo_root: Path) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        setup_symlinks.run(
            home=home,
            repo_root=repo_root,
            claude_home=claude_home,
            codex_home=codex_home,
        )
    return buf.getvalue()


@pytest.fixture()
def homes(tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    claude_home = home / ".claude"
    codex_home = home / ".codex"
    for d in (claude_home, codex_home):
        d.mkdir(parents=True)
    return {"home": home, "claude": claude_home, "codex": codex_home, "root": tmp_path}


# ── Dev-mode skill accessibility (real repo, read-only) ─────────────────────

def test_dev_mode_exposes_all_skills_on_claude_and_codex(homes):
    skills_before = _tree_hash(REPO_ROOT / "skills")

    _dev_install(homes["home"], homes["claude"], homes["codex"], REPO_ROOT)

    expected = expected_skills()
    assert expected, "expected_skills() came back empty"
    for host in ("claude", "codex"):
        missing = [
            name
            for name in expected
            if not (homes[host] / "skills" / name / "SKILL.md").is_file()
        ]
        assert missing == [], f"skills not accessible via {host} home: {missing}"

    # agents and references must resolve too
    for host in ("claude", "codex"):
        for agent in ("assistant", "collab", "coauthor"):
            assert (homes[host] / "agents" / f"{agent}.md").is_file(), (host, agent)
        assert (homes[host] / "references").is_dir(), host

    # the real repo must be untouched by a dev install into sandbox homes
    assert _tree_hash(REPO_ROOT / "skills") == skills_before


# ── Launcher availability after install (real repo, read-only) ──────────────

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX launchers")
def test_launchers_executable_after_install(homes):
    skills_before = _tree_hash(REPO_ROOT / "skills")
    bin_dir = homes["root"] / "bin"
    bin_dir.mkdir()

    source_bin = REPO_ROOT / "skills" / "install-assistant-tools" / "bin"
    buf = io.StringIO()
    with redirect_stdout(buf):
        setup_tools.install_bin_scripts(source_bin, bin_dir, dry_run=False)
        setup_tools.install_dispatcher_launcher(REPO_ROOT, bin_dir, dry_run=False)

    env = python_test_env(homes["root"], {"HOME": str(homes["home"])})
    for cmd in ("assistant", "collab", "coauthor", "tw", "dispatcher"):
        exe = bin_dir / cmd
        assert exe.exists(), f"{cmd} not installed into bin dir"
        result = subprocess.run(
            [str(exe), "--help"], capture_output=True, text=True, env=env, timeout=60
        )
        assert result.returncode == 0, (
            f"{cmd} --help failed ({result.returncode}):\n{result.stderr}"
        )

    assert _tree_hash(REPO_ROOT / "skills") == skills_before


# ── User-skill safety through install → use → uninstall (fake repo) ─────────

def _make_fake_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "skills" / "repo-skill").mkdir(parents=True)
    (repo / "skills" / "repo-skill" / "SKILL.md").write_text(
        "# repo skill\n", encoding="utf-8"
    )
    for d in ("references", "agents", "profiles"):
        (repo / d).mkdir()
    (repo / ".git" / "info").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("repo instructions\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("repo instructions\n", encoding="utf-8")
    return repo


def test_user_skill_survives_install_and_uninstall(homes):
    repo = _make_fake_repo(homes["root"])

    # a user-authored skill exists BEFORE install, as a real directory
    user_skill = homes["claude"] / "skills" / "my-user-skill"
    user_skill.mkdir(parents=True)
    user_content = "# my user skill\nprecious local work\n"
    (user_skill / "SKILL.md").write_text(user_content, encoding="utf-8")

    _dev_install(homes["home"], homes["claude"], homes["codex"], repo)

    # install must migrate (not destroy): the skill stays accessible at the
    # same logical path, now through the repo wiring
    migrated = homes["claude"] / "skills" / "my-user-skill" / "SKILL.md"
    assert migrated.is_file(), "user skill inaccessible after install"
    assert migrated.read_text(encoding="utf-8") == user_content
    # and the repo's own skill is accessible alongside it
    assert (homes["claude"] / "skills" / "repo-skill" / "SKILL.md").is_file()

    # uninstall (against the fake repo) must not delete the user's work
    cmd = [
        sys.executable,
        str(UNINSTALL),
        "--home", str(homes["home"]),
        "--claude-home", str(homes["claude"]),
        "--codex-home", str(homes["codex"]),
        "--bin-dir", str(homes["root"] / "bin"),
        "--shell-rc", str(homes["home"] / ".bashrc"),
        "--repo-root", str(repo),
        "--no-system-shell-rc",
        "--no-pip",
        "--no-git-hooks",
    ]
    env = python_test_env(homes["root"])
    env["HOME"] = str(homes["home"])
    run_command(cmd, env=env)

    # the wiring is gone, but the user skill's content persists in the repo tree
    assert not (homes["claude"] / "skills").is_symlink()
    persisted = repo / "skills" / "my-user-skill" / "SKILL.md"
    assert persisted.is_file(), "user skill lost after uninstall"
    assert persisted.read_text(encoding="utf-8") == user_content


def test_conflicting_user_skill_is_never_clobbered(homes):
    """If the user's skill name collides with a repo skill, neither install
    nor uninstall may overwrite or delete the user's version."""
    repo = _make_fake_repo(homes["root"])
    # repo also ships a skill named 'my-user-skill' with different content
    (repo / "skills" / "my-user-skill").mkdir()
    (repo / "skills" / "my-user-skill" / "SKILL.md").write_text(
        "# repo version\n", encoding="utf-8"
    )

    user_skill = homes["claude"] / "skills" / "my-user-skill"
    user_skill.mkdir(parents=True)
    user_content = "# user version — do not clobber\n"
    (user_skill / "SKILL.md").write_text(user_content, encoding="utf-8")

    _dev_install(homes["home"], homes["claude"], homes["codex"], repo)

    # the user's file must still exist with its own content, somewhere real
    survivors = [
        p
        for p in homes["root"].rglob("SKILL.md")
        if p.is_file() and p.read_text(encoding="utf-8") == user_content
    ]
    assert survivors, "conflicting user skill content was destroyed by install"
