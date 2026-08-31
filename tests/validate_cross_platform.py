"""Tests for the canonical cross-platform validator."""
from __future__ import annotations

import ast
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from validators.cross_platform import validate  # noqa: E402
from validators import cross_platform as module_under_test  # noqa: E402
import officina.common.python_source_cache as cache_module  # noqa: E402
from officina.common.python_source_cache import PythonSourceCache  # noqa: E402
from officina.validators.snapshot import _load_validator  # noqa: E402


def _copy_module(repo_root: Path, source_name: str = "get-weather") -> Path:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint-schema",
        repo_root / "references" / "blueprint-schema",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )
    target = repo_root / "skills" / source_name
    shutil.copytree(
        REPO_ROOT / "skills" / source_name,
        target,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            ".pooled-blueprint-review.yaml",
        ),
    )
    return target


def _write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_overlapping_internal_passes_parse_one_python_file_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "skills" / "demo" / "shared.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "import subprocess\n"
        "subprocess.run(['grep'])\n"
        "TARGET = '_rtx/_worker.py:Interface'\n",
        encoding="utf-8",
    )
    original_parse = cache_module.ast.parse
    original_walk = module_under_test.ast.walk
    parse_calls: list[str] = []
    walk_calls: list[ast.AST] = []

    def counting_parse(
        source: str,
        filename: str = "<unknown>",
        mode: str = "exec",
        **kwargs,
    ):
        if filename == str(path):
            parse_calls.append(filename)
        return original_parse(source, filename=filename, mode=mode, **kwargs)

    def counting_walk(tree: ast.AST):
        walk_calls.append(tree)
        return original_walk(tree)

    monkeypatch.setattr(cache_module.ast, "parse", counting_parse)
    monkeypatch.setattr(module_under_test.ast, "walk", counting_walk)

    assert module_under_test._validate(
        tmp_path,
        None,
        PythonSourceCache(tmp_path),
    ) == [
        "skills/demo/shared.py:2: command `grep` is not cross-platform",
        "skills/demo/shared.py:3: composite Python process target is not "
        "allowed; carry gateway path and process entry separately",
    ]
    assert parse_calls == [str(path)]
    assert len(walk_calls) == 1


def test_validation_builds_one_inventory_scan_per_live_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "skills" / "demo" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    test = tmp_path / "tests" / "test_git.py"
    test.parent.mkdir()
    test.write_text(
        "from officina.git.provenance import run_git\n"
        "run_git(repo, 'status')\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "officina" / "worker.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "TARGET = '_rtx/_worker.py:Interface'\n",
        encoding="utf-8",
    )
    original_rglob = Path.rglob
    scans: list[Path] = []

    def counting_rglob(root: Path, pattern: str):
        scans.append(root)
        return original_rglob(root, pattern)

    monkeypatch.setattr(Path, "rglob", counting_rglob)

    errors = validate(tmp_path)

    assert any("shell scripts are not allowed" in error for error in errors)
    assert any("direct run_git call requires" in error for error in errors)
    assert any("composite Python process target" in error for error in errors)
    assert scans.count(tmp_path / "skills") == 1
    assert scans.count(tmp_path / "tests") == 1
    assert scans.count(tmp_path / "src") == 1


def test_inventory_preserves_each_consumer_legacy_root_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths_by_root: dict[Path, list[Path]] = {}

    def add_paths(root_name: str, *relative_paths: str) -> list[Path]:
        root = tmp_path / root_name
        created: list[Path] = []
        for relative_path in relative_paths:
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("value = 1\n", encoding="utf-8")
            created.append(path)
        paths_by_root[root] = created
        return created

    src_paths = add_paths("src", "z.py", "a.py")
    validator_paths = add_paths("validators", "z.py", "a.py")
    script_paths = add_paths("scripts", "z.py", "a.py")
    documentation_paths = add_paths("docs_tooling", "z.py", "a.py")
    skill_paths = add_paths(
        "skills",
        "demo/z.py",
        "demo/a.py",
        "demo/tests/z.py",
        "demo/tests/a.py",
        "demo/_rtx/tests/z.py",
        "demo/_rtx/tests/a.py",
    )
    repository_test_paths = add_paths("tests", "z.py", "a.py")

    def controlled_rglob(root: Path, pattern: str):
        assert pattern == "*"
        return iter(paths_by_root[root])

    monkeypatch.setattr(Path, "rglob", controlled_rglob)

    inventory = module_under_test._build_path_inventory(tmp_path)

    assert inventory.skill_files == tuple(skill_paths[:2])
    assert inventory.ordinary_test_files == (
        repository_test_paths[1],
        repository_test_paths[0],
        skill_paths[5],
        skill_paths[4],
        skill_paths[3],
        skill_paths[2],
    )
    assert inventory.live_python_files == (
        src_paths[1],
        src_paths[0],
        validator_paths[1],
        validator_paths[0],
        script_paths[1],
        script_paths[0],
        documentation_paths[1],
        documentation_paths[0],
        skill_paths[1],
        skill_paths[0],
    )


def test_canonical_dynamic_loader_can_import_validator() -> None:
    module, validate_fn = _load_validator(
        "repo/cross-platform-focused",
        REPO_ROOT / "validators" / "cross_platform.py",
    )

    assert validate_fn is module.validate


def test_clean_module_passes_through_symlinked_repository_root(
    tmp_path: Path,
) -> None:
    physical_parent = tmp_path / "physical-parent"
    physical_root = physical_parent / "repository"
    _copy_module(physical_root)
    logical_parent = tmp_path / "logical-parent"
    try:
        logical_parent.symlink_to(physical_parent, target_is_directory=True)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=some Windows runners deny directory-symlink creation; alternate=Linux and macOS exercise the parent-alias regression
        pytest.skip(f"directory symlinks unavailable: {exc}")
    logical_root = logical_parent / "repository"

    assert validate(logical_root) == []


def test_shell_script_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "bad-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    errors = validate(tmp_path)

    assert any("shell scripts are not allowed" in error for error in errors)


def test_copied_module_portability_violations_share_one_repository_scan(
    tmp_path: Path,
) -> None:
    skill = _copy_module(tmp_path)
    path = skill / "blueprint.yaml"
    module = yaml.safe_load(path.read_text(encoding="utf-8"))
    module["authority"].setdefault("suggested_permissions", {})["bash"] = [
        {
            "command": ["grep"],
            "reason": "Invalid portable permission.",
        },
        {
            "command": ["python3"],
            "args_prefix": ["_rtx/run.sh"],
            "reason": "Invalid shell entrypoint.",
        }
    ]
    _write_yaml(path, module)

    source_path = skill / "_rtx" / "blueprints" / "rtx-weather-client.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["runtime_dependencies"] = [
        {
            "kind": "binary",
            "name": "grep",
            "version": "any",
            "platforms": {
                "linux": True,
                "macos": True,
                "windows": True,
            },
            "reason": "Invalid all-platform command.",
        }
    ]
    _write_yaml(source_path, source)

    command = [
        "python3",
        "-m",
        "officina.runtime.python_machine_interface_runner",
        "_rtx/_worker.py:Interface",
    ]
    child_path = skill / "_rtx" / "blueprint.yaml"
    child = yaml.safe_load(child_path.read_text(encoding="utf-8"))
    child["authority"].setdefault("suggested_permissions", {})["bash"] = [
        {"command": command, "reason": "Legacy composite target."}
    ]
    _write_yaml(child_path, child)

    projection = tmp_path / "skills" / "demo" / ".pooled-blueprint-review.yaml"
    projection.parent.mkdir(parents=True)
    _write_yaml(
        projection,
        {"suggested_permissions": {"bash": [{"command": command}]}},
    )

    errors = validate(tmp_path)

    assert any(
        "skills/get-weather/blueprint.yaml" in error
        and "command `grep` is not cross-platform" in error
        for error in errors
    )
    assert any(
        "skills/get-weather/blueprint.yaml" in error
        and "shell script token `_rtx/run.sh`" in error
        for error in errors
    )
    assert any(
        "skills/get-weather/_rtx/blueprints/rtx-weather-client.yaml" in error
        and "command `grep` is not cross-platform" in error
        for error in errors
    )
    assert any(
        "skills/get-weather/_rtx/blueprint.yaml" in error
        and "composite runner permission target" in error
        for error in errors
    )
    assert any(
        "skills/demo/.pooled-blueprint-review.yaml" in error
        and "composite runner permission target" in error
        for error in errors
    )


def test_python_macos_subprocess_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "bad-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.py").write_text(
        "import subprocess\nsubprocess.run(['osascript', '-e', 'beep'])\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("command `osascript` is not cross-platform" in error for error in errors)


def test_platform_named_python_file_may_use_platform_command(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "skills" / "scheduler-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "_osx_backend.py").write_text(
        "import subprocess\nsubprocess.run(['launchctl', 'list'])\n",
        encoding="utf-8",
    )

    assert validate(tmp_path) == []


def test_python_shell_true_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "bad-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.py").write_text(
        "import subprocess\nsubprocess.run('echo hi', shell=True)\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("shell=True is not allowed" in error for error in errors)


def test_raw_git_in_ordinary_test_requires_local_annotation(tmp_path: Path) -> None:
    test = tmp_path / "tests" / "test_bad_git.py"
    test.parent.mkdir(parents=True)
    test.write_text(
        "import subprocess\n"
        "subprocess.run(['git', 'status'], check=True)\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("raw Git call requires" in error for error in errors)


def test_raw_git_annotation_is_statement_local_and_category_checked(
    tmp_path: Path,
) -> None:
    test = tmp_path / "tests" / "test_git.py"
    test.parent.mkdir(parents=True)
    test.write_text(
        "import subprocess\n"
        "# famulus-raw-git: category=hooks; reason=observe the real hooksPath\n"
        "subprocess.run(['git', 'config', 'core.hooksPath'], check=True)\n"
        "subprocess.run(['git', 'status'], check=True)\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert len([error for error in errors if "raw Git call requires" in error]) == 1
    assert ":4:" in next(error for error in errors if "raw Git call requires" in error)


def test_raw_git_annotation_rejects_unknown_category(tmp_path: Path) -> None:
    test = tmp_path / "tests" / "test_git.py"
    test.parent.mkdir(parents=True)
    test.write_text(
        "import subprocess\n"
        "# famulus-raw-git: category=convenience; reason=short fixture\n"
        "subprocess.run(['git', 'status'], check=True)\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("unknown famulus-raw-git category `convenience`" in error for error in errors)


def test_direct_run_git_in_ordinary_and_child_runtime_tests_requires_annotation(
    tmp_path: Path,
) -> None:
    ordinary_test = tmp_path / "skills" / "demo" / "tests" / "test_git.py"
    child_runtime_test = (
        tmp_path / "skills" / "demo" / "_rtx" / "tests" / "test_git.py"
    )
    ordinary_test.parent.mkdir(parents=True)
    child_runtime_test.parent.mkdir(parents=True)
    source = (
        "from officina.git.provenance import run_git\n"
        "run_git(repo, 'status')\n"
    )
    ordinary_test.write_text(source, encoding="utf-8")
    child_runtime_test.write_text(source, encoding="utf-8")

    errors = validate(tmp_path)

    assert any(
        "skills/demo/tests/test_git.py" in error
        and "direct run_git call requires" in error
        for error in errors
    ), "ordinary test direct run_git call must retain its diagnostic"
    assert any(
        "skills/demo/_rtx/tests/test_git.py" in error
        and "direct run_git call requires" in error
        for error in errors
    ), "child-runtime test direct run_git call must retain its diagnostic"


def test_registered_child_blueprint_authority_and_runtime_are_validated(
    tmp_path: Path,
) -> None:
    child_root = tmp_path / "skills" / "demo" / "_rtx"
    graph = SimpleNamespace(
        nodes={
            "demo-rtx": SimpleNamespace(
                node_type="module",
                blueprint_path=child_root / "blueprint.yaml",
                module_root=child_root,
                declaration={
                    "authority": {
                        "suggested_permissions": {
                            "bash": [{"command": ["grep"]}],
                        }
                    }
                },
            ),
            "demo-rtx.source.runtime": SimpleNamespace(
                node_type="behavioral_source",
                blueprint_path=child_root / "blueprints" / "runtime.yaml",
                module_root=child_root,
                declaration={
                    "runtime_dependencies": [
                        {
                            "kind": "binary",
                            "name": "grep",
                            "platforms": {
                                "linux": True,
                                "macos": True,
                                "windows": True,
                            },
                        }
                    ]
                },
            ),
        }
    )

    errors = validate.__globals__["_validate_blueprints"](graph, tmp_path)

    assert len([error for error in errors if "command `grep`" in error]) == 2


def test_registered_child_artifacts_are_not_runtime_but_executables_still_are(
    tmp_path: Path,
) -> None:
    child_root = tmp_path / "skills" / "demo" / "_rtx"
    (child_root / "assets").mkdir(parents=True)
    (child_root / "assets" / "fixture.sh").write_text(
        "#!/bin/sh\n", encoding="utf-8"
    )
    (child_root / "schemas").mkdir()
    (child_root / "schemas" / "generator.py").write_text(
        "import subprocess\nsubprocess.run(['grep'])\n",
        encoding="utf-8",
    )
    (child_root / "_bad_runtime.py").write_text(
        "import subprocess\nsubprocess.run(['grep'])\n",
        encoding="utf-8",
    )
    graph = SimpleNamespace(
        nodes={
            "demo-rtx": SimpleNamespace(
                node_type="module",
                blueprint_path=child_root / "blueprint.yaml",
                module_root=child_root,
                declaration={},
            )
        },
        module_parents={"demo-rtx": "demo"},
    )

    errors = validate.__globals__["validate_with_graph"](tmp_path, graph)

    assert not any("fixture.sh" in error for error in errors)
    assert not any("generator.py" in error for error in errors)
    assert any("_bad_runtime.py" in error for error in errors)


def test_registered_child_classification_uses_indexed_ancestors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "skills" / "demo" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.py").write_text(
        "import subprocess\nsubprocess.run(['grep'])\n",
        encoding="utf-8",
    )
    nodes = {}
    module_parents = {}
    for index in range(4):
        child_root = runtime / "children" / f"child-{index}"
        assets = child_root / "assets"
        assets.mkdir(parents=True)
        (assets / "fixture.py").write_text(
            "import subprocess\nsubprocess.run(['grep'])\n",
            encoding="utf-8",
        )
        node_id = f"demo.child-{index}"
        nodes[node_id] = SimpleNamespace(
            node_type="module",
            blueprint_path=child_root / "blueprint.yaml",
            module_root=child_root,
            declaration={},
        )
        module_parents[node_id] = "demo"
    graph = SimpleNamespace(
        nodes=nodes,
        module_parents=module_parents,
        direct_file_owners={},
    )
    original_is_relative_to = Path.is_relative_to
    child_root_checks: list[tuple[Path, Path]] = []

    def counting_is_relative_to(path: Path, other: Path) -> bool:
        child_root_checks.append((path, other))
        return original_is_relative_to(path, other)

    monkeypatch.setattr(Path, "is_relative_to", counting_is_relative_to)

    errors = module_under_test.validate_with_graph(tmp_path, graph)

    assert len([error for error in errors if "command `grep`" in error]) == 1
    assert not child_root_checks


def test_root_blueprint_runner_permissions_use_graph_and_only_pool_is_parsed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill_root = tmp_path / "skills" / "demo"
    skill_root.mkdir(parents=True)
    command = [
        "python3",
        "-m",
        "officina.runtime.python_machine_interface_runner",
        "_rtx/_worker.py:Interface",
    ]
    blueprint_path = skill_root / "blueprint.yaml"
    _write_yaml(
        blueprint_path,
        {
            "authority": {
                "suggested_permissions": {
                    "bash": [{"command": command}],
                }
            }
        },
    )
    projection_path = skill_root / ".pooled-blueprint-review.yaml"
    _write_yaml(
        projection_path,
        {"suggested_permissions": {"bash": [{"command": command}]}},
    )
    graph = SimpleNamespace(
        nodes={
            "demo": SimpleNamespace(
                node_type="module",
                blueprint_path=blueprint_path,
                module_root=skill_root,
                declaration={
                    "authority": {
                        "suggested_permissions": {
                            "bash": [{"command": command}],
                        }
                    }
                },
            )
        },
        module_parents={},
        direct_file_owners={},
    )
    original_safe_load = module_under_test.yaml.safe_load
    yaml_loads: list[str] = []

    def counting_safe_load(source: str):
        yaml_loads.append(source)
        return original_safe_load(source)

    monkeypatch.setattr(module_under_test.yaml, "safe_load", counting_safe_load)

    errors = module_under_test.validate_with_graph(tmp_path, graph)

    root_findings = [
        error
        for error in errors
        if "skills/demo/blueprint.yaml" in error
        and "composite runner permission target" in error
    ]
    pool_findings = [
        error
        for error in errors
        if "skills/demo/.pooled-blueprint-review.yaml" in error
        and "composite runner permission target" in error
    ]
    assert len(root_findings) == 1
    assert len(pool_findings) == 1
    assert len(yaml_loads) == 1


def test_live_python_composite_target_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "demo" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "worker.py").write_text(
        "TARGET = '_rtx/_worker.py:Interface'\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("composite Python process target" in error for error in errors)


def test_composite_parser_is_rejected_under_common_module(tmp_path: Path) -> None:
    parser = (
        tmp_path
        / "src"
        / "officina"
        / "common"
        / "parser.py"
    )
    parser.parent.mkdir(parents=True)
    parser.write_text(
        "def parse_gateway():\n"
        "    return '_rtx/_worker.py:Interface'\n",
        encoding="utf-8",
    )

    assert any("composite Python process target" in error for error in validate(tmp_path))
