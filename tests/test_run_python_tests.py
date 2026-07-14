from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run-python-tests.py"
SPEC = importlib.util.spec_from_file_location("run_python_tests", MODULE_PATH)
assert SPEC is not None
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


def test_runner_supplies_repo_src_pythonpath() -> None:
    assert runner._pytest_args(verbose=False) == [
        "-o",
        "pythonpath=src",
        "-q",
    ]


def mkdir(path: Path) -> None:
    path.mkdir(parents=True)


def test_precommit_discovers_skill_tests_except_install_tests(
    tmp_path: Path, monkeypatch
) -> None:
    mkdir(tmp_path / "tests")
    mkdir(tmp_path / "hooks" / "tests")
    mkdir(tmp_path / "skills" / "new-skill" / "tests")
    mkdir(tmp_path / "skills" / "skill-drift" / "tests")
    mkdir(tmp_path / "skills" / "install-assistant-tools" / "tests")
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)

    assert runner._resolve_suite("precommit") == [
        "tests",
        "hooks/tests",
        "skills/new-skill/tests",
        "skills/skill-drift/tests",
    ]


def test_full_discovers_install_tests(tmp_path: Path, monkeypatch) -> None:
    mkdir(tmp_path / "tests")
    mkdir(tmp_path / "hooks" / "tests")
    mkdir(tmp_path / "skills" / "new-skill" / "tests")
    mkdir(tmp_path / "skills" / "install-assistant-tools" / "tests")
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)

    assert runner._resolve_suite("full") == [
        "tests",
        "hooks/tests",
        "skills/install-assistant-tools/tests",
        "skills/new-skill/tests",
    ]
