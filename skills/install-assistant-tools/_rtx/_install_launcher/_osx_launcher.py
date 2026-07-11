"""macOS launcher bundle installer."""
from __future__ import annotations

from ._linux_launcher import LinuxLauncherInstaller


class OSXLauncherInstaller(LinuxLauncherInstaller):
    """macOS currently shares the Linux launcher-file contract."""
