"""cloud_transport: shared cloud-list download/upload transport for list-manager.

Both lists.py (core CLI) and read_beautify.py (read+render convenience
wrapper) need "download a cloud list to a local temp file" / "upload a local
file back to a cloud list name". This used to be implemented twice with
near-identical copy-pasted dispatch/error-handling code that could silently
drift apart -- this module is the one place that talks to cloud-files'
lists-read/lists-write interfaces.

Functions here raise CloudTransportError on any failure rather than exiting
the process directly, since the two callers report errors differently
(lists.py's die() vs. read_beautify.py's own print+exit).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface


DISPATCHES = {
    "cloud-files-lists-read": DispatchCall(
        caller_module_id="list-manager._rtx",
        target_module_id="cloud-files._rtx",
        interface="lists-read",
    ),
    "cloud-files-lists-write": DispatchCall(
        caller_module_id="list-manager._rtx",
        target_module_id="cloud-files._rtx",
        interface="lists-write",
    ),
}

_INTERFACE_KEYS = {
    "lists-read": "cloud-files-lists-read",
    "lists-write": "cloud-files-lists-write",
}


class _CloudTransportInterface(PythonMachineInterface):
    dispatches = DISPATCHES


class CloudTransportError(Exception):
    """Raised when talking to cloud-files fails for any reason."""


_DISPATCHER = _CloudTransportInterface()


def _dispatch(interface_id: str, remote_path: str, *, stdin: str | None = None) -> tuple[int, str, str]:
    try:
        key = _INTERFACE_KEYS[interface_id]
    except KeyError as exc:
        raise CloudTransportError(f"unknown cloud-files interface: {interface_id}") from exc

    try:
        result = _DISPATCHER.dispatch(
            key,
            args=[remote_path],
            stdin=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloudTransportError(f"{interface_id} timed out") from exc
    except Exception as exc:
        raise CloudTransportError(f"{interface_id} failed: {exc}") from exc

    return result.returncode, result.stdout, result.stderr


def _test_cloud_dir() -> Path | None:
    """Test-only seam: when LIST_MANAGER_TEST_CLOUD_DIR is set, download_list
    and upload_list read/write a plain file under that directory instead of
    dispatching to the real cloud-files skill (which needs live Drive OAuth
    and network access).

    This exists so tests can exercise a genuine cross-process race against a
    shared "remote" resource -- two real subprocess invocations of lists.py
    --cloud, each still downloading to its own private tempfile.mkdtemp()
    path (exactly like production), but both reading/writing the same
    underlying file here in place of Drive. It reproduces the real bug
    (independent local temp paths, shared remote resource) without needing
    real cloud credentials. Unset in all non-test use; production always goes
    through _dispatch()/the real cloud-files interfaces below.
    """
    override = os.environ.get("LIST_MANAGER_TEST_CLOUD_DIR")
    return Path(override) if override else None


def download_list(list_name: str, dest_path: Path) -> None:
    """Download list from cloud storage via cloud-files lists-read interface.

    Raises CloudTransportError on failure.
    """
    test_dir = _test_cloud_dir()
    if test_dir is not None:
        remote_file = test_dir / f"{list_name}.yaml"
        if not remote_file.exists():
            raise CloudTransportError(f"no such cloud list (test backend): {list_name}")
        shutil.copyfile(remote_file, dest_path)
        return

    remote_path = f"lists/{list_name}.yaml"
    returncode, stdout, stderr = _dispatch("lists-read", remote_path)
    if returncode != 0:
        raise CloudTransportError(f"failed to download {remote_path}: {stderr}")
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(stdout)


def upload_list(list_name: str, src_path: Path) -> None:
    """Upload list to cloud storage via cloud-files lists-write interface.

    Raises CloudTransportError on failure.
    """
    test_dir = _test_cloud_dir()
    if test_dir is not None:
        test_dir.mkdir(parents=True, exist_ok=True)
        remote_file = test_dir / f"{list_name}.yaml"
        shutil.copyfile(src_path, remote_file)
        return

    remote_path = f"lists/{list_name}.yaml"
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()
    returncode, _stdout, stderr = _dispatch("lists-write", remote_path, stdin=content)
    if returncode != 0:
        raise CloudTransportError(f"failed to upload {remote_path}: {stderr}")
