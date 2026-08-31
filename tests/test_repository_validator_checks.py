"""End-to-end tests for validator mirror Git isolation."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ElementTree

import pytest
from officina.repository.checks.runner import ValidatorPytestPlugin
from test_support.git_repository import GitTestRepository


_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PATH = _REPO_ROOT / "src" / "officina" / "validators" / "snapshot.py"
_REPO_CHECKS_PATH = _REPO_ROOT / "repo_checks.py"
_CHECKS_IMPL_PATH = _REPO_ROOT / "src" / "officina" / "repository" / "checks" / "runner.py"
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
    officina = repo / "src" / "officina"
    common = officina / "common"
    checks = officina / "repository" / "checks"
    validator_package = officina / "validators"
    common.mkdir(parents=True)
    checks.mkdir(parents=True)
    validator_package.mkdir(parents=True)
    shutil.copy2(_RUNNER_PATH, validator_package / "snapshot.py")
    shutil.copy2(_CHECKS_IMPL_PATH, checks / "runner.py")
    shutil.copy2(_REPO_ROOT / "src" / "officina" / "__init__.py", officina / "__init__.py")
    shutil.copy2(_REPO_ROOT / "src" / "officina" / "common" / "__init__.py", common / "__init__.py")
    shutil.copy2(_REPO_ROOT / "src" / "officina" / "repository" / "__init__.py", checks.parent / "__init__.py")
    shutil.copy2(_REPO_ROOT / "src" / "officina" / "repository" / "checks" / "__init__.py", checks / "__init__.py")
    shutil.copy2(_REPO_ROOT / "src" / "officina" / "validators" / "__init__.py", validator_package / "__init__.py")
    shutil.copy2(
        _REPO_ROOT / "src" / "officina" / "repository" / "checks" / "discovery.py",
        checks / "discovery.py",
    )
    source_cache = (
        _REPO_ROOT / "src" / "officina" / "common" / "python_source_cache.py"
    )
    shutil.copy2(source_cache, common / "python_source_cache.py")
    shutil.copy2(_REPO_CHECKS_PATH, repo / "repo_checks.py")
    _require_git_ok(
        repository.git("add", "repo_checks.py", "src/officina")
    )
    return validators


@pytest.fixture(scope="session")
def runner_repository_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Provide one worker-local, unborn, staged starting repository."""
    template = tmp_path_factory.mktemp("repository-validator-checks") / "template"
    _initialize_runner_repository(template)
    return template


@pytest.fixture
def runner_repository(tmp_path: Path, runner_repository_template: Path) -> Path:
    """Copy the immutable runner repository into one test's private workspace."""
    repo = tmp_path / "repo"
    shutil.copytree(
        runner_repository_template,
        repo,
        copy_function=shutil.copy2,
        symlinks=True,
    )
    return repo


def test_run_all_public_staged_runner_contracts(
    tmp_path: Path,
    runner_repository: Path,
) -> None:
    repo = runner_repository
    validators = repo / "validators"
    repository = GitTestRepository(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("staged\n", encoding="utf-8")
    (validators / "content_probe.py").write_text(
        "from pathlib import Path\n"
        "def validate(repo_root: Path) -> list[str]:\n"
        "    value = (repo_root / 'tracked.txt').read_text(encoding='utf-8')\n"
        "    return [] if value == 'staged\\n' else [f'unexpected bytes: {value!r}']\n",
        encoding="utf-8",
    )
    (repo / "src").mkdir(exist_ok=True)
    helper = repo / "src" / "validator_helper.py"
    helper.write_text("VALUE = 'staged'\n", encoding="utf-8")
    (validators / "dependency_probe.py").write_text(
        "from validator_helper import VALUE\n"
        "def validate(repo_root):\n"
        "    return [] if VALUE == 'staged' else [f'unexpected helper: {VALUE}']\n",
        encoding="utf-8",
    )
    (validators / "probe.py").write_text(
        "def validate(repo_root): return ['problem']\n",
        encoding="utf-8",
    )
    (validators / "fixture_probe.py").write_text(
        "def validate(repo_root, request):\n"
        "    errors = []\n"
        "    if request.node.name != 'repo/fixture_probe':\n"
        "        errors.append(f'unexpected pytest node: {request.node.name}')\n"
        "    if repo_root != request.config.rootpath:\n"
        "        errors.append('repo_root fixture does not match pytest root')\n"
        "    return errors\n",
        encoding="utf-8",
    )
    (validators / "timed_probe.py").write_text(
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "fixture-calls"
    (validators / "multi_item.py").write_text(
        "from pathlib import Path\n"
        "import pytest\n"
        f"EVIDENCE = Path({str(evidence)!r})\n"
        "@pytest.fixture(scope='module')\n"
        "def prepared(repo_root):\n"
        "    prior = EVIDENCE.read_text() if EVIDENCE.exists() else ''\n"
        "    EVIDENCE.write_text(prior + 'x')\n"
        "    return 'prepared' if repo_root.is_dir() else 'missing'\n"
        "def test_first(prepared): return [f'first:{prepared}']\n"
        "def test_second(prepared): return [f'second:{prepared}']\n"
        "def validate(repo_root): return ['legacy fallback ran']\n",
        encoding="utf-8",
    )
    (repo / "shared.py").write_text("value = 1\n", encoding="utf-8")
    (validators / "a_cache_writer.py").write_text(
        "def test_cache_starts_empty(repo_root, python_source_cache):\n"
        "    if python_source_cache._entries:\n"
        "        return ['cache did not start empty']\n"
        "    python_source_cache.read_parse(repo_root / 'shared.py')\n"
        "    return []\n"
        "def validate(repo_root): return ['legacy fallback ran']\n",
        encoding="utf-8",
    )
    (validators / "b_cache_reader.py").write_text(
        "def test_cache_reuses_prior_parse(repo_root, python_source_cache):\n"
        "    before = len(python_source_cache._entries)\n"
        "    python_source_cache.read_parse(repo_root / 'shared.py')\n"
        "    after = len(python_source_cache._entries)\n"
        "    return [] if (before, after) == (1, 1) else "
        "[f'unexpected cache sizes: {(before, after)}']\n"
        "def validate(repo_root): return ['legacy fallback ran']\n",
        encoding="utf-8",
    )
    (validators / "included.py").write_text(
        "def validate(repo_root): return ['included finding']\n",
        encoding="utf-8",
    )
    (validators / "excluded.py").write_text(
        "def validate(repo_root): return ['excluded finding']\n",
        encoding="utf-8",
    )
    staged_observation = tmp_path / "unborn-staged-paths.json"
    (repo / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("tracked\n", encoding="utf-8")
    (validators / "staged_probe.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"OBSERVATION = Path({str(staged_observation)!r})\n"
        "def validate_staged(repo_root, staged_paths):\n"
        "    OBSERVATION.write_text(json.dumps(list(staged_paths)), encoding='utf-8')\n"
        "    return []\n"
        "def validate(repo_root): return ['ordinary entry point ran']\n",
        encoding="utf-8",
    )
    _require_git_ok(repository.git("add", "."))
    tracked.write_text("unstaged\n", encoding="utf-8")
    helper.write_text("VALUE = 'unstaged'\n", encoding="utf-8")
    sentinel = tmp_path / "untracked-imported"
    (validators / "untracked_probe.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n"
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    timing_output = tmp_path / "validator-timings.xml"

    # Phase 1: one unborn-repository session owns unselected discovery, staged
    # bytes and paths, imports, fixtures, aggregation, IDs, and exclusions.
    results = _RUNNER.run_all(
        repo,
        excluded_validator_ids=["repo/excluded"],
        timing_output=timing_output,
    )

    assert results == {
        "repo/included": ["included finding"],
        "repo/multi_item": ["first:prepared", "second:prepared"],
        "repo/probe": ["problem"],
    }
    assert not sentinel.exists()
    assert evidence.read_text(encoding="utf-8") == "x"
    assert json.loads(staged_observation.read_text(encoding="utf-8")) == [
        "module.py",
        "notes.txt",
        "repo_checks.py",
        "shared.py",
        "src/officina/__init__.py",
        "src/officina/common/__init__.py",
        "src/officina/common/python_source_cache.py",
        "src/officina/repository/__init__.py",
        "src/officina/repository/checks/__init__.py",
        "src/officina/repository/checks/discovery.py",
        "src/officina/repository/checks/runner.py",
        "src/officina/validators/__init__.py",
        "src/officina/validators/snapshot.py",
        "src/validator_helper.py",
        "tracked.txt",
        "validators/a_cache_writer.py",
        "validators/b_cache_reader.py",
        "validators/content_probe.py",
        "validators/dependency_probe.py",
        "validators/excluded.py",
        "validators/fixture_probe.py",
        "validators/included.py",
        "validators/multi_item.py",
        "validators/probe.py",
        "validators/staged_probe.py",
        "validators/timed_probe.py",
    ]
    testcases = list(ElementTree.parse(timing_output).getroot().iter("testcase"))
    assert sorted(
        (testcase.attrib["classname"], testcase.attrib["name"])
        for testcase in testcases
    ) == sorted(
        [
            ("validators.a_cache_writer", "test_cache_starts_empty"),
            ("validators.b_cache_reader", "test_cache_reuses_prior_parse"),
            ("validators.content_probe", "repo/content_probe"),
            ("validators.dependency_probe", "repo/dependency_probe"),
            ("validators.fixture_probe", "repo/fixture_probe"),
            ("validators.included", "repo/included"),
            ("validators.multi_item", "test_first"),
            ("validators.multi_item", "test_second"),
            ("validators.probe", "repo/probe"),
            ("validators.staged_probe", "repo/staged_probe"),
            ("validators.timed_probe", "repo/timed_probe"),
        ]
    )
    assert all(float(testcase.attrib["time"]) >= 0.0 for testcase in testcases)

    # Phase 2: the preceding run must not leak its source cache into this
    # session, while both selected validators share one cache in this session.
    selected = ["repo/a_cache_writer", "repo/b_cache_reader"]
    assert _RUNNER.run_all(repo, validator_ids=selected) == {}

    with pytest.raises(_RUNNER.ValidatorRunnerError, match="unknown validator"):
        _RUNNER._selected_validator_paths(repo, ["repo/missing"])


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


def test_graph_preflight_shares_schema_and_isolates_consumer_mutation(
    tmp_path: Path,
    runner_repository: Path,
) -> None:
    repo = runner_repository
    validators = repo / "validators"
    skill_validators = repo / "skills" / "skill-maker" / "validators"
    skill_validators.mkdir(parents=True)
    counter = tmp_path / "preflight-count"
    observation = tmp_path / "later-observation"
    (skill_validators / "blueprints.py").write_text(
        "from pathlib import Path\n"
        f"COUNTER = Path({str(counter)!r})\n"
        "def preflight(repo_root):\n"
        "    count = int(COUNTER.read_text() or '0') if COUNTER.exists() else 0\n"
        "    COUNTER.write_text(str(count + 1))\n"
        "    return [], {'token': 'shared', 'items': []}\n"
        "def validate_with_graph(repo_root, graph):\n"
        "    return [] if graph == {'token': 'shared', 'items': []} else ['wrong graph']\n"
        "def validate(repo_root): return ['duplicate graph load']\n",
        encoding="utf-8",
    )
    for name in ("blueprint_relationships", "interface_ids"):
        (skill_validators / f"{name}.py").write_text(
            "REQUIRES_BLUEPRINT_GRAPH = True\n"
            "def validate_with_graph(repo_root, graph):\n"
            "    return [] if graph == {'token': 'shared', 'items': []} else ['wrong graph']\n"
            "def validate(repo_root): return ['duplicate topology error']\n",
            encoding="utf-8",
        )
    (validators / "duplicate_subcommand_tokens.py").write_text(
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        "def validate_with_graph(repo_root, graph):\n"
        "    return [] if graph == {'token': 'shared', 'items': []} else ['wrong graph']\n"
        "def validate(repo_root): return ['duplicate graph load']\n",
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
            "repo/duplicate_subcommand_tokens",
            "skill-maker/a_mutator",
            "skill-maker/blueprint_relationships",
            "skill-maker/interface_ids",
            "skill-maker/z_observer",
        ],
    )

    assert results == {
        "skill-maker/a_mutator": [
            "skill-maker/a_mutator: validator mutated its blueprint graph view"
        ]
    }
    assert counter.read_text(encoding="utf-8") == "1"
    assert observation.read_text(encoding="utf-8") == "[]"


def test_selected_graph_consumer_is_a_noop_when_preflight_has_no_graph(
    tmp_path: Path,
    runner_repository: Path,
) -> None:
    repo = runner_repository
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

    # Phase 1: an optional missing graph is a successful no-op.
    assert _RUNNER.run_all(
        repo,
        validator_ids=["skill-maker/interface_ids"],
    ) == {}

    # Phase 2: after explicitly staging a failing owner and every consumer
    # protocol, the owner finding gates all consumers.
    sentinel = tmp_path / "fixture-consumer-ran"
    (skill_validators / "blueprints.py").write_text(
        "def preflight(repo_root): return ['topology error'], None\n"
        "def validate(repo_root): return ['duplicate topology error']\n",
        encoding="utf-8",
    )
    (skill_validators / "blueprint_relationships.py").write_text(
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        "def validate_with_graph(repo_root, graph): return ['consumer ran']\n"
        "def validate(repo_root): return ['duplicate topology error']\n",
        encoding="utf-8",
    )
    (repo / "validators" / "duplicate_subcommand_tokens.py").write_text(
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        "def validate_with_graph(repo_root, graph): return ['consumer ran']\n"
        "def validate(repo_root): return ['duplicate topology error']\n",
        encoding="utf-8",
    )
    (skill_validators / "fixture_consumer.py").write_text(
        "from pathlib import Path\n"
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        f"SENTINEL = Path({str(sentinel)!r})\n"
        "def test_graph_consumer(repo_root, python_source_cache):\n"
        "    SENTINEL.write_text('ran')\n"
        "    return ['consumer ran']\n"
        "def validate_with_graph(repo_root, graph): return ['legacy ran']\n"
        "def validate(repo_root): return ['duplicate topology error']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    assert _RUNNER.run_all(
        repo,
        validator_ids=[
            "repo/duplicate_subcommand_tokens",
            "skill-maker/blueprint_relationships",
            "skill-maker/fixture_consumer",
            "skill-maker/interface_ids",
        ],
    ) == {"skill-maker/blueprints": ["topology error"]}
    assert not sentinel.exists()


def test_validator_paths_excludes_the_repository_validator_helper(
    tmp_path: Path,
) -> None:
    validators = tmp_path / "validators"
    validators.mkdir()
    (validators / "skill_md_body.py").write_text(
        "VALUE = 'shared helper'\n",
        encoding="utf-8",
    )
    (validators / "probe.py").write_text(
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )

    assert _RUNNER._validator_paths(tmp_path) == {
        "repo/probe": validators / "probe.py",
    }


def test_direct_validator_protocol_validation_rejects_invalid_modules(
    tmp_path: Path,
) -> None:
    validator = tmp_path / "not_a_helper.py"
    validator.write_text(
        "VALUE = 'not a validator'\n",
        encoding="utf-8",
    )

    with pytest.raises(
        _RUNNER.ValidatorRunnerError,
        match="repo/not_a_helper: validator has no callable validate",
    ):
        _RUNNER._load_validator("repo/not_a_helper", validator)

    overlap = tmp_path / "staged_graph_overlap.py"
    overlap.write_text(
        "REQUIRES_BLUEPRINT_GRAPH = True\n"
        "def preflight(repo_root): return [], None\n"
        "def validate_with_graph(repo_root, graph): return []\n"
        "def validate_staged(repo_root, staged_paths): return []\n"
        "def validate(repo_root): return []\n",
        encoding="utf-8",
    )
    with pytest.raises(
        _RUNNER.ValidatorRunnerError,
        match=(
            "repo/staged_graph_overlap: validate_staged cannot be combined with "
            "REQUIRES_BLUEPRINT_GRAPH, preflight, validate_with_graph"
        ),
    ):
        ValidatorPytestPlugin(
            runner=_RUNNER,
            tracked_root=tmp_path,
            display_root=tmp_path,
            selected_paths=(("repo/staged_graph_overlap", overlap),),
            staged_paths=(),
        )


def test_staged_validator_receives_only_changed_regular_index_paths(
    tmp_path: Path,
    runner_repository: Path,
) -> None:
    repo = runner_repository
    validators = repo / "validators"
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
    with _RUNNER.staged_repository_view(repo) as view:
        baseline = view.root / ".git" / "officina-validator-baseline"
        assert (baseline / "modified.py").read_text(encoding="utf-8") == (
            "NAME = 'modified.py'\n"
        )
        assert (baseline / "renamed.py").read_text(encoding="utf-8") == (
            "NAME = 'old.py'\n"
        )
        assert not (baseline / "new.py").exists()


def test_captured_index_drives_both_paths_and_mirrored_bytes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repository = GitTestRepository.create(repo)
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
    repository = GitTestRepository.create(repo)
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


# famulus-skip: category=platform-contract; reason=this arbitrary-byte filename contract is supported by Linux but macOS and Windows paths reject the surrogate spelling used here; alternate=test_staged_validator_receives_eligible_paths_with_unborn_head
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux filename bytes")
def test_staged_path_transport_preserves_non_utf8_filename_bytes(
    tmp_path: Path,
    runner_repository: Path,
) -> None:
    repo = runner_repository
    validators = repo / "validators"
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


# famulus-skip: category=platform-contract; reason=POSIX symlink and executable index modes are not supported on Windows; alternate=test_staged_validator_receives_only_changed_regular_index_paths
@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink and executable modes")
def test_run_all_preserves_posix_index_modes(
    tmp_path: Path,
    runner_repository: Path,
) -> None:
    repo = runner_repository
    validators = repo / "validators"
    observation = tmp_path / "symlink-paths.json"
    (repo / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "link.py").symlink_to("target.py")
    command = repo / "command"
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    command.chmod(0o755)
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
    (validators / "mode_probe.py").write_text(
        "import os\n"
        "def validate(repo_root):\n"
        "    return [] if os.access(repo_root / 'command', os.X_OK) else ['not executable']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))
    command.chmod(0o644)

    results = _RUNNER.run_all(
        repo,
        validator_ids=["repo/mode_probe", "repo/staged_probe"],
    )

    assert results == {}
    paths = json.loads(observation.read_text(encoding="utf-8"))
    assert "target.py" in paths
    assert "link.py" not in paths


def test_staged_validator_supports_split_index_snapshot(
    tmp_path: Path,
    runner_repository: Path,
) -> None:
    repo = runner_repository
    validators = repo / "validators"
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


def test_staged_validator_supports_linked_worktree_index(
    tmp_path: Path,
    runner_repository: Path,
) -> None:
    repo = runner_repository
    validators = repo / "validators"
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


def test_run_all_isolates_unmerged_index_and_restores_git_environment(
    tmp_path: Path,
    runner_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = runner_repository
    validators = repo / "validators"
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
