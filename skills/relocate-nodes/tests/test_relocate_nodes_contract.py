from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
RTX_ROOT = SKILL_ROOT / "_rtx"
REPO_ROOT = SKILL_ROOT.parents[1]
ADAPTER_PATH = RTX_ROOT / "_relocate_nodes.py"
RUNTIME_PACKAGE = "relocate_nodes_contract_runtime"


def _load_adapter():
    return _load_runtime_module(f"{RUNTIME_PACKAGE}._relocate_nodes", ADAPTER_PATH)


def _load_runtime_module(name: str, path: Path):
    package = ModuleType(RUNTIME_PACKAGE)
    package.__path__ = [str(RTX_ROOT)]
    sys.modules.setdefault(RUNTIME_PACKAGE, package)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load relocate-nodes runtime from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
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


def test_registered_route_is_the_only_live_relocation_entrypoint() -> None:
    documentation = (REPO_ROOT / "docs/officina/source-relocation.md").read_text(
        encoding="utf-8"
    )

    assert not (REPO_ROOT / "scripts/relocate_officina_sources.py").exists()
    assert "dispatcher --caller-skill relocate-nodes" in documentation
    assert "relocate-nodes._rtx.interface.relocate" in documentation
    assert "relocate_officina_sources.py" not in documentation


@pytest.mark.parametrize(
    "legacy",
    (
        REPO_ROOT / "src/officina/refactor",
        REPO_ROOT / "scripts/relocate_officina_sources.py",
        REPO_ROOT / "refactors/officina-source-relocation.yaml",
    ),
)
def test_legacy_relocation_surfaces_are_absent(legacy: Path) -> None:
    assert not legacy.exists()


def test_runtime_engine_validates_manifest_with_its_adjacent_schema(
    tmp_path: Path,
) -> None:
    engine = _load_runtime_module(
        f"{RUNTIME_PACKAGE}._relocation_engine",
        RTX_ROOT / "_relocation_engine.py",
    )
    manifest = tmp_path / "relocation.yaml"
    manifest.write_text("schema_version: 2\n", encoding="utf-8")

    loaded = engine.load_manifest(manifest)

    assert loaded.moves == ()


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
