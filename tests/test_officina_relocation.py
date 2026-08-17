"""Behavioral tests for manifest-driven Officina source relocation."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest
import yaml

from officina.refactor.relocation import (
    RelocationError,
    apply_change_set,
    load_manifest,
    plan_relocation,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest(path: Path, value: dict[str, object]):
    _write(path, yaml.safe_dump(value, sort_keys=False))
    return load_manifest(path)


def test_preflight_uses_typed_renames_without_writing(tmp_path: Path) -> None:
    """A plan derives address variants while leaving the repository untouched."""

    source = tmp_path / "src/pkg/old_name.py"
    caller = tmp_path / "tests/test_caller.py"
    _write(source, "VALUE = 'old.source.item old.interface.item'\n")
    _write(caller, "from pkg.old_name import VALUE\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "moves": [{"from": "src/pkg/old_name.py", "to": "src/pkg/new/name.py"}],
            "renames": {
                "python_modules": [{"from": "pkg.old_name", "to": "pkg.new.name"}],
                "source_ids": [{"from": "old.source.item", "to": "new.source.item"}],
                "interface_ids": [{"from": "old.interface.item", "to": "new.interface.item"}],
            },
        },
    )
    before = {path: path.read_bytes() for path in (source, caller)}

    change_set = plan_relocation(tmp_path, manifest)

    assert source.exists()
    assert not (tmp_path / "src/pkg/new/name.py").exists()
    assert {path: path.read_bytes() for path in (source, caller)} == before
    report = change_set.report()
    assert report["moves"] == [
        {"from": "src/pkg/old_name.py", "to": "src/pkg/new/name.py"}
    ]
    assert "tests/test_caller.py" in report["writes"]
    assert json.dumps(report, sort_keys=True) == json.dumps(
        change_set.report(), sort_keys=True
    )


def test_manifest_v2_requires_explicit_package_boundary_dispositions(
    tmp_path: Path,
) -> None:
    """A v2 manifest parses each declared package-policy record by disposition."""

    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "package_boundaries": [
                {
                    "path": "src/officina/tools",
                    "disposition": "registered-module",
                    "module_id": "tools",
                    "blueprint": "src/officina/tools/blueprint.yaml",
                },
                {
                    "path": "src/officina/helpers",
                    "disposition": "unregistered-package",
                },
            ],
        },
    )

    assert manifest.package_boundaries[0].module_id == "tools"
    assert manifest.package_boundaries[1].module_id is None


@pytest.mark.parametrize(
    "value",
    [
        {"schema_version": 1},
        {
            "schema_version": 2,
            "package_boundaries": [
                {"path": "src/officina/tools", "disposition": "registered-module"}
            ],
        },
        {
            "schema_version": 2,
            "package_boundaries": [
                {
                    "path": "src/officina/tools",
                    "disposition": "unregistered-package",
                    "module_id": "tools",
                }
            ],
        },
        {
            "schema_version": 2,
            "package_boundaries": [
                {
                    "path": "src/officina/tools",
                    "disposition": "existing-module",
                    "blueprint": "src/officina/tools/blueprint.yaml",
                }
            ],
        },
    ],
)
def test_manifest_rejects_legacy_or_incoherent_boundary_policy(
    tmp_path: Path,
    value: dict[str, object],
) -> None:
    """Legacy schemas and disposition-incompatible fields are rejected."""

    with pytest.raises(RelocationError):
        _manifest(tmp_path / "move.yaml", value)


def test_manifest_rejects_duplicate_package_boundary_paths(tmp_path: Path) -> None:
    """One package path cannot be assigned two relocation dispositions."""

    with pytest.raises(RelocationError, match="duplicate package boundary path"):
        _manifest(
            tmp_path / "move.yaml",
            {
                "schema_version": 2,
                "package_boundaries": [
                    {
                        "path": "src/officina/tools",
                        "disposition": "unregistered-package",
                    },
                    {
                        "path": "src/officina/tools",
                        "disposition": "unregistered-package",
                    },
                ],
            },
        )


def test_boundary_policy_requires_a_declaration_for_a_new_package(
    tmp_path: Path,
) -> None:
    """A moved initializer cannot create an undeclared package boundary."""

    _write(tmp_path / "src/old/__init__.py", '"""Old package."""\n')
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "moves": [{"from": "src/old", "to": "src/new"}],
        },
    )

    with pytest.raises(RelocationError, match=r"src/new"):
        plan_relocation(tmp_path, manifest)


def test_boundary_policy_validates_declared_projection_state(tmp_path: Path) -> None:
    """Each disposition constrains the projected module blueprint at its path."""

    _write(tmp_path / "src/old/__init__.py", '"""Old package."""\n')
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "moves": [{"from": "src/old", "to": "src/new"}],
            "blueprint_documents": [
                {
                    "path": "src/new/blueprint.yaml",
                    "document": {"id": "new", "node_type": "module"},
                }
            ],
            "package_boundaries": [
                {
                    "path": "src/new",
                    "disposition": "registered-module",
                    "module_id": "new",
                    "blueprint": "src/new/blueprint.yaml",
                }
            ],
        },
    )

    assert plan_relocation(tmp_path, manifest).exists("src/new/blueprint.yaml")


def test_boundary_policy_rejects_a_blueprint_for_an_unregistered_package(
    tmp_path: Path,
) -> None:
    """Unregistered packages cannot project an Officina module blueprint."""

    _write(tmp_path / "src/old/__init__.py", '"""Old package."""\n')
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "moves": [{"from": "src/old", "to": "src/new"}],
            "blueprint_documents": [
                {
                    "path": "src/new/blueprint.yaml",
                    "document": {"id": "new", "node_type": "module"},
                }
            ],
            "package_boundaries": [
                {"path": "src/new", "disposition": "unregistered-package"}
            ],
        },
    )

    with pytest.raises(RelocationError, match=r"src/new/blueprint.yaml"):
        plan_relocation(tmp_path, manifest)


def test_manifest_rejects_unknown_or_escaping_declarations(tmp_path: Path) -> None:
    """Manifest validation closes typo and repository-escape routes."""

    with pytest.raises(RelocationError, match="unknown manifest key"):
        _manifest(tmp_path / "unknown.yaml", {"schema_version": 2, "mvoes": []})
    with pytest.raises(RelocationError, match="repository-relative"):
        _manifest(
            tmp_path / "escape.yaml",
            {
                "schema_version": 2,
                "moves": [{"from": "../outside.py", "to": "src/pkg/new.py"}],
            },
        )


def test_ownership_transfer_moves_blueprint_records_without_changing_contracts(
    tmp_path: Path,
) -> None:
    """A source transfer preserves its contract while changing module ownership."""

    old_root = tmp_path / "src/pkg/old"
    target_root = tmp_path / "src/pkg/new"
    _write(old_root / "worker.py", "def run():\n    return 1\n")
    old_module = {
        "authority": {"owns_filesystem": []},
        "children": {},
        "content": [r"__init__\.py", r"worker\.py", r"keep\.py"],
        "description": "old",
        "exports": {
            "old.interface.worker": {
                "access": {"allow_all_modules": False, "allowed_callers": ["consumer"]},
                "source_interface": "old.source.worker.interface.python-api",
            }
        },
        "gateway": {"language": "Python", "path": "__init__.py"},
        "id": "old",
        "namespace_exports": {},
        "node_type": "module",
        "schema_version": 6,
        "sources": {
            "old.source.worker": {
                "blueprint": {"base": "module-root", "path": "blueprints/worker.yaml"}
            }
        },
        "version": 1,
    }
    target_module = {
        **old_module,
        "content": [r"__init__\.py"],
        "description": "new",
        "exports": {},
        "id": "new",
        "sources": {},
    }
    sidecar = {
        "content": [r"worker\.py"],
        "dependencies": [],
        "description": "worker",
        "gateway": {"language": "Python", "path": "worker.py"},
        "id": "old.source.worker",
        "interfaces": {
            "old.source.worker.interface.python-api": {
                "contract": {"arguments": {}, "outcomes": [], "outputs": []},
                "description": "run worker",
                "version": 1,
            }
        },
        "node_type": "behavioral_source",
        "schema_version": 6,
        "version": 1,
    }
    _write(old_root / "blueprint.yaml", yaml.safe_dump(old_module, sort_keys=False))
    _write(target_root / "blueprint.yaml", yaml.safe_dump(target_module, sort_keys=False))
    _write(old_root / "blueprints/worker.yaml", yaml.safe_dump(sidecar, sort_keys=False))
    _write(tmp_path / "consumer.yaml", "uses_interfaces:\n- interface: old.interface.worker\n  version: 1\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "moves": [
                {"from": "src/pkg/old/worker.py", "to": "src/pkg/new/worker.py"},
                {
                    "from": "src/pkg/old/blueprints/worker.yaml",
                    "to": "src/pkg/new/blueprints/worker.yaml",
                },
            ],
            "renames": {
                "source_ids": [{"from": "old.source.worker", "to": "new.source.worker"}],
                "interface_ids": [
                    {"from": "old.interface.worker", "to": "new.interface.worker"},
                    {
                        "from": "old.source.worker.interface.python-api",
                        "to": "new.source.worker.interface.python-api",
                    },
                ],
            },
            "ownership_transfers": [
                {
                    "from_blueprint": "src/pkg/old/blueprint.yaml",
                    "to_blueprint": "src/pkg/new/blueprint.yaml",
                    "source": {"from": "old.source.worker", "to": "new.source.worker"},
                    "export": {"from": "old.interface.worker", "to": "new.interface.worker"},
                    "content": {
                        "from": r"worker\.py",
                        "to": r"worker\.py",
                    },
                }
            ],
        },
    )

    changes = plan_relocation(tmp_path, manifest)
    projected_old = yaml.safe_load(changes.read_text("src/pkg/old/blueprint.yaml"))
    projected_new = yaml.safe_load(changes.read_text("src/pkg/new/blueprint.yaml"))
    projected_sidecar = yaml.safe_load(
        changes.read_text("src/pkg/new/blueprints/worker.yaml")
    )

    assert projected_old["content"] == [r"__init__\.py", r"keep\.py"]
    assert projected_old["sources"] == {}
    assert projected_old["exports"] == {}
    assert projected_new["content"] == [r"__init__\.py", r"worker\.py"]
    assert projected_new["sources"]["new.source.worker"]["blueprint"]["path"] == (
        "blueprints/worker.yaml"
    )
    assert projected_new["exports"]["new.interface.worker"]["access"] == {
        "allow_all_modules": False,
        "allowed_callers": ["consumer"],
    }
    assert projected_sidecar["interfaces"][
        "new.source.worker.interface.python-api"
    ]["contract"] == sidecar["interfaces"][
        "old.source.worker.interface.python-api"
    ]["contract"]
    assert "new.interface.worker" in changes.read_text("consumer.yaml")


def test_catalog_generation_and_application_are_idempotent(tmp_path: Path) -> None:
    """The accepted projected tree publishes once and has no package facade."""

    _write(tmp_path / "src/pkg/domain/tool.py", "VALUE = 1\n")
    _write(tmp_path / "src/pkg/domain/__init__.py", "from .tool import VALUE\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "package_catalogs": [
                {
                    "path": "src/pkg/domain",
                    "summary": "Domain tools.",
                    "description": "Callers import concrete owning modules.",
                    "roles": {"tool.py": "Implements the domain operation."},
                }
            ],
            "forbid_facade_imports": ["pkg.domain"],
        },
    )
    changes = plan_relocation(tmp_path, manifest)
    rendered = changes.read_text("src/pkg/domain/__init__.py")
    assert "``tool.py``" in rendered
    assert "Implements the domain operation." in rendered
    assert "from .tool" not in rendered

    apply_change_set(changes)
    assert (tmp_path / "src/pkg/domain/__init__.py").read_text(encoding="utf-8") == rendered
    second = plan_relocation(tmp_path, manifest)
    assert second.report()["moves"] == []
    assert second.report()["writes"] == []


# famulus-skip: category=platform-contract; reason=POSIX executable modes are not represented faithfully on Windows; alternate=the relocation engine's byte-preservation and Windows-focused suites cover platform-neutral application behavior
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable modes")
def test_application_preserves_modes_of_moved_and_rewritten_files(
    tmp_path: Path,
) -> None:
    """Publishing projected bytes must not remove existing executable bits."""

    moved = tmp_path / "old.py"
    rewritten = tmp_path / "runner.py"
    _write(moved, "VALUE = 1\n")
    _write(rewritten, "MODULE = 'old.module'\n")
    moved.chmod(0o755)
    rewritten.chmod(0o755)
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "moves": [{"from": "old.py", "to": "new.py"}],
            "renames": {
                "python_modules": [
                    {"from": "old.module", "to": "new.module"}
                ]
            },
        },
    )

    apply_change_set(plan_relocation(tmp_path, manifest))

    assert stat.S_IMODE((tmp_path / "new.py").stat().st_mode) == 0o755
    assert stat.S_IMODE(rewritten.stat().st_mode) == 0o755


def test_catalog_regeneration_preserves_an_unchanged_moved_initializer(
    tmp_path: Path,
) -> None:
    """Catalog generation must not cancel a pending move-target write."""

    initializer = '''"""Domain tools.

Callers import concrete modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``tool.py``
    Provides the tool operation.
"""
'''
    _write(tmp_path / "src/pkg/old/__init__.py", initializer)
    _write(tmp_path / "src/pkg/old/tool.py", "VALUE = 1\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "moves": [{"from": "src/pkg/old", "to": "src/pkg/new"}],
            "package_boundaries": [
                {"path": "src/pkg/new", "disposition": "unregistered-package"}
            ],
            "package_catalogs": [
                {
                    "path": "src/pkg/new",
                    "summary": "Domain tools.",
                    "description": "Callers import concrete modules.",
                    "roles": {"tool.py": "Provides the tool operation."},
                }
            ],
        },
    )

    changes = plan_relocation(tmp_path, manifest)

    assert "src/pkg/new/__init__.py" in changes.writes
    apply_change_set(changes)
    assert (tmp_path / "src/pkg/new/__init__.py").read_text(
        encoding="utf-8"
    ) == initializer


def test_exact_rewrite_precondition_fails_before_any_write(tmp_path: Path) -> None:
    """A missing exceptional rewrite makes the whole projected change invalid."""

    original = tmp_path / "src/pkg/module.py"
    _write(original, "VALUE = 1\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "exact_rewrites": [
                {
                    "path": "src/pkg/module.py",
                    "from": "MISSING = 1",
                    "to": "PRESENT = 1",
                }
            ],
        },
    )

    with pytest.raises(RelocationError, match="exact rewrite precondition"):
        plan_relocation(tmp_path, manifest)
    assert original.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_exact_rewrite_is_idempotent_when_replacement_contains_original(
    tmp_path: Path,
) -> None:
    """A status block extending its header must not be appended repeatedly."""

    _write(tmp_path / "plan.md", "Header\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "exact_rewrites": [
                {
                    "path": "plan.md",
                    "from": "Header",
                    "to": "Header\n\nStatus: complete",
                }
            ],
        },
    )
    first = plan_relocation(tmp_path, manifest)
    apply_change_set(first)

    second = plan_relocation(tmp_path, manifest)

    assert second.report()["writes"] == []


def test_exact_rewrite_rejects_old_and_new_text_side_by_side(tmp_path: Path) -> None:
    """An unrelated replacement occurrence must not hide an unapplied rewrite."""

    _write(tmp_path / "module.py", "OLD\nNEW\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "exact_rewrites": [
                {"path": "module.py", "from": "OLD", "to": "NEW"}
            ],
        },
    )

    with pytest.raises(RelocationError, match="exact rewrite precondition"):
        plan_relocation(tmp_path, manifest)


def test_exact_rewrite_applies_when_replacement_is_a_prefix_of_old(
    tmp_path: Path,
) -> None:
    """An old block containing its replacement must still be shortened once."""

    _write(tmp_path / "module.py", "HEADER\nREMOVE\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "exact_rewrites": [
                {
                    "path": "module.py",
                    "from": "HEADER\nREMOVE",
                    "to": "HEADER",
                }
            ],
        },
    )

    first = plan_relocation(tmp_path, manifest)
    assert first.read_text("module.py") == "HEADER\n"
    apply_change_set(first)
    assert plan_relocation(tmp_path, manifest).report()["writes"] == []


def test_command_preflights_then_applies_the_same_report(tmp_path: Path) -> None:
    """The thin command exposes one manifest through read-only and apply modes."""

    _write(tmp_path / "old.py", "VALUE = 1\n")
    manifest_path = tmp_path / "move.yaml"
    _write(
        manifest_path,
        yaml.safe_dump(
            {
                "schema_version": 2,
                "moves": [{"from": "old.py", "to": "new.py"}],
            },
            sort_keys=False,
        ),
    )
    command = Path(__file__).resolve().parents[1] / "scripts/relocate_officina_sources.py"
    preflight = subprocess.run(
        [
            sys.executable,
            str(command),
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert preflight.returncode == 0, preflight.stderr
    assert (tmp_path / "old.py").exists()
    expected_report = json.loads(preflight.stdout)

    applied = subprocess.run(
        [
            sys.executable,
            str(command),
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
            "--apply",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout) == expected_report
    assert not (tmp_path / "old.py").exists()
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_plan_snapshots_repository_file_inventory_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many package catalogs must not trigger repeated full repository walks."""

    _write(tmp_path / "src/pkg/one/a.py", "A = 1\n")
    _write(tmp_path / "src/pkg/two/b.py", "B = 1\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "package_boundaries": [
                {"path": "src/pkg/one", "disposition": "unregistered-package"},
                {"path": "src/pkg/two", "disposition": "unregistered-package"},
            ],
            "package_catalogs": [
                {"path": "src/pkg/one", "summary": "One.", "description": "One."},
                {"path": "src/pkg/two", "summary": "Two.", "description": "Two."},
            ],
        },
    )
    original_rglob = Path.rglob
    root_walks = 0

    def counted_rglob(path: Path, pattern: str):
        nonlocal root_walks
        if path == tmp_path.resolve() and pattern == "*":
            root_walks += 1
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", counted_rglob)

    plan_relocation(tmp_path, manifest)

    assert root_walks == 1


def test_plan_excludes_nested_worktree_metadata(tmp_path: Path) -> None:
    """Copies maintained by other worktree managers are outside repository scope."""

    _write(tmp_path / "src/pkg/domain.py", "VALUE = 1\n")
    _write(
        tmp_path / ".worktrees/old/src/pkg/bad.py",
        "from pkg.domain import VALUE\n",
    )
    _write(
        tmp_path / ".claude/worktrees/old/src/pkg/bad.py",
        "from pkg.domain import VALUE\n",
    )
    _write(
        tmp_path / "build/lib/pkg/bad.py",
        "from pkg.domain import VALUE\n",
    )
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 2,
            "forbid_facade_imports": ["pkg.domain"],
            "inventory_exclusions": [".claude"],
        },
    )

    changes = plan_relocation(tmp_path, manifest)

    assert all(
        "worktrees" not in path and not path.startswith("build/")
        for path in changes.projected_files()
    )
