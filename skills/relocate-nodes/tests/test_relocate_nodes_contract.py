from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
RTX_ROOT = SKILL_ROOT / "_rtx"
ADAPTER_PATH = RTX_ROOT / "_relocate_nodes.py"


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "relocate_nodes_contract_adapter",
        ADAPTER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load relocate-nodes adapter from {ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relocate_nodes_has_one_private_runtime_route() -> None:
    parent = yaml.safe_load((SKILL_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))
    child = yaml.safe_load((RTX_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))

    assert parent["children"] == {"_rtx": {}}
    assert parent["namespace_exports"]["_rtx"]["surface"]["only"] == {
        "relocate-nodes._rtx.interface.relocate": 1
    }
    assert set(child["exports"]) == {"relocate-nodes._rtx.interface.relocate"}
    assert child["gateway"] == {"language": "Python>=3.11", "path": "__init__.py"}


def test_adapter_requires_manifest() -> None:
    adapter = _load_adapter()

    with pytest.raises(SystemExit):
        adapter.Interface().build_parser().parse_args([])
