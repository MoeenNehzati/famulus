from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _state_record as state_record
    from .._shell_block import BLOCK_BEGIN, BLOCK_END, ensure_rc_vars
else:
    import _state_record as state_record
    from _shell_block import BLOCK_BEGIN, BLOCK_END, ensure_rc_vars


def _recorder(
    tmp_path: Path,
    *,
    journal: state_record.TransactionJournal | None = None,
) -> state_record.MutationRecorder:
    state_root = tmp_path / "state"
    selected = journal or state_record.TransactionJournal(
        transaction_id="2" * 32,
        phase="prepared",
        prior_release_id="release-old",
        candidate_release_id="release-new",
        resolver_bundle_id="resolver-001",
        certificate_key_id="sha256:" + "a" * 64,
        certificate_intent=None,
        certificate_progress="committed",
        pending_mutation=None,
        completed_mutation_ids=(),
    )
    journal_path = state_root / "transaction-journal.json"
    if not journal_path.exists():
        selected.save(journal_path, state_root=state_root)
    return state_record.MutationRecorder(
        journal=selected,
        journal_path=journal_path,
        state_root=state_root,
        manifest=state_record.Manifest(
            state_root / "install-manifest.json", state_root=state_root
        ),
    )


def test_ensure_rc_vars_writes_new_block(tmp_path):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# existing content\n")
    rc_file.chmod(0o644)

    ensure_rc_vars(
        rc_file,
        {"PATH": 'export PATH="/bin/dir:$PATH"'},
        dry_run=False,
        recorder=_recorder(tmp_path),
        operation_key="test.shell.path",
    )

    content = rc_file.read_text()
    assert "# existing content" in content
    assert BLOCK_BEGIN in content
    assert 'export PATH="/bin/dir:$PATH"' in content
    assert BLOCK_END in content
    assert rc_file.stat().st_mode & 0o777 == 0o644


def test_ensure_rc_vars_merges_without_clobbering_other_vars(tmp_path):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")
    recorder = _recorder(tmp_path)

    ensure_rc_vars(
        rc_file,
        {"PATH": 'export PATH="/bin/dir:$PATH"'},
        dry_run=False,
        recorder=recorder,
        operation_key="test.shell.path",
    )
    ensure_rc_vars(
        rc_file,
        {"ASSISTANT_DEFAULT": "export ASSISTANT_DEFAULT=claude"},
        dry_run=False,
        recorder=recorder,
        operation_key="test.shell.default",
    )

    content = rc_file.read_text()
    assert 'export PATH="/bin/dir:$PATH"' in content
    assert "export ASSISTANT_DEFAULT=claude" in content
    # Only one managed block, not two
    assert content.count(BLOCK_BEGIN) == 1


def test_ensure_rc_vars_replaces_existing_value_for_same_key(tmp_path):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")
    recorder = _recorder(tmp_path)

    ensure_rc_vars(
        rc_file,
        {"ASSISTANT_DEFAULT": "export ASSISTANT_DEFAULT=claude"},
        dry_run=False,
        recorder=recorder,
        operation_key="test.shell.default",
    )
    ensure_rc_vars(
        rc_file,
        {"ASSISTANT_DEFAULT": "export ASSISTANT_DEFAULT=codex"},
        dry_run=False,
        recorder=recorder,
        operation_key="test.shell.default",
    )

    content = rc_file.read_text()
    assert "export ASSISTANT_DEFAULT=codex" in content
    assert "export ASSISTANT_DEFAULT=claude" not in content


def test_ensure_rc_vars_does_not_accumulate_blank_lines_across_repeated_writes(tmp_path):
    """Regression: three separate callers (scaffold/launchers/dev_link) each
    writing their one var, one after another, must not each add another
    blank separator line before the block."""
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# user line\n")
    recorder = _recorder(tmp_path)

    ensure_rc_vars(
        rc_file,
        {"AI": 'export AI="/repo"'},
        dry_run=False,
        recorder=recorder,
        operation_key="test.shell.ai",
    )
    ensure_rc_vars(
        rc_file,
        {"PATH": 'export PATH="/bin:$PATH"'},
        dry_run=False,
        recorder=recorder,
        operation_key="test.shell.path",
    )
    ensure_rc_vars(
        rc_file,
        {"ASSISTANT_DEFAULT": "export ASSISTANT_DEFAULT=claude"},
        dry_run=False,
        recorder=recorder,
        operation_key="test.shell.default",
    )

    content = rc_file.read_text()
    assert content.startswith("# user line\n\n" + BLOCK_BEGIN)


def test_ensure_rc_vars_dry_run_does_not_write(tmp_path, capsys):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("original\n")

    ensure_rc_vars(
        rc_file,
        {"PATH": 'export PATH="/bin/dir:$PATH"'},
        dry_run=True,
        recorder=None,
        operation_key="test.shell.path",
    )

    assert rc_file.read_text() == "original\n"
    assert "Would update" in capsys.readouterr().out


def test_ensure_rc_vars_preserves_existing_mode(tmp_path):
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("existing\n", encoding="utf-8")
    rc_file.chmod(0o640)

    ensure_rc_vars(
        rc_file,
        {"PATH": 'export PATH="/bin:$PATH"'},
        dry_run=False,
        recorder=_recorder(tmp_path),
        operation_key="test.shell.path",
    )

    assert rc_file.stat().st_mode & 0o777 == 0o640


def test_ensure_rc_vars_creates_absent_file_with_private_mode(tmp_path):
    rc_file = tmp_path / ".bashrc"

    ensure_rc_vars(
        rc_file,
        {"PATH": 'export PATH="/bin:$PATH"'},
        dry_run=False,
        recorder=_recorder(tmp_path),
        operation_key="test.shell.path",
    )

    assert rc_file.stat().st_mode & 0o777 == 0o600


def test_ensure_rc_vars_requires_recorder_before_parent_or_build_creation(tmp_path):
    rc_file = tmp_path / "missing-parent" / ".bashrc"

    with pytest.raises(state_record.InstallerMutationError, match="durable mutation"):
        ensure_rc_vars(
            rc_file,
            {"PATH": 'export PATH="/bin:$PATH"'},
            dry_run=False,
            recorder=None,
            operation_key="test.shell.path",
        )

    assert not rc_file.parent.exists()


def test_ensure_rc_vars_resumes_pending_after_post_publication_crash(
    tmp_path,
    monkeypatch,
):
    if __package__ and __package__.count('.') >= 1:
        from .. import _shell_block as shell_block
    else:
        import _shell_block as shell_block

    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("existing\n", encoding="utf-8")
    first = _recorder(tmp_path)
    real_publish = shell_block.atomic_publish_bytes

    def crash_after_publish(*args, **kwargs):
        real_publish(*args, **kwargs)
        raise RuntimeError("post-publication crash")

    monkeypatch.setattr(shell_block, "atomic_publish_bytes", crash_after_publish)
    with pytest.raises(RuntimeError, match="post-publication"):
        ensure_rc_vars(
            rc_file,
            {"PATH": 'export PATH="/bin:$PATH"'},
            dry_run=False,
            recorder=first,
            operation_key="test.shell.path",
        )
    durable = state_record.TransactionJournal.load(
        tmp_path / "state" / "transaction-journal.json",
        state_root=tmp_path / "state",
    )
    assert durable.pending_mutation is not None

    monkeypatch.setattr(shell_block, "atomic_publish_bytes", real_publish)
    ensure_rc_vars(
        rc_file,
        {"PATH": 'export PATH="/bin:$PATH"'},
        dry_run=False,
        recorder=_recorder(tmp_path, journal=durable),
        operation_key="test.shell.path",
    )

    assert 'export PATH="/bin:$PATH"' in rc_file.read_text(encoding="utf-8")


def test_ensure_rc_vars_rejects_oversized_and_unclosed_input(tmp_path):
    oversized = tmp_path / ".oversized"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(state_record.StateRecordError, match="closed bound"):
        ensure_rc_vars(
            oversized,
            {"PATH": "export PATH=/bin"},
            dry_run=False,
            recorder=_recorder(tmp_path),
            operation_key="test.shell.oversized",
        )

    malformed = tmp_path / ".malformed"
    malformed.write_text(BLOCK_BEGIN + "\nexport PATH=/old\n", encoding="utf-8")
    with pytest.raises(state_record.InstallerMutationError, match="not closed"):
        ensure_rc_vars(
            malformed,
            {"PATH": "export PATH=/bin"},
            dry_run=False,
            recorder=_recorder(tmp_path / "other"),
            operation_key="test.shell.malformed",
        )


def test_ensure_rc_vars_rejects_existing_crlf_block_before_recording(
    tmp_path: Path,
) -> None:
    rc_file = tmp_path / ".bashrc"
    original = (
        b"# user content\r\n"
        + BLOCK_BEGIN.encode("utf-8")
        + b"\r\nexport PATH=/old\r\n"
        + BLOCK_END.encode("utf-8")
        + b"\r\n"
    )
    rc_file.write_bytes(original)
    recorder = _recorder(tmp_path)

    with pytest.raises(state_record.InstallerMutationError, match="carriage return"):
        ensure_rc_vars(
            rc_file,
            {"PATH": "export PATH=/new"},
            dry_run=False,
            recorder=recorder,
            operation_key="test.shell.crlf",
        )

    assert rc_file.read_bytes() == original
    assert rc_file.read_bytes().count(BLOCK_BEGIN.encode("utf-8")) == 1
    assert recorder.journal.pending_mutation is None


@pytest.mark.parametrize(
    ("key", "line"),
    [
        ("PATH", "export OTHER=/bin"),
        ("PATH", "export PATH=/bin\nexport OTHER=/tmp"),
        ("PATH", "export PATH=/bin\rignored"),
        ("PATH", "export PATH=/bin\x00ignored"),
        ("PATH", f"export PATH=/bin {BLOCK_BEGIN}"),
        ("BAD-NAME", "export BAD-NAME=/bin"),
        ("PATH", " export PATH=/bin"),
    ],
)
def test_ensure_rc_vars_rejects_noncanonical_update_before_recording(
    tmp_path: Path,
    key: str,
    line: str,
) -> None:
    rc_file = tmp_path / ".bashrc"
    recorder = _recorder(tmp_path)

    with pytest.raises(state_record.InstallerMutationError, match="rc update"):
        ensure_rc_vars(
            rc_file,
            {key: line},
            dry_run=False,
            recorder=recorder,
            operation_key="test.shell.invalid-update",
        )

    assert recorder.journal.pending_mutation is None
    assert not rc_file.exists()


def test_ensure_rc_vars_reparses_rendered_block_before_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if __package__ and __package__.count('.') >= 1:
        from .. import _shell_block as shell_block
    else:
        import _shell_block as shell_block

    rc_file = tmp_path / ".bashrc"
    recorder = _recorder(tmp_path)
    real_parse = shell_block._parse_block_vars
    calls = 0

    def corrupt_rendered_parse(lines: list[str]) -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_parse(lines)
        return {"OTHER": "export OTHER=corrupted"}

    monkeypatch.setattr(shell_block, "_parse_block_vars", corrupt_rendered_parse)

    with pytest.raises(state_record.InstallerMutationError, match="rendered rc block"):
        ensure_rc_vars(
            rc_file,
            {"PATH": "export PATH=/bin"},
            dry_run=False,
            recorder=recorder,
            operation_key="test.shell.rendered-reparse",
        )

    assert calls == 2
    assert recorder.journal.pending_mutation is None
    assert not rc_file.exists()
