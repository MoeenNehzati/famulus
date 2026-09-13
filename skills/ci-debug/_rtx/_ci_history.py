"""Deterministic models, analysis, and publication for CI history evidence."""

from __future__ import annotations

import ast
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import stat
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse

from officina.common.atomic_files import (
    atomic_create_bytes,
    ensure_private_directory,
    read_regular_file_bytes,
)

try:
    from ._runner_labels import classify_runner_labels
except ImportError:
    from _runner_labels import classify_runner_labels


SCHEMA_VERSION = 1
TERMINAL_CONCLUSIONS = {
    "success", "failure", "cancelled", "timed_out", "skipped",
    "action_required", "neutral", "stale", "startup_failure",
}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
NODE_PREFIX = re.compile(
    r"^(?:(?:FAILED|ERROR|XPASS|XFAIL)\s*|\[[^]]+\]\s*)*",
    re.IGNORECASE,
)
PY_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$", re.UNICODE)
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


class HistoryError(RuntimeError):
    """Carry a stable error code across CI history operations.

    Intent
    ------
    Preserve a machine-readable failure category beside the human message.

    Rationale
    ---------
    Command adapters need stable classifications without parsing prose.

    Pseudocode
    ----------
    - set operation = `store error code with runtime error message`

    Wraps
    -----
    - none
    """

    def __init__(self, code: str, message: str) -> None:
        """Initialize a classified history error.

        Intent
        ------
        Bind one stable code to one runtime error message.

        Rationale
        ---------
        Callers report the code independently from diagnostic text.

        Pseudocode
        ----------
        - set operation = `initialize runtime error with message`
        - set error_code = code

        Wraps
        -----
        - none
        """
        super().__init__(message)
        self.code = code


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON-compatible object to canonical UTF-8 bytes.

    Intent
    ------
    Produce deterministic bytes for stored records and content digests.

    Rationale
    ---------
    Stable key order and separators make identical logical values byte-identical.

    Pseudocode
    ----------
    - set operation = `serialize input with sorted keys and compact separators`
    - set operation = `append newline and encode as UTF-8`

    Wraps
    -----
    - none
    """
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Return the hexadecimal SHA-256 digest of bytes.

    Intent
    ------
    Compute a stable identity for an in-memory publication member.

    Rationale
    ---------
    Publication verification compares exact bytes rather than parsed objects.

    Pseudocode
    ----------
    - set operation = `hash input bytes with SHA-256`
    - return hexadecimal digest

    Wraps
    -----
    - none
    """
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the streaming SHA-256 digest of a file.

    Intent
    ------
    Hash a potentially large file without loading it wholly into memory.

    Rationale
    ---------
    Fixed-size reads bound transient memory during evidence verification.

    Pseudocode
    ----------
    - set operation = `initialize SHA-256 digest`
    - set operation = `update digest for each file block`
    - return hexadecimal digest

    Wraps
    -----
    - none
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_timestamp(value: str | None) -> str | None:
    """Normalize an optional timezone-aware timestamp to UTC text.

    Intent
    ------
    Canonicalize GitHub timestamp spellings while retaining missing values.

    Rationale
    ---------
    Reports need sortable timestamps with an explicit common timezone.

    Pseudocode
    ----------
    - return missing input unchanged
    - set operation = `parse timezone-aware timestamp`
    - return microsecond UTC representation

    Wraps
    -----
    - none
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def elapsed_ms(start: str | None, end: str | None) -> int | None:
    """Compute non-negative elapsed milliseconds between two timestamps.

    Intent
    ------
    Convert available API boundaries into one descriptive duration.

    Rationale
    ---------
    Missing, malformed, or reversed boundaries must remain explicitly missing.

    Pseudocode
    ----------
    - return missing when either boundary is absent or malformed
    - set operation = `compute rounded millisecond difference`
    - return only non-negative duration

    Wraps
    -----
    - none
    """
    if not start or not end:
        return None
    try:
        left = datetime.fromisoformat(start.replace("Z", "+00:00"))
        right = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    result = round((right - left).total_seconds() * 1000)
    return result if result >= 0 else None


def _resolved(path: Path) -> Path:
    """Resolve a path without requiring its final component to exist.

    Intent
    ------
    Normalize candidate and forbidden paths for containment checks.

    Rationale
    ---------
    Output destinations are intentionally absent before reservation.

    Pseudocode
    ----------
    - return non-strict resolved path

    Wraps
    -----
    - none
    """
    return path.resolve(strict=False)


def require_outside(path: Path, forbidden: Iterable[Path]) -> None:
    """Reject a destination inside any forbidden root.

    Intent
    ------
    Keep collected evidence outside every repository worktree.

    Rationale
    ---------
    Reports and raw logs may contain private or untracked CI evidence.

    InstantiationsFromRepo
    ----------------------
      ._resolved:
        why:
          transforms: "Normalizes candidate and forbidden roots before containment checks."
      .HistoryError:
        why:
          raises: "Classifies a destination that falls inside a repository worktree."

    Pseudocode
    ----------
    - set operation = `resolve candidate destination`
    - set containment_checked = `all forbidden roots`

    Wraps
    -----
    - none
    """
    candidate = _resolved(path)
    for root in forbidden:
        resolved = _resolved(root)
        if candidate == resolved or candidate.is_relative_to(resolved):
            raise HistoryError("destination_in_repository", "destination must be outside every worktree")


def reserve_private_root(path: Path) -> Path:
    """Atomically reserve a new private output directory.

    Intent
    ------
    Create an absent destination with restrictive permissions and no symlink ancestry.

    Rationale
    ---------
    Exclusive reservation prevents accidental replacement or evidence disclosure.

    CallsFromRepo
    -------------
      .officina.common.atomic_files.ensure_private_directory:
        why:
          validates: "Confirms the reserved directory keeps owner-only permissions."

    InstantiationsFromRepo
    ----------------------
      .HistoryError:
        why:
          raises: "Classifies existing destinations and symlink ancestry violations."

    Pseudocode
    ----------
    - set operation = `reject existing destination and symlink ancestors`
    - set operation = `create directory with owner-only permissions`
    - set operation = `validate private directory boundary`
    - return resolved directory

    Wraps
    -----
    - none
    """
    candidate = Path(path)
    if candidate.exists() or candidate.is_symlink():
        raise HistoryError("destination_exists", "destination must not exist")
    cursor = candidate.parent.absolute()
    while True:
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise HistoryError("symlink_ancestor", "destination ancestor cannot be a symlink")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    parent = candidate.parent.absolute()
    ensure_private_directory(candidate, allowed_root=parent)
    return candidate.absolute()


def _write_once(root: Path, relative: str, data: bytes) -> None:
    """Create one publication member without replacement.

    Intent
    ------
    Materialize a private member beneath an already reserved root.

    Rationale
    ---------
    Atomic create-only writes preserve immutable publication semantics.

    CallsFromRepo
    -------------
      .officina.common.atomic_files.atomic_create_bytes:
        why:
          writes: "Creates the publication member once with restrictive permissions."

    InstantiationsFromRepo
    ----------------------
      .HistoryError:
        why:
          raises: "Classifies a collision when a publication member already exists."

    Pseudocode
    ----------
    - set operation = `create private parent directories`
    - set operation = `atomically create member bytes`
    - set operation = `reject destination collision`

    Wraps
    -----
    - none
    """
    destination = root / relative
    if destination.parent != root:
        try:
            ensure_private_directory(destination.parent, allowed_root=root)
        except OSError as exc:
            raise HistoryError(
                "invalid_member",
                "publication member parent cannot be created through a redirected path",
            ) from exc
    if not atomic_create_bytes(destination, data, allowed_root=root, mode=0o600):
        raise HistoryError("destination_collision", f"refused to replace {relative}")


def publish_tree(
    root: Path,
    *,
    manifest_fields: dict[str, Any],
    members: dict[str, bytes],
    outcome: str,
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write members, a self-excluding manifest digest, and a final marker.

    Intent
    ------
    Publish an immutable evidence tree whose logical identity is independently verifiable.

    Rationale
    ---------
    Writing the publication marker last distinguishes complete trees from abandoned output.

    CallsFromRepo
    -------------
      ._write_once:
        why:
          writes: "Creates each member and publication metadata without replacement."
      .canonical_json_bytes:
        why:
          serializes: "Produces deterministic manifest and publication metadata bytes."

    InstantiationsFromRepo
    ----------------------
      .HistoryError:
        why:
          raises: "Classifies invalid publication member paths."
      .canonical_json_bytes:
        why:
          constructs: "Constructs the canonical manifest representation used for its digest."
      .sha256_bytes:
        why:
          constructs: "Constructs member, snapshot, and manifest content identities."

    Pseudocode
    ----------
    - set operation = `validate and write each member`
    - set operation = `compute member and logical snapshot digests`
    - set publication_order = `manifest then marker`
    - return publication record

    Wraps
    -----
    - none
    """

    for relative in sorted(members):
        pure = PurePosixPath(relative)
        if pure.is_absolute() or "\\" in relative or re.match(r"^[A-Za-z]:", relative) or ".." in pure.parts or relative in {"manifest.json", "publication.json"}:
            raise HistoryError("invalid_member", "refused an invalid publication member path")
        _write_once(root, relative, members[relative])
    member_digests = [
        {"path": relative, "sha256": sha256_bytes(members[relative])}
        for relative in sorted(members)
    ]
    manifest_without_digest = {
        "schema_version": SCHEMA_VERSION,
        **manifest_fields,
        "gaps": sorted(gaps, key=gap_sort_key),
        "members": member_digests,
    }
    snapshot_digest = sha256_bytes(canonical_json_bytes(manifest_without_digest))
    manifest = {**manifest_without_digest, "snapshot_digest": snapshot_digest}
    manifest_bytes = canonical_json_bytes(manifest)
    _write_once(root, "manifest.json", manifest_bytes)
    publication = {
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "snapshot_digest": snapshot_digest,
        "manifest_sha256": sha256_bytes(manifest_bytes),
    }
    _write_once(root, "publication.json", canonical_json_bytes(publication))
    return publication


def load_published_tree(root: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Load and verify one private immutable publication tree.

    Intent
    ------
    Return manifest and members only after structural, permission, and digest checks.

    Rationale
    ---------
    Reporters must fail closed on tampering, partial publication, or undeclared files.

    CallsFromRepo
    -------------
      .canonical_json_bytes:
        why:
          serializes: "Reconstructs canonical manifest bytes for snapshot verification."
      .sha256_bytes:
        why:
          validates: "Compares manifest, snapshot, and member content identities."
      .file_mode_is_private:
        why:
          validates: "Checks that publication metadata and declared members retain private modes."
      .officina.common.atomic_files.ensure_private_directory:
        why:
          validates: "Confirms the publication root remains a confined private directory."
    InstantiationsFromRepo
    ----------------------
      .officina.common.atomic_files.read_regular_file_bytes:
        why:
          misc: "Obtains metadata and member bytes through confined no-follow descriptor walks after privacy checks."
      .HistoryError:
        why:
          raises: "Classifies malformed, incomplete, or tampered publication trees."

    Pseudocode
    ----------
    - set operation = `validate private root and metadata files`
    - set operation = `verify manifest and snapshot digests`
    - set operation = `verify every declared member and reject extras or symlinks`
    - return manifest and member bytes

    Wraps
    -----
    - none
    """
    lexical_root = Path(root)
    cursor = lexical_root.absolute()
    while True:
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise HistoryError("invalid_publication", "published tree path cannot traverse a redirect")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    root = lexical_root.absolute()
    if not root.is_dir():
        raise HistoryError("invalid_publication", "published tree root is not a private directory")
    try:
        ensure_private_directory(root, allowed_root=root.parent)
    except OSError as exc:
        raise HistoryError(
            "invalid_publication", "published tree root is not a private directory"
        ) from exc
    try:
        manifest_path = root / "manifest.json"
        publication_path = root / "publication.json"
        if manifest_path.is_symlink() or publication_path.is_symlink():
            raise HistoryError("invalid_publication", "publication metadata cannot be symlinked")
        if not file_mode_is_private(manifest_path) or not file_mode_is_private(publication_path):
            raise HistoryError("invalid_publication", "publication metadata is not private")
        manifest_bytes = read_regular_file_bytes(manifest_path, allowed_root=root)
        publication_bytes = read_regular_file_bytes(publication_path, allowed_root=root)
        publication = json.loads(publication_bytes.decode("utf-8"))
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HistoryError("invalid_publication", "published tree metadata is unavailable") from exc
    if not isinstance(publication, dict) or not isinstance(manifest, dict):
        raise HistoryError("invalid_publication", "published tree metadata has an invalid shape")
    if publication.get("schema_version") != SCHEMA_VERSION or manifest.get("schema_version") != SCHEMA_VERSION:
        raise HistoryError("unsupported_schema", "published tree schema version is unsupported")
    if publication.get("outcome") not in {"collected", "collected-with-gaps", "reported", "reported-with-gaps"}:
        raise HistoryError("invalid_publication", "publication outcome is invalid")
    if not isinstance(manifest.get("members"), list) or not isinstance(manifest.get("gaps"), list):
        raise HistoryError("invalid_publication", "manifest collections have invalid shapes")
    if publication.get("manifest_sha256") != sha256_bytes(manifest_bytes):
        raise HistoryError("digest_mismatch", "manifest digest mismatch")
    expected_snapshot = manifest.get("snapshot_digest")
    without_digest = {key: value for key, value in manifest.items() if key != "snapshot_digest"}
    if expected_snapshot != sha256_bytes(canonical_json_bytes(without_digest)):
        raise HistoryError("digest_mismatch", "logical snapshot digest mismatch")
    if publication.get("snapshot_digest") != expected_snapshot:
        raise HistoryError("digest_mismatch", "publication snapshot identity mismatch")
    members: dict[str, bytes] = {}
    declared: set[str] = set()
    for item in manifest.get("members", []):
        if not isinstance(item, dict):
            raise HistoryError("invalid_member", "manifest member must be an object")
        relative = str(item.get("path", ""))
        pure = PurePosixPath(relative)
        if pure.is_absolute() or "\\" in relative or re.match(r"^[A-Za-z]:", relative) or ".." in pure.parts or relative in {"manifest.json", "publication.json"}:
            raise HistoryError("invalid_member", "manifest contains an invalid member path")
        if relative in declared:
            raise HistoryError("invalid_member", "manifest contains a duplicate member path")
        declared.add(relative)
        path = root.joinpath(*pure.parts)
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or not path.is_file():
            raise HistoryError("invalid_member", f"snapshot member unavailable: {relative}")
        if not file_mode_is_private(path):
            raise HistoryError("invalid_member", f"snapshot member is not private: {relative}")
        try:
            data = read_regular_file_bytes(path, allowed_root=root)
        except OSError as exc:
            raise HistoryError(
                "invalid_member", f"snapshot member unavailable: {relative}"
            ) from exc
        if sha256_bytes(data) != item.get("sha256"):
            raise HistoryError("digest_mismatch", f"snapshot member digest mismatch: {relative}")
        members[relative] = data
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    } - {"manifest.json", "publication.json"}
    if actual != declared:
        raise HistoryError("invalid_member", "published tree contains undeclared or missing members")
    if any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) for path in root.rglob("*")):
        raise HistoryError("invalid_member", "published tree contains a symlink")
    return manifest, members


def gap(scope_type: str, scope_id: str, operation: str, code: str, detail: str) -> dict[str, Any]:
    """Construct a deterministic classified evidence-gap record.

    Intent
    ------
    Preserve incomplete collection details with a stable identity.

    Rationale
    ---------
    Sorting and deduplication require identifiers independent of insertion order.

    CallsFromRepo
    -------------
      .canonical_json_bytes:
        why:
          serializes: "Produces deterministic bytes for the complete gap identity."

    InstantiationsFromRepo
    ----------------------
      .sha256_bytes:
        why:
          constructs: "Constructs stable detail and gap identifiers."

    Pseudocode
    ----------
    - set operation = `hash diagnostic detail`
    - set operation = `assemble classified gap fields`
    - set operation = `hash fields into gap identifier`
    - return gap record

    Wraps
    -----
    - none
    """
    detail_digest = sha256_bytes(detail.encode("utf-8"))
    fields = {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "operation": operation,
        "code": code,
        "detail": detail,
        "detail_digest": detail_digest,
    }
    fields["gap_id"] = sha256_bytes(canonical_json_bytes(fields))
    return fields


def gap_sort_key(item: dict[str, Any]) -> tuple[str, ...]:
    """Return the canonical ordering key for a gap record.

    Intent
    ------
    Sort gaps deterministically across concurrent collection orderings.

    Rationale
    ---------
    Stable manifests must not depend on task completion timing.

    Pseudocode
    ----------
    - set operation = `select ordered classification and detail fields`
    - return fields as text tuple

    Wraps
    -----
    - none
    """
    return tuple(str(item.get(key, "")) for key in (
        "scope_type", "scope_id", "operation", "code", "detail_digest"
    ))


def parse_pytest_node(raw: str) -> dict[str, Any]:
    """Parse one pytest failure line into exact and canonical test identities.

    Intent
    ------
    Normalize safe relative node IDs while retaining original parameterized text.

    Rationale
    ---------
    Episode incidence counts the test definition, not each parameter spelling.

    Pseudocode
    ----------
    - set operation = `remove terminal decoration and status prefixes`
    - set operation = `validate relative Python path and scope identifiers`
    - set operation = `separate final parameter suffix from test function`
    - return exact and canonical identities or rejection reason

    Wraps
    -----
    - none
    """
    exact = raw.rstrip("\r\n")
    cleaned = ANSI.sub("", exact).strip()
    cleaned = NODE_PREFIX.sub("", cleaned)
    if " - " in cleaned:
        cleaned = cleaned.split(" - ", 1)[0].strip()
    parts = cleaned.replace("\\", "/").split("::")
    if len(parts) < 2:
        return {"exact": exact, "canonical": None, "reason": "missing_scope"}
    path = PurePosixPath(parts[0])
    if path.is_absolute() or re.match(r"^[A-Za-z]:/", parts[0]) or ".." in path.parts or not path.suffix == ".py":
        return {"exact": exact, "canonical": None, "reason": "invalid_path"}
    scopes = parts[1:]
    final = scopes[-1]
    parameter = None
    bracket = final.find("[")
    if bracket >= 0:
        if not final.endswith("]"):
            return {"exact": exact, "canonical": None, "reason": "malformed_parameter"}
        parameter = final[bracket + 1 : -1]
        final = final[:bracket]
    if not PY_IDENTIFIER.fullmatch(final) or not final.startswith("test"):
        return {"exact": exact, "canonical": None, "reason": "invalid_test_function"}
    if any(not PY_IDENTIFIER.fullmatch(scope) for scope in scopes[:-1]):
        return {"exact": exact, "canonical": None, "reason": "invalid_scope"}
    canonical = "::".join((path.as_posix(), *scopes[:-1], final))
    return {"exact": exact, "canonical": canonical, "parameter": parameter, "reason": None}


def extract_pytest_nodes(text: str, *, source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract pytest node occurrences and parse gaps from log text.

    Intent
    ------
    Attach source coordinates to every recognizable pytest failure line.

    Rationale
    ---------
    Reports need deterministic evidence ordering and explicit unparsable candidates.

    InstantiationsFromRepo
    ----------------------
      .gap:
        why:
          constructs: "Constructs classified records for malformed pytest candidates."
      .parse_pytest_node:
        why:
          constructs: "Constructs normalized identities from candidate failure lines."

    Pseudocode
    ----------
    - set operation = `scan lines containing a test scope marker`
    - set operation = `parse candidate node and record malformed candidates as gaps`
    - set operation = `deduplicate exact line occurrences by offset`
    - return occurrences and gaps

    Wraps
    -----
    - none
    """
    occurrences: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for offset, line in enumerate(text.splitlines()):
        if "::test" not in line.replace("\\", "/"):
            continue
        parsed = parse_pytest_node(line)
        if parsed["canonical"] is None:
            gaps.append(gap("log-line", f"{source.get('log_path', '')}:{offset}", "parse-node", parsed["reason"], line[:500]))
            continue
        key = (parsed["exact"], offset)
        if key in seen:
            continue
        seen.add(key)
        occurrences.append({**source, **parsed, "source_offset": offset})
    return occurrences, gaps


def _run_key(run: dict[str, Any]) -> tuple[int, int]:
    """Return branch-progression ordering fields for one logical run.

    Intent
    ------
    Order runs by GitHub run number with run ID as a deterministic tiebreaker.

    Rationale
    ---------
    API response order is not authoritative for episode construction.

    Pseudocode
    ----------
    - return integer run number and run identifier

    Wraps
    -----
    - none
    """
    return int(run.get("run_number", 0)), int(run.get("run_id", 0))


def authoritative_attempt(run: dict[str, Any]) -> dict[str, Any] | None:
    """Select the latest completed terminal attempt when listing is complete.

    Intent
    ------
    Determine the single authoritative outcome for a logical workflow run.

    Rationale
    ---------
    Earlier rerun attempts and incomplete attempt listings cannot define final status.

    Pseudocode
    ----------
    - set operation = `sort attempts by attempt number`
    - set operation = `reject empty or incomplete listings`
    - set operation = `reject nonterminal latest attempt`
    - return latest terminal attempt

    Wraps
    -----
    - none
    """
    expected = run.get("expected_attempt_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        return None
    attempts = sorted(run.get("attempts", []), key=lambda item: int(item.get("attempt_number", 0)))
    if not attempts or not run.get("attempt_listing_complete", False):
        return None
    if [item.get("attempt_number") for item in attempts] != list(range(1, expected + 1)):
        return None
    latest = attempts[-1]
    if latest.get("status") != "completed" or latest.get("conclusion") not in TERMINAL_CONCLUSIONS:
        return None
    return latest


def build_episodes(runs: list[dict[str, Any]], sequence_gaps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build deduplicated red spans bounded by consecutive green runs.

    Intent
    ------
    Partition branch history into complete green-to-green failure episodes and censored spans.

    Rationale
    ---------
    One episode counts recurring failures once until the branch becomes green again.

    CallsFromRepo
    -------------
      .canonical_json_bytes:
        why:
          serializes: "Compares complete occurrence records during deduplication."

    InstantiationsFromRepo
    ----------------------
      .authoritative_attempt:
        why:
          constructs: "Selects the terminal attempt that determines each logical run outcome."

    Pseudocode
    ----------
    - set operation = `order logical runs by branch progression`
    - set operation = `reset on unavailable authoritative attempts`
    - set operation = `close red spans when the next green arrives`
    - set operation = `deduplicate and index observed tests per episode`
    - return complete episodes and censored spans

    Wraps
    -----
    - none
    """
    ordered = sorted(runs, key=_run_key)
    gap_before = {
        item.get("newer_run_id") for item in (sequence_gaps or [])
        if item.get("newer_run_id") is not None
    }
    episodes: list[dict[str, Any]] = []
    censored: list[dict[str, Any]] = []
    preceding_green: dict[str, Any] | None = None
    reds: list[dict[str, Any]] = []
    leading: list[dict[str, Any]] = []
    for run in ordered:
        if run.get("run_id") in gap_before:
            if preceding_green or reds:
                censored.append({
                    "side": "interior",
                    "run_ids": ([preceding_green["run_id"]] if preceding_green else [])
                    + [item["run_id"] for item in reds],
                    "reason": "run_index_evidence_gap",
                })
            preceding_green = None
            reds = []
        attempt = authoritative_attempt(run)
        if attempt is None:
            if preceding_green or reds:
                censored.append({
                    "side": "interior",
                    "run_ids": ([preceding_green["run_id"]] if preceding_green else [])
                    + [item["run_id"] for item in reds]
                    + [run.get("run_id")],
                    "reason": "authoritative_attempt_unavailable",
                })
            else:
                leading.append(run)
            preceding_green = None
            reds = []
            continue
        if attempt["conclusion"] == "success":
            if preceding_green is not None and reds:
                episode_id = f"{preceding_green['run_id']}..{run['run_id']}"
                occurrences = []
                for red in reds:
                    for candidate in red.get("attempts", []):
                        occurrences.extend(candidate.get("occurrences", []))
                occurrences.sort(key=occurrence_sort_key)
                occurrences = [
                    occurrence
                    for index, occurrence in enumerate(occurrences)
                    if index == 0
                    or canonical_json_bytes(occurrence)
                    != canonical_json_bytes(occurrences[index - 1])
                ]
                incidence: dict[str, dict[str, Any]] = {}
                for occurrence in occurrences:
                    canonical = occurrence.get("canonical")
                    if canonical and canonical not in incidence:
                        incidence[canonical] = occurrence
                red_attempts = [attempt for red in reds for attempt in red.get("attempts", [])]
                expected_logs = sum(bool(attempt.get("failure_log_expected")) for attempt in red_attempts)
                available_logs = sum(bool(attempt.get("failure_log_available")) for attempt in red_attempts)
                episodes.append({
                    "episode_id": episode_id,
                    "preceding_green_run_id": preceding_green["run_id"],
                    "preceding_green_sha": preceding_green.get("head_sha"),
                    "next_green_run_id": run["run_id"],
                    "next_green_sha": run.get("head_sha"),
                    "red_run_ids": [item["run_id"] for item in reds],
                    "evidence_complete": all(item.get("failure_evidence_complete", False) for item in reds),
                    "coverage": {
                        "failed_attempt_logs_expected": expected_logs,
                        "failed_attempt_logs_available": available_logs,
                        "job_listings_expected": len(red_attempts),
                        "job_listings_complete": sum(bool(attempt.get("jobs_listing_complete")) for attempt in red_attempts),
                    },
                    "occurrences": occurrences,
                    "incidence": [
                        {"canonical_test_id": key, "first_observation": incidence[key]}
                        for key in sorted(incidence)
                    ],
                })
            if preceding_green is None and leading:
                censored.append({
                    "side": "leading",
                    "run_ids": [item["run_id"] for item in leading],
                    "reason": "no_preceding_green_in_window",
                })
                leading = []
            preceding_green = run
            reds = []
        elif preceding_green is not None:
            reds.append(run)
        else:
            leading.append(run)
    if preceding_green is None and leading:
        censored.append({
            "side": "leading",
            "run_ids": [item["run_id"] for item in leading],
            "reason": "no_preceding_green_in_window",
        })
    if reds:
        censored.append({"side": "trailing", "run_ids": [item["run_id"] for item in reds], "reason": "no_next_green_in_window"})
    if any(item.get("older_run_id") is None for item in (sequence_gaps or [])):
        censored.append({"side": "leading", "run_ids": [], "reason": "run_index_evidence_gap"})
    if any(item.get("newer_run_id") is None for item in (sequence_gaps or [])):
        censored.append({"side": "trailing", "run_ids": [], "reason": "run_index_evidence_gap"})
    return {"episodes": episodes, "censored_spans": censored}


def occurrence_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Return the total deterministic ordering key for one failure occurrence.

    Intent
    ------
    Stabilize first-observation selection across runs, attempts, jobs, and logs.

    Rationale
    ---------
    Concurrent collection must not change episode evidence output.

    Pseudocode
    ----------
    - return ordered numeric and textual source coordinates

    Wraps
    -----
    - none
    """
    return (
        int(item.get("run_number", 0)), int(item.get("run_id", 0)),
        int(item.get("attempt_number", 0)), int(item.get("job_id", 0)),
        int(item.get("step_ordinal", -1)), str(item.get("log_path", "")),
        int(item.get("source_offset", 0)), str(item.get("exact", "")),
    )


def build_failure_report(snapshot: dict[str, Any], git_evidence: dict[str, Any]) -> dict[str, Any]:
    """Build recurrence bounds and provenance for failure episodes.

    Intent
    ------
    Summarize how often each canonical test appears between consecutive greens.

    Rationale
    ---------
    Incomplete failure evidence contributes uncertainty rather than a false absence.

    InstantiationsFromRepo
    ----------------------
      .build_episodes:
        why:
          constructs: "Constructs the complete and censored episode sets summarized here."

    Pseudocode
    ----------
    - set operation = `build episodes and enumerate canonical tests`
    - set operation = `attach frozen provenance to each incidence`
    - set operation = `count observed and uncertain episode membership`
    - return exact recurrence numerators and censored spans

    Wraps
    -----
    - none
    """
    built = build_episodes(snapshot.get("runs", []), snapshot.get("sequence_gaps", []))
    episodes = built["episodes"]
    all_tests = sorted({
        item["canonical_test_id"]
        for episode in episodes
        for item in episode["incidence"]
    })
    tests: dict[str, dict[str, Any]] = {
        canonical: {"canonical_test_id": canonical, "k": 0, "u": 0, "episode_ids": []}
        for canonical in all_tests
    }
    evidence_lookup = {
        (item.get("episode_id"), item.get("canonical_test_id")): item
        for item in git_evidence.get("comparisons", [])
    }
    for episode in episodes:
        observed = {item["canonical_test_id"] for item in episode["incidence"]}
        for item in episode["incidence"]:
            canonical = item["canonical_test_id"]
            record = tests[canonical]
            record["k"] += 1
            record["episode_ids"].append(episode["episode_id"])
            item["provenance"] = evidence_lookup.get((episode["episode_id"], canonical), {"state": "not_found"})
        if not episode["evidence_complete"]:
            for canonical, record in tests.items():
                if canonical not in observed:
                    record["u"] += 1
    denominator = len(episodes)
    for record in tests.values():
        record["E"] = denominator
        record["lower_numerator"] = record["k"]
        record["upper_numerator"] = record["k"] + record["u"]
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_digest": snapshot.get("snapshot_digest"),
        "complete_failure_episodes": denominator,
        "episodes": episodes,
        "censored_spans": built["censored_spans"],
        "tests": [tests[key] for key in sorted(tests)],
    }


def _duration_summary(values: list[int], missing: int) -> dict[str, Any]:
    """Summarize integer durations with exact median representation.

    Intent
    ------
    Compute count, total, median, nearest-rank p90, maximum, and missing count.

    Rationale
    ---------
    Integer numerators avoid hidden floating-point rounding in benchmark output.

    Pseudocode
    ----------
    - set operation = `sort observed durations`
    - return empty summary when none are observed
    - set operation = `compute exact median numerator and denominator`
    - set operation = `compute total, nearest-rank p90, and maximum`

    Wraps
    -----
    - none
    """
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "missing": missing, "total_ms": 0, "median_numerator_ms": None, "median_denominator": None, "p90_ms": None, "maximum_ms": None}
    n = len(ordered)
    if n % 2:
        median_numerator, median_denominator = ordered[n // 2], 1
    else:
        median_numerator, median_denominator = ordered[n // 2 - 1] + ordered[n // 2], 2
    return {
        "n": n, "missing": missing, "total_ms": sum(ordered),
        "median_numerator_ms": median_numerator,
        "median_denominator": median_denominator,
        "p90_ms": ordered[math.ceil(0.9 * n) - 1], "maximum_ms": ordered[-1],
    }


def build_runtime_report(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Aggregate descriptive workflow, job, and step timing metrics.

    Intent
    ------
    Expose observed CI time distributions without claiming causal attribution.

    Rationale
    ---------
    Separate missing counters and normalized heuristic groups make limitations visible.

    InstantiationsFromRepo
    ----------------------
      ._duration_summary:
        why:
          constructs: "Constructs exact descriptive summaries for metrics and groups."
      .elapsed_ms:
        why:
          constructs: "Constructs elapsed values from available timestamp boundaries."
      .normalize_name:
        why:
          constructs: "Constructs stable heuristic grouping labels for jobs and steps."

    Pseudocode
    ----------
    - set operation = `traverse every collected run attempt`
    - set operation = `record API spans, job envelopes, job durations, and step durations`
    - set operation = `group jobs and steps by normalized labels and ordinals`
    - set operation = `summarize observations and missing boundaries`
    - return descriptive metrics and groups

    Wraps
    -----
    - none
    """
    metrics: dict[str, list[int]] = defaultdict(list)
    missing: dict[str, int] = defaultdict(int)
    job_groups: dict[tuple[str, tuple[str, ...]], list[int]] = defaultdict(list)
    job_group_missing: dict[tuple[str, tuple[str, ...]], int] = defaultdict(int)
    step_groups: dict[tuple[str, tuple[str, ...], int, str], list[int]] = defaultdict(list)
    step_group_missing: dict[tuple[str, tuple[str, ...], int, str], int] = defaultdict(int)
    for run in sorted(snapshot.get("runs", []), key=_run_key):
        for attempt in sorted(run.get("attempts", []), key=lambda item: int(item.get("attempt_number", 0))):
            update = elapsed_ms(attempt.get("created_at"), attempt.get("updated_at"))
            if update is None:
                missing["api_update_span_ms"] += 1
            else:
                metrics["api_update_span_ms"].append(update)
            if attempt.get("start_origin_proven"):
                start_delay = elapsed_ms(attempt.get("created_at"), attempt.get("run_started_at"))
                if start_delay is None:
                    missing["api_start_delay_ms"] += 1
                else:
                    metrics["api_start_delay_ms"].append(start_delay)
            else:
                missing["api_start_delay_ms"] += 1
            starts: list[str] = []
            completions: list[str] = []
            for job in attempt.get("jobs", []):
                key = (normalize_name(job.get("name", "")), tuple(sorted(str(label) for label in job.get("labels", []))))
                duration = elapsed_ms(job.get("started_at"), job.get("completed_at"))
                if duration is None:
                    missing["observed_job_elapsed_ms"] += 1
                    job_group_missing[key] += 1
                else:
                    metrics["observed_job_elapsed_ms"].append(duration)
                    job_groups[key].append(duration)
                    starts.append(job["started_at"])
                    completions.append(job["completed_at"])
                for step in job.get("steps", []):
                    ordinal = int(step["step_ordinal"])
                    step_key = (key[0], key[1], ordinal, normalize_name(step.get("name", "")))
                    step_duration = elapsed_ms(step.get("started_at"), step.get("completed_at"))
                    if step_duration is None:
                        missing["observed_step_elapsed_ms"] += 1
                        step_group_missing[step_key] += 1
                        continue
                    metrics["observed_step_elapsed_ms"].append(step_duration)
                    step_groups[step_key].append(step_duration)
            envelope = elapsed_ms(min(starts) if starts else None, max(completions) if completions else None)
            if envelope is None:
                missing["observed_job_execution_envelope_ms"] += 1
            else:
                metrics["observed_job_execution_envelope_ms"].append(envelope)
    summaries = {name: _duration_summary(values, missing[name]) for name, values in sorted(metrics.items())}
    for name in sorted(missing):
        summaries.setdefault(name, _duration_summary([], missing[name]))
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_digest": snapshot.get("snapshot_digest"),
        "metrics": summaries,
        "observed_job_minutes_numerator_ms": sum(metrics.get("observed_job_elapsed_ms", [])),
        "job_groups": [{"job_name": key[0], "labels": list(key[1]), **classify_runner_labels(list(key[1])), **_duration_summary(job_groups.get(key, []), job_group_missing[key])} for key in sorted(set(job_groups) | set(job_group_missing))],
        "step_groups": [{"job_name": key[0], "labels": list(key[1]), "step_ordinal": key[2], "step_name": key[3], **classify_runner_labels(list(key[1])), **_duration_summary(step_groups.get(key, []), step_group_missing[key])} for key in sorted(set(step_groups) | set(step_group_missing))],
    }


def normalize_name(value: str) -> str:
    """Collapse whitespace in an observed GitHub name.

    Intent
    ------
    Produce a stable heuristic grouping label for jobs and steps.

    Rationale
    ---------
    Superficial spacing differences should not split timing groups.

    Pseudocode
    ----------
    - set operation = `convert input to text`
    - set operation = `split and rejoin whitespace-separated words`

    Wraps
    -----
    - none
    """
    return " ".join(str(value).split())


def spreadsheet_safe(value: Any) -> str:
    """Quote text that spreadsheet software could interpret as a formula.

    Intent
    ------
    Preserve report fields as inert CSV text when opened interactively.

    Rationale
    ---------
    Untrusted CI names can begin with spreadsheet formula sigils.

    Pseudocode
    ----------
    - set operation = `convert optional input to text`
    - set operation = `inspect first non-space character`
    - set operation = `prefix dangerous text with an apostrophe`

    Wraps
    -----
    - none
    """
    text = "" if value is None else str(value)
    significant = text.lstrip(" ")
    return "'" + text if significant.startswith(FORMULA_PREFIXES) else text


def csv_bytes(headers: list[str], rows: Iterable[dict[str, Any]]) -> bytes:
    """Render report rows as spreadsheet-safe UTF-8 CSV bytes.

    Intent
    ------
    Produce a deterministic tabular export with a fixed column order.

    Rationale
    ---------
    Standard CSV quoting and formula neutralization protect downstream inspection.

    CallsFromRepo
    -------------
      .spreadsheet_safe:
        why:
          transforms: "Neutralizes formula-like values before CSV serialization."

    Pseudocode
    ----------
    - set csv_header = `fixed headers`
    - set operation = `sanitize and write each requested row field`
    - return UTF-8 bytes

    Wraps
    -----
    - none
    """
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: spreadsheet_safe(row.get(key)) for key in headers})
    return stream.getvalue().encode("utf-8")


def html_bytes(title: str, headers: list[str], rows: Iterable[dict[str, Any]]) -> bytes:
    """Render escaped report rows as a minimal UTF-8 HTML table.

    Intent
    ------
    Provide a human-readable local report without executable content.

    Rationale
    ---------
    Escaping every title, header, and cell treats CI metadata as untrusted text.

    Pseudocode
    ----------
    - set operation = `escape title and headers`
    - set operation = `escape every row cell`
    - set operation = `assemble static table document`
    - return UTF-8 bytes

    Wraps
    -----
    - none
    """
    safe_title = html.escape(title, quote=True)
    head = "".join(f"<th>{html.escape(header, quote=True)}</th>" for header in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(row.get(header, '')), quote=True)}</td>" for header in headers)
        body.append(f"<tr>{cells}</tr>")
    document = f"<!doctype html><meta charset=\"utf-8\"><title>{safe_title}</title><h1>{safe_title}</h1><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>\n"
    return document.encode("utf-8")


def validate_https_url(value: str, github_host: str) -> bool:
    """Check that a URL is anonymous HTTPS on the expected GitHub host.

    Intent
    ------
    Validate external evidence links before presentation.

    Rationale
    ---------
    Credentials, alternate schemes, and unrelated hosts are not trusted evidence links.

    Pseudocode
    ----------
    - set operation = `parse candidate URL`
    - set operation = `compare scheme and hostname`
    - set operation = `reject embedded username`

    Wraps
    -----
    - none
    """
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname == github_host and not parsed.username


def file_mode_is_private(path: Path) -> bool:
    """Return whether a path grants no group or other permission bits.

    Intent
    ------
    Enforce the private evidence-tree permission invariant.

    Rationale
    ---------
    Report contents may include logs and repository metadata.

    Pseudocode
    ----------
    - set mode = `filesystem mode`
    - return whether group and other bits are clear

    Wraps
    -----
    - none
    """
    if os.name == "nt":
        return not bool(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def require_sibling_destination(snapshot_root: Path, report_root: Path, expected_name: str) -> None:
    """Require a report destination to be a named sibling of its snapshot.

    Intent
    ------
    Constrain reporter writes to the analysis bundle selected by the caller.

    Rationale
    ---------
    Fixed sibling names prevent a report argument from expanding write scope.

    InstantiationsFromRepo
    ----------------------
      .HistoryError:
        why:
          raises: "Classifies report destinations outside the required sibling boundary."

    Pseudocode
    ----------
    - set operation = `resolve existing snapshot root`
    - set operation = `compare report basename and resolved parent`
    - set operation = `reject mismatched destination`

    Wraps
    -----
    - none
    """
    snapshot = Path(snapshot_root).resolve(strict=True)
    report = Path(report_root)
    if report.name != expected_name or report.parent.resolve(strict=True) != snapshot.parent:
        raise HistoryError("invalid_report_destination", "report destination must be the named sibling of the snapshot")


def load_json_object(members: dict[str, bytes], name: str) -> dict[str, Any]:
    """Decode one required schema-versioned JSON object from members.

    Intent
    ------
    Return a validated structured snapshot member by exact name.

    Rationale
    ---------
    Report builders should reject missing, malformed, or incompatible inputs uniformly.

    InstantiationsFromRepo
    ----------------------
      .HistoryError:
        why:
          raises: "Classifies unavailable or schema-incompatible JSON members."

    Pseudocode
    ----------
    - set operation = `locate and decode named UTF-8 JSON member`
    - set operation = `require object shape and supported schema version`
    - return decoded object

    Wraps
    -----
    - none
    """
    try:
        value = json.loads(members[name].decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise HistoryError("invalid_snapshot", f"required JSON member is unavailable: {name}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise HistoryError("invalid_snapshot", f"required JSON member has an invalid schema: {name}")
    return value
