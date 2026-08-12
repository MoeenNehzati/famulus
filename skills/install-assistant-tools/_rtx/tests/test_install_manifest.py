"""Tests for the install manifest: recording at install time, replay at uninstall.

The manifest is the source of truth for uninstall. Key property: uninstall
removes exactly what install recorded — including symlinks pointing at a
*stale* root (e.g. an old plugin-cache version dir), which the heuristic
fallback cannot know about.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
import stat
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from test_support.git_repository import GitTestRepository

from install_test_utils import REPO_ROOT, can_create_symlink

SCRIPTS = REPO_ROOT / "skills" / "install-assistant-tools" / "_rtx"
sys.path.insert(0, str(SCRIPTS))

if __package__ and __package__.count('.') >= 1:
    from .._state_record import (
        JournalMutation,
        Manifest,
        StateRecordError,
        TransactionJournal,
        manifest_path,
        manifest_state_root,
        recover_pending_mutation,
        snapshot_path_state,
    )
else:
    from _state_record import (  # noqa: E402
        JournalMutation,
        Manifest,
        StateRecordError,
        TransactionJournal,
        manifest_path,
        manifest_state_root,
        recover_pending_mutation,
        snapshot_path_state,
    )
if __package__ and __package__.count('.') >= 1:
    from .. import _install_uninstall as uninstall
else:
    import _install_uninstall as uninstall  # noqa: E402

UNINSTALL = SCRIPTS / "_install_uninstall.py"

# famulus-skip: category=capability-unavailable; reason=link-specific regressions require native link creation; alternate=non-link manifest, journal, recovery, and corruption tests run on every platform
requires_symlink = pytest.mark.skipif(
    not can_create_symlink(), reason="symlinks unavailable"
)


# ── Manifest unit tests ───────────────────────────────────────────────────────

def test_manifest_round_trip(tmp_path: Path):
    path = tmp_path / "manifest.json"
    m = Manifest(path, state_root=tmp_path)
    m.record("symlink", path=str(tmp_path / "a"), target=str(tmp_path / "b"))
    m.record("file", path=str(tmp_path / "c"))
    m.save()
    loaded = Manifest(path, state_root=tmp_path)
    assert len(loaded.entries) == 2
    assert loaded.entries[0]["kind"] == "symlink"


def test_manifest_dedupes_on_kind_and_path(tmp_path: Path):
    m = Manifest(tmp_path / "manifest.json", state_root=tmp_path)
    m.record("symlink", path="/x", target="/old")
    m.record("symlink", path="/x", target="/new")
    assert len(m.entries) == 1
    assert m.entries[0]["target"] == "/new"


def test_manifest_forget_removes_matching_kind_and_path(tmp_path: Path):
    path = tmp_path / "manifest.json"
    m = Manifest(path, state_root=tmp_path)
    m.record("symlink", path="/x", target="/target")
    m.record("file", path="/x")

    m.forget("symlink", path="/x")

    assert m.entries == [{"kind": "file", "path": "/x"}]
    assert Manifest(path, state_root=tmp_path).entries == m.entries


def test_manifest_path_is_under_home_state(tmp_path: Path):
    p = manifest_path(tmp_path)
    assert p == manifest_state_root(tmp_path) / "install-manifest.json"


def test_manifest_replace_is_atomic_and_parent_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from officina.common import atomic_files

    fsync_calls: list[Path] = []
    real_fsync = atomic_files.os.fsync

    def record_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            fsync_calls.append(tmp_path / "state")
        real_fsync(descriptor)

    monkeypatch.setattr(atomic_files.os, "fsync", record_fsync)
    state_root = tmp_path / "state"
    manifest = Manifest(
        state_root / "install-manifest.json", state_root=state_root
    )
    manifest.record("file", path=str(tmp_path / "bin" / "dispatcher"))

    assert json.loads(manifest.path.read_text(encoding="utf-8"))["version"] == 2
    assert manifest.path.parent in fsync_calls


@requires_symlink
def test_manifest_symlink_fails_closed_without_replacing_target(tmp_path: Path) -> None:
    target = tmp_path / "user-owned.json"
    target.write_text('{"owner": "user"}\n', encoding="utf-8")
    path = tmp_path / "install-manifest.json"
    path.symlink_to(target)

    with pytest.raises(StateRecordError, match="symbolic link"):
        Manifest(path, state_root=tmp_path)
    assert target.read_text(encoding="utf-8") == '{"owner": "user"}\n'


def _journal(*, pending_mutation: JournalMutation | None) -> TransactionJournal:
    return TransactionJournal(
        transaction_id="transaction-001",
        phase="prepared",
        prior_release_id="release-old",
        candidate_release_id="release-new",
        resolver_bundle_id="resolver-001",
        staged_key_id="key-001",
        pending_mutation=pending_mutation,
        completed_mutation_ids=(),
    )


def test_transaction_journal_round_trip_preserves_exact_mutation_metadata(
    tmp_path: Path,
) -> None:
    target = tmp_path / "bin" / "dispatcher"
    mutation = JournalMutation(
        mutation_id="mutation-001",
        kind="file",
        path=str(target),
        expected_before={"kind": "absent"},
        intended_after={
            "kind": "file",
            "mode": 0o755,
            "size": 11,
            "sha256": "1" * 64,
        },
        ownership_entry={"kind": "file", "path": str(target)},
    )
    path = tmp_path / "state" / "transaction-journal.json"

    _journal(pending_mutation=mutation).save(path, state_root=path.parent)

    assert TransactionJournal.load(path, state_root=path.parent) == _journal(
        pending_mutation=mutation
    )


def test_transaction_journal_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "transaction-journal.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StateRecordError, match="invalid transaction journal"):
        TransactionJournal.load(path, state_root=tmp_path)


def test_transaction_journal_rejects_incomplete_file_state(tmp_path: Path) -> None:
    target = tmp_path / "dispatcher"

    with pytest.raises(StateRecordError, match="intended_after"):
        JournalMutation(
            mutation_id="mutation-001",
            kind="file",
            path=str(target),
            expected_before={"kind": "absent"},
            intended_after={"kind": "file"},
            ownership_entry={"kind": "file", "path": str(target)},
        )


@requires_symlink
def test_transaction_journal_symlink_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "user-owned.json"
    target.write_text('{"owner": "user"}\n', encoding="utf-8")
    path = tmp_path / "transaction-journal.json"
    path.symlink_to(target)

    with pytest.raises(StateRecordError, match="symbolic link"):
        TransactionJournal.load(path, state_root=tmp_path)
    with pytest.raises(StateRecordError, match="symbolic link"):
        _journal(pending_mutation=None).save(path, state_root=tmp_path)
    assert target.read_text(encoding="utf-8") == '{"owner": "user"}\n'


def test_manifest_rejects_path_outside_explicit_state_root(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    outside = tmp_path / "outside" / "install-manifest.json"

    with pytest.raises(StateRecordError, match="outside state_root"):
        Manifest(outside, state_root=state_root)

    assert not outside.parent.exists()


@requires_symlink
def test_manifest_rejects_intermediate_directory_symlink(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    outside = tmp_path / "outside"
    state_root.mkdir()
    outside.mkdir()
    redirected = state_root / "redirected"
    redirected.symlink_to(outside, target_is_directory=True)

    with pytest.raises(StateRecordError):
        Manifest(
            redirected / "install-manifest.json", state_root=state_root
        ).record("file", path=str(tmp_path / "dispatcher"))

    assert not (outside / "install-manifest.json").exists()


def test_complete_journal_rejects_pending_mutation(tmp_path: Path) -> None:
    target = tmp_path / "dispatcher"
    mutation = JournalMutation(
        mutation_id="mutation-001",
        kind="file",
        path=str(target),
        expected_before={"kind": "absent"},
        intended_after={
            "kind": "file",
            "mode": 0o600,
            "size": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
        ownership_entry={"kind": "file", "path": str(target)},
    )

    with pytest.raises(StateRecordError, match="complete.*pending"):
        TransactionJournal(
            transaction_id="transaction-001",
            phase="complete",
            prior_release_id=None,
            candidate_release_id="release-new",
            resolver_bundle_id="resolver-001",
            staged_key_id=None,
            pending_mutation=mutation,
            completed_mutation_ids=(),
        )


def test_journal_rejects_pending_id_already_completed(tmp_path: Path) -> None:
    target = tmp_path / "dispatcher"
    mutation = JournalMutation(
        mutation_id="mutation-001",
        kind="file",
        path=str(target),
        expected_before={"kind": "absent"},
        intended_after={
            "kind": "file",
            "mode": 0o600,
            "size": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
        ownership_entry={"kind": "file", "path": str(target)},
    )

    with pytest.raises(StateRecordError, match="pending.*completed"):
        TransactionJournal(
            transaction_id="transaction-001",
            phase="prepared",
            prior_release_id=None,
            candidate_release_id="release-new",
            resolver_bundle_id="resolver-001",
            staged_key_id=None,
            pending_mutation=mutation,
            completed_mutation_ids=("mutation-001",),
        )


@pytest.mark.parametrize(
    ("phase", "pending_completed"),
    [("complete", False), ("prepared", True)],
)
def test_transaction_journal_load_rejects_impossible_state_machine_transition(
    tmp_path: Path, phase: str, pending_completed: bool
) -> None:
    target = tmp_path / "dispatcher"
    mutation = JournalMutation(
        mutation_id="mutation-001",
        kind="file",
        path=str(target),
        expected_before={"kind": "absent"},
        intended_after={
            "kind": "file",
            "mode": 0o600,
            "size": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
        ownership_entry={"kind": "file", "path": str(target)},
    )
    path = tmp_path / "transaction-journal.json"
    payload = {
        "version": 1,
        "transaction_id": "transaction-001",
        "phase": phase,
        "prior_release_id": None,
        "candidate_release_id": "release-new",
        "resolver_bundle_id": "resolver-001",
        "staged_key_id": None,
        "pending_mutation": {
            "mutation_id": mutation.mutation_id,
            "kind": mutation.kind,
            "path": mutation.path,
            "expected_before": mutation.expected_before,
            "intended_after": mutation.intended_after,
            "ownership_entry": mutation.ownership_entry,
        },
        "completed_mutation_ids": ["mutation-001"] if pending_completed else [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateRecordError, match="invalid transaction journal"):
        TransactionJournal.load(path, state_root=tmp_path)


def test_recovery_performs_untouched_pending_mutation_and_verifies_result(
    tmp_path: Path,
) -> None:
    target = tmp_path / "dispatcher"
    desired = b"dispatcher\n"
    intended = {
        "kind": "file",
        "mode": 0o644,
        "size": 11,
        "sha256": hashlib.sha256(desired).hexdigest(),
    }
    mutation = JournalMutation(
        mutation_id="mutation-001",
        kind="file",
        path=str(target),
        expected_before={"kind": "absent"},
        intended_after=intended,
        ownership_entry={"kind": "file", "path": str(target)},
    )

    def apply(_mutation: JournalMutation) -> None:
        target.write_bytes(desired)
        target.chmod(0o644)

    manifest = Manifest(tmp_path / "install-manifest.json", state_root=tmp_path)
    recovered = recover_pending_mutation(
        _journal(pending_mutation=mutation),
        manifest=manifest,
        apply_mutation=apply,
    )

    assert recovered.pending_mutation is None
    assert recovered.completed_mutation_ids == ("mutation-001",)
    assert snapshot_path_state(target) == intended


def test_recovery_adopts_already_completed_pending_mutation(tmp_path: Path) -> None:
    target = tmp_path / "dispatcher"
    target.write_bytes(b"dispatcher\n")
    intended = snapshot_path_state(target)
    mutation = JournalMutation(
        mutation_id="mutation-001",
        kind="file",
        path=str(target),
        expected_before={"kind": "absent"},
        intended_after=intended,
        ownership_entry={"kind": "file", "path": str(target)},
    )
    called = False

    def unexpected_apply(_mutation: JournalMutation) -> None:
        nonlocal called
        called = True

    manifest_path_value = tmp_path / "install-manifest.json"
    manifest = Manifest(manifest_path_value, state_root=tmp_path)
    recovered = recover_pending_mutation(
        _journal(pending_mutation=mutation),
        manifest=manifest,
        apply_mutation=unexpected_apply,
    )

    assert called is False
    assert recovered.pending_mutation is None
    assert recovered.completed_mutation_ids == ("mutation-001",)
    assert Manifest(manifest_path_value, state_root=tmp_path).entries == [
        {"kind": "file", "path": str(target)}
    ]


def test_recovery_fails_closed_on_third_path_state(tmp_path: Path) -> None:
    target = tmp_path / "dispatcher"
    target.write_bytes(b"user-owned\n")
    mutation = JournalMutation(
        mutation_id="mutation-001",
        kind="file",
        path=str(target),
        expected_before={"kind": "absent"},
        intended_after={
            "kind": "file",
            "mode": 0o644,
            "size": 11,
            "sha256": hashlib.sha256(b"dispatcher\n").hexdigest(),
        },
        ownership_entry={"kind": "file", "path": str(target)},
    )
    called = False

    def unexpected_apply(_mutation: JournalMutation) -> None:
        nonlocal called
        called = True

    with pytest.raises(StateRecordError, match="third state"):
        manifest = Manifest(tmp_path / "install-manifest.json", state_root=tmp_path)
        recover_pending_mutation(
            _journal(pending_mutation=mutation),
            manifest=manifest,
            apply_mutation=unexpected_apply,
        )
    assert called is False
    assert target.read_bytes() == b"user-owned\n"


@requires_symlink
def test_snapshot_regular_file_uses_one_no_follow_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "dispatcher"
    displaced = tmp_path / "dispatcher-original"
    outside = tmp_path / "outside"
    original = b"trusted dispatcher\n"
    target.write_bytes(original)
    target.chmod(0o644)
    outside.write_bytes(b"outside bytes are longer\n")
    real_open = os.open
    swapped = False

    def open_then_swap(*args: object, **kwargs: object) -> int:
        nonlocal swapped
        descriptor = real_open(*args, **kwargs)
        path_argument = Path(args[0]) if args else None
        if not swapped and path_argument == target:
            target.rename(displaced)
            target.symlink_to(outside)
            swapped = True
        return descriptor

    monkeypatch.setattr(os, "open", open_then_swap)

    state = snapshot_path_state(target)

    assert swapped is True
    assert state == {
        "kind": "file",
        "mode": 0o644,
        "size": len(original),
        "sha256": hashlib.sha256(original).hexdigest(),
    }


# famulus-skip: category=platform-contract; reason=POSIX FIFO creation is unavailable on some hosts; alternate=regular-file descriptor coherence and third-state recovery tests cover the shared snapshot contract
@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "mkfifo"), reason="POSIX FIFOs unavailable"
)
def test_snapshot_and_recovery_classify_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "installer-fifo"
    os.mkfifo(fifo)
    manifest_path_value = tmp_path / "install-manifest.json"
    script = """
import json
import stat
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
sys.path.insert(0, str(repo_root / "src"))
sys.path.insert(0, str(repo_root / "skills" / "install-assistant-tools" / "_rtx"))
from _state_record import (
    JournalMutation,
    Manifest,
    TransactionJournal,
    recover_pending_mutation,
    snapshot_path_state,
)

fifo = Path(sys.argv[2])
manifest_path = Path(sys.argv[3])
declared = {"kind": "other", "mode": stat.S_IMODE(fifo.lstat().st_mode)}
state = snapshot_path_state(fifo)
mutation = JournalMutation(
    mutation_id="fifo-001",
    kind="file",
    path=str(fifo),
    expected_before={"kind": "absent"},
    intended_after=declared,
    ownership_entry=None,
)
journal = TransactionJournal(
    transaction_id="transaction-001",
    phase="prepared",
    prior_release_id=None,
    candidate_release_id="release-new",
    resolver_bundle_id="resolver-001",
    staged_key_id=None,
    pending_mutation=mutation,
    completed_mutation_ids=(),
)
recovered = recover_pending_mutation(
    journal,
    manifest=Manifest(manifest_path, state_root=manifest_path.parent),
    apply_mutation=lambda _mutation: None,
)
print(json.dumps({
    "state": state,
    "declared": declared,
    "pending": recovered.pending_mutation is not None,
    "completed": list(recovered.completed_mutation_ids),
}))
"""

    result = subprocess.run(
        [sys.executable, "-c", script, str(REPO_ROOT), str(fifo), str(manifest_path_value)],
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    )
    payload = json.loads(result.stdout)
    expected_state = {
        "kind": "other",
        "mode": stat.S_IMODE(fifo.lstat().st_mode),
    }
    assert payload == {
        "state": expected_state,
        "declared": expected_state,
        "pending": False,
        "completed": ["fifo-001"],
    }


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


@requires_symlink
def test_setup_symlinks_records_links(tmp_path: Path):
    if __package__ and __package__.count('.') >= 1:
        from .. import _config_bridge as dev_link
    else:
        import _config_bridge as dev_link

    repo = _make_repo_for_manifest_tests(tmp_path)
    claude_home = tmp_path / ".claude"
    manifest = Manifest(tmp_path / "manifest.json", state_root=tmp_path)
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
    if __package__ and __package__.count('.') >= 1:
        from .. import _config_bridge as dev_link
    else:
        import _config_bridge as dev_link

    repo = _make_repo_for_manifest_tests(tmp_path)
    manifest = Manifest(tmp_path / "manifest.json", state_root=tmp_path)
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
    manifest = Manifest(tmp_path / "manifest.json", state_root=tmp_path)
    ensure_rc_vars(rc, {"PATH": 'export PATH="/bin:$PATH"'}, False, manifest=manifest)
    blocks = [e for e in manifest.entries if e["kind"] == "marker_block"]
    assert any(e["path"] == str(rc) for e in blocks)


# ── Uninstall replay ──────────────────────────────────────────────────────────

def run_uninstall_with_home(home: Path, *extra: str, check: bool = True):
    """Exercise manifest replay through real parser/main while the companion suite retains executable smoke coverage."""
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
    with (
        patch.object(sys, "argv", [str(UNINSTALL), *args]),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        try:
            uninstall.main()
        except SystemExit as exc:
            returncode = int(exc.code or 0)
        else:
            returncode = 0

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


@requires_symlink
def test_uninstall_replays_manifest_removing_stale_root_symlink(tmp_path: Path):
    """The drift case: link points at an old plugin-cache dir, not the current repo."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    old_root = tmp_path / "plugins-cache" / "old-version"
    old_root.mkdir(parents=True)
    (old_root / "skills").mkdir()
    link = home / ".claude" / "skills"
    link.symlink_to(old_root / "skills")

    m = Manifest(manifest_path(home), state_root=manifest_state_root(home))
    m.record("symlink", path=str(link), target=str(old_root / "skills"))
    m.save()

    run_uninstall_with_home(home)
    assert not link.is_symlink()


@requires_symlink
def test_uninstall_replay_skips_retargeted_symlink(tmp_path: Path):
    """A link the user re-pointed elsewhere since install must be preserved."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    users_dir = tmp_path / "users-own"
    users_dir.mkdir()
    link = home / ".claude" / "skills"
    link.symlink_to(users_dir)

    m = Manifest(manifest_path(home), state_root=manifest_state_root(home))
    m.record("symlink", path=str(link), target=str(tmp_path / "somewhere-else"))
    m.save()

    run_uninstall_with_home(home)
    assert link.is_symlink()


@requires_symlink
def test_uninstall_removes_manifest_after_clean_run(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    target = tmp_path / "t"
    target.mkdir()
    link = home / ".claude" / "skills"
    link.symlink_to(target)

    m = Manifest(manifest_path(home), state_root=manifest_state_root(home))
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

    m = Manifest(manifest_path(home), state_root=manifest_state_root(home))
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

    scaffold.run(repo_root=repo, home=home, bin_dir=home / "bin", shell_rc=home / ".bashrc")
    launchers.run(
        repo_root=repo,
        agents=["assistant"],
        home=home,
        bin_dir=home / "bin",
        codex_home=home / ".codex",
        claude_home=home / ".claude",
        shell_rc=home / ".bashrc",
        default_llm="claude",
    )

    mpath = manifest_path(home)
    assert mpath.exists()
    entries = json.loads(mpath.read_text())["entries"]
    kinds = {e["kind"] for e in entries}
    if sys.platform == "win32":
        assert "file" in kinds
        assert "registry_env" in kinds
    else:
        assert "symlink" in kinds
        assert "marker_block" in kinds
