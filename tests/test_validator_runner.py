"""End-to-end tests for validator mirror Git isolation."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PATH = _REPO_ROOT / "validators" / "runner.py"
_BLUEPRINT_VALIDATOR = (
    _REPO_ROOT / "skills" / "skill-maker" / "validators" / "blueprints.py"
)
_SPEC = importlib.util.spec_from_file_location("validator_runner_under_test", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_RUNNER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RUNNER)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    ):
        env.pop(name, None)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        check=False,
    )


def _require_git_ok(result: subprocess.CompletedProcess[bytes]) -> None:
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_run_all_isolates_unmerged_index_and_restores_git_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    validators = repo / "validators"
    validators.mkdir(parents=True)
    conflict = repo / "conflict.txt"
    conflict.write_text("base\n", encoding="utf-8")

    _require_git_ok(_git(repo, "init", "-q"))
    _require_git_ok(_git(repo, "config", "user.email", "test@example.invalid"))
    _require_git_ok(_git(repo, "config", "user.name", "Test User"))
    _require_git_ok(_git(repo, "add", "conflict.txt"))
    _require_git_ok(_git(repo, "commit", "-qm", "base"))
    base_branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.decode().strip()

    _require_git_ok(_git(repo, "switch", "-qc", "other"))
    conflict.write_text("other\n", encoding="utf-8")
    _require_git_ok(_git(repo, "add", "conflict.txt"))
    _require_git_ok(_git(repo, "commit", "-qm", "other"))
    _require_git_ok(_git(repo, "switch", "-q", base_branch))
    conflict.write_text("base branch\n", encoding="utf-8")
    _require_git_ok(_git(repo, "add", "conflict.txt"))
    _require_git_ok(_git(repo, "commit", "-qm", "base branch"))
    assert _git(repo, "merge", "other").returncode != 0

    validator = validators / "mirror_probe.py"
    validator.write_text(
        "from __future__ import annotations\n"
        "import importlib.util\n"
        "import os\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        f"BLUEPRINT_VALIDATOR = Path({str(_BLUEPRINT_VALIDATOR)!r})\n"
        f"LIVE_GIT_DIR = Path({str(repo / '.git')!r})\n"
        "def validate(repo_root: Path) -> list[str]:\n"
        "    spec = importlib.util.spec_from_file_location('mirror_blueprints', BLUEPRINT_VALIDATOR)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n"
        "    tracked = module._git_tracked_files(repo_root)\n"
        "    errors = []\n"
        "    if tracked is None or tracked.get('conflict.txt') != ((\"100644\", \"1\"), (\"100644\", \"2\"), (\"100644\", \"3\")):\n"
        "        errors.append(f'unmerged stages were not preserved: {tracked}')\n"
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

    index_before = _git(repo, "ls-files", "--stage", "-z").stdout
    head_before = _git(repo, "symbolic-ref", "HEAD").stdout
    monkeypatch.setattr(_RUNNER, "_VALIDATOR_PACKAGES", [validators])
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
    assert _git(repo, "ls-files", "--stage", "-z").stdout == index_before
    assert _git(repo, "symbolic-ref", "HEAD").stdout == head_before
