import ast
from pathlib import Path

import officina.repository.checks.runner as repository_checks


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_browser_inventory_matches_all_discovered_browser_modules() -> None:
    discovered = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests").rglob("*_browser.py")
    }

    assert discovered == repository_checks.CHROME_TESTS


def test_browser_inventory_is_derived_from_the_filename_convention(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_alpha_browser.py").touch()
    (tests / "test_beta.py").touch()

    assert repository_checks.discover_browser_tests(tmp_path) == {
        "tests/test_alpha_browser.py"
    }


def test_browser_tests_use_shared_runner_and_portable_paths() -> None:
    violations = []
    for path in (REPO_ROOT / "tests").rglob("*_browser.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        uses_shared_runner = False
        requires_browser = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"subprocess", "tempfile"}:
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)} imports {alias.name}"
                        )
            if (
                isinstance(node, ast.ImportFrom)
                and node.module in {"subprocess", "tempfile"}
            ):
                violations.append(
                    f"{path.relative_to(REPO_ROOT)} imports from {node.module}"
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "run_html"
            ):
                uses_shared_runner = True
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "require_chrome"
            ):
                requires_browser = True
        if not uses_shared_runner:
            violations.append(f"{path.relative_to(REPO_ROOT)} does not use run_html")
        if not requires_browser:
            violations.append(
                f"{path.relative_to(REPO_ROOT)} does not require Chrome"
            )

    assert violations == []
