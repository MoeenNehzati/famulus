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
    """Replace parsed date objects with ISO strings throughout a YAML tree.

    Intent
    ------
    Normalize mapping and sequence values in place before schema validation.

    Rationale
    ---------
    PyYAML converts unquoted dates to ``date`` objects, but list schemas require
    portable string values; recursive normalization accepts those documents
    without weakening the schema.

    Pseudocode
    ----------
    - if node is a mapping:
      - for child in mapping values:
        - if child is a date:
          - set normalized_child = ISO date string
        - else:
          - @normalize_dates(child)
    - else:
      - if node is a sequence:
        - for indexed_child in enumerated sequence:
          - set child = indexed_child value
          - if child is a date:
            - set updated_sequence = child ISO string written at indexed_child index
          - else:
            - @normalize_dates(child)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .normalize_dates:
      why:
        transforms: "Recursively normalizes nested mapping and sequence values."
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
    """Load one YAML document and normalize its parsed date values.

    Intent
    ------
    Return a mapping suitable for list operations from a UTF-8 YAML file.

    Rationale
    ---------
    Centralizing empty-document handling and date normalization keeps every
    read path consistent before filtering, mutation, or validation.

    Pseudocode
    ----------
    - set parsed_document = YAML mapping read from the UTF-8 path
    - @normalize_dates(parsed_document)
    - return parsed_document

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .normalize_dates:
      why:
        transforms: "Converts YAML date objects in the parsed document to schema-compatible strings."
    """
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    normalize_dates(data)
    return data


def save_yaml(path: Path, data: dict) -> None:
    """Serialize one list mapping to a UTF-8 YAML file.

    Intent
    ------
    Persist a mapping with readable block formatting and insertion-order keys.

    Rationale
    ---------
    A single serializer keeps Unicode, flow style, and key ordering identical
    across initialization and mutation paths.

    Pseudocode
    ----------
    - set serialized_document = block YAML preserving Unicode and key order
    - set persisted_file = UTF-8 destination containing serialized_document

    Wraps
    -----
    - none
    """
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def die(msg: str) -> None:
    """Report a caller-facing error and terminate the current command.

    Intent
    ------
    Give command handlers one consistent nonzero failure boundary.

    Rationale
    ---------
    Writing diagnostics to stderr before ``SystemExit(1)`` preserves stdout for
    machine-readable command results.

    Pseudocode
    ----------
    - set diagnostic = error prefix plus caller message
    - raise SystemExit with status one after writing diagnostic to stderr

    Wraps
    -----
    - none
    """
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
    """Represent an optimistic-concurrency mismatch for one list file.

    Intent
    ------
    Carry the file path and both revision values across the mutation boundary.

    Rationale
    ---------
    A distinct exception lets local and cloud orchestration reject stale input
    before writing while rendering one actionable diagnostic at the CLI edge.

    Pseudocode
    ----------
    - set conflict_context = file plus expected and observed revisions
    - set runtime_message = conflict context plus reread and retry guidance

    Wraps
    -----
    - none
    """

    def __init__(self, file: Path, expected: int, actual: int):
        """Initialize a stale-revision error with conflict context.

        Intent
        ------
        Preserve structured revision values and a complete user-facing message.

        Rationale
        ---------
        Callers need both programmatic fields and a diagnostic that names the
        changed file and the safe recovery action.

        Pseudocode
        ----------
        - set stored_file = conflicted list path
        - set stored_revisions = expected and observed revisions
        - set base_error = conflict and retry message

        Wraps
        -----
        - none
        """
        self.file = file
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"stale revision: expected {expected}, but {file} is at revision {actual}. "
            f"Someone else modified this list since you read it -- re-read {file} and retry your change."
        )


def check_revision(data: dict, expected: int | None, file: Path) -> None:
    """Reject a list snapshot that does not match an optional revision guard.

    Intent
    ------
    Enforce opt-in optimistic concurrency before any mutation is applied.

    Rationale
    ---------
    Treating a missing document revision as zero preserves old files, while a
    missing expected revision intentionally retains unconditional writes.

    Pseudocode
    ----------
    - if expected revision is missing:
      - return
    - set actual_revision = document revision or zero
    - if actual_revision differs:
      - raise StaleRevisionError(file, expected, actual_revision)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .StaleRevisionError:
      why:
        raises: "Constructs the structured conflict raised for a stale snapshot."
    """
    if expected is None:
        return
    actual = data.get("revision", 0)
    if actual != expected:
        raise StaleRevisionError(file, expected, actual)


def save_with_revision_bump(path: Path, data: dict) -> None:
    """Advance, validate, and persist one successful list mutation.

    Intent
    ------
    Make revision advancement the common finalization path for mutating commands.

    Rationale
    ---------
    Bumping before validation ensures the exact persisted snapshot, including
    its new revision, satisfies the selected list schema.

    Pseudocode
    ----------
    - set next_revision = current revision plus one
    - @validate_list(document)
    - @save_yaml(path, document)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .validate_list:
      why:
        validates: "Checks the revision-advanced snapshot before persistence."
    .save_yaml:
      why:
        writes: "Persists the validated snapshot exactly once."
    """
    data["revision"] = data.get("revision", 0) + 1
    validate_list(data)
    save_yaml(path, data)


_DEFAULT_LOCK_TIMEOUT_S = 30.0
_LOCK_POLL_INTERVAL_S = 0.05


def _lock_timeout_s() -> float:
    """Resolve the bounded wait used for advisory-lock acquisition.

    Intent
    ------
    Return the production timeout or an explicit test-only override.

    Rationale
    ---------
    Operating systems release locks after crashes, but a hung live process can
    retain one indefinitely; a bounded wait keeps unattended calls from wedging.

    Pseudocode
    ----------
    - set override_seconds = environment timeout override
    - if override_seconds exists:
      - return parsed override_seconds
    - return production timeout

    Wraps
    -----
    - none
    """
    override = os.environ.get("LIST_MANAGER_TEST_LOCK_TIMEOUT_S")
    return float(override) if override else _DEFAULT_LOCK_TIMEOUT_S


@contextlib.contextmanager
def file_lock(path: Path):
    """Hold a bounded cross-platform advisory lock on a file sidecar.

    Intent
    ------
    Serialize each cooperating read-check-mutate-save critical section.

    Rationale
    ---------
    Locking ``<file>.lock`` remains stable when the data file is replaced, and
    nonblocking retries give every supported host the same bounded timeout
    behavior.

    Pseudocode
    ----------
    - set sidecar = stable lock path beside the target
    - timeout_seconds = _lock_timeout_s()
    - set deadline = current monotonic time plus timeout_seconds
    - while the platform lock is unavailable:
      - if deadline has expired:
        - @die(lock recovery diagnostic)
      - set retry_wait = polling interval
    - set critical_section = caller executes while the lock is held
    - set released_lock = platform lock released and descriptor closed

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .die:
      why:
        orchestrates: "Routes acquisition timeout through the shared command failure boundary."

    InstantiationsFromRepo
    ----------------------
    ._lock_timeout_s:
      why:
        constructs: "Constructs the timeout value carried into the deadline and failure diagnostics."
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
    """Resolve the stable directory that coordinates cloud-list writers.

    Intent
    ------
    Select a shared state directory or an isolated test override for lock files.

    Rationale
    ---------
    Per-invocation download directories cannot coordinate cloud writers, so
    their lock sidecars need a machine-stable location outside those snapshots.

    Pseudocode
    ----------
    - if cloud-lock override exists:
      - return override path
    - resolved_paths = resolve_famulus_paths(platform, selected home)
    - return list-manager locks directory beneath resolved_paths state root

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.famulus_paths.resolve_famulus_paths:
      why:
        constructs: "Constructs resolved path state whose state root is carried into the returned lock directory."
    """
    override = os.environ.get("LIST_MANAGER_CLOUD_LOCK_DIR")
    if override:
        return Path(override)
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(platform=sys.platform, home=home or Path.home()).state_root / "list-manager" / "locks"


_SAFE_LOCK_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def cloud_lock_path(list_name: str) -> Path:
    """Build the shared local lock target for one cloud-list name.

    Intent
    ------
    Map equal cloud names on one machine to the same safe sidecar stem.

    Rationale
    ---------
    Stable name-based paths serialize same-machine writers across independent
    download directories; they intentionally cannot coordinate separate hosts.

    Pseudocode
    ----------
    - set safe_name = cloud name with unsafe characters replaced
    - lock_directory = _cloud_lock_dir()
    - set existing_directory = lock_directory created when absent
    - return name-keyed target beneath lock_directory

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._cloud_lock_dir:
      why:
        constructs: "Provides the shared directory used to construct the name-keyed lock target."
    """
    safe_name = _SAFE_LOCK_NAME_RE.sub("_", list_name) or "_"
    lock_dir = _cloud_lock_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    # ".yaml" suffix purely so file_lock()'s "<file>.lock" sidecar naming
    # reads the same way it does for local-file locks; no such file is ever
    # created or read here, only its ".lock" sidecar.
    return lock_dir / f"{safe_name}.yaml"


def _test_race_delay() -> None:
    """Pause a lock-held mutation only when the race-test hook is enabled.

    Intent
    ------
    Make concurrent tests hold a writer inside the check-to-write interval.

    Rationale
    ---------
    An environment-gated delay creates deterministic overlap without affecting
    production invocations or changing the lock protocol under test.

    Pseudocode
    ----------
    - set delay_seconds = environment race-test delay
    - if delay_seconds exists:
      - set elapsed_delay = sleep for delay_seconds

    Wraps
    -----
    - none
    """
    delay = os.environ.get("LIST_MANAGER_TEST_RACE_DELAY")
    if delay:
        time.sleep(float(delay))


# ── Cloud transport ───────────────────────────────────────────────────────────
# See cloud_transport.py -- shared with read_beautify.py so there's exactly
# one implementation of "talk to cloud-files' lists-read/lists-write".

def download_list(list_name: str, dest_path: Path) -> None:
    """Download one cloud list or terminate with a bounded transport error.

    Intent
    ------
    Translate the cloud helper's exception into the CLI failure protocol.

    Rationale
    ---------
    Command orchestration should not expose transport exception types or stack
    traces to callers expecting stderr diagnostics and a nonzero status.

    Pseudocode
    ----------
    - @_cloud_transport.download_list(list_name, destination_path)
    - if CloudTransportError is raised:
      - @die(transport diagnostic)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._cloud_transport.download_list [implicit]:
      why:
        reads: "Downloads the named cloud list into the requested local snapshot path."
    .die:
      why:
        orchestrates: "Translates a transport exception into the command failure protocol."
    """
    try:
        cloud_transport.download_list(list_name, dest_path)
    except cloud_transport.CloudTransportError as exc:
        die(str(exc))


def upload_list(list_name: str, src_path: Path) -> None:
    """Upload one cloud list or terminate with a bounded transport error.

    Intent
    ------
    Translate the cloud helper's exception into the CLI failure protocol.

    Rationale
    ---------
    Keeping the conversion at this boundary gives local command handlers one
    uniform error model for cloud reads and writes.

    Pseudocode
    ----------
    - @_cloud_transport.upload_list(list_name, source_path)
    - if CloudTransportError is raised:
      - @die(transport diagnostic)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._cloud_transport.upload_list [implicit]:
      why:
        writes: "Uploads the requested local snapshot to the named cloud list."
    .die:
      why:
        orchestrates: "Translates a transport exception into the command failure protocol."
    """
    try:
        cloud_transport.upload_list(list_name, src_path)
    except cloud_transport.CloudTransportError as exc:
        die(str(exc))


# ── ID generation ─────────────────────────────────────────────────────────────

def collect_ids(node) -> set[str]:
    """Collect every entry ID reachable in a nested list structure.

    Intent
    ------
    Return the set of identifiers already occupied by entries at any depth.

    Rationale
    ---------
    ID generation must avoid collisions in categories and child-entry trees,
    not only in a single top-level collection.

    Pseudocode
    ----------
    - set identifiers = empty set
    - if node is a mapping:
      - set identifiers = identifiers plus the mapping ID
      - nested_ids = collect_ids(nested_value)
      - set identifiers = identifiers plus nested_ids
    - else:
      - if node is a sequence:
        - nested_ids = collect_ids(each nested sequence item)
        - set identifiers = identifiers plus nested_ids
    - return identifiers

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .collect_ids:
      why:
        transforms: "Produces recursive identifier sets merged into the caller's accumulated result."
    """
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
    """Generate the requested number of unique six-character hexadecimal IDs.

    Intent
    ------
    Return fresh identifiers absent from both the document and the current batch.

    Rationale
    ---------
    Three random bytes match the schema's six-hex-character shape, while explicit
    collision checks protect the occupied set and multi-ID requests.

    Pseudocode
    ----------
    - set accepted_ids = empty list
    - while accepted count is less than requested count:
      - set candidate_id = three random bytes encoded as hexadecimal
      - if candidate_id is unused:
        - set accepted_ids = accepted_ids plus candidate_id
    - return accepted_ids

    Wraps
    -----
    - none
    """
    ids: list[str] = []
    while len(ids) < count:
        candidate = os.urandom(3).hex()
        if candidate not in existing_ids and candidate not in ids:
            ids.append(candidate)
    return ids


# ── Validation ───────────────────────────────────────────────────────────────

_AUTO_GENERATED_FIELDS = {"id", "created", "state"}


def validate_entries_before_insert(entries: list, schema_name: str) -> None:
    """Reject entry inputs missing fields that callers must supply.

    Intent
    ------
    Check user-provided required fields before generated defaults are added.

    Rationale
    ---------
    Failing early prevents invented schema-required values while exempting fields
    generated by the command itself.

    Pseudocode
    ----------
    - if validation support is unavailable:
      - return
    - schema_exists = @_get_schema.list_schema_exists(schema_name)
    - if schema_exists is false:
      - return
    - whole_schema = _get_schema.get_schema(schema_name, `*`)
    - set required_inputs = whole_schema requirements minus generated fields
    - for entry in entries:
      - set missing_inputs = required inputs absent from entry
      - if missing_inputs is nonempty:
        - @die(missing field diagnostic)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._get_schema.list_schema_exists [implicit]:
      why:
        validates: "Checks whether the named schema is available before required-field lookup."
    .die:
      why:
        validates: "Rejects an entry that omits caller-owned required fields."

    InstantiationsFromRepo
    ----------------------
    ._get_schema.get_schema [implicit]:
      why:
        constructs: "Provides the whole-schema mapping used to derive caller-owned required fields."
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
    """Validate a list document against its declared schema.

    Intent
    ------
    Normalize the document and reject unknown, unavailable, or violated schemas.

    Rationale
    ---------
    Mutating commands share this fail-closed boundary so invalid snapshots are
    never deliberately written.

    Pseudocode
    ----------
    - @normalize_dates(document)
    - set schema_name = document schema
    - if schema_name is missing:
      - @die(missing schema diagnostic)
    - schema_exists = @_get_schema.list_schema_exists(schema_name)
    - if schema_exists is false:
      - missing_schema_path = _get_schema.list_schema_path(schema_name)
      - @die(unknown schema and missing_schema_path)
    - if validation support is unavailable:
      - @die(installation diagnostic)
    - @_get_schema.validate_document(document, schema_name)
    - if schema validation fails:
      - failure_message = describe_validation_error(document, failure)
      - @die(failure_message)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .normalize_dates:
      why:
        transforms: "Converts parsed dates before applying string schema constraints."
    ._get_schema.list_schema_exists [implicit]:
      why:
        validates: "Checks that the document's declared schema exists before validation."
    ._get_schema.validate_document [implicit]:
      why:
        validates: "Applies the selected schema to the normalized document."
    .die:
      why:
        validates: "Terminates mutation when schema selection or validation fails."

    InstantiationsFromRepo
    ----------------------
    ._get_schema.list_schema_path [implicit]:
      why:
        constructs: "Provides the expected schema path included in an unknown-schema diagnostic."
    .describe_validation_error:
      why:
        constructs: "Constructs a contextual failure message carried into the shared termination boundary."
    """
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
    """Render a schema failure with its document and entry context.

    Intent
    ------
    Return an actionable message naming the failing path and nearest entry.

    Rationale
    ---------
    Raw schema messages often omit the stable ID and title needed to locate an
    invalid row in a nested list.

    Pseudocode
    ----------
    - set error_path = schema failure path
    - set nearest_entry = mapping with ID and title found while walking error_path
    - set location = joined path or document root marker
    - return schema message with location and optional entry identity

    Wraps
    -----
    - none
    """
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
    """Parse exact and regular-expression filters into structured triples.

    Intent
    ------
    Convert each supported token into a key, operator, and value.

    Rationale
    ---------
    Strict parsing rejects malformed filters before matching while preserving
    comma-separated exact alternatives for later OR evaluation.

    Pseudocode
    ----------
    - set parsed_filters = empty list
    - for token in filter arguments:
      - if token does not match the supported grammar:
        - @die(filter syntax diagnostic)
      - set parsed_filters = parsed_filters plus captured triple
    - return parsed_filters

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .die:
      why:
        parses: "Rejects a token outside the two supported filter forms."
    """
    filters = []
    for f in filter_args:
        m = re.match(r"^([^~=]+)(~=|=)(.+)$", f)
        if not m:
            die(f"invalid filter '{f}': expected key=value or key~=value")
        filters.append((m.group(1), m.group(2), m.group(3)))
    return filters


def validate_filter_values(filters: list[tuple[str, str, str]], schema_name: str) -> None:
    """Reject invalid enum literals used in exact-match filters.

    Intent
    ------
    Validate comma-separated exact values when the selected field defines an enum.

    Rationale
    ---------
    A mistyped enum should be an actionable error rather than an apparently valid
    empty result; pattern filters remain exempt because they are not literals.

    Pseudocode
    ----------
    - for filter_spec in filters:
      - set key_operator_value = unpacked filter_spec
      - if operator is not exact:
        - continue
      - field_schema = _get_schema.get_schema(schema_name, key)
      - if field_schema has no enum:
        - continue
      - set invalid_values = values absent from the field enum
      - if invalid_values is nonempty:
        - @die(enum diagnostic)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .die:
      why:
        validates: "Rejects invalid exact-match enum members with allowed values."

    InstantiationsFromRepo
    ----------------------
    ._get_schema.get_schema [implicit]:
      why:
        constructs: "Provides the field schema whose enum constrains exact filter literals."
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
    """Decide whether an entry satisfies grouped exact and pattern filters.

    Intent
    ------
    Apply OR within each field and AND across distinct fields.

    Rationale
    ---------
    Grouping repeated keys preserves alternative-value semantics, while invalid
    regular expressions fall back to literal containment instead of crashing.

    Pseudocode
    ----------
    - set grouped_filters = conditions grouped by field
    - for field_conditions in grouped_filters:
      - set field_and_conditions = unpacked field_conditions
      - set field_match = any exact alternative or pattern match
      - if field_match is false:
        - return false
    - return true

    Wraps
    -----
    - none
    """
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
    """Prune one entry while retaining ancestors of matching descendants.

    Intent
    ------
    Return one copied branch when the entry or any child matches, otherwise ``None``.

    Rationale
    ---------
    Preserving position and pruned children retains context without duplicating a
    nested match as a separate top-level result.

    Pseudocode
    ----------
    - set pruned_children = empty list
    - for child in declared children:
      - pruned_child = _prune_entry(child, filters)
      - if pruned_child exists:
        - set pruned_children = pruned_children plus pruned_child
    - self_matches = @entry_matches(entry, filters)
    - if self_matches is false and pruned_children is empty:
      - return none
    - set retained_entry = copy with pruned declared children
    - return retained_entry

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .entry_matches:
      why:
        computes: "Tests whether the current entry itself satisfies the grouped filters."

    InstantiationsFromRepo
    ----------------------
    "._prune_entry [implicit]":
      why:
        transforms: "Produces each recursively pruned child carried into the retained branch."
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
    """Prune a category to entry and subcategory branches containing matches.

    Intent
    ------
    Return a copied category only when some descendant entry survives filtering.

    Rationale
    ---------
    Retaining only nonempty ancestors preserves navigational context and removes
    unrelated branches without mutating the source document.

    Pseudocode
    ----------
    - set pruned_entries = empty list
    - for entry in direct entries:
      - pruned_entry = _prune_entry(entry, filters)
      - if pruned_entry exists:
        - set pruned_entries = pruned_entries plus pruned_entry
    - set pruned_categories = empty list
    - for category in nested categories:
      - pruned_category = _prune_category(category, filters)
      - if pruned_category exists:
        - set pruned_categories = pruned_categories plus pruned_category
    - if both collections are empty:
      - return none
    - set retained_category = copy with pruned declared collections
    - return retained_category

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    "._prune_entry [implicit]":
      why:
        transforms: "Produces each pruned entry carried into the retained category."
    "._prune_category [implicit]":
      why:
        transforms: "Produces each pruned subcategory carried into the retained category."
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
    """Return the current heterogeneous ordering key for one filtered entry.

    Intent
    ------
    Preserve the exact key shapes currently consumed by recursive list sorting.

    Rationale
    ---------
    Missing fields produce positive infinity, strings of length at least ten
    produce ``(string, 0)``, and other values remain unchanged. Mixed shapes can
    be mutually incomparable and therefore cause sorting to raise ``TypeError``.

    Pseudocode
    ----------
    - if the sort field is missing:
      - return positive infinity
    - set field_value = selected entry value
    - if field_value is a string of length at least ten:
      - return tuple of field_value and zero
    - return field_value unchanged

    Wraps
    -----
    - none
    """
    if sort_field not in entry:
        return float('inf')  # missing values sort last
    v = entry[sort_field]
    if isinstance(v, str) and len(v) >= 10:
        return (v, 0)  # YYYY-MM-DD sorts lexicographically (earlier dates first)
    return v


def _sort_tree(node, sort_field: str) -> None:
    """Sort every entry-bearing sequence in a filtered result tree.

    Intent
    ------
    Apply one field ordering recursively to entries, children, and bare lists.

    Rationale
    ---------
    Ancestor-preserving filters can return matches at arbitrary depth, so sorting
    only one top-level collection would produce inconsistent output. The helper's
    heterogeneous keys are used unchanged, so an incompatible comparison can
    propagate ``TypeError`` to the command boundary.

    Pseudocode
    ----------
    - if node is a mapping:
      - entry_key = _entry_sort_key(each entry, sort_field)
      - set ordered_entries = entries sorted by entry_key
      - @_sort_tree(each nested branch, sort field)
      - child_key = _entry_sort_key(each child, sort_field)
      - set ordered_children = children sorted by child_key
    - if node is a sequence:
      - item_key = _entry_sort_key(each item, sort_field)
      - set ordered_items = items sorted by item_key
      - @_sort_tree(each ordered item, sort field)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._sort_tree:
      why:
        transforms: "Recursively sorts every nested entry-bearing branch."

    InstantiationsFromRepo
    ----------------------
    ._entry_sort_key [implicit]:
      why:
        constructs: "Produces each lambda key consumed by in-place sequence sorting."
    """
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
    """Filter a document or bare entry list without flattening its structure.

    Intent
    ------
    Return only matching branches while retaining every category and entry ancestor.

    Rationale
    ---------
    Preserving the input shape gives callers context and prevents nested matches
    from appearing twice in independently flattened output.

    Pseudocode
    ----------
    - if input is a document with categories:
      - for category in document categories:
        - pruned_category = _prune_category(category, filters)
      - return document copy with nonempty pruned_category products
    - if input is a sequence:
      - for entry in input sequence:
        - pruned_entry = _prune_entry(entry, filters)
      - return nonempty pruned_entry products
    - return input unchanged

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    "._prune_category [implicit]":
      why:
        transforms: "Produces each pruned category carried into the returned document copy."
    "._prune_entry [implicit]":
      why:
        transforms: "Produces each pruned entry carried into the returned bare list."
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
    """Find a nested category by its ordered name path.

    Intent
    ------
    Return the category at the complete path, or ``None`` when unresolved.

    Rationale
    ---------
    Recursive descent mirrors the category tree and prevents partial path matches
    from being mistaken for a valid mutation target.

    Pseudocode
    ----------
    - if the path is empty:
      - return none
    - for category in the current level:
      - if its name matches the final segment:
        - return category
      - if its name matches:
        - return find_category_by_path(children, remaining segments)
    - return none

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .find_category_by_path:
      why:
        constructs: "Constructs the recursive lookup result for the remaining path."
    """
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
    """Enumerate display paths for every category in a nested tree.

    Intent
    ------
    Return parent-qualified category names in traversal order.

    Rationale
    ---------
    Mutation errors can show valid targets instead of reporting only that a
    requested category was absent.

    Pseudocode
    ----------
    - set paths = empty list
    - for category in categories:
      - set path = prefix plus category name
      - set paths = paths plus path and all_category_paths(children, path)
    - return paths

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .all_category_paths:
      why:
        constructs: "Constructs descendant display paths used to extend the result."
    """
    paths = []
    for cat in categories:
        path = f"{prefix}/{cat['name']}" if prefix else cat["name"]
        paths.append(path)
        paths.extend(all_category_paths(cat.get("categories", []), path))
    return paths


def find_entry_by_id(node, target_id: str) -> dict | None:
    """Find the first mapping with a requested entry ID in a nested tree.

    Intent
    ------
    Return the matching entry mapping or ``None`` across mappings and sequences.

    Rationale
    ---------
    Entries may occur in categories or child lists at arbitrary depth, so every
    nested value participates in target lookup.

    Pseudocode
    ----------
    - if node is a mapping with the target ID:
      - return node
    - set candidate = find_entry_by_id(each nested mapping value or sequence item, target ID)
    - if candidate exists:
      - return candidate
    - return none

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .find_entry_by_id:
      why:
        constructs: "Constructs each recursive candidate returned to the caller."
    """
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
    """Print field metadata for one packaged list schema.

    Intent
    ------
    Expose one field or the complete entry contract as stable YAML output.

    Rationale
    ---------
    Callers can discover required, generated, typed, and enumerated fields without
    parsing raw JSON Schema or guessing acceptable values.

    Pseudocode
    ----------
    - schema_exists = @_get_schema.list_schema_exists(schema_name)
    - if schema_exists is false:
      - missing_schema_path = _get_schema.list_schema_path(schema_name)
      - @die(unknown schema and missing_schema_path)
    - if all fields were requested:
      - whole_schema = _get_schema.get_schema(schema_name, `*`)
      - set schema_description = whole_schema properties and requirement groups
    - else:
      - field_schema = _get_schema.get_schema(schema_name, field_name)
      - set schema_description = requested field_schema metadata
    - if the requested field is unknown:
      - @die(field diagnostic)
    - set emitted_yaml = schema_description serialized to stdout

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._get_schema.list_schema_exists [implicit]:
      why:
        validates: "Checks that the requested packaged schema exists before metadata lookup."
    .die:
      why:
        orchestrates: "Rejects unknown schemas and fields before metadata emission."

    InstantiationsFromRepo
    ----------------------
    ._get_schema.list_schema_path [implicit]:
      why:
        constructs: "Provides the expected file path included in an unknown-schema diagnostic."
    ._get_schema.get_schema [implicit]:
      why:
        constructs: "Provides whole-schema or field metadata carried into emitted YAML."
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
    """Build a domain category with its schema-defined subcategories.

    Intent
    ------
    Return one named category populated for personal or general use.

    Rationale
    ---------
    Reading the packaged vocabulary avoids duplicating enum values and keeps fresh
    list defaults aligned with schema validation.

    Pseudocode
    ----------
    - subcategory_names = _get_schema.domain_subcategory_names(personal)
    - return domain mapping with child categories named by subcategory_names

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._get_schema.domain_subcategory_names [implicit]:
      why:
        constructs: "Provides the schema-defined names carried into child category mappings."
    """
    sub_names = get_schema.domain_subcategory_names(personal)
    return {"name": name, "categories": [{"name": n} for n in sub_names]}


def default_categories(schema: str) -> list[dict]:
    """Choose usable seed categories for a newly initialized list.

    Intent
    ------
    Populate structured action schemas with Personal and Work domains.

    Rationale
    ---------
    Those schemas require fixed child vocabularies, whereas schemas without a
    category vocabulary have no defensible seed and therefore remain empty.

    Pseudocode
    ----------
    - if schema uses structured action categories:
      - set seeded_categories = _domain_category(Personal) and _domain_category(Work)
      - return seeded_categories
    - return empty list

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._domain_category:
      why:
        constructs: "Constructs each schema-valid seeded domain category."
    """
    if schema in ("todo", "triage"):
        return [
            _domain_category("Personal", personal=True),
            _domain_category("Work", personal=False),
        ]
    return []


def cmd_init(args: argparse.Namespace) -> None:
    """Create and validate a new local list document.

    Intent
    ------
    Refuse existing paths, apply schema-appropriate defaults, and persist one list.

    Rationale
    ---------
    Validation before the first write prevents unusable files, while deriving the
    default name from the path keeps the optional display name predictable.

    Pseudocode
    ----------
    - set destination = requested list path
    - if destination exists:
      - @die(existing path diagnostic)
    - initialized_categories = default_categories(schema name)
    - set list_document = schema name display name and initialized_categories
    - @validate_list(list_document)
    - @save_yaml(destination, list_document)
    - set confirmation = created destination emitted to stdout

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .die:
      why:
        orchestrates: "Refuses to overwrite an existing list path."
    .validate_list:
      why:
        validates: "Checks the initialized document before its first write."
    .save_yaml:
      why:
        writes: "Persists the validated list document to the requested path."

    InstantiationsFromRepo
    ----------------------
    .default_categories:
      why:
        constructs: "Constructs schema-appropriate seed categories carried into the document."
    """
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
    """Print fresh identifiers that avoid one list snapshot.

    Intent
    ------
    Generate the requested count of IDs absent from every nested entry.

    Rationale
    ---------
    Reading and collecting once gives the command a consistent collision set, and
    one ID per output line remains straightforward for machine callers.

    Pseudocode
    ----------
    - list_document = load_yaml(requested path)
    - occupied_ids = collect_ids(list_document)
    - generated_ids = gen_ids(occupied_ids, requested count)
    - for generated_id in generated_ids:
      - set emitted_line = generated_id written to stdout

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .load_yaml:
      why:
        transforms: "Builds the normalized list snapshot used for collision checks."
    .collect_ids:
      why:
        constructs: "Constructs the occupied identifier set from the snapshot."
    .gen_ids:
      why:
        constructs: "Constructs the fresh identifier sequence emitted by the command."
    """
    file = Path(args.file)
    data = load_yaml(file)
    existing = collect_ids(data)
    ids = gen_ids(existing, args.count)
    for id_ in ids:
        print(id_)


def cmd_read(args: argparse.Namespace) -> None:
    """Read, filter, optionally sort, and emit one list snapshot.

    Intent
    ------
    Preserve raw document shape for unfiltered reads and ancestor context for matches.

    Rationale
    ---------
    A shared path handles local and downloaded snapshots, validates enum filters before
    matching, and applies sorting throughout the retained tree rather than flattening it.

    Pseudocode
    ----------
    - list_document = load_yaml(requested path)
    - if filters are absent:
      - set emitted_yaml = complete list_document written to the selected output
      - return
    - parsed_filters = parse_filters(filter tokens)
    - @validate_filter_values(parsed_filters, schema name)
    - matching_tree = collect_matching_entries(list_document, parsed_filters)
    - if a sort field is present:
      - @_sort_tree(matching_tree, sort field)
    - if sorting fails:
      - @die(sort diagnostic)
    - set emitted_yaml = matching_tree written to the selected output

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .validate_filter_values:
      why:
        validates: "Rejects impossible exact enum filters before matching."
    ._sort_tree:
      why:
        transforms: "Orders every retained entry branch by the requested field."
    .die:
      why:
        orchestrates: "Converts an incompatible sort into a bounded command error."

    InstantiationsFromRepo
    ----------------------
    .load_yaml:
      why:
        transforms: "Builds the normalized list snapshot used by the read command."
    .parse_filters:
      why:
        constructs: "Constructs structured filter triples from caller tokens."
    .collect_matching_entries:
      why:
        constructs: "Constructs the ancestor-preserving filtered result tree."
    """
    file = Path(args.file)
    data = load_yaml(file)

    def emit(content: str) -> None:
        """Write serialized list content to the selected output boundary.

        Intent
        ------
        Honor an explicit output file and otherwise preserve stdout behavior.

        Rationale
        ---------
        Keeping this choice local prevents filtering and sorting paths from duplicating
        file encoding and newline-preservation details.

        Pseudocode
        ----------
        - if an output path is configured:
          - set persisted_content = UTF-8 file containing content
        - else:
          - set emitted_content = content written to stdout without an added newline

        Wraps
        -----
        - none
        """
        if getattr(args, "output", None):
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(content)
        else:
            print(content, end="")

    if not args.filters:
        emit(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False))
        return

    filters = parse_filters(args.filters)
    validate_filter_values(filters, data.get("schema", ""))
    matches = collect_matching_entries(data, filters)

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
    """Insert validated entries beneath a category or parent entry.

    Intent
    ------
    Resolve one destination, supply generated fields, and save one revisioned mutation.

    Rationale
    ---------
    Holding the lock across load, stale-check, mutation, validation, and write prevents
    races; validating caller-owned fields before defaults avoids invented information.

    Pseudocode
    ----------
    - list_document = load_yaml(path)
    - if resolved parent-entry or category destination is missing:
      - @die(available target diagnostic)
    - set new_entries = YAML sequence read from file or stdin
    - @validate_entries_before_insert(new_entries, schema name)
    - for entry in new_entries preserving any supplied ID:
      - if entry ID is missing:
        - generated_ids = gen_ids(occupied IDs, one)
        - set entry_id = first member of generated_ids and reserve it
      - if entry state is missing:
        - set entry_state = undecided for triage or incomplete otherwise
      - if entry created date is missing:
        - set entry_created = today
    - @save_with_revision_bump(path, list_document)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .file_lock:
      why:
        orchestrates: "Holds the complete local mutation inside one advisory critical section."
    .check_revision:
      why:
        validates: "Prevents insertion into a revision superseded since the caller read it."
    ._test_race_delay:
      why:
        orchestrates: "Widens the create critical section for deterministic concurrency tests."
    .die:
      why:
        orchestrates: "Rejects absent targets and malformed entry input before persistence."
    .validate_entries_before_insert:
      why:
        validates: "Checks caller-owned required fields before generated defaults are added."
    .save_with_revision_bump:
      why:
        orchestrates: "Advances, validates, and writes the completed mutation once."

    InstantiationsFromRepo
    ----------------------
    .load_yaml:
      why:
        transforms: "Deserializes and normalizes the document that will receive new entries."
    .find_entry_by_id:
      why:
        constructs: "Constructs the optional parent-entry destination for ID targets."
    .find_category_by_path:
      why:
        constructs: "Constructs the optional category destination for path targets."
    .all_category_paths:
      why:
        constructs: "Constructs available target paths carried into an error diagnostic."
    .collect_ids:
      why:
        constructs: "Constructs the occupied ID set used to avoid collisions."
    .gen_ids [implicit]:
      why:
        constructs: "Produces the fresh ID sequence whose first member is assigned to an entry."
    """
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
    """Apply a batch of validated field patches to existing entries.

    Intent
    ------
    Update only identified mutable fields and stamp modification lifecycle dates.

    Rationale
    ---------
    The lock and optional revision guard prevent stale writes, while resolving every ID
    before the final validated save keeps the batch within one revision transition.

    Pseudocode
    ----------
    - list_document = load_yaml(requested path)
    - set patches = YAML sequence read from file or stdin
    - for patch in patches:
      - if ID is missing or an immutable field is selected:
        - @die(patch diagnostic)
      - target_entry = find_entry_by_id(list_document, patch ID)
      - if target_entry is missing:
        - @die(missing ID diagnostic)
      - set target_entry = mutable fields preserving supplied modified and completed dates
      - if modified is absent from patch:
        - set target_modified = today
      - if completed is absent and state first enters FINISHED_STATES with no recorded completion:
        - set target_completed = today
    - @save_with_revision_bump(requested path, list_document)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .file_lock:
      why:
        orchestrates: "Holds the complete batch mutation inside one advisory critical section."
    .check_revision:
      why:
        validates: "Prevents patching a revision superseded since the caller read it."
    ._test_race_delay:
      why:
        orchestrates: "Widens the update critical section for deterministic concurrency tests."
    .die:
      why:
        orchestrates: "Rejects malformed patches immutable fields and absent IDs."
    .save_with_revision_bump:
      why:
        orchestrates: "Advances, validates, and writes the completed patch batch once."

    InstantiationsFromRepo
    ----------------------
    .load_yaml:
      why:
        transforms: "Deserializes and normalizes the document whose entries receive patches."
    .find_entry_by_id:
      why:
        constructs: "Constructs each optional target entry carried into patch application."
    """
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
    """Remove selected entries and their subtrees from a nested list in place.

    Intent
    ------
    Filter matching IDs from every sequence while preserving all surviving branches.

    Rationale
    ---------
    Filtering before recursion naturally drops a removed parent's complete subtree and
    ensures recursion visits only survivors at category and child-entry depths.

    Pseudocode
    ----------
    - if node is a mapping:
      - for child in mapping values:
        - set surviving_items = sequence items whose IDs are not selected
        - @remove_entries_by_ids(each surviving item, selected IDs)
    - else:
      - if node is a sequence:
        - set surviving_items = sequence items whose IDs are not selected
        - @remove_entries_by_ids(each surviving item, selected IDs)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .remove_entries_by_ids:
      why:
        transforms: "Recursively removes selected entries from every surviving branch."
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
    """Delete a verified set of entry IDs in one revisioned mutation.

    Intent
    ------
    Abort the whole request when any ID is absent, otherwise remove every selected subtree.

    Rationale
    ---------
    Prechecking the complete set avoids partial deletion, and the lock plus optional
    revision guard prevents concurrent writers from silently replacing one another.

    Pseudocode
    ----------
    - @file_lock(requested path)
    - list_document = load_yaml(requested path)
    - @check_revision(list_document, expected revision, requested path)
    - @_test_race_delay()
    - occupied_ids = collect_ids(list_document)
    - set missing_ids = requested IDs absent from occupied_ids
    - if missing_ids is nonempty:
      - raise SystemExit after writing each missing ID to stderr
    - @remove_entries_by_ids(list_document, requested IDs)
    - @save_with_revision_bump(requested path, list_document)
    - set confirmations = sorted deleted IDs written to stdout

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .file_lock:
      why:
        orchestrates: "Holds the complete deletion inside one advisory critical section."
    .check_revision:
      why:
        validates: "Prevents deletion from a revision superseded since the caller read it."
    ._test_race_delay:
      why:
        orchestrates: "Widens the delete critical section for deterministic concurrency tests."
    .remove_entries_by_ids:
      why:
        transforms: "Removes each selected entry and its descendants from the snapshot."
    .save_with_revision_bump:
      why:
        orchestrates: "Advances, validates, and writes the completed deletion once."

    InstantiationsFromRepo
    ----------------------
    .load_yaml:
      why:
        transforms: "Deserializes and normalizes the document whose selected subtrees are removed."
    .collect_ids:
      why:
        constructs: "Constructs the occupied ID set used to prove all targets exist."
    """
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
    """Build the command parser for every local and cloud list operation.

    Intent
    ------
    Define subcommands, positionals, shared cloud flags, and revision guards in one place.

    Rationale
    ---------
    Central construction keeps local and cloud entrypoints on the same argument contract
    while limiting schema-description parsing to its dedicated reusable helper.

    Pseudocode
    ----------
    - set command_parser = parser with required subcommand selection
    - set shared_flags = cloud mode and optional expected revision definitions
    - @build_describe_schema_parser(schema description subparser)
    - set subcommands = initialization read create update ID generation and deletion parsers
    - return command_parser

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .build_describe_schema_parser:
      why:
        transforms: "Adds the reusable schema and field positionals to its subparser."
    """
    parser = argparse.ArgumentParser(prog="lists.py")
    sub = parser.add_subparsers(dest="command", required=True)

    # Helper to add --cloud to any subcommand. When set, the source positional
    # is treated as a cloud list NAME (download → operate → upload) instead of a
    # local file PATH. It is a plain boolean, so it never consumes a positional
    # and filters keep their own slot.
    def add_cloud_arg(subparser):
        """Add the shared cloud-mode switch to one subcommand parser.

        Intent
        ------
        Mark a source positional as a cloud list name rather than a local path.

        Rationale
        ---------
        One boolean option avoids duplicated definitions and leaves positional filter
        parsing unchanged because the switch consumes no value.

        Pseudocode
        ----------
        - set cloud_option = boolean cloud switch added to subparser

        Wraps
        -----
        - none
        """
        subparser.add_argument(
            "--cloud",
            action="store_true",
            help="Treat the source as a cloud list name; download, operate, and upload",
        )

    # Optimistic-concurrency guard (feedback items 24/25): optional on every
    # mutating subcommand, opt-in for backward compat. See the module note
    # above check_revision()/StaleRevisionError for the full rationale.
    def add_expected_revision_arg(subparser):
        """Add the optional optimistic-concurrency guard to a mutating parser.

        Intent
        ------
        Parse an expected integer revision while preserving an unconditional default.

        Rationale
        ---------
        Reusing one option definition keeps every mutating command backward compatible
        and gives guarded callers identical stale-write semantics.

        Pseudocode
        ----------
        - set revision_option = optional integer guard added to subparser

        Wraps
        -----
        - none
        """
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
    """Add schema-description positionals to an existing parser.

    Intent
    ------
    Configure a required schema and an optional field defaulting to all fields.

    Rationale
    ---------
    The helper supports both the full command tree and the dedicated machine interface
    without duplicating positional names, defaults, or help text.

    Pseudocode
    ----------
    - set schema_argument = required schema positional added to parser
    - set field_argument = optional field positional defaulting to all fields
    - return parser

    Wraps
    -----
    - none
    """
    parser.add_argument("schema", help="Schema name (todo, triage, default)")
    parser.add_argument(
        "field",
        nargs="?",
        default="*",
        help="Field name to describe, or omit / pass '*' for all fields",
    )
    return parser


class DescribeSchemaInterface(PythonMachineInterface):
    """Expose schema description as a dedicated machine interface.

    Intent
    ------
    Bind schema and optional field arguments directly to the read-only handler.

    Rationale
    ---------
    A narrow interface avoids requiring the general subcommand parser for callers that
    need packaged field metadata without local or cloud list access.

    Pseudocode
    ----------
    - set interface_program = schema-description command label
    - set interface_parser = base parser extended with schema positionals
    - set interface_result = schema handler completion status

    Wraps
    -----
    - none
    """
    prog = "lists.py describe-schema"

    def build_parser(self) -> argparse.ArgumentParser:
        """Extend the base interface parser with schema positionals.

        Intent
        ------
        Return the dedicated parser accepted by this machine interface.

        Rationale
        ---------
        Delegating positional construction preserves exact parity with the general
        ``describe-schema`` subcommand while retaining the framework base parser.

        Pseudocode
        ----------
        - base_parser = PythonMachineInterface.build_parser()
        - return base_parser extended with schema and field positionals

        Wraps
        -----
        - .build_describe_schema_parser -> preprocess: supply the superclass parser; postprocess: return the extended parser; fixed_arguments: none

        InstantiationsFromRepo
        ----------------------
        "officina.runtime.python_machine_interface.PythonMachineInterface.build_parser [implicit]":
          why:
            constructs: "Produces the base parser carried into schema-position configuration."
        """
        return build_describe_schema_parser(super().build_parser())

    def run(self, args: argparse.Namespace) -> int:
        """Execute schema description and return a successful interface status.

        Intent
        ------
        Invoke the shared handler with parsed interface arguments.

        Rationale
        ---------
        The handler owns validation and output, while this method supplies the integer
        status required by the machine-interface runtime after normal completion.

        Pseudocode
        ----------
        - @cmd_describe_schema(parsed arguments)
        - set completion_status = zero after normal handler completion
        - return completion_status

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        .cmd_describe_schema:
          why:
            dispatches: "Produces schema output before this adapter returns the required integer success status."
        """
        cmd_describe_schema(args)
        return 0


class Interface(PythonArgvMachineInterface):
    """Expose the complete list command tree as an argv machine interface.

    Intent
    ------
    Bind declared cloud dispatches and command-line parsing to the runtime gateway.

    Rationale
    ---------
    The generic argv adapter preserves one implementation for direct execution and
    dispatcher-managed local or cloud invocations.

    Pseudocode
    ----------
    - set declared_dispatches = cloud transport interface menu
    - set interface_program = list command label
    - set interface_result = main invoked with provided argv

    Wraps
    -----
    - none
    """
    dispatches = cloud_transport.DISPATCHES
    prog = "lists.py"

    def run(self, argv: list[str]) -> int:
        """Forward interface arguments to the shared command entrypoint.

        Intent
        ------
        Return the command status produced for the supplied argv sequence.

        Rationale
        ---------
        Direct delegation keeps interface-managed execution behavior identical to the
        module's executable entrypoint without re-parsing or translating arguments.

        Pseudocode
        ----------
        - return status from shared command entrypoint

        Wraps
        -----
        - .main -> preprocess: forward argv unchanged; postprocess: return status unchanged; fixed_arguments: none
        """
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    """Parse and orchestrate one local or cloud list command.

    Intent
    ------
    Route parsed arguments through local handlers and bounded cloud snapshot transport.

    Rationale
    ---------
    Cloud mutations hold a stable name-keyed lock across download, local mutation, and
    upload, while local mode dispatches directly and stale conflicts share one error edge.

    Pseudocode
    ----------
    - set parsed_command = argv parsed by the command parser
    - if cloud mode is selected:
      - set cloud_workspace = isolated snapshot plus name-keyed mutation guard
      - if command is initialization:
        - if display name is missing:
          - set display_name = cloud list name
      - else:
        - @download_list(list name, temporary snapshot)
      - set command_effects = selected handler executed against temporary snapshot
      - if command mutates:
        - @upload_list(list name, temporary snapshot)
      - set cloud_workspace = removed unconditionally
    - else:
      - set command_effects = selected handler executed with parsed arguments
    - if a stale revision is raised:
      - @die(conflict diagnostic)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ".cmd_init [implicit]":
      why:
        dispatches: "Handles the dynamically selected local initialization command."
    ".cmd_read [implicit]":
      why:
        dispatches: "Handles the dynamically selected local read command."
    ".cmd_create_entry [implicit]":
      why:
        dispatches: "Handles the dynamically selected local entry-creation command."
    ".cmd_update [implicit]":
      why:
        dispatches: "Handles the dynamically selected local update command."
    ".cmd_delete [implicit]":
      why:
        dispatches: "Handles the dynamically selected local deletion command."
    ".cmd_gen_id [implicit]":
      why:
        dispatches: "Handles the dynamically selected local ID-generation command."
    ".cmd_describe_schema [implicit]":
      why:
        dispatches: "Handles the dynamically selected local schema-description command."
    .download_list:
      why:
        reads: "Materializes the current cloud snapshot before local handling."
    .upload_list:
      why:
        writes: "Publishes a successfully mutated cloud snapshot."
    .die:
      why:
        orchestrates: "Translates stale revision conflicts into the command failure protocol."

    InstantiationsFromRepo
    ----------------------
    .build_parser:
      why:
        constructs: "Constructs the parser carried into argument selection."
    .cloud_lock_path:
      why:
        constructs: "Constructs the stable target carried into cloud lock acquisition."
    .file_lock:
      why:
        constructs: "Constructs the context manager carried across the cloud transport critical section."
    """
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
