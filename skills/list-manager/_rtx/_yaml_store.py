#!/usr/bin/env python3
"""list-manager: pure local YAML file operator.

Subcommands:
  describe-schema  <schema> [field]
  init             <file> --schema <name> [--name <list-name>]
  read             <file> [key=value | key~=value ...]
  create-entry     <file> <target> [--entries <file>]
  update           <file> [--file <file>]
  delete           <file> <id> [<id>...]
  gen-id           <file> [--count <n>]

Every subcommand except describe-schema also accepts --cloud, treating the
`file` positional as a cloud list name instead of a local path (see main()).
"""

import argparse
import contextlib
import datetime
import os
import re
import shutil
import sys
import tempfile
import time
import warnings
from pathlib import Path

import yaml

from officina.runtime.python_machine_interface import PythonArgvMachineInterface, PythonMachineInterface

try:
    if __package__:
        from . import _cloud_transport as cloud_transport
    else:
        import _rtx._cloud_transport as cloud_transport
    if __package__:
        from . import _get_schema as get_schema
    else:
        import _rtx._get_schema as get_schema
except ModuleNotFoundError:
    import _cloud_transport as cloud_transport
    import _get_schema as get_schema

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

if HAS_JSONSCHEMA:
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="jsonschema")

IMMUTABLE_FIELDS = frozenset({"id", "created"})
HEX6_RE = re.compile(r"^[0-9a-f]{6}$")


# ── I/O helpers ──────────────────────────────────────────────────────────────

def normalize_dates(node) -> None:
    """Coerce any date/datetime values to ISO strings, in place, recursively.

    YAML parses an unquoted `deadline: 2026-07-05` into a datetime.date, which
    then fails the schema's `type: string, format: date`. Normalizing on load
    and before validation makes the store robust to writers that emit unquoted
    dates, without changing the schema.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, datetime.date):  # also matches datetime.datetime
                node[k] = v.isoformat()
            else:
                normalize_dates(v)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, datetime.date):
                node[i] = v.isoformat()
            else:
                normalize_dates(v)


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    normalize_dates(data)
    return data


def save_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ── Optimistic concurrency + mutual exclusion (feedback items 24/25) ─────────
#
# Design: two processes (e.g. email-triage running concurrently with a user's
# manual edit, or two triage runs overlapping) can each load_yaml() the same
# file, mutate their own in-memory copy, and save_yaml() back -- the second
# write silently clobbers the first's changes with no detection.
#
# Two parts close this, and BOTH are required -- a revision check alone is
# check-then-write with no lock, which only narrows the race window (to the
# time spent parsing/validating between the check and the write) without
# closing it: two processes that both load+check before either reaches the
# write would both pass the check and both write, the second still silently
# clobbering the first.
#
#  1. `revision`: an integer counter on every list document (missing == 0, so
#     every existing list file on disk today is compatible without
#     migration). Every successful mutating save increments it by exactly one
#     (save_with_revision_bump, below). A caller that wants the safety check
#     reads the list first, notes `revision`, and passes it back via
#     --expected-revision; if the file's revision has moved on since, the
#     mutation is rejected before anything is written.
#  2. `file_lock`: an exclusive advisory lock on a `<file>.lock` sidecar, held
#     for the ENTIRE load -> check_revision -> mutate -> save sequence in
#     cmd_create_entry/cmd_update/cmd_delete (see their bodies). This is what
#     actually serializes two racing processes: the second one's load_yaml()
#     cannot even start until the first has released the lock (i.e. finished
#     saving), so the second's check always sees the first's write and is
#     correctly rejected -- there is no window left for both to pass the
#     check. The revision field is what makes the now-guaranteed-correct
#     ordering *detectable and rejectable*, rather than just serialized-and-
#     silently-overwriting like an unguarded lock alone would still be.
#
# This deliberately does NOT introduce a new generic multi-target "apply
# batch" entrypoint: create-entry already accepts a *list* of entries for one
# target in a single download/mutate/upload (see cmd_create_entry), and
# update/delete already accept a list of ids/patches in one call. The actual
# gap closed here is the missing staleness check + mutual exclusion shared by
# all three mutating commands -- adding another batch-apply surface would
# duplicate that existing batching rather than fix the race.
#
# Passing --expected-revision is optional everywhere: existing callers that
# don't know about revisions keep working exactly as before (unconditional
# read-modify-write, same as pre-existing behavior) -- except that they too
# now serialize against other lock-holders, since the lock is unconditional
# and taken regardless of whether --expected-revision was passed. Making
# --expected-revision itself mandatory would break every caller that
# predates this feature for no safety gain on local, single-writer usage;
# the rejection only matters where two writers can race, and those callers
# opt in by passing it.
#
# Note on scope: this local file_lock() only protects the *local* file path
# each command operates on. In --cloud mode, that path is a fresh per-
# invocation tempfile.mkdtemp() path (see main()), so file_lock() ALONE
# serializes nothing across two independent --cloud processes -- each gets
# its own unique lock sidecar that no other process will ever contend for.
# Two overlapping --cloud invocations would both download the same cloud
# revision, both pass check_revision, and the second upload would silently
# clobber the first's write -- the exact race this module exists to close,
# still open for cloud mode's real usage (see cloud_lock_path() and its use
# in main(), below, which closes it).
class StaleRevisionError(RuntimeError):
    """Raised when --expected-revision no longer matches the file's current
    revision: someone else saved this list since the caller last read it."""

    def __init__(self, file: Path, expected: int, actual: int):
        self.file = file
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"stale revision: expected {expected}, but {file} is at revision {actual}. "
            f"Someone else modified this list since you read it -- re-read {file} and retry your change."
        )


def check_revision(data: dict, expected: int | None, file: Path) -> None:
    """No-op when expected is None: the check is opt-in (see module note above)."""
    if expected is None:
        return
    actual = data.get("revision", 0)
    if actual != expected:
        raise StaleRevisionError(file, expected, actual)


def save_with_revision_bump(path: Path, data: dict) -> None:
    """The single choke point every mutating command (create-entry, update,
    delete) goes through to validate, bump `revision`, and write once."""
    data["revision"] = data.get("revision", 0) + 1
    validate_list(data)
    save_yaml(path, data)


_DEFAULT_LOCK_TIMEOUT_S = 30.0
_LOCK_POLL_INTERVAL_S = 0.05


def _lock_timeout_s() -> float:
    """Bounded-wait deadline for file_lock() acquisition. A crashed writer
    isn't the risk -- the OS releases flock/msvcrt locks automatically on
    process exit -- but a HUNG-but-alive writer (stuck network call,
    deadlock) must not make every later local invocation on this file stall
    silently forever, which is exactly the wrong failure mode for an
    unattended caller like email-triage. 30s is meant to catch genuinely
    stuck processes, not add friction to normal fast operations.

    LIST_MANAGER_TEST_LOCK_TIMEOUT_S overrides it for tests that need to
    exercise the timeout path in well under a second rather than waiting out
    the real default.
    """
    override = os.environ.get("LIST_MANAGER_TEST_LOCK_TIMEOUT_S")
    return float(override) if override else _DEFAULT_LOCK_TIMEOUT_S


@contextlib.contextmanager
def file_lock(path: Path):
    """Cross-platform exclusive advisory lock, held for the full
    load -> check_revision -> mutate -> save sequence so two racing
    processes are genuinely serialized rather than merely optimistically
    checked (see the module note above for why the revision check alone is
    not sufficient).

    Uses a `<file>.lock` sidecar as the lock handle rather than locking
    `path` itself, since `path` is fully replaced (not written in place) by
    save_yaml -- locking a path across a replace is unreliable. Advisory
    only: it serializes cooperating callers that go through this same
    function (every mutating list-manager subcommand does); it does not
    prevent a process that ignores locking entirely from writing the file.

    Acquisition is a bounded-retry loop (non-blocking lock attempt, sleep,
    repeat, until a deadline) rather than one blocking call on either
    platform: a plain blocking fcntl.flock has no built-in timeout, and a
    stuck-but-alive lock holder must not wedge every later invocation on
    this file forever -- see _lock_timeout_s(). On timeout, dies with a
    message naming the sidecar and suggesting manual recovery if no process
    is actually still running.

    os.name == "posix": fcntl.flock(LOCK_EX | LOCK_NB) -- the same
    primitive officina.common.atomic_files uses to serialize its own
    compare-and-append writers (see _posix_atomic_append_bytes). That
    module's public API is purpose-built for confined-root, restrictive-ACL
    certificate-log operations (secure_open, ACL verification, native NT-ABI
    calls) and isn't a structural fit for list-manager's arbitrary
    user-supplied file paths, so this mirrors its per-os.name split directly
    rather than reusing it.

    os.name == "nt": msvcrt.locking(LK_NBLCK) on the sidecar file (the
    stdlib cross-platform equivalent; atomic_files.py's nt-branch lock uses
    the lower-level LockFileEx/UnlockFileEx pair via ctypes for its own
    confined-handle pipeline, which isn't reachable without that pipeline).
    """
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.monotonic() + _lock_timeout_s()
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        die(
                            f"could not acquire lock on {lock_path} after "
                            f"{_lock_timeout_s():.0f}s -- another process may be stuck; "
                            f"if none is actually running, delete the stale lock file and retry."
                        )
                    time.sleep(_LOCK_POLL_INTERVAL_S)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        die(
                            f"could not acquire lock on {lock_path} after "
                            f"{_lock_timeout_s():.0f}s -- another process may be stuck; "
                            f"if none is actually running, delete the stale lock file and retry."
                        )
                    time.sleep(_LOCK_POLL_INTERVAL_S)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _cloud_lock_dir(*, home: Path | None = None) -> Path:
    """Stable, well-known directory for cloud-mode lock sidecars -- as
    opposed to file_lock()'s per-command sidecar next to the file it locks,
    which for --cloud mode is a fresh tempfile.mkdtemp() path every
    invocation and therefore useless as a lock (see cloud_lock_path()).

    LIST_MANAGER_CLOUD_LOCK_DIR overrides it for tests, mirroring the
    EMAIL_TRIAGE_STATE_DIR override pattern used by
    email-triage/_rtx/_failure_sentinel.py for the same reason: tests need a
    tmp_path, not the real shared state root.
    """
    override = os.environ.get("LIST_MANAGER_CLOUD_LOCK_DIR")
    if override:
        return Path(override)
    from officina.common.famulus_paths import resolve_famulus_paths

    return (
        resolve_famulus_paths(
            platform=sys.platform, home=home or Path.home(), environ=os.environ
        ).state_root
        / "list-manager"
        / "locks"
    )


_SAFE_LOCK_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def cloud_lock_path(list_name: str) -> Path:
    """Resolve a STABLE lock target for a cloud list name, keyed by the name
    itself rather than by any per-invocation local path.

    This is what actually closes the cloud-mode race: unlike file_lock()
    applied to lists.py's per-invocation temp file (unique every time, so it
    serializes nothing), every --cloud invocation for the same list name
    resolves to the same lock sidecar here, in the same well-known directory
    -- so file_lock(cloud_lock_path(name)), held for the whole
    download -> mutate -> upload sequence in main(), genuinely serializes two
    concurrent --cloud processes on the same machine the same way the
    existing per-command lock serializes two local-file processes.

    This only coordinates writers that share a local filesystem (i.e. the
    same machine) -- it cannot serialize truly independent machines writing
    to the same Drive-backed list with no shared local state, since there is
    no cloud-native conditional-write primitive plumbed through
    _cloud_transport.py's upload_list() to use instead (Drive's v3 files.update
    has no documented If-Match/etag-conditional semantics that this codebase
    exposes). Same-machine concurrent invocations (e.g. two triage runs, or a
    triage run racing a manual edit) are the realistic common case this
    closes.
    """
    safe_name = _SAFE_LOCK_NAME_RE.sub("_", list_name) or "_"
    lock_dir = _cloud_lock_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    # ".yaml" suffix purely so file_lock()'s "<file>.lock" sidecar naming
    # reads the same way it does for local-file locks; no such file is ever
    # created or read here, only its ".lock" sidecar.
    return lock_dir / f"{safe_name}.yaml"


def _test_race_delay() -> None:
    """Test-only hook: sleeping here -- inside the lock, after check_revision
    has passed, before the mutation and save -- lets a test deterministically
    hold a writer inside the exact check-to-write gap that a lock (rather
    than a bare revision check) is required to close. No-op unless
    LIST_MANAGER_TEST_RACE_DELAY is set. When
    LIST_MANAGER_TEST_RACE_READY_FILE is also set, touch that path before the
    delay so the test can observe that the writer owns the lock instead of
    guessing from process-startup timing. Both controls are used only by
    test_update_concurrent_writers_are_serialized_by_the_lock.
    """
    ready_file = os.environ.get("LIST_MANAGER_TEST_RACE_READY_FILE")
    if ready_file:
        Path(ready_file).touch()
    delay = os.environ.get("LIST_MANAGER_TEST_RACE_DELAY")
    if delay:
        time.sleep(float(delay))


# ── Cloud transport ───────────────────────────────────────────────────────────
# See cloud_transport.py -- shared with read_beautify.py so there's exactly
# one implementation of "talk to cloud-files' lists-read/lists-write".

def download_list(list_name: str, dest_path: Path) -> None:
    try:
        cloud_transport.download_list(list_name, dest_path)
    except cloud_transport.CloudTransportError as exc:
        die(str(exc))


def upload_list(list_name: str, src_path: Path) -> None:
    try:
        cloud_transport.upload_list(list_name, src_path)
    except cloud_transport.CloudTransportError as exc:
        die(str(exc))


# ── ID generation ─────────────────────────────────────────────────────────────

def collect_ids(node) -> set[str]:
    """Recursively collect all entry IDs from a list document."""
    ids: set[str] = set()
    if isinstance(node, dict):
        if "id" in node:
            ids.add(node["id"])
        for v in node.values():
            ids |= collect_ids(v)
    elif isinstance(node, list):
        for item in node:
            ids |= collect_ids(item)
    return ids


def gen_ids(existing_ids: set[str], count: int = 1) -> list[str]:
    """Return `count` collision-free 6-char lowercase hex IDs."""
    ids: list[str] = []
    while len(ids) < count:
        candidate = os.urandom(3).hex()
        if candidate not in existing_ids and candidate not in ids:
            ids.append(candidate)
    return ids


# ── Validation ───────────────────────────────────────────────────────────────

_AUTO_GENERATED_FIELDS = {"id", "created", "state"}


def validate_entries_before_insert(entries: list, schema_name: str) -> None:
    """Check that each entry has all required fields before insertion.

    This prevents the mistake of inventing missing required fields. If any entry
    is missing a required field, fail loudly so the caller is forced to ask for
    the value instead of guessing.

    Auto-generated fields (id, created, state) are not required in the input.
    """
    if not HAS_JSONSCHEMA:
        return  # Skip if jsonschema not available; full validation will happen later

    if not get_schema.list_schema_exists(schema_name):
        return  # Schema unknown; let full validation handle it

    whole = get_schema.get_schema(schema_name, "*")
    user_required = set(whole["required"]) - _AUTO_GENERATED_FIELDS

    # Check each entry for missing user-provided required fields
    for entry in entries:
        if not isinstance(entry, dict):
            continue  # Let full validation handle type errors

        missing = user_required - set(entry.keys())
        if missing:
            title = entry.get("title", "(no title)")
            die(
                f"entry '{title}' is missing required field(s): {', '.join(sorted(missing))}. "
                f"Provide {missing.pop() if len(missing) == 1 else 'these fields'} instead of inventing them."
            )


def validate_list(data: dict) -> None:
    """Validate data against its declared schema. Calls die() on failure."""
    # Patch inputs (create-entry/update) may carry date objects from YAML; coerce
    # them so what we validate matches what we save.
    normalize_dates(data)
    schema_name = data.get("schema")
    if not schema_name:
        die("list file missing 'schema' field")

    if not get_schema.list_schema_exists(schema_name):
        die(f"unknown schema '{schema_name}' (no file at {get_schema.list_schema_path(schema_name)})")

    if not HAS_JSONSCHEMA:
        print(
            "warning: jsonschema is not installed — schema validation skipped. "
            "Install it (`pip install jsonschema`) to validate entries before saving.",
            file=sys.stderr,
        )
        die("cannot write: jsonschema is required for mutating operations but is not installed")

    try:
        get_schema.validate_document(data, schema_name)
    except jsonschema.ValidationError as e:
        die(f"validation failed: {describe_validation_error(data, e)}")


def describe_validation_error(data: dict, err) -> str:
    """Turn a jsonschema error into an actionable message: the specific problem,
    the location, and the offending entry's id/title when there is one."""
    path = list(err.absolute_path)
    # Walk the document along the error path, remembering the nearest enclosing
    # entry (a dict with id + title) so we can name the row that is wrong.
    node, entry = data, None
    for key in path:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            break
        if isinstance(node, dict) and "id" in node and "title" in node:
            entry = node
    loc = "/".join(str(p) for p in path) or "(document root)"
    where = f"\n  at: {loc}"
    who = ""
    if entry is not None:
        who = f"\n  entry: id={entry.get('id')} title={entry.get('title')!r}"
    # `err.message` already names the field for required/type/format failures.
    return f"{err.message}{where}{who}"


# ── Filter helpers (for `read`) ───────────────────────────────────────────────

def parse_filters(filter_args: list[str]) -> list[tuple[str, str, str]]:
    """Parse filter strings into (key, op, value) tuples.

    Supported ops:
      key=value    exact match (comma-separated = OR)
      key~=value   regex search on the field (case-insensitive; substring is a
                   plain-text regex, so old substring filters keep working)
    """
    filters = []
    for f in filter_args:
        m = re.match(r"^([^~=]+)(~=|=)(.+)$", f)
        if not m:
            die(f"invalid filter '{f}': expected key=value or key~=value")
        filters.append((m.group(1), m.group(2), m.group(3)))
    return filters


def validate_filter_values(filters: list[tuple[str, str, str]], schema_name: str) -> None:
    """Reject exact-match (`=`) filters whose value isn't a valid enum member
    for that field, instead of silently matching zero entries.

    Only applies to `=` filters on fields with a known enum (currently just
    `state`, per schema). `~=` (regex) filters are intentionally exempt since
    partial/pattern matches aren't a fixed-value comparison.
    """
    for key, op, val in filters:
        if op != "=":
            continue
        spec = get_schema.get_schema(schema_name, key)
        if not isinstance(spec, dict) or "enum" not in spec:
            continue
        allowed = spec["enum"]
        values = [v.strip() for v in val.split(",")]
        bad = [v for v in values if v not in allowed]
        if bad:
            die(
                f"invalid value(s) for filter '{key}': {', '.join(bad)}. "
                f"Valid values are: {', '.join(allowed)}."
            )


def entry_matches(entry: dict, filters: list[tuple[str, str, str]]) -> bool:
    """Return True if entry satisfies all filters (AND across keys, OR within a key)."""
    from collections import defaultdict
    by_key: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, op, val in filters:
        by_key[key].append((op, val))

    for key, conditions in by_key.items():
        field_val = str(entry.get(key, ""))
        matched_any = False
        for op, val in conditions:
            if op == "=":
                if field_val in [v.strip() for v in val.split(",")]:
                    matched_any = True
                    break
            elif op == "~=":
                # Regex search; fall back to literal substring on a bad pattern
                # so filters containing regex metacharacters never crash.
                try:
                    if re.search(val, field_val, re.IGNORECASE):
                        matched_any = True
                        break
                except re.error:
                    if val in field_val:
                        matched_any = True
                        break
        if not matched_any:
            return False
    return True


def _prune_entry(entry: dict, filters: list[tuple[str, str, str]]) -> dict | None:
    """Prune one entry (and its children) to only what matches or has a matching
    descendant. Returns None if neither the entry nor any descendant matches.

    A matching entry is never duplicated: it appears exactly once, in place,
    with its children pruned to just the matching branches -- an ancestor of a
    match is always included (so context is never lost), but a match is not
    also promoted to a separate top-level result.
    """
    children = entry.get("children", [])
    pruned_children = [c for c in (_prune_entry(ch, filters) for ch in children) if c is not None]
    self_matches = entry_matches(entry, filters)
    if not self_matches and not pruned_children:
        return None
    new_entry = dict(entry)
    if "children" in entry:
        new_entry["children"] = pruned_children
    return new_entry


def _prune_category(cat: dict, filters: list[tuple[str, str, str]]) -> dict | None:
    """Prune one category (and its subcategories) to only branches containing a
    match. Returns None if the category has no matching entry anywhere beneath it.
    """
    pruned_entries = [e for e in (_prune_entry(en, filters) for en in cat.get("entries", [])) if e is not None]
    pruned_subs = [s for s in (_prune_category(sc, filters) for sc in cat.get("categories", [])) if s is not None]
    if not pruned_entries and not pruned_subs:
        return None
    new_cat = dict(cat)
    if "entries" in cat:
        new_cat["entries"] = pruned_entries
    if "categories" in cat:
        new_cat["categories"] = pruned_subs
    return new_cat


def _entry_sort_key(entry: dict, sort_field: str):
    if sort_field not in entry:
        return (1, None)  # missing values sort last without cross-type comparison
    return (0, entry[sort_field])


def _sort_tree(node, sort_field: str) -> None:
    """Sort every entries/children list found anywhere in a (possibly nested)
    filtered-read result, in place, so sorting still works now that matches
    can be nested rather than a single flat list."""
    if isinstance(node, dict):
        if "entries" in node:
            node["entries"].sort(key=lambda e: _entry_sort_key(e, sort_field))
            for e in node["entries"]:
                _sort_tree(e, sort_field)
        if "categories" in node:
            for c in node["categories"]:
                _sort_tree(c, sort_field)
        if "children" in node:
            node["children"].sort(key=lambda e: _entry_sort_key(e, sort_field))
            for c in node["children"]:
                _sort_tree(c, sort_field)
    elif isinstance(node, list):
        node.sort(key=lambda e: _entry_sort_key(e, sort_field))
        for item in node:
            _sort_tree(item, sort_field)


def collect_matching_entries(data, filters: list[tuple[str, str, str]]):
    """Filter a list document down to only what matches, preserving structure:
    every ancestor (category and/or parent entry) of a match is kept so a
    match is never returned without its context, and a match is never
    duplicated as both a nested child and an independent top-level result.

    - Full document (dict with 'categories'): returns the same dict shape,
      pruned to only categories/subcategories/entries containing a match.
    - Bare entry list (e.g. already-filtered input): returns a pruned list.
    """
    if isinstance(data, dict) and "categories" in data:
        pruned_cats = [c for c in (_prune_category(cat, filters) for cat in data.get("categories", [])) if c is not None]
        result = dict(data)
        result["categories"] = pruned_cats
        return result
    elif isinstance(data, list):
        return [e for e in (_prune_entry(en, filters) for en in data) if e is not None]
    return data


# ── Category / entry lookup helpers ──────────────────────────────────────────

def find_category_by_path(categories: list[dict], path_parts: list[str]) -> dict | None:
    """Navigate nested categories by name path. Returns the category dict or None."""
    if not path_parts:
        return None
    name = path_parts[0]
    for cat in categories:
        if cat.get("name") == name:
            if len(path_parts) == 1:
                return cat
            return find_category_by_path(cat.get("categories", []), path_parts[1:])
    return None


def all_category_paths(categories: list[dict], prefix: str = "") -> list[str]:
    """Return all category paths for error messages."""
    paths = []
    for cat in categories:
        path = f"{prefix}/{cat['name']}" if prefix else cat["name"]
        paths.append(path)
        paths.extend(all_category_paths(cat.get("categories", []), path))
    return paths


def find_entry_by_id(node, target_id: str) -> dict | None:
    """Recursively find an entry by ID."""
    if isinstance(node, dict):
        if node.get("id") == target_id:
            return node
        for v in node.values():
            result = find_entry_by_id(v, target_id)
            if result is not None:
                return result
    elif isinstance(node, list):
        for item in node:
            result = find_entry_by_id(item, target_id)
            if result is not None:
                return result
    return None


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_describe_schema(args: argparse.Namespace) -> None:
    """Answer "what fields/values does this schema allow" without reading raw
    JSON Schema. Purely local and read-only -- no --cloud, no file/list arg.
    """
    if not get_schema.list_schema_exists(args.schema):
        die(f"unknown schema '{args.schema}' (no file at {get_schema.list_schema_path(args.schema)})")

    if args.field == "*":
        whole = get_schema.get_schema(args.schema, "*")
        required = set(whole["required"]) - _AUTO_GENERATED_FIELDS
        out = {
            "entry_fields": whole["properties"],
            "required_fields": sorted(required),
            "auto_generated_fields": sorted(_AUTO_GENERATED_FIELDS & set(whole["required"])),
        }
    else:
        spec = get_schema.get_schema(args.schema, args.field)
        if spec is None:
            die(
                f"field '{args.field}' is not defined for schema '{args.schema}' "
                f"(run with field '*' to see all fields)"
            )
        out = {args.field: spec}

    print(yaml.dump(out, allow_unicode=True, default_flow_style=False, sort_keys=False), end="")


def _domain_category(name: str, personal: bool) -> dict:
    """Build one domain category, populated with the fixed subcategory set
    todo/triage schemas require (task-list.json / task-list-personal.json),
    resolved through get_schema so this stays in sync with the schema files
    instead of duplicating their enum here.
    """
    sub_names = get_schema.domain_subcategory_names(personal)
    return {"name": name, "categories": [{"name": n} for n in sub_names]}


def default_categories(schema: str) -> list[dict]:
    """Usable starting categories for a freshly initialized list.

    Fixes feedback item 23: an unconditional `categories: []` left every new
    list unusable until the caller manually built out the schema's required
    category structure. todo/triage lists need at least one domain category,
    and that domain category must carry the schema's fixed subcategory set
    (see task-list.json / task-list-personal.json), so a bare `[{"name":
    "Personal"}]` would itself fail validation -- the seed must be fully
    populated. Schemas without a fixed category vocabulary (e.g. "default")
    have no meaningful default, so they keep an empty list.

    "Personal" and "Work" are the two seed domain names: "Personal" is not
    arbitrary -- todo.json/triage.json route on the literal name "Personal"
    to require the 7-subcategory (incl. "Shop") variant, so it must be spelled
    exactly that way to exercise it. "Work" has no schema significance (any
    other name would validate identically); it's just a second, common-sense
    domain so a fresh list isn't limited to a single bucket.
    """
    if schema in ("todo", "triage"):
        return [
            _domain_category("Personal", personal=True),
            _domain_category("Work", personal=False),
        ]
    return []


def cmd_init(args: argparse.Namespace) -> None:
    file = Path(args.file)
    if file.exists():
        die(f"file already exists: {file}")

    name = args.name if hasattr(args, "name") and args.name else file.stem
    data: dict = {
        "schema": args.schema,
        "name": name,
        "categories": default_categories(args.schema),
    }

    validate_list(data)
    save_yaml(file, data)
    print(f"created {file}")


def cmd_gen_id(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)
    existing = collect_ids(data)
    ids = gen_ids(existing, args.count)
    for id_ in ids:
        print(id_)


def cmd_read(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)

    def emit(content: str) -> None:
        if getattr(args, "output", None):
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(content)
        else:
            print(content, end="")

    if args.filters:
        filters = parse_filters(args.filters)
        validate_filter_values(filters, data.get("schema", ""))
        matches = collect_matching_entries(data, filters)
    else:
        matches = data

    # Sort if requested. Sorting recurses into every entries/children list in
    # the (possibly nested, ancestor-preserving) result, since a match may now
    # live several levels deep rather than in one flat list.
    if hasattr(args, 'sort') and args.sort:
        sort_field = args.sort
        try:
            _sort_tree(matches, sort_field)
        except (TypeError, ValueError) as ex:
            die(f"sort by '{sort_field}' failed: {ex}")

    emit(yaml.dump(matches, allow_unicode=True, default_flow_style=False, sort_keys=False))


def cmd_create_entry(args: argparse.Namespace) -> None:
    file = Path(args.file)
    # The lock spans the whole load -> check -> mutate -> save sequence (not
    # just the check) -- see the module note above check_revision() for why
    # a bare check-then-write is insufficient to close the race.
    with file_lock(file):
        data = load_yaml(file)
        check_revision(data, getattr(args, "expected_revision", None), file)
        _test_race_delay()
        target = args.target

        if HEX6_RE.match(target):
            parent_entry = find_entry_by_id(data, target)
            if parent_entry is None:
                die(f"no entry with id '{target}' found in {file}")
            dest_list = parent_entry.setdefault("children", [])
        else:
            parts = [p.strip() for p in target.split("/") if p.strip()]
            category = find_category_by_path(data.get("categories", []), parts)
            if category is None:
                available = all_category_paths(data.get("categories", []))
                die(
                    f"category '{target}' not found. Available: "
                    + (", ".join(available) if available else "(none)")
                )
            dest_list = category.setdefault("entries", [])

        if args.entries:
            with open(args.entries, encoding="utf-8") as f:
                new_entries = yaml.safe_load(f)
        else:
            new_entries = yaml.safe_load(sys.stdin.read())

        if not isinstance(new_entries, list):
            die("entries input must be a YAML list")

        # Validate required fields before adding to list. This fails fast and forces
        # the caller to ask for missing values instead of inventing them.
        schema_name = data.get("schema")
        validate_entries_before_insert(new_entries, schema_name)

        existing_ids = collect_ids(data)
        today = datetime.date.today().isoformat()
        for entry in new_entries:
            if "id" not in entry:
                new_id = gen_ids(existing_ids, 1)[0]
                entry["id"] = new_id
                existing_ids.add(new_id)
            # Default state and created so callers (e.g. email-triage) don't need
            # to supply them; these are only required by todo/triage schemas but
            # are harmless on others.
            if "state" not in entry:
                # Use schema-aware defaults: triage uses "undecided", todo uses
                # "incomplete".
                entry["state"] = "undecided" if schema_name == "triage" else "incomplete"
            if "created" not in entry:
                entry["created"] = today

        dest_list.extend(new_entries)
        save_with_revision_bump(file, data)


# States that mean "this entry is finished" across both todo and triage schemas.
FINISHED_STATES = frozenset({"complete", "accepted", "rejected"})


def cmd_update(args: argparse.Namespace) -> None:
    file = Path(args.file)
    with file_lock(file):
        data = load_yaml(file)
        check_revision(data, getattr(args, "expected_revision", None), file)
        _test_race_delay()

        if args.file_input:
            with open(args.file_input, encoding="utf-8") as f:
                updates = yaml.safe_load(f)
        else:
            updates = yaml.safe_load(sys.stdin.read())

        if not isinstance(updates, list):
            die("update input must be a YAML list")

        today = datetime.date.today().isoformat()

        for patch in updates:
            if "id" not in patch:
                die("each update must have an 'id' field")

            bad = IMMUTABLE_FIELDS & set(patch.keys()) - {"id"}
            if bad:
                die(f"cannot update immutable field(s): {', '.join(sorted(bad))}")

            target_id = patch["id"]
            entry = find_entry_by_id(data, target_id)
            if entry is None:
                die(f"no entry with id '{target_id}' found in {file}")

            for k, v in patch.items():
                if k == "id":
                    continue
                entry[k] = v

            # `modified`: auto-stamped on every touch (debugging aid; not shown to
            # the user). `completed`: auto-stamped only the first time a patch
            # itself transitions state into a finished value, so later unrelated
            # edits (e.g. a deadline correction) never overwrite the real
            # completion date. Both are skipped if the patch already set them
            # explicitly.
            if "modified" not in patch:
                entry["modified"] = today
            if (
                "completed" not in patch
                and "state" in patch
                and patch["state"] in FINISHED_STATES
                and not entry.get("completed")
            ):
                entry["completed"] = today

        save_with_revision_bump(file, data)


# ── Deletion helpers ─────────────────────────────────────────────────────────

def remove_entries_by_ids(node, ids_to_remove: set[str]) -> None:
    """Remove entries with the given IDs from the tree, in place.

    Operates on any list-bearing node: top-level category entries AND nested
    children lists. Removing a parent removes the whole subtree naturally
    (the node is never visited after removal).
    """
    if isinstance(node, dict):
        for val in node.values():
            if isinstance(val, list):
                # Filter out matching entries at this level
                val[:] = [
                    item for item in val
                    if not (isinstance(item, dict) and item.get("id") in ids_to_remove)
                ]
                # Recurse into survivors
                for item in val:
                    remove_entries_by_ids(item, ids_to_remove)
            else:
                remove_entries_by_ids(val, ids_to_remove)
    elif isinstance(node, list):
        node[:] = [
            item for item in node
            if not (isinstance(item, dict) and item.get("id") in ids_to_remove)
        ]
        for item in node:
            remove_entries_by_ids(item, ids_to_remove)


def cmd_delete(args: argparse.Namespace) -> None:
    file = Path(args.file)
    with file_lock(file):
        data = load_yaml(file)
        check_revision(data, getattr(args, "expected_revision", None), file)
        _test_race_delay()

        ids_to_delete = set(args.ids)

        # Detect missing ids before touching data
        all_ids = collect_ids(data)
        missing = ids_to_delete - all_ids
        if missing:
            for mid in sorted(missing):
                print(f"error: id '{mid}' not found", file=sys.stderr)
            sys.exit(1)

        remove_entries_by_ids(data, ids_to_delete)
        save_with_revision_bump(file, data)

    for id_ in sorted(ids_to_delete):
        print(f"deleted: {id_}")


# ── Argument parsing + dispatch ───────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lists.py")
    sub = parser.add_subparsers(dest="command", required=True)

    # Helper to add --cloud to any subcommand. When set, the source positional
    # is treated as a cloud list NAME (download → operate → upload) instead of a
    # local file PATH. It is a plain boolean, so it never consumes a positional
    # and filters keep their own slot.
    def add_cloud_arg(subparser):
        subparser.add_argument(
            "--cloud",
            action="store_true",
            help="Treat the source as a cloud list name; download, operate, and upload",
        )

    # Optimistic-concurrency guard (feedback items 24/25): optional on every
    # mutating subcommand, opt-in for backward compat. See the module note
    # above check_revision()/StaleRevisionError for the full rationale.
    def add_expected_revision_arg(subparser):
        subparser.add_argument(
            "--expected-revision",
            dest="expected_revision",
            type=int,
            default=None,
            help=(
                "Only apply if the list's current 'revision' field equals this value "
                "(read it from a prior `read`); rejects with a stale-revision error "
                "otherwise instead of silently overwriting a concurrent change. "
                "Omit to skip the check (default; matches pre-existing behavior)."
            ),
        )

    p_describe = sub.add_parser(
        "describe-schema",
        help="Describe entry-level fields (types/required/enums) for a list schema",
    )
    build_describe_schema_parser(p_describe)

    p_init = sub.add_parser("init", help="Create a new empty list file")
    p_init.add_argument("file", help="Path to create, or cloud list name with --cloud")
    p_init.add_argument("--schema", required=True, help="Schema name (todo, triage, default)")
    p_init.add_argument("--name", help="List name (defaults to filename stem)")
    add_cloud_arg(p_init)

    p_read = sub.add_parser("read", help="Read list, optionally filtered")
    p_read.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_read.add_argument("filters", nargs="*", help="key=value (exact/OR) or key~=value (regex) filters")
    p_read.add_argument("--sort", metavar="FIELD", help="Sort results by field (e.g., deadline, created). Dates sorted ascending (earliest first)")
    p_read.add_argument("-o", "--output", metavar="FILE", help="Write output to file instead of stdout")
    add_cloud_arg(p_read)

    p_create = sub.add_parser("create-entry", help="Add entries to a category or entry")
    p_create.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_create.add_argument("target", help="Category path (Work/Writing) or 6-char entry ID")
    p_create.add_argument("--entries", dest="entries", help="YAML file of entries (default: stdin)")
    add_cloud_arg(p_create)
    add_expected_revision_arg(p_create)

    p_update = sub.add_parser("update", help="Update fields on entries")
    p_update.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_update.add_argument("--file", dest="file_input", help="YAML file of updates (default: stdin)")
    add_cloud_arg(p_update)
    add_expected_revision_arg(p_update)

    p_genid = sub.add_parser("gen-id", help="Generate collision-free IDs")
    p_genid.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_genid.add_argument("--count", type=int, default=1, help="Number of IDs to generate")
    add_cloud_arg(p_genid)

    p_delete = sub.add_parser("delete", help="Delete entries by ID (removes whole subtree)")
    p_delete.add_argument("file", help="Path to list YAML, or cloud list name with --cloud")
    p_delete.add_argument("ids", nargs="+", help="One or more 6-char entry IDs to delete")
    add_cloud_arg(p_delete)
    add_expected_revision_arg(p_delete)

    return parser


def build_describe_schema_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("schema", help="Schema name (todo, triage, default)")
    parser.add_argument(
        "field",
        nargs="?",
        default="*",
        help="Field name to describe, or omit / pass '*' for all fields",
    )
    return parser


class DescribeSchemaInterface(PythonMachineInterface):
    prog = "lists.py describe-schema"

    def build_parser(self) -> argparse.ArgumentParser:
        return build_describe_schema_parser(super().build_parser())

    def run(self, args: argparse.Namespace) -> int:
        cmd_describe_schema(args)
        return 0


class Interface(PythonArgvMachineInterface):
    dispatches = cloud_transport.DISPATCHES
    prog = "lists.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "init": cmd_init,
        "read": cmd_read,
        "create-entry": cmd_create_entry,
        "update": cmd_update,
        "delete": cmd_delete,
        "gen-id": cmd_gen_id,
        "describe-schema": cmd_describe_schema,
    }

    # Cloud mode: the source positional is a list NAME. For reads we download →
    # operate; for mutations we download → operate → upload; for init we create
    # → upload (nothing to download). Local mode operates on the file in place.
    #
    # Mutating cloud commands are wrapped in file_lock(cloud_lock_path(name)),
    # held for the ENTIRE download -> dispatch -> upload sequence -- the cloud
    # analogue of cmd_create_entry/cmd_update/cmd_delete's own file_lock(file)
    # around their load -> check -> mutate -> save. Without this, each
    # invocation's per-command lock is keyed on a fresh tempfile.mkdtemp()
    # path unique to that process, so it serializes nothing across two
    # concurrent --cloud processes; cloud_lock_path() is keyed on the list
    # NAME instead, so two processes targeting the same cloud list genuinely
    # contend for the same sidecar (see cloud_lock_path()'s docstring for the
    # single-machine-only scope of this).
    try:
        if getattr(args, "cloud", False):
            list_name = args.file
            mutating = args.command in ("init", "create-entry", "update", "delete")
            lock_cm = file_lock(cloud_lock_path(list_name)) if mutating else contextlib.nullcontext()
            with lock_cm:
                tmp_dir = Path(tempfile.mkdtemp())
                temp_path = tmp_dir / f"{list_name}.yaml"
                try:
                    if args.command == "init":
                        # New list: nothing to download; default display name to
                        # the cloud list name unless the caller set one explicitly.
                        if not getattr(args, "name", None):
                            args.name = list_name
                    else:
                        download_list(list_name, temp_path)
                    args.file = str(temp_path)
                    dispatch[args.command](args)
                    if mutating:
                        upload_list(list_name, temp_path)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            dispatch[args.command](args)
    except StaleRevisionError as exc:
        # Rejected before any download/upload of the mutated snapshot -- no
        # partial or corrupt write results from a stale-revision rejection.
        die(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
