"""Tests for deterministic NoCloud input and disposable isolated-VM runs."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess

import pytest

from test_support.isolated_lm.guest import (
    prepare_run,
    render_meta_data,
    render_user_data,
    validate_run_id,
    write_nocloud_seed,
)
from test_support.isolated_lm.image import IMAGE_URL, CHECKSUMS_URL, SIGNATURE_URL
from test_support.isolated_lm.model import CloudImageRecord, RuntimePaths, VmResources


_PUBLIC_KEY = "ssh-ed25519 AAAATEST isolated-lm"
_DIGEST = hashlib.sha256(b"base image").hexdigest()


def _image(path: Path) -> CloudImageRecord:
    """Build a real-byte-matching verified image record for run tests."""
    path.write_bytes(b"base image")
    return CloudImageRecord(
        schema_version=1,
        image_url=IMAGE_URL,
        checksums_url=CHECKSUMS_URL,
        signature_url=SIGNATURE_URL,
        filename="noble-server-cloudimg-amd64.img",
        verified_source_digest=_DIGEST,
        byte_size=path.stat().st_size,
        retrieved_at=datetime(2026, 8, 11, tzinfo=UTC),
        cached_path=path.resolve(),
    )


def test_user_data_contains_only_generic_guest_prerequisites() -> None:
    """Catch an unapproved host-specific package or guest bootstrap payload."""
    rendered = render_user_data(_PUBLIC_KEY)

    assert "name: famulus-test" in rendered
    assert "lock_passwd: true" in rendered
    assert "openssh-server" in rendered
    assert "ca-certificates" in rendered
    assert "curl" in rendered
    assert "python3" in rendered
    for forbidden in ("Famulus", "officina", "uv", "/maintainer/checkout", "private guidance"):
        assert forbidden not in rendered


def test_user_data_encodes_a_yaml_significant_public_key_as_one_json_scalar() -> None:
    """Catch YAML parsing an allowed key with a colon as a mapping, not a key string."""
    public_key = 'ssh-ed25519 AAAA: injected "quoted" \\backslash'
    rendered = render_user_data(public_key)
    key_items = [
        line.removeprefix("      - ")
        for line in rendered.splitlines()
        if line.startswith("      - ")
    ]

    assert len(key_items) == 1
    assert json.loads(key_items[0]) == public_key


@pytest.mark.parametrize(
    "run_id",
    ["../escape", "a/b", "a\\b", "-leading", "Upper", "a_underscore", "a" * 64],
)
def test_validate_run_id_rejects_values_outside_the_closed_run_namespace(run_id: str) -> None:
    """Catch a traversal, separator, or non-regex run ID before it reaches disk."""
    with pytest.raises(ValueError, match="run ID"):
        validate_run_id(run_id)


def test_rendered_metadata_uses_only_the_validated_run_identity() -> None:
    """Catch metadata that leaks host state or changes the guest identity contract."""
    assert render_meta_data("run-42") == (
        "instance-id: isolated-lm-run-42\n"
        "local-hostname: isolated-lm-run-42\n"
    )


def test_write_nocloud_seed_uses_private_inputs_and_the_exact_command(tmp_path: Path) -> None:
    """Catch writable seed inputs or an argv that changes cloud-localds semantics."""
    calls: list[list[str]] = []
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    def run(argv: list[str], **kwargs: object) -> CompletedProcess[object]:
        calls.append(argv)
        Path(argv[1]).write_bytes(b"seed")
        return CompletedProcess(argv, 0)

    seed_iso = write_nocloud_seed(
        run_dir,
        render_user_data(_PUBLIC_KEY),
        render_meta_data("run-42"),
        run=run,
    )

    user_data = run_dir / "user-data"
    meta_data = run_dir / "meta-data"
    assert calls == [["cloud-localds", str(seed_iso), str(user_data), str(meta_data)]]
    assert user_data.stat().st_mode & 0o777 == 0o600
    assert meta_data.stat().st_mode & 0o777 == 0o600
    assert user_data.read_text(encoding="utf-8") == render_user_data(_PUBLIC_KEY)
    assert meta_data.read_text(encoding="utf-8") == render_meta_data("run-42")


def test_prepare_run_persists_a_complete_prepared_record(tmp_path: Path) -> None:
    """Catch a run record that omits its isolated artifacts or verified provenance."""
    paths = RuntimePaths.from_root((tmp_path / "state").resolve())
    image = _image(tmp_path / "base.qcow2")
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> CompletedProcess[object]:
        calls.append(argv)
        if argv[0] == "qemu-img":
            Path(argv[-2]).write_bytes(b"overlay")
        else:
            Path(argv[1]).write_bytes(b"seed")
        return CompletedProcess(argv, 0)

    record = prepare_run(
        paths,
        image,
        "run-42",
        _PUBLIC_KEY,
        VmResources(vcpus=2, memory_mib=1024, disk_gib=7),
        run=run,
        now=lambda: datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )

    run_dir = paths.runs / "run-42"
    assert record.run_id == "run-42"
    assert record.run_dir == run_dir.resolve()
    assert record.resources == VmResources(vcpus=2, memory_mib=1024, disk_gib=7)
    assert record.source_image_digest == _DIGEST
    assert record.overlay == run_dir / "overlay.qcow2"
    assert record.seed_iso == run_dir / "seed.iso"
    assert record.known_hosts == run_dir / "known_hosts"
    assert record.serial_log == run_dir / "serial.log"
    assert record.qmp_socket == run_dir / "qmp.sock"
    assert record.pid_file == run_dir / "qemu.pid"
    assert record.record_path == run_dir / "run.json"
    assert record.ssh_user == "famulus-test"
    assert record.created_at_utc == "2026-08-11T12:00:00+00:00"
    assert record.lifecycle == "prepared"
    assert record.ssh_port is None
    assert record.identity_file is None
    assert record.qemu_command == ()
    assert calls == [
        [
            "qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
            "-b", str(image.cached_path), str(record.overlay), "7G",
        ],
        [
            "cloud-localds", str(record.seed_iso), str(run_dir / "user-data"),
            str(run_dir / "meta-data"),
        ],
    ]
    assert record.known_hosts.stat().st_mode & 0o777 == 0o600
    assert record.record_path.read_text(encoding="utf-8") == record.to_json()
    assert json.loads(record.to_json()) == {
        "created_at_utc": "2026-08-11T12:00:00+00:00",
        "identity_file": None,
        "known_hosts": str(record.known_hosts),
        "lifecycle": "prepared",
        "overlay": str(record.overlay),
        "pid_file": str(record.pid_file),
        "qemu_command": [],
        "qmp_socket": str(record.qmp_socket),
        "record_path": str(record.record_path),
        "resources": {"disk_gib": 7, "memory_mib": 1024, "vcpus": 2},
        "run_dir": str(record.run_dir),
        "run_id": "run-42",
        "schema_version": 1,
        "seed_iso": str(record.seed_iso),
        "serial_log": str(record.serial_log),
        "source_image_digest": _DIGEST,
        "ssh_port": None,
        "ssh_user": "famulus-test",
    }
    assert record.to_json().endswith("\n")


def test_prepare_run_rejects_an_invalid_id_before_creating_state(tmp_path: Path) -> None:
    """Catch validation after state-directory creation, which permits path probing."""
    paths = RuntimePaths.from_root((tmp_path / "state").resolve())
    image = _image(tmp_path / "base.qcow2")

    with pytest.raises(ValueError, match="run ID"):
        prepare_run(paths, image, "../escape", _PUBLIC_KEY, VmResources())

    assert not paths.root.exists()


def test_prepare_run_refuses_reuse_and_cleans_its_partial_directory(tmp_path: Path) -> None:
    """Catch reuse or a failed overlay leaving a subsequent run contaminated."""
    paths = RuntimePaths.from_root((tmp_path / "state").resolve())
    image = _image(tmp_path / "base.qcow2")
    existing = paths.runs / "existing"
    existing.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        prepare_run(paths, image, "existing", _PUBLIC_KEY, VmResources())

    def fail_after_overlay(argv: list[str], **kwargs: object) -> CompletedProcess[object]:
        Path(argv[-2]).write_bytes(b"partial")
        raise CalledProcessError(1, argv)

    with pytest.raises(CalledProcessError):
        prepare_run(paths, image, "failed", _PUBLIC_KEY, VmResources(), run=fail_after_overlay)

    assert not (paths.runs / "failed").exists()


def test_prepare_run_rejects_a_symlinked_runs_directory(tmp_path: Path) -> None:
    """Catch a state symlink that would place transient VM artifacts outside state."""
    paths = RuntimePaths.from_root((tmp_path / "state").resolve())
    paths.root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    paths.runs.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        prepare_run(paths, _image(tmp_path / "base.qcow2"), "run-42", _PUBLIC_KEY, VmResources())

    assert list(outside.iterdir()) == []


def test_prepare_run_rejects_a_runs_path_with_parent_traversal_before_mutation(
    tmp_path: Path,
) -> None:
    """Catch validation that creates an attacker-selected sibling before rejecting it."""
    root = (tmp_path / "state").resolve()
    paths = RuntimePaths(
        root=root,
        downloads=root / "downloads",
        images=root / "images",
        runs=root / ".." / "outside",
    )

    with pytest.raises(ValueError, match="runs directory"):
        prepare_run(paths, _image(tmp_path / "base.qcow2"), "run-42", _PUBLIC_KEY, VmResources())

    assert not (tmp_path / "outside").exists()
