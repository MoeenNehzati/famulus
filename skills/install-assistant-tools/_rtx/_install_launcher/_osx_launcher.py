"""macOS launcher bundle installer."""
from __future__ import annotations

from ._linux_launcher import LinuxLauncherInstaller


class OSXLauncherInstaller(LinuxLauncherInstaller):
    """macOS currently shares the Linux launcher-file contract.

    Intentionally an empty subclass, not a stub: now that
    ``_unix_dispatcher_content``/``_unix_invoke_skill_content`` generate
    shims that only ever invoke the stable ``bootstrap/resolvers/v1/launch.py``
    resolver (never a platform-specific embedded repo/interpreter path), both
    Linux and macOS are POSIX shells calling the exact same resolver
    contract, so there is nothing macOS-specific left to override here.
    Any future ``launchctl``/LaunchAgent-specific behavior belongs to a later,
    separately scoped cross-platform-acceptance task, not this one.
    """
