import ast
from pathlib import Path

import officina.repository.checks.runner as repository_checks


REPO_ROOT = Path(__file__).resolve().parents[1]


def chrome_owner_violations(root: Path, chrome_nodes: set[str]) -> list[str]:
    """Return bare require_chrome calls outside serial browser ownership."""
    violations = []
    for path in (root / "tests").rglob("test*.py"):
        relative = path.relative_to(root).as_posix()
        browser_module = path.name.startswith("test_") and path.name.endswith(
            "_browser.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            calls_chrome = any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "require_chrome"
                for child in ast.walk(node)
            )
            selector = f"{relative}::{node.name}"
            if calls_chrome and not browser_module and selector not in chrome_nodes:
                violations.append(
                    f"{selector} calls require_chrome outside serial Chrome ownership"
                )
    return sorted(violations)


def test_browser_inventory_matches_all_discovered_browser_modules() -> None:
    discovered = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests").rglob("*_browser.py")
    }

    assert discovered == (
        repository_checks.CHROME_TESTS - repository_checks.CHROME_NODE_TESTS
    )


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


def test_direct_chrome_calls_require_serial_browser_ownership(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_misplaced.py").write_text(
        "def test_real():\n    require_chrome()\n", encoding="utf-8"
    )
    (tests / "test_owned_browser.py").write_text(
        "def test_real():\n    require_chrome()\n", encoding="utf-8"
    )

    assert chrome_owner_violations(tmp_path, chrome_nodes=set()) == [
        "tests/test_misplaced.py::test_real calls require_chrome outside serial Chrome ownership"
    ]
    (tests / "test_misplaced.py").rename(tests / "test_owned.py")
    assert chrome_owner_violations(
        tmp_path, chrome_nodes={"tests/test_owned.py::test_real"}
    ) == []


def test_repository_direct_chrome_calls_are_serially_owned() -> None:
    assert chrome_owner_violations(
        REPO_ROOT, repository_checks.CHROME_NODE_TESTS
    ) == []
