"""Contract tests for the supported isolated-VM command-line interface."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_support.isolated_lm import cli
from test_support.isolated_lm.cli import build_parser, main
from test_support.isolated_lm.host import CheckResult, HostPreflightReport
from test_support.isolated_lm.model import CloudImageRecord, RunRecord, VmResources


SUPPORTED_COMMANDS = {
    "preflight",
    "prepare-image",
    "prepare-run",
    "start-run",
    "exec",
    "stop-run",
    "status",
}


def _record_files(state_root: Path, run_id: str = "manual-001") -> RunRecord:
    """Create one truthful prepared manifest and its required artifacts."""
    run_dir = state_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    for name in ("overlay.qcow2", "seed.iso", "known_hosts", "serial.log"):
        (run_dir / name).write_bytes(b"artifact")
    record = RunRecord(
        schema_version=1,
        run_id=run_id,
        run_dir=run_dir,
        resources=VmResources(),
        source_image_digest="a" * 64,
        overlay=run_dir / "overlay.qcow2",
        seed_iso=run_dir / "seed.iso",
        known_hosts=run_dir / "known_hosts",
        serial_log=run_dir / "serial.log",
        qmp_socket=run_dir / "qmp.sock",
        pid_file=run_dir / "qemu.pid",
        record_path=run_dir / "run.json",
        ssh_user="famulus-test",
        created_at_utc="2026-08-11T12:00:00+00:00",
        lifecycle="prepared",
    )
    record.record_path.write_text(record.to_json(), encoding="utf-8")
    return record


def _ready_record(state_root: Path, identity: Path) -> RunRecord:
    """Extend a prepared fixture with the canonical recorded launch facts."""
    identity.write_text("PRIVATE TEST KEY\n", encoding="utf-8")
    identity.chmod(0o600)
    record = replace(
        _record_files(state_root),
        lifecycle="ready",
        ssh_port=40222,
        identity_file=identity,
    )
    record = replace(record, qemu_command=tuple(cli.build_qemu_command(record, 40222)))
    record.pid_file.write_text("999999\n", encoding="ascii")
    record.record_path.write_text(record.to_json(), encoding="utf-8")
    return record


def _json_stdout(capsys: pytest.CaptureFixture[str]) -> tuple[dict[str, object], str]:
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def _tree_state(root: Path) -> dict[str, tuple[int, int, int]]:
    """Capture mutation-relevant metadata without relying on access times."""
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mode,
            path.lstat().st_mtime_ns,
            path.lstat().st_size,
        )
        for path in sorted(root.rglob("*"))
    }


def _replace_run_with_equivalent_directory(run_dir: Path, replacement: Path) -> None:
    """Replace one selected run with a different inode and equivalent artifacts."""
    run_dir.rename(replacement)
    run_dir.mkdir()
    for artifact in replacement.iterdir():
        if artifact.is_file():
            (run_dir / artifact.name).write_bytes(artifact.read_bytes())


def test_parser_exposes_only_supported_commands() -> None:
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(subparsers.choices) == SUPPORTED_COMMANDS


def test_source_image_manifest_replace_fsyncs_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make authenticated source-image manifest publication directory-durable."""
    destination = tmp_path / "source-image.json"
    events: list[str] = []
    real_fsync = cli.os.fsync
    real_replace = cli.os.replace

    def record_fsync(descriptor: int) -> None:
        events.append(
            "directory-fsync"
            if stat.S_ISDIR(os.fstat(descriptor).st_mode)
            else "file-fsync"
        )
        real_fsync(descriptor)

    def record_replace(source: Path, target: Path) -> None:
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(cli.os, "fsync", record_fsync)
    monkeypatch.setattr(cli.os, "replace", record_replace)
    cli._write_private_atomic(destination, "{}\n")

    assert events == ["file-fsync", "replace", "directory-fsync"]


def test_parser_rejects_abbreviated_state_root_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep every supported option spelling explicit and stable."""
    assert main(["status", "--state", str(tmp_path), "--run-id", "manual-001"]) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "--state" in str(payload["error"])
    assert diagnostic


def test_unexpected_value_error_is_not_classified_as_operator_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not hide internal defects under the structured usage-error contract."""
    monkeypatch.setattr(
        cli, "_dispatch", lambda args, paths: (_ for _ in ()).throw(ValueError("bug"))
    )

    with pytest.raises(ValueError, match="bug"):
        main(["preflight", "--state-root", str(tmp_path)])


@pytest.mark.parametrize("command", sorted(SUPPORTED_COMMANDS))
def test_every_command_requires_explicit_absolute_state_root(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    suffix_by_command = {
        "prepare-run": ["--run-id", "manual-001", "--ssh-public-key", "/key.pub"],
        "start-run": ["--run-id", "manual-001", "--ssh-private-key", "/key"],
        "exec": [
            "--run-id",
            "manual-001",
            "--ssh-private-key",
            "/key",
            "--",
            "true",
        ],
        "stop-run": ["--run-id", "manual-001", "--ssh-private-key", "/key"],
        "status": ["--run-id", "manual-001"],
    }
    suffix = suffix_by_command.get(command, [])
    assert main([command, "--state-root", "relative-state", *suffix]) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "absolute" in str(payload["error"])
    assert diagnostic


def test_state_reader_opens_each_state_directory_without_following_symlinks(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    record = _record_files(state_root)
    real_open = os.open
    calls: list[tuple[object, int, object]] = []

    def recording_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        calls.append((path, flags, kwargs.get("dir_fd")))
        return real_open(path, flags, *args, **kwargs)

    with cli._StateReader(state_root, open_file=recording_open) as reader:
        runs_fd = reader.open_directory(reader.root_fd, "runs", "runs directory")
        run_fd = reader.open_directory(runs_fd, record.run_id, "run directory")
        payload = reader.read_json(run_fd, "run.json", "run manifest")

    assert payload["run_id"] == record.run_id
    assert calls[0][0] == "/"
    assert calls[0][2] is None
    directory_calls = [call for call in calls if call[1] & os.O_DIRECTORY]
    assert all(flags & os.O_NOFOLLOW for _, flags, _ in directory_calls)
    runs_call = next(call for call in directory_calls if call[0] == "runs")
    assert runs_call[2] == reader.root_fd


def test_state_reader_rejects_a_symlinked_state_root_ancestor(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    state_root = real_parent / "state"
    _record_files(state_root)
    alias_parent = tmp_path / "alias-parent"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(cli.CliUsageError, match="real directory"):
        with cli._StateReader(alias_parent / "state"):
            pass


def test_status_rejects_a_symlinked_state_root_ancestor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_root = tmp_path / "state"
    record = _record_files(state_root)
    alias = tmp_path / "state-alias"
    alias.symlink_to(state_root, target_is_directory=True)

    assert main(
        ["status", "--state-root", str(alias), "--run-id", record.run_id]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "symlink" in (str(payload["error"]) + diagnostic).lower()


def test_failed_preflight_emits_json_and_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = HostPreflightReport(
        platform="Linux-test",
        machine="x86_64",
        checks=(CheckResult("kvm:read-write", False, "permission denied"),),
    )
    monkeypatch.setattr(cli, "check_host", lambda: report)

    assert main(["preflight", "--state-root", str(tmp_path)]) == 1
    payload, diagnostic = _json_stdout(capsys)
    assert payload == {
        "checks": [
            {"detail": "permission denied", "name": "kvm:read-write", "ok": False}
        ],
        "command": "preflight",
        "machine": "x86_64",
        "ok": False,
        "platform": "Linux-test",
        "state_root": str(tmp_path),
    }
    assert "preflight failed" in diagnostic
    assert list(tmp_path.iterdir()) == []


def test_prepare_image_persists_manifest_before_emitting_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    cached = state_root / "images" / "noble-server-cloudimg-amd64.img"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"image")
    record = CloudImageRecord(
        schema_version=1,
        image_url=cli.IMAGE_URL,
        checksums_url=cli.CHECKSUMS_URL,
        signature_url=cli.SIGNATURE_URL,
        filename=cli.IMAGE_FILENAME,
        verified_source_digest="a" * 64,
        byte_size=5,
        retrieved_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
        cached_path=cached,
    )
    monkeypatch.setattr(cli, "prepare_cloud_image", lambda paths: record)
    emitted: list[dict[str, object]] = []

    def capture(payload: dict[str, object]) -> None:
        assert (state_root / "images" / "source-image.json").is_file()
        emitted.append(payload)

    monkeypatch.setattr(cli, "_emit_json", capture)

    assert main(["prepare-image", "--state-root", str(state_root)]) == 0
    assert emitted == [{"command": "prepare-image", "ok": True, **json.loads(record.to_json())}]
    manifest = state_root / "images" / "source-image.json"
    assert manifest.read_text(encoding="utf-8") == record.to_json()
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr().out == ""


def test_prepare_run_loads_image_and_persists_run_before_emitting_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    cached = state_root / "images" / cli.IMAGE_FILENAME
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"image")
    image = CloudImageRecord(
        schema_version=1,
        image_url=cli.IMAGE_URL,
        checksums_url=cli.CHECKSUMS_URL,
        signature_url=cli.SIGNATURE_URL,
        filename=cli.IMAGE_FILENAME,
        verified_source_digest="a" * 64,
        byte_size=5,
        retrieved_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
        cached_path=cached,
    )
    (cached.parent / "source-image.json").write_text(image.to_json(), encoding="utf-8")
    public_key = tmp_path / "isolated-lm.pub"
    public_key.write_text("ssh-ed25519 AAAATEST isolated-lm\n", encoding="utf-8")
    calls: list[tuple[object, ...]] = []

    def fake_prepare(
        paths: object,
        loaded_image: CloudImageRecord,
        run_id: str,
        key: str,
        resources: VmResources,
    ) -> RunRecord:
        calls.append((paths, loaded_image, run_id, key, resources))
        return _record_files(state_root, run_id)

    def capture(payload: dict[str, object]) -> None:
        assert (state_root / "runs" / "manual-001" / "run.json").is_file()
        assert payload["lifecycle"] == "prepared"

    monkeypatch.setattr(cli, "prepare_run", fake_prepare)
    monkeypatch.setattr(cli, "_emit_json", capture)

    assert main(
        [
            "prepare-run",
            "--state-root",
            str(state_root),
            "--run-id",
            "manual-001",
            "--ssh-public-key",
            str(public_key),
        ]
    ) == 0
    assert calls[0][1:] == (
        image,
        "manual-001",
        "ssh-ed25519 AAAATEST isolated-lm",
        VmResources(),
    )


def test_status_reads_only_selected_manifest_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_root = tmp_path / "state"
    selected = _record_files(state_root)
    other = state_root / "runs" / "other" / "run.json"
    other.parent.mkdir()
    other.symlink_to(tmp_path / "must-not-be-read")
    before = _tree_state(state_root)

    assert main(
        ["status", "--state-root", str(state_root), "--run-id", selected.run_id]
    ) == 0
    payload, diagnostic = _json_stdout(capsys)

    assert payload["command"] == "status"
    assert payload["ok"] is True
    assert payload["run_id"] == selected.run_id
    assert payload["lifecycle"] == "prepared"
    assert diagnostic == ""
    assert _tree_state(state_root) == before


def test_status_accepts_crash_recoverable_launching_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep status usable after launch facts persist but running does not."""
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    identity.write_text("PRIVATE TEST KEY\n", encoding="utf-8")
    identity.chmod(0o600)
    record = replace(
        _record_files(state_root),
        lifecycle="launching",
        ssh_port=40222,
        identity_file=identity,
    )
    record = replace(record, qemu_command=tuple(cli.build_qemu_command(record, 40222)))
    record.record_path.write_text(record.to_json(), encoding="utf-8")

    assert main(
        ["status", "--state-root", str(state_root), "--run-id", record.run_id]
    ) == 0
    payload, diagnostic = _json_stdout(capsys)
    assert payload["lifecycle"] == "launching"
    assert diagnostic == ""


def test_status_does_not_access_recorded_identity_outside_state_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_root = tmp_path / "state"
    identity = tmp_path / "external-identity"
    record = _ready_record(state_root, identity)
    identity.unlink()

    assert main(
        ["status", "--state-root", str(state_root), "--run-id", record.run_id]
    ) == 0
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is True
    assert payload["identity_file"] == str(identity)
    assert diagnostic == ""


def test_parser_failures_use_structured_json_transport(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["status", "--run-id", "manual-001"]) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["command"] == "status"
    assert payload["ok"] is False
    assert "--state-root" in str(payload["error"])
    assert diagnostic


@pytest.mark.parametrize("violation", ["manifest-symlink", "artifact-symlink", "escape"])
def test_status_rejects_symlink_and_escape_violations(
    tmp_path: Path,
    violation: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    record = _record_files(state_root)
    outside = tmp_path / "outside"
    outside.write_text("PRIVATE OUTSIDE CONTENT", encoding="utf-8")
    if violation == "manifest-symlink":
        safe_manifest = tmp_path / "safe-run.json"
        safe_manifest.write_text(record.to_json(), encoding="utf-8")
        record.record_path.unlink()
        record.record_path.symlink_to(safe_manifest)
    elif violation == "artifact-symlink":
        record.overlay.unlink()
        record.overlay.symlink_to(outside)
    else:
        payload = json.loads(record.to_json())
        payload["overlay"] = str(outside)
        record.record_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(
        ["status", "--state-root", str(state_root), "--run-id", record.run_id]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert diagnostic
    assert "PRIVATE OUTSIDE CONTENT" not in json.dumps(payload)
    assert "PRIVATE OUTSIDE CONTENT" not in diagnostic


@pytest.mark.parametrize("artifact", ["pid-fifo", "qmp-regular"])
def test_status_rejects_wrong_runtime_artifact_types(
    tmp_path: Path,
    artifact: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    if artifact == "pid-fifo":
        record.pid_file.unlink()
        os.mkfifo(record.pid_file)
    else:
        record.qmp_socket.unlink(missing_ok=True)
        record.qmp_socket.write_text("not a socket", encoding="utf-8")

    assert main(
        ["status", "--state-root", str(state_root), "--run-id", record.run_id]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert artifact.split("-")[0].upper() in (str(payload["error"]) + diagnostic).upper()


@pytest.mark.parametrize("contents", ["not-json", "{}"])
def test_status_reports_corrupt_or_incomplete_manifest(
    tmp_path: Path,
    contents: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    record = _record_files(state_root)
    record.record_path.write_text(contents, encoding="utf-8")

    assert main(
        ["status", "--state-root", str(state_root), "--run-id", record.run_id]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "manifest" in str(payload["error"]).lower()
    assert diagnostic


def test_status_reports_a_missing_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_root = tmp_path / "state"

    assert main(
        ["status", "--state-root", str(state_root), "--run-id", "missing"]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "not found" in str(payload["error"]).lower()
    assert diagnostic


def test_exec_requires_nonempty_argv_after_explicit_separator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_root = tmp_path / "state"
    identity = tmp_path / "identity"
    _ready_record(state_root, identity)

    assert main(
        [
            "exec",
            "--state-root",
            str(state_root),
            "--run-id",
            "manual-001",
            "--ssh-private-key",
            str(identity),
        ]
    ) == 2
    first, _ = _json_stdout(capsys)
    assert first["ok"] is False

    assert main(
        [
            "exec",
            "--state-root",
            str(state_root),
            "--run-id",
            "manual-001",
            "--ssh-private-key",
            str(identity),
            "true",
        ]
    ) == 2
    second, _ = _json_stdout(capsys)
    assert second["ok"] is False
    assert "--" in str(second["error"])


def test_exec_parser_accepts_documented_positive_bounded_options() -> None:
    """Expose explicit exec deadline and per-stream output cap before guest argv."""
    parsed = build_parser().parse_args(
        [
            "exec",
            "--state-root", "/state",
            "--run-id", "manual-001",
            "--ssh-private-key", "/key",
            "--timeout-seconds", "12.5",
            "--max-output-bytes", "4096",
            "--", "true",
        ]
    )

    assert parsed.timeout_seconds == 12.5
    assert parsed.max_output_bytes == 4096
    assert parsed.guest_argv == ["--", "true"]


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--timeout-seconds", "0"),
        ("--timeout-seconds", "3601"),
        ("--timeout-seconds", "nan"),
        ("--max-output-bytes", "0"),
        ("--max-output-bytes", str(16 * 1024 * 1024 + 1)),
    ],
)
def test_exec_parser_rejects_unbounded_or_nonpositive_limits(
    option: str, value: str
) -> None:
    """Reject limits that disable or exceed the supported execution bounds."""
    with pytest.raises(cli.CliUsageError):
        build_parser().parse_args(
            [
                "exec", "--state-root", "/state", "--run-id", "manual-001",
                "--ssh-private-key", "/key", option, value, "--", "true",
            ]
        )


def test_bounded_process_drains_and_caps_both_flooding_streams() -> None:
    """Concurrent capped drains must not deadlock when both pipes exceed capacity."""
    payload_size = 2 * 1024 * 1024
    result = cli._run_bounded_process(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                f"os.write(1, b'o' * {payload_size}); "
                f"os.write(2, b'e' * {payload_size})"
            ),
        ],
        timeout_seconds=5,
        max_output_bytes=1024,
    )

    assert result.returncode == 0
    assert not result.timed_out
    assert result.stdout == b"o" * 1024
    assert result.stderr == b"e" * 1024
    assert result.stdout_truncated
    assert result.stderr_truncated


def test_bounded_process_timeout_kills_and_reaps_child() -> None:
    """A timed-out guest boundary must leave no unreaped local SSH process."""
    result = cli._run_bounded_process(
        [
            sys.executable,
            "-c",
            "import os,time; print(os.getpid(), flush=True); time.sleep(30)",
        ],
        timeout_seconds=0.1,
        max_output_bytes=1024,
    )

    child_pid = int(result.stdout.decode("ascii").strip())
    assert result.timed_out
    assert result.returncode != 0
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_exec_uses_recorded_ssh_boundary_and_returns_guest_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=7,
            stdout=b"guest out\n",
            stderr=b"guest err\n",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(cli, "_run_bounded_process", fake_run, raising=False)
    exit_code = main(
        [
            "exec",
            "--state-root",
            str(state_root),
            "--run-id",
            record.run_id,
            "--ssh-private-key",
            str(identity),
            "--",
            "printf",
            "%s",
            "hello world",
        ]
    )
    payload, diagnostic = _json_stdout(capsys)

    assert exit_code == 7
    assert payload == {
        "command": "exec",
        "guest_exit_code": 7,
        "max_output_bytes": 1024 * 1024,
        "ok": False,
        "run_id": record.run_id,
        "stderr": "guest err\n",
        "stderr_truncated": False,
        "stdout": "guest out\n",
        "stdout_truncated": False,
        "timed_out": False,
        "timeout_seconds": 300.0,
    }
    assert diagnostic == "guest command exited with status 7\n"
    argv, kwargs = calls[0]
    assert argv == cli.build_ssh_command(record, ["printf", "%s", "hello world"])
    assert kwargs == {"max_output_bytes": 1024 * 1024, "timeout_seconds": 300.0}


def test_exec_timeout_emits_stable_json_facts_and_returns_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Distinguish deadline cleanup from a guest exit code without leaking output."""
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    monkeypatch.setattr(
        cli,
        "_run_bounded_process",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=-9,
            stdout=b"partial:\xff",
            stderr=b"",
            timed_out=True,
            stdout_truncated=False,
            stderr_truncated=False,
        ),
        raising=False,
    )
    assert main(
        [
            "exec", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity), "--timeout-seconds", "0.25",
            "--max-output-bytes", "64", "--", "sleep", "30",
        ]
    ) == 1
    payload, diagnostic = _json_stdout(capsys)
    assert payload == {
        "command": "exec",
        "guest_exit_code": None,
        "max_output_bytes": 64,
        "ok": False,
        "run_id": record.run_id,
        "stderr": "",
        "stderr_truncated": False,
        "stdout": "partial:\ufffd",
        "stdout_truncated": False,
        "timed_out": True,
        "timeout_seconds": 0.25,
    }
    assert diagnostic == "guest command timed out after 0.25 seconds\n"


def test_exec_replaces_invalid_utf8_without_losing_exit_or_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    monkeypatch.setattr(
        cli,
        "_run_bounded_process",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=23,
            stdout=b"out:\xff\n",
            stderr=b"err:\xfe\n",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
        ),
        raising=False,
    )

    assert main(
        [
            "exec", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity), "--", "false",
        ]
    ) == 23
    payload, diagnostic = _json_stdout(capsys)
    assert payload["guest_exit_code"] == 23
    assert payload["stdout"] == "out:\ufffd\n"
    assert payload["stderr"] == "err:\ufffd\n"
    assert "status 23" in diagnostic


def test_start_run_captures_fake_tool_file_descriptors_before_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    identity.write_text("PRIVATE TEST KEY", encoding="utf-8")
    identity.chmod(0o600)
    record = _record_files(state_root)
    real_run = subprocess.run

    def delegated_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return real_run(
            [
                sys.executable,
                "-c",
                (
                    "import os; "
                    "os.write(1, b'delegated-stdout\\n'); "
                    "os.write(2, b'delegated-stderr\\n')"
                ),
            ],
            **kwargs,
        )

    # Exercise the real lifecycle functions while replacing only their external
    # process boundary with a harmless Python child that writes both OS fds.
    from test_support.isolated_lm import qemu as qemu_module

    monkeypatch.setattr(
        cli,
        "start_run",
        lambda run, key, **kwargs: qemu_module.start_run(
            run,
            key,
            **kwargs,
            allocate_port=lambda: 40222,
            run_process=delegated_run,
        ),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_ssh",
        lambda run, **kwargs: qemu_module.wait_for_ssh(
            run, **kwargs, run_process=delegated_run
        ),
    )

    assert main(
        [
            "start-run", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity),
        ]
    ) == 0
    captured = capfd.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["lifecycle"] == "ready"
    assert "delegated-" not in captured.out
    assert captured.err == ""


def test_subprocess_failure_diagnostic_is_bounded_decoded_and_secret_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    identity.write_text("PRIVATE TEST KEY", encoding="utf-8")
    identity.chmod(0o600)
    record = _record_files(state_root)
    private_key_block = (
        b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----\n"
        b"SECRET-BYTES\n"
        b"-----END " + b"OPENSSH PRIVATE KEY-----\n"
    )
    stderr = b"qemu rejected invalid byte \xff\n" + private_key_block + b"x" * 5000
    monkeypatch.setattr(
        cli,
        "start_run",
        lambda run, key, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(9, ["qemu"], stderr=stderr)
        ),
    )

    assert main(
        [
            "start-run", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity),
        ]
    ) == 1
    payload, diagnostic = _json_stdout(capsys)
    message = str(payload["error"])
    assert "status 9" in message
    assert "qemu rejected invalid byte \ufffd" in message
    assert "redacted private-key material" in message
    assert "SECRET-BYTES" not in message + diagnostic
    assert len(message.encode("utf-8")) < 3000


def test_usage_failure_diagnostic_is_bounded_and_secret_redacted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep argparse-rejected input from bypassing diagnostic safeguards."""
    private_key_block = (
        "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n"
        "SECRET-BYTES\n"
        "-----END " + "OPENSSH PRIVATE KEY-----\n"
    )
    rejected_argument = private_key_block + "x" * 5000

    assert main(
        ["preflight", "--state-root", str(tmp_path), rejected_argument]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    message = str(payload["error"])
    assert "redacted private-key material" in message
    assert "SECRET-BYTES" not in message + diagnostic
    assert len(message.encode("utf-8")) < 3000


def test_exec_rejects_wrong_lifecycle_without_leaking_private_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_root = tmp_path / "state"
    identity = tmp_path / "identity"
    identity.write_text("DO-NOT-LEAK-THIS-PRIVATE-KEY", encoding="utf-8")
    identity.chmod(0o600)
    _record_files(state_root)

    assert main(
        [
            "exec",
            "--state-root",
            str(state_root),
            "--run-id",
            "manual-001",
            "--ssh-private-key",
            str(identity),
            "--",
            "true",
        ]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    combined = json.dumps(payload) + diagnostic
    assert payload["ok"] is False
    assert "identity" in combined.lower()
    assert "DO-NOT-LEAK-THIS-PRIVATE-KEY" not in combined


def test_exec_revalidates_run_directory_after_manifest_load_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    original_load = cli._load_run_record

    def load_then_swap(paths: object, run_id: str) -> cli._LoadedRun:
        loaded = original_load(paths, run_id)
        moved = tmp_path / "moved-run"
        loaded.record.run_dir.rename(moved)
        loaded.record.run_dir.symlink_to(moved, target_is_directory=True)
        return loaded

    monkeypatch.setattr(cli, "_load_run_record", load_then_swap)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("SSH must not use a swapped run directory"),
    )

    assert main(
        [
            "exec", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity), "--", "true",
        ]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "run directory" in (str(payload["error"]) + diagnostic).lower()


def test_start_rejects_equivalent_real_directory_replacement_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    record = _record_files(state_root)
    identity = (tmp_path / "identity").resolve()
    identity.write_text("PRIVATE TEST KEY\n", encoding="utf-8")
    identity.chmod(0o600)
    original_load = cli._load_run_record
    from test_support.isolated_lm import qemu as qemu_module

    def load_then_replace(paths: object, run_id: str) -> cli._LoadedRun:
        loaded = original_load(paths, run_id)
        _replace_run_with_equivalent_directory(
            loaded.record.run_dir, tmp_path / "moved-run"
        )
        return loaded

    monkeypatch.setattr(cli, "_load_run_record", load_then_replace)
    monkeypatch.setattr(
        cli,
        "start_run",
        lambda run, key, **kwargs: qemu_module.start_run(
            run,
            key,
            **kwargs,
            allocate_port=lambda: pytest.fail(
                "port allocation must reject a replaced run"
            ),
            run_process=lambda *args, **kwargs: pytest.fail(
                "QEMU launch must reject a replaced run"
            ),
        ),
    )

    assert main(
        [
            "start-run", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity),
        ]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "run directory" in (str(payload["error"]) + diagnostic).lower()


def test_readiness_rejects_real_directory_replacement_after_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    record = _record_files(state_root)
    identity = (tmp_path / "identity").resolve()
    identity.write_text("PRIVATE TEST KEY\n", encoding="utf-8")
    identity.chmod(0o600)
    from test_support.isolated_lm import qemu as qemu_module

    def launch_then_replace(
        run: RunRecord,
        key: Path,
        *,
        expected_run_dir_identity: tuple[int, int],
    ) -> RunRecord:
        assert expected_run_dir_identity == (
            run.run_dir.stat().st_dev,
            run.run_dir.stat().st_ino,
        )
        running = replace(run, lifecycle="running", ssh_port=40222, identity_file=key)
        running = replace(
            running, qemu_command=tuple(cli.build_qemu_command(running, 40222))
        )
        running.write_atomic()
        _replace_run_with_equivalent_directory(run.run_dir, tmp_path / "moved-run")
        return running

    monkeypatch.setattr(cli, "start_run", launch_then_replace)
    monkeypatch.setattr(
        cli,
        "wait_for_ssh",
        lambda run, **kwargs: qemu_module.wait_for_ssh(
            run,
            **kwargs,
            run_process=lambda *args, **kwargs: pytest.fail(
                "readiness SSH must reject a replaced run"
            ),
        ),
    )

    assert main(
        [
            "start-run", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity),
        ]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "run directory" in (str(payload["error"]) + diagnostic).lower()


def test_exec_rejects_equivalent_real_directory_replacement_before_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    original_load = cli._load_run_record

    def load_then_replace(paths: object, run_id: str) -> cli._LoadedRun:
        loaded = original_load(paths, run_id)
        _replace_run_with_equivalent_directory(
            loaded.record.run_dir, tmp_path / "moved-run"
        )
        return loaded

    monkeypatch.setattr(cli, "_load_run_record", load_then_replace)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("SSH must reject a replaced run"),
    )

    assert main(
        [
            "exec", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity), "--", "true",
        ]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "run directory" in (str(payload["error"]) + diagnostic).lower()


def test_stop_rejects_equivalent_real_directory_replacement_before_pid_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    original_load = cli._load_run_record
    from test_support.isolated_lm import qemu as qemu_module

    def load_then_replace(paths: object, run_id: str) -> cli._LoadedRun:
        loaded = original_load(paths, run_id)
        _replace_run_with_equivalent_directory(
            loaded.record.run_dir, tmp_path / "moved-run"
        )
        return loaded

    monkeypatch.setattr(cli, "_load_run_record", load_then_replace)
    monkeypatch.setattr(
        cli,
        "stop_run",
        lambda run, **kwargs: qemu_module.stop_run(
            run,
            **kwargs,
            read_proc_cmdline=lambda pid: pytest.fail(
                "PID action must reject a replaced run"
            ),
            run_process=lambda *args, **kwargs: pytest.fail(
                "SSH must reject a replaced run"
            ),
            qmp_quit=lambda *args, **kwargs: pytest.fail(
                "QMP must reject a replaced run"
            ),
        ),
    )

    assert main(
        [
            "stop-run", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity),
        ]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "run directory" in (str(payload["error"]) + diagnostic).lower()


def test_start_run_delegates_launch_and_readiness_after_loading_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    prepared = _record_files(state_root)
    identity = tmp_path / "identity"
    identity.write_text("PRIVATE TEST KEY", encoding="utf-8")
    identity.chmod(0o600)
    calls: list[str] = []

    expected_identity = (
        prepared.run_dir.stat().st_dev,
        prepared.run_dir.stat().st_ino,
    )

    def fake_start(
        run: RunRecord,
        key: Path,
        *,
        expected_run_dir_identity: tuple[int, int],
    ) -> RunRecord:
        assert run == prepared
        assert key == identity
        assert expected_run_dir_identity == expected_identity
        calls.append("start")
        running = replace(run, lifecycle="running", ssh_port=40222, identity_file=key)
        running = replace(
            running, qemu_command=tuple(cli.build_qemu_command(running, 40222))
        )
        running.write_atomic()
        return running

    def fake_wait(
        run: RunRecord, *, expected_run_dir_identity: tuple[int, int]
    ) -> RunRecord:
        assert run.lifecycle == "running"
        assert expected_run_dir_identity == expected_identity
        calls.append("wait")
        ready = replace(run, lifecycle="ready")
        ready.write_atomic()
        return ready

    monkeypatch.setattr(cli, "start_run", fake_start)
    monkeypatch.setattr(cli, "wait_for_ssh", fake_wait)

    assert main(
        [
            "start-run",
            "--state-root",
            str(state_root),
            "--run-id",
            prepared.run_id,
            "--ssh-private-key",
            str(identity),
        ]
    ) == 0
    payload, diagnostic = _json_stdout(capsys)
    assert calls == ["start", "wait"]
    assert payload["lifecycle"] == "ready"
    assert json.loads(prepared.record_path.read_text(encoding="utf-8"))["lifecycle"] == "ready"
    assert diagnostic == ""


def test_stop_run_delegates_only_with_the_recorded_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    ready = _ready_record(state_root, identity)
    calls: list[RunRecord] = []

    expected_identity = (ready.run_dir.stat().st_dev, ready.run_dir.stat().st_ino)

    def fake_stop(
        run: RunRecord, *, expected_run_dir_identity: tuple[int, int]
    ) -> RunRecord:
        assert expected_run_dir_identity == expected_identity
        calls.append(run)
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped

    monkeypatch.setattr(cli, "stop_run", fake_stop)
    assert main(
        [
            "stop-run",
            "--state-root",
            str(state_root),
            "--run-id",
            ready.run_id,
            "--ssh-private-key",
            str(identity),
        ]
    ) == 0
    payload, diagnostic = _json_stdout(capsys)
    assert calls == [ready]
    assert payload["lifecycle"] == "stopped"
    assert diagnostic == ""

    other = tmp_path / "other-identity"
    other.write_text("OTHER PRIVATE KEY", encoding="utf-8")
    other.chmod(0o600)
    assert main(
        [
            "stop-run",
            "--state-root",
            str(state_root),
            "--run-id",
            ready.run_id,
            "--ssh-private-key",
            str(other),
        ]
    ) == 2
    rejected, rejected_diagnostic = _json_stdout(capsys)
    assert rejected["ok"] is False
    assert "recorded identity" in str(rejected["error"])
    assert "OTHER PRIVATE KEY" not in json.dumps(rejected) + rejected_diagnostic


def test_stop_run_accepts_crash_recoverable_launching_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep stop usable when launch facts persisted before running transition."""
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    identity.write_text("PRIVATE TEST KEY\n", encoding="utf-8")
    identity.chmod(0o600)
    record = replace(
        _record_files(state_root),
        lifecycle="launching",
        ssh_port=40222,
        identity_file=identity,
    )
    record = replace(record, qemu_command=tuple(cli.build_qemu_command(record, 40222)))
    record.record_path.write_text(record.to_json(), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "stop_run",
        lambda run, **kwargs: replace(run, lifecycle="stopped"),
    )

    assert main(
        [
            "stop-run", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(identity),
        ]
    ) == 0
    payload, diagnostic = _json_stdout(capsys)
    assert payload["lifecycle"] == "stopped"
    assert diagnostic == ""


@pytest.mark.parametrize("defect", ["group-readable", "symlink"])
def test_stop_validates_private_key_even_when_recorded_pid_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    defect: str,
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    record.pid_file.unlink()
    supplied = identity
    if defect == "group-readable":
        identity.chmod(0o640)
    else:
        supplied = tmp_path / "identity-link"
        supplied.symlink_to(identity)
    monkeypatch.setattr(
        cli, "stop_run", lambda run: pytest.fail("stop must not receive an invalid key")
    )

    assert main(
        [
            "stop-run", "--state-root", str(state_root), "--run-id", record.run_id,
            "--ssh-private-key", str(supplied),
        ]
    ) == 2
    payload, diagnostic = _json_stdout(capsys)
    assert payload["ok"] is False
    assert "identity" in (str(payload["error"]) + diagnostic).lower()


def test_wrapper_loads_by_exact_path_from_foreign_working_directory() -> None:
    wrapper = Path(__file__).parents[1] / "scripts" / "isolated-lm-vm.py"
    completed = subprocess.run(
        [sys.executable, str(wrapper), "--help"],
        cwd="/",
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert all(command in completed.stdout for command in SUPPORTED_COMMANDS)
    assert os.access(wrapper, os.X_OK)

    spec = importlib.util.spec_from_file_location("isolated_lm_vm_entrypoint", wrapper)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main is cli.main
