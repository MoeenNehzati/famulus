"""Tests for the version 4 dependency validator."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR = _REPO_ROOT / "skills" / "skill-maker" / "validators" / "dependencies.py"
_spec = importlib.util.spec_from_file_location("dependencies", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _copy_schemas(repo_root: Path) -> None:
    shutil.copytree(
        _REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )


def _module(
    repo_root: Path,
    module_id: str,
    *,
    body: str = "Body.\n",
    uses: list[str] | None = None,
    source_dependencies: list[str] | None = None,
) -> Path:
    root = repo_root / "skills" / module_id
    root.mkdir(parents=True)
    source_id = f"{module_id}.source.gateway"
    source_interface = f"{source_id}.interface.default"
    (root / "SKILL.md").write_text(body, encoding="utf-8")
    _write_yaml(
        root / "blueprint.yaml",
        {
            "schema_version": 4,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
            "authority": {"owns_filesystem": []},
            "sources": {
                source_id: {
                    "blueprint": {
                        "base": "module-root",
                        "path": "blueprints/gateway.yaml",
                    }
                }
            },
            "exports": {
                f"{module_id}.interface.default": {
                    "source_interface": source_interface,
                    "access": {
                        "allow_all_modules": True,
                        "allowed_callers": [],
                    },
                }
            },
        },
    )
    _write_yaml(
        root / "blueprints" / "gateway.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": 1,
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
            "dependencies": [
                {
                    "source": f"{dependency}.source.gateway",
                    "version": 1,
                    "blueprint": {
                        "base": "repository-root",
                        "path": f"skills/{dependency}/blueprints/gateway.yaml",
                    },
                    "reason": "The instructions refer to the dependency.",
                }
                for dependency in (source_dependencies or [])
            ],
            "uses_interfaces": [
                {"interface": interface_id, "version": 1}
                for interface_id in (uses or [])
            ],
            "interfaces": {
                source_interface: {
                    "version": 1,
                    "description": "Default behavior.",
                }
            },
        },
    )
    return root


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_v4_declared_module_dependency_passes(tmp_path: Path) -> None:
    _copy_schemas(tmp_path)
    _module(tmp_path, "other-skill")
    _module(
        tmp_path,
        "my-skill",
        body="Use other-skill through other-skill.interface.default.\n",
        uses=["other-skill.interface.default"],
    )
    assert _mod.validate(tmp_path) == []


def test_v4_source_dependency_allows_a_bare_module_reference(
    tmp_path: Path,
) -> None:
    _copy_schemas(tmp_path)
    _module(tmp_path, "other-skill")
    _module(
        tmp_path,
        "my-skill",
        body="Certification state belongs to other-skill.\n",
        source_dependencies=["other-skill"],
    )

    assert _mod.validate(tmp_path) == []


def test_undeclared_interface_mention_is_rejected(tmp_path: Path) -> None:
    _copy_schemas(tmp_path)
    _module(tmp_path, "other-skill")
    _module(
        tmp_path,
        "my-skill",
        body="Use other-skill.interface.default.\n",
    )
    errors = _mod.validate(tmp_path)
    assert any("canonical interface" in error and "is not declared" in error for error in errors)


def test_undeclared_bare_module_mention_is_rejected(tmp_path: Path) -> None:
    _copy_schemas(tmp_path)
    _module(tmp_path, "other-skill")
    _module(tmp_path, "my-skill", body="Use other-skill for this.\n")
    errors = _mod.validate(tmp_path)
    assert any("exact module-name mentions" in error for error in errors)


def test_opaque_runtime_path_is_rejected(tmp_path: Path) -> None:
    _copy_schemas(tmp_path)
    _module(tmp_path, "my-skill", body="The implementation is _rtx/_worker.py.\n")
    errors = _mod.validate(tmp_path)
    assert any("opaque runtime path" in error for error in errors)


def test_deprecated_dependency_marker_is_rejected(tmp_path: Path) -> None:
    _copy_schemas(tmp_path)
    _module(tmp_path, "my-skill", body="Depends on: other-skill\n")
    errors = _mod.validate(tmp_path)
    assert any("Depends on:" in error for error in errors)


def test_disallowed_parent_path_is_rejected(tmp_path: Path) -> None:
    _copy_schemas(tmp_path)
    _module(tmp_path, "my-skill", body="Read ../../private/file.md.\n")
    errors = _mod.validate(tmp_path)
    assert any("parent paths in SKILL.md" in error for error in errors)
