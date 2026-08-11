"""Tests for the isolated-VM runtime model and host preflight."""
from __future__ import annotations

from pathlib import Path

import pytest

from test_support.isolated_lm.host import REQUIRED_COMMANDS, check_host
from test_support.isolated_lm.model import RuntimePaths, VmResources


def test_runtime_paths_are_derived_only_from_explicit_root(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "state")

    assert paths.root == (tmp_path / "state").resolve()
    assert paths.downloads == paths.root / "downloads"
    assert paths.images == paths.root / "images"
    assert paths.runs == paths.root / "runs"


def test_runtime_paths_reject_relative_root() -> None:
    with pytest.raises(ValueError, match="state root must be absolute"):
        RuntimePaths.from_root(Path("state"))


def test_vm_resources_use_the_approved_defaults() -> None:
    assert VmResources() == VmResources(vcpus=4, memory_mib=8192, disk_gib=40)


def test_host_preflight_requires_every_command_and_writable_kvm() -> None:
    commands = {
        name: f"/usr/bin/{name}"
        for name in REQUIRED_COMMANDS
        if name != "cloud-localds"
    }

    report = check_host(
        which=commands.get,
        open_kvm=lambda: (_ for _ in ()).throw(PermissionError("denied")),
        platform_name=lambda: "Linux-6-test-x86_64-with-glibc",
        machine=lambda: "x86_64",
    )

    assert not report.ok
    assert report.platform == "Linux-6-test-x86_64-with-glibc"
    assert report.machine == "x86_64"
    assert report.by_name("command:cloud-localds").detail == "not found"
    assert report.by_name("kvm:read-write").detail == "denied"
    assert [check.name for check in report.checks] == [
        "platform:linux",
        "machine:x86_64",
        *[f"command:{name}" for name in REQUIRED_COMMANDS],
        "kvm:read-write",
    ]


def test_host_preflight_rejects_non_linux_and_non_x86_64_hosts() -> None:
    report = check_host(
        which=lambda name: f"/opt/bin/{name}",
        open_kvm=lambda: None,
        platform_name=lambda: "Darwin-25.0.0-arm64",
        machine=lambda: "arm64",
    )

    assert not report.ok
    assert report.by_name("platform:linux").detail == "Darwin-25.0.0-arm64"
    assert report.by_name("machine:x86_64").detail == "arm64"
    assert not report.by_name("platform:linux").ok
    assert not report.by_name("machine:x86_64").ok
