"""Tests for the version 4 dependency validator."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR = _REPO_ROOT / "validators" / "skill" / "dependencies.py"
_spec = importlib.util.spec_from_file_location("dependencies", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
_REAL_LOAD_GRAPH = _mod.load_repository_blueprint_graph
_V4_SCHEMA_ROOT = _REPO_ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v4"


@pytest.fixture(autouse=True)
def _select_frozen_v4_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    def load_v4_graph(repo_root: Path, **kwargs: object) -> object:
        kwargs["expected_schema_version"] = 4
        kwargs["schema_root"] = _V4_SCHEMA_ROOT
        return _REAL_LOAD_GRAPH(repo_root, **kwargs)

    monkeypatch.setattr(
        _mod,
        "load_repository_blueprint_graph",
        load_v4_graph,
    )


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _copy_schemas(repo_root: Path) -> None:
    shutil.copytree(
        _REPO_ROOT / "references" / "blueprint-schema",
        repo_root / "references" / "blueprint-schema",
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


def test_undeclared_dotted_child_interface_mention_is_rejected(
    tmp_path: Path,
) -> None:
    _copy_schemas(tmp_path)
    _module(tmp_path, "other-skill")
    _module(
        tmp_path,
        "my-skill",
        body="Use other-skill._rtx.interface.default.\n",
    )
    errors = _mod.validate(tmp_path)
    assert any(
        "canonical interface" in error
        and "other-skill._rtx.interface.default" in error
        for error in errors
    )


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


def test_parent_dependency_accounting_includes_registered_child_sources() -> None:
    child_source = SimpleNamespace(
        declaration={
            "uses_interfaces": [
                {"interface": "other-skill.interface.default"},
            ],
        }
    )
    graph = SimpleNamespace(
        module_sources={
            "my-skill": ("my-skill.source.gateway",),
            "my-skill-rtx": ("my-skill-rtx.source.runtime",),
            "other-skill": ("other-skill.source.gateway",),
        },
        module_ancestry={
            "my-skill": ("my-skill",),
            "my-skill-rtx": ("my-skill", "my-skill-rtx"),
            "other-skill": ("other-skill",),
        },
        nodes={
            "my-skill.source.gateway": SimpleNamespace(
                declaration={"uses_interfaces": []}
            ),
            "my-skill-rtx.source.runtime": child_source,
            "other-skill.source.gateway": SimpleNamespace(
                declaration={"uses_interfaces": []}
            ),
        },
        exports={
            "other-skill.interface.default": SimpleNamespace(
                module_node_id="other-skill",
            ),
        },
        node_edges=(),
    )

    assert _mod._used_module_ids(graph, "my-skill") == {"other-skill"}


def test_dependency_on_dotted_child_export_accounts_for_top_level_module() -> None:
    source = SimpleNamespace(
        declaration={
            "uses_interfaces": [
                {"interface": "other-skill._rtx.interface.default"},
            ],
        }
    )
    graph = SimpleNamespace(
        module_sources={
            "my-skill": ("my-skill.source.gateway",),
            "other-skill._rtx": ("other-skill._rtx.source.runtime",),
        },
        module_ancestry={
            "my-skill": ("my-skill",),
            "other-skill._rtx": ("other-skill", "other-skill._rtx"),
        },
        nodes={"my-skill.source.gateway": source},
        exports={
            "other-skill._rtx.interface.default": SimpleNamespace(
                module_node_id="other-skill._rtx",
                source_interface_id="other-skill._rtx.source.runtime.interface.default",
            ),
        },
        node_edges=(),
    )

    assert _mod._used_module_ids(graph, "my-skill") == {"other-skill"}
