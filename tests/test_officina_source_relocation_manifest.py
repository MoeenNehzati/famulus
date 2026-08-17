"""Acceptance contract for the repository's Officina source-relocation manifest."""

from __future__ import annotations

from pathlib import Path
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
RELOCATION_SKILL_ROOT = REPO_ROOT / "skills/relocate-nodes"
if str(RELOCATION_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(RELOCATION_SKILL_ROOT))

from _rtx._relocation_engine import load_manifest, plan_relocation  # noqa: E402
MANIFEST_PATH = REPO_ROOT / "refactors/officina-source-relocation.yaml"
TASK_2_REPORT = ".superpowers/sdd/2026-08-17-relocate-nodes-skill/task-2-report.md"


def test_manifest_covers_every_remaining_domain_move_and_blueprint_transfer() -> None:
    """The acceptance manifest is complete enough to replace the one-off script."""

    document = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert document["schema_version"] == 2
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
    catalog_paths = {catalog.path for catalog in manifest.package_catalogs}
    assert {
        "src/officina/common",
        "src/officina/standards",
        "src/officina/visualization",
        "src/officina/repository",
        "src/officina/repository/checks",
        "src/officina/validators",
    }.issubset(catalog_paths)
    boundaries = {boundary.path: boundary for boundary in manifest.package_boundaries}
    assert {(boundary.path, boundary.disposition) for boundary in boundaries.values()} == {
        ("src/officina/standards", "registered-module"),
        ("src/officina/visualization", "unregistered-package"),
        ("src/officina/repository", "unregistered-package"),
        ("src/officina/repository/checks", "unregistered-package"),
        ("src/officina/validators", "unregistered-package"),
    }
    standards = boundaries["src/officina/standards"]
    assert standards.module_id == "standards"
    assert standards.blueprint == "src/officina/standards/blueprint.yaml"
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


def test_manifest_declares_the_complete_extractor_transfer_fixture_subset() -> None:
    """The end-to-end fixture draws every extractor address from manifest v2."""

    manifest = load_manifest(MANIFEST_PATH)
    moves = {(move.source, move.target) for move in manifest.moves}
    assert {
        (
            "src/officina/common/standard_extractor.py",
            "src/officina/standards/extractor.py",
        ),
        (
            "src/officina/common/blueprints/standard-extractor.yaml",
            "src/officina/standards/blueprints/extractor.yaml",
        ),
    }.issubset(moves)
    assert any(
        transfer.source.old == "common.source.standard-extractor"
        and transfer.source.new == "standards.source.extractor"
        and transfer.from_blueprint == "src/officina/common/blueprint.yaml"
        and transfer.to_blueprint == "src/officina/standards/blueprint.yaml"
        for transfer in manifest.ownership_transfers
    )
    assert any(
        catalog.path == "src/officina/standards" for catalog in manifest.package_catalogs
    )
    assert any(
        boundary.path == "src/officina/standards"
        and boundary.disposition == "registered-module"
        for boundary in manifest.package_boundaries
    )
    assert "skills/relocate-nodes/_rtx/tests/test_relocation_closure.py" in manifest.text_exclusions
    assert "skills/relocate-nodes/_rtx/tests/test_relocation_closure.py" in manifest.active_address_exclusions


def test_task_2_report_is_excluded_from_rewrites_and_active_addresses() -> None:
    """The current run report remains immutable historical evidence."""

    manifest = load_manifest(MANIFEST_PATH)

    assert TASK_2_REPORT in manifest.text_exclusions
    assert TASK_2_REPORT in manifest.active_address_exclusions


def test_exact_worktree_postflight_has_no_projected_changes() -> None:
    """The completed bootstrap manifest is idempotent on the exact worktree."""

    def synchronize(repository: Path, *, check: bool) -> None:
        """Leave already-synchronized generated artifacts unchanged."""

    report = plan_relocation(
        REPO_ROOT,
        load_manifest(MANIFEST_PATH),
        synchronize=synchronize,
    ).report()

    for category in (
        "moves",
        "writes",
        "deletes",
        "blueprint_changes",
        "certification_basis_changes",
        "digest_changes",
        "generated_artifact_changes",
        "unresolved_references",
    ):
        assert report[category] == [], category
