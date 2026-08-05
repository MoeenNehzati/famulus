"""Tests for the canonical cross-platform validator."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml
from test_support.git_repository import GitTestRepository


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from validators.cross_platform import validate  # noqa: E402


def _copy_module(repo_root: Path, source_name: str = "get-weather") -> Path:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
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


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_clean_python_module_passes(tmp_path: Path) -> None:
    _copy_module(tmp_path)

    assert validate(tmp_path) == []


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


def test_module_permission_shell_command_is_rejected(tmp_path: Path) -> None:
    skill = _copy_module(tmp_path, "loose-mode")
    path = skill / "blueprint.yaml"
    module = yaml.safe_load(path.read_text(encoding="utf-8"))
    module["authority"].setdefault("suggested_permissions", {})["bash"] = [
        {
            "command": ["grep"],
            "reason": "Invalid portable permission.",
        }
    ]
    _write_yaml(path, module)

    errors = validate(tmp_path)

    assert any("command `grep` is not cross-platform" in error for error in errors)


def test_module_permission_shell_script_argument_is_rejected(
    tmp_path: Path,
) -> None:
    skill = _copy_module(tmp_path, "loose-mode")
    path = skill / "blueprint.yaml"
    module = yaml.safe_load(path.read_text(encoding="utf-8"))
    module["authority"]["suggested_permissions"]["bash"] = [
        {
            "command": ["python3"],
            "args_prefix": ["_rtx/run.sh"],
            "reason": "Invalid shell entrypoint.",
        }
    ]
    _write_yaml(path, module)

    errors = validate(tmp_path)

    assert any("shell script token `_rtx/run.sh`" in error for error in errors)


def test_all_platform_source_binary_is_rejected(tmp_path: Path) -> None:
    skill = _copy_module(tmp_path)
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

    errors = validate(tmp_path)

    assert any("command `grep` is not cross-platform" in error for error in errors)


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


def test_direct_run_git_in_ordinary_test_requires_annotation(tmp_path: Path) -> None:
    test = tmp_path / "skills" / "demo" / "tests" / "test_git.py"
    test.parent.mkdir(parents=True)
    test.write_text(
        "from officina.common.git_provenance import run_git\n"
        "run_git(repo, 'status')\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("direct run_git call requires" in error for error in errors)


def test_direct_run_git_in_child_runtime_test_requires_annotation(
    tmp_path: Path,
) -> None:
    test = (
        tmp_path
        / "skills"
        / "demo"
        / "_rtx"
        / "tests"
        / "test_git.py"
    )
    test.parent.mkdir(parents=True)
    test.write_text(
        "from officina.common.git_provenance import run_git\n"
        "run_git(repo, 'status')\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("direct run_git call requires" in error for error in errors)


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

    errors = validate.__globals__["_validate_v4_blueprints"](graph, tmp_path)

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


def test_live_python_composite_target_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "skills" / "demo" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "worker.py").write_text(
        "TARGET = '_rtx/_worker.py:Interface'\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("composite Python process target" in error for error in errors)


def test_migration_only_composite_parser_is_allowed(tmp_path: Path) -> None:
    migration = (
        tmp_path
        / "src"
        / "officina"
        / "common"
        / "interface_injection_migration.py"
    )
    migration.parent.mkdir(parents=True)
    migration.write_text(
        "def _legacy_gateway():\n"
        "    return '_rtx/_worker.py:Interface'\n",
        encoding="utf-8",
    )

    assert validate(tmp_path) == []


def test_composite_runner_permission_is_rejected_in_live_blueprint(
    tmp_path: Path,
) -> None:
    skill = _copy_module(tmp_path, "loose-mode")
    path = skill / "blueprint.yaml"
    module = yaml.safe_load(path.read_text(encoding="utf-8"))
    module["authority"]["suggested_permissions"]["bash"] = [
        {
            "command": [
                "python3",
                "-m",
                "officina.runtime.python_machine_interface_runner",
                "_rtx/_worker.py:Interface",
            ],
            "reason": "Legacy composite target.",
        }
    ]
    _write_yaml(path, module)

    errors = validate(tmp_path)

    assert any("composite runner permission target" in error for error in errors)


def test_composite_runner_permission_is_rejected_in_registered_child(
    tmp_path: Path,
) -> None:
    skill = _copy_module(tmp_path, "get-weather")
    path = skill / "_rtx" / "blueprint.yaml"
    module = yaml.safe_load(path.read_text(encoding="utf-8"))
    module["authority"].setdefault("suggested_permissions", {})["bash"] = [
        {
            "command": [
                "python3",
                "-m",
                "officina.runtime.python_machine_interface_runner",
                "_rtx/_worker.py:Interface",
            ],
            "reason": "Legacy composite target.",
        }
    ]
    _write_yaml(path, module)

    errors = validate(tmp_path)

    assert any("composite runner permission target" in error for error in errors)


def test_composite_runner_permission_is_rejected_in_generated_projection(
    tmp_path: Path,
) -> None:
    projection = (
        tmp_path / "skills" / "demo" / ".pooled-blueprint-review.yaml"
    )
    projection.parent.mkdir(parents=True)
    _write_yaml(
        projection,
        {
            "suggested_permissions": {
                "bash": [
                    {
                        "command": [
                            "python3",
                            "-m",
                            "officina.runtime.python_machine_interface_runner",
                            "_rtx/_worker.py:Interface",
                        ]
                    }
                ]
            }
        },
    )

    errors = validate(tmp_path)

    assert any("composite runner permission target" in error for error in errors)


def test_runner_reports_cross_platform_errors(tmp_path: Path) -> None:
    repository = GitTestRepository.initialize_existing_empty(tmp_path)
    (tmp_path / "validators").mkdir()
    shutil.copy2(REPO_ROOT / "repo_checks.py", tmp_path / "repo_checks.py")
    shutil.copy2(
        REPO_ROOT / "validators" / "cross_platform.py",
        tmp_path / "validators",
    )
    shutil.copy2(
        REPO_ROOT / "validators" / "skill_runtime_files.py",
        tmp_path / "validators",
    )
    skill_validators = tmp_path / "validators" / "skill"
    skill_validators.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "validators" / "skill" / "blueprints.py",
        skill_validators,
    )
    shutil.copytree(REPO_ROOT / "src", tmp_path / "src")
    _copy_module(tmp_path)
    shutil.copy2(
        REPO_ROOT / "references" / "blueprint" / "blueprint.yaml",
        tmp_path / "references" / "blueprint" / "blueprint.yaml",
    )
    runtime = tmp_path / "skills" / "bad-skill" / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    repository.git("add", ".")
    runner = tmp_path / "repo_checks.py"

    result = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--suite",
            "validators",
            "--repo-root",
            str(tmp_path),
            "--validator",
            "repo/cross_platform",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
