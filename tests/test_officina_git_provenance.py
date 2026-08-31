from __future__ import annotations

import hashlib
import multiprocessing
import os
from pathlib import Path
import queue
import stat
import subprocess

import pytest

import officina.git.provenance as git_provenance
from officina.git.provenance import (
    GitMaterializationError,
    GitSnapshot,
    capture_git_snapshot,
    check_commit_readiness,
    materialize_git_commit,
    run_git,
    snapshot_head_matches,
)
from test_support.git_repository import GitTestRepository


# famulus-skip: category=platform-contract; reason=descriptor-safe opens require POSIX dir-fd support; alternate=unsupported-host readiness tests cover fail-closed behavior
requires_descriptor_safe_open = pytest.mark.skipif(
    not git_provenance._descriptor_safe_open_supported(),
    reason="descriptor-safe open is unavailable on this host",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return GitTestRepository(repo).git(*args)


def _git_bytes(repo: Path, *args: str, input_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
    return GitTestRepository(repo).git(*args, input_bytes=input_bytes)


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_git_test_repository_preserves_exact_bytes_under_ambient_autocrlf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    repository = tmp_path / "repo"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    git = GitTestRepository.create(repository)
    tracked = repository / "tracked.txt"
    tracked.write_bytes(b"exact\r\nbytes\r\n")

    git.git("add", "tracked.txt")
    git.git("commit", "--quiet", "-m", "exact bytes")
    committed = git.git("show", "HEAD:tracked.txt").stdout

    assert committed == tracked.read_bytes()


def test_run_git_sanitizes_ambient_routing_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setenv("GIT_DIR", "/tmp/wrong-repository")
    monkeypatch.setenv("GIT_WORK_TREE", "/tmp/wrong-worktree")
    monkeypatch.setenv("GIT_INDEX_FILE", "/tmp/wrong-index")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/tmp/ambient-hooks")
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Ambient Author")
    monkeypatch.setenv("UNRELATED_ENVIRONMENT_VALUE", "retained")
    monkeypatch.setattr(git_provenance.subprocess, "run", fake_run)

    # famulus-raw-git: category=run-git-contract; reason=the test instruments the production run_git boundary itself
    result = run_git(tmp_path, "status", "--short", check=False, timeout=30)

    assert result.returncode == 0
    assert observed["timeout"] == 30
    command = observed["command"]
    assert isinstance(command, list)
    assert command == [
        "git",
        "-c",
        "core.hooksPath=",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(tmp_path.resolve()),
        "status",
        "--short",
    ]
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert "GIT_DIR" not in environment
    assert "GIT_WORK_TREE" not in environment
    assert "GIT_INDEX_FILE" not in environment
    assert "GIT_CONFIG_COUNT" not in environment
    assert "GIT_CONFIG_KEY_0" not in environment
    assert "GIT_CONFIG_VALUE_0" not in environment
    assert "GIT_LITERAL_PATHSPECS" not in environment
    assert "GIT_AUTHOR_NAME" not in environment
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["UNRELATED_ENVIRONMENT_VALUE"] == "retained"
    assert {
        name for name in environment if name.startswith("GIT_")
    } == {
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
    }


def test_capture_snapshot_ignores_ambient_git_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = tmp_path / "requested"
    decoy = tmp_path / "decoy"
    for repository, value in ((requested, "requested"), (decoy, "decoy")):
        GitTestRepository.create(repository)
        (repository / "value.txt").write_text(value, encoding="utf-8")
        _git(repository, "add", "value.txt")
        _git(repository, "commit", "--quiet", "-m", value)

    requested_commit = _git(requested, "rev-parse", "HEAD").stdout.decode().strip()
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))

    snapshot = capture_git_snapshot(requested)

    assert snapshot is not None
    assert snapshot.repo_root == requested.resolve()
    assert snapshot.commit == requested_commit


def test_run_git_disables_repository_hooks_and_local_fsmonitor(tmp_path: Path) -> None:
    GitTestRepository.initialize_existing_empty(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    GitTestRepository(tmp_path).git("add", "tracked.txt")
    hook_marker = tmp_path / "hook-ran"
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        f"#!/bin/sh\ntouch {hook_marker}\nexit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    fsmonitor_marker = tmp_path / "fsmonitor-ran"
    monitor = tmp_path / "malicious-fsmonitor"
    monitor.write_text(
        f"#!/bin/sh\ntouch {fsmonitor_marker}\nexit 1\n",
        encoding="utf-8",
    )
    monitor.chmod(0o755)
    _git(tmp_path, "config", "core.fsmonitor", str(monitor))

    # famulus-raw-git: category=run-git-contract; reason=the test verifies that production run_git suppresses repository hooks
    commit = run_git(tmp_path, "commit", "--quiet", "-m", "commit", check=False)
    # famulus-raw-git: category=run-git-contract; reason=the test verifies that production run_git suppresses a configured fsmonitor
    status = run_git(tmp_path, "status", "--short", check=False)

    assert commit.returncode == 0
    assert status.returncode == 0
    assert not hook_marker.exists()
    assert not fsmonitor_marker.exists()


def test_git_file_provenance_batch_classifies_normalized_literal_paths_in_two_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    GitTestRepository.initialize_existing_empty(tmp_path)
    tracked_literal = tmp_path / "[ab].txt"
    tracked_unusual = tmp_path / "line break.txt"
    ignored = tmp_path / "ignored.txt"
    untracked = tmp_path / "[ab].tmp"
    decoy = tmp_path / "a.tmp"
    (tmp_path / ".gitignore").write_text(
        "/ignored.txt\n",
        encoding="utf-8",
    )
    for path in (tracked_literal, tracked_unusual, ignored, untracked, decoy):
        path.write_text(f"{path.name}\n", encoding="utf-8")
    _git(
        tmp_path,
        "add",
        "--",
        ".gitignore",
        "a.tmp",
        ":(literal)[ab].txt",
        "line break.txt",
    )
    _git(tmp_path, "commit", "--quiet", "-m", "Add literal paths")
    calls: list[tuple[str, ...]] = []
    real_run_git = git_provenance.run_git

    def counting_run_git(
        repo_root: Path,
        *args: str,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return real_run_git(repo_root, *args, **kwargs)

    monkeypatch.setattr(git_provenance, "run_git", counting_run_git)

    result = git_provenance.git_file_provenance_batch(
        tmp_path,
        (
            untracked,
            tracked_literal,
            Path(tracked_unusual.name),
            ignored,
            tmp_path / "missing-parent" / ".." / tracked_literal.name,
            tracked_literal,
        ),
    )

    expected = {
        ignored: "ignored",
        tracked_literal: "tracked",
        tracked_unusual: "tracked",
        untracked: "untracked",
    }
    assert result == expected
    assert list(result) == sorted(expected, key=lambda path: path.as_posix())
    assert len(calls) <= 2


# famulus-skip: category=platform-contract; reason=Windows forbids newline characters in filenames; alternate=test_git_file_provenance_batch_classifies_normalized_literal_paths_in_two_calls covers portable batch classification
@pytest.mark.skipif(os.name != "posix", reason="newline filenames require POSIX")
def test_git_file_provenance_batch_parses_nul_delimited_newline_path(
    tmp_path: Path,
) -> None:
    GitTestRepository.initialize_existing_empty(tmp_path)
    tracked = tmp_path / "line\nbreak.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "line\nbreak.txt")
    _git(tmp_path, "commit", "--quiet", "-m", "Add newline path")

    result = git_provenance.git_file_provenance_batch(tmp_path, (tracked,))

    assert result == {tracked: "tracked"}


def test_git_file_provenance_batch_rejects_fatal_tracked_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fatal_tracked_query(
        _repo_root: Path,
        *args: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            128,
            b"",
            b"fatal: tracked lookup failed\n",
        )

    monkeypatch.setattr(git_provenance, "run_git", fatal_tracked_query)

    with pytest.raises(ValueError, match="fatal: tracked lookup failed"):
        git_provenance.git_file_provenance_batch(
            tmp_path,
            (tmp_path / "input.txt",),
        )

    assert len(calls) == 1


def test_git_file_provenance_batch_rejects_fatal_ignore_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fatal_ignore_query(
        _repo_root: Path,
        *args: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        if args[0] == "ls-files":
            return subprocess.CompletedProcess(args, 0, b"", b"")
        return subprocess.CompletedProcess(
            args,
            128,
            b"",
            b"fatal: ignore lookup failed\n",
        )

    monkeypatch.setattr(git_provenance, "run_git", fatal_ignore_query)

    with pytest.raises(ValueError, match="fatal: ignore lookup failed"):
        git_provenance.git_file_provenance_batch(
            tmp_path,
            (tmp_path / "input.txt",),
        )

    assert [args[0] for args in calls] == ["ls-files", "check-ignore"]


def test_materialize_git_commit_rejects_escaping_symlink_before_writes(
    repo: Path,
) -> None:
    repository = GitTestRepository(repo)
    object_id = repository.git(
        "hash-object",
        "-w",
        "--stdin",
        input_bytes=b"../../outside",
    ).stdout.decode("ascii").strip()
    repository.git(
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{object_id},escape-link",
    )
    repository.git("commit", "--quiet", "-m", "unsafe symlink")
    commit = repository.git("rev-parse", "HEAD").stdout.decode("ascii").strip()
    destination = repo / "materialized"
    destination.mkdir(mode=0o700)

    with pytest.raises(GitMaterializationError, match="symlink escapes"):
        materialize_git_commit(repo, commit, destination)

    assert not tuple(destination.iterdir())


def test_materialize_git_commit_ignores_export_attribute_transformations(
    repo: Path,
) -> None:
    (repo / ".gitattributes").write_text(
        "hidden.txt export-ignore\ntemplate.txt export-subst\n",
        encoding="utf-8",
    )
    (repo / "hidden.txt").write_text("must remain\n", encoding="utf-8")
    (repo / "template.txt").write_text("$Format:%H$\n", encoding="utf-8")
    executable = repo / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _git(repo, "add", ".gitattributes", "hidden.txt", "template.txt", "run.sh")
    _git(repo, "commit", "--quiet", "-m", "export attributes")
    commit = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    (repo / "skills" / "demo" / "SKILL.md").write_text(
        "uncommitted\n", encoding="utf-8"
    )
    destination = repo / "materialized"
    destination.mkdir(mode=0o700)

    paths = materialize_git_commit(repo, commit, destination)

    assert paths == (
        Path(".gitattributes"),
        Path("hidden.txt"),
        Path("run.sh"),
        Path("skills/demo/SKILL.md"),
        Path("template.txt"),
    )
    assert (destination / "skills" / "demo" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "original\n"
    assert (destination / "hidden.txt").read_text(encoding="utf-8") == "must remain\n"
    assert (destination / "template.txt").read_text(
        encoding="utf-8"
    ) == "$Format:%H$\n"
    if os.name == "posix":
        assert (destination / "run.sh").stat().st_mode & stat.S_IXUSR


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    GitTestRepository.initialize_existing_empty(tmp_path)
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
    else:
        raise ValueError(f"unsupported state {state!r}")
    return path


def mark_skip_worktree(repo: Path, path: Path) -> None:
    _git(repo, "update-index", "--skip-worktree", "--", path.relative_to(repo).as_posix())


def _assert_and_restore_readiness_baseline(
    repo: Path,
    *,
    path: Path,
    untracked: Path,
    baseline_bytes: bytes,
    baseline_mode: int,
    baseline_index: bytes,
) -> None:
    _git(repo, "reset", "--quiet", "--mixed", "HEAD")
    _git(
        repo,
        "update-index",
        "--no-skip-worktree",
        "--",
        path.relative_to(repo).as_posix(),
    )
    if path.is_symlink() or (path.exists() and not path.is_file()):
        path.unlink()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(baseline_bytes)
    else:
        path.write_bytes(baseline_bytes)
    path.chmod(baseline_mode)
    if untracked.is_symlink() or untracked.exists():
        untracked.unlink()

    assert path.exists() and path.is_file() and not path.is_symlink()
    assert path.read_bytes() == baseline_bytes
    assert stat.S_IMODE(path.stat().st_mode) == baseline_mode
    assert not untracked.exists() and not untracked.is_symlink()
    assert _git(repo, "ls-files", "--stage", "-z").stdout == baseline_index
    skip_state = _git(
        repo,
        "ls-files",
        "-v",
        "--",
        path.relative_to(repo).as_posix(),
    ).stdout
    assert skip_state.startswith(b"H ")


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
def test_commit_readiness_outcomes_share_one_repository_history(repo: Path) -> None:
    path = repo / "skills" / "demo" / "SKILL.md"
    snapshot = capture_git_snapshot(repo)
    (repo / "unrelated.txt").write_text("dirty", encoding="utf-8")

    result = check_commit_readiness(
        snapshot,
        [path],
        {"skills/demo/SKILL.md": sha256_file(path)},
    )

    assert result.stamp_worthy
    assert result.source == {
        "vcs": "git",
        "commit": snapshot.commit,
        "input_paths": ["skills/demo/SKILL.md"],
    }
    assert result.reasons == ()

    captured_from_subdirectory = capture_git_snapshot(repo / "skills" / "demo")
    deduplicated = check_commit_readiness(
        captured_from_subdirectory,
        [path, path],
        {},
    )
    assert captured_from_subdirectory is not None
    assert captured_from_subdirectory.repo_root == repo.resolve()
    assert deduplicated.source is not None
    assert deduplicated.source["input_paths"] == ["skills/demo/SKILL.md"]

    missing = check_commit_readiness(
        snapshot,
        [repo / "skills" / "demo" / "missing.md"],
        {},
    )
    assert not missing.stamp_worthy
    assert missing.source is None
    assert missing.reasons == ("not-tracked-at-commit:skills/demo/missing.md",)

    hash_mismatch = check_commit_readiness(
        snapshot,
        [path],
        {"skills/demo/SKILL.md": "sha256:" + "0" * 64},
    )
    assert not hash_mismatch.stamp_worthy
    assert hash_mismatch.source is None
    assert hash_mismatch.reasons == (
        "expected-hash-mismatch:skills/demo/SKILL.md",
    )

    outside = repo.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    outside_result = check_commit_readiness(
        snapshot,
        [outside, Path("..") / outside.name, outside],
        {},
    )
    assert outside_result.reasons == ("input-outside-repository",)

    # Binary readiness is last because it advances HEAD for this shared history.
    binary = repo / "skills" / "demo" / "binary.bin"
    binary.write_bytes(b"\x00\xff\x80binary\n")
    _git(repo, "add", "skills/demo/binary.bin")
    _git(repo, "commit", "--quiet", "-m", "Add binary input")
    binary_snapshot = capture_git_snapshot(repo)
    binary_result = check_commit_readiness(
        binary_snapshot,
        [binary],
        {"skills/demo/binary.bin": sha256_file(binary)},
    )
    assert binary_result.stamp_worthy


def test_native_confined_reader_supports_readiness_and_rejects_changed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    path = repo / "skills" / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("original\n", encoding="utf-8")
    snapshot = GitSnapshot(repo.resolve(), "a" * 40)
    monkeypatch.setattr(
        git_provenance,
        "_tree_entry",
        lambda _snapshot, _relative: ("100644", "b" * 40),
    )
    monkeypatch.setattr(
        git_provenance,
        "_index_entries",
        lambda _root, _relative: (("100644", "b" * 40, "0"),),
    )
    monkeypatch.setattr(
        git_provenance,
        "_commit_blob",
        lambda _root, _object_id: b"original\n",
    )
    observed: list[bool] = []

    def native_read(
        target: Path,
        *,
        allowed_root: Path,
        allow_non_atomic: bool = False,
    ) -> bytes:
        assert target == path
        assert allowed_root == repo.resolve()
        observed.append(allow_non_atomic)
        return target.read_bytes()

    monkeypatch.setattr(git_provenance, "_use_native_confined_read", lambda: True)
    monkeypatch.setattr(git_provenance, "read_regular_file_bytes", native_read)

    result = check_commit_readiness(
        snapshot,
        [path],
        {},
        allow_non_atomic=True,
    )

    assert result.stamp_worthy
    assert observed == [True]
    path.write_text("changed\n", encoding="utf-8")

    changed = check_commit_readiness(snapshot, [path], {})

    assert changed.reasons == (
        "worktree-differs-from-commit:skills/demo/SKILL.md",
    )
    assert observed == [True, False]


def test_mutating_readiness_transitions_restore_the_exact_repository_state(
    repo: Path,
) -> None:
    path = repo / "skills" / "demo" / "SKILL.md"
    untracked = repo / "skills" / "demo" / "untracked.md"
    snapshot = capture_git_snapshot(repo)
    baseline_bytes = path.read_bytes()
    baseline_mode = stat.S_IMODE(path.stat().st_mode)
    baseline_index = _git(repo, "ls-files", "--stage", "-z").stdout

    def reset() -> None:
        _assert_and_restore_readiness_baseline(
            repo,
            path=path,
            untracked=untracked,
            baseline_bytes=baseline_bytes,
            baseline_mode=baseline_mode,
            baseline_index=baseline_index,
        )

    reset()
    mutate_local_input(repo, "staged")
    assert check_commit_readiness(snapshot, [path], {}).reasons == (
        "index-differs-from-commit:skills/demo/SKILL.md",
    )

    reset()
    mutate_local_input(repo, "unstaged")
    assert check_commit_readiness(snapshot, [path], {}).reasons == (
        "worktree-differs-from-commit:skills/demo/SKILL.md",
    )

    reset()
    mutate_local_input(repo, "untracked")
    assert check_commit_readiness(snapshot, [untracked], {}).reasons == (
        "not-tracked-at-commit:skills/demo/untracked.md",
    )

    reset()
    mark_skip_worktree(repo, path)
    path.write_text("changed", encoding="utf-8")
    assert check_commit_readiness(snapshot, [path], {}).reasons == (
        "worktree-differs-from-commit:skills/demo/SKILL.md",
    )

    reset()
    _git(repo, "update-index", "--chmod=+x", "--", "skills/demo/SKILL.md")
    assert check_commit_readiness(snapshot, [path], {}).reasons == (
        "index-mode-differs-from-commit:skills/demo/SKILL.md",
    )

    reset()
    path.write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "skills/demo/SKILL.md")
    path.write_bytes(baseline_bytes)
    assert check_commit_readiness(snapshot, [path], {}).reasons == (
        "index-differs-from-commit:skills/demo/SKILL.md",
    )

    if git_provenance._descriptor_safe_open_supported():
        reset()
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        assert check_commit_readiness(snapshot, [path], {}).reasons == (
            "worktree-mode-differs-from-commit:skills/demo/SKILL.md",
        )

    reset()
    relative_path = "skills/demo/SKILL.md"
    object_id = (
        _git(repo, "rev-parse", f"HEAD:{relative_path}")
        .stdout.decode()
        .strip()
    )
    _git(repo, "read-tree", "--empty")
    _git_bytes(
        repo,
        "update-index",
        "--index-info",
        input_bytes=f"100644 {object_id} 1\t{relative_path}\n".encode("ascii"),
    )
    assert check_commit_readiness(snapshot, [path], {}).reasons == (
        "nonzero-index-stage:skills/demo/SKILL.md",
    )

    reset()


def test_literal_pathspec_metacharacters_do_not_match_another_file(tmp_path: Path) -> None:
    GitTestRepository.initialize_existing_empty(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "--quiet", "-m", "Add tracked text")
    path = tmp_path / "[t]racked.txt"
    path.write_text("original\n", encoding="utf-8")

    result = check_commit_readiness(capture_git_snapshot(tmp_path), [path], {})

    assert result.reasons == ("not-tracked-at-commit:[t]racked.txt",)


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


def test_non_git_snapshot_is_a_no_stamp_outcome(tmp_path: Path) -> None:
    path = tmp_path / "input.txt"
    path.write_text("input\n", encoding="utf-8")

    result = check_commit_readiness(capture_git_snapshot(tmp_path), [path], {})

    assert not result.stamp_worthy
    assert result.source is None
    assert result.reasons == ("not-a-git-repository",)


@requires_descriptor_safe_open
def test_sha256_repository_is_supported_when_available(tmp_path: Path) -> None:
    # famulus-raw-git: category=object-format; reason=the test probes whether this Git supports SHA-256 repository initialization
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


def test_unsupported_descriptor_capability_is_a_no_stamp_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    path = repo / "skills" / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("original\n", encoding="utf-8")
    snapshot = GitSnapshot(repo.resolve(), "a" * 40)
    monkeypatch.setattr(
        git_provenance,
        "_tree_entry",
        lambda _snapshot, _relative: ("100644", "b" * 40),
    )
    monkeypatch.setattr(
        git_provenance,
        "_index_entries",
        lambda _root, _relative: (("100644", "b" * 40, "0"),),
    )
    monkeypatch.setattr(
        git_provenance,
        "_commit_blob",
        lambda _root, _object_id: b"original\n",
    )
    monkeypatch.setattr(git_provenance, "_use_native_confined_read", lambda: False)
    monkeypatch.setattr(git_provenance, "_descriptor_safe_open_supported", lambda: False)

    result = check_commit_readiness(snapshot, [path], {})

    assert result.reasons == (
        "descriptor-safe-open-unavailable:skills/demo/SKILL.md",
    )
