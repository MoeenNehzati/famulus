"""Tests narrowing the setup-prerequisite exception in the dependency validator.

The exception must apply only to the exact behavioral source that implements
a module's `.interface.setup` export, and only for interfaces that are exact
direct entries of that export's `setup_requires_setup_of`. It must not exempt
other Markdown behavioral sources in the same module.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR = _REPO_ROOT / "validators" / "skill" / "dependencies.py"
_spec = importlib.util.spec_from_file_location("dependencies_setup_exception", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _graph(*, module_id: str, setup_source_id: str, prerequisite_interface: str):
    module_export_key = f"{module_id}.interface.setup"
    module_node = SimpleNamespace(
        declaration={
            "exports": {
                module_export_key: {
                    "setup_requires_setup_of": [
                        {"interface": prerequisite_interface, "version": 1}
                    ],
                }
            }
        }
    )
    export = SimpleNamespace(
        source_interface_id=f"{setup_source_id}.interface.setup",
        module_node_id=module_id,
    )
    return SimpleNamespace(
        nodes={module_id: module_node},
        exports={module_export_key: export},
    )


def _markdown_source(node_id: str, gateway_path: Path):
    return SimpleNamespace(
        node_id=node_id,
        gateway_path=gateway_path,
        declaration={"gateway": {"language": "Markdown"}, "uses_interfaces": []},
    )


def test_setup_owner_source_may_mention_its_exact_prerequisite(tmp_path: Path) -> None:
    module_id = "storage-skill"
    setup_source_id = f"{module_id}.source.setup"
    graph = _graph(
        module_id=module_id,
        setup_source_id=setup_source_id,
        prerequisite_interface="connect-google.interface.setup",
    )
    gateway_path = tmp_path / "setup.md"
    gateway_path.write_text(
        "Requires `connect-google.interface.setup` to be complete first.\n",
        encoding="utf-8",
    )
    source = _markdown_source(setup_source_id, gateway_path)

    errors = _mod._validate_markdown_source(graph, source)
    assert errors == []


def test_other_markdown_source_in_same_module_is_not_exempted(tmp_path: Path) -> None:
    module_id = "storage-skill"
    setup_source_id = f"{module_id}.source.setup"
    other_source_id = f"{module_id}.source.other"
    graph = _graph(
        module_id=module_id,
        setup_source_id=setup_source_id,
        prerequisite_interface="connect-google.interface.setup",
    )
    gateway_path = tmp_path / "other.md"
    gateway_path.write_text(
        "See `connect-google.interface.setup` for background.\n",
        encoding="utf-8",
    )
    source = _markdown_source(other_source_id, gateway_path)

    errors = _mod._validate_markdown_source(graph, source)
    assert any(
        "connect-google.interface.setup" in error and "is not declared" in error
        for error in errors
    )


def test_transitive_prerequisite_is_not_exempted(tmp_path: Path) -> None:
    """Only exact direct `setup_requires_setup_of` entries are permitted."""

    module_id = "storage-skill"
    setup_source_id = f"{module_id}.source.setup"
    graph = _graph(
        module_id=module_id,
        setup_source_id=setup_source_id,
        prerequisite_interface="connect-google.interface.setup",
    )
    gateway_path = tmp_path / "setup.md"
    gateway_path.write_text(
        "Also see `unrelated-module.interface.setup` for background.\n",
        encoding="utf-8",
    )
    source = _markdown_source(setup_source_id, gateway_path)

    errors = _mod._validate_markdown_source(graph, source)
    assert any(
        "unrelated-module.interface.setup" in error and "is not declared" in error
        for error in errors
    )
