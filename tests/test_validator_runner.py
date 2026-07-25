"""End-to-end tests for validator mirror Git isolation."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_support.git_repository import GitTestRepository


_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PATH = _REPO_ROOT / "validators" / "runner.py"
_SPEC = importlib.util.spec_from_file_location("validator_runner_under_test", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_RUNNER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RUNNER)


def _require_git_ok(result: subprocess.CompletedProcess[bytes]) -> None:
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _initialize_runner_repository(repo: Path) -> Path:
    repository = GitTestRepository.create(repo)
    validators = repo / "validators"
    validators.mkdir(parents=True)
    shutil.copy2(_RUNNER_PATH, validators / "runner.py")
    _require_git_ok(repository.git("add", "validators/runner.py"))
    return validators


def test_run_all_uses_staged_bytes_and_ignores_untracked_validator(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("staged\n", encoding="utf-8")
    (validators / "content_probe.py").write_text(
        "from pathlib import Path\n"
        "def validate(repo_root: Path) -> list[str]:\n"
        "    value = (repo_root / 'tracked.txt').read_text(encoding='utf-8')\n"
        "    return [] if value == 'staged\\n' else [f'unexpected bytes: {value!r}']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))
    tracked.write_text("unstaged\n", encoding="utf-8")
    sentinel = tmp_path / "untracked-imported"
    (validators / "untracked_probe.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n"
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )

    results = _RUNNER.run_all(repo, validator_ids=["repo/content_probe"])

    assert results == {}
    assert not sentinel.exists()


def test_run_all_returns_canonical_ids_and_rejects_unknown_selection(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    (validators / "probe.py").write_text(
        "def validate(repo_root): return ['problem']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    assert _RUNNER.run_all(repo, validator_ids=["repo/probe"]) == {
        "repo/probe": ["problem"]
    }
    with pytest.raises(_RUNNER.ValidatorRunnerError, match="unknown validator"):
        _RUNNER.run_all(repo, validator_ids=["repo/missing"])


def test_run_all_excludes_the_repository_validator_helper(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    (validators / "skill_md_body.py").write_text(
        "VALUE = 'shared helper'\n",
        encoding="utf-8",
    )
    (validators / "probe.py").write_text(
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    assert _RUNNER.run_all(repo) == {}


def test_run_all_rejects_an_unknown_module_without_validate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    (validators / "not_a_helper.py").write_text(
        "VALUE = 'not a validator'\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    with pytest.raises(
        _RUNNER.ValidatorRunnerError,
        match="repo/not_a_helper: validator has no callable validate",
    ):
        _RUNNER.run_all(repo)


def test_run_all_imports_staged_transitive_dependencies(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    (repo / "src").mkdir()
    helper = repo / "src" / "validator_helper.py"
    helper.write_text("VALUE = 'staged'\n", encoding="utf-8")
    (validators / "dependency_probe.py").write_text(
        "from validator_helper import VALUE\n"
        "def validate(repo_root):\n"
        "    return [] if VALUE == 'staged' else [f'unexpected helper: {VALUE}']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))
    helper.write_text("VALUE = 'unstaged'\n", encoding="utf-8")

    assert _RUNNER.run_all(
        repo,
        validator_ids=["repo/dependency_probe"],
    ) == {}


# famulus-skip: category=platform-contract; reason=POSIX executable bits are not meaningful on Windows; alternate=the isolated Git index mode tests cover the cross-platform source of truth
@pytest.mark.skipif(os.name != "posix", reason="POSIX executable mode")
def test_run_all_materializes_staged_executable_mode(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    command = repo / "command"
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    command.chmod(0o755)
    (validators / "mode_probe.py").write_text(
        "import os\n"
        "def validate(repo_root):\n"
        "    return [] if os.access(repo_root / 'command', os.X_OK) else ['not executable']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))
    command.chmod(0o644)

    assert _RUNNER.run_all(repo, validator_ids=["repo/mode_probe"]) == {}


def test_run_all_isolates_unmerged_index_and_restores_git_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    conflict = repo / "conflict.txt"
    conflict.write_text("base\n", encoding="utf-8")

    _require_git_ok(GitTestRepository(repo).git("add", "conflict.txt"))
    _require_git_ok(GitTestRepository(repo).git("commit", "-qm", "base"))
    base_branch = GitTestRepository(repo).git("symbolic-ref", "--short", "HEAD").stdout.decode().strip()

    _require_git_ok(GitTestRepository(repo).git("switch", "-qc", "other"))
    conflict.write_text("other\n", encoding="utf-8")
    _require_git_ok(GitTestRepository(repo).git("add", "conflict.txt"))
    _require_git_ok(GitTestRepository(repo).git("commit", "-qm", "other"))
    _require_git_ok(GitTestRepository(repo).git("switch", "-q", base_branch))
    conflict.write_text("base branch\n", encoding="utf-8")
    _require_git_ok(GitTestRepository(repo).git("add", "conflict.txt"))
    _require_git_ok(GitTestRepository(repo).git("commit", "-qm", "base branch"))
    assert GitTestRepository(repo).git(
        "merge",
        "other",
        check=False,
    ).returncode != 0

    validator = validators / "mirror_probe.py"
    validator.write_text(
        "from __future__ import annotations\n"
        "import os\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        f"LIVE_GIT_DIR = Path({str(repo / '.git')!r})\n"
        "def validate(repo_root: Path) -> list[str]:\n"
        "    listed = subprocess.run(['git', 'ls-files', '--stage', '-z'], cwd=repo_root, capture_output=True, check=False)\n"
        "    records = listed.stdout.decode().split('\\0')\n"
        "    stages = tuple(record.split()[2] for record in records if record.endswith('\\tconflict.txt'))\n"
        "    errors = []\n"
        "    if stages != ('1', '2', '3'):\n"
        "        errors.append(f'unmerged stages were not preserved: {stages}')\n"
        "    if Path(os.environ['GIT_DIR']).resolve() == LIVE_GIT_DIR.resolve():\n"
        "        errors.append('validator received the live Git directory')\n"
        "    for name in ('GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_OBJECT_DIRECTORY'):\n"
        "        if name in os.environ:\n"
        "            errors.append(f'validator inherited {name}')\n"
        "    removed = subprocess.run(['git', 'rm', '--cached', '-f', '--', 'conflict.txt'], cwd=repo_root, capture_output=True, check=False)\n"
        "    if removed.returncode != 0:\n"
        "        errors.append('isolated index was not writable')\n"
        "    changed_head = subprocess.run(['git', 'symbolic-ref', 'HEAD', 'refs/heads/mirror-mutated'], cwd=repo_root, capture_output=True, check=False)\n"
        "    if changed_head.returncode != 0:\n"
        "        errors.append('isolated HEAD was not writable')\n"
        "    return errors\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "validators/mirror_probe.py"))

    index_before = GitTestRepository(repo).git("ls-files", "--stage", "-z").stdout
    head_before = GitTestRepository(repo).git("symbolic-ref", "HEAD").stdout
    monkeypatch.setenv("GIT_DIR", "/sentinel/git-dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/sentinel/work-tree")
    monkeypatch.setenv("GIT_INDEX_FILE", "/sentinel/index")
    monkeypatch.setenv("GIT_COMMON_DIR", "/sentinel/common-dir")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/sentinel/object-dir")

    results = _RUNNER.run_all(repo)

    assert results == {}
    assert os.environ["GIT_DIR"] == "/sentinel/git-dir"
    assert os.environ["GIT_WORK_TREE"] == "/sentinel/work-tree"
    assert os.environ["GIT_INDEX_FILE"] == "/sentinel/index"
    assert os.environ["GIT_COMMON_DIR"] == "/sentinel/common-dir"
    assert os.environ["GIT_OBJECT_DIRECTORY"] == "/sentinel/object-dir"
    assert GitTestRepository(repo).git("ls-files", "--stage", "-z").stdout == index_before
    assert GitTestRepository(repo).git("symbolic-ref", "HEAD").stdout == head_before
