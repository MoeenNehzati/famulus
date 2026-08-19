"""Acceptance contract for the repository's Officina source-relocation manifest."""

from __future__ import annotations

import ast
from pathlib import Path

from officina.refactor.relocation import load_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "refactors/officina-source-relocation.yaml"


def test_rutter_storage_uses_relocation_safe_atomic_files_import() -> None:
    """The storage owner must name the common submodule at its concrete address."""

    storage_path = REPO_ROOT / "src/officina/rutter/storage.py"
    tree = ast.parse(storage_path.read_text(encoding="utf-8"))
    concrete = [
        node
        for node in tree.body
        if isinstance(node, ast.Import)
        and any(
            alias.name == "officina.common.atomic_files"
            and alias.asname == "atomic_files"
            for alias in node.names
        )
    ]
    package_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "officina.common"
        and any(alias.name == "atomic_files" for alias in node.names)
    ]

    assert len(concrete) == 1
    assert package_imports == []


def test_active_tree_contains_no_retired_rutter_machine_addresses() -> None:
    """Retired machine addresses may survive only in dated plan/spec history."""

    retired = (
        "officina." + "compass",
        "Compass" + "Turn",
        "Compass" + "Run",
        "get_" + "instructions",
        "Refresh" + "Input",
        "InstructionResult" + "Input",
        "Update" + "Result",
    )
    active_roots = (
        "src",
        "tests",
        "test_support",
        "skills",
        "refactors",
        "references",
        "docs",
    )
    text_suffixes = {".json", ".md", ".py", ".toml", ".yaml", ".yml"}
    violations: list[str] = []

    for root_name in active_roots:
        for path in (REPO_ROOT / root_name).rglob("*"):
            relative = path.relative_to(REPO_ROOT)
            if not path.is_file() or path.suffix not in text_suffixes:
                continue
            if "famulus_officina.egg-info" in relative.parts:
                continue
            if relative.parts[:3] in {
                ("docs", "superpowers", "plans"),
                ("docs", "superpowers", "specs"),
            }:
                continue
            text = path.read_text(encoding="utf-8")
            violations.extend(
                f"{relative}:{token}" for token in retired if token in text
            )

    assert violations == []


def test_manifest_contains_no_retired_controller_address() -> None:
    """No executable manifest field may preserve a retired controller address."""

    text = MANIFEST_PATH.read_text(encoding="utf-8")
    for retired in ("officina.controller", "src/officina/controller"):
        assert retired not in text


def test_manifest_contains_no_retired_compass_machine_address() -> None:
    """No active manifest field may preserve the retired Compass machine address."""

    text = MANIFEST_PATH.read_text(encoding="utf-8")
    for retired in (
        "officina." + "compass",
        "compass.interface",
        "compass.source",
        "src/officina/compass",
    ):
        assert retired not in text


def test_manifest_covers_every_remaining_domain_move_and_blueprint_transfer() -> None:
    """The acceptance manifest is complete enough to replace the one-off script."""

    manifest = load_manifest(MANIFEST_PATH)
    moves = {(move.source, move.target) for move in manifest.moves}
    assert (
        "src/officina/common/visualization",
        "src/officina/visualization",
    ) in moves
    assert (
        "src/officina/common/standard_extractor.py",
        "src/officina/standards/extractor.py",
    ) in moves
    assert (
        "src/officina/repository_checks.py",
        "src/officina/repository/checks/runner.py",
    ) in moves
    assert (
        "src/officina/_validator_snapshot.py",
        "src/officina/validators/snapshot.py",
    ) in moves
    assert {transfer.source.new for transfer in manifest.ownership_transfers} == {
        "standards.source.extractor",
        "standards.source.query",
    }
    catalogs = {catalog.path: catalog for catalog in manifest.package_catalogs}
    catalog_paths = set(catalogs)
    assert {
        "src/officina/common",
        "src/officina/standards",
        "src/officina/visualization",
        "src/officina/repository",
        "src/officina/repository/checks",
        "src/officina/validators",
        "src/officina/rutter",
    }.issubset(catalog_paths)
    assert "src/officina/controller" not in catalog_paths
    assert catalogs["src/officina/rutter"].roles == {
        "model.py": (
            "Defines immutable Charter, Fix, Reckoning, state, effect, and validation "
            "values for direct Rutters."
        ),
        "engine.py": (
            "Binds one direct Rutter definition to durable validation, reduction, "
            "continuation, and effect-recovery semantics."
        ),
        "storage.py": (
            "Encodes strict Reckonings and provides confined atomic persistence and "
            "per-Reckoning locking."
        ),
        "runtime.py": (
            "Resolves explicit Rutter registrations and creates or opens bound "
            "voyages beneath one Reckoning root."
        ),
    }
    assert "officina.rutter" in manifest.forbid_facade_imports
    assert "officina.controller" not in manifest.forbid_facade_imports
    rewrites = {(rewrite.path, rewrite.old) for rewrite in manifest.exact_rewrites}
    assert (
        "src/officina/repository/checks/runner.py",
        "from officina.repo_checks import remote",
    ) in rewrites
    assert any(
        path == "tests/test_repository_validator_checks.py"
        and 'shutil.copy2(_RUNNER_PATH, officina / "_validator_snapshot.py")' in old
        for path, old in rewrites
    )
    assert {
        "skills/refactor-node/SKILL.md",
        "skills/skill-maker/SKILL.md",
    }.issubset(
        {
            path
            for path, old in rewrites
            if "Uses Interfaces:" in old
            and "standards.interface.query-standard@1" in old
        }
    )


def test_generated_package_readmes_explain_file_relevance() -> None:
    """Affected package initializers must not contain placeholder descriptions."""

    manifest = load_manifest(MANIFEST_PATH)
    placeholders = (
        "Implements the package's",
        "Provides a tracked runtime resource owned by this package.",
        "Provides declarative runtime configuration owned by this package.",
        "Defines a structured runtime schema or manifest owned by this package.",
    )

    for catalog in manifest.package_catalogs:
        initializer = REPO_ROOT / catalog.path / "__init__.py"
        text = initializer.read_text(encoding="utf-8")
        assert not any(placeholder in text for placeholder in placeholders), initializer
