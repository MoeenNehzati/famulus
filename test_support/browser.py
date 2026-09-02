"""Cross-platform browser discovery shared by browser-backed tests."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping

import pytest


def _native_roots(platform: str, env: Mapping[str, str]) -> tuple[Path, ...]:
    if platform == "win32":
        return tuple(
            Path(value)
            for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if (value := env.get(name))
        )
    if platform == "darwin":
        return (Path("/"),)
    return ()


def chrome_executable(
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str | None:
    """Return an installed Chromium-family executable on the current host."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    override = env.get("CHROME_BIN")
    if override and Path(override).is_file():
        return override

    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        executable = shutil.which(name)
        if executable:
            return executable

    relative = (
        Path("Google/Chrome/Application/chrome.exe")
        if platform == "win32"
        else Path("Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    )
    for root in _native_roots(platform, env):
        candidate = root / relative
        if candidate.is_file():
            return str(candidate)
    return None


def require_chrome() -> str:
    """Return Chrome, failing only when the active gate promises browser coverage."""
    executable = chrome_executable()
    if executable is not None:
        return executable
    if os.environ.get("FAMULUS_REQUIRE_BROWSER") == "1":
        pytest.fail("browser phase requires Chrome, but no supported executable was found")
    # famulus-skip: category=capability-unavailable; reason=no supported Chrome executable is installed; alternate=renderer contract tests cover payload and HTML generation
    pytest.skip("Chrome unavailable")


def run_html(
    chrome: str,
    html: str,
    *,
    virtual_time_budget: int,
    window_size: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Render one HTML document with portable temporary paths and decoding."""
    with tempfile.TemporaryDirectory(prefix="famulus-browser-") as workdir:
        root = Path(workdir)
        page = root / "page.html"
        page.write_text(html, encoding="utf-8")
        command = [
            chrome,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-crash-reporter",
            "--no-first-run",
            "--disable-background-networking",
            "--disable-component-update",
            f"--user-data-dir={root / 'profile'}",
            f"--virtual-time-budget={virtual_time_budget}",
        ]
        if window_size is not None:
            command.append(f"--window-size={window_size}")
        command.extend(("--dump-dom", page.as_uri()))
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60 if sys.platform == "win32" else 30,
        )
