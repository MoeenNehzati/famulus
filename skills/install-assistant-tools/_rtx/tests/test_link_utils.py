from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _state_record as state_record
    from .._fs_links import make_copy, make_link
else:
    import _state_record as state_record
    from _fs_links import make_copy, make_link


def _recorder(tmp_path: Path) -> state_record.MutationRecorder:
    state_root = tmp_path / "state"
    journal = state_record.TransactionJournal(
        transaction_id="1" * 32,
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
    journal.save(journal_path, state_root=state_root)
    return state_record.MutationRecorder(
        journal=journal,
        journal_path=journal_path,
        state_root=state_root,
        manifest=state_record.Manifest(
            state_root / "install-manifest.json", state_root=state_root
        ),
    )


def test_make_link_creates_symlink(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello")
    dst = tmp_path / "dst.txt"

    make_link(
        src,
        dst,
        dry_run=False,
        recorder=_recorder(tmp_path),
        operation_key="test.link",
    )

    assert dst.is_symlink()
    assert dst.resolve() == src.resolve()


def test_make_link_rejects_missing_source(tmp_path):
    src = tmp_path / "missing.txt"
    dst = tmp_path / "dst.txt"

    with pytest.raises(state_record.InstallerMutationError, match="source is absent"):
        make_link(
            src,
            dst,
            dry_run=False,
            recorder=_recorder(tmp_path),
            operation_key="test.missing-link",
        )

    assert not dst.exists()


def test_make_link_revalidates_source_identity_immediately_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src.txt"
    replacement = tmp_path / "replacement.txt"
    src.write_text("original", encoding="utf-8")
    replacement.write_text("replacement", encoding="utf-8")
    dst = tmp_path / "dst.txt"
    recorder = _recorder(tmp_path)
    original_mutate = recorder.mutate

    def mutate_after_source_swap(**kwargs):
        os.replace(replacement, src)
        return original_mutate(**kwargs)

    monkeypatch.setattr(recorder, "mutate", mutate_after_source_swap)

    with pytest.raises(
        state_record.InstallerMutationError, match="link source changed"
    ):
        make_link(
            src,
            dst,
            dry_run=False,
            recorder=recorder,
            operation_key="test.link-source-race",
        )

    assert not dst.exists()


def test_make_link_requires_recorder_before_target_or_build_creation(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello", encoding="utf-8")
    dst = tmp_path / "missing-parent" / "dst.txt"

    with pytest.raises(state_record.InstallerMutationError, match="durable mutation"):
        make_link(
            src,
            dst,
            dry_run=False,
            recorder=None,
            operation_key="test.no-recorder",
        )

    assert not dst.parent.exists()
    assert not list(tmp_path.glob(".famulus-build-*"))


def test_make_copy_creates_copy(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello")
    dst = tmp_path / "dst.txt"

    make_copy(
        src,
        dst,
        dry_run=False,
        recorder=_recorder(tmp_path),
        operation_key="test.copy",
    )

    assert dst.read_text() == "hello"


# famulus-skip: category=platform-contract; reason=POSIX special mode bits are not represented on every host; alternate=journal mode-domain tests cover portable validation
@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
def test_make_copy_drops_source_special_bits_before_recording(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("hello", encoding="utf-8")
    src.chmod(0o4755)
    dst = tmp_path / "dst.txt"
    recorder = _recorder(tmp_path)

    make_copy(
        src,
        dst,
        dry_run=False,
        recorder=recorder,
        operation_key="test.copy-special-mode",
    )

    assert dst.stat().st_mode & 0o7777 == 0o755
    assert recorder.journal.pending_mutation is None


def test_make_copy_preserves_existing_copy(tmp_path, capsys):
    src = tmp_path / "src.txt"
    src.write_text("v2")
    dst = tmp_path / "dst.txt"
    dst.write_text("v1")

    make_copy(
        src,
        dst,
        dry_run=False,
        recorder=_recorder(tmp_path),
        operation_key="test.copy-preserve",
    )

    # Existing file is NOT overwritten - keeps machine-local state
    assert dst.read_text() == "v1"
    assert "SKIP (exists, keeping machine-local state)" in capsys.readouterr().out
