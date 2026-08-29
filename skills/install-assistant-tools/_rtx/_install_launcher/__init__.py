"""Platform-specific launcher bundle installation for install-assistant-tools."""
from __future__ import annotations

import sys

from officina.common.command_files import LauncherInstallResult

DISPATCHER_WORKFLOWS = ("machine-interface dispatch", "SKILL.md interface invocation")
INVOKE_SKILL_WORKFLOWS = ("recurring automation", "systemd/cron skill invocation")
WAKEUP_WORKFLOWS = ("guarded LLM session wakeups", "wakeup scheduling and diagnostics")
WAKEUP_COMMANDS = ("llm-wakeup", "lw")


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
