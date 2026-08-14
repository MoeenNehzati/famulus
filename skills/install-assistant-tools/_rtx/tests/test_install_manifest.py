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
from officina.common.certificate_intents import (
    CertificateMutationIntent,
    CertificatePublicFileIntent,
    canonical_certificate_intent_bytes,
)

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
        transaction_id="1" * 32,
        phase="prepared",
        prior_release_id="release-old",
        candidate_release_id="release-new",
        resolver_bundle_id="resolver-001",
        certificate_key_id="sha256:" + "a" * 64,
        certificate_intent=None,
        certificate_progress="committed",
        pending_mutation=pending_mutation,
        completed_mutation_ids=(),
    )


def _certificate_intent() -> CertificateMutationIntent:
    key_id = "sha256:" + "a" * 64
    return CertificateMutationIntent(
        schema_version=1,
        transaction_id="1" * 32,
        intent_id="2" * 32,
        action="create",
        backend_identity="keyring.backends.SecretService.Keyring",
        active_key_id=key_id,
        prior_key_id=None,
        public_files=(
            CertificatePublicFileIntent(
                key_id=key_id,
                size=7,
                sha256=hashlib.sha256(b"public\n").hexdigest(),
                quarantine_id="3" * 32,
            ),
        ),
        secret_target=(
            "Famulus:skill-certifier:ed25519-private-key:" + key_id
        ),
    )


def _certificate_selector_mutation(
    tmp_path: Path,
    intent: CertificateMutationIntent,
    *,
    path: Path | None = None,
    expected_before: dict[str, object] | None = None,
    intended_after: dict[str, object] | None = None,
    ownership_entry: dict[str, object] | None = None,
) -> JournalMutation:
    active_bytes = (intent.active_key_id + "\n").encode("ascii")
    return JournalMutation(
        mutation_id=hashlib.sha256(
            b"famulus-certificate-selector-mutation-v1\x00"
            + canonical_certificate_intent_bytes(intent)
        ).hexdigest()[:32],
        kind="certificate_selector",
        path=str(path or tmp_path / "certificates" / "active-key-id"),
        expected_before=(
            {"kind": "absent"}
            if expected_before is None
            else expected_before
        ),
        intended_after=(
            {
                "kind": "file",
                "mode": 0o600,
                "size": len(active_bytes),
                "sha256": hashlib.sha256(active_bytes).hexdigest(),
            }
            if intended_after is None
            else intended_after
        ),
        ownership_entry=ownership_entry,
    )


def _journal_v2(
    *,
    phase: str = "prepared",
    certificate_progress: str = "planned",
    certificate_key_id: str | None = None,
    certificate_intent: CertificateMutationIntent | None = None,
    pending_mutation: JournalMutation | None = None,
) -> TransactionJournal:
    intent = _certificate_intent() if certificate_intent is None else certificate_intent
    key_id = intent.active_key_id if certificate_key_id is None else certificate_key_id
    return TransactionJournal(
        transaction_id=intent.transaction_id,
        phase=phase,
        prior_release_id="release-old",
        candidate_release_id="release-new",
        resolver_bundle_id="resolver-001",
        certificate_key_id=key_id,
        certificate_intent=intent,
        certificate_progress=certificate_progress,
        pending_mutation=pending_mutation,
        completed_mutation_ids=(),
    )


def test_transaction_journal_v2_round_trip_preserves_certificate_intent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "transaction-journal.json"
    journal = _journal_v2()

    journal.save(path, state_root=path.parent)

    assert json.loads(path.read_bytes())["version"] == 2
    assert TransactionJournal.load(path, state_root=path.parent) == journal


def test_transaction_journal_rejects_every_v1_record_without_inference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "transaction-journal.json"
    payload = {
        "version": 1,
        "transaction_id": "transaction-001",
        "phase": "prepared",
        "prior_release_id": None,
        "candidate_release_id": "release-new",
        "resolver_bundle_id": "resolver-001",
        "staged_key_id": "sha256:" + "a" * 64,
        "pending_mutation": None,
        "completed_mutation_ids": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        StateRecordError,
        match=r"^invalid transaction journal at .*: unsupported transaction journal version$",
    ):
        TransactionJournal.load(path, state_root=tmp_path)


@pytest.mark.parametrize(
    (
        "phase",
        "progress",
        "key_mode",
        "intent_mode",
        "pending_kind",
        "error",
    ),
    [
        ("prepared", "none", "active", "present", None, "none.*null"),
        ("prepared", "planned", "null", "present", None, "planned.*key"),
        ("prepared", "planned", "active", "null", None, "planned.*intent"),
        ("prepared", "planned", "other", "present", None, "key.*intent"),
        ("prepared", "staged", "active", "null", None, "staged.*intent"),
        ("prepared", "committed", "null", "null", None, "committed.*key"),
        ("complete", "staged", "active", "present", None, "complete.*committed"),
        ("preparing", "none", "null", "null", "file", "preparing.*pending"),
    ],
)
def test_transaction_journal_v2_rejects_certificate_state_invariant_breaks(
    tmp_path: Path,
    phase: str,
    progress: str,
    key_mode: str,
    intent_mode: str,
    pending_kind: str | None,
    error: str,
) -> None:
    intent = _certificate_intent()
    key_id = {
        "active": intent.active_key_id,
        "other": "sha256:" + "b" * 64,
        "null": None,
    }[key_mode]
    pending = None
    if pending_kind is not None:
        target = tmp_path / "dispatcher"
        pending = JournalMutation(
            mutation_id="4" * 32,
            kind=pending_kind,
            path=str(target),
            expected_before={"kind": "absent"},
            intended_after={"kind": "file", "mode": 0o600, "size": 0, "sha256": hashlib.sha256(b"").hexdigest()},
            ownership_entry=None,
        )

    with pytest.raises(StateRecordError, match=error):
        TransactionJournal(
            transaction_id=intent.transaction_id,
            phase=phase,
            prior_release_id=None,
            candidate_release_id="release-new",
            resolver_bundle_id="resolver-001",
            certificate_key_id=key_id,
            certificate_intent=intent if intent_mode == "present" else None,
            certificate_progress=progress,
            pending_mutation=pending,
            completed_mutation_ids=(),
        )


def test_complete_journal_accepts_verified_reuse_without_intent() -> None:
    key_id = "sha256:" + "c" * 64

    journal = TransactionJournal(
        transaction_id="1" * 32,
        phase="complete",
        prior_release_id=None,
        candidate_release_id="release-new",
        resolver_bundle_id="resolver-001",
        certificate_key_id=key_id,
        certificate_intent=None,
        certificate_progress="committed",
        pending_mutation=None,
        completed_mutation_ids=(),
    )

    assert journal.certificate_key_id == key_id


def test_certificate_selector_mutation_requires_staged_intent_and_exact_kind(
    tmp_path: Path,
) -> None:
    intent = _certificate_intent()
    selector = _certificate_selector_mutation(tmp_path, intent)

    journal = _journal_v2(
        certificate_progress="staged",
        pending_mutation=selector,
    )

    assert journal.pending_mutation is selector


def test_certificate_selector_journal_rejects_forged_canonical_mutation_id(
    tmp_path: Path,
) -> None:
    intent = _certificate_intent()
    selector = JournalMutation(
        mutation_id="4" * 32,
        kind="certificate_selector",
        path=str(tmp_path / "active-key-id"),
        expected_before={"kind": "absent"},
        intended_after={
            "kind": "file",
            "mode": 0o600,
            "size": len(intent.active_key_id) + 1,
            "sha256": hashlib.sha256(
                (intent.active_key_id + "\n").encode("ascii")
            ).hexdigest(),
        },
        ownership_entry=None,
    )

    with pytest.raises(StateRecordError, match="selector mutation ID"):
        _journal_v2(
            certificate_progress="staged",
            pending_mutation=selector,
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("path_basename", "selector path"),
        ("ownership", "selector ownership"),
        ("before_kind", "expected-before"),
        ("after_mode", "intended-after"),
        ("after_size", "intended-after"),
        ("after_digest", "intended-after"),
    ],
)
def test_certificate_selector_journal_binds_every_transition_component(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    """Break caught: a selector ACK authorizes a different path or byte state."""

    intent = _certificate_intent()
    active_bytes = (intent.active_key_id + "\n").encode("ascii")
    kwargs: dict[str, object] = {}
    if mutation == "path_basename":
        kwargs["path"] = tmp_path / "certificates" / "other-selector"
    elif mutation == "ownership":
        path = tmp_path / "certificates" / "active-key-id"
        kwargs["ownership_entry"] = {"kind": "file", "path": str(path)}
    elif mutation == "before_kind":
        kwargs["expected_before"] = {
            "kind": "file",
            "mode": 0o600,
            "size": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        }
    else:
        intended = {
            "kind": "file",
            "mode": 0o600,
            "size": len(active_bytes),
            "sha256": hashlib.sha256(active_bytes).hexdigest(),
        }
        if mutation == "after_mode":
            intended["mode"] = 0o644
        elif mutation == "after_size":
            intended["size"] = len(active_bytes) + 1
        else:
            intended["sha256"] = hashlib.sha256(b"wrong\n").hexdigest()
        kwargs["intended_after"] = intended

    selector = _certificate_selector_mutation(tmp_path, intent, **kwargs)

    with pytest.raises(StateRecordError, match=error):
        _journal_v2(
            certificate_progress="staged",
            pending_mutation=selector,
        )


def test_certificate_selector_journal_binds_prior_selector_snapshot(
    tmp_path: Path,
) -> None:
    """Break caught: recovery accepts absent-before for an existing prior selector."""

    payload = _certificate_intent().to_dict()
    prior_key_id = "sha256:" + "c" * 64
    payload["prior_key_id"] = prior_key_id
    intent = CertificateMutationIntent.from_dict(payload)
    selector = _certificate_selector_mutation(tmp_path, intent)

    with pytest.raises(StateRecordError, match="expected-before"):
        _journal_v2(
            certificate_progress="staged",
            certificate_intent=intent,
            pending_mutation=selector,
        )

    prior_bytes = (prior_key_id + "\n").encode("ascii")
    exact = _certificate_selector_mutation(
        tmp_path,
        intent,
        expected_before={
            "kind": "file",
            "mode": 0o600,
            "size": len(prior_bytes),
            "sha256": hashlib.sha256(prior_bytes).hexdigest(),
        },
    )
    assert _journal_v2(
        certificate_progress="staged",
        certificate_intent=intent,
        pending_mutation=exact,
    ).pending_mutation is exact


@pytest.mark.parametrize("field", ["mode", "size", "sha256"])
def test_certificate_selector_journal_binds_each_prior_snapshot_field(
    tmp_path: Path,
    field: str,
) -> None:
    """Break caught: selector recovery omits one prior-file snapshot field."""

    payload = _certificate_intent().to_dict()
    prior_key_id = "sha256:" + "c" * 64
    payload["prior_key_id"] = prior_key_id
    intent = CertificateMutationIntent.from_dict(payload)
    prior_bytes = (prior_key_id + "\n").encode("ascii")
    before = {
        "kind": "file",
        "mode": 0o600,
        "size": len(prior_bytes),
        "sha256": hashlib.sha256(prior_bytes).hexdigest(),
    }
    before[field] = {
        "mode": 0o644,
        "size": len(prior_bytes) + 1,
        "sha256": hashlib.sha256(b"wrong\n").hexdigest(),
    }[field]

    with pytest.raises(StateRecordError, match="expected-before"):
        _journal_v2(
            certificate_progress="staged",
            certificate_intent=intent,
            pending_mutation=_certificate_selector_mutation(
                tmp_path,
                intent,
                expected_before=before,
            ),
        )


def test_transaction_journal_independently_binds_intent_transaction() -> None:
    """Break caught: journal and certificate intent name different transactions."""

    intent = _certificate_intent()
    with pytest.raises(StateRecordError, match="journal transaction"):
        TransactionJournal(
            transaction_id="9" * 32,
            phase="prepared",
            prior_release_id=None,
            candidate_release_id="release-new",
            resolver_bundle_id="resolver-001",
            certificate_key_id=intent.active_key_id,
            certificate_intent=intent,
            certificate_progress="planned",
            pending_mutation=None,
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


def test_transaction_journal_rejects_duplicate_json_fields(tmp_path: Path) -> None:
    path = tmp_path / "transaction-journal.json"
    encoded = json.dumps(_journal_v2().to_dict())
    encoded = encoded.replace('"version": 2', '"version": 2, "version": 2', 1)
    path.write_text(encoded, encoding="utf-8")

    with pytest.raises(StateRecordError, match="invalid transaction journal"):
        TransactionJournal.load(path, state_root=tmp_path)


def test_transaction_journal_closes_invalid_nested_certificate_intent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "transaction-journal.json"
    payload = _journal_v2().to_dict()
    payload["certificate_intent"]["action"] = "invented"  # type: ignore[index]
    path.write_text(json.dumps(payload), encoding="utf-8")

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
            transaction_id="1" * 32,
            phase="complete",
            prior_release_id=None,
            candidate_release_id="release-new",
            resolver_bundle_id="resolver-001",
            certificate_key_id="sha256:" + "a" * 64,
            certificate_intent=None,
            certificate_progress="committed",
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
            transaction_id="1" * 32,
            phase="prepared",
            prior_release_id=None,
            candidate_release_id="release-new",
            resolver_bundle_id="resolver-001",
            certificate_key_id="sha256:" + "a" * 64,
            certificate_intent=None,
            certificate_progress="committed",
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
        "version": 2,
        "transaction_id": "1" * 32,
        "phase": phase,
        "prior_release_id": None,
        "candidate_release_id": "release-new",
        "resolver_bundle_id": "resolver-001",
        "certificate_key_id": "sha256:" + "a" * 64,
        "certificate_intent": None,
        "certificate_progress": "committed",
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


def test_generic_pending_recovery_refuses_certificate_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: generic recovery trusts the selector's persisted pathname."""

    intent = _certificate_intent()
    journal = _journal_v2(
        certificate_progress="staged",
        pending_mutation=_certificate_selector_mutation(tmp_path, intent),
    )
    manifest = Manifest(tmp_path / "manifest.json", state_root=tmp_path)
    path_observed = False
    mutation_called = False

    def unexpected_snapshot(_path: Path) -> dict[str, object]:
        nonlocal path_observed
        path_observed = True
        pytest.fail("certificate selector path must not be observed generically")

    def unexpected_apply(_mutation: JournalMutation) -> None:
        nonlocal mutation_called
        mutation_called = True
        pytest.fail("certificate selector callback must not run generically")

    monkeypatch.setattr(
        sys.modules[recover_pending_mutation.__module__],
        "snapshot_path_state",
        unexpected_snapshot,
    )

    with pytest.raises(StateRecordError, match="certificate selector.*canonical"):
        recover_pending_mutation(
            journal,
            manifest=manifest,
            apply_mutation=unexpected_apply,
        )

    assert path_observed is False
    assert mutation_called is False


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
    transaction_id="1" * 32,
    phase="prepared",
    prior_release_id=None,
    candidate_release_id="release-new",
    resolver_bundle_id="resolver-001",
    certificate_key_id="sha256:" + "a" * 64,
    certificate_intent=None,
    certificate_progress="committed",
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
