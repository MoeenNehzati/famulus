"""Native process-tree containment for the credential preflight child."""
from __future__ import annotations

import multiprocessing
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


_TERMINATION_GRACE_SECONDS = 0.5


@dataclass
class _ProcessContainment:
    """Parent-owned authority for one child's complete process tree."""

    pid: int
    windows_job: int | None = None


def _terminate_direct_process(process: multiprocessing.Process) -> bool:
    """Terminate a child that has not received authority to access a backend."""
    if not process.is_alive():
        process.join()
        return True
    process.terminate()
    process.join(_TERMINATION_GRACE_SECONDS)
    if process.is_alive():
        kill = getattr(process, "kill", None)
        if kill is not None:
            kill()
        process.join(_TERMINATION_GRACE_SECONDS)
    return not process.is_alive()


def _prepare_parent_containment(
    process: multiprocessing.Process,
) -> _ProcessContainment | None:
    """Prepare tree containment before the parent authorizes backend access."""
    if process.pid is None:
        return None
    if sys.platform == "win32":
        job = _windows_create_kill_on_close_job(process.pid)
        if job is None:
            return None
        return _ProcessContainment(process.pid, job)
    return _ProcessContainment(process.pid)


def _establish_child_containment() -> bool:
    """Establish the child-side containment primitive before signaling READY."""
    if sys.platform == "win32":
        return True
    try:
        os.setsid()
    except OSError:
        return False
    return True


def _terminate_and_verify_tree(
    containment: _ProcessContainment,
    process: multiprocessing.Process,
) -> bool:
    """Terminate the complete child tree and prove no live member remains."""
    if sys.platform == "win32":
        return _windows_terminate_and_verify_job(containment.windows_job, process)
    return _posix_terminate_and_verify_group(containment.pid, process)


def _posix_terminate_and_verify_group(
    process_group: int,
    process: multiprocessing.Process,
) -> bool:
    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    process.join(min(0.05, max(0.0, deadline - time.monotonic())))
    if _posix_group_has_live_members(process_group):
        try:
            os.killpg(process_group, signal.SIGTERM)
        except OSError:
            pass
    midpoint = time.monotonic() + max(0.0, deadline - time.monotonic()) / 2
    process.join(max(0.0, midpoint - time.monotonic()))
    while _posix_group_has_live_members(process_group) and time.monotonic() < midpoint:
        time.sleep(0.01)
    if _posix_group_has_live_members(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except OSError:
            pass
    process.join(max(0.0, deadline - time.monotonic()))
    while _posix_group_has_live_members(process_group) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not process.is_alive() and not _posix_group_has_live_members(process_group)


def _posix_group_has_live_members(process_group: int) -> bool:
    proc_root = Path("/proc")
    if sys.platform.startswith("linux") and proc_root.is_dir():
        try:
            entries = tuple(proc_root.iterdir())
        except OSError:
            return True
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                stat_text = (entry / "stat").read_text(encoding="ascii")
                _, fields_text = stat_text.rsplit(") ", 1)
                fields = fields_text.split()
                state = fields[0]
                member_group = int(fields[2])
            except (OSError, ValueError, IndexError):
                continue
            if member_group == process_group and state != "Z":
                return True
        return False
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_create_kill_on_close_job(process_id: int) -> int | None:
    """Create and assign a kill-on-close Job before the child receives GO."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00002000
    configured = kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)
    )
    process_handle = kernel32.OpenProcess(0x0100 | 0x0001 | 0x1000, False, process_id)
    assigned = bool(process_handle) and bool(
        kernel32.AssignProcessToJobObject(job, process_handle)
    )
    if process_handle:
        kernel32.CloseHandle(process_handle)
    if not configured or not assigned:
        kernel32.CloseHandle(job)
        return None
    return int(job)


def _windows_terminate_and_verify_job(
    job: int | None,
    process: multiprocessing.Process,
) -> bool:
    if sys.platform != "win32" or job is None:
        return False
    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    process.join(min(0.05, max(0.0, deadline - time.monotonic())))
    info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = wintypes.DWORD()
    queried = bool(
        kernel32.QueryInformationJobObject(
            job, 1, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)
        )
    )
    empty = queried and info.ActiveProcesses == 0
    terminated = True
    if queried and not empty:
        terminated = bool(kernel32.TerminateJobObject(job, 1))
    while queried and not empty and time.monotonic() < deadline:
        queried = kernel32.QueryInformationJobObject(
            job, 1, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)
        )
        if queried and info.ActiveProcesses == 0:
            empty = True
            break
        time.sleep(0.01)
    process.join(max(0.0, deadline - time.monotonic()))
    kernel32.CloseHandle(job)
    return bool(queried) and terminated and empty and not process.is_alive()
