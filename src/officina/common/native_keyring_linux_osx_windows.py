"""Platform vocabulary for the audited native keyring backends."""
from __future__ import annotations

import sys


NATIVE_BACKENDS = {
    "linux": {
        "keyring.backends.SecretService.Keyring",
        "keyring.backends.libsecret.Keyring",
    },
    "darwin": {"keyring.backends.macOS.Keyring"},
    "win32": {"keyring.backends.Windows.WinVaultKeyring"},
}


def current_platform_name() -> str:
    """Return the interpreter's canonical platform selector."""
    return sys.platform
