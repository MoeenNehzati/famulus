"""Contract tests for the supported isolated-VM command-line interface."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

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


def test_parser_exposes_only_supported_commands() -> None:
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(subparsers.choices) == SUPPORTED_COMMANDS


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


def test_exec_uses_recorded_ssh_boundary_and_returns_guest_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    identity = (tmp_path / "identity").resolve()
    record = _ready_record(state_root, identity)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 7, stdout="guest out\n", stderr="guest err\n")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
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
        "ok": False,
        "run_id": record.run_id,
        "stderr": "guest err\n",
        "stdout": "guest out\n",
    }
    assert diagnostic == "guest command exited with status 7\n"
    argv, kwargs = calls[0]
    assert argv == cli.build_ssh_command(record, ["printf", "%s", "hello world"])
    assert kwargs == {"capture_output": True, "check": False, "text": True}


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
    assert "ready" in combined
    assert "DO-NOT-LEAK-THIS-PRIVATE-KEY" not in combined


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

    def fake_start(run: RunRecord, key: Path) -> RunRecord:
        assert run == prepared
        assert key == identity
        calls.append("start")
        running = replace(run, lifecycle="running", ssh_port=40222, identity_file=key)
        running = replace(
            running, qemu_command=tuple(cli.build_qemu_command(running, 40222))
        )
        running.write_atomic()
        return running

    def fake_wait(run: RunRecord) -> RunRecord:
        assert run.lifecycle == "running"
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

    def fake_stop(run: RunRecord) -> RunRecord:
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
