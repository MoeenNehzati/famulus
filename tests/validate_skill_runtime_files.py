"""Tests for validators/skill_runtime_files.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "skill_runtime_files.py"
_spec = importlib.util.spec_from_file_location("skill_runtime_files", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# runtime\n", encoding="utf-8")


def test_private_python_runtime_name_passes(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_Calendar_Gateway.py")

    assert _mod.validate(tmp_path) == []


def test_private_shell_runtime_name_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_mail_transport.sh")

    errors = _mod.validate(tmp_path)

    assert any("unsupported runtime suffix `.sh`" in error for error in errors)


def test_init_file_is_exempt(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "__init__.py")

    assert _mod.validate(tmp_path) == []


def test_registered_runtime_child_artifacts_are_not_executable_files(
    tmp_path: Path,
) -> None:
    child_root = tmp_path / "skills" / "demo-skill" / "_rtx"
    for relative in (
        "blueprint.yaml",
        "blueprints/runtime.yaml",
        "schemas/input.schema.json",
        "assets/data.json",
        "state/cache.json",
        "tests/test_runtime.py",
        ".certificates/runtime.jsonl",
        ".pooled-blueprint-review.yaml",
        ".pooled-blueprint-review.health.json",
        "_run_task.py",
    ):
        _write(child_root / relative)
    graph = SimpleNamespace(
        module_parents={"demo-skill-rtx": "demo-skill"},
        nodes={
            "demo-skill-rtx": SimpleNamespace(
                node_type="module",
                module_root=child_root,
            )
        },
    )

    assert _mod.validate_with_graph(tmp_path, graph) == []


def test_source_owned_top_level_bin_payloads_still_require_artifact_layout(
    tmp_path: Path,
) -> None:
    child_root = tmp_path / "skills" / "demo-skill" / "_rtx"
    gateway = child_root / "_install_launchers.py"
    payloads = (
        child_root / "bin" / "assistant",
        child_root / "bin" / "assistant.bat",
        child_root / "bin" / "_agent_launch.py",
    )
    _write(gateway)
    for payload in payloads:
        _write(payload)
    source_id = "demo-skill-rtx.source.launchers"
    graph = SimpleNamespace(
        module_parents={"demo-skill-rtx": "demo-skill"},
        nodes={
            "demo-skill-rtx": SimpleNamespace(
                node_type="module",
                module_root=child_root,
            ),
            source_id: SimpleNamespace(
                node_type="behavioral_source",
                module_root=child_root,
                gateway_path=gateway,
                declaration={"gateway": {"language": "Python"}},
            ),
        },
        direct_file_owners={
            gateway: source_id,
            **{payload: source_id for payload in payloads},
        },
    )

    errors = _mod.validate_with_graph(tmp_path, graph)

    assert any("runtime directory name must match" in error for error in errors)


def test_non_python_source_gateway_is_registered_child_configuration(
    tmp_path: Path,
) -> None:
    child_root = tmp_path / "skills" / "demo-skill" / "_rtx"
    gateway = child_root / "jobs.yaml"
    _write(gateway)
    source_id = "demo-skill-rtx.source.jobs-config"
    graph = SimpleNamespace(
        module_parents={"demo-skill-rtx": "demo-skill"},
        nodes={
            "demo-skill-rtx": SimpleNamespace(
                node_type="module",
                module_root=child_root,
            ),
            source_id: SimpleNamespace(
                node_type="behavioral_source",
                module_root=child_root,
                gateway_path=gateway,
                declaration={"gateway": {"language": "YAML"}},
            ),
        },
        direct_file_owners={gateway: source_id},
    )

    assert _mod.validate_with_graph(tmp_path, graph) == []


def test_unowned_bin_file_is_not_a_registered_child_artifact(
    tmp_path: Path,
) -> None:
    child_root = tmp_path / "skills" / "demo-skill" / "_rtx"
    payload = child_root / "bin" / "assistant"
    _write(payload)
    graph = SimpleNamespace(
        module_parents={"demo-skill-rtx": "demo-skill"},
        nodes={
            "demo-skill-rtx": SimpleNamespace(
                node_type="module",
                module_root=child_root,
            ),
        },
        direct_file_owners={},
    )

    errors = _mod.validate_with_graph(tmp_path, graph)

    assert any("runtime directory name must match" in error for error in errors)
    assert any("runtime filename stem must match" in error for error in errors)


def test_unregistered_runtime_tree_is_not_treated_as_child_artifacts(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path
        / "skills"
        / "demo-skill"
        / "_rtx"
        / "assets"
        / "data.json"
    )
    graph = SimpleNamespace(module_parents={}, nodes={})

    errors = _mod.validate_with_graph(tmp_path, graph)

    assert any("runtime directory name must match" in error for error in errors)
    assert any("unsupported runtime suffix `.json`" in error for error in errors)


def test_registered_artifact_classification_uses_deepest_matching_child(
    tmp_path: Path,
) -> None:
    parent_root = tmp_path / "modules" / "parent"
    child_root = parent_root / "nested"
    artifact = child_root / "assets" / "data.json"
    _write(artifact)
    graph = SimpleNamespace(
        module_parents={
            "parent": None,
            "child": "parent",
        },
        nodes={
            "parent": SimpleNamespace(
                node_type="module",
                module_root=parent_root,
            ),
            "child": SimpleNamespace(
                node_type="module",
                module_root=child_root,
            ),
        },
    )

    assert _mod._registered_child_artifact(artifact, graph)


def test_parent_module_artifact_directories_are_not_child_exempt(
    tmp_path: Path,
) -> None:
    parent_root = tmp_path / "skills" / "demo-skill"
    artifact = parent_root / "assets" / "data.json"
    _write(artifact)
    graph = SimpleNamespace(
        module_parents={"demo-skill": None},
        nodes={
            "demo-skill": SimpleNamespace(
                node_type="module",
                module_root=parent_root,
            ),
        },
    )

    assert not _mod._registered_child_artifact(artifact, graph)


def test_nested_private_runtime_package_passes(tmp_path: Path) -> None:
    _write(
        tmp_path
        / "skills"
        / "demo-skill"
        / "_rtx"
        / "_install_launcher"
        / "_windows_launcher.py"
    )
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_install_launcher" / "__init__.py")

    assert _mod.validate(tmp_path) == []


def test_runtime_file_under_scripts_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "scripts" / "_calendar_gateway.py")

    errors = _mod.validate(tmp_path)

    assert any("must live under `skills/<skill>/_rtx/`" in error for error in errors)


def test_missing_leading_underscore_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "calendar_gateway.py")

    errors = _mod.validate(tmp_path)

    assert any("runtime filename stem must match" in error and "calendar_gateway" in error for error in errors)


def test_nested_directory_missing_leading_underscore_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path
        / "skills"
        / "demo-skill"
        / "_rtx"
        / "install_launcher"
        / "_windows_launcher.py"
    )

    errors = _mod.validate(tmp_path)

    assert any("runtime directory name must match" in error and "install_launcher" in error for error in errors)


def test_one_word_runtime_name_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_gcal.py")

    errors = _mod.validate(tmp_path)

    assert any("runtime filename stem must match" in error for error in errors)


def test_hyphenated_runtime_name_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_get-weather.py")

    errors = _mod.validate(tmp_path)

    assert any("runtime filename stem must match" in error for error in errors)


def test_one_word_nested_directory_name_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_launcher" / "__init__.py")

    errors = _mod.validate(tmp_path)

    assert any("runtime directory name must match" in error and "_launcher" in error for error in errors)


def test_unsupported_runtime_suffix_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_calendar_gateway.txt")

    errors = _mod.validate(tmp_path)

    assert any("unsupported runtime suffix `.txt`" in error for error in errors)


def test_hidden_runtime_blueprint_sidecar_is_allowed(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_worker_file.py")
    _write(
        tmp_path
        / "skills"
        / "demo-skill"
        / "_rtx"
        / "._worker_file.py.run.blueprint.yaml"
    )

    assert _mod.validate(tmp_path) == []


def test_hidden_runtime_health_sidecar_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_worker_file.py")
    _write(
        tmp_path
        / "skills"
        / "demo-skill"
        / "_rtx"
        / "._worker_file.py.run.health.json"
    )

    assert _mod.validate(tmp_path) == []


def test_nonhidden_runtime_health_lookalike_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_worker_file.health.json")

    errors = _mod.validate(tmp_path)

    assert any(
        "unsupported runtime suffix `.json`" in error
        and "_worker_file.health.json" in error
        for error in errors
    )


def test_cx_command_mode_is_owned_by_blueprint_validator(tmp_path: Path) -> None:
    command = tmp_path / "skills" / "demo-skill" / "_cx" / "run-task"
    _write(command)
    command.chmod(0o644)

    assert _mod.validate(tmp_path) == []


def test_case_insensitive_runtime_name_collision_is_rejected(tmp_path: Path, monkeypatch) -> None:
    rel_paths = [
        Path("skills/demo-skill/_rtx/_Calendar_Gateway.py"),
        Path("skills/demo-skill/_rtx/_calendar_gateway.py"),
    ]
    monkeypatch.setattr(_mod, "_iter_skill_files", lambda repo_root: [(tmp_path / rel, rel) for rel in rel_paths])

    errors = _mod.validate(tmp_path)

    assert any("case-insensitive runtime path collision" in error for error in errors)


def test_case_insensitive_nested_directory_collision_is_rejected(tmp_path: Path, monkeypatch) -> None:
    rel_paths = [
        Path("skills/demo-skill/_rtx/_Install_Launcher/_linux_launcher.py"),
        Path("skills/demo-skill/_rtx/_install_launcher/_osx_launcher.py"),
    ]
    monkeypatch.setattr(_mod, "_iter_skill_files", lambda repo_root: [(tmp_path / rel, rel) for rel in rel_paths])

    errors = _mod.validate(tmp_path)

    assert any("case-insensitive runtime path collision" in error for error in errors)


def test_system_skill_cache_is_exempt(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / ".system" / "tool" / "_rtx" / "run-task.py")

    assert _mod.validate(tmp_path) == []
