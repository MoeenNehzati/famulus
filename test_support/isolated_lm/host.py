"""Host compatibility checks for the isolated language-model VM profile."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
from collections.abc import Callable


REQUIRED_COMMANDS = (
    "kvm-ok",
    "qemu-system-x86_64",
    "qemu-img",
    "cloud-localds",
    "gpgv",
    "sha256sum",
    "ssh",
    "ssh-keygen",
)


@dataclass(frozen=True)
class CheckResult:
    """The outcome and diagnostic detail of a single host requirement."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HostPreflightReport:
    """Recorded host facts and every result for the supported VM profile."""

    platform: str
    machine: str
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        """Whether all requirements for the profile passed."""
        return all(check.ok for check in self.checks)

    def by_name(self, name: str) -> CheckResult:
        """Return the uniquely named check, raising KeyError when absent."""
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(name)


def _open_kvm() -> None:
    """Open the KVM device read/write to verify usable acceleration access."""
    descriptor = os.open("/dev/kvm", os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    os.close(descriptor)


def check_host(
    *,
    which: Callable[[str], str | None] = shutil.which,
    open_kvm: Callable[[], None] = _open_kvm,
    platform_name: Callable[[], str] = platform.platform,
    machine: Callable[[], str] = platform.machine,
) -> HostPreflightReport:
    """Check every requirement for the first Linux x86_64 KVM profile."""
    platform_value = platform_name()
    machine_value = machine()
    checks = [
        CheckResult(
            name="platform:linux",
            ok=platform_value.lower().startswith("linux"),
            detail=platform_value,
        ),
        CheckResult(
            name="machine:x86_64",
            ok=machine_value == "x86_64",
            detail=machine_value,
        ),
    ]

    for command in REQUIRED_COMMANDS:
        executable = which(command)
        if executable is None:
            checks.append(CheckResult(f"command:{command}", False, "not found"))
        else:
            checks.append(
                CheckResult(
                    name=f"command:{command}",
                    ok=True,
                    detail=str(Path(executable).resolve()),
                )
            )

    try:
        open_kvm()
    except OSError as error:
        checks.append(CheckResult("kvm:read-write", False, str(error)))
    else:
        checks.append(CheckResult("kvm:read-write", True, "available"))

    return HostPreflightReport(
        platform=platform_value,
        machine=machine_value,
        checks=tuple(checks),
    )
