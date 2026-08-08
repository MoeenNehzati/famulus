"""End-to-end tests for validator mirror Git isolation."""
from __future__ import annotations

import importlib.util
import json
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


def test_skill_validator_discovery_supports_each_layout_with_explicit_ids(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    (current / "validators").mkdir(parents=True)
    (current / "skills" / "skill-maker" / "validators").mkdir(parents=True)
    (current / "skills" / "skill-maker" / "validators" / "probe.py").write_text(
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    future = tmp_path / "future"
    (future / "validators" / "skill").mkdir(parents=True)
    (future / "validators" / "skill" / "probe.py").write_text(
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )

    assert set(_RUNNER._validator_paths(current)) == {"skill-maker/probe"}
    assert set(_RUNNER._validator_paths(future)) == {"skill-maker/probe"}


def test_skill_validator_discovery_rejects_ambiguous_dual_layout(
    tmp_path: Path,
) -> None:
    (tmp_path / "validators" / "skill").mkdir(parents=True)
    (tmp_path / "skills" / "skill-maker" / "validators").mkdir(parents=True)

    with pytest.raises(
        _RUNNER.ValidatorRunnerError,
        match="ambiguous skill validator layout",
    ):
        _RUNNER._validator_paths(tmp_path)


def test_selected_graph_consumers_share_one_automatic_blueprint_preflight(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    skill_validators = repo / "skills" / "skill-maker" / "validators"
    skill_validators.mkdir(parents=True)
    counter = tmp_path / "preflight-count"
    (skill_validators / "blueprints.py").write_text(
        "from pathlib import Path\n"
        f"COUNTER = Path({str(counter)!r})\n"
        "def preflight(repo_root):\n"
        "    count = int(COUNTER.read_text() or '0') if COUNTER.exists() else 0\n"
        "    COUNTER.write_text(str(count + 1))\n"
        "    return [], {'token': 'shared'}\n"
        "def validate_with_graph(repo_root, graph):\n"
        "    return [] if graph == {'token': 'shared'} else ['wrong graph']\n"
        "def validate(repo_root): return ['duplicate graph load']\n",
        encoding="utf-8",
    )
    for name in ("blueprint_relationships", "interface_ids"):
        (skill_validators / f"{name}.py").write_text(
            "REQUIRES_BLUEPRINT_GRAPH = True\n"
            "def validate_with_graph(repo_root, graph):\n"
            "    return [] if graph == {'token': 'shared'} else ['wrong graph']\n"
            "def validate(repo_root): return ['duplicate topology error']\n",
            encoding="utf-8",
        )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    results = _RUNNER.run_all(
        repo,
        validator_ids=[
            "skill-maker/blueprint_relationships",
            "skill-maker/interface_ids",
        ],
    )

    assert results == {}
    assert counter.read_text(encoding="utf-8") == "1"


def test_blueprint_preflight_receives_detected_repository_schema_version(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _initialize_runner_repository(repo)
    skill_validators = repo / "skills" / "skill-maker" / "validators"
    skill_validators.mkdir(parents=True)
    evidence = tmp_path / "schema-version"
    (skill_validators / "blueprints.py").write_text(
        "from pathlib import Path\n"
        f"EVIDENCE = Path({str(evidence)!r})\n"
        "def repository_schema_version(repo_root): return 5\n"
        "def preflight(repo_root, *, expected_schema_version):\n"
        "    EVIDENCE.write_text(str(expected_schema_version))\n"
        "    return [], {'token': 'shared'}\n"
        "def validate_with_graph(repo_root, graph): return []\n"
        "def validate(repo_root): return ['duplicate graph load']\n",
        encoding="utf-8",
    )
    (skill_validators / "interface_ids.py").write_text(
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        "def validate_with_graph(repo_root, graph): return []\n"
        "def validate(repo_root): return ['duplicate graph load']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    results = _RUNNER.run_all(
        repo,
        validator_ids=["skill-maker/interface_ids"],
    )

    assert results == {}
    assert evidence.read_text(encoding="utf-8") == "5"


def test_graph_preflight_errors_are_reported_only_by_blueprint_owner(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _initialize_runner_repository(repo)
    skill_validators = repo / "skills" / "skill-maker" / "validators"
    skill_validators.mkdir(parents=True)
    (skill_validators / "blueprints.py").write_text(
        "def preflight(repo_root): return ['topology error'], None\n"
        "def validate(repo_root): return ['duplicate topology error']\n",
        encoding="utf-8",
    )
    for name in ("blueprint_relationships", "interface_ids"):
        (skill_validators / f"{name}.py").write_text(
            "REQUIRES_BLUEPRINT_GRAPH = True\n"
            "def validate_with_graph(repo_root, graph): return ['consumer ran']\n"
            "def validate(repo_root): return ['duplicate topology error']\n",
            encoding="utf-8",
        )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    assert _RUNNER.run_all(
        repo,
        validator_ids=[
            "skill-maker/blueprint_relationships",
            "skill-maker/interface_ids",
        ],
    ) == {
        "skill-maker/blueprints": ["topology error"],
    }


def test_selected_graph_consumer_is_a_noop_when_preflight_has_no_graph(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _initialize_runner_repository(repo)
    skill_validators = repo / "skills" / "skill-maker" / "validators"
    skill_validators.mkdir(parents=True)
    (skill_validators / "blueprints.py").write_text(
        "def preflight(repo_root): return [], None\n"
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    (skill_validators / "interface_ids.py").write_text(
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        "def validate_with_graph(repo_root, graph): return ['must not run']\n"
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    assert _RUNNER.run_all(
        repo,
        validator_ids=["skill-maker/interface_ids"],
    ) == {}


def test_graph_consumer_mutation_is_reported_and_not_shared_with_later_consumer(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _initialize_runner_repository(repo)
    skill_validators = repo / "skills" / "skill-maker" / "validators"
    skill_validators.mkdir(parents=True)
    observation = tmp_path / "later-observation"
    (skill_validators / "blueprints.py").write_text(
        "def preflight(repo_root): return [], {'items': []}\n"
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    (skill_validators / "a_mutator.py").write_text(
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        "def validate_with_graph(repo_root, graph):\n"
        "    graph['items'].append('poison')\n"
        "    return []\n"
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    (skill_validators / "z_observer.py").write_text(
        "from pathlib import Path\n"
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        f"OBSERVATION = Path({str(observation)!r})\n"
        "def validate_with_graph(repo_root, graph):\n"
        "    OBSERVATION.write_text(repr(graph['items']))\n"
        "    return []\n"
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    results = _RUNNER.run_all(
        repo,
        validator_ids=[
            "skill-maker/a_mutator",
            "skill-maker/z_observer",
        ],
    )

    assert results == {
        "skill-maker/a_mutator": [
            "skill-maker/a_mutator: validator mutated its blueprint graph view"
        ]
    }
    assert observation.read_text(encoding="utf-8") == "[]"


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


def test_staged_validator_receives_eligible_paths_with_unborn_head(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    observation = tmp_path / "staged-paths.json"
    (repo / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("tracked\n", encoding="utf-8")
    (validators / "staged_probe.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"OBSERVATION = Path({str(observation)!r})\n"
        "def validate_staged(repo_root, staged_paths):\n"
        "    OBSERVATION.write_text(json.dumps(list(staged_paths)), encoding='utf-8')\n"
        "    return []\n"
        "def validate(repo_root): return ['ordinary entry point ran']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    results = _RUNNER.run_all(repo, validator_ids=["repo/staged_probe"])

    assert results == {}
    assert json.loads(observation.read_text(encoding="utf-8")) == [
        "module.py",
        "notes.txt",
        "validators/runner.py",
        "validators/staged_probe.py",
    ]


def test_staged_validator_receives_only_changed_regular_index_paths(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    repository = GitTestRepository(repo)
    observation = tmp_path / "changed-paths.json"
    for name in (
        "deleted.py",
        "modified.py",
        "old.py",
        "unchanged.py",
        "unstaged.py",
    ):
        (repo / name).write_text(f"NAME = {name!r}\n", encoding="utf-8")
    (validators / "staged_probe.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"OBSERVATION = Path({str(observation)!r})\n"
        "def validate_staged(repo_root, staged_paths):\n"
        "    OBSERVATION.write_text(json.dumps(list(staged_paths)), encoding='utf-8')\n"
        "    return []\n"
        "def validate(repo_root): return ['ordinary entry point ran']\n",
        encoding="utf-8",
    )
    _require_git_ok(repository.git("add", "."))
    _require_git_ok(repository.git("commit", "-qm", "baseline"))

    (repo / "modified.py").write_text("NAME = 'modified'\n", encoding="utf-8")
    (repo / "new.py").write_text("NAME = 'new'\n", encoding="utf-8")
    shutil.copy2(repo / "unchanged.py", repo / "copied.py")
    (repo / "deleted.py").unlink()
    _require_git_ok(repository.git("mv", "old.py", "renamed.py"))
    _require_git_ok(
        repository.git(
            "add",
            "modified.py",
            "new.py",
            "copied.py",
            "deleted.py",
        )
    )
    (repo / "unstaged.py").write_text("NAME = 'working tree'\n", encoding="utf-8")
    (repo / "intent.py").write_text("NAME = 'intent only'\n", encoding="utf-8")
    _require_git_ok(repository.git("add", "-N", "intent.py"))

    results = _RUNNER.run_all(repo, validator_ids=["repo/staged_probe"])

    assert results == {}
    assert json.loads(observation.read_text(encoding="utf-8")) == [
        "copied.py",
        "modified.py",
        "new.py",
        "renamed.py",
    ]


@pytest.mark.parametrize(
    ("graph_source", "expected_hook"),
    [
        ("REQUIRES_BLUEPRINT_GRAPH = True\n", "REQUIRES_BLUEPRINT_GRAPH"),
        ("def preflight(repo_root): return [], None\n", "preflight"),
        ("def validate_with_graph(repo_root, graph): return []\n", "validate_with_graph"),
    ],
)
def test_staged_validator_rejects_graph_protocol_overlap(
    tmp_path: Path,
    graph_source: str,
    expected_hook: str,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    (validators / "staged_probe.py").write_text(
        graph_source
        + "def validate_staged(repo_root, staged_paths): return []\n"
        + "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    with pytest.raises(
        _RUNNER.ValidatorRunnerError,
        match=rf"validate_staged cannot be combined with .*{expected_hook}",
    ):
        _RUNNER.run_all(repo, validator_ids=["repo/staged_probe"])


def test_captured_index_drives_both_paths_and_mirrored_bytes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _initialize_runner_repository(repo)
    repository = GitTestRepository(repo)
    module = repo / "module.py"
    module.write_text("VALUE = 'baseline'\n", encoding="utf-8")
    _require_git_ok(repository.git("add", "."))
    _require_git_ok(repository.git("commit", "-qm", "baseline"))
    module.write_text("VALUE = 'captured'\n", encoding="utf-8")
    _require_git_ok(repository.git("add", "module.py"))

    snapshot = _RUNNER._capture_repository_snapshot(repo)
    mirror_root: Path | None = None
    try:
        module.write_text("VALUE = 'later'\n", encoding="utf-8")
        _require_git_ok(repository.git("add", "module.py"))

        entries = _RUNNER._index_entries(repo, snapshot=snapshot)
        changed = _RUNNER._changed_regular_paths(repo, snapshot, entries)
        mirror_root, _ = _RUNNER._materialize_tracked_mirror(repo, entries)

        assert changed == ("module.py",)
        assert (mirror_root / "module.py").read_text(encoding="utf-8") == (
            "VALUE = 'captured'\n"
        )
    finally:
        if mirror_root is not None:
            shutil.rmtree(mirror_root)
        shutil.rmtree(snapshot.root)


def test_snapshot_capture_fails_if_head_changes_while_index_is_copied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _initialize_runner_repository(repo)
    repository = GitTestRepository(repo)
    module = repo / "module.py"
    module.write_text("VALUE = 'baseline'\n", encoding="utf-8")
    _require_git_ok(repository.git("add", "."))
    _require_git_ok(repository.git("commit", "-qm", "baseline"))
    module.write_text("VALUE = 'staged'\n", encoding="utf-8")
    _require_git_ok(repository.git("add", "module.py"))

    source_index = _RUNNER._source_git_dir(repo) / "index"
    original_copy = _RUNNER.shutil.copy2

    def copy_and_advance_head(source: Path, destination: Path) -> Path:
        copied = original_copy(source, destination)
        if Path(source) == source_index:
            _require_git_ok(repository.git("commit", "-qm", "concurrent commit"))
        return copied

    monkeypatch.setattr(_RUNNER.shutil, "copy2", copy_and_advance_head)

    with pytest.raises(
        _RUNNER.ValidatorRunnerError,
        match="HEAD changed while capturing repository snapshot",
    ):
        _RUNNER._capture_repository_snapshot(repo)


# famulus-skip: category=platform-contract; reason=POSIX preserves arbitrary filename bytes while Windows paths are Unicode; alternate=test_staged_validator_receives_eligible_paths_with_unborn_head
@pytest.mark.skipif(os.name != "posix", reason="POSIX filename bytes")
def test_staged_path_transport_preserves_non_utf8_filename_bytes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    observation = tmp_path / "non-utf8-paths.json"
    relative_path = os.fsdecode(b"module-\xff.py")
    (repo / relative_path).write_text("VALUE = 1\n", encoding="utf-8")
    (validators / "staged_probe.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"OBSERVATION = Path({str(observation)!r})\n"
        "def validate_staged(repo_root, staged_paths):\n"
        "    OBSERVATION.write_text(json.dumps(list(staged_paths)), encoding='utf-8')\n"
        "    return []\n"
        "def validate(repo_root): return ['ordinary entry point ran']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    results = _RUNNER.run_all(repo, validator_ids=["repo/staged_probe"])

    assert results == {}
    assert relative_path in json.loads(observation.read_text(encoding="utf-8"))


# famulus-skip: category=platform-contract; reason=POSIX symlink index modes are not supported on Windows; alternate=test_staged_validator_receives_only_changed_regular_index_paths
@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink mode")
def test_staged_validator_excludes_changed_symlinks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    observation = tmp_path / "symlink-paths.json"
    (repo / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "link.py").symlink_to("target.py")
    (validators / "staged_probe.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"OBSERVATION = Path({str(observation)!r})\n"
        "def validate_staged(repo_root, staged_paths):\n"
        "    OBSERVATION.write_text(json.dumps(list(staged_paths)), encoding='utf-8')\n"
        "    return []\n"
        "def validate(repo_root): return ['ordinary entry point ran']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    results = _RUNNER.run_all(repo, validator_ids=["repo/staged_probe"])

    assert results == {}
    paths = json.loads(observation.read_text(encoding="utf-8"))
    assert "target.py" in paths
    assert "link.py" not in paths


def test_staged_validator_supports_split_index_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    repository = GitTestRepository(repo)
    observation = tmp_path / "split-index-paths.json"
    (repo / "module.py").write_text("VALUE = 'baseline'\n", encoding="utf-8")
    (validators / "staged_probe.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"OBSERVATION = Path({str(observation)!r})\n"
        "def validate_staged(repo_root, staged_paths):\n"
        "    OBSERVATION.write_text(json.dumps(list(staged_paths)), encoding='utf-8')\n"
        "    return []\n"
        "def validate(repo_root): return ['ordinary entry point ran']\n",
        encoding="utf-8",
    )
    _require_git_ok(repository.git("add", "."))
    _require_git_ok(repository.git("commit", "-qm", "baseline"))
    _require_git_ok(repository.git("update-index", "--split-index"))
    (repo / "module.py").write_text("VALUE = 'staged'\n", encoding="utf-8")
    _require_git_ok(repository.git("add", "module.py"))

    results = _RUNNER.run_all(repo, validator_ids=["repo/staged_probe"])

    assert results == {}
    assert json.loads(observation.read_text(encoding="utf-8")) == ["module.py"]


def test_staged_validator_supports_linked_worktree_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    repository = GitTestRepository(repo)
    observation = tmp_path / "linked-worktree-paths.json"
    (repo / "module.py").write_text("VALUE = 'baseline'\n", encoding="utf-8")
    (validators / "staged_probe.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"OBSERVATION = Path({str(observation)!r})\n"
        "def validate_staged(repo_root, staged_paths):\n"
        "    OBSERVATION.write_text(json.dumps(list(staged_paths)), encoding='utf-8')\n"
        "    return []\n"
        "def validate(repo_root): return ['ordinary entry point ran']\n",
        encoding="utf-8",
    )
    _require_git_ok(repository.git("add", "."))
    _require_git_ok(repository.git("commit", "-qm", "baseline"))
    linked = tmp_path / "linked"
    _require_git_ok(
        repository.git(
            "worktree",
            "add",
            "--quiet",
            "-b",
            "validator-linked",
            str(linked),
        )
    )
    (linked / "module.py").write_text("VALUE = 'staged'\n", encoding="utf-8")
    _require_git_ok(GitTestRepository(linked).git("add", "module.py"))

    results = _RUNNER.run_all(linked, validator_ids=["repo/staged_probe"])

    assert results == {}
    assert json.loads(observation.read_text(encoding="utf-8")) == ["module.py"]


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


def test_run_all_excludes_only_requested_validator(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    (validators / "included.py").write_text(
        "def validate(repo_root): return ['included finding']\n",
        encoding="utf-8",
    )
    (validators / "excluded.py").write_text(
        "def validate(repo_root): return ['excluded finding']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    results = _RUNNER.run_all(
        repo,
        excluded_validator_ids=["repo/excluded"],
    )

    assert results == {"repo/included": ["included finding"]}
