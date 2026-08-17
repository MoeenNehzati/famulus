from pathlib import Path
import runpy

import pytest

import officina.repository.checks.runner as repository_checks


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_browser_inventory_matches_all_discovered_browser_modules() -> None:
    discovered = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests").rglob("*_browser.py")
    }

    assert discovered == repository_checks.CHROME_TESTS


@pytest.mark.parametrize("relative_path", sorted(repository_checks.CHROME_TESTS))
def test_browser_modules_declare_one_shared_xdist_group(
    relative_path: str,
) -> None:
    namespace = runpy.run_path(str(REPO_ROOT / relative_path))
    marker = namespace.get("pytestmark")

    assert marker is not None
    assert marker.name == "xdist_group"
    assert marker.args == ("browser",)
    assert marker.kwargs == {}
