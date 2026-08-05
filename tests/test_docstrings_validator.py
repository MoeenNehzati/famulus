"""Behavior tests for the staged canonical docstring root adapter."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import pytest

from test_support.git_repository import GitTestRepository


_REPO_ROOT = Path(__file__).resolve().parents[1]
_ADAPTER_PATH = _REPO_ROOT / "validators" / "docstrings.py"
_RUNNER_PATH = _REPO_ROOT / "src" / "officina" / "_validator_snapshot.py"


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "docstrings_validator_under_test",
        _ADAPTER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "docstrings_runner_under_test",
        _RUNNER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_staged_uses_test_production_and_base_profiles(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "validators").mkdir()
    (repo / "tests" / "test_lightweight.py").write_text(
        '"""Lightweight test fixture."""\n\n'
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    for relative_path in ("src/missing.py", "validators/missing.py"):
        (repo / relative_path).write_text(
            '"""Production fixture."""\n\n'
            "def public():\n"
            "    return 1\n",
            encoding="utf-8",
        )

    errors = adapter.validate_staged(
        repo,
        [
            "validators/missing.py",
            "tests/test_lightweight.py",
            "src/missing.py",
        ],
    )

    assert not any("tests/test_lightweight.py" in error for error in errors)
    assert any(
        "src/missing.py" in error and "docstring.missing" in error
        for error in errors
    )
    assert any(
        "validators/missing.py" in error and "docstring.missing" in error
        for error in errors
    )
    assert errors == sorted(errors)


def test_validate_staged_fails_closed_for_missing_python_path(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("not Python\n", encoding="utf-8")

    assert adapter.validate_staged(repo, ["missing.py", "notes.txt"]) == [
        "missing.py: staged Python source is missing or unreadable"
    ]


def test_validate_staged_bounds_target_parse_and_decode_failures(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "decode.py").write_bytes(b"\xff\n")
    (repo / "syntax.py").write_text("def broken(:\n", encoding="utf-8")

    errors = adapter.validate_staged(repo, ["syntax.py", "decode.py"])

    assert errors[0].startswith("decode.py:")
    assert "cannot decode Python source as UTF-8" in errors[0]
    assert errors[1].startswith("syntax.py:1:")
    assert "cannot parse Python" in errors[1]


def test_validate_staged_propagates_checker_infrastructure_unicode_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _load_adapter()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text(
        '"""Valid target bytes."""\n',
        encoding="utf-8",
    )

    def fail_infrastructure(_module_path: Path):
        raise UnicodeError("invalid canonical configuration")

    monkeypatch.setattr(
        adapter,
        "validate_module_docstrings",
        fail_infrastructure,
    )

    with pytest.raises(UnicodeError, match="canonical configuration"):
        adapter.validate_staged(repo, ["module.py"])


def test_validate_without_staged_protocol_fails_closed(tmp_path: Path) -> None:
    adapter = _load_adapter()

    assert adapter.validate(tmp_path) == [
        "docstrings: staged-path-aware validator runner is required"
    ]


def test_root_runner_executes_docstring_adapter_against_staged_bytes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repository = GitTestRepository.create(repo)
    shutil.copytree(
        _REPO_ROOT / "src" / "officina",
        repo / "src" / "officina",
        dirs_exist_ok=True,
    )
    shutil.copy2(_REPO_ROOT / "repo_checks.py", repo / "repo_checks.py")
    (repo / "validators").mkdir()
    shutil.copy2(_ADAPTER_PATH, repo / "validators" / "docstrings.py")
    repository.git("add", ".")
    repository.git("commit", "--quiet", "-m", "fixture baseline")

    (repo / "src" / "staged_bad.py").write_text(
        '"""Staged integration fixture."""\n\n'
        "def public():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    added = repository.git(
        "add",
        "src/staged_bad.py",
        check=False,
    )
    assert added.returncode == 0, added.stderr.decode(errors="replace")

    results = _load_runner().run_all(
        repo,
        validator_ids=["repo/docstrings"],
    )

    assert set(results) == {"repo/docstrings"}
    assert len(results["repo/docstrings"]) == 1, "\n".join(
        results["repo/docstrings"]
    )
    assert "src/staged_bad.py" in results["repo/docstrings"][0]
    assert "docstring.missing" in results["repo/docstrings"][0]
