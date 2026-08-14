"""Tests for the install manifest: recording at install time, replay at uninstall.

The manifest is the source of truth for uninstall. Key property: uninstall
removes exactly what install recorded — including symlinks pointing at a
*stale* root (e.g. an old plugin-cache version dir), which the heuristic
fallback cannot know about.
"""
from __future__ import annotations

import asyncio
import io
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import types
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
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

state_record = sys.modules[JournalMutation.__module__]


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


def _filesystem_mutation(
    target: Path,
    *,
    intended_after: dict[str, object],
    expected_before: dict[str, object] | None = None,
    ownership_entry: dict[str, object] | None = None,
    operation_key: str = "test.filesystem",
    kind: str = "file",
) -> JournalMutation:
    ownership_delta = (
        {"action": "none"}
        if ownership_entry is None
        else {"action": "upsert", "entry": ownership_entry}
    )
    fields = {
        "operation_key": operation_key,
        "kind": kind,
        "resource_kind": "filesystem",
        "resource_id": str(target),
        "intended_after": intended_after,
        "ownership_delta": ownership_delta,
    }
    return JournalMutation(
        mutation_id=state_record.mutation_id_for(
            transaction_id="1" * 32, **fields
        ),
        expected_before=expected_before or {"kind": "absent"},
        **fields,
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


def test_transaction_journal_v3_round_trip_preserves_certificate_intent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "transaction-journal.json"
    journal = _journal_v2()

    journal.save(path, state_root=path.parent)

    assert json.loads(path.read_bytes())["version"] == 3
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
        pending = _filesystem_mutation(
            target,
            kind=pending_kind,
            intended_after={"kind": "file", "mode": 0o600, "size": 0, "sha256": hashlib.sha256(b"").hexdigest()},
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
    mutation = _filesystem_mutation(
        target,
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
    encoded = encoded.replace('"version": 3', '"version": 3, "version": 3', 1)
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
        _filesystem_mutation(
            target,
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
    mutation = _filesystem_mutation(
        target,
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
    mutation = _filesystem_mutation(
        target,
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
            completed_mutation_ids=(mutation.mutation_id,),
        )


@pytest.mark.parametrize(
    ("phase", "pending_completed"),
    [("complete", False), ("prepared", True)],
)
def test_transaction_journal_load_rejects_impossible_state_machine_transition(
    tmp_path: Path, phase: str, pending_completed: bool
) -> None:
    target = tmp_path / "dispatcher"
    mutation = _filesystem_mutation(
        target,
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
        "version": 3,
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
            "operation_key": mutation.operation_key,
            "kind": mutation.kind,
            "resource_kind": mutation.resource_kind,
            "resource_id": mutation.resource_id,
            "expected_before": mutation.expected_before,
            "intended_after": mutation.intended_after,
            "ownership_delta": mutation.ownership_delta,
        },
        "completed_mutation_ids": [mutation.mutation_id] if pending_completed else [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateRecordError, match="invalid transaction journal"):
        TransactionJournal.load(path, state_root=tmp_path)


def _logical_mutation_fields(tmp_path: Path) -> dict[str, object]:
    target = tmp_path / "bin" / "dispatcher"
    intended = {
        "kind": "file",
        "mode": 0o755,
        "size": len(b"dispatcher\n"),
        "sha256": hashlib.sha256(b"dispatcher\n").hexdigest(),
    }
    return {
        "operation_key": "scaffold.dispatcher",
        "kind": "file",
        "resource_kind": "filesystem",
        "resource_id": str(target),
        "intended_after": intended,
        "ownership_delta": {
            "action": "upsert",
            "entry": {"kind": "file", "path": str(target)},
        },
    }


def test_mutation_id_is_canonical_deterministic_and_excludes_before_state(
    tmp_path: Path,
) -> None:
    fields = _logical_mutation_fields(tmp_path)

    first = state_record.mutation_id_for(transaction_id="1" * 32, **fields)
    second = state_record.mutation_id_for(
        transaction_id="1" * 32,
        **dict(reversed(list(fields.items()))),
    )

    assert first == second
    assert len(first) == 32
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert first == hashlib.sha256(
        json.dumps(
            {
                "domain": "famulus-install-mutation-v1",
                "intended_after": fields["intended_after"],
                "kind": fields["kind"],
                "operation_key": fields["operation_key"],
                "ownership_delta": fields["ownership_delta"],
                "resource_id": fields["resource_id"],
                "resource_kind": fields["resource_kind"],
                "transaction_id": "1" * 32,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:32]


@pytest.mark.parametrize("resource_kind", ["filesystem", "windows_registry", "git_config"])
def test_journal_v3_round_trip_closes_logical_resource_schema(
    tmp_path: Path,
    resource_kind: str,
) -> None:
    if resource_kind == "filesystem":
        resource_id = str(tmp_path / "dispatcher")
        state: dict[str, object] = {"kind": "absent"}
        assert Path(resource_id).is_absolute()
    elif resource_kind == "windows_registry":
        resource_id = state_record.windows_registry_resource_id(
            hive="HKEY_CURRENT_USER", key="Environment", name="AI"
        )
        state = {
            "kind": "windows_registry_value",
            "value_type": 1,
            "value": "C:/AI",
        }
    else:
        resource_id = state_record.git_config_resource_id(
            repo=tmp_path / "repo", key="core.hooksPath"
        )
        state = {"kind": "git_config_value", "value": ".githooks"}
        assert Path(json.loads(resource_id)["repo"]).is_absolute()
    delta = {"action": "none"}
    operation_key = "logical.write"
    mutation = state_record.JournalMutation(
        mutation_id=state_record.mutation_id_for(
            transaction_id="1" * 32,
            operation_key=operation_key,
            kind="logical_value",
            resource_kind=resource_kind,
            resource_id=resource_id,
            intended_after=state,
            ownership_delta=delta,
        ),
        operation_key=operation_key,
        kind="logical_value",
        resource_kind=resource_kind,
        resource_id=resource_id,
        expected_before={"kind": "absent"},
        intended_after=state,
        ownership_delta=delta,
    )
    path = tmp_path / "state" / "transaction-journal.json"
    journal = _journal(pending_mutation=mutation)

    journal.save(path, state_root=path.parent)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["version"] == 3
    assert set(payload["pending_mutation"]) == {
        "mutation_id",
        "operation_key",
        "kind",
        "resource_kind",
        "resource_id",
        "expected_before",
        "intended_after",
        "ownership_delta",
    }
    assert TransactionJournal.load(path, state_root=path.parent) == journal


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("mutation_id", "A" * 32, "mutation_id"),
        ("operation_key", "", "operation_key"),
        ("resource_kind", "path", "resource_kind"),
        ("resource_id", "", "resource_id"),
        ("ownership_delta", {"action": "invented"}, "ownership_delta"),
        (
            "intended_after",
            {"kind": "windows_registry_value", "value_type": 1, "value": "x"},
            "intended_after",
        ),
    ],
)
def test_journal_v3_rejects_noncanonical_or_cross_kind_fields(
    tmp_path: Path, field: str, value: object, error: str
) -> None:
    fields = _logical_mutation_fields(tmp_path)
    kwargs = {
        "mutation_id": state_record.mutation_id_for(
            transaction_id="1" * 32, **fields
        ),
        "expected_before": {"kind": "absent"},
        **fields,
    }
    kwargs[field] = value

    with pytest.raises(StateRecordError, match=error):
        state_record.JournalMutation(**kwargs)


@pytest.mark.parametrize(
    "delta",
    [
        {"action": "upsert"},
        {"action": "upsert", "entry": {"kind": "file"}},
        {"action": "forget"},
        {"action": "forget", "kind": "file", "path": ""},
        {"action": "none", "entry": {"kind": "file", "path": "/x"}},
    ],
)
def test_journal_v3_rejects_open_or_incomplete_ownership_delta(
    tmp_path: Path, delta: dict[str, object]
) -> None:
    fields = _logical_mutation_fields(tmp_path)
    fields["ownership_delta"] = delta

    with pytest.raises(StateRecordError, match="ownership_delta"):
        state_record.mutation_id_for(transaction_id="1" * 32, **fields)


def test_journal_v3_normalizes_valid_v2_certificate_selector_only(
    tmp_path: Path,
) -> None:
    intent = _certificate_intent()
    selector = _certificate_selector_mutation(tmp_path, intent)
    path = tmp_path / "transaction-journal.json"
    payload = _journal_v2(
        certificate_progress="staged", pending_mutation=selector
    ).to_dict()
    payload["version"] = 2
    payload["pending_mutation"] = {
        "mutation_id": selector.mutation_id,
        "kind": selector.kind,
        "path": selector.path,
        "expected_before": selector.expected_before,
        "intended_after": selector.intended_after,
        "ownership_entry": selector.ownership_entry,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = TransactionJournal.load(path, state_root=tmp_path)

    assert loaded.pending_mutation.resource_kind == "filesystem"
    assert loaded.pending_mutation.resource_id == selector.path
    assert loaded.pending_mutation.operation_key == "certificate.selector"
    assert loaded.pending_mutation.ownership_delta == {"action": "none"}
    assert loaded.to_dict()["version"] == 3


def test_journal_v3_normalizes_valid_v2_completed_certificate_selector(
    tmp_path: Path,
) -> None:
    intent = _certificate_intent()
    selector = _certificate_selector_mutation(tmp_path, intent)
    path = tmp_path / "transaction-journal.json"
    payload = _journal_v2(
        certificate_progress="committed", pending_mutation=None
    ).to_dict()
    payload["version"] = 2
    payload["completed_mutation_ids"] = [selector.mutation_id]
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = TransactionJournal.load(path, state_root=tmp_path)

    assert loaded.pending_mutation is None
    assert loaded.completed_mutation_ids == (selector.mutation_id,)
    assert loaded.to_dict()["version"] == 3


@pytest.mark.parametrize("route", ["construct", "load"])
@pytest.mark.parametrize(
    "field",
    [
        "transaction_id",
        "operation_key",
        "kind",
        "resource_kind",
        "resource_id",
        "intended_after",
        "ownership_delta",
    ],
)
def test_fixed_mutation_id_rejects_each_independently_changed_bound_field(
    tmp_path: Path, route: str, field: str
) -> None:
    fields = _logical_mutation_fields(tmp_path)
    fixed_id = state_record.mutation_id_for(transaction_id="1" * 32, **fields)
    mutation_payload = {
        "mutation_id": fixed_id,
        "expected_before": {"kind": "absent"},
        **fields,
    }
    transaction_id = "1" * 32
    if field == "transaction_id":
        transaction_id = "2" * 32
    elif field == "operation_key":
        mutation_payload[field] = "scaffold.other"
    elif field == "kind":
        mutation_payload[field] = "other_file"
    elif field == "resource_id":
        mutation_payload[field] = str(tmp_path / "bin" / "other")
    elif field == "intended_after":
        mutation_payload[field] = {"kind": "absent"}
    elif field == "ownership_delta":
        mutation_payload[field] = {"action": "none"}
    else:
        mutation_payload.update(
            resource_kind="git_config",
            resource_id=state_record.git_config_resource_id(
                repo=tmp_path, key="core.hooksPath"
            ),
            intended_after={"kind": "git_config_value", "value": ".githooks"},
        )

    if route == "construct":
        mutation = state_record.JournalMutation.from_dict(mutation_payload)
        with pytest.raises(StateRecordError, match="canonical request"):
            replace(
                _journal(pending_mutation=None),
                transaction_id=transaction_id,
                pending_mutation=mutation,
            )
    else:
        payload = _journal(pending_mutation=None).to_dict()
        payload["transaction_id"] = transaction_id
        payload["pending_mutation"] = mutation_payload
        path = tmp_path / f"{field}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(StateRecordError, match="canonical request"):
            TransactionJournal.load(path, state_root=tmp_path)


def test_journal_v3_rejects_v2_generic_pending_mutation(tmp_path: Path) -> None:
    path = tmp_path / "transaction-journal.json"
    payload = _journal(pending_mutation=None).to_dict()
    payload["version"] = 2
    payload["pending_mutation"] = {
        "mutation_id": "4" * 32,
        "kind": "file",
        "path": str(tmp_path / "dispatcher"),
        "expected_before": {"kind": "absent"},
        "intended_after": {"kind": "absent"},
        "ownership_entry": None,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateRecordError, match="version 2.*certificate"):
        TransactionJournal.load(path, state_root=tmp_path)


def test_journal_v3_rejects_v2_generic_completed_mutation_id(tmp_path: Path) -> None:
    path = tmp_path / "transaction-journal.json"
    payload = _journal_v2().to_dict()
    payload["version"] = 2
    payload["completed_mutation_ids"] = ["4" * 32]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateRecordError, match="version 2.*certificate"):
        TransactionJournal.load(path, state_root=tmp_path)


def test_journal_version_rejects_float_equal_to_supported_integer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "transaction-journal.json"
    payload = _journal_v2().to_dict()
    payload["version"] = 3.0
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateRecordError, match="unsupported transaction journal version"):
        TransactionJournal.load(path, state_root=tmp_path)


def test_logical_resource_identity_factories_do_not_encode_fake_paths(
    tmp_path: Path,
) -> None:
    registry_id = state_record.windows_registry_resource_id(
        hive="HKEY_CURRENT_USER", key="Environment", name="AI"
    )
    git_id = state_record.git_config_resource_id(
        repo=tmp_path / "repo", key="core.hooksPath"
    )

    assert json.loads(registry_id) == {
        "hive": "HKEY_CURRENT_USER",
        "key": "environment",
        "name": "ai",
    }
    assert json.loads(git_id) == {
        "key": "core.hookspath",
        "repo": str((tmp_path / "repo").absolute()),
        "scope": "local",
    }
    assert not Path(registry_id).is_absolute()
    assert not Path(git_id).is_absolute()


def test_logical_resource_identity_preserves_registry_slash_and_git_subsections(
    tmp_path: Path,
) -> None:
    registry_slash = state_record.windows_registry_resource_id(
        hive="hkcu", key="Environment/Variables", name="AI_HOME"
    )
    registry_backslash = state_record.windows_registry_resource_id(
        hive="HKEY_CURRENT_USER", key="Environment\\Variables", name="AI_HOME"
    )
    registry_case_alias = state_record.windows_registry_resource_id(
        hive="hKeY_cUrReNt_UsEr", key="environment/variables", name="ai_home"
    )
    assert json.loads(registry_slash) == {
        "hive": "HKEY_CURRENT_USER",
        "key": "environment/variables",
        "name": "ai_home",
    }
    assert registry_slash == registry_case_alias
    assert registry_slash != registry_backslash

    aliased_repo = tmp_path / "parent" / ".." / "repo"
    canonical = state_record.git_config_resource_id(
        repo=aliased_repo, key="Remote.Origin.Fetch"
    )
    distinct = state_record.git_config_resource_id(
        repo=tmp_path / "repo", key="remote.origin.fetch"
    )
    assert json.loads(canonical) == {
        "key": "remote.Origin.fetch",
        "repo": str(tmp_path / "repo"),
        "scope": "local",
    }
    assert canonical != distinct


@pytest.mark.parametrize("field", ["hive", "key", "name"])
@pytest.mark.parametrize("unsupported", ["Straße", "line\nbreak", "delete\x7f"])
def test_registry_identity_rejects_non_printable_ascii_before_id_creation(
    monkeypatch: pytest.MonkeyPatch, field: str, unsupported: str
) -> None:
    ascii_id = state_record.windows_registry_resource_id(
        hive="HKCU", key="Strasse", name="AI"
    )
    monkeypatch.setattr(
        state_record,
        "_canonical_json_text",
        lambda _payload: pytest.fail("invalid registry identity reached ID creation"),
    )

    with pytest.raises(StateRecordError, match="printable ASCII"):
        identity = {"hive": "HKCU", "key": "Environment", "name": "AI"}
        identity[field] = unsupported
        state_record.windows_registry_resource_id(**identity)
    assert "strasse" in ascii_id


@pytest.mark.parametrize("alias_kind", ["parent", "current", "duplicate-separator"])
def test_filesystem_resource_identity_rejects_native_lexical_aliases(
    tmp_path: Path, alias_kind: str
) -> None:
    if alias_kind == "parent":
        alias = str(tmp_path / "a" / ".." / "b")
    elif alias_kind == "current":
        alias = str(tmp_path) + os.sep + "." + os.sep + "b"
    else:
        alias = str(tmp_path) + os.sep + os.sep + "b"
    assert Path(alias).is_absolute()

    with pytest.raises(StateRecordError, match="canonical"):
        state_record.mutation_id_for(
            transaction_id="1" * 32,
            operation_key="file.write",
            kind="file",
            resource_kind="filesystem",
            resource_id=alias,
            intended_after={"kind": "absent"},
            ownership_delta={"action": "none"},
        )


def test_windows_registry_observer_returns_closed_absent_and_value_states() -> None:
    values: dict[str, tuple[object, int]] = {"AI": ("C:/AI", 1)}

    class Key:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_READ=1,
        OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda _key, name: (
            values[name]
            if name in values
            else (_ for _ in ()).throw(FileNotFoundError(name))
        ),
    )

    assert state_record.snapshot_windows_registry_value(
        hive=fake_winreg.HKEY_CURRENT_USER,
        key="Environment",
        name="AI",
        winreg_module=fake_winreg,
    ) == {"kind": "windows_registry_value", "value_type": 1, "value": "C:/AI"}
    assert state_record.snapshot_windows_registry_value(
        hive=fake_winreg.HKEY_CURRENT_USER,
        key="Environment",
        name="MISSING",
        winreg_module=fake_winreg,
    ) == {"kind": "absent"}

    values["BINARY"] = ("not-a-string-registry-type", 3)
    with pytest.raises(StateRecordError, match="value_type"):
        state_record.snapshot_windows_registry_value(
            hive=fake_winreg.HKEY_CURRENT_USER,
            key="Environment",
            name="BINARY",
            winreg_module=fake_winreg,
        )

    values["FLOAT-TYPE"] = ("value", 1.0)
    with pytest.raises(StateRecordError, match="value_type"):
        state_record.snapshot_windows_registry_value(
            hive=fake_winreg.HKEY_CURRENT_USER,
            key="Environment",
            name="FLOAT-TYPE",
            winreg_module=fake_winreg,
        )

    values["EMPTY"] = ("", 1)
    assert state_record.snapshot_windows_registry_value(
        hive=fake_winreg.HKEY_CURRENT_USER,
        key="Environment",
        name="EMPTY",
        winreg_module=fake_winreg,
    ) == {"kind": "windows_registry_value", "value_type": 1, "value": ""}


def test_git_config_observer_closes_absent_single_and_multiple_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = iter(
        [
            (1, b"", b""),
            (0, b".githooks\0", b""),
            (0, b"one\0two\0", b""),
            (0, b"\0", b""),
        ]
    )
    monkeypatch.setattr(
        state_record, "_bounded_process_capture", lambda *_a, **_kw: next(results)
    )

    assert state_record.snapshot_git_config_value(
        repo=tmp_path, key="core.hooksPath"
    ) == {"kind": "absent"}
    assert state_record.snapshot_git_config_value(
        repo=tmp_path, key="core.hooksPath"
    ) == {"kind": "git_config_value", "value": ".githooks"}
    with pytest.raises(StateRecordError, match="multiple"):
        state_record.snapshot_git_config_value(
            repo=tmp_path, key="core.hooksPath"
        )
    assert state_record.snapshot_git_config_value(
        repo=tmp_path, key="core.hooksPath"
    ) == {"kind": "git_config_value", "value": ""}


def test_git_config_observer_does_not_misclassify_an_error_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        state_record,
        "_bounded_process_capture",
        lambda *_a, **_kw: (1, b"", b"permission denied"),
    )

    with pytest.raises(StateRecordError, match="observation failed"):
        state_record.snapshot_git_config_value(
            repo=tmp_path, key="core.hooksPath"
        )


def test_git_config_observer_constructs_exact_shell_free_capture_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        state_record.os,
        "environ",
        {
            "PATH": "/controlled/bin",
            "FAMULUS_TEST_SENTINEL": "retained",
            "GIT_DIR": str(tmp_path / "attacker.git"),
            "GIT_WORK_TREE": str(tmp_path / "attacker-tree"),
            "GIT_COMMON_DIR": str(tmp_path / "attacker-common"),
            "GIT_CONFIG": str(tmp_path / "attacker-config"),
            "GIT_CONFIG_GLOBAL": str(tmp_path / "attacker-global"),
            "GIT_CONFIG_SYSTEM": str(tmp_path / "attacker-system"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "attacker-hooks",
            "GIT_INDEX_FILE": str(tmp_path / "attacker-index"),
            "GIT_OBJECT_DIRECTORY": str(tmp_path / "attacker-objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(tmp_path / "other-objects"),
        },
    )
    observed: tuple[list[str], dict[str, object]] | None = None

    def capture_run(command: list[str], **kwargs: object) -> tuple[int, bytes, bytes]:
        nonlocal observed
        observed = (command, kwargs)
        return 0, b".githooks\0", b""

    monkeypatch.setattr(state_record, "_bounded_process_capture", capture_run)

    assert state_record.snapshot_git_config_value(
        repo=tmp_path, key="core.Team.hooksPath", timeout_seconds=1.25
    ) == {"kind": "git_config_value", "value": ".githooks"}
    assert observed == (
        [
            "git",
            "-C",
            str(tmp_path),
            "config",
            "--local",
            "--null",
            "--get-all",
            "core.Team.hookspath",
        ],
        {
            "environment": {
                "PATH": "/controlled/bin",
                "FAMULUS_TEST_SENTINEL": "retained",
            },
            "timeout_seconds": 1.25,
            "stdout_limit": 65537,
            "stderr_limit": 65536,
        },
    )


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -float("inf")])
def test_git_config_observer_rejects_nonfinite_timeout(
    tmp_path: Path, timeout: float
) -> None:
    with pytest.raises(StateRecordError, match="timeout"):
        state_record.snapshot_git_config_value(
            repo=tmp_path, key="core.hooksPath", timeout_seconds=timeout
        )


def test_git_capture_rejects_an_active_event_loop_before_spawning(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "child-spawned"

    async def invoke_sync_boundary() -> None:
        with pytest.raises(StateRecordError, match="active event loop"):
            state_record._bounded_process_capture(
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
                    str(marker),
                ],
                environment=os.environ,
                timeout_seconds=1.0,
                stdout_limit=16,
                stderr_limit=16,
            )

    asyncio.run(invoke_sync_boundary())
    assert not marker.exists()


def test_async_capture_kills_reaps_and_closes_after_terminate_does_not_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained: dict[str, object] = {}
    events: list[str] = []

    async def exercise() -> None:
        loop = asyncio.get_running_loop()

        class FakePipeTransport:
            def __init__(self, protocol: object, descriptor: int) -> None:
                self.protocol = protocol
                self.descriptor = descriptor
                self.closed = False
                self.paused = False

            def pause_reading(self) -> None:
                self.paused = True

            def close(self) -> None:
                if self.closed:
                    return
                self.closed = True
                events.append(f"pipe-{self.descriptor}-close")
                self.protocol.pipe_connection_lost(self.descriptor, None)

        class FakeSubprocessTransport:
            def __init__(self, protocol: object) -> None:
                self.protocol = protocol
                self.returncode: int | None = None
                self.closed = False
                self.pipes = {
                    descriptor: FakePipeTransport(protocol, descriptor)
                    for descriptor in (1, 2)
                }

            def get_returncode(self) -> int | None:
                return self.returncode

            def get_pipe_transport(self, descriptor: int) -> FakePipeTransport:
                return self.pipes[descriptor]

            def terminate(self) -> None:
                events.append("terminate")

            def kill(self) -> None:
                events.append("kill")
                loop.call_soon(self._reap_after_kill)

            def _reap_after_kill(self) -> None:
                events.append("reap")
                self.returncode = -9
                self.protocol.process_exited()

            def close(self) -> None:
                events.append("transport-close")
                self.closed = True
                for pipe in self.pipes.values():
                    pipe.close()
                self.protocol.connection_lost(None)

        async def fake_subprocess_exec(
            protocol_factory: object, *command: str, **kwargs: object
        ) -> tuple[object, object]:
            assert command == (sys.executable, "-c", "pass")
            assert kwargs == {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "env": {},
                "shell": False,
                "bufsize": 0,
            }
            protocol = protocol_factory()
            transport = FakeSubprocessTransport(protocol)
            retained.update(protocol=protocol, transport=transport)
            protocol.connection_made(transport)
            protocol.pipe_data_received(1, b"abcd")
            protocol.pipe_data_received(2, b"ef")
            return transport, protocol

        monkeypatch.setattr(loop, "subprocess_exec", fake_subprocess_exec)
        with pytest.raises(StateRecordError, match="closed bound"):
            await state_record._bounded_process_capture_async(
                [sys.executable, "-c", "pass"],
                environment={},
                timeout_seconds=1.0,
                stdout_limit=3,
                stderr_limit=2,
            )
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(exercise())

    protocol = retained["protocol"]
    transport = retained["transport"]
    assert protocol.standard_output == b"abc"
    assert protocol.standard_error == b"ef"
    assert events == [
        "terminate",
        "kill",
        "reap",
        "pipe-1-close",
        "pipe-2-close",
        "transport-close",
    ]
    assert transport.returncode == -9
    assert transport.closed is True
    assert transport.pipes[1].paused is True
    assert transport.pipes[2].paused is False
    assert all(pipe.closed for pipe in transport.pipes.values())


def test_async_capture_uses_one_absolute_deadline_across_every_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_deadlines: list[float] = []
    phase_deadlines: list[tuple[str, float]] = []
    retained: dict[str, object] = {}

    async def exercise() -> None:
        real_loop = asyncio.get_running_loop()

        class FakePipeTransport:
            def __init__(self, protocol: object, descriptor: int) -> None:
                self.protocol = protocol
                self.descriptor = descriptor
                self.closed = False

            def close(self) -> None:
                self.closed = True
                self.protocol.pipe_connection_lost(self.descriptor, None)

        class FakeSubprocessTransport:
            def __init__(self, protocol: object) -> None:
                self.protocol = protocol
                self.returncode: int | None = None
                self.closed = False
                self.pipes = {
                    descriptor: FakePipeTransport(protocol, descriptor)
                    for descriptor in (1, 2)
                }

            def get_returncode(self) -> int | None:
                return self.returncode

            def get_pipe_transport(self, descriptor: int) -> FakePipeTransport:
                return self.pipes[descriptor]

            def terminate(self) -> None:
                pytest.fail("the direct child is already reaped before cleanup")

            def kill(self) -> None:
                pytest.fail("the direct child is already reaped before cleanup")

            def close(self) -> None:
                self.closed = True

        class FakeProtocol:
            def __init__(self, *, stdout_limit: int, stderr_limit: int) -> None:
                assert (stdout_limit, stderr_limit) == (8, 8)
                self.transport: FakeSubprocessTransport | None = None
                self.overflow = False
                self.failed = False
                self._closed_pipes: set[int] = set()
                self._connection_closed = False

            @property
            def standard_output(self) -> bytes:
                return b""

            @property
            def standard_error(self) -> bytes:
                return b""

            @property
            def pipes_closed(self) -> bool:
                return self._closed_pipes == {1, 2}

            @property
            def connection_closed(self) -> bool:
                return self._connection_closed

            def connection_made(self, transport: object) -> None:
                self.transport = transport

            def pipe_connection_lost(
                self, descriptor: int, exception: Exception | None
            ) -> None:
                assert exception is None
                self._closed_pipes.add(descriptor)

            def process_exited(self) -> None:
                pass

            def connection_lost(self, exception: Exception | None) -> None:
                assert exception is None
                self._connection_closed = True

            async def wait_for_change(self, *, deadline: float) -> bool:
                assert self.transport is not None
                if self.transport.returncode is None:
                    phase_deadlines.append(("work", deadline))
                    fake_loop.now = 100.4
                    self.transport.returncode = 0
                    self.process_exited()
                    return True
                if not self.pipes_closed:
                    phase_deadlines.append(("drain", deadline))
                    fake_loop.now = 100.6
                    return False
                phase_deadlines.append(("cleanup", deadline))
                self.connection_lost(None)
                return True

        class FakeLoop:
            def __init__(self) -> None:
                self.now = 100.0

            def time(self) -> float:
                return self.now

            async def subprocess_exec(
                self, protocol_factory: object, *_command: str, **_kwargs: object
            ) -> tuple[object, object]:
                protocol = protocol_factory()
                transport = FakeSubprocessTransport(protocol)
                retained.update(protocol=protocol, transport=transport)
                protocol.connection_made(transport)
                self.now = 100.2
                return transport, protocol

        class RecordingTimeout:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_args: object) -> None:
                return None

        fake_loop = FakeLoop()

        def recording_timeout(delay: float) -> RecordingTimeout:
            setup_deadlines.append(fake_loop.time() + delay)
            return RecordingTimeout()

        monkeypatch.setattr(state_record.asyncio, "get_running_loop", lambda: fake_loop)
        monkeypatch.setattr(state_record.asyncio, "timeout", recording_timeout)
        monkeypatch.setattr(state_record, "_BoundedCaptureProtocol", FakeProtocol)
        with pytest.raises(
            StateRecordError, match="^Git config observation failed$"
        ):
            await state_record._bounded_process_capture_async(
                [sys.executable, "-c", "pass"],
                environment={},
                timeout_seconds=1.0,
                stdout_limit=8,
                stderr_limit=8,
            )
        assert real_loop.is_running()

    asyncio.run(exercise())

    protocol = retained["protocol"]
    transport = retained["transport"]
    assert setup_deadlines == [pytest.approx(100.75)]
    assert phase_deadlines == [
        ("work", pytest.approx(100.21)),
        ("drain", pytest.approx(100.5)),
        ("cleanup", pytest.approx(101.0)),
    ]
    assert transport.returncode == 0
    assert transport.closed is True
    assert protocol.pipes_closed is True
    assert protocol.connection_closed is True


def test_async_capture_rejects_unsupported_platform_loop_with_static_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "sensitive platform subprocess diagnostic"

    async def exercise() -> None:
        loop = asyncio.get_running_loop()

        async def unsupported_subprocess_exec(
            *_args: object, **_kwargs: object
        ) -> tuple[object, object]:
            raise NotImplementedError(raw)

        monkeypatch.setattr(loop, "subprocess_exec", unsupported_subprocess_exec)
        with pytest.raises(
            StateRecordError, match="^Git config observation failed$"
        ) as caught:
            await state_record._bounded_process_capture_async(
                ["git", "config"],
                environment={},
                timeout_seconds=1.0,
                stdout_limit=3,
                stderr_limit=2,
            )
        assert raw not in str(caught.value)

    asyncio.run(exercise())


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_git_capture_terminates_real_child_on_capture_overflow(channel: str) -> None:
    stream = "stdout" if channel == "stdout" else "stderr"
    script = (
        "import sys,time; "
        f"sys.{stream}.buffer.write(b'x' * 70000); sys.{stream}.buffer.flush(); "
        "time.sleep(10)"
    )
    started = time.monotonic()
    with pytest.raises(StateRecordError, match="closed bound"):
        state_record._bounded_process_capture(
            [sys.executable, "-c", script],
            environment=os.environ,
            timeout_seconds=5.0,
            stdout_limit=65536,
            stderr_limit=65536,
        )

    assert time.monotonic() - started < 3.0


def test_git_capture_accepts_simultaneous_exact_real_child_output_caps() -> None:
    script = (
        "import sys; "
        "sys.stdout.buffer.write(b'x' * 65536 + b'\\0'); "
        "sys.stderr.buffer.write(b'y' * 65536)"
    )

    returncode, standard_output, standard_error = state_record._bounded_process_capture(
        [sys.executable, "-c", script],
        environment=os.environ,
        timeout_seconds=2.0,
        stdout_limit=65537,
        stderr_limit=65536,
    )

    assert returncode == 0
    assert standard_output == b"x" * 65536 + b"\0"
    assert standard_error == b"y" * 65536


def test_git_capture_reaps_real_child_on_timeout() -> None:
    with pytest.raises(
        StateRecordError, match="^Git config observation failed$"
    ):
        state_record._bounded_process_capture(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            environment=os.environ,
            timeout_seconds=0.1,
            stdout_limit=16,
            stderr_limit=16,
        )


def test_git_capture_refuses_descendant_held_pipes_without_reader_leaks(
    tmp_path: Path,
) -> None:
    stop_path = tmp_path / "stop-descendant"
    done_path = tmp_path / "descendant-done"
    pid_path = tmp_path / "descendant-pid"
    descendant = (
        "import pathlib,sys,time; "
        "stop=pathlib.Path(sys.argv[1]); done=pathlib.Path(sys.argv[2]); "
        "\nwhile not stop.exists(): time.sleep(0.01)\n"
        "done.write_text('done', encoding='utf-8')"
    )
    wrapper = (
        "import pathlib,subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]],"
        " stdout=sys.stdout,stderr=sys.stderr); "
        "pathlib.Path(sys.argv[4]).write_text(str(child.pid),encoding='ascii')"
    )
    started = time.monotonic()
    try:
        with pytest.raises(
            StateRecordError, match="^Git config observation failed$"
        ):
            state_record._bounded_process_capture(
                [
                    sys.executable,
                    "-c",
                    wrapper,
                    descendant,
                    str(stop_path),
                    str(done_path),
                    str(pid_path),
                ],
                environment=os.environ,
                timeout_seconds=5.0,
                stdout_limit=16,
                stderr_limit=16,
            )
        assert time.monotonic() - started < 3.0
    finally:
        stop_path.write_text("stop", encoding="ascii")
        cleanup_deadline = time.monotonic() + 2.0
        while not done_path.exists() and time.monotonic() < cleanup_deadline:
            time.sleep(0.01)
        descendant_pid = pid_path.read_text() if pid_path.exists() else "unknown"
        assert done_path.exists(), f"descendant {descendant_pid} did not exit"


def test_filesystem_observer_rejects_oversized_file_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large"
    path.write_bytes(b"x" * (state_record._MAX_FILESYSTEM_FILE_BYTES + 1))

    monkeypatch.setattr(
        state_record.os,
        "read",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("oversized file must be rejected before reading")
        ),
    )
    with pytest.raises(StateRecordError, match="closed bound"):
        state_record.snapshot_path_state(path)


def test_filesystem_observer_enforces_bound_while_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "growing"
    path.write_bytes(b"x" * (state_record._MAX_FILESYSTEM_FILE_BYTES + 1))
    real_metadata = path.stat()
    monkeypatch.setattr(
        state_record.os,
        "fstat",
        lambda _descriptor: types.SimpleNamespace(
            st_mode=real_metadata.st_mode,
            st_size=state_record._MAX_FILESYSTEM_FILE_BYTES,
        ),
    )

    with pytest.raises(StateRecordError, match="closed bound"):
        state_record.snapshot_path_state(path)


def test_filesystem_mutation_id_rejects_state_above_observer_bound(
    tmp_path: Path,
) -> None:
    with pytest.raises(StateRecordError, match="closed bound"):
        state_record.mutation_id_for(
            transaction_id="1" * 32,
            operation_key="file.write",
            kind="file",
            resource_kind="filesystem",
            resource_id=str(tmp_path / "file"),
            intended_after={
                "kind": "file",
                "mode": 0o600,
                "size": state_record._MAX_FILESYSTEM_FILE_BYTES + 1,
                "sha256": "0" * 64,
            },
            ownership_delta={"action": "none"},
        )


def test_recorder_rejects_oversized_filesystem_state_before_pending_or_apply(
    tmp_path: Path,
) -> None:
    recorder = _recorder(tmp_path)
    applied = False

    def apply() -> None:
        nonlocal applied
        applied = True

    with pytest.raises(StateRecordError, match="closed bound"):
        recorder.mutate(
            operation_key="file.write",
            kind="file",
            resource_kind="filesystem",
            resource_id=str(tmp_path / "file"),
            intended_after={
                "kind": "file",
                "mode": 0o600,
                "size": state_record._MAX_FILESYSTEM_FILE_BYTES + 1,
                "sha256": "0" * 64,
            },
            ownership_delta={"action": "none"},
            observe=lambda: {"kind": "absent"},
            apply=apply,
        )

    durable = TransactionJournal.load(
        tmp_path / "state" / "transaction-journal.json",
        state_root=tmp_path / "state",
    )
    assert durable.pending_mutation is None
    assert applied is False


def test_windows_registry_observer_wraps_operating_system_errors() -> None:
    raw = "sensitive registry diagnostic"
    fake_winreg = types.SimpleNamespace(
        KEY_READ=1,
        OpenKey=lambda *_args: (_ for _ in ()).throw(PermissionError(raw)),
    )

    with pytest.raises(
        StateRecordError, match="^Windows registry observation failed$"
    ) as caught:
        state_record.snapshot_windows_registry_value(
            hive=object(), key="Environment", name="AI", winreg_module=fake_winreg
        )
    assert raw not in str(caught.value)


def _recorder(
    tmp_path: Path,
    *,
    journal: TransactionJournal | None = None,
) -> object:
    state_root = tmp_path / "state"
    journal_path = state_root / "transaction-journal.json"
    selected = journal or _journal(pending_mutation=None)
    if not journal_path.exists():
        selected.save(journal_path, state_root=state_root)
    return state_record.MutationRecorder(
        journal=selected,
        journal_path=journal_path,
        state_root=state_root,
        manifest=Manifest(state_root / "install-manifest.json", state_root=state_root),
    )


def _mutate_dispatcher(recorder: object, target: Path, apply: object) -> str:
    intended = {
        "kind": "file",
        "mode": 0o755,
        "size": len(b"dispatcher\n"),
        "sha256": hashlib.sha256(b"dispatcher\n").hexdigest(),
    }
    return recorder.mutate(
        operation_key="scaffold.dispatcher",
        kind="file",
        resource_kind="filesystem",
        resource_id=str(target),
        intended_after=intended,
        ownership_delta={
            "action": "upsert",
            "entry": {"kind": "file", "path": str(target)},
        },
        observe=lambda: snapshot_path_state(target),
        apply=apply,
    )


def test_mutation_recorder_orders_pending_effect_manifest_then_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "bin" / "dispatcher"
    target.parent.mkdir()
    recorder = _recorder(tmp_path)
    journal_path = tmp_path / "state" / "transaction-journal.json"
    manifest_path_value = tmp_path / "state" / "install-manifest.json"
    events: list[str] = []
    real_journal_save = TransactionJournal.save
    real_manifest_save = Manifest.save

    def journal_save(journal: TransactionJournal, *args: object, **kwargs: object) -> None:
        events.append("journal-pending" if journal.pending_mutation else "journal-complete")
        real_journal_save(journal, *args, **kwargs)

    def manifest_save(manifest: Manifest) -> None:
        assert TransactionJournal.load(
            journal_path, state_root=journal_path.parent
        ).pending_mutation is not None
        events.append("manifest")
        real_manifest_save(manifest)

    monkeypatch.setattr(TransactionJournal, "save", journal_save)
    monkeypatch.setattr(Manifest, "save", manifest_save)

    def apply() -> None:
        assert TransactionJournal.load(
            journal_path, state_root=journal_path.parent
        ).pending_mutation is not None
        events.append("effect")
        target.write_bytes(b"dispatcher\n")
        target.chmod(0o755)

    mutation_id = _mutate_dispatcher(recorder, target, apply)

    assert events == ["journal-pending", "effect", "manifest", "journal-complete"]
    assert recorder.journal.pending_mutation is None
    assert recorder.journal.completed_mutation_ids == (mutation_id,)
    assert json.loads(manifest_path_value.read_text(encoding="utf-8"))["entries"] == [
        {"kind": "file", "path": str(target)}
    ]


@pytest.mark.parametrize("boundary", ["before_effect", "during_effect", "after_effect"])
def test_mutation_recorder_leaves_durable_pending_across_effect_crashes(
    tmp_path: Path, boundary: str
) -> None:
    target = tmp_path / "bin" / "dispatcher"
    target.parent.mkdir()
    recorder = _recorder(tmp_path)

    def apply() -> None:
        if boundary == "before_effect":
            raise RuntimeError("crash")
        target.write_bytes(b"partial" if boundary == "during_effect" else b"dispatcher\n")
        target.chmod(0o755)
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError, match="crash"):
        _mutate_dispatcher(recorder, target, apply)

    durable = TransactionJournal.load(
        tmp_path / "state" / "transaction-journal.json",
        state_root=tmp_path / "state",
    )
    assert durable.pending_mutation is not None
    assert durable.completed_mutation_ids == ()
    assert not (tmp_path / "state" / "install-manifest.json").exists()


def test_mutation_recorder_recovers_same_request_and_rejects_changed_request(
    tmp_path: Path,
) -> None:
    target = tmp_path / "bin" / "dispatcher"
    target.parent.mkdir()
    first = _recorder(tmp_path)

    def crash_after_effect() -> None:
        target.write_bytes(b"dispatcher\n")
        target.chmod(0o755)
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        _mutate_dispatcher(first, target, crash_after_effect)
    durable = TransactionJournal.load(
        tmp_path / "state" / "transaction-journal.json",
        state_root=tmp_path / "state",
    )
    resumed = _recorder(tmp_path, journal=durable)
    with pytest.raises(StateRecordError, match="differs from.*pending mutation"):
        resumed.mutate(
            operation_key="scaffold.other",
            kind="file",
            resource_kind="filesystem",
            resource_id=str(target),
            intended_after=snapshot_path_state(target),
            ownership_delta={"action": "none"},
            observe=lambda: snapshot_path_state(target),
            apply=lambda: pytest.fail("changed request must not apply"),
        )

    applied = False

    def unexpected_apply() -> None:
        nonlocal applied
        applied = True

    mutation_id = _mutate_dispatcher(resumed, target, unexpected_apply)

    assert applied is False
    assert resumed.journal.completed_mutation_ids == (mutation_id,)


def test_mutation_recorder_identical_request_resumes_from_expected_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "bin" / "dispatcher"
    target.parent.mkdir()
    first = _recorder(tmp_path)

    with pytest.raises(RuntimeError, match="before effect"):
        _mutate_dispatcher(
            first,
            target,
            lambda: (_ for _ in ()).throw(RuntimeError("before effect")),
        )
    durable = TransactionJournal.load(
        tmp_path / "state" / "transaction-journal.json",
        state_root=tmp_path / "state",
    )
    assert snapshot_path_state(target) == durable.pending_mutation.expected_before

    resumed = _recorder(tmp_path, journal=durable)
    applied = False

    def apply() -> None:
        nonlocal applied
        applied = True
        target.write_bytes(b"dispatcher\n")
        target.chmod(0o755)

    mutation_id = _mutate_dispatcher(resumed, target, apply)

    assert applied is True
    assert resumed.journal.pending_mutation is None
    assert resumed.journal.completed_mutation_ids == (mutation_id,)


def test_mutation_recorder_refuses_third_state_and_verifies_completed_id(
    tmp_path: Path,
) -> None:
    target = tmp_path / "bin" / "dispatcher"
    target.parent.mkdir()
    recorder = _recorder(tmp_path)
    mutation_id = _mutate_dispatcher(
        recorder,
        target,
        lambda: (target.write_bytes(b"dispatcher\n"), target.chmod(0o755)),
    )
    target.write_bytes(b"user bytes\n")

    with pytest.raises(StateRecordError, match="completed mutation.*intended state"):
        _mutate_dispatcher(recorder, target, lambda: pytest.fail("must not replay"))

    pending = state_record.JournalMutation(
        mutation_id=mutation_id,
        operation_key="scaffold.dispatcher",
        kind="file",
        resource_kind="filesystem",
        resource_id=str(target),
        expected_before={"kind": "absent"},
        intended_after={
            "kind": "file",
            "mode": 0o755,
            "size": len(b"dispatcher\n"),
            "sha256": hashlib.sha256(b"dispatcher\n").hexdigest(),
        },
        ownership_delta={
            "action": "upsert",
            "entry": {"kind": "file", "path": str(target)},
        },
    )
    third = _journal(pending_mutation=pending)
    third.save(
        tmp_path / "state" / "third-journal.json", state_root=tmp_path / "state"
    )
    third_recorder = state_record.MutationRecorder(
        journal=third,
        journal_path=tmp_path / "state" / "third-journal.json",
        state_root=tmp_path / "state",
        manifest=Manifest(
            tmp_path / "state" / "third-manifest.json",
            state_root=tmp_path / "state",
        ),
    )
    with pytest.raises(StateRecordError, match="third state"):
        _mutate_dispatcher(
            third_recorder, target, lambda: pytest.fail("third state must not apply")
        )


@pytest.mark.parametrize("boundary", ["manifest", "completion_journal"])
def test_mutation_recorder_recovers_crashes_after_effect_before_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    target = tmp_path / "bin" / "dispatcher"
    target.parent.mkdir()
    recorder = _recorder(tmp_path)
    real_manifest_save = Manifest.save
    real_journal_save = TransactionJournal.save

    if boundary == "manifest":
        monkeypatch.setattr(
            Manifest,
            "save",
            lambda _manifest: (_ for _ in ()).throw(RuntimeError("manifest crash")),
        )
    else:
        def fail_completion(
            journal: TransactionJournal, *args: object, **kwargs: object
        ) -> None:
            if journal.pending_mutation is None:
                raise RuntimeError("journal crash")
            real_journal_save(journal, *args, **kwargs)

        monkeypatch.setattr(TransactionJournal, "save", fail_completion)

    with pytest.raises(RuntimeError, match="crash"):
        _mutate_dispatcher(
            recorder,
            target,
            lambda: (target.write_bytes(b"dispatcher\n"), target.chmod(0o755)),
        )

    durable = TransactionJournal.load(
        tmp_path / "state" / "transaction-journal.json",
        state_root=tmp_path / "state",
    )
    assert durable.pending_mutation is not None
    if boundary == "completion_journal":
        assert Manifest(
            tmp_path / "state" / "install-manifest.json",
            state_root=tmp_path / "state",
        ).entries == [{"kind": "file", "path": str(target)}]
    else:
        monkeypatch.setattr(Manifest, "save", real_manifest_save)
    monkeypatch.setattr(TransactionJournal, "save", real_journal_save)

    resumed = _recorder(tmp_path, journal=durable)
    _mutate_dispatcher(resumed, target, lambda: pytest.fail("adopt, do not replay"))

    assert resumed.journal.pending_mutation is None


@pytest.mark.parametrize(
    ("delta", "initial", "expected"),
    [
        ({"action": "none"}, [], []),
        (
            {"action": "forget", "kind": "file", "path": "/owned"},
            [{"kind": "file", "path": "/owned"}],
            [],
        ),
    ],
)
def test_mutation_recorder_applies_none_and_forget_ownership_deltas(
    tmp_path: Path,
    delta: dict[str, object],
    initial: list[dict[str, object]],
    expected: list[dict[str, object]],
) -> None:
    state_root = tmp_path / "state"
    journal = _journal(pending_mutation=None)
    journal_path = state_root / "transaction-journal.json"
    journal.save(journal_path, state_root=state_root)
    manifest = Manifest(state_root / "install-manifest.json", state_root=state_root)
    manifest.entries = initial
    manifest.save()
    recorder = state_record.MutationRecorder(
        journal=journal,
        journal_path=journal_path,
        state_root=state_root,
        manifest=manifest,
    )
    state = {"kind": "git_config_value", "value": ".githooks"}

    recorder.mutate(
        operation_key="git.hooks-path",
        kind="git_config_value",
        resource_kind="git_config",
        resource_id=state_record.git_config_resource_id(
            repo=tmp_path, key="core.hooksPath"
        ),
        intended_after=state,
        ownership_delta=delta,
        observe=lambda: state,
        apply=lambda: pytest.fail("already intended state must be adopted"),
    )

    assert Manifest(manifest.path, state_root=state_root).entries == expected


def test_recovery_classifies_expected_state_as_pending_without_replay(
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
    mutation = _filesystem_mutation(
        target,
        intended_after=intended,
        ownership_entry={"kind": "file", "path": str(target)},
    )

    manifest = Manifest(tmp_path / "install-manifest.json", state_root=tmp_path)
    journal = _journal(pending_mutation=mutation)
    recovered = recover_pending_mutation(
        journal,
        manifest=manifest,
    )

    assert recovered is journal
    assert recovered.pending_mutation is mutation
    assert recovered.completed_mutation_ids == ()
    assert snapshot_path_state(target) == {"kind": "absent"}
    assert not manifest.path.exists()


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

    def unexpected_snapshot(_path: Path) -> dict[str, object]:
        nonlocal path_observed
        path_observed = True
        pytest.fail("certificate selector path must not be observed generically")

    monkeypatch.setattr(
        sys.modules[recover_pending_mutation.__module__],
        "snapshot_path_state",
        unexpected_snapshot,
    )

    with pytest.raises(StateRecordError, match="certificate selector.*canonical"):
        recover_pending_mutation(
            journal,
            manifest=manifest,
        )

    assert path_observed is False


def test_recovery_adopts_already_completed_pending_mutation(tmp_path: Path) -> None:
    target = tmp_path / "dispatcher"
    target.write_bytes(b"dispatcher\n")
    intended = snapshot_path_state(target)
    mutation = _filesystem_mutation(
        target,
        intended_after=intended,
        ownership_entry={"kind": "file", "path": str(target)},
    )
    manifest_path_value = tmp_path / "install-manifest.json"
    manifest = Manifest(manifest_path_value, state_root=tmp_path)
    recovered = recover_pending_mutation(
        _journal(pending_mutation=mutation),
        manifest=manifest,
    )

    assert recovered.pending_mutation is None
    assert recovered.completed_mutation_ids == (mutation.mutation_id,)
    assert Manifest(manifest_path_value, state_root=tmp_path).entries == [
        {"kind": "file", "path": str(target)}
    ]


def test_recovery_fails_closed_on_third_path_state(tmp_path: Path) -> None:
    target = tmp_path / "dispatcher"
    target.write_bytes(b"user-owned\n")
    mutation = _filesystem_mutation(
        target,
        intended_after={
            "kind": "file",
            "mode": 0o644,
            "size": 11,
            "sha256": hashlib.sha256(b"dispatcher\n").hexdigest(),
        },
        ownership_entry={"kind": "file", "path": str(target)},
    )
    with pytest.raises(StateRecordError, match="third state"):
        manifest = Manifest(tmp_path / "install-manifest.json", state_root=tmp_path)
        recover_pending_mutation(
            _journal(pending_mutation=mutation),
            manifest=manifest,
        )
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
    mutation_id_for,
    recover_pending_mutation,
    snapshot_path_state,
)

fifo = Path(sys.argv[2])
manifest_path = Path(sys.argv[3])
declared = {"kind": "other", "mode": stat.S_IMODE(fifo.lstat().st_mode)}
state = snapshot_path_state(fifo)
mutation_fields = dict(
    operation_key="test.fifo",
    kind="file",
    resource_kind="filesystem",
    resource_id=str(fifo),
    intended_after=declared,
    ownership_delta={"action": "none"},
)
mutation = JournalMutation(
    mutation_id=mutation_id_for(transaction_id="1" * 32, **mutation_fields),
    expected_before={"kind": "absent"},
    **mutation_fields,
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
    "completed": [state_record.mutation_id_for(
        transaction_id="1" * 32,
        operation_key="test.fifo",
        kind="file",
        resource_kind="filesystem",
        resource_id=str(fifo),
        intended_after=expected_state,
        ownership_delta={"action": "none"},
    )],
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
