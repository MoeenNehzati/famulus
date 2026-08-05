"""Deduplicate rescan candidates against items already present in a
destination list, identified by ``source.message_id``, so a historical
rescan (an operator re-fetching mail from an arbitrary earlier watermark)
can safely re-run without creating duplicate `todo`/`triage` entries.

Pure functions only -- no dispatch, no network, no filesystem access. The
destination list's already-parsed YAML document is supplied by the caller
(e.g. `_mail_envelope_stream.py`, which fetches it via a declared dispatch
to `list-manager._rtx.interface.cloud-read` and passes the parsed result in).
This keeps the cross-skill boundary explicit: this module never imports
list-manager's `_rtx` internals directly.
"""
from __future__ import annotations

from typing import Any


def collect_existing_message_ids(node: Any) -> set[str]:
    """Recursively collect every `source.message_id` found anywhere in a
    parsed destination-list YAML tree (categories/items, arbitrarily nested,
    including `children`)."""
    ids: set[str] = set()
    if isinstance(node, dict):
        source = node.get("source")
        if isinstance(source, dict):
            message_id = source.get("message_id")
            if message_id:
                ids.add(message_id)
        for value in node.values():
            ids |= collect_existing_message_ids(value)
    elif isinstance(node, list):
        for item in node:
            ids |= collect_existing_message_ids(item)
    return ids


def _candidate_message_id(candidate: dict) -> str | None:
    """A candidate may be either an email envelope (top-level `message_id`,
    e.g. straight off `fetch-filtered-envelopes`) or an already-shaped list
    entry (nested `source.message_id`). Accept either shape."""
    message_id = candidate.get("message_id")
    if message_id:
        return message_id
    source = candidate.get("source")
    if isinstance(source, dict):
        return source.get("message_id")
    return None


def filter_destination_duplicates(
    candidates: list[dict], *, existing_entries: dict
) -> list[dict]:
    """Return only the candidates whose message_id is not already present
    anywhere in `existing_entries` (a parsed destination-list YAML document).

    Candidates with no discoverable message_id are always kept -- there is
    nothing to dedup against, so dropping them would silently lose data.
    """
    existing_ids = collect_existing_message_ids(existing_entries)
    kept = []
    for candidate in candidates:
        message_id = _candidate_message_id(candidate)
        if message_id is not None and message_id in existing_ids:
            continue
        kept.append(candidate)
    return kept
