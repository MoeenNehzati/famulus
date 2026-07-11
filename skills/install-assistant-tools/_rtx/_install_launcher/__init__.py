"""Platform-specific launcher bundle installation for install-assistant-tools."""
from __future__ import annotations

import sys

from ._base_launcher import LauncherInstallResult


def platform_launcher_installer(platform: str | None = None):
    """Return the launcher-file installer for the current or requested host."""
    selected = platform or sys.platform
    if selected == "win32":
        from ._windows_launcher import WindowsLauncherInstaller

        return WindowsLauncherInstaller()
    if selected == "darwin":
        from ._osx_launcher import OSXLauncherInstaller

        return OSXLauncherInstaller()
    from ._linux_launcher import LinuxLauncherInstaller

    return LinuxLauncherInstaller()


__all__ = ["LauncherInstallResult", "platform_launcher_installer"]
