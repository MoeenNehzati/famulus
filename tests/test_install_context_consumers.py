from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEV_ACTIVATION = "dev" + "-activation"


def test_unsupported_development_activation_surface_is_absent() -> None:
    removed = (
        ROOT / ".envrc",
        ROOT / "tools" / "dev-code",
        ROOT / "tools" / "dev-code.cmd",
        ROOT / "skills" / DEV_ACTIVATION,
        ROOT / "tests" / "test_officina_development_activation.py",
    )

    assert all(not path.exists() for path in removed)


def test_active_registries_do_not_reference_development_activation() -> None:
    active_registries = (
        ROOT / "validators" / "platform_neutral.py",
        ROOT / "tests" / "validate_platform_neutral.py",
        ROOT / "references" / "blueprint-schema" / "runtime_dependencies.json",
        ROOT / "docs" / "skills.md",
        ROOT / "docs" / "contributors" / "README.md",
    )

    for path in active_registries:
        assert DEV_ACTIVATION not in path.read_text(encoding="utf-8"), path
