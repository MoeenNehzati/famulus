from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
RTX_ROOT = REPO_ROOT / "skills" / "daily-plan" / "_rtx"

BLUEPRINT_FILES = [
    RTX_ROOT / "blueprints" / "rtx-plan-orchestrate.yaml",
    RTX_ROOT / "blueprints" / "rtx-plan-storage.yaml",
    RTX_ROOT / "blueprints" / "rtx-state-patch.yaml",
]


@pytest.mark.parametrize("blueprint_path", BLUEPRINT_FILES, ids=lambda p: p.name)
def test_runtime_dependencies_are_available_on_macos_and_windows(blueprint_path: Path) -> None:
    payload = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    dependencies = payload.get("runtime_dependencies", [])
    assert dependencies, f"{blueprint_path.name} declares no runtime_dependencies to check"
    for dependency in dependencies:
        platforms = dependency["platforms"]
        assert platforms.get("macos") is True, (
            f"{blueprint_path.name}: {dependency['name']} still gated off macOS"
        )
        assert platforms.get("windows") is True, (
            f"{blueprint_path.name}: {dependency['name']} still gated off Windows"
        )


@pytest.mark.parametrize("blueprint_path", BLUEPRINT_FILES, ids=lambda p: p.name)
def test_source_platform_support_is_available_on_macos_and_windows(blueprint_path: Path) -> None:
    payload = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    platform_support = payload["platform_support"]
    assert platform_support.get("macos") is True, (
        f"{blueprint_path.name}: platform_support still gates off macOS"
    )
    assert platform_support.get("windows") is True, (
        f"{blueprint_path.name}: platform_support still gates off Windows"
    )
