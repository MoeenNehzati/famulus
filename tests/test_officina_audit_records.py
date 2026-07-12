from __future__ import annotations

from officina.common.audit_records import (
    attach_record_digest,
    compute_record_digest,
    record_digest_matches,
)


def test_record_digest_ignores_existing_digest_field() -> None:
    record = {"skill": "demo-skill", "checks": {"semantic": {"passed": True}}}
    signed = attach_record_digest(record)

    assert signed["record_digest"] == compute_record_digest(signed)
    assert record_digest_matches(signed)


def test_record_digest_changes_when_record_content_changes() -> None:
    signed = attach_record_digest({"skill": "demo-skill", "checks": {"semantic": {"passed": True}}})

    signed["checks"]["semantic"]["passed"] = False

    assert not record_digest_matches(signed)
