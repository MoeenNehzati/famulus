from pathlib import Path
import subprocess
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from test_support import browser
from test_support.browser import chrome_executable, run_html


def test_chrome_executable_prefers_explicit_override(tmp_path: Path) -> None:
    chrome = tmp_path / "custom-chrome"
    chrome.touch()

    assert chrome_executable(env={"CHROME_BIN": str(chrome)}, platform="linux") == str(chrome)


@pytest.mark.parametrize(
    ("platform", "env", "relative_path"),
    [
        (
            "win32",
            {"PROGRAMFILES": "C:/Program Files"},
            "Google/Chrome/Application/chrome.exe",
        ),
        (
            "darwin",
            {},
            "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ),
    ],
)
def test_chrome_executable_uses_native_install_locations(
    tmp_path: Path,
    monkeypatch,
    platform: str,
    env: dict[str, str],
    relative_path: str,
) -> None:
    root = tmp_path / "root"
    chrome = root / relative_path
    chrome.parent.mkdir(parents=True)
    chrome.touch()
    monkeypatch.setattr("test_support.browser._native_roots", lambda *_args: (root,))
    monkeypatch.setattr("test_support.browser.shutil.which", lambda _name: None)

    assert chrome_executable(env=env, platform=platform) == str(chrome)


def test_required_browser_gate_fails_instead_of_skipping(monkeypatch) -> None:
    monkeypatch.setattr(browser, "chrome_executable", lambda: None)
    monkeypatch.setenv("FAMULUS_REQUIRE_BROWSER", "1")

    with pytest.raises(pytest.fail.Exception, match="browser phase requires Chrome"):
        browser.require_chrome()


def test_run_html_uses_temporary_paths_and_decodes_chrome_as_utf8(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        page = Path(url2pathname(urlparse(command[-1]).path))
        observed.update(command=command, kwargs=kwargs, page=page)
        assert page.is_file()
        assert page.read_text(encoding="utf-8") == "<html>portable</html>"
        return subprocess.CompletedProcess(command, 0, stdout="rendered", stderr="")

    monkeypatch.setattr(browser.subprocess, "run", fake_run)

    result = run_html(
        "/browser",
        "<html>portable</html>",
        virtual_time_budget=2500,
        window_size="800,600",
    )

    assert result.stdout == "rendered"
    assert observed["kwargs"] == {
        "check": True,
        "capture_output": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 10,
    }
    command = observed["command"]
    assert "--no-first-run" in command
    assert "--disable-background-networking" in command
    assert "--disable-component-update" in command
    assert "--virtual-time-budget=2500" in command
    assert "--window-size=800,600" in command
    assert not observed["page"].exists()


def test_run_html_suppresses_macos_permission_dialogs(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="rendered", stderr="")

    monkeypatch.setattr(browser.subprocess, "run", fake_run)
    monkeypatch.setattr(browser.sys, "platform", "darwin")

    run_html("/browser", "<html></html>", virtual_time_budget=2500)

    command = observed["command"]
    assert "--use-mock-keychain" in command
    assert "--disable-features=DialMediaRouteProvider" in command


def test_run_html_recovers_complete_dom_when_chrome_cleanup_times_out(monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        raise subprocess.TimeoutExpired(
            command,
            10,
            output=b"<html><body data-test-status=\"PASS\"></body></html>\n",
            stderr=b"background process did not exit\n",
        )

    monkeypatch.setattr(browser.subprocess, "run", fake_run)

    result = run_html("/browser", "<html></html>", virtual_time_budget=2500)

    assert result.returncode == 0
    assert result.stdout.endswith("</html>\n")
    assert result.stderr == "background process did not exit\n"


def test_run_html_rejects_incomplete_dom_when_chrome_times_out(monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 10, output=b"<html>", stderr=b"stuck\n")

    monkeypatch.setattr(browser.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        run_html("/browser", "<html></html>", virtual_time_budget=2500)
