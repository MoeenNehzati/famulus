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
from unittest.mock import patch

import pytest
from test_support.git_repository import GitTestRepository

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

from install_test_utils import (  # noqa: E402
    REPO_ROOT,
    build_minimal_managed_runtime_release,
    can_create_symlink,
    expected_skills,
    managed_runtime_uv_bin,
    python_test_env,
    run_command,
)

if __package__ and __package__.count('.') >= 1:
    from .. import _config_bridge as dev_link
else:
    import _config_bridge as dev_link  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .. import _agent_launchers as launchers
else:
    import _agent_launchers as launchers  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .. import _install_scaffold as scaffold
else:
    import _install_scaffold as scaffold  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .._install_launcher import platform_launcher_installer
else:
    from _install_launcher import platform_launcher_installer  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .._state_record import Manifest, manifest_path
else:
    from _state_record import Manifest, manifest_path  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .. import _install_uninstall as uninstall
else:
    import _install_uninstall as uninstall  # noqa: E402
from officina.recurring import native as recurring_native
from officina.install.context import resolve_installation_context

UNINSTALL = SCRIPT_DIR.parent / "_install_uninstall.py"


def _run_isolated_uninstall(command: list[str], native_root: Path) -> None:
    """Run parser/main with an explicitly empty native scheduler namespace."""
    empty = recurring_native._NativeInventory(True, ())
    with (
        patch.object(sys, "argv", [str(UNINSTALL), *command[2:]]),
        patch.object(uninstall, "native_registration_root", lambda context, platform: native_root),
        patch.object(recurring_native, "_systemd_unit_inventory", lambda *args, **kwargs: empty),
        patch.object(recurring_native, "_launchd_label_inventory", lambda *args, **kwargs: empty),
        patch.object(recurring_native, "_windows_task_inventory", lambda *args, **kwargs: empty),
        patch.object(recurring_native, "_read_crontab", lambda: ""),
        pytest.raises(SystemExit) as stopped,
    ):
        uninstall.main()
    assert stopped.value.code == 0

# famulus-skip: category=capability-unavailable; reason=dev-mode lifecycle test requires symlink creation; alternate=plugin-mode install tests cover copy-based Windows launcher behavior
pytestmark = pytest.mark.skipif(
    not can_create_symlink(), reason="symlink creation unavailable"
)


def _raw_git_config(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    # famulus-raw-git: category=hooks; reason=the lifecycle test must preserve the real repository hooksPath without the shared helper's hooksPath override
    return subprocess.run(
        ["git", "-C", str(repo_root), "config", *args],
        capture_output=True,
        check=check,
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
        # This projection test uses the live checkout read-only. Git-hook
        # mutation/restoration is covered against disposable repositories.
        with patch.object(dev_link, "install_git_hooks"):
            dev_link.run(
                home=home,
                repo_root=repo_root,
                claude_home=claude_home,
                codex_home=codex_home,
            )
    return buf.getvalue()


def _publish_standard_manifest(home: Path, repo_root: Path) -> None:
    legacy = Manifest(manifest_path(home))
    context = resolve_installation_context(
        mode="standard",
        source_root=repo_root,
        development_root=None,
        platform=sys.platform,
        home=home,
        environ={},
    )
    current = Manifest(context.paths.install_state_root / "install-manifest.json")
    current.entries = list(legacy.entries)
    current.bind_context(mode="standard", installation_id="standard")


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

# famulus-skip: category=platform-contract; reason=Windows uses .bat launcher wrappers instead of POSIX executables; alternate=test_codex_install exercises installed Windows launchers
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX launchers",
)
def test_launchers_executable_after_install(homes):
    skills_before = _tree_hash(REPO_ROOT / "skills")
    bin_dir = homes["root"] / "bin"
    bin_dir.mkdir()

    source_bin = REPO_ROOT / "skills" / "install-assistant-tools" / "_rtx/assets/bin"
    launcher_installer = platform_launcher_installer()
    buf = io.StringIO()
    with redirect_stdout(buf):
        launcher_installer.install_dispatcher_launcher(
            REPO_ROOT, bin_dir, dry_run=False, home=homes["home"]
        )
        for agent in ("assistant", "collab", "coauthor", "tw"):
            launchers.install_agent_launcher_files(
                source_bin, bin_dir, agent, dry_run=False, manifest=None, environ={}
            )

    env = python_test_env(homes["root"], {"HOME": str(homes["home"])})
    # "dispatcher" is generated separately below: it now execs into the
    # stable managed-runtime resolver (officina.install.resolvers.launch)
    # instead of running self-contained against this repo checkout -- see the
    # assertion further down, once a real candidate release has been built
    # and the resolver deployed, for its current, expected failure mode.
    for cmd in ("assistant", "collab", "coauthor", "tw"):
        exe = bin_dir / cmd
        assert exe.exists(), f"{cmd} not installed into bin dir"
        result = subprocess.run(
            [str(exe), "--help"], capture_output=True, text=True, env=env, timeout=60
        )
        assert result.returncode == 0, (
            f"{cmd} --help failed ({result.returncode}):\n{result.stderr}"
        )

    dispatcher = bin_dir / "dispatcher"
    assert dispatcher.exists(), "dispatcher not installed into bin dir"

    if managed_runtime_uv_bin() is None:
        # famulus-skip: category=capability-unavailable; reason=proving the resolver hop succeeds requires building a real managed-runtime release, which needs a real uv binary; alternate=tests/test_officina_managed_runtime.py and tests/test_officina_launcher_entry.py cover the build+deploy+resolver flow directly
        pytest.skip("uv is not installed on this machine")
    # Build and activate a real managed-runtime candidate release under this
    # test's sandboxed home. build_candidate_release deploys the
    # dependency-free launcher resolver and its trusted-roots.json sidecar as
    # part of that activation (Task 7's Step 3b), so the resolver hop this
    # dispatcher shim execs into now succeeds.
    build_minimal_managed_runtime_release(home=homes["home"], tmp_root=homes["root"])

    result = subprocess.run(
        [str(dispatcher), "--help"], capture_output=True, text=True, env=env, timeout=60
    )
    # The candidate release installs Officina and the core dependencies needed
    # by dispatcher, so this exercises the complete generated-launcher hop.
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout

    assert _tree_hash(REPO_ROOT / "skills") == skills_before


# ── User-skill safety through install → use → uninstall (fake repo) ─────────

def _make_fake_repo(root: Path) -> Path:
    repo = root / "repo"
    GitTestRepository.create(repo)
    (repo / "skills" / "repo-skill").mkdir(parents=True)
    (repo / "skills" / "repo-skill" / "SKILL.md").write_text(
        "# repo skill\n", encoding="utf-8"
    )
    for d in ("references", "agents", "profiles"):
        (repo / d).mkdir()
    (repo / ".githooks").mkdir()
    (repo / "llmhooks").mkdir()
    (repo / "llmhooks" / "registry.py").write_text(
        "def hooks_for_host(host):\n    return []\n", encoding="utf-8"
    )
    (repo / "CLAUDE.md").write_text("repo instructions\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("repo instructions\n", encoding="utf-8")

    # launchers.py needs source bin scripts + profiles/agent content
    src_bin = repo / "skills" / "install-assistant-tools" / "_rtx/assets/bin"
    src_bin.mkdir(parents=True)
    for name in ("_agent_launch.py", "assistant", "collab", "coauthor", "tmux-workspace",
                 "assistant.bat", "collab.bat", "coauthor.bat"):
        (src_bin / name).write_text("#!/bin/bash\n", encoding="utf-8")
    for agent in ("assistant", "collab", "coauthor"):
        (repo / "profiles" / f"{agent}.config.toml").write_text(
            f'model_instructions_file = "agents/{agent}.md"\n', encoding="utf-8"
        )
        (repo / "agents" / f"{agent}.md").write_text(
            f"---\ndescription: {agent}\n---\nBody.\n", encoding="utf-8"
        )

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
    _publish_standard_manifest(homes["home"], repo)
    cmd = [
        sys.executable,
        str(UNINSTALL),
        "--mode", "standard",
        "--home", str(homes["home"]),
        "--claude-home", str(homes["claude"]),
        "--codex-home", str(homes["codex"]),
        "--bin-dir", str(homes["root"] / "bin"),
        "--shell-rc", str(homes["home"] / ".bashrc"),
        "--repo-root", str(repo),
        "--no-system-shell-rc",
        "--no-pip",
        "--no-git-hooks",
        "--purge",
    ]
    _run_isolated_uninstall(cmd, homes["root"] / "native")

    # the wiring is gone, but the user skill's content persists in the repo tree
    assert not (homes["claude"] / "skills").is_symlink()
    persisted = repo / "skills" / "my-user-skill" / "SKILL.md"
    assert persisted.is_file(), "user skill lost after uninstall"
    assert persisted.read_text(encoding="utf-8") == user_content


def _home_snapshot(home: Path) -> dict[str, str]:
    """Map of relpath -> content fingerprint for every file/symlink under home.

    Directories are ignored: empty leftover dirs are harmless and platform
    cleanup behavior differs.
    """
    snap: dict[str, str] = {}
    if not home.exists():
        return snap
    for path in sorted(home.rglob("*")):
        rel = path.relative_to(home).as_posix()  # separator-stable keys
        if path.is_symlink():
            snap[rel] = f"symlink:{path.readlink()}"
        elif path.is_file():
            snap[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snap


# Paths (relative to home) uninstall deliberately leaves behind. Every entry
# must be justified; anything else left over is a bug.
_ALLOWED_LEFTOVERS = {
    # OAuth/service configs are user credentials-adjacent; kept unless --purge
    ".config/cloud-files/config.json",
    # the manifest correctly stays while it still tracks kept artifacts
    # (the cloud-files config above); it is removed on a fully clean run
    ".local/state/assistant-tools/install-manifest.json",
    # --no-git-hooks deliberately retains the context manifest entry so a
    # later retry can restore the checkout's previous local hooksPath.
    ".local/state/famulus/install/install-manifest.json",
    "Library/Application Support/Famulus/state/install/install-manifest.json",
    "AppData/Local/Famulus/state/install/install-manifest.json",
    # The empty coordination sidecar has a stable name so waiting and future
    # Famulus lifecycle calls cannot lock different inodes.
    ".local/state/famulus/install/assistant-access.lock",
    "Library/Application Support/Famulus/state/install/assistant-access.lock",
    "AppData/Local/Famulus/state/install/assistant-access.lock",
    ".local/state/famulus/recurring-tasks/lifecycle.lock",
    "Library/Application Support/Famulus/state/recurring-tasks/lifecycle.lock",
    "AppData/Local/Famulus/state/recurring-tasks/lifecycle.lock",
}


def test_install_uninstall_roundtrip_restores_home(homes, tmp_path: Path):
    """Full install then uninstall must return the home to its pristine state,
    modulo the explicit _ALLOWED_LEFTOVERS list. This catches any future
    install side effect that uninstall forgets to reverse."""
    repo = _make_fake_repo(homes["root"])
    (repo / ".githooks" / "pre-commit").write_text("#!/bin/bash\n", encoding="utf-8")

    home = homes["home"]
    bin_dir = homes["root"] / "bin"
    shell_rc = home / ".bashrc"
    shell_rc.parent.mkdir(parents=True, exist_ok=True)
    shell_rc.write_text("# user line\n", encoding="utf-8")

    before = _home_snapshot(home)

    # full install (symlink wiring + tools), sandbox-scoped
    saved_path = list(sys.path)
    saved_llmhooks = {
        name: mod for name, mod in sys.modules.items()
        if name == "llmhooks" or name.startswith("llmhooks.")
    }
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            dev_link.run(
                home=home,
                repo_root=repo,
                claude_home=homes["claude"],
                codex_home=homes["codex"],
            )
            scaffold.run(
                repo_root=repo, home=home, bin_dir=bin_dir, shell_rc=shell_rc,
                environ={},
            )
            launchers.run(
                repo_root=repo,
                agents=["assistant", "collab", "coauthor", "tw"],
                home=home,
                bin_dir=bin_dir,
                codex_home=homes["codex"],
                claude_home=homes["claude"],
                shell_rc=shell_rc,
                default_llm="claude",
                environ={},
            )
    finally:
        sys.path[:] = saved_path
        for name in [n for n in sys.modules if n == "llmhooks" or n.startswith("llmhooks.")]:
            del sys.modules[name]
        sys.modules.update(saved_llmhooks)

    installed = _home_snapshot(home)
    assert installed != before, "install produced no observable change — test is vacuous"

    _publish_standard_manifest(home, repo)
    cmd = [
        sys.executable,
        str(UNINSTALL),
        "--mode", "standard",
        "--home", str(home),
        "--claude-home", str(homes["claude"]),
        "--codex-home", str(homes["codex"]),
        "--bin-dir", str(bin_dir),
        "--shell-rc", str(shell_rc),
        "--repo-root", str(repo),
        "--no-system-shell-rc",
        "--no-pip",
        "--no-git-hooks",
        "--purge",
    ]
    _run_isolated_uninstall(cmd, homes["root"] / "native")

    after = _home_snapshot(home)

    added = {
        rel for rel in after
        if rel not in before and rel not in _ALLOWED_LEFTOVERS
    }
    changed = {
        rel for rel in after
        if rel in before and after[rel] != before[rel] and rel not in _ALLOWED_LEFTOVERS
    }
    removed = {rel for rel in before if rel not in after}

    assert added == set(), f"uninstall left behind unreversed install artifacts: {sorted(added)}"
    assert changed == set(), f"uninstall did not restore modified files: {sorted(changed)}"
    assert removed == set(), f"uninstall deleted pre-existing user files: {sorted(removed)}"

    # bin dir lives outside home; it must be emptied of installed launchers too
    leftover_bin = [p.name for p in bin_dir.iterdir()] if bin_dir.exists() else []
    assert leftover_bin == [], f"launchers left in bin dir: {leftover_bin}"


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
