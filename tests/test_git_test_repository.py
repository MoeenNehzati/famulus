"""Tests for the deterministic Git repository fixture."""
from __future__ import annotations

from pathlib import Path

import pytest

from test_support.git_repository import GitTestRepository


def test_create_initializes_exact_target_with_fixed_configuration(
    tmp_path: Path,
) -> None:
    target = tmp_path / "repository"

    repository = GitTestRepository.create(target, branch="fixture", filemode=False)

    assert repository.root == target.resolve()
    assert repository.git("symbolic-ref", "--short", "HEAD").stdout == b"fixture\n"
    assert repository.git("config", "user.name").stdout == b"Famulus Tests\n"
    assert (
        repository.git("config", "user.email").stdout
        == b"famulus-tests@example.invalid\n"
    )
    assert repository.git("config", "core.autocrlf").stdout == b"false\n"
    assert repository.git("config", "core.filemode").stdout == b"false\n"


def test_create_requires_nonexistent_target(tmp_path: Path) -> None:
    target = tmp_path / "repository"
    target.mkdir()

    with pytest.raises(FileExistsError):
        GitTestRepository.create(target)


def test_initialize_existing_empty_requires_empty_directory(
    tmp_path: Path,
) -> None:
    repository = GitTestRepository.initialize_existing_empty(
        tmp_path,
        branch="fixture",
    )

    assert repository.root == tmp_path.resolve()
    assert repository.git("symbolic-ref", "--short", "HEAD").stdout == b"fixture\n"
    (tmp_path / "occupied").write_text("content\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be empty"):
        GitTestRepository.initialize_existing_empty(tmp_path)


def test_git_returns_bytes_sanitizes_environment_and_does_not_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = GitTestRepository.create(tmp_path / "repository")
    (repository.root / "untracked.txt").write_text("content\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong-git-dir"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "wrong-work-tree"))

    result = repository.git("status", "--porcelain")

    assert isinstance(result.stdout, bytes)
    assert result.stdout == b"?? untracked.txt\n"
    assert repository.git("rev-parse", "--verify", "HEAD", check=False).returncode != 0
