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


def test_runtime_declares_authorized_graph_and_synchronizer_dependencies() -> None:
    source = yaml.safe_load(
        (RTX_ROOT / "blueprints/rtx-relocate-nodes.yaml").read_text(encoding="utf-8")
    )

    assert source["uses_interfaces"] == [
        {"interface": "blueprints.interface.graph", "version": 1},
        {"interface": "skill-maker._rtx.interface.sync-blueprints", "version": 1},
    ]


def test_adapter_declares_exact_synchronizer_dispatch() -> None:
    adapter = _load_adapter()
    call = adapter.Interface.dispatches["sync-blueprints"]

    assert call.caller_module_id == "relocate-nodes._rtx"
    assert call.target_module_id == "skill-maker._rtx"
    assert call.interface == "sync-blueprints"
    assert call.version == 1


def test_adapter_rejects_report_path_inside_selected_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = _load_adapter()
    root = tmp_path / "repository"
    root.mkdir()
    manifest = tmp_path / "relocation.yaml"
    manifest.write_text("schema_version: 2\n", encoding="utf-8")
    report = root / "report.json"
    args = adapter.Interface().build_parser().parse_args(
        ["--root", str(root), "--manifest", str(manifest), "--report", str(report)]
    )

    assert adapter.Interface().run(args) == 2
    assert not report.exists()
    assert "report path must be outside selected repository" in capsys.readouterr().err
