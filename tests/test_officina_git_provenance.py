from __future__ import annotations

import hashlib
import multiprocessing
import os
from pathlib import Path
import queue
import stat
import subprocess

import pytest

from officina.common import git_provenance
from officina.common.git_provenance import (
    capture_git_snapshot,
    check_commit_readiness,
    snapshot_head_matches,
)


# famulus-skip: category=platform-contract; reason=descriptor-safe opens require POSIX dir-fd support; alternate=unsupported-host readiness tests cover fail-closed behavior
requires_descriptor_safe_open = pytest.mark.skipif(
    not git_provenance._descriptor_safe_open_supported(),
    reason="descriptor-safe open is unavailable on this host",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )


def _git_bytes(repo: Path, *args: str, input_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        input=input_bytes,
        capture_output=True,
    )


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    path = tmp_path / "skills" / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", "skills/demo/SKILL.md")
    _git(tmp_path, "commit", "--quiet", "-m", "Initial commit")
    return tmp_path


def mutate_local_input(repo: Path, state: str) -> Path:
    path = repo / "skills" / "demo" / "SKILL.md"
    if state == "staged":
        path.write_text("staged\n", encoding="utf-8")
        _git(repo, "add", "skills/demo/SKILL.md")
    elif state == "unstaged":
        path.write_text("unstaged\n", encoding="utf-8")
    elif state == "untracked":
        path = repo / "skills" / "demo" / "untracked.md"
        path.write_text("untracked\n", encoding="utf-8")
    elif state == "symlink":
        path.unlink()
        path.symlink_to("replacement.md")
    else:
        raise ValueError(f"unsupported state {state!r}")
    return path


def mark_skip_worktree(repo: Path, path: Path) -> None:
    _git(repo, "update-index", "--skip-worktree", "--", path.relative_to(repo).as_posix())


def commit_unrelated_change(repo: Path) -> None:
    path = repo / "unrelated.txt"
    path.write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")
    _git(repo, "commit", "--quiet", "-m", "Unrelated commit")


def _fifo_readiness_worker(repo_text: str, path_text: str, result_queue) -> None:
    result = check_commit_readiness(
        capture_git_snapshot(Path(repo_text)), [Path(path_text)], {}
    )
    result_queue.put(result.reasons)


@requires_descriptor_safe_open
def test_unrelated_dirty_file_does_not_block_node(repo: Path) -> None:
    snapshot = capture_git_snapshot(repo)
    (repo / "unrelated.txt").write_text("dirty", encoding="utf-8")

    result = check_commit_readiness(
        snapshot,
        [repo / "skills/demo/SKILL.md"],
        {"skills/demo/SKILL.md": sha256_file(repo / "skills/demo/SKILL.md")},
    )

    assert result.stamp_worthy
    assert result.source == {
        "vcs": "git",
        "commit": snapshot.commit,
        "input_paths": ["skills/demo/SKILL.md"],
    }
    assert result.reasons == ()


@pytest.mark.parametrize("state", ["staged", "unstaged", "untracked", "symlink"])
def test_local_input_change_blocks_stamp(repo: Path, state: str) -> None:
    path = mutate_local_input(repo, state)

    result = check_commit_readiness(capture_git_snapshot(repo), [path], {})

    assert not result.stamp_worthy
    assert result.source is None
    assert result.reasons


def test_index_skip_worktree_does_not_hide_changed_bytes(repo: Path) -> None:
    path = repo / "skills" / "demo" / "SKILL.md"
    mark_skip_worktree(repo, path)
    path.write_text("changed", encoding="utf-8")

    assert not check_commit_readiness(capture_git_snapshot(repo), [path], {}).stamp_worthy


def test_staged_mode_only_change_blocks_stamp(repo: Path) -> None:
    path = repo / "skills" / "demo" / "SKILL.md"
    _git(repo, "update-index", "--chmod=+x", "--", "skills/demo/SKILL.md")

    result = check_commit_readiness(capture_git_snapshot(repo), [path], {})

    assert result.reasons == ("index-mode-differs-from-commit:skills/demo/SKILL.md",)


def test_index_only_content_change_blocks_stamp(repo: Path) -> None:
    path = repo / "skills" / "demo" / "SKILL.md"
    original_bytes = path.read_bytes()
    path.write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "skills/demo/SKILL.md")
    path.write_bytes(original_bytes)

    result = check_commit_readiness(capture_git_snapshot(repo), [path], {})

    assert result.reasons == ("index-differs-from-commit:skills/demo/SKILL.md",)


@requires_descriptor_safe_open
def test_unstaged_mode_only_change_blocks_stamp(repo: Path) -> None:
    path = repo / "skills" / "demo" / "SKILL.md"
    path.chmod(path.stat().st_mode | stat.S_IXUSR)

    result = check_commit_readiness(capture_git_snapshot(repo), [path], {})

    assert result.reasons == ("worktree-mode-differs-from-commit:skills/demo/SKILL.md",)


def test_nonzero_index_stage_blocks_stamp(repo: Path) -> None:
    relative_path = "skills/demo/SKILL.md"
    object_id = _git(repo, "rev-parse", f"HEAD:{relative_path}").stdout.strip()
    _git(repo, "read-tree", "--empty")
    _git_bytes(
        repo,
        "update-index",
        "--index-info",
        input_bytes=f"100644 {object_id} 1\t{relative_path}\n".encode("ascii"),
    )

    result = check_commit_readiness(
        capture_git_snapshot(repo), [repo / relative_path], {}
    )

    assert result.reasons == ("nonzero-index-stage:skills/demo/SKILL.md",)


def test_literal_pathspec_metacharacters_do_not_match_another_file(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "--quiet", "-m", "Add tracked text")
    path = tmp_path / ":"
    path.write_text("original\n", encoding="utf-8")

    result = check_commit_readiness(capture_git_snapshot(tmp_path), [path], {})

    assert result.reasons == ("not-tracked-at-commit::",)


@requires_descriptor_safe_open
def test_capture_from_subdirectory_uses_repository_relative_paths(repo: Path) -> None:
    snapshot = capture_git_snapshot(repo / "skills" / "demo")
    path = repo / "skills" / "demo" / "SKILL.md"

    result = check_commit_readiness(snapshot, [path, path], {})

    assert snapshot.repo_root == repo.resolve()
    assert result.source is not None
    assert result.source["input_paths"] == ["skills/demo/SKILL.md"]


def test_missing_input_is_not_stamp_worthy(repo: Path) -> None:
    result = check_commit_readiness(
        capture_git_snapshot(repo),
        [repo / "skills" / "demo" / "missing.md"],
        {},
    )

    assert not result.stamp_worthy
    assert result.source is None
    assert result.reasons == ("not-tracked-at-commit:skills/demo/missing.md",)


@requires_descriptor_safe_open
def test_expected_hash_mismatch_is_not_stamp_worthy(repo: Path) -> None:
    result = check_commit_readiness(
        capture_git_snapshot(repo),
        [repo / "skills" / "demo" / "SKILL.md"],
        {"skills/demo/SKILL.md": "sha256:" + "0" * 64},
    )

    assert not result.stamp_worthy
    assert result.source is None
    assert result.reasons == ("expected-hash-mismatch:skills/demo/SKILL.md",)


@requires_descriptor_safe_open
def test_binary_input_bytes_are_compared_without_decoding(repo: Path) -> None:
    path = repo / "skills" / "demo" / "binary.bin"
    path.write_bytes(b"\x00\xff\x80binary\n")
    _git(repo, "add", "skills/demo/binary.bin")
    _git(repo, "commit", "--quiet", "-m", "Add binary input")

    result = check_commit_readiness(
        capture_git_snapshot(repo),
        [path],
        {"skills/demo/binary.bin": sha256_file(path)},
    )

    assert result.stamp_worthy


@requires_descriptor_safe_open
def test_final_symlink_blocks_stamp_with_descriptor_safe_reason(repo: Path) -> None:
    path = repo / "skills" / "demo" / "SKILL.md"
    path.unlink()
    path.symlink_to("replacement.md")

    result = check_commit_readiness(capture_git_snapshot(repo), [path], {})

    assert result.reasons == ("unsafe-worktree-input:skills/demo/SKILL.md",)


@requires_descriptor_safe_open
def test_parent_symlink_blocks_stamp_with_descriptor_safe_reason(repo: Path) -> None:
    path = repo / "skills" / "linked" / "SKILL.md"
    path.parent.mkdir()
    path.write_text("original\n", encoding="utf-8")
    _git(repo, "add", "skills/linked/SKILL.md")
    _git(repo, "commit", "--quiet", "-m", "Add linked input")
    path.parent.rename(repo / "skills" / "linked-original")
    (repo / "skills" / "linked").symlink_to("demo", target_is_directory=True)

    result = check_commit_readiness(capture_git_snapshot(repo), [path], {})

    assert result.reasons == ("unsafe-worktree-input:skills/linked/SKILL.md",)


def test_descriptor_open_rejects_final_path_replaced_by_symlink(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not getattr(git_provenance, "_descriptor_safe_open_supported", lambda: False)():
        # famulus-skip: category=platform-contract; reason=symlink-swap injection requires descriptor-safe opens; alternate=unsupported-host readiness tests cover fail-closed behavior
        pytest.skip("descriptor-safe open is unavailable on this host")

    path = repo / "skills" / "demo" / "SKILL.md"
    replacement = path.with_name("replacement.md")
    replacement.write_text("replacement\n", encoding="utf-8")
    original_open = os.open
    replaced = False

    def replace_before_final_open(
        name: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal replaced
        if name == "SKILL.md" and dir_fd is not None and not replaced:
            replaced = True
            path.unlink()
            path.symlink_to(replacement.name)
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(git_provenance.os, "open", replace_before_final_open)
    monkeypatch.setattr(git_provenance, "_descriptor_safe_open_supported", lambda: True)

    result = check_commit_readiness(capture_git_snapshot(repo), [path], {})

    assert replaced
    assert result.reasons == ("unsafe-worktree-input:skills/demo/SKILL.md",)


@requires_descriptor_safe_open
def test_fifo_replacement_returns_without_blocking(repo: Path) -> None:
    path = repo / "skills" / "demo" / "SKILL.md"
    path.unlink()
    os.mkfifo(path)
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_fifo_readiness_worker,
        args=(str(repo), str(path), result_queue),
    )
    try:
        process.start()
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join()
            pytest.fail("readiness blocked while opening a FIFO input")
        assert process.exitcode == 0
        assert result_queue.get(timeout=1) == (
            "unsafe-worktree-input:skills/demo/SKILL.md",
        )
    except queue.Empty:
        pytest.fail("FIFO readiness worker returned no result")
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
        result_queue.close()


def test_out_of_repository_inputs_have_one_canonical_reason(repo: Path) -> None:
    outside = repo.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")

    result = check_commit_readiness(
        capture_git_snapshot(repo),
        [outside, Path("..") / outside.name, outside],
        {},
    )

    assert result.reasons == ("input-outside-repository",)


def test_non_git_snapshot_is_a_no_stamp_outcome(tmp_path: Path) -> None:
    path = tmp_path / "input.txt"
    path.write_text("input\n", encoding="utf-8")

    result = check_commit_readiness(capture_git_snapshot(tmp_path), [path], {})

    assert not result.stamp_worthy
    assert result.source is None
    assert result.reasons == ("not-a-git-repository",)


@requires_descriptor_safe_open
def test_sha256_repository_is_supported_when_available(tmp_path: Path) -> None:
    initialized = subprocess.run(
        ["git", "-C", str(tmp_path), "init", "--quiet", "--object-format=sha256"],
        capture_output=True,
    )
    if initialized.returncode != 0:
        # famulus-skip: category=capability-unavailable; reason=installed Git may lack SHA-256 repository support; alternate=SHA-1 repository provenance tests cover the shared contract
        pytest.skip("installed Git does not support SHA-256 repositories")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    path = tmp_path / "skills" / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("sha256\n", encoding="utf-8")
    _git(tmp_path, "add", "skills/demo/SKILL.md")
    _git(tmp_path, "commit", "--quiet", "-m", "Initial SHA-256 commit")

    snapshot = capture_git_snapshot(tmp_path)
    result = check_commit_readiness(snapshot, [path], {})

    assert snapshot is not None
    assert len(snapshot.commit) == 64
    assert result.stamp_worthy


def test_snapshot_head_matches_detects_new_commit(repo: Path) -> None:
    snapshot = capture_git_snapshot(repo)
    commit_unrelated_change(repo)

    assert not snapshot_head_matches(snapshot)
def test_unsupported_descriptor_capability_is_a_no_stamp_outcome(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_provenance, "_descriptor_safe_open_supported", lambda: False)

    result = check_commit_readiness(
        capture_git_snapshot(repo), [repo / "skills" / "demo" / "SKILL.md"], {}
    )

    assert result.reasons == (
        "descriptor-safe-open-unavailable:skills/demo/SKILL.md",
    )
