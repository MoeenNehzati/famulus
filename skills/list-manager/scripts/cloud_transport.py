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

import subprocess
from pathlib import Path


class CloudTransportError(Exception):
    """Raised when talking to cloud-files fails for any reason."""


def _dispatch(interface_id: str, remote_path: str, *, stdin: str | None = None) -> tuple[int, str, str]:
    try:
        from script_dispatcher import InvocationError, dispatch
    except ImportError as exc:
        raise CloudTransportError(
            "script_dispatcher is not installed. Re-run install-assistant-tools "
            "to install the shared dispatcher package."
        ) from exc

    try:
        result = dispatch(
            caller_skill="list-manager",
            target_skill="cloud-files",
            script_interface=interface_id,
            args=[remote_path],
            stdin=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except InvocationError as exc:
        raise CloudTransportError(
            f"invalid dispatcher request for cloud-files:{interface_id}: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CloudTransportError(f"{interface_id} timed out") from exc
    except Exception as exc:
        raise CloudTransportError(f"{interface_id} failed: {exc}") from exc

    return result.returncode, result.stdout, result.stderr


def download_list(list_name: str, dest_path: Path) -> None:
    """Download list from cloud storage via cloud-files lists-read interface.

    Raises CloudTransportError on failure.
    """
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
    remote_path = f"lists/{list_name}.yaml"
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()
    returncode, _stdout, stderr = _dispatch("lists-write", remote_path, stdin=content)
    if returncode != 0:
        raise CloudTransportError(f"failed to upload {remote_path}: {stderr}")
