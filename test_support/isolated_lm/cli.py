"""Supported JSON orchestration for the isolated language-model VM harness."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import NoReturn

from test_support.isolated_lm.guest import (
    GUEST_USER,
    prepare_run,
    render_user_data,
    validate_run_id,
)
from test_support.isolated_lm.host import HostPreflightReport, check_host
from test_support.isolated_lm.image import (
    CHECKSUMS_URL,
    IMAGE_FILENAME,
    IMAGE_URL,
    SIGNATURE_URL,
    prepare_cloud_image,
)
from test_support.isolated_lm.model import (
    CloudImageRecord,
    RunRecord,
    RuntimePaths,
    VmResources,
)
from test_support.isolated_lm.qemu import (
    build_qemu_command,
    build_ssh_command,
    start_run,
    stop_run,
    validate_identity_file,
    wait_for_ssh,
)


IMAGE_RECORD_NAME = "source-image.json"
"""Canonical manifest name for the authenticated Ubuntu source image."""

_MAX_DIAGNOSTIC_BYTES = 2048
_MAX_MANIFEST_BYTES = 1024 * 1024

_RUN_LIFECYCLES = frozenset(
    {"prepared", "launch-failed", "running", "ready", "stopped"}
)
_RUN_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "run_dir",
        "resources",
        "source_image_digest",
        "overlay",
        "seed_iso",
        "known_hosts",
        "serial_log",
        "qmp_socket",
        "pid_file",
        "record_path",
        "ssh_user",
        "created_at_utc",
        "lifecycle",
        "ssh_port",
        "identity_file",
        "qemu_command",
    }
)
_IMAGE_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "image_url",
        "checksums_url",
        "signature_url",
        "filename",
        "verified_source_digest",
        "byte_size",
        "retrieved_at",
        "cached_path",
    }
)


class CliUsageError(ValueError):
    """Represent rejected operator input or stale local state.

    Intent
    ------
    Distinguish caller-correctable CLI contract failures from host operations
    and unexpected programming defects.

    Rationale
    ---------
    The top-level transport maps this category to exit status two without
    broadly misclassifying every ValueError raised inside orchestration.

    Pseudocode
    ----------
    - set error_category = operator input or state contract failure
    - return error_category

    Wraps
    -----
    none
    """


class _JsonArgumentParser(argparse.ArgumentParser):
    """Raise parser errors so main can preserve the JSON failure transport.

    Intent
    ------
    Disable abbreviated options and convert argparse usage failures into the
    CLI's structured error category.

    Rationale
    ---------
    Default argparse writes plaintext and exits before the orchestration layer
    can emit its single JSON object contract.

    Pseudocode
    ----------
    - set parser_abbreviation_policy = disabled
    - set parser_error_policy = raise CLI usage error

    Wraps
    -----
    none
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Initialize argparse with option abbreviation disabled by default.

        Intent
        ------
        Apply an explicit closed-option policy while retaining caller-supplied
        parser configuration.

        Rationale
        ---------
        Accepting prefixes such as ``--state`` would make unsupported spellings
        part of the operator interface and weaken stable invocation checks.

        Pseudocode
        ----------
        - set allow_abbrev_default = false
        - set parser = inherited initialization with supplied configuration

        Wraps
        -----
        none
        """
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        """Reject malformed arguments without argparse writing mixed output.

        Intent
        ------
        Carry argparse's diagnostic into the structured CLI failure path.

        Rationale
        ---------
        Raising instead of printing prevents plaintext usage output from
        preceding or corrupting the sole JSON stdout object.

        Pseudocode
        ----------
        - raise CliUsageError(message)

        Wraps
        -----
        none

        InstantiationsFromRepo
        ----------------------
        .CliUsageError:
          why:
            raises: "Raises the operator-input category caught by the top-level JSON transport."
        """
        raise CliUsageError(message)


class _StateReader:
    """Read state-owned files through retained no-follow directory descriptors.

    Intent
    ------
    Traverse only literal children of the fixed state layout and keep every
    ancestor descriptor alive while metadata and manifests are consumed.

    Rationale
    ---------
    Relative descriptor operations prevent path resolution from silently
    crossing a substituted directory symlink during a selected read.

    Pseudocode
    ----------
    - set reader = root path opener and retained descriptor collection
    - return reader

    Wraps
    -----
    none
    """

    def __init__(
        self, root: Path, *, open_file: Callable[..., int] = os.open
    ) -> None:
        """Initialize a state reader with one injectable descriptor opener.

        Intent
        ------
        Retain the lexical root and descriptor ownership needed by the context
        manager without opening filesystem state during construction.

        Rationale
        ---------
        Deferring I/O to context entry keeps construction side-effect free and
        gives tests one narrow boundary for asserting open flags.

        Pseudocode
        ----------
        - set root = supplied state root
        - set opener = supplied descriptor opener
        - set retained_descriptors = empty collection
        - set root_descriptor = unopened sentinel

        Wraps
        -----
        none
        """
        self.root = root
        self._open_file = open_file
        self._descriptors: list[int] = []
        self.root_fd = -1

    def __enter__(self) -> "_StateReader":
        """Open and retain the no-follow state-root directory descriptor.

        Intent
        ------
        Establish the root authority from which every child operation is
        resolved for this context.

        Rationale
        ---------
        Opening once prevents later child lookups from restarting at a mutable
        absolute pathname.

        Pseudocode
        ----------
        - set root_descriptor = no-follow directory open for root
        - return reader

        Wraps
        -----
        none
        """
        self.root_fd = self._open_directory_path(self.root, "state root")
        return self

    def __exit__(self, *unused: object) -> None:
        """Close every retained directory descriptor in reverse-open order.

        Intent
        ------
        Release all descriptor authority owned by one state-reader context.

        Rationale
        ---------
        Deterministic cleanup avoids descriptor leaks while preserving each
        ancestor until all descendant reads have completed.

        Pseudocode
        ----------
        - while retained_descriptors is nonempty:
          - set descriptor = removed last retained descriptor
          - set descriptor = closed
        - return none

        Wraps
        -----
        none
        """
        while self._descriptors:
            os.close(self._descriptors.pop())

    @staticmethod
    def _directory_flags() -> int:
        """Build the platform-supported flags for a no-follow directory open.

        Intent
        ------
        Combine read-only directory enforcement with close-on-exec and final-hop
        symlink rejection where the host exposes those flags.

        Rationale
        ---------
        Centralizing flags keeps root and child opens under the same containment
        contract and prevents accidental writable descriptors.

        Pseudocode
        ----------
        - set flags = read-only directory close-on-exec and no-follow bits
        - return flags

        Wraps
        -----
        none
        """
        return (
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )

    def _open_directory_path(self, path: Path, label: str) -> int:
        """Open one absolute directory path and retain its verified descriptor.

        Intent
        ------
        Convert the root path into a real directory descriptor or one bounded
        usage error.

        Rationale
        ---------
        Root establishment cannot use a parent descriptor, so this one method
        owns the absolute no-follow open and normalizes filesystem failures.

        Pseudocode
        ----------
        - set descriptor = no-follow directory open for path
        - if path is missing or unsafe:
          - raise state directory usage error
        - return retained directory descriptor

        Wraps
        -----
        none

        InstantiationsFromRepo
        ----------------------
        .CliUsageError:
          why:
            raises: "Raises the structured usage category when the root directory cannot be safely opened."
        """
        try:
            descriptor = self._open_file(path, self._directory_flags())
        except FileNotFoundError as error:
            raise CliUsageError(f"{label} not found: {path}") from error
        except OSError as error:
            raise CliUsageError(f"{label} must be a real directory") from error
        return self._retain_directory(descriptor, label)

    def open_directory(self, parent_fd: int, name: str, label: str) -> int:
        """Open one literal child directory without following its final hop.

        Intent
        ------
        Resolve a validated single-component child relative to an already
        retained parent descriptor.

        Rationale
        ---------
        Rejecting separators, dot components, and NUL prevents the fixed state
        layout API from becoming an arbitrary pathname traversal surface.

        Pseudocode
        ----------
        - if name is not one literal child component:
          - raise CliUsageError(invalid state layout name)
        - set descriptor = relative no-follow directory open
        - return retained directory descriptor

        Wraps
        -----
        none

        InstantiationsFromRepo
        ----------------------
        .CliUsageError:
          why:
            raises: "Raises the structured usage category for invalid or unsafe child directories."
        """
        if not name or name in {".", ".."} or "/" in name or "\0" in name:
            raise CliUsageError(f"{label} has an invalid state-layout name")
        try:
            descriptor = self._open_file(
                name, self._directory_flags(), dir_fd=parent_fd
            )
        except FileNotFoundError as error:
            raise CliUsageError(f"{label} not found") from error
        except OSError as error:
            raise CliUsageError(f"{label} must be a real directory") from error
        return self._retain_directory(descriptor, label)

    def _retain_directory(self, descriptor: int, label: str) -> int:
        """Verify a descriptor is a directory and transfer it into reader ownership.

        Intent
        ------
        Add only fstat-confirmed directory descriptors to the context's cleanup
        collection.

        Rationale
        ---------
        Descriptor metadata is authoritative after open; closing immediately on
        failure prevents ownership ambiguity and leaks.

        Pseudocode
        ----------
        - if descriptor metadata is not a directory:
          - set descriptor = closed
          - raise invalid state directory
        - set retained_descriptors = retained_descriptors plus descriptor
        - return descriptor

        Wraps
        -----
        none

        InstantiationsFromRepo
        ----------------------
        .CliUsageError:
          why:
            raises: "Raises the structured usage category when fstat disproves directory type."
        """
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise CliUsageError(f"{label} must be a real directory")
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptors.append(descriptor)
        return descriptor

    def entry_stat(
        self, parent_fd: int, name: str, label: str, *, missing_ok: bool = False
    ) -> os.stat_result | None:
        """Return no-follow metadata for one fixed child entry.

        Intent
        ------
        Inspect a named artifact relative to its retained directory without
        following a final symlink.

        Rationale
        ---------
        Status needs artifact types without opening FIFOs or devices; relative
        metadata provides that evidence without blocking reads.

        Pseudocode
        ----------
        - set metadata = relative no-follow stat for name
        - if entry is absent and missing is allowed:
          - return none
        - if entry is absent or unreadable:
          - raise CliUsageError(artifact metadata failure)
        - return metadata

        Wraps
        -----
        none

        InstantiationsFromRepo
        ----------------------
        .CliUsageError:
          why:
            raises: "Raises the structured usage category for required or unreadable state artifacts."
        """
        try:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as error:
            if missing_ok:
                return None
            raise CliUsageError(f"{label} is missing") from error
        except OSError as error:
            raise CliUsageError(f"{label} is unreadable") from error

    def read_json(self, parent_fd: int, name: str, label: str) -> dict[str, object]:
        """Read one bounded regular UTF-8 JSON object relative to a retained fd.

        Intent
        ------
        Decode one manifest only after no-follow open, regular-file fstat, and a
        one-megabyte read bound.

        Rationale
        ---------
        Descriptor-first validation prevents symlink, FIFO, device, and
        unbounded manifest inputs from redirecting or blocking the CLI.

        Pseudocode
        ----------
        - set descriptor = relative no-follow file open
        - if descriptor is not a bounded regular file:
          - raise CliUsageError(invalid manifest file)
        - set payload = bounded descriptor reads
        - set decoded = UTF-8 JSON parse of payload
        - if decoded is not one object:
          - raise CliUsageError(invalid manifest object)
        - return decoded

        Wraps
        -----
        none

        InstantiationsFromRepo
        ----------------------
        .CliUsageError:
          why:
            raises: "Raises the structured usage category for unsafe, oversized, or corrupt manifests."
        """
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = self._open_file(name, flags, dir_fd=parent_fd)
        except FileNotFoundError as error:
            raise CliUsageError(f"{label} not found") from error
        except OSError as error:
            raise CliUsageError(f"{label} is not safely readable") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise CliUsageError(f"{label} must be a regular file")
            if metadata.st_size > _MAX_MANIFEST_BYTES:
                raise CliUsageError(f"{label} is too large")
            chunks: list[bytes] = []
            remaining = _MAX_MANIFEST_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > _MAX_MANIFEST_BYTES:
                raise CliUsageError(f"{label} is too large")
        finally:
            os.close(descriptor)
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise CliUsageError(f"{label} is not valid UTF-8") from error
        except json.JSONDecodeError as error:
            raise CliUsageError(f"{label} is corrupt JSON") from error
        if not isinstance(decoded, dict):
            raise CliUsageError(f"{label} must contain one JSON object")
        return decoded


def build_parser() -> argparse.ArgumentParser:
    """Build the complete and intentionally closed supported command surface.

    Intent
    ------
    Expose exactly seven commands with explicit state, run, key, and guest argv
    arguments while prohibiting abbreviated options.

    Rationale
    ---------
    One discoverable parser prevents development helpers or ambient state from
    becoming accidental operator contracts.

    Pseudocode
    ----------
    - parser = _JsonArgumentParser(program description)
    - set commands = required subparser collection
    - for command in seven supported commands:
      - @_add_state_root(command parser)
    - @_add_run_and_private_key(launched-run parsers)
    - set command_specific_arguments = exact documented options
    - return parser

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._add_state_root:
      why:
        orchestrates: "Adds the mandatory explicit state authority to every supported command."
    ._add_run_and_private_key:
      why:
        orchestrates: "Adds run identity and private-key authority only to launched-run commands."

    InstantiationsFromRepo
    ----------------------
    ._JsonArgumentParser:
      why:
        constructs: "Constructs the closed parser whose errors flow through JSON transport."
    """
    parser = _JsonArgumentParser(
        prog="isolated-lm-vm.py",
        description="Prepare and control one disposable Ubuntu QEMU/KVM guest.",
    )
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_JsonArgumentParser
    )

    preflight = commands.add_parser("preflight", help="check host prerequisites")
    _add_state_root(preflight)

    prepare_image = commands.add_parser(
        "prepare-image", help="download and authenticate the Ubuntu source image"
    )
    _add_state_root(prepare_image)

    prepare = commands.add_parser("prepare-run", help="create a disposable run")
    _add_state_root(prepare)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--ssh-public-key", required=True)

    start = commands.add_parser("start-run", help="launch and await one prepared run")
    _add_state_root(start)
    _add_run_and_private_key(start)

    execute = commands.add_parser("exec", help="execute an argv in one ready guest")
    _add_state_root(execute)
    _add_run_and_private_key(execute)
    execute.add_argument(
        "guest_argv",
        nargs=argparse.REMAINDER,
        help="non-empty guest argv following an explicit -- separator",
    )

    stop = commands.add_parser("stop-run", help="bounded shutdown of one launched run")
    _add_state_root(stop)
    _add_run_and_private_key(stop)

    status = commands.add_parser("status", help="read one validated run manifest")
    _add_state_root(status)
    status.add_argument("--run-id", required=True)
    return parser


def _add_state_root(parser: argparse.ArgumentParser) -> None:
    """Require the explicit state-root option shared by every subcommand.

    Intent
    ------
    Attach one mandatory absolute-state option to a supported parser.

    Rationale
    ---------
    Commands must never infer VM authority from the checkout, current directory,
    environment, or user home.

    Pseudocode
    ----------
    - set parser_state_root_option = required absolute external directory
    - return none

    Wraps
    -----
    none
    """
    parser.add_argument(
        "--state-root",
        required=True,
        help="absolute external directory for images, manifests, keys, and runs",
    )


def _add_run_and_private_key(parser: argparse.ArgumentParser) -> None:
    """Add the common explicit authority required by launched-run commands.

    Intent
    ------
    Require both selected run identity and supplied SSH private-key path where
    lifecycle control needs them.

    Rationale
    ---------
    Pairing these options makes authority explicit and enables comparison with
    the immutable identity recorded at launch.

    Pseudocode
    ----------
    - set parser_run_identifier_option = required
    - set parser_private_key_option = required
    - return none

    Wraps
    -----
    none
    """
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ssh-private-key", required=True)


def _emit_json(payload: dict[str, object]) -> None:
    """Write exactly one stable compact JSON object to standard output.

    Intent
    ------
    Serialize a command result with deterministic key ordering and no auxiliary
    stdout content.

    Rationale
    ---------
    Automation depends on one parseable object regardless of success or
    structured failure.

    Pseudocode
    ----------
    - set encoded_payload = sorted compact JSON serialization
    - set stdout = encoded_payload plus newline
    - return none

    Wraps
    -----
    none
    """
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _diagnose(message: str) -> None:
    """Keep human-oriented diagnostics off the machine-readable stdout stream.

    Intent
    ------
    Publish one concise operator message only on standard error.

    Rationale
    ---------
    Separating human text preserves stdout as an unambiguous JSON transport for
    scripts and calling agents.

    Pseudocode
    ----------
    - set stderr = message plus newline
    - return none

    Wraps
    -----
    none
    """
    print(message, file=sys.stderr)


def _runtime_paths(raw_root: str) -> RuntimePaths:
    """Validate one canonical absolute state root without creating it.

    Intent
    ------
    Convert explicit operator text into the canonical Task 1 runtime layout only
    when the root contains no symlink or lexical alias.

    Rationale
    ---------
    A single unambiguous filesystem authority keeps every command independent
    of checkout, current directory, environment, and user home.

    Pseudocode
    ----------
    - set supplied_root = path from raw_root
    - if supplied_root is not absolute canonical and real when present:
      - raise CliUsageError(invalid state root)
    - return runtime paths derived from supplied_root

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the operator-input category for noncanonical or unsafe state roots."
    """
    supplied = Path(raw_root)
    if not supplied.is_absolute():
        raise CliUsageError("state root must be absolute")
    resolved = supplied.resolve()
    if supplied != resolved:
        raise CliUsageError("state root must be canonical and must not contain symlinks")
    if supplied.is_symlink() or (supplied.exists() and not supplied.is_dir()):
        raise CliUsageError("state root must be a real directory")
    return RuntimePaths.from_root(supplied)


def _read_regular_text(path: Path, label: str) -> str:
    """Read one regular UTF-8 file through a no-follow descriptor boundary.

    Intent
    ------
    Consume public-key text only from an existing regular final path without
    following a symlink.

    Rationale
    ---------
    Descriptor metadata closes the final-hop redirection and special-file
    boundary before text decoding occurs.

    Pseudocode
    ----------
    - if path is a symlink:
      - raise CliUsageError(redirected text file)
    - set descriptor = no-follow file open
    - if descriptor is not regular:
      - raise CliUsageError(invalid text file)
    - return decoded UTF-8 text

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the structured usage category for unsafe or non-UTF-8 text inputs."
    """
    if path.is_symlink():
        raise CliUsageError(f"{label} must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as error:
        raise CliUsageError(f"{label} not found: {path}") from error
    except OSError as error:
        raise CliUsageError(f"{label} is not safely readable: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CliUsageError(f"{label} must be a regular file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            descriptor = -1
            return source.read()
    except UnicodeError as error:
        raise CliUsageError(f"{label} is not valid UTF-8") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_private_atomic(path: Path, content: str) -> None:
    """Persist one mode-0600 manifest atomically before any result is emitted.

    Intent
    ------
    Publish complete private manifest bytes through a same-directory durable
    replacement under a canonical parent.

    Rationale
    ---------
    Readers must never observe partial state, and a symlinked parent or final
    path must not redirect evidence outside the selected layout.

    Pseudocode
    ----------
    - if parent or destination is noncanonical or symlinked:
      - raise CliUsageError(unsafe manifest path)
    - set temporary_manifest = private file beside destination
    - set temporary_content = content
    - set temporary_durability = synchronized
    - set destination = atomic replacement of temporary_manifest
    - return none

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the structured usage category before writing through an unsafe manifest path."
    """
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.resolve() != parent:
        raise CliUsageError("manifest parent must be a real canonical directory")
    if path.is_symlink():
        raise CliUsageError("manifest path must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            os.chmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _expect_exact_fields(
    data: dict[str, object], expected: frozenset[str], label: str
) -> None:
    """Reject missing and unknown fields instead of guessing at schema drift.

    Intent
    ------
    Require a manifest object to expose exactly one frozen field set.

    Rationale
    ---------
    Silent defaults or ignored unknown fields could reinterpret stale or
    attacker-modified lifecycle authority.

    Pseudocode
    ----------
    - set actual_fields = manifest field names
    - if actual_fields differ from expected fields:
      - raise CliUsageError(schema field mismatch)
    - return none

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the structured usage category with deterministic missing and unknown fields."
    """
    actual = frozenset(data)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise CliUsageError(
            f"{label} fields do not match schema; missing={missing}, unknown={unknown}"
        )


def _expect_string(data: dict[str, object], name: str, label: str) -> str:
    """Return one required nonempty string manifest field.

    Intent
    ------
    Narrow one manifest member to the nonempty string type consumed by later
    schema checks.

    Rationale
    ---------
    JSON nulls, numbers, booleans, and empty strings must not flow into paths,
    digests, identities, or lifecycle names.

    Pseudocode
    ----------
    - set field = manifest member named by name
    - if field is not a nonempty string:
      - raise CliUsageError(invalid string field)
    - return field

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the structured usage category when a required string field has the wrong JSON type."
    """
    value = data[name]
    if not isinstance(value, str) or not value:
        raise CliUsageError(f"{label} field {name!r} must be a nonempty string")
    return value


def _expect_integer(data: dict[str, object], name: str, label: str) -> int:
    """Return one required integer manifest field without accepting booleans.

    Intent
    ------
    Narrow one manifest member to a true JSON integer for resource, size, or
    schema validation.

    Rationale
    ---------
    Python booleans are integer subclasses, but accepting them would corrupt
    numeric manifest semantics.

    Pseudocode
    ----------
    - set field = manifest member named by name
    - if field is not an integer or is boolean:
      - raise CliUsageError(invalid integer field)
    - return field

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the structured usage category when a numeric manifest field has the wrong JSON type."
    """
    value = data[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise CliUsageError(f"{label} field {name!r} must be an integer")
    return value


def _expect_digest(data: dict[str, object], name: str, label: str) -> str:
    """Return one canonical lowercase SHA-256 manifest field.

    Intent
    ------
    Validate the textual type, length, alphabet, and case of one provenance
    digest before constructing a record.

    Rationale
    ---------
    Canonical lowercase form prevents multiple textual identities for the same
    digest and rejects malformed provenance early.

    Pseudocode
    ----------
    - digest = _expect_string(manifest name and label)
    - if digest is not 64 lowercase hexadecimal characters:
      - raise CliUsageError(invalid SHA-256 field)
    - return digest

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    ._expect_string:
      why:
        transforms: "Returns the nonempty text subsequently narrowed to canonical SHA-256 syntax."
    .CliUsageError:
      why:
        raises: "Raises the structured usage category for malformed digest text."
    """
    value = _expect_string(data, name, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise CliUsageError(f"{label} field {name!r} must be a lowercase SHA-256 digest")
    return value


def _expect_utc_timestamp(data: dict[str, object], name: str, label: str) -> datetime:
    """Parse one timezone-aware UTC timestamp from a manifest.

    Intent
    ------
    Convert one required timestamp field into a datetime only when it explicitly
    denotes UTC.

    Rationale
    ---------
    Naive or non-UTC records make provenance and lifecycle ordering ambiguous
    across hosts.

    Pseudocode
    ----------
    - timestamp_text = _expect_string(manifest name and label)
    - set parsed_timestamp = ISO timestamp parse
    - if parsed_timestamp is naive or non-UTC:
      - raise CliUsageError(invalid UTC timestamp)
    - return parsed_timestamp

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    ._expect_string:
      why:
        transforms: "Returns the nonempty timestamp text passed to the ISO parser."
    .CliUsageError:
      why:
        raises: "Raises the structured usage category for malformed, naive, or non-UTC timestamps."
    """
    value = _expect_string(data, name, label)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CliUsageError(f"{label} field {name!r} is not an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise CliUsageError(f"{label} field {name!r} must be in UTC")
    return parsed


def _require_exact_path(
    data: dict[str, object], name: str, expected: Path, label: str
) -> Path:
    """Require one manifest path to equal its canonical state-derived path.

    Intent
    ------
    Convert a manifest string into a Path only when its absolute lexical value
    exactly matches the trusted layout-derived expectation.

    Rationale
    ---------
    Manifest-controlled path aliases or parent traversal must not redirect later
    metadata checks or lifecycle subprocess arguments.

    Pseudocode
    ----------
    - @_expect_string(manifest path field)
    - set manifest_path = path parsed from field
    - if manifest_path is not absolute exact and traversal-free:
      - raise CliUsageError(escaped manifest path)
    - return manifest_path

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._expect_string:
      why:
        validates: "Requires nonempty path text before exact lexical layout comparison."

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the structured usage category for escaped or noncanonical manifest paths."
    """
    value = Path(_expect_string(data, name, label))
    if not value.is_absolute() or value != expected or ".." in value.parts:
        raise CliUsageError(f"{label} field {name!r} escapes or violates the state layout")
    return value


def _load_image_record(paths: RuntimePaths) -> CloudImageRecord:
    """Load the sole canonical source-image manifest for run preparation.

    Intent
    ------
    Read the fixed image manifest and cached-image metadata through retained
    descriptors, then reconstruct only a current approved provenance record.

    Rationale
    ---------
    Frozen fields, URLs, path, digest, timestamp, type, and size prevent stale or
    redirected cache authority from reaching overlay preparation.

    Pseudocode
    ----------
    - @_StateReader(paths.root)
    - set image_manifest = bounded relative manifest read
    - set cached_metadata = relative no-follow image stat
    - @_expect_exact_fields(image_manifest and image schema)
    - source_text = _expect_string(approved source fields)
    - cached_path = _require_exact_path(cached image field)
    - byte_size = _expect_integer(image size field)
    - if cached metadata is not regular with byte_size:
      - raise CliUsageError(stale cached image)
    - digest = _expect_digest(image digest field)
    - retrieved_at = _expect_utc_timestamp(image timestamp field)
    - return authenticated image record

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._StateReader:
      why:
        reads: "Retains root and image directory descriptors for bounded relative manifest and metadata access."
    ._expect_exact_fields:
      why:
        validates: "Requires the frozen Task 2 image-manifest schema before field interpretation."

    InstantiationsFromRepo
    ----------------------
    ._expect_string:
      why:
        transforms: "Returns approved source identifier text compared with fixed Ubuntu constants."
    ._expect_integer:
      why:
        transforms: "Returns schema and byte-size integers used for freshness checks."
    ._require_exact_path:
      why:
        transforms: "Returns the exact state-derived cached image path stored in the record."
    ._expect_digest:
      why:
        transforms: "Returns the canonical authenticated digest carried into the image record."
    ._expect_utc_timestamp:
      why:
        transforms: "Returns the UTC retrieval time carried into the image record."
    .CliUsageError:
      why:
        raises: "Raises the structured usage category for stale constants, size, type, or schema state."
    """
    with _StateReader(paths.root) as reader:
        images_fd = reader.open_directory(reader.root_fd, "images", "images directory")
        data = reader.read_json(images_fd, IMAGE_RECORD_NAME, "source-image manifest")
        cached_metadata = reader.entry_stat(
            images_fd, IMAGE_FILENAME, "cached source image"
        )
    if cached_metadata is None or not stat.S_ISREG(cached_metadata.st_mode):
        raise CliUsageError("cached source image must be a regular non-symlink file")
    _expect_exact_fields(data, _IMAGE_MANIFEST_FIELDS, "source-image manifest")
    schema_version = _expect_integer(data, "schema_version", "source-image manifest")
    if schema_version != 1:
        raise CliUsageError("source-image manifest schema version is stale")
    expected_constants = {
        "image_url": IMAGE_URL,
        "checksums_url": CHECKSUMS_URL,
        "signature_url": SIGNATURE_URL,
        "filename": IMAGE_FILENAME,
    }
    for name, expected in expected_constants.items():
        actual = _expect_string(data, name, "source-image manifest")
        if actual != expected:
            raise CliUsageError(f"source-image manifest field {name!r} is stale")
    cached = _require_exact_path(
        data,
        "cached_path",
        paths.images / IMAGE_FILENAME,
        "source-image manifest",
    )
    byte_size = _expect_integer(data, "byte_size", "source-image manifest")
    if byte_size <= 0 or cached_metadata.st_size != byte_size:
        raise CliUsageError("source-image manifest byte size is stale")
    return CloudImageRecord(
        schema_version=1,
        image_url=IMAGE_URL,
        checksums_url=CHECKSUMS_URL,
        signature_url=SIGNATURE_URL,
        filename=IMAGE_FILENAME,
        verified_source_digest=_expect_digest(
            data, "verified_source_digest", "source-image manifest"
        ),
        byte_size=byte_size,
        retrieved_at=_expect_utc_timestamp(data, "retrieved_at", "source-image manifest"),
        cached_path=cached,
    )


def _load_run_record(paths: RuntimePaths, selected_run_id: str) -> RunRecord:
    """Read and validate exactly one selected run manifest without mutation.

    Intent
    ------
    Reconstruct lifecycle authority only from the selected run's bounded
    manifest and descriptor-relative artifact metadata.

    Rationale
    ---------
    Exact schema, paths, types, launch facts, and rebuilt QEMU argv prevent
    directory scans, symlink redirection, or injected command authority.

    Pseudocode
    ----------
    - set run_id = validated selected identifier
    - @_StateReader(paths.root)
    - set selected_evidence = bounded manifest and artifact reads
    - @_expect_exact_fields(run_manifest and run schema)
    - parsed_paths = _require_exact_path(each layout-owned field)
    - digest = _expect_digest(source digest field)
    - if artifact types or lifecycle are inconsistent:
      - raise CliUsageError(stale run record)
    - set record = reconstructed run authority
    - if QEMU command differs from rebuilt vector:
      - raise CliUsageError(unsafe command record)
    - return record

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._StateReader:
      why:
        reads: "Retains root, runs, and selected-run descriptors while reading manifest and artifact metadata."
    ._expect_exact_fields:
      why:
        validates: "Requires frozen run and nested resource schemas before interpreting authority."
    ._expect_utc_timestamp:
      why:
        validates: "Requires an explicit UTC creation time before preserving its canonical text."

    InstantiationsFromRepo
    ----------------------
    ._expect_string:
      why:
        transforms: "Returns required run identity, user, lifecycle, path, and digest source text."
    ._expect_integer:
      why:
        transforms: "Returns schema and resource integers used in supported-profile checks."
    ._require_exact_path:
      why:
        transforms: "Returns each exact state-derived artifact path carried into the run record."
    ._expect_digest:
      why:
        transforms: "Returns canonical source-image provenance carried into the run record."
    .CliUsageError:
      why:
        raises: "Raises the structured usage category for any stale, escaped, malformed, or unsafe run fact."
    """
    try:
        run_id = validate_run_id(selected_run_id)
    except ValueError as error:
        raise CliUsageError(str(error)) from error
    run_dir = paths.runs / run_id
    manifest = run_dir / "run.json"
    with _StateReader(paths.root) as reader:
        runs_fd = reader.open_directory(reader.root_fd, "runs", "runs directory")
        run_fd = reader.open_directory(runs_fd, run_id, "run directory")
        data = reader.read_json(run_fd, "run.json", "run manifest")
        artifact_metadata = {
            name: reader.entry_stat(
                run_fd,
                filename,
                f"run artifact {name}",
                missing_ok=name in {"qmp_socket", "pid_file"},
            )
            for name, filename in {
                "overlay": "overlay.qcow2",
                "seed_iso": "seed.iso",
                "known_hosts": "known_hosts",
                "serial_log": "serial.log",
                "record_path": "run.json",
                "qmp_socket": "qmp.sock",
                "pid_file": "qemu.pid",
            }.items()
        }
    _expect_exact_fields(data, _RUN_MANIFEST_FIELDS, "run manifest")
    schema_version = _expect_integer(data, "schema_version", "run manifest")
    if schema_version != 1:
        raise CliUsageError("run manifest schema version is stale")
    recorded_run_id = _expect_string(data, "run_id", "run manifest")
    if recorded_run_id != run_id:
        raise CliUsageError("run manifest does not match the selected run ID")
    ssh_user = _expect_string(data, "ssh_user", "run manifest")
    if ssh_user != GUEST_USER:
        raise CliUsageError("run manifest SSH user is stale")
    lifecycle = _expect_string(data, "lifecycle", "run manifest")
    if lifecycle not in _RUN_LIFECYCLES:
        raise CliUsageError("run manifest lifecycle is unknown")

    resources_data = data["resources"]
    if not isinstance(resources_data, dict):
        raise CliUsageError("run manifest resources must be an object")
    _expect_exact_fields(
        resources_data, frozenset({"vcpus", "memory_mib", "disk_gib"}), "resources"
    )
    resources = VmResources(
        vcpus=_expect_integer(resources_data, "vcpus", "resources"),
        memory_mib=_expect_integer(resources_data, "memory_mib", "resources"),
        disk_gib=_expect_integer(resources_data, "disk_gib", "resources"),
    )
    if resources != VmResources():
        raise CliUsageError("run manifest resources do not match the supported profile")

    expected_paths = {
        "run_dir": run_dir,
        "overlay": run_dir / "overlay.qcow2",
        "seed_iso": run_dir / "seed.iso",
        "known_hosts": run_dir / "known_hosts",
        "serial_log": run_dir / "serial.log",
        "qmp_socket": run_dir / "qmp.sock",
        "pid_file": run_dir / "qemu.pid",
        "record_path": manifest,
    }
    parsed_paths = {
        name: _require_exact_path(data, name, expected, "run manifest")
        for name, expected in expected_paths.items()
    }
    for name in ("overlay", "seed_iso", "known_hosts", "serial_log", "record_path"):
        metadata = artifact_metadata[name]
        if metadata is None or not stat.S_ISREG(metadata.st_mode):
            raise CliUsageError(
                f"run artifact {name} must be a regular non-symlink file"
            )
    pid_metadata = artifact_metadata["pid_file"]
    pid_mode = pid_metadata.st_mode if pid_metadata is not None else None
    if pid_mode is not None and not stat.S_ISREG(pid_mode):
        raise CliUsageError("run artifact PID file must be a regular non-symlink file")
    qmp_metadata = artifact_metadata["qmp_socket"]
    qmp_mode = qmp_metadata.st_mode if qmp_metadata is not None else None
    if qmp_mode is not None and not stat.S_ISSOCK(qmp_mode):
        raise CliUsageError("run artifact QMP path must be a Unix socket")

    created = _expect_utc_timestamp(data, "created_at_utc", "run manifest").isoformat()
    digest = _expect_digest(data, "source_image_digest", "run manifest")
    ssh_port_value = data["ssh_port"]
    if ssh_port_value is not None and (
        not isinstance(ssh_port_value, int)
        or isinstance(ssh_port_value, bool)
        or not 1 <= ssh_port_value <= 65535
    ):
        raise CliUsageError("run manifest SSH port is invalid")
    identity_value = data["identity_file"]
    if identity_value is not None and not isinstance(identity_value, str):
        raise CliUsageError("run manifest identity file is invalid")
    identity = Path(identity_value) if isinstance(identity_value, str) else None
    if identity is not None:
        # The identity may intentionally live outside state. Status validates
        # only its recorded lexical shape; exec/stop validate the operator's
        # matching live key before any SSH boundary. Never probe arbitrary host
        # paths merely because a selected manifest records one.
        if not identity.is_absolute() or ".." in identity.parts or "\0" in identity_value:
            raise CliUsageError("run manifest identity file path is invalid")

    command_value = data["qemu_command"]
    if not isinstance(command_value, list) or any(
        not isinstance(argument, str) for argument in command_value
    ):
        raise CliUsageError("run manifest QEMU command must be a string list")
    record = RunRecord(
        schema_version=1,
        run_id=run_id,
        run_dir=parsed_paths["run_dir"],
        resources=resources,
        source_image_digest=digest,
        overlay=parsed_paths["overlay"],
        seed_iso=parsed_paths["seed_iso"],
        known_hosts=parsed_paths["known_hosts"],
        serial_log=parsed_paths["serial_log"],
        qmp_socket=parsed_paths["qmp_socket"],
        pid_file=parsed_paths["pid_file"],
        record_path=parsed_paths["record_path"],
        ssh_user=GUEST_USER,
        created_at_utc=created,
        lifecycle=lifecycle,
        ssh_port=ssh_port_value,
        identity_file=identity,
        qemu_command=tuple(command_value),
    )
    if lifecycle == "prepared":
        if ssh_port_value is not None or identity is not None or command_value:
            raise CliUsageError("prepared run manifest contains stale launch fields")
    else:
        if ssh_port_value is None or identity is None:
            raise CliUsageError("launched run manifest is missing launch fields")
        expected_command = build_qemu_command(record, ssh_port_value)
        if command_value != expected_command:
            raise CliUsageError("run manifest QEMU command is stale or unsafe")
    return record


def _record_result(command: str, record: RunRecord | CloudImageRecord) -> dict[str, object]:
    """Add stable command transport fields to one canonical model record.

    Intent
    ------
    Merge a successful command identity with the model's canonical JSON object.

    Rationale
    ---------
    Reusing model serialization keeps CLI field shapes identical to persisted
    evidence while adding only transport-level status.

    Pseudocode
    ----------
    - set record_payload = parsed canonical record serialization
    - set command_payload = command success fields plus record_payload
    - return command_payload

    Wraps
    -----
    none
    """
    return {"command": command, "ok": True, **json.loads(record.to_json())}


def _report_result(paths: RuntimePaths, report: HostPreflightReport) -> dict[str, object]:
    """Serialize every Task 1 preflight result without dropping failures.

    Intent
    ------
    Publish host identity and each named prerequisite check in one stable
    preflight payload.

    Rationale
    ---------
    Operators need the complete check set to distinguish missing commands,
    unsupported architecture, and KVM permission failures.

    Pseudocode
    ----------
    - set checks = serialized name status and detail for every report check
    - set report_payload = command status root host facts and checks
    - return report_payload

    Wraps
    -----
    none
    """
    return {
        "command": "preflight",
        "ok": report.ok,
        "state_root": str(paths.root),
        "platform": report.platform,
        "machine": report.machine,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in report.checks
        ],
    }


def _provided_identity(raw_identity: str, record: RunRecord) -> Path:
    """Require an explicit private-key path to equal the recorded launch key.

    Intent
    ------
    Canonicalize and validate the operator-supplied private key, then compare it
    exactly with launch authority stored in the selected record.

    Rationale
    ---------
    Exec and stop must not substitute a different valid key or bypass Task 4
    permissions merely because the process or lifecycle state is absent.

    Pseudocode
    ----------
    - set supplied_identity = Task 4 identity validation
    - if supplied_identity differs from recorded identity:
      - raise CliUsageError(identity mismatch)
    - return supplied_identity

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the structured usage category for invalid or mismatched private-key authority."
    """
    try:
        supplied = validate_identity_file(Path(raw_identity))
    except ValueError as error:
        raise CliUsageError(str(error)) from error
    if record.identity_file is None or supplied != record.identity_file:
        raise CliUsageError("SSH private key does not match the recorded identity")
    return supplied


def _exec_argv(remainder: Sequence[str]) -> list[str]:
    """Require the documented separator and a nonempty remote argument vector.

    Intent
    ------
    Remove exactly one literal separator and return the remaining guest
    arguments without shell parsing.

    Rationale
    ---------
    The explicit separator distinguishes CLI options from guest arguments and
    prevents an empty remote command from reaching SSH.

    Pseudocode
    ----------
    - if remainder lacks separator or guest arguments:
      - raise CliUsageError(missing guest arguments)
    - return arguments after separator

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .CliUsageError:
      why:
        raises: "Raises the structured usage category when exec lacks its required separator or argv."
    """
    if not remainder or remainder[0] != "--" or len(remainder) == 1:
        raise CliUsageError("exec requires a nonempty argv after an explicit -- separator")
    return list(remainder[1:])


def _dispatch(args: argparse.Namespace, paths: RuntimePaths) -> int:
    """Delegate one parsed command to Tasks 1 through 4 and emit its result.

    Intent
    ------
    Orchestrate exactly one supported command, loading only required records and
    emitting JSON only after delegated state transitions are durable.

    Rationale
    ---------
    Keeping host, image, guest, and QEMU behavior in their owning modules leaves
    this boundary responsible only for authority checks and stable transport.

    Pseudocode
    ----------
    - set selected_command = parser-controlled command
    - if selected_command requires image authority:
      - image = _load_image_record(paths)
    - if selected_command requires run authority:
      - run = _load_run_record(paths and run identifier)
    - if selected_command is prepare-run:
      - @_read_regular_text(public key)
    - if selected_command is exec or stop:
      - @_provided_identity(key and run)
    - if selected_command is exec:
      - guest_argv = _exec_argv(remainder)
    - set command_result = delegated and durably persisted result
    - @_emit_json(serialized command_result)
    - if guest command failed:
      - @_diagnose(bounded guest status)
    - return documented command status

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._report_result:
      why:
        serializes: "Builds the complete host-preflight payload without dropping failed checks."
    ._write_private_atomic:
      why:
        writes: "Publishes authenticated image provenance before reporting prepare-image success."
    ._read_regular_text:
      why:
        reads: "Consumes a no-follow regular public-key file before guest preparation."
    ._provided_identity:
      why:
        validates: "Compares Task 4 validated private-key authority with the recorded launch identity."
    ._record_result:
      why:
        serializes: "Builds successful command payloads from canonical model serialization."
    ._emit_json:
      why:
        serializes: "Writes the sole compact JSON object for every successful command result."
    ._diagnose:
      why:
        writes: "Keeps concise preflight and guest-exit diagnostics on standard error."

    InstantiationsFromRepo
    ----------------------
    ._load_image_record:
      why:
        constructs: "Constructs validated authenticated-image authority for run preparation."
    ._load_run_record:
      why:
        constructs: "Constructs validated selected-run authority for status and lifecycle commands."
    ._exec_argv:
      why:
        transforms: "Returns the nonempty guest vector following the explicit separator."
    .CliUsageError:
      why:
        raises: "Raises structured input and lifecycle failures before unsupported delegation."
    """
    if args.command == "preflight":
        report = check_host()
        _emit_json(_report_result(paths, report))
        if report.ok:
            return 0
        _diagnose("preflight failed; inspect the JSON checks for details")
        return 1

    if args.command == "prepare-image":
        record = prepare_cloud_image(paths)
        _write_private_atomic(paths.images / IMAGE_RECORD_NAME, record.to_json())
        _emit_json(_record_result(args.command, record))
        return 0

    if args.command == "prepare-run":
        image = _load_image_record(paths)
        public_key_path = Path(args.ssh_public_key)
        if not public_key_path.is_absolute() or public_key_path.resolve() != public_key_path:
            raise CliUsageError("SSH public key must be an absolute canonical path")
        key_text = _read_regular_text(public_key_path, "SSH public key").removesuffix("\n")
        try:
            run_id = validate_run_id(args.run_id)
            render_user_data(key_text)
        except ValueError as error:
            raise CliUsageError(str(error)) from error
        record = prepare_run(paths, image, run_id, key_text, VmResources())
        _emit_json(_record_result(args.command, record))
        return 0

    if args.command == "status":
        record = _load_run_record(paths, args.run_id)
        _emit_json(_record_result(args.command, record))
        return 0

    record = _load_run_record(paths, args.run_id)
    if args.command == "start-run":
        if record.lifecycle != "prepared":
            raise CliUsageError("start-run requires lifecycle prepared")
        try:
            identity = validate_identity_file(Path(args.ssh_private_key))
            running = start_run(record, identity)
        except ValueError as error:
            raise CliUsageError(str(error)) from error
        ready = wait_for_ssh(running)
        _emit_json(_record_result(args.command, ready))
        return 0

    if args.command == "exec":
        _provided_identity(args.ssh_private_key, record)
        if record.lifecycle != "ready":
            raise CliUsageError("exec requires lifecycle ready")
        guest_argv = _exec_argv(args.guest_argv)
        try:
            command = build_ssh_command(record, guest_argv)
        except ValueError as error:
            raise CliUsageError(str(error)) from error
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
        )
        guest_stdout = completed.stdout.decode("utf-8", errors="replace")
        guest_stderr = completed.stderr.decode("utf-8", errors="replace")
        ok = completed.returncode == 0
        _emit_json(
            {
                "command": "exec",
                "ok": ok,
                "run_id": record.run_id,
                "guest_exit_code": completed.returncode,
                "stdout": guest_stdout,
                "stderr": guest_stderr,
            }
        )
        if not ok:
            _diagnose(f"guest command exited with status {completed.returncode}")
            return completed.returncode if 1 <= completed.returncode <= 255 else 1
        return 0

    if args.command == "stop-run":
        _provided_identity(args.ssh_private_key, record)
        if record.lifecycle not in {"launch-failed", "running", "ready", "stopped"}:
            raise CliUsageError("stop-run requires a launched lifecycle")
        try:
            stopped = stop_run(record)
        except ValueError as error:
            raise CliUsageError(str(error)) from error
        _emit_json(_record_result(args.command, stopped))
        return 0
    raise AssertionError(f"unsupported parser command: {args.command}")


def _failure(command: str, error: BaseException, exit_code: int) -> int:
    """Emit one structured expected failure plus a concise human diagnostic.

    Intent
    ------
    Convert an expected exception into matching JSON and stderr messages, then
    return the caller-selected documented exit status.

    Rationale
    ---------
    One shared failure boundary keeps machine and human transports consistent
    while applying sanitization before either receives exception text.

    Pseudocode
    ----------
    - message = _safe_error_message(error)
    - @_emit_json(command failure and message)
    - @_diagnose(command failure and message)
    - return exit_code

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._emit_json:
      why:
        serializes: "Writes the sole structured failure object to standard output."
    ._diagnose:
      why:
        writes: "Writes the same bounded failure context to standard error for operators."

    InstantiationsFromRepo
    ----------------------
    ._safe_error_message:
      why:
        transforms: "Returns bounded sanitized exception text shared by both transports."
    """
    message = _safe_error_message(error)
    _emit_json({"command": command, "ok": False, "error": message})
    _diagnose(f"{command} failed: {message}")
    return exit_code


def _safe_error_message(error: BaseException) -> str:
    """Return one bounded diagnostic without commands or private-key material.

    Intent
    ------
    Select captured stderr for child-process failures and ordinary exception text
    otherwise, then apply the appropriate redaction strength.

    Rationale
    ---------
    CalledProcessError stringification can expose full commands, while arbitrary
    usage text can be oversized or contain pasted PEM material.

    Pseudocode
    ----------
    - if error is not a captured process failure:
      - message = _bounded_diagnostic_detail(error text)
      - return message or exception type
    - set status_message = external command status
    - detail = _bounded_diagnostic_detail(captured stderr)
    - if detail is present:
      - return status_message plus detail
    - return status_message

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    ._bounded_diagnostic_detail:
      why:
        transforms: "Bounds, decodes, sanitizes, and redacts diagnostic text before transport."

    """
    if not isinstance(error, subprocess.CalledProcessError):
        message = _bounded_diagnostic_detail(
            str(error), redact_private_key_lines=False
        )
        return message or type(error).__name__
    message = f"external command failed with status {error.returncode}"
    detail = _bounded_diagnostic_detail(error.stderr)
    return f"{message}: {detail}" if detail else message


def _bounded_diagnostic_detail(
    value: object, *, redact_private_key_lines: bool = True
) -> str:
    """Decode diagnostic text with replacement, redact key blocks, and bound it.

    Intent
    ------
    Normalize bytes or text into printable diagnostic content, remove PEM blocks
    and optional key-related lines, and cap encoded size.

    Rationale
    ---------
    External tools and parser input are untrusted transport content; invalid
    UTF-8, controls, secrets, and unbounded output must not escape in errors.

    Pseudocode
    ----------
    - if diagnostic input is absent or unsupported:
      - return empty text
    - set decoded = UTF-8 replacement decode or supplied text
    - for line in decoded lines:
      - if line begins or continues private-key material:
        - set redacted_lines = redacted_lines plus marker
      - else:
        - set redacted_lines = redacted_lines plus printable characters
    - set encoded = stripped redacted text as UTF-8
    - if encoded exceeds diagnostic bound:
      - return bounded prefix plus truncation marker
    - return encoded text

    Wraps
    -----
    none
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", errors="replace")
    elif isinstance(value, str):
        decoded = value
    else:
        return ""
    redacted: list[str] = []
    inside_private_key = False
    for line in decoded.splitlines(keepends=True):
        lowered = line.lower()
        if "begin " in lowered and "private key" in lowered:
            if not inside_private_key:
                redacted.append("[redacted private-key material]\n")
            inside_private_key = True
            continue
        if inside_private_key:
            if "end " in lowered and "private key" in lowered:
                inside_private_key = False
            continue
        if redact_private_key_lines and "private key" in lowered:
            redacted.append("[redacted private-key material]\n")
            continue
        redacted.append(
            "".join(
                character
                if character in "\n\t" or character.isprintable()
                else "\ufffd"
                for character in line
            )
        )
    encoded = "".join(redacted).strip().encode("utf-8")
    if len(encoded) <= _MAX_DIAGNOSTIC_BYTES:
        return encoded.decode("utf-8", errors="replace")
    prefix = encoded[:_MAX_DIAGNOSTIC_BYTES].decode("utf-8", errors="replace")
    return prefix + "...[truncated]"


def main(argv: Sequence[str] | None = None) -> int:
    """Run one supported command and return its documented process exit status.

    Intent
    ------
    Parse explicit input, validate the state root, dispatch one command, and map
    only expected failure categories into structured transport.

    Rationale
    ---------
    Narrow exception mapping preserves programming defects such as unexpected
    ValueError while keeping operator, filesystem, subprocess, and timeout
    failures stable.

    Pseudocode
    ----------
    - parser = build_parser()
    - set parsed_arguments = parser result for supplied argv
    - paths = _runtime_paths(parsed_root)
    - command_status = _dispatch(parsed arguments and paths)
    - if structured usage failure occurs:
      - return _failure(command error and status two)
    - if expected operation failure occurs:
      - return _failure(command error and status one)
    - return command_status

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .build_parser:
      why:
        constructs: "Constructs the closed parser used for this invocation."
    ._runtime_paths:
      why:
        constructs: "Constructs the canonical runtime layout from explicit state-root text."
    ._dispatch:
      why:
        transforms: "Returns the selected command's documented process status after transport."
    ._failure:
      why:
        transforms: "Returns status one or two after emitting a bounded structured failure."
    """
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    command = raw_argv[0] if raw_argv else "arguments"
    try:
        parser = build_parser()
        args = parser.parse_args(raw_argv)
        command = args.command
        paths = _runtime_paths(args.state_root)
        return _dispatch(args, paths)
    except CliUsageError as error:
        return _failure(command, error, 2)
    except (FileNotFoundError, FileExistsError, PermissionError) as error:
        return _failure(command, error, 1)
    except (subprocess.SubprocessError, TimeoutError, RuntimeError, OSError) as error:
        return _failure(command, error, 1)


def _exit() -> NoReturn:
    """Translate main's integer contract into the process exit status.

    Intent
    ------
    Terminate the thin executable wrapper with exactly the orchestration status.

    Rationale
    ---------
    Keeping process termination separate lets tests call main directly without
    catching SystemExit while the script preserves shell semantics.

    Pseudocode
    ----------
    - set exit_status = main result for process arguments
    - raise process exit with exit_status

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    .main:
      why:
        orchestrates: "Runs the complete CLI and supplies its returned integer to process termination."
    """
    raise SystemExit(main())
