"""Tests for the isolated-VM runtime model and host preflight."""
from __future__ import annotations

from pathlib import Path
import subprocess

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
        run_process=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
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
        "kvm:acceleration",
        "kvm:read-write",
    ]


def test_host_preflight_rejects_non_linux_and_non_x86_64_hosts() -> None:
    report = check_host(
        which=lambda name: f"/opt/bin/{name}",
        open_kvm=lambda: None,
        platform_name=lambda: "Darwin-25.0.0-arm64",
        machine=lambda: "arm64",
        run_process=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
    )

    assert not report.ok
    assert report.by_name("platform:linux").detail == "Darwin-25.0.0-arm64"
    assert report.by_name("machine:x86_64").detail == "arm64"
    assert not report.by_name("platform:linux").ok
    assert not report.by_name("machine:x86_64").ok


def test_host_preflight_executes_bounded_kvm_ok_and_requires_zero() -> None:
    """Require cpu-checker's semantic acceleration probe, not command presence alone."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run_process(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"KVM acceleration can be used\n", b"")

    report = check_host(
        which=lambda name: f"/usr/bin/{name}",
        open_kvm=lambda: None,
        platform_name=lambda: "Linux-test",
        machine=lambda: "x86_64",
        run_process=run_process,
    )

    assert report.by_name("kvm:acceleration").ok
    assert "KVM acceleration" in report.by_name("kvm:acceleration").detail
    assert calls == [(
        ["/usr/bin/kvm-ok"],
        {"capture_output": True, "check": False, "timeout": 5.0},
    )]


def test_host_preflight_reports_missing_kvm_ok_without_invocation() -> None:
    """Keep missing cpu-checker distinct from a failed or timed-out probe."""
    report = check_host(
        which=lambda name: None if name == "kvm-ok" else f"/usr/bin/{name}",
        open_kvm=lambda: None,
        platform_name=lambda: "Linux-test",
        machine=lambda: "x86_64",
        run_process=lambda *args, **kwargs: pytest.fail("missing kvm-ok must not run"),
    )

    assert not report.by_name("kvm:acceleration").ok
    assert report.by_name("kvm:acceleration").detail == "not run: kvm-ok not found"


def test_host_preflight_reports_nonzero_and_bounded_kvm_ok_diagnostic() -> None:
    """Expose a bounded cpu-checker failure without treating device access as enough."""
    report = check_host(
        which=lambda name: f"/usr/bin/{name}",
        open_kvm=lambda: None,
        platform_name=lambda: "Linux-test",
        machine=lambda: "x86_64",
        run_process=lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, b"x" * 5000, b"KVM disabled by BIOS\n"
        ),
    )

    check = report.by_name("kvm:acceleration")
    assert not check.ok
    assert "exit 1" in check.detail
    assert "KVM disabled" in check.detail
    assert len(check.detail.encode("utf-8")) <= 2048


def test_host_preflight_reports_kvm_ok_timeout_and_device_failure() -> None:
    """Retain both semantic-probe timeout and independent device-open failure."""
    report = check_host(
        which=lambda name: f"/usr/bin/{name}",
        open_kvm=lambda: (_ for _ in ()).throw(PermissionError("device denied")),
        platform_name=lambda: "Linux-test",
        machine=lambda: "x86_64",
        run_process=lambda argv, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(argv, kwargs["timeout"])
        ),
    )

    assert not report.by_name("kvm:acceleration").ok
    assert "timed out" in report.by_name("kvm:acceleration").detail
    assert not report.by_name("kvm:read-write").ok
    assert report.by_name("kvm:read-write").detail == "device denied"
