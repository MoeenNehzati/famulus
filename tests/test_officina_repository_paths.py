"""Tests for repository-relative path conversion."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common.repository_paths import (
    RepositoryPathError,
    equivalent_root_relative_path,
    repository_relative_path,
    repository_relative_posix,
)


def test_equivalent_root_relative_path_accepts_lexical_descendant(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    assert equivalent_root_relative_path(root / "a" / "b", root) == Path("a/b")


def test_repository_relative_path_roots_relative_input_at_repository(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    assert repository_relative_path(Path("a/b"), root) == Path("a/b")
    assert repository_relative_posix(Path("a/b"), root) == "a/b"


def test_repository_relative_path_accepts_equivalent_root_alias(
    tmp_path: Path,
) -> None:
    physical_parent = tmp_path / "physical"
    root = physical_parent / "repo"
    root.mkdir(parents=True)
    alias_parent = tmp_path / "alias"
    try:
        alias_parent.symlink_to(physical_parent, target_is_directory=True)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=directory symlink creation is unavailable on some hosts; alternate=lexical descendant tests cover the portable containment path
        pytest.skip(f"directory symlinks unavailable: {exc}")

    assert repository_relative_path(
        root / "nested" / "file.txt",
        alias_parent / "repo",
    ) == Path("nested/file.txt")


def test_repository_relative_path_accepts_nonexistent_descendant(
    tmp_path: Path,
) -> None:
    physical_parent = tmp_path / "physical"
    root = physical_parent / "repo"
    root.mkdir(parents=True)
    alias_parent = tmp_path / "alias"
    try:
        alias_parent.symlink_to(physical_parent, target_is_directory=True)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=directory symlink creation is unavailable on some hosts; alternate=lexical nonexistent-descendant tests cover the portable containment path
        pytest.skip(f"directory symlinks unavailable: {exc}")

    assert repository_relative_path(
        root / "missing" / "file.txt",
        alias_parent / "repo",
    ) == Path("missing/file.txt")


def test_repository_relative_path_does_not_follow_descendant_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=directory symlink creation is unavailable on some hosts; alternate=lexical descendant tests cover the no-resolution contract
        pytest.skip(f"directory symlinks unavailable: {exc}")

    assert repository_relative_path(link / "file.txt", root) == Path(
        "link/file.txt"
    )


def test_repository_relative_path_rejects_outside_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(RepositoryPathError, match="outside repository"):
        repository_relative_path(tmp_path / "outside.txt", root)


def test_equivalent_root_relative_path_does_not_use_process_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert repository_relative_path(Path("child"), root) == Path("child")
    assert os.getcwd() == str(elsewhere)
