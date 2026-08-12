"""Host compatibility checks for the isolated language-model VM profile."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
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

KVM_OK_TIMEOUT_SECONDS = 5.0
_KVM_OK_DETAIL_BYTES = 2048


@dataclass(frozen=True)
class CheckResult:
    """The outcome and diagnostic detail of a single host requirement.

    Intent
    ------
    Carry one named preflight result and its bounded diagnostic detail.

    Rationale
    ---------
    Immutable named results keep the JSON report complete and auditable.

    Pseudocode
    ----------
    - set fields = requirement name, outcome, and diagnostic detail

    Wraps
    -----
    none
    """

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HostPreflightReport:
    """Recorded host facts and every result for the supported VM profile.

    Intent
    ------
    Carry host identity and the complete immutable preflight result set.

    Rationale
    ---------
    One report preserves failed and successful checks for stable JSON output.

    Pseudocode
    ----------
    - set fields = platform, machine, and ordered check results

    Wraps
    -----
    none
    """

    platform: str
    machine: str
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        """Whether all requirements for the profile passed.

        Intent
        ------
        Reduce the complete check set to the supported-profile verdict.

        Rationale
        ---------
        The command exit status must reflect every preflight requirement.

        Pseudocode
        ----------
        - return whether every check passed

        Wraps
        -----
        none
        """
        return all(check.ok for check in self.checks)

    def by_name(self, name: str) -> CheckResult:
        """Return the uniquely named check, raising KeyError when absent.

        Intent
        ------
        Resolve one recorded result by its stable public check name.

        Rationale
        ---------
        Tests and callers need deterministic access without positional coupling.

        Pseudocode
        ----------
        - for check in recorded checks:
          - if check name matches requested name:
            - return check
        - raise missing check name

        Wraps
        -----
        none
        """
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(name)


def _open_kvm() -> None:
    """Open the KVM device read/write to verify usable acceleration access.

    Intent
    ------
    Prove the current process can open the canonical KVM device read/write.

    Rationale
    ---------
    File existence or mode inspection alone does not prove effective access.

    Pseudocode
    ----------
    - set descriptor = read-write KVM device open
    - return after descriptor close

    Wraps
    -----
    none
    """
    descriptor = os.open("/dev/kvm", os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    os.close(descriptor)


def check_host(
    *,
    which: Callable[[str], str | None] = shutil.which,
    open_kvm: Callable[[], None] = _open_kvm,
    platform_name: Callable[[], str] = platform.platform,
    machine: Callable[[], str] = platform.machine,
    run_process: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    kvm_ok_timeout_seconds: float = KVM_OK_TIMEOUT_SECONDS,
) -> HostPreflightReport:
    """Check every requirement for the first Linux x86_64 KVM profile.

    Intent
    ------
    Record platform, executable, semantic KVM acceleration, and device-access
    outcomes without stopping at the first failed requirement.

    Rationale
    ---------
    Command presence and read/write access to ``/dev/kvm`` do not prove that
    cpu-checker's host acceleration probe succeeds, so ``kvm-ok`` must execute
    through a captured deadline-bound subprocess boundary.

    Pseudocode
    ----------
    - set checks = Linux and x86-64 profile results
    - set resolved_commands = every required executable resolution
    - set acceleration_check = captured deadline-bound kvm-ok result
    - set device_check = independent KVM read-write probe result
    - return every check in one immutable report

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._limit_kvm_message:
      why:
        transforms: "Limits complete failure messages to the public diagnostic ceiling."

    InstantiationsFromRepo
    ----------------------
    ._bounded_kvm_detail:
      why:
        transforms: "Returns a bounded decoded summary of captured kvm-ok output."
    .CheckResult:
      why:
        constructs: "Builds every named host requirement result retained in the report."
    .HostPreflightReport:
      why:
        constructs: "Builds the complete immutable host-preflight result."
    """
    if (
        not isinstance(kvm_ok_timeout_seconds, (int, float))
        or isinstance(kvm_ok_timeout_seconds, bool)
        or not 0 < float(kvm_ok_timeout_seconds) < float("inf")
    ):
        raise ValueError("kvm-ok timeout must be finite and positive")
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

    resolved_commands: dict[str, str | None] = {}
    for command in REQUIRED_COMMANDS:
        executable = which(command)
        resolved_commands[command] = executable
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

    kvm_ok = resolved_commands["kvm-ok"]
    if kvm_ok is None:
        checks.append(
            CheckResult("kvm:acceleration", False, "not run: kvm-ok not found")
        )
    else:
        try:
            completed = run_process(
                [str(Path(kvm_ok).resolve())],
                capture_output=True,
                check=False,
                timeout=float(kvm_ok_timeout_seconds),
            )
        except subprocess.TimeoutExpired as error:
            detail = _bounded_kvm_detail(error.stdout, error.stderr)
            suffix = f": {detail}" if detail else ""
            checks.append(
                CheckResult(
                    "kvm:acceleration",
                    False,
                    _limit_kvm_message(f"kvm-ok timed out{suffix}"),
                )
            )
        except OSError as error:
            checks.append(
                CheckResult(
                    "kvm:acceleration",
                    False,
                    _limit_kvm_message(f"kvm-ok failed: {error}"),
                )
            )
        else:
            detail = _bounded_kvm_detail(completed.stdout, completed.stderr)
            if completed.returncode == 0:
                checks.append(
                    CheckResult("kvm:acceleration", True, detail or "exit 0")
                )
            else:
                suffix = f": {detail}" if detail else ""
                checks.append(
                    CheckResult(
                        "kvm:acceleration",
                        False,
                        _limit_kvm_message(
                            f"kvm-ok exit {completed.returncode}{suffix}"
                        ),
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


def _bounded_kvm_detail(stdout: object, stderr: object) -> str:
    """Decode a bounded non-secret cpu-checker diagnostic summary.

    Intent
    ------
    Prefer stderr then stdout from the captured ``kvm-ok`` result while
    retaining at most the preflight diagnostic byte limit.

    Rationale
    ---------
    Host reports need actionable probe detail without allowing unexpected tool
    output to grow the JSON result without bound or fail UTF-8 decoding.

    Pseudocode
    ----------
    - set chunks = normalized nonempty stderr and stdout byte strings
    - set retained = joined chunks limited to configured bytes
    - return replacement-decoded retained bytes without outer whitespace

    Wraps
    -----
    none
    """
    chunks: list[bytes] = []
    for value in (stderr, stdout):
        if isinstance(value, str):
            encoded = value.encode("utf-8", errors="replace")
        elif isinstance(value, bytes):
            encoded = value
        else:
            encoded = b""
        if encoded:
            chunks.append(encoded)
    return b"\n".join(chunks)[:_KVM_OK_DETAIL_BYTES].decode(
        "utf-8", errors="replace"
    ).strip()


def _limit_kvm_message(message: str) -> str:
    """Limit a complete KVM diagnostic, including its status prefix, by bytes.

    Intent
    ------
    Enforce the public preflight detail ceiling after status text and captured
    tool output have been combined.

    Rationale
    ---------
    Bounding only child output allows a prefix to push the final JSON field
    beyond its promised limit, especially with multibyte replacement text.

    Pseudocode
    ----------
    - set retained = UTF-8 message limited to configured bytes
    - return replacement-decoded retained bytes

    Wraps
    -----
    none
    """
    return message.encode("utf-8")[:_KVM_OK_DETAIL_BYTES].decode(
        "utf-8", errors="replace"
    )
