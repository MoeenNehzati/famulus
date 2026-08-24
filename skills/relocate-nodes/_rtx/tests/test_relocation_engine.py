"""Behavioral tests for manifest-driven Officina source relocation."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

import pytest
import yaml

from .. import _relocation_engine as engine_module
from .._relocation_engine import (
    ChangeSet,
    DerivedIdentityMap,
    RelocationError,
    Rename,
    SemanticDecision,
    apply_change_set,
    load_manifest,
    plan_relocation,
)
from .._relocation_semantics import SemanticOccurrence
from .._relocate_nodes import Interface


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _manifest(path: Path, value: dict[str, object]):
    _write(path, yaml.safe_dump(value, sort_keys=False))
    return load_manifest(path)


@pytest.mark.parametrize(
    "fault",
    (
        "change-preserved-file",
        "add-included-text",
        "add-empty-directory",
        "remove-scanned-file",
        "binary-becomes-utf8",
        "symlink-becomes-file",
        "change-exclusion-boundary",
    ),
)
def test_apply_rejects_every_physical_inventory_change_before_publish(
    tmp_path: Path,
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The public apply route returns 2 before writing on physical inventory drift."""

    repository = tmp_path / "repository"
    _write(repository / "old.txt", "payload\n")
    _write(repository / "preserved.md", "preserved\n")
    _write(repository / "scanned.md", "scanned\n")
    (repository / "binary.bin").write_bytes(b"\xff\x00")
    (repository / "preserved-link").symlink_to("preserved.md")
    _write(repository / ".scratch/ignored.md", "ignored\n")
    manifest_path = tmp_path / "move.yaml"
    _manifest(
        manifest_path,
        {
            "schema_version": 3,
            "relocations": [{"from": "old.txt", "to": "new.txt"}],
            "inventory_exclusions": [".scratch"],
        },
    )
    actual_apply = engine_module.apply_change_set

    def inject_fault(changes: object) -> None:
        if fault == "change-preserved-file":
            _write(repository / "preserved.md", "changed\n")
        elif fault == "add-included-text":
            _write(repository / "added.md", "added\n")
        elif fault == "add-empty-directory":
            (repository / "added-directory").mkdir()
        elif fault == "remove-scanned-file":
            (repository / "scanned.md").unlink()
        elif fault == "binary-becomes-utf8":
            _write(repository / "binary.bin", "now text\n")
        elif fault == "symlink-becomes-file":
            (repository / "preserved-link").unlink()
            _write(repository / "preserved-link", "preserved.md\n")
        else:
            os.rename(repository / ".scratch", repository / ".scratch-content")
            (repository / ".scratch").symlink_to(".scratch-content")
        actual_apply(changes)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_module, "apply_change_set", inject_fault)
    interface = Interface()
    monkeypatch.setattr(interface, "_synchronize", lambda repository, *, check: None)
    args = interface.build_parser().parse_args(
        ["--root", str(repository), "--manifest", str(manifest_path), "--apply"]
    )

    assert interface.run(args) == 2
    assert "repository changed after preflight" in capsys.readouterr().err
    assert (repository / "old.txt").read_text(encoding="utf-8") == "payload\n"
    assert not (repository / "new.txt").exists()


def test_apply_documents_per_file_atomicity_without_repository_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-publish fault retains completed atomic files without rolling back."""

    _write(tmp_path / "old-a.txt", "first\n")
    _write(tmp_path / "old-b.txt", "second\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "relocations": [
                {"from": "old-a.txt", "to": "new-a.txt"},
                {"from": "old-b.txt", "to": "new-b.txt"},
            ],
        },
    )
    changes = plan_relocation(tmp_path, manifest)
    original_replace = os.replace
    replacements = 0

    def fail_second_replace(source: Path, target: Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("injected mid-publish fault")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="injected mid-publish fault"):
        apply_change_set(changes)

    assert (tmp_path / "new-a.txt").read_text(encoding="utf-8") == "first\n"
    assert not (tmp_path / "new-b.txt").exists()
    assert (tmp_path / "old-a.txt").is_file()
    assert (tmp_path / "old-b.txt").is_file()


def test_manifest_v3_loads_unified_relocations(tmp_path: Path) -> None:
    """Schema v3 exposes one relocation collection instead of legacy moves."""

    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "relocations": [
                {"from": "skills/a/b/c", "to": "skills/a/d/e"}
            ],
        },
    )

    assert [(item.source, item.target) for item in manifest.relocations] == [
        ("skills/a/b/c", "skills/a/d/e")
    ]


@pytest.mark.parametrize("legacy_key", ["moves", "renames"])
def test_manifest_v3_rejects_legacy_relocation_keys(
    tmp_path: Path, legacy_key: str
) -> None:
    """The breaking v3 contract reports legacy top-level keys as unknown."""

    with pytest.raises(RelocationError, match=rf"unknown manifest key: {legacy_key}"):
        _manifest(
            tmp_path / "move.yaml",
            {"schema_version": 3, legacy_key: [] if legacy_key == "moves" else {}},
        )


def test_manifest_v2_reports_explicit_migration_text(tmp_path: Path) -> None:
    """A stale manifest receives a direct v2-to-v3 migration instruction."""

    with pytest.raises(RelocationError, match="migrate moves/renames"):
        _manifest(tmp_path / "move.yaml", {"schema_version": 2})


def test_semantic_decisions_rewrite_and_preserve_identical_matches(
    tmp_path: Path,
) -> None:
    """Complete selectors distinguish identical text and survive target postflight."""

    repository = tmp_path / "repository"
    _write(
        repository / "officina.toml",
        'schema_version = 1\n\n[modules]\nroots = ["skills"]\n',
    )

    def module(relative: str, node_id: str, children: dict[str, object]) -> None:
        _write(
            repository / relative / "blueprint.yaml",
            yaml.safe_dump(
                {
                    "schema_version": 6,
                    "id": node_id,
                    "node_type": "module",
                    "children": children,
                },
                sort_keys=False,
            ),
        )

    module("skills/a", "a", {"b": {}, "d": {}})
    module("skills/a/b", "a.b", {"c": {}})
    module("skills/a/d", "a.d", {})
    module("skills/a/b/c", "a.b.c", {})
    _write(repository / "notes.md", "rewrite b.c here\npreserve b.c there\n")
    manifest_path = tmp_path / "move.yaml"
    base = {
        "schema_version": 3,
        "relocations": [{"from": "skills/a/b/c", "to": "skills/a/d/e"}],
    }
    first = plan_relocation(repository, _manifest(manifest_path, base))
    occurrences = [
        item
        for item in first.semantic_occurrences
        if getattr(item, "path") == "notes.md" and getattr(item, "match") == "b.c"
    ]
    assert len(occurrences) == 2

    decisions: list[dict[str, object]] = []
    for occurrence, disposition, text, replacement in (
        (occurrences[0], "rewrite", "rewrite b.c here", "rewrite d.e here"),
        (occurrences[1], "preserve", "preserve b.c there", None),
    ):
        decision = {
            "occurrence_id": occurrence.occurrence_id,
            "mapping_kind": occurrence.mapping_kind,
            "mapping_id": occurrence.mapping_id,
            "path": occurrence.path,
            "original_digest": occurrence.projected_digest,
            "byte_start": occurrence.byte_start,
            "byte_end": occurrence.byte_end,
            "ordinal": occurrence.ordinal,
            "match": occurrence.match,
            "count": 1,
            "disposition": disposition,
            "text": text,
            "reason": f"Reviewed {disposition} decision.",
        }
        if replacement is not None:
            decision["replacement"] = replacement
        decisions.append(decision)
    for occurrence in first.semantic_occurrences:
        if occurrence in occurrences:
            continue
        decisions.append(
            {
                "occurrence_id": occurrence.occurrence_id,
                "mapping_kind": occurrence.mapping_kind,
                "mapping_id": occurrence.mapping_id,
                "path": occurrence.path,
                "original_digest": occurrence.projected_digest,
                "byte_start": occurrence.byte_start,
                "byte_end": occurrence.byte_end,
                "ordinal": occurrence.ordinal,
                "match": occurrence.match,
                "count": 1,
                "disposition": "preserve",
                "text": occurrence.context,
                "reason": "The surviving ancestor is still an active node.",
            }
        )
    decided_manifest = _manifest(
        manifest_path,
        {**base, "semantic_decisions": decisions},
    )

    decided = plan_relocation(repository, decided_manifest)

    assert decided.read_text("notes.md") == "rewrite d.e here\npreserve b.c there\n"
    assert decided.unaccounted_semantic_occurrences == []
    apply_change_set(decided)
    postflight = plan_relocation(repository, decided_manifest)
    assert postflight.report()["writes"] == []
    assert postflight.unaccounted_semantic_occurrences == []


def test_preflight_uses_typed_renames_without_writing(tmp_path: Path) -> None:
    """A plan derives address variants while leaving the repository untouched."""

    source = tmp_path / "src/pkg/old_name.py"
    caller = tmp_path / "tests/test_caller.py"
    _write(source, "VALUE = 'old.source.item old.interface.item'\n")
    _write(caller, "from pkg.old_name import VALUE\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "relocations": [
                {
                    "from": "src/pkg/old_name.py",
                    "to": "src/pkg/new/name.py",
                    "python_modules": [
                        {"from": "pkg.old_name", "to": "pkg.new.name"}
                    ],
                }
            ],
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


def test_python_projection_rewrites_only_absolute_import_ast_spans(
    tmp_path: Path,
) -> None:
    """Comments, strings, relative imports, and near names remain byte-identical."""

    _write(tmp_path / "old.py", "VALUE = 1\n")
    caller = (
        "import old_pkg.api\n"
        "import old_pkg.api.child as child\n"
        "from old_pkg.api import VALUE\n"
        "from .old_pkg.api import RELATIVE\n"
        "# import old_pkg.api\n"
        "TEXT = 'old_pkg.api'\n"
        "import old_pkgish.api\n"
    )
    _write(tmp_path / "caller.py", caller)
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "relocations": [
                {
                    "from": "old.py",
                    "to": "new.py",
                    "python_modules": [
                        {"from": "old_pkg.api", "to": "new_pkg.api"}
                    ],
                }
            ],
        },
    )

    changes = plan_relocation(tmp_path, manifest)

    assert changes.read_text("caller.py") == (
        "import new_pkg.api\n"
        "import new_pkg.api.child as child\n"
        "from new_pkg.api import VALUE\n"
        "from .old_pkg.api import RELATIVE\n"
        "# import old_pkg.api\n"
        "TEXT = 'old_pkg.api'\n"
        "import old_pkgish.api\n"
    )


def test_schema_v3_preserves_graph_owned_file_relocation(
    tmp_path: Path,
) -> None:
    """A non-node endpoint remains relocatable only with one graph-proven owner."""

    _write(
        tmp_path / "officina.toml",
        'schema_version = 1\n\n[modules]\nroots = ["skills"]\n',
    )
    _write(
        tmp_path / "skills/a/blueprint.yaml",
        yaml.safe_dump(
            {
                "schema_version": 6,
                "id": "a",
                "node_type": "module",
                "children": {},
                "content": [r"old\.py", r"new\.py"],
            },
            sort_keys=False,
        ),
    )
    _write(tmp_path / "skills/a/old.py", "VALUE = 1\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "relocations": [
                {"from": "skills/a/old.py", "to": "skills/a/new.py"}
            ],
        },
    )

    assert plan_relocation(tmp_path, manifest).exists("skills/a/new.py")

    blueprint = yaml.safe_load(
        (tmp_path / "skills/a/blueprint.yaml").read_text(encoding="utf-8")
    )
    blueprint["content"] = []
    _write(
        tmp_path / "skills/a/blueprint.yaml",
        yaml.safe_dump(blueprint, sort_keys=False),
    )
    with pytest.raises(RelocationError, match="exactly one blueprint owner"):
        plan_relocation(tmp_path, manifest)


def test_nested_node_projection_rewrites_only_structural_identities(
    tmp_path: Path,
) -> None:
    """A subtree move closes graph identities while prose-like bytes stay authored."""

    _write(
        tmp_path / "officina.toml",
        'schema_version = 1\n\n[modules]\nroots = ["skills"]\n',
    )

    def module(relative: str, node_id: str, children: dict[str, object], **extra: object) -> None:
        _write(
            tmp_path / relative / "blueprint.yaml",
            yaml.safe_dump(
                {
                    "schema_version": 6,
                    "id": node_id,
                    "node_type": "module",
                    "children": children,
                    **extra,
                },
                sort_keys=False,
            ),
        )

    module("skills/a", "a", {"b": {}, "d": {}})
    module("skills/a/b", "a.b", {"c": {}})
    module("skills/a/d", "a.d", {})
    module(
        "skills/a/b/c",
        "a.b.c",
        {"_rtx": {}},
        sources={
            "a.b.c.source.worker": {
                "blueprint": {"base": "module-root", "path": "blueprints/worker.yaml"}
            }
        },
        exports={
            "a.b.c.interface.worker": {
                "source_interface": "a.b.c.source.worker.interface.run",
                "access": {
                    "allow_all_modules": False,
                    "allowed_callers": ["a.b.c._rtx"],
                },
            }
        },
    )
    module("skills/a/b/c/_rtx", "a.b.c._rtx", {})
    _write(
        tmp_path / "skills/a/b/c/blueprints/worker.yaml",
        yaml.safe_dump(
            {
                "schema_version": 6,
                "id": "a.b.c.source.worker",
                "node_type": "behavioral_source",
                "interfaces": {
                    "a.b.c.source.worker.interface.run": {
                        "uses_interfaces": [
                            {"interface": "a.b.c.interface.worker", "version": 1}
                        ]
                    }
                },
                "dependencies": ["a.b.c._rtx"],
            },
            sort_keys=False,
        ),
    )
    _write(
        tmp_path / "consumer.yaml",
        "schema_version: 6\nid: fixture.consumer\nnode_type: behavioral_source\n"
        "uses_interfaces:\n- interface: a.b.c.interface.worker\n  version: 1\n",
    )
    untouched = {
        "notes.md": "Mention a.b.c and skills/a/b/c in prose.\n",
        "proof.tex": "\\texttt{a.b.c}\n",
        "literal.py": "VALUE = 'a.b.c'  # a.b.c\n",
        ".config/g-calendar": "a.b.c\n",
    }
    for relative, text in untouched.items():
        _write(tmp_path / relative, text)
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "relocations": [
                {"from": "skills/a/b/c", "to": "skills/a/d/e"}
            ],
        },
    )

    changes = plan_relocation(tmp_path, manifest)

    assert yaml.safe_load(changes.read_text("skills/a/b/blueprint.yaml"))["children"] == {}
    assert yaml.safe_load(changes.read_text("skills/a/d/blueprint.yaml"))["children"] == {"e": {}}
    moved = yaml.safe_load(changes.read_text("skills/a/d/e/blueprint.yaml"))
    assert moved["id"] == "a.d.e"
    assert moved["children"] == {"_rtx": {}}
    assert "a.d.e.source.worker" in moved["sources"]
    assert "a.d.e.interface.worker" in moved["exports"]
    nested = yaml.safe_load(changes.read_text("skills/a/d/e/_rtx/blueprint.yaml"))
    assert nested["id"] == "a.d.e._rtx"
    assert "a.d.e.interface.worker" in changes.read_text("consumer.yaml")
    for relative, text in untouched.items():
        assert changes.read_text(relative) == text

    apply_change_set(changes)
    assert plan_relocation(tmp_path, manifest).report()["moves"] == []


def test_manifest_v3_requires_explicit_package_boundary_dispositions(
    tmp_path: Path,
) -> None:
    """A v3 manifest parses each declared package-policy record by disposition."""

    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
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
        {"schema_version": 2},
        {
            "schema_version": 3,
            "package_boundaries": [
                {"path": "src/officina/tools", "disposition": "registered-module"}
            ],
        },
        {
            "schema_version": 3,
            "package_boundaries": [
                {
                    "path": "src/officina/tools",
                    "disposition": "unregistered-package",
                    "module_id": "tools",
                }
            ],
        },
        {
            "schema_version": 3,
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
                "schema_version": 3,
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
            "schema_version": 3,
            "relocations": [{"from": "src/old", "to": "src/new"}],
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
            "schema_version": 3,
            "relocations": [{"from": "src/old", "to": "src/new"}],
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
            "schema_version": 3,
            "relocations": [{"from": "src/old", "to": "src/new"}],
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
        _manifest(tmp_path / "unknown.yaml", {"schema_version": 3, "mvoes": []})
    with pytest.raises(RelocationError, match="repository-relative"):
        _manifest(
            tmp_path / "escape.yaml",
            {
                "schema_version": 3,
                "relocations": [{"from": "../outside.py", "to": "src/pkg/new.py"}],
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
    _write(
        tmp_path / "consumer.yaml",
        "schema_version: 6\nid: fixture.consumer\nnode_type: behavioral_source\n"
        "uses_interfaces:\n- interface: old.interface.worker\n  version: 1\n",
    )
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "relocations": [
                {"from": "src/pkg/old/worker.py", "to": "src/pkg/new/worker.py"},
                {
                    "from": "src/pkg/old/blueprints/worker.yaml",
                    "to": "src/pkg/new/blueprints/worker.yaml",
                },
            ],
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
            "schema_version": 3,
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
    _write(rewritten, "import old.module\n")
    moved.chmod(0o755)
    rewritten.chmod(0o755)
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "relocations": [
                {
                    "from": "old.py",
                    "to": "new.py",
                    "python_modules": [
                        {"from": "old.module", "to": "new.module"}
                    ],
                }
            ],
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
            "schema_version": 3,
            "relocations": [{"from": "src/pkg/old", "to": "src/pkg/new"}],
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
            "schema_version": 3,
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


def test_exact_rewrite_cannot_erase_a_semantic_occurrence(tmp_path: Path) -> None:
    """Exceptional exact rewrites cannot bypass semantic adjudication."""
    _write(tmp_path / "old.txt", "reference old.txt\n")
    manifest = _manifest(tmp_path / "move.yaml", {
        "schema_version": 3,
        "relocations": [{"from": "old.txt", "to": "new.txt"}],
        "exact_rewrites": [{"path": "new.txt", "from": "old.txt", "to": "new.txt"}],
    })
    with pytest.raises(RelocationError, match="exact rewrite targets semantic occurrence"):
        plan_relocation(tmp_path, manifest)


def test_preserve_decision_owns_only_occurrences_inside_its_selected_span(tmp_path: Path) -> None:
    """One preserved context cannot conceal an identical unreviewed occurrence."""
    _write(tmp_path / "notes.md", "review old and old here\nunreviewed old there\n")
    changes = ChangeSet(tmp_path)
    decision = SemanticDecision(
        "selected", "physical_fragment", "move", "notes.md", "sha256:x",
        7, 10, 1, "old", 1, "preserve", "review old and old here", "intentional",
    )
    occurrence = SemanticOccurrence(
        "unreviewed", "physical_fragment", "move", "move", "notes.md",
        "sha256:x", 15, 18, 1, 16, 2, "old", "new", "review old and old here",
    )
    assert not engine_module._is_accounted_final_occurrence(changes, occurrence, (decision,))


def test_semantic_decision_count_must_be_exactly_one(tmp_path: Path) -> None:
    """A complete occurrence selector cannot adjudicate repeated text as a group."""

    with pytest.raises(RelocationError, match="invalid relocation manifest"):
        _manifest(tmp_path / "manifest.yaml", {
            "schema_version": 3,
            "semantic_decisions": [{
                "occurrence_id": "sha256:selector",
                "mapping_kind": "physical_fragment",
                "mapping_id": "old->new",
                "path": "notes.md",
                "original_digest": "sha256:" + "0" * 64,
                "byte_start": 0,
                "byte_end": 3,
                "ordinal": 1,
                "match": "old",
                "count": 2,
                "disposition": "preserve",
                "text": "old",
                "reason": "reviewed",
            }],
        })


def test_projected_move_destinations_cannot_overlap(tmp_path: Path) -> None:
    """Two source files cannot silently project onto the same destination."""
    _write(tmp_path / "one/item.txt", "one\n")
    _write(tmp_path / "two/item.txt", "two\n")
    manifest = _manifest(tmp_path / "move.yaml", {
        "schema_version": 3,
        "relocations": [
            {"from": "one", "to": "out"},
            {"from": "two/item.txt", "to": "out/item.txt"},
        ],
    })
    with pytest.raises(RelocationError, match="projected move target collision"):
        plan_relocation(tmp_path, manifest)


def test_structural_projectors_ignore_untyped_yaml_and_noncommand_text(tmp_path: Path) -> None:
    """Familiar keys and command-like prose remain for semantic review."""
    _write(tmp_path / "data.yaml", "id: old.interface.default\n")
    _write(tmp_path / "notes.md", "dispatcher --caller-skill old old.interface.default\n")
    _write(tmp_path / "run.sh", "dispatcher --caller-skill old old.interface.default\n")
    changes = ChangeSet(tmp_path)
    mapping = DerivedIdentityMap(
        "skills/old", "skills/new", source_node_id="old", target_node_id="new",
        module_ids=(Rename("old", "new"),),
        interface_ids=(Rename("old.interface.default", "new.interface.default"),),
    )
    manifest = _manifest(tmp_path / "manifest.yaml", {"schema_version": 3})
    engine_module._project_derived_blueprints(changes, manifest, (mapping,))
    engine_module._project_structural_code(changes, manifest, (mapping,))
    assert changes.read_text("data.yaml") == "id: old.interface.default\n"
    assert changes.read_text("notes.md") == "dispatcher --caller-skill old old.interface.default\n"
    assert changes.read_text("run.sh") == "dispatcher --caller-skill new new.interface.default\n"


def test_dispatcher_projection_rewrites_only_recognized_argument_tokens(tmp_path: Path) -> None:
    """Comments and unrelated option values stay authored for semantic review."""

    line = (
        "dispatcher --caller-skill old --note old "
        "old.interface.default # old old.interface.default\n"
    )
    _write(tmp_path / "run.sh", line)
    changes = ChangeSet(tmp_path, derived_relocations=(DerivedIdentityMap(
        "skills/old", "skills/new", source_node_id="old", target_node_id="new",
        module_ids=(Rename("old", "new"),),
        interface_ids=(Rename("old.interface.default", "new.interface.default"),),
    ),))
    manifest = _manifest(tmp_path / "manifest.yaml", {"schema_version": 3})

    engine_module._project_structural_code(changes, manifest, changes.derived_relocations)

    assert changes.read_text("run.sh") == (
        "dispatcher --caller-skill new --note old "
        "new.interface.default # old old.interface.default\n"
    )
    from .._relocation_semantics import SemanticScan
    occurrences = [item for item in SemanticScan(changes).run().occurrences if item.path == "run.sh"]
    assert len(occurrences) >= 2


def test_move_preserves_safe_internal_symlink_without_dereferencing(tmp_path: Path) -> None:
    """A moved internal link remains a link with the same relative link text."""
    _write(tmp_path / "old/target.txt", "payload\n")
    (tmp_path / "old/link.txt").symlink_to("target.txt")
    manifest = _manifest(tmp_path / "move.yaml", {
        "schema_version": 3, "relocations": [{"from": "old", "to": "new"}],
    })
    changes = plan_relocation(tmp_path, manifest)
    apply_change_set(changes)
    assert (tmp_path / "new/link.txt").is_symlink()
    assert os.readlink(tmp_path / "new/link.txt") == "target.txt"
    assert not (tmp_path / "old").exists()


@pytest.mark.parametrize(
    ("link_path", "link_text", "expected"),
    (("old/link", "directory", "directory"), ("old/sub/link", "../target.txt", "../target.txt")),
)
def test_move_preserves_internal_directory_and_normalized_parent_links(
    tmp_path: Path, link_path: str, link_text: str, expected: str
) -> None:
    """Moved directory targets and safe parent components remain symlinks."""

    _write(tmp_path / "old/directory/item.txt", "payload\n")
    _write(tmp_path / "old/target.txt", "target\n")
    (tmp_path / PurePosixPath(link_path)).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / PurePosixPath(link_path)).symlink_to(link_text)
    manifest = _manifest(tmp_path / "move.yaml", {
        "schema_version": 3, "relocations": [{"from": "old", "to": "new"}],
    })

    changes = plan_relocation(tmp_path, manifest)
    apply_change_set(changes)

    moved = tmp_path / PurePosixPath(link_path.replace("old/", "new/", 1))
    assert moved.is_symlink()
    assert os.readlink(moved) == expected


@pytest.mark.parametrize("link_text", ("missing.txt", "../../outside.txt"))
def test_move_rejects_unsafe_symlink(tmp_path: Path, link_text: str) -> None:
    """Dangling and repository-escaping links are rejected, never followed."""
    (tmp_path / "old").mkdir()
    _write(tmp_path.parent / "outside.txt", "outside\n")
    (tmp_path / "old/link.txt").symlink_to(link_text)
    manifest = _manifest(tmp_path / "move.yaml", {
        "schema_version": 3, "relocations": [{"from": "old", "to": "new"}],
    })
    with pytest.raises(RelocationError, match="unsafe move symlink"):
        plan_relocation(tmp_path, manifest)


def test_exact_rewrite_is_idempotent_when_replacement_contains_original(
    tmp_path: Path,
) -> None:
    """A status block extending its header must not be appended repeatedly."""

    _write(tmp_path / "plan.md", "Header\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
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
            "schema_version": 3,
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
            "schema_version": 3,
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
            "schema_version": 3,
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
            "schema_version": 3,
            "forbid_facade_imports": ["pkg.domain"],
            "inventory_exclusions": [".claude"],
        },
    )

    changes = plan_relocation(tmp_path, manifest)

    assert all(
        "worktrees" not in path and not path.startswith("build/")
        for path in changes.projected_files()
    )


def test_plan_combines_default_and_manifest_inventory_exclusions(
    tmp_path: Path,
) -> None:
    """Host projections and caller-selected trees stay outside inventory."""

    _write(tmp_path / "src/pkg/domain.py", "VALUE = 1\n")
    for directory in (".git", ".claude", ".codex", ".superpowers", ".scratch"):
        _write(tmp_path / directory / "ignored.py", "VALUE = 2\n")
    manifest = _manifest(
        tmp_path / "move.yaml",
        {
            "schema_version": 3,
            "inventory_exclusions": [".scratch"],
        },
    )

    changes = plan_relocation(tmp_path, manifest)

    projected = changes.projected_files()
    ignored_roots = (".git", ".claude", ".codex", ".superpowers", ".scratch")
    assert set(changes.inventory_exclusions) == set(ignored_roots)
    assert "src/pkg/domain.py" in projected
    assert all(
        not any(path == root or path.startswith(root + "/") for root in ignored_roots)
        for path in projected
    )
