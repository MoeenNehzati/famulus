"""Tests for the install manifest: recording at install time, replay at uninstall.

The manifest is the source of truth for uninstall. Key property: uninstall
removes exactly what install recorded — including symlinks pointing at a
*stale* root (e.g. an old plugin-cache version dir), which the heuristic
fallback cannot know about.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from test_support.git_repository import GitTestRepository

if __package__ and __package__.count('.') >= 1:
    from .install_test_utils import REPO_ROOT, can_create_symlink
else:
    from install_test_utils import REPO_ROOT, can_create_symlink

SCRIPTS = REPO_ROOT / "skills" / "install-assistant-tools" / "_rtx"
sys.path.insert(0, str(SCRIPTS))

if __package__ and __package__.count('.') >= 1:
    from .._state_record import Manifest, manifest_path
else:
    from _state_record import Manifest, manifest_path  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .. import _install_uninstall as uninstall
else:
    import _install_uninstall as uninstall  # noqa: E402

UNINSTALL = SCRIPTS / "_install_uninstall.py"

# famulus-skip: category=capability-unavailable; reason=manifest tests exercise symlink entries and cleanup; alternate=Windows manifest tests cover copy and registry entries
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


def test_unbound_legacy_manifest_stays_schema_1_until_context_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    manifest = Manifest(path)

    manifest.record("file", path=str(tmp_path / "legacy-file"))

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "entries": [{"kind": "file", "path": str(tmp_path / "legacy-file")}],
    }


def test_manifest_dedupes_on_kind_and_path(tmp_path: Path):
    m = Manifest(tmp_path / "manifest.json")
    m.record("symlink", path="/x", target="/old")
    m.record("symlink", path="/x", target="/new")
    assert len(m.entries) == 1
    assert m.entries[0]["target"] == "/new"


def test_manifest_forget_removes_matching_kind_and_path(tmp_path: Path):
    path = tmp_path / "manifest.json"
    m = Manifest(path)
    m.record("symlink", path="/x", target="/target")
    m.record("file", path="/x")

    m.forget("symlink", path="/x")

    assert m.entries == [{"kind": "file", "path": "/x"}]
    assert Manifest(path).entries == m.entries


def test_manifest_path_is_under_home_state(tmp_path: Path):
    p = manifest_path(tmp_path)
    assert p == tmp_path / ".local" / "state" / "assistant-tools" / "install-manifest.json"


def test_manifest_binds_one_explicit_installation_context(tmp_path: Path):
    path = tmp_path / "manifest.json"
    manifest = Manifest(path)

    manifest.bind_context(
        mode="development",
        installation_id="dev-0123456789abcdef0123456789abcdef",
        development_root=tmp_path / "checkout",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["installation"] == {
        "mode": "development",
        "installation_id": "dev-0123456789abcdef0123456789abcdef",
        "development_root": str(tmp_path / "checkout"),
    }

    with pytest.raises(ValueError, match="different installation context"):
        manifest.bind_context(mode="standard", installation_id="standard")


def test_manifest_rebases_same_development_installation_after_checkout_move(
    tmp_path: Path,
) -> None:
    old_checkout = tmp_path / "old checkout 雪"
    moved_checkout = tmp_path / "moved checkout 雪"
    manifest_path = (
        old_checkout
        / ".famulus"
        / "home"
        / ".local"
        / "state"
        / "famulus"
        / "install"
        / "install-manifest.json"
    )
    (old_checkout / ".famulus").mkdir(parents=True)
    manifest = Manifest(manifest_path)
    installation_id = "dev-0123456789abcdef0123456789abcdef"
    (old_checkout / ".famulus" / "install-id").write_text(
        installation_id + "\n", encoding="utf-8"
    )
    manifest.bind_context(
        mode="development",
        installation_id=installation_id,
        development_root=old_checkout,
    )
    manifest.record(
        "symlink",
        path=str(old_checkout / ".famulus" / "homes" / "codex" / "skills"),
        target=str(old_checkout / "skills"),
        metadata={"source": str(old_checkout / "profiles")},
    )
    stable_canary = tmp_path / "stable home" / "canary.bin"
    stable_canary.parent.mkdir()
    stable_canary.write_bytes(b"stable")
    manifest.record("file", path=str(stable_canary), purge_only=True)

    old_checkout.rename(moved_checkout)
    moved_manifest_path = moved_checkout / manifest_path.relative_to(old_checkout)
    moved_manifest = Manifest(moved_manifest_path)
    moved_manifest.bind_context(
        mode="development",
        installation_id=installation_id,
        development_root=moved_checkout,
    )

    raw = moved_manifest_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert str(old_checkout) not in raw
    assert payload["installation"]["development_root"] == str(moved_checkout)
    assert payload["entries"][0]["path"].startswith(str(moved_checkout))
    assert payload["entries"][0]["target"] == str(moved_checkout / "skills")
    assert payload["entries"][0]["metadata"]["source"] == str(
        moved_checkout / "profiles"
    )
    assert payload["entries"][1]["path"] == str(stable_canary)
    assert stable_canary.read_bytes() == b"stable"


def test_manifest_records_file_and_block_content_identity(tmp_path: Path):
    generated = tmp_path / "generated.json"
    generated.write_text('{"owned": true}\n', encoding="utf-8")
    rc = tmp_path / ".bashrc"
    rc.write_bytes(
        b"user\n# >>> assistant-tools >>>\nexport AI=/checkout\n"
        b"# <<< assistant-tools <<<\n"
    )
    manifest = Manifest(tmp_path / "manifest.json")

    manifest.record("file", path=str(generated))
    manifest.record(
        "marker_block",
        path=str(rc),
        begin="# >>> assistant-tools >>>",
        end="# <<< assistant-tools <<<",
    )

    file_entry, block_entry = manifest.entries
    assert file_entry["sha256"] == hashlib.sha256(generated.read_bytes()).hexdigest()
    assert block_entry["block_sha256"] == hashlib.sha256(
        b"# >>> assistant-tools >>>\nexport AI=/checkout\n# <<< assistant-tools <<<\n"
    ).hexdigest()


def test_git_hook_install_records_prior_and_installed_values(tmp_path: Path):
    if __package__ and __package__.count('.') >= 1:
        from .. import _config_bridge as dev_link
    else:
        import _config_bridge as dev_link

    repo = _make_repo_for_manifest_tests(tmp_path)
    # famulus-raw-git: category=hooks; reason=seed the real hooksPath that manifest recording must preserve
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath", "custom-hooks"],
        check=True,
    )
    manifest = Manifest(tmp_path / "manifest.json")

    dev_link.install_git_hooks(repo, repo / ".githooks", False, manifest)

    entry = next(item for item in manifest.entries if item["kind"] == "git_hooks_path")
    assert entry["prior_value"] == "custom-hooks"
    assert entry["installed_value"] == ".githooks"


def test_git_hook_restore_record_survives_interruption_and_retry(tmp_path: Path, monkeypatch):
    if __package__ and __package__.count('.') >= 1:
        from .. import _config_bridge as dev_link
    else:
        import _config_bridge as dev_link

    repo = _make_repo_for_manifest_tests(tmp_path)
    # famulus-raw-git: category=hooks; reason=seed the real hooksPath restored after the injected interruption
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath", "custom-hooks"],
        check=True,
    )
    manifest = Manifest(tmp_path / "manifest.json")
    real_run = dev_link.subprocess.run

    def interrupt_after_git_write(command, *args, **kwargs):
        result = real_run(command, *args, **kwargs)
        if command[-3:] == ["config", "core.hooksPath", ".githooks"]:
            raise RuntimeError("injected interruption after git write")
        return result

    monkeypatch.setattr(dev_link.subprocess, "run", interrupt_after_git_write)
    with pytest.raises(RuntimeError, match="injected interruption"):
        dev_link.install_git_hooks(repo, repo / ".githooks", False, manifest)

    recorded = Manifest(manifest.path)
    entry = next(item for item in recorded.entries if item["kind"] == "git_hooks_path")
    assert entry["prior_value"] == "custom-hooks"
    assert entry["installed_value"] == ".githooks"

    monkeypatch.setattr(dev_link.subprocess, "run", real_run)
    dev_link.install_git_hooks(repo, repo / ".githooks", False, recorded)
    report = uninstall.Report()
    assert uninstall.remove_manifest_git_hooks(recorded.entries[0], report, dry_run=False)
    # famulus-raw-git: category=hooks; reason=observe the real hooksPath after manifest replay
    restored = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
        capture_output=True, text=True, check=True,
    )
    assert restored.stdout.strip() == "custom-hooks"


# ── Install-side recording ────────────────────────────────────────────────────

def _make_repo_for_manifest_tests(tmp_path: Path) -> Path:
    """Build the disposable hooks/llmhooks repo required by dev_link without touching the live checkout."""
    repo = tmp_path / "repo"
    GitTestRepository.create(repo)
    (repo / "skills").mkdir(parents=True)
    (repo / "references").mkdir()
    (repo / "agents").mkdir()
    (repo / ".githooks").mkdir()
    (repo / "llmhooks").mkdir()
    (repo / "llmhooks" / "registry.py").write_text(
        "def hooks_for_host(host):\n    return []\n", encoding="utf-8"
    )
    (repo / "CLAUDE.md").write_text("repo instructions\n", encoding="utf-8")
    return repo


def test_setup_symlinks_records_links(tmp_path: Path):
    if __package__ and __package__.count('.') >= 1:
        from .. import _config_bridge as dev_link
    else:
        import _config_bridge as dev_link

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
    if sys.platform != "win32":
        assert str(claude_home / "CLAUDE.md") in recorded
    else:
        assert str(claude_home / "CLAUDE.md") not in recorded


def test_setup_symlinks_dry_run_records_nothing(tmp_path: Path):
    if __package__ and __package__.count('.') >= 1:
        from .. import _config_bridge as dev_link
    else:
        import _config_bridge as dev_link

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
    # ensure_rc_block (setup_tools.py, legacy) is gone; the merge-based
    # writer used by scaffold/launchers/dev_link is rc_block.ensure_rc_vars,
    # already covered exhaustively by test_rc_block.py. This test just
    # confirms it records into a manifest the way callers expect.
    if __package__ and __package__.count('.') >= 1:
        from .._shell_block import ensure_rc_vars
    else:
        from _shell_block import ensure_rc_vars

    rc = tmp_path / ".bashrc"
    manifest = Manifest(tmp_path / "manifest.json")
    ensure_rc_vars(rc, {"PATH": 'export PATH="/bin:$PATH"'}, False, manifest=manifest)
    blocks = [e for e in manifest.entries if e["kind"] == "marker_block"]
    assert any(e["path"] == str(rc) for e in blocks)


# ── Uninstall replay ──────────────────────────────────────────────────────────

def run_uninstall_with_home(home: Path, *extra: str, check: bool = True):
    """Exercise legacy-v1 replay; context-bound CLI coverage lives in test_uninstall."""
    args = [
        "--home", str(home),
        "--claude-home", str(home / ".claude"),
        "--codex-home", str(home / ".codex"),
        "--bin-dir", str(home / "bin"),
        "--shell-rc", str(home / ".bashrc"),
        "--no-system-shell-rc", "--no-pip", "--no-git-hooks",
        *extra,
    ]
    stdout = io.StringIO()
    stderr = io.StringIO()
    report = uninstall.Report()
    manifest = Manifest(manifest_path(home))
    with redirect_stdout(stdout), redirect_stderr(stderr):
        uninstall.replay_manifest(
            manifest,
            report,
            dry_run="--dry-run" in extra,
            purge="--purge" in extra,
            no_pip=True,
            no_git_hooks=True,
        )
        report.print()
    returncode = 1 if report.failed else 0

    result = subprocess.CompletedProcess(
        [sys.executable, str(UNINSTALL), *args],
        returncode,
        stdout.getvalue(),
        stderr.getvalue(),
    )
    if check and returncode != 0:
        raise AssertionError(
            f"uninstall exited {returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


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
    """Verify scaffold and launchers record home-scoped side effects; dev_link owns hook-install coverage."""
    if __package__ and __package__.count('.') >= 1:
        from .. import _install_scaffold as scaffold
    else:
        import _install_scaffold as scaffold
    if __package__ and __package__.count('.') >= 1:
        from .. import _agent_launchers as launchers
    else:
        import _agent_launchers as launchers

    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "install-assistant-tools"
    source_bin = skill_dir / "_rtx/assets/bin"
    source_bin.mkdir(parents=True)
    for name in ["assistant", "_agent_launch.py", "assistant.bat"]:
        (source_bin / name).write_text("#!/bin/sh\necho stub\n")
        (source_bin / name).chmod(0o755)
    (repo / "profiles").mkdir()
    (repo / "profiles" / "assistant.config.toml").write_text(
        'model_instructions_file = "agents/assistant.md"\n'
    )
    (repo / "agents").mkdir()
    (repo / "agents" / "assistant.md").write_text("---\ndescription: t\n---\nBody.\n")

    home = tmp_path / "home"
    home.mkdir()

    scaffold.run(
        repo_root=repo, home=home, bin_dir=home / "bin", shell_rc=home / ".bashrc",
        environ={},
    )
    launchers.run(
        repo_root=repo,
        agents=["assistant"],
        home=home,
        bin_dir=home / "bin",
        codex_home=home / ".codex",
        claude_home=home / ".claude",
        shell_rc=home / ".bashrc",
        default_llm="claude",
        environ={},
    )

    mpath = manifest_path(home)
    assert mpath.exists()
    entries = json.loads(mpath.read_text())["entries"]
    kinds = {e["kind"] for e in entries}
    if sys.platform == "win32":
        assert "file" in kinds
        assert "registry_env" in kinds
    else:
        assert "file" in kinds
        assert "marker_block" in kinds
