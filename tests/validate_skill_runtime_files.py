"""Tests for validators/skill_runtime_files.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

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


def test_private_shell_runtime_name_passes(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_mail_transport.sh")

    assert _mod.validate(tmp_path) == []


def test_init_file_is_exempt(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "__init__.py")

    assert _mod.validate(tmp_path) == []


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


def test_cx_command_file_must_be_executable(tmp_path: Path) -> None:
    command = tmp_path / "skills" / "demo-skill" / "_cx" / "run-task"
    _write(command)
    command.chmod(0o644)

    errors = _mod.validate(tmp_path)

    assert any("_cx command file must be executable" in error for error in errors)


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
