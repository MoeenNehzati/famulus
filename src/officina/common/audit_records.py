"""Helpers for local skill audit records."""

from __future__ import annotations

import hashlib
import json
from typing import Any


RECORD_DIGEST_FIELD = "record_digest"


def canonical_record_bytes(record: dict[str, Any]) -> bytes:
    """Return stable JSON bytes for record integrity checks."""

    unsigned = {key: value for key, value in record.items() if key != RECORD_DIGEST_FIELD}
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return canonical.encode("utf-8")


def compute_record_digest(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_record_bytes(record)).hexdigest()


def attach_record_digest(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result[RECORD_DIGEST_FIELD] = compute_record_digest(result)
    return result


def record_digest_matches(record: dict[str, Any]) -> bool:
    digest = record.get(RECORD_DIGEST_FIELD)
    return isinstance(digest, str) and digest == compute_record_digest(record)
