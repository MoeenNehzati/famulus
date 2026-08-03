"""Host scheduler capability discovery for Linux, macOS, and Windows."""

from __future__ import annotations

import shutil
import sys


def scheduler_capability() -> tuple[bool, str]:
    """Report the native scheduler command expected on the current host."""

    if sys.platform.startswith("linux"):
        command = "systemctl"
    elif sys.platform == "darwin":
        command = "launchctl"
    elif sys.platform == "win32":
        command = "schtasks"
    else:
        return False, f"unsupported platform: {sys.platform}"
    path = shutil.which(command)
    return (path is not None, path or f"{command} not found")
