"""Supported JSON orchestration for the isolated language-model VM harness."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import NoReturn, Sequence

from test_support.isolated_lm.guest import GUEST_USER, prepare_run, validate_run_id
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
    wait_for_ssh,
)


IMAGE_RECORD_NAME = "source-image.json"
"""Canonical manifest name for the authenticated Ubuntu source image."""

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
    """A rejected operator input or stale local manifest."""


class _JsonArgumentParser(argparse.ArgumentParser):
    """Raise parser errors so ``main`` can preserve the JSON failure transport."""

    def error(self, message: str) -> NoReturn:
        """Reject malformed arguments without argparse writing mixed output."""
        raise CliUsageError(message)


def build_parser() -> argparse.ArgumentParser:
    """Build the complete and intentionally closed supported command surface.

    Rationale
    ---------
    Operators need one discoverable interface whose commands always name an
    external state root. Keeping parser construction separate also lets tests
    prove that no accidental development-only subcommand becomes supported.

    Pseudocode
    ----------
    - create one required subparser set
    - add an explicit required state root to every command
    - add only the run/key/remote-argv inputs consumed by the selected command
    - return the parser without reading environment variables or filesystem state

    Call boundary
    -------------
    ``main`` consumes the parsed namespace; the repository wrapper imports and
    calls ``main`` without reproducing any parser or orchestration logic.
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
    """Require the explicit state-root option shared by every subcommand."""
    parser.add_argument(
        "--state-root",
        required=True,
        help="absolute external directory for images, manifests, keys, and runs",
    )


def _add_run_and_private_key(parser: argparse.ArgumentParser) -> None:
    """Add the common explicit authority required by launched-run commands."""
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ssh-private-key", required=True)


def _emit_json(payload: dict[str, object]) -> None:
    """Write exactly one stable compact JSON object to standard output."""
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _diagnose(message: str) -> None:
    """Keep human-oriented diagnostics off the machine-readable stdout stream."""
    print(message, file=sys.stderr)


def _runtime_paths(raw_root: str) -> RuntimePaths:
    """Validate one canonical absolute state root without creating it.

    Rationale
    ---------
    Every command must remain independent of the checkout and current working
    directory. Rejecting symlinked or lexically noncanonical roots also gives
    later containment checks a single unambiguous filesystem authority.

    Pseudocode
    ----------
    - require an absolute path
    - resolve it without creating it and require the supplied spelling to agree
    - when present, require a real directory rather than a symlink or file
    - derive all runtime descendants through the Task 1 model

    Call boundary
    -------------
    ``main`` calls this before dispatch. Read-only commands perform no mkdir;
    Task 2 or Task 3 creates only the descendants that it owns.
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
    """Read one regular file through a no-follow descriptor boundary."""
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


def _read_json_manifest(path: Path, label: str) -> dict[str, object]:
    """Decode one no-follow UTF-8 manifest and require a JSON object."""
    try:
        decoded = json.loads(_read_regular_text(path, label))
    except json.JSONDecodeError as error:
        raise CliUsageError(f"{label} is corrupt JSON") from error
    if not isinstance(decoded, dict):
        raise CliUsageError(f"{label} must contain one JSON object")
    return decoded


def _write_private_atomic(path: Path, content: str) -> None:
    """Persist one mode-0600 manifest atomically before any result is emitted."""
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
    """Reject missing and unknown fields instead of guessing at schema drift."""
    actual = frozenset(data)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise CliUsageError(
            f"{label} fields do not match schema; missing={missing}, unknown={unknown}"
        )


def _expect_string(data: dict[str, object], name: str, label: str) -> str:
    """Return one required nonempty string manifest field."""
    value = data[name]
    if not isinstance(value, str) or not value:
        raise CliUsageError(f"{label} field {name!r} must be a nonempty string")
    return value


def _expect_integer(data: dict[str, object], name: str, label: str) -> int:
    """Return one required integer manifest field without accepting booleans."""
    value = data[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise CliUsageError(f"{label} field {name!r} must be an integer")
    return value


def _expect_digest(data: dict[str, object], name: str, label: str) -> str:
    """Return one canonical lowercase SHA-256 field."""
    value = _expect_string(data, name, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise CliUsageError(f"{label} field {name!r} must be a lowercase SHA-256 digest")
    return value


def _expect_utc_timestamp(data: dict[str, object], name: str, label: str) -> datetime:
    """Parse one timezone-aware UTC timestamp from a manifest."""
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
    """Require one manifest path to equal its canonical state-root-derived path."""
    value = Path(_expect_string(data, name, label))
    if not value.is_absolute() or value != expected or value.resolve() != expected:
        raise CliUsageError(f"{label} field {name!r} escapes or violates the state layout")
    return value


def _require_real_directory(path: Path, label: str) -> None:
    """Require one existing canonical directory without following a symlink hop."""
    if path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise CliUsageError(f"{label} must be a real canonical directory")


def _require_artifact(path: Path, label: str) -> None:
    """Require one existing regular non-symlink run artifact."""
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise CliUsageError(f"{label} is missing, stale, or a symlink")


def _load_image_record(paths: RuntimePaths) -> CloudImageRecord:
    """Load the sole canonical source-image manifest for run preparation.

    The loader validates the frozen Task 2 schema, approved source identifiers,
    canonical cached path, and current byte size. Task 3 subsequently rehashes
    the cached image before creating an overlay, so this orchestration layer
    neither duplicates nor weakens the authenticated-image boundary.
    """
    manifest = paths.images / IMAGE_RECORD_NAME
    data = _read_json_manifest(manifest, "source-image manifest")
    _expect_exact_fields(data, _IMAGE_MANIFEST_FIELDS, "source-image manifest")
    if _expect_integer(data, "schema_version", "source-image manifest") != 1:
        raise CliUsageError("source-image manifest schema version is stale")
    expected_constants = {
        "image_url": IMAGE_URL,
        "checksums_url": CHECKSUMS_URL,
        "signature_url": SIGNATURE_URL,
        "filename": IMAGE_FILENAME,
    }
    for name, expected in expected_constants.items():
        if _expect_string(data, name, "source-image manifest") != expected:
            raise CliUsageError(f"source-image manifest field {name!r} is stale")
    cached = _require_exact_path(
        data,
        "cached_path",
        paths.images / IMAGE_FILENAME,
        "source-image manifest",
    )
    _require_artifact(cached, "cached source image")
    byte_size = _expect_integer(data, "byte_size", "source-image manifest")
    if byte_size <= 0 or cached.stat().st_size != byte_size:
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

    Rationale
    ---------
    Lifecycle commands must not reconstruct authority from directory scans, and
    ``status`` must never repair a malformed record. Exact schema and path checks
    prevent a selected manifest from redirecting later reads or QEMU/SSH actions
    through symlinks, escape paths, or injected command-vector fields.

    Pseudocode
    ----------
    - validate the selected run ID and derive its one exact manifest path
    - read only that no-follow manifest and require the frozen schema
    - rebuild all state-owned paths from the state root and compare them exactly
    - validate lifecycle-dependent launch fields and exact QEMU argv
    - return the immutable Task 3/4 record without writing any file

    Call boundary
    -------------
    ``status`` serializes this record directly. Start, exec, and stop consume the
    same validated record before delegating to Task 4 lifecycle functions.
    """
    run_id = validate_run_id(selected_run_id)
    run_dir = paths.runs / run_id
    manifest = run_dir / "run.json"
    if not manifest.exists() and not manifest.is_symlink():
        raise CliUsageError(f"run manifest not found: {manifest}")
    if paths.root.exists():
        _require_real_directory(paths.root, "state root")
    _require_real_directory(paths.runs, "runs directory")
    _require_real_directory(run_dir, "run directory")
    data = _read_json_manifest(manifest, "run manifest")
    _expect_exact_fields(data, _RUN_MANIFEST_FIELDS, "run manifest")
    if _expect_integer(data, "schema_version", "run manifest") != 1:
        raise CliUsageError("run manifest schema version is stale")
    if _expect_string(data, "run_id", "run manifest") != run_id:
        raise CliUsageError("run manifest does not match the selected run ID")
    if _expect_string(data, "ssh_user", "run manifest") != GUEST_USER:
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
        _require_artifact(parsed_paths[name], f"run artifact {name}")
    for name in ("qmp_socket", "pid_file"):
        if parsed_paths[name].is_symlink():
            raise CliUsageError(f"run artifact {name} must not be a symlink")

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
    """Add stable command transport fields to one canonical model record."""
    return {"command": command, "ok": True, **json.loads(record.to_json())}


def _report_result(paths: RuntimePaths, report: HostPreflightReport) -> dict[str, object]:
    """Serialize every Task 1 preflight result without dropping failures."""
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
    """Require an explicit private-key path to equal the recorded launch key."""
    supplied = Path(raw_identity)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise CliUsageError("SSH private key must be an absolute non-symlink path")
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as error:
        raise CliUsageError("SSH private key does not exist") from error
    if resolved != supplied or not supplied.is_file():
        raise CliUsageError("SSH private key must be a resolved regular file")
    if record.identity_file is None or supplied != record.identity_file:
        raise CliUsageError("SSH private key does not match the recorded identity")
    return supplied


def _exec_argv(remainder: Sequence[str]) -> list[str]:
    """Require the documented separator and a nonempty remote argument vector."""
    if not remainder or remainder[0] != "--" or len(remainder) == 1:
        raise CliUsageError("exec requires a nonempty argv after an explicit -- separator")
    return list(remainder[1:])


def _dispatch(args: argparse.Namespace, paths: RuntimePaths) -> int:
    """Delegate one parsed command to Tasks 1--4 and emit its persisted result.

    Rationale
    ---------
    The CLI is orchestration only: host checks, image acquisition, run creation,
    and VM lifecycle stay owned by their established modules. State-changing
    delegates persist their model records before this function emits JSON.

    Pseudocode
    ----------
    - dispatch exactly one of the seven parser-controlled command names
    - load only canonical manifests needed by that command
    - persist an image record or rely on Task 3/4 atomic run-record transitions
    - emit one JSON result; for exec, include captured guest streams and status

    Call boundary
    -------------
    ``main`` validates the state root and maps expected failures to structured
    JSON. This function never reads a private key and never invokes a local shell.
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
        record = prepare_run(paths, image, args.run_id, key_text, VmResources())
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
        running = start_run(record, Path(args.ssh_private_key))
        ready = wait_for_ssh(running)
        _emit_json(_record_result(args.command, ready))
        return 0

    if args.command == "exec":
        if record.lifecycle != "ready":
            raise CliUsageError("exec requires lifecycle ready")
        _provided_identity(args.ssh_private_key, record)
        guest_argv = _exec_argv(args.guest_argv)
        command = build_ssh_command(record, guest_argv)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        ok = completed.returncode == 0
        _emit_json(
            {
                "command": "exec",
                "ok": ok,
                "run_id": record.run_id,
                "guest_exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if not ok:
            _diagnose(f"guest command exited with status {completed.returncode}")
            return completed.returncode if 1 <= completed.returncode <= 255 else 1
        return 0

    if args.command == "stop-run":
        if record.lifecycle not in {"launch-failed", "running", "ready", "stopped"}:
            raise CliUsageError("stop-run requires a launched lifecycle")
        _provided_identity(args.ssh_private_key, record)
        stopped = stop_run(record)
        _emit_json(_record_result(args.command, stopped))
        return 0
    raise AssertionError(f"unsupported parser command: {args.command}")


def _failure(command: str, error: BaseException, exit_code: int) -> int:
    """Emit one structured expected failure plus a concise human diagnostic."""
    message = str(error) or type(error).__name__
    _emit_json({"command": command, "ok": False, "error": message})
    _diagnose(f"{command} failed: {message}")
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Run one supported command and return its documented process exit status.

    Successful orchestration and guest exit zero return 0. A failed preflight or
    external lifecycle operation returns 1. Invalid operator input, a missing or
    stale manifest, and a wrong lifecycle return 2. ``exec`` returns the remote
    SSH/guest status when it is in 1..255, while always publishing that status
    and both captured streams in its JSON result.
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
    except ValueError as error:
        return _failure(command, error, 2)
    except (FileNotFoundError, FileExistsError, PermissionError) as error:
        return _failure(command, error, 1)
    except (subprocess.SubprocessError, TimeoutError, RuntimeError, OSError) as error:
        return _failure(command, error, 1)


def _exit() -> NoReturn:
    """Translate ``main``'s integer contract into the process exit status."""
    raise SystemExit(main())
