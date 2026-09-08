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


@pytest.mark.parametrize(
    ("platform", "expected_timeout"),
    [("linux", 30), ("win32", 60)],
)
def test_run_html_uses_temporary_paths_and_decodes_chrome_as_utf8(
    monkeypatch,
    platform: str,
    expected_timeout: int,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        page = Path(url2pathname(urlparse(command[-1]).path))
        observed.update(command=command, kwargs=kwargs, page=page)
        assert page.is_file()
        assert page.read_text(encoding="utf-8") == "<html>portable</html>"
        return subprocess.CompletedProcess(command, 0, stdout="rendered", stderr="")

    monkeypatch.setattr(browser.subprocess, "run", fake_run)
    monkeypatch.setattr(browser.sys, "platform", platform)

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
        "timeout": expected_timeout,
    }
    command = observed["command"]
    assert "--no-first-run" in command
    assert "--disable-background-networking" in command
    assert "--disable-component-update" in command
    assert "--virtual-time-budget=2500" in command
    assert "--window-size=800,600" in command
    assert not observed["page"].exists()


def test_run_html_uses_bundled_elk_worker_under_virtual_time(monkeypatch) -> None:
    vendor = Path(__file__).parents[1] / "src/officina/visualization/html_renderer/vendor"
    elk_api = (vendor / "elk-api.js").read_text(encoding="utf-8")
    elk_bundled = (vendor / "elk.bundled.js").read_text(encoding="utf-8")
    def fake_run(command, **_kwargs):
        page = Path(url2pathname(urlparse(command[-1]).path))
        rendered = page.read_text(encoding="utf-8")
        assert f'<script id="officina-elk-client">{elk_bundled}</script>' in rendered
        assert rendered.count(elk_api) == 1
        assert '<script id="officina-viewer-runtime">/* virtual-time ELK uses the bundled worker */</script>' in rendered
        assert '<script>workerFactory: () => new Worker(ELK_WORKER_URL),</script>' in rendered
        return subprocess.CompletedProcess(command, 0, stdout="rendered", stderr="")
    monkeypatch.setattr(browser.subprocess, "run", fake_run)
    run_html(
        "/browser", f'<script type="application/json">{elk_api}</script><script id="officina-elk-client">{elk_api}</script><script id="officina-viewer-runtime">workerFactory: () => new Worker(ELK_WORKER_URL),</script><script>workerFactory: () => new Worker(ELK_WORKER_URL),</script>',
        virtual_time_budget=2500,
    )


def test_run_html_propagates_chrome_timeout_after_complete_dom(monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        raise subprocess.TimeoutExpired(
            command,
            30,
            output=b"<html><body data-test-status=\"PASS\"></body></html>\n",
            stderr=b"background process did not exit\n",
        )

    monkeypatch.setattr(browser.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        run_html("/browser", "<html></html>", virtual_time_budget=2500)
