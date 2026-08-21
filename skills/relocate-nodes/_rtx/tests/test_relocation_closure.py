"""Focused behavioral tests for deterministic relocation closure."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, TypeVar

import pytest
import yaml

from .. import _relocation_closure as closure_module
from .._relocation_closure import (
    MechanicalClosureError,
    MechanicalClosureResult,
    close_projected_relocation as _close_projected_relocation,
)
from .._relocation_engine import (
    BlueprintSynchronizer,
    ChangeSet,
    ExactRewrite,
    Move,
    OwnershipTransfer,
    PackageBoundary,
    PackageCatalog,
    Rename,
    RelocationManifest,
    apply_change_set,
    plan_relocation,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
T = TypeVar("T")


closure_module.subprocess = subprocess


def _fixture_synchronize(repository: Path, *, check: bool) -> None:
    """Adapt legacy fixture callbacks to the injected synchronizer boundary."""

    command = [sys.executable, "fixture-synchronizer"]
    if check:
        command.append("--check")
    completed = closure_module.subprocess.run(command, cwd=repository)
    if completed.returncode != 0:
        raise MechanicalClosureError("fixture synchronizer failed")


def close_projected_relocation(
    changes: ChangeSet,
    manifest: RelocationManifest,
    *,
    synchronize: BlueprintSynchronizer | None = None,
) -> MechanicalClosureResult:
    """Inject a test-owned synchronizer when a test has no explicit one."""

    return _close_projected_relocation(
        changes,
        manifest,
        synchronize=synchronize or _fixture_synchronize,
    )


def test_relocation_closure_test_module_parses_as_python_311() -> None:
    """The acceptance fixture remains runnable on the oldest supported Python."""

    ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=(3, 11))


def _one(values: tuple[T, ...], predicate: Callable[[T], bool]) -> T:
    """Return the one declaration matching an acceptance-fixture predicate."""

    return next(value for value in values if predicate(value))


def _empty_runtime_dependencies() -> str:
    """Render the deterministic empty v2 runtime-dependency artifact."""

    return json.dumps(
        {
            "version": 2,
            "skills": {},
            "all": {
                "python-package": [],
                "binary": [],
                "system-service": [],
                "system-library": [],
                "external-application": [],
                "runtime": [],
                "model-data": [],
            },
        },
        indent=2,
    ) + "\n"


def _write(path: Path, text: str) -> None:
    """Write one UTF-8 fixture file, creating its parents."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _closure_fixture(
    tmp_path: Path,
    initializer: str = '"""Catalog package."""\n',
    skill_content: str = "# Demo\n<!-- BEGIN BLUEPRINT CONTRACT -->\nold\n<!-- END BLUEPRINT CONTRACT -->\n",
    include_module_schema: bool = True,
    module_schema_directory: bool = False,
    agents_link_target: str | None = None,
    assistant_tooling: bool = False,
    officina_content: str = '"""Officina."""\n',
) -> tuple[ChangeSet, RelocationManifest]:
    """Build the smallest projected tree that exercises mechanical closure."""

    _write(
        tmp_path / "references/certification/certification-basis-roots.json",
        json.dumps(["src/officina/__init__.py"], indent=2) + "\n",
    )
    _write(tmp_path / "src/officina/__init__.py", officina_content)
    _write(tmp_path / "references/blueprint/schema.json", "{}\n")
    if include_module_schema:
        _write(tmp_path / "references/blueprint/module.schema.json", "{}\n")
    elif module_schema_directory:
        _write(
            tmp_path / "references/blueprint/module.schema.json/placeholder",
            "not a schema file\n",
        )
    _write(
        tmp_path / "skills/demo/SKILL.md",
        skill_content,
    )
    if agents_link_target is not None:
        if agents_link_target == "CLAUDE.md":
            _write(tmp_path / "CLAUDE.md", "Shadow instructions.\n")
        (tmp_path / "AGENTS.md").symlink_to(agents_link_target)
    if assistant_tooling:
        _write(tmp_path / ".agents/session.json", "{}\n")
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex/agents").symlink_to("missing-agent")
    changes = ChangeSet(tmp_path)
    changes.write_text("src/officina/catalog/__init__.py", initializer)
    manifest = RelocationManifest(
        package_catalogs=(
            PackageCatalog(
                path="src/officina/catalog",
                summary="Catalog package.",
                description="Documents catalog ownership.",
            ),
        ),
    )
    return changes, manifest


def test_shadow_preserves_an_internal_relative_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository-relative symlink is preserved by link text in the shadow."""

    changes, manifest = _closure_fixture(tmp_path, agents_link_target="CLAUDE.md")
    observed: list[str] = []

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Record the materialized symlink seen by the shadow synchronizer."""

        shadow_link = Path(str(kwargs["cwd"])) / "AGENTS.md"
        assert shadow_link.is_symlink()
        observed.append(shadow_link.readlink().as_posix())
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    close_projected_relocation(changes, manifest)

    assert observed == ["CLAUDE.md", "CLAUDE.md"]


@pytest.mark.parametrize(
    ("link_target", "external_target"),
    [
        ("../outside.md", True),
    ],
    ids=["escape"],
)
def test_shadow_rejects_unsafe_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_target: str,
    external_target: bool,
) -> None:
    """An escaping link fails with its exact repository path."""

    if external_target:
        _write(tmp_path.parent / "outside.md", "outside\n")
    changes, manifest = _closure_fixture(tmp_path, agents_link_target=link_target)
    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", _sync_without_writes)

    with pytest.raises(MechanicalClosureError, match=r"unsafe shadow symlink: AGENTS\.md"):
        close_projected_relocation(changes, manifest)


def test_injected_synchronizer_receives_the_shadow_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The injected synchronizer receives the materialized shadow rather than source."""

    changes, manifest = _closure_fixture(
        tmp_path,
        officina_content='ORIGIN = "shadow"\n',
    )
    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)

    def synchronize(repository: Path, *, check: bool) -> None:
        assert (repository / "src/officina/__init__.py").read_text(encoding="utf-8") == (
            'ORIGIN = "shadow"\n'
        )

    result = close_projected_relocation(changes, manifest, synchronize=synchronize)

    assert result.validation_results == (
        "blueprint synchronizer synchronize",
        "blueprint synchronizer check",
        "repository blueprint graph",
    )


def test_closure_uses_the_injected_synchronizer_for_sync_then_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closure uses the supplied authorized synchronizer instead of a private path."""

    changes, manifest = _closure_fixture(tmp_path)
    calls: list[tuple[Path, bool]] = []

    def synchronize(repository: Path, *, check: bool) -> None:
        calls.append((repository, check))

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)

    close_projected_relocation(changes, manifest, synchronize=synchronize)

    assert [check for _, check in calls] == [False, True]
    assert all(repository != tmp_path for repository, _ in calls)


def test_stale_generated_artifact_is_synchronized_before_check_and_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synchronize stale generated output before the check-only and graph passes."""

    changes, manifest = _closure_fixture(tmp_path)
    target = "references/blueprint/runtime_dependencies.json"
    expected = _empty_runtime_dependencies()
    changes.write_text(target, "stale generated artifact\n")
    calls: list[str] = []

    def synchronize(repository: Path, *, check: bool) -> None:
        generated = repository / target
        if check:
            calls.append("check")
            if generated.read_text(encoding="utf-8") != expected:
                raise MechanicalClosureError("fixture generated artifact is stale")
            return
        calls.append("synchronize")
        generated.write_text(expected, encoding="utf-8")

    def graph(*args: object, **kwargs: object) -> None:
        calls.append("graph")

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", graph)

    result = close_projected_relocation(changes, manifest, synchronize=synchronize)

    assert calls == ["synchronize", "check", "graph"]
    assert result.validation_results == (
        "blueprint synchronizer synchronize",
        "blueprint synchronizer check",
        "repository blueprint graph",
    )
    assert changes.read_text(target) == expected


def test_shadow_excludes_assistant_tooling_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assistant metadata trees are absent even when they contain a bad symlink."""

    changes, manifest = _closure_fixture(tmp_path, assistant_tooling=True)
    assert ".codex/agents" not in changes.projected_files()

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Assert that excluded assistant tooling never reaches the shadow."""

        shadow = Path(str(kwargs["cwd"]))
        assert not (shadow / ".agents").exists()
        assert not (shadow / ".codex").exists()
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    close_projected_relocation(changes, manifest)


def _pass_graph(*args: object, **kwargs: object) -> object:
    """Stand in for the canonical loader when graph data is irrelevant to a test."""

    return object()


def _sync_without_writes(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Model a successful canonical synchronizer invocation without generated output."""

    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def test_readme_only_officina_catalog_initializers_join_certification_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest catalog initializer adds one sorted certification-basis entry."""

    changes, manifest = _closure_fixture(tmp_path)
    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", _sync_without_writes)

    result = close_projected_relocation(changes, manifest)

    basis = json.loads(
        changes.read_text("references/certification/certification-basis-roots.json")
    )
    assert basis == ["src/officina/__init__.py", "src/officina/catalog/__init__.py"]
    assert result.certification_basis_changes == (
        "references/certification/certification-basis-roots.json",
    )


def test_substantive_initializer_is_not_added_to_certification_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Executable package code is rejected instead of receiving certification trust."""

    changes, manifest = _closure_fixture(tmp_path, "VALUE = 1\n")
    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", _sync_without_writes)

    with pytest.raises(
        MechanicalClosureError,
        match=r"src/officina/catalog/__init__\.py: certification basis requires a README-only initializer",
    ):
        close_projected_relocation(changes, manifest)


def test_shadow_contains_projection_without_mutating_real_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shadow synchronization sees projected bytes while the source fixture remains unchanged."""

    changes, manifest = _closure_fixture(tmp_path)
    observed: list[str] = []

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Record the projected catalog initializer visible to the synchronizer."""

        shadow = Path(str(kwargs["cwd"]))
        observed.append((shadow / "src/officina/catalog/__init__.py").read_text(encoding="utf-8"))
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    close_projected_relocation(changes, manifest)

    assert observed == ['"""Catalog package."""\n', '"""Catalog package."""\n']
    assert not (tmp_path / "src/officina/catalog/__init__.py").exists()


def test_syncer_generated_bytes_are_reconciled_into_change_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowed synchronizer output becomes an in-memory projected write."""

    changes, manifest = _closure_fixture(tmp_path, skill_content="# Demo\n")
    calls = 0

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Write an allowed generated artifact only during the sync pass."""

        nonlocal calls
        calls += 1
        if "--check" not in args[0]:
            shadow = Path(str(kwargs["cwd"]))
            (shadow / "skills/demo/SKILL.md").write_text(
                "# Demo\n<!-- BEGIN BLUEPRINT CONTRACT -->\nnew\n<!-- END BLUEPRINT CONTRACT -->\n",
                encoding="utf-8",
            )
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    result = close_projected_relocation(changes, manifest)

    assert calls == 2
    assert changes.read_text("skills/demo/SKILL.md") == (
        "# Demo\n<!-- BEGIN BLUEPRINT CONTRACT -->\nnew\n<!-- END BLUEPRINT CONTRACT -->\n"
    )
    assert result.generated_artifact_changes == ("skills/demo/SKILL.md",)


def test_unexpected_shadow_write_is_rejected_with_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synchronizer write outside the generated-output allowlist aborts closure."""

    changes, manifest = _closure_fixture(tmp_path)

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Write one unapproved shadow path during the synchronization pass."""

        if "--check" not in args[0]:
            shadow = Path(str(kwargs["cwd"]))
            (shadow / "unapproved.txt").write_text("unexpected\n", encoding="utf-8")
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    with pytest.raises(MechanicalClosureError, match=r"unexpected shadow write: unapproved\.txt"):
        close_projected_relocation(changes, manifest)


def test_excluded_synchronize_write_is_rejected_with_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synchronizer cannot hide a generated cache artifact from reconciliation."""

    changes, manifest = _closure_fixture(tmp_path)

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Write an excluded cache file during the synchronization action."""

        if "--check" not in args[0]:
            shadow = Path(str(kwargs["cwd"]))
            (shadow / ".git").write_text("unexpected\n", encoding="utf-8")
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    with pytest.raises(
        MechanicalClosureError,
        match=r"unexpected shadow write: \.git",
    ):
        close_projected_relocation(changes, manifest)


def test_graph_failure_leaves_real_repository_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph failure after shadow synchronization cannot alter source bytes."""

    changes, manifest = _closure_fixture(tmp_path)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    def fail_graph(*args: object, **kwargs: object) -> object:
        """Raise the canonical graph error used to prove shadow-only failure."""

        raise ValueError("invalid graph")

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", fail_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", _sync_without_writes)

    with pytest.raises(MechanicalClosureError, match=r"repository graph validation failed: invalid graph"):
        close_projected_relocation(changes, manifest)

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_excluded_synchronize_directory_is_rejected_with_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synchronizer cannot hide a new excluded directory from reconciliation."""

    changes, manifest = _closure_fixture(tmp_path)

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Create one excluded output directory during synchronization."""

        if "--check" not in args[0]:
            shadow = Path(str(kwargs["cwd"]))
            (shadow / "_build").mkdir()
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    with pytest.raises(MechanicalClosureError, match=r"unexpected shadow write: _build"):
        close_projected_relocation(changes, manifest)


def test_plain_mechanics_fixture_skips_closure_without_canonical_markers(
    tmp_path: Path,
) -> None:
    """A narrow fixture with neither canonical marker needs no shadow closure."""

    changes = ChangeSet(tmp_path)

    result = close_projected_relocation(changes, RelocationManifest())

    assert result.validation_results == ()


def test_partial_canonical_marker_requires_the_missing_closure_input(
    tmp_path: Path,
) -> None:
    """A partial Officina fixture cannot silently skip a required closure step."""

    _write(tmp_path / "references/blueprint/schema.json", "{}\n")
    changes = ChangeSet(tmp_path)

    with pytest.raises(
        MechanicalClosureError,
        match=r"missing closure input: references/certification/certification-basis-roots\.json",
    ):
        close_projected_relocation(changes, RelocationManifest())


@pytest.mark.parametrize(
    "module_schema_directory",
    [False, True],
    ids=["missing", "directory"],
)
def test_partial_schema_cannot_fall_back_to_the_live_imported_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_schema_directory: bool,
) -> None:
    """A shadow graph load requires a schema file before imported code runs."""

    changes, manifest = _closure_fixture(
        tmp_path,
        include_module_schema=False,
        module_schema_directory=module_schema_directory,
    )

    def graph_must_not_run(*args: object, **kwargs: object) -> object:
        """Fail if an incomplete shadow reaches the imported graph loader."""

        pytest.fail("graph loader must not run without the shadow module schema")

    monkeypatch.setattr(
        closure_module,
        "load_repository_blueprint_graph",
        graph_must_not_run,
    )
    monkeypatch.setattr(closure_module.subprocess, "run", _sync_without_writes)

    with pytest.raises(
        MechanicalClosureError,
        match=r"missing closure input: references/blueprint/module\.schema\.json",
    ):
        close_projected_relocation(changes, manifest)


def test_mode_only_generated_artifact_change_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synchronizer may not change a generated artifact mode without its bytes."""

    changes, manifest = _closure_fixture(tmp_path)

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Change only the shadow skill mode during the synchronization pass."""

        if "--check" not in args[0]:
            shadow = Path(str(kwargs["cwd"]))
            (shadow / "skills/demo/SKILL.md").chmod(0o755)
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    with pytest.raises(
        MechanicalClosureError,
        match=r"unexpected shadow mode change: skills/demo/SKILL\.md",
    ):
        close_projected_relocation(changes, manifest)


def test_empty_generated_file_replaced_by_directory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synchronizer cannot exchange an empty generated file for a directory."""

    changes, manifest = _closure_fixture(tmp_path, skill_content="")

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Replace one same-mode empty generated file with a directory."""

        if "--check" not in args[0]:
            target = Path(str(kwargs["cwd"])) / "skills/demo/SKILL.md"
            target.unlink()
            target.mkdir()
            target.chmod(0o644)
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    with pytest.raises(
        MechanicalClosureError,
        match=r"unexpected shadow kind change: skills/demo/SKILL\.md",
    ):
        close_projected_relocation(changes, manifest)


def test_plan_absorbs_calculated_closure_categories_into_its_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The relocation report exposes the coordinator's calculated result fields."""

    _write(tmp_path / "plain.py", "VALUE = 1\n")
    result = MechanicalClosureResult(
        certification_basis_changes=("references/certification/certification-basis-roots.json",),
        generated_artifact_changes=("references/blueprint/runtime_dependencies.json",),
        validation_results=("repository blueprint graph",),
    )
    monkeypatch.setattr(
        closure_module,
        "close_projected_relocation",
        lambda changes, manifest, *, synchronize: result,
    )

    report = plan_relocation(tmp_path, RelocationManifest()).report()

    assert report["certification_basis_changes"] == [
        "references/certification/certification-basis-roots.json"
    ]
    assert report["generated_artifact_changes"] == [
        "references/blueprint/runtime_dependencies.json"
    ]
    assert report["validation_results"] == ["repository blueprint graph"]


def test_check_synchronizer_write_is_rejected_with_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check action cannot hide a write after generated output was reconciled."""

    changes, manifest = _closure_fixture(tmp_path)

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Write one path only while the synchronizer claims to check."""

        if "--check" in args[0]:
            shadow = Path(str(kwargs["cwd"]))
            (shadow / "check-write.txt").write_text("unexpected\n", encoding="utf-8")
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    with pytest.raises(
        MechanicalClosureError,
        match=r"blueprint synchronizer check changed shadow: check-write\.txt",
    ):
        close_projected_relocation(changes, manifest)


def test_excluded_check_write_is_rejected_with_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check action cannot hide an excluded assistant-tooling artifact."""

    changes, manifest = _closure_fixture(tmp_path)

    def sync(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Write an excluded assistant artifact only during the check action."""

        if "--check" in args[0]:
            shadow = Path(str(kwargs["cwd"]))
            (shadow / ".agents").write_text("unexpected\n", encoding="utf-8")
        return _sync_without_writes(*args, **kwargs)

    monkeypatch.setattr(closure_module, "load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr(closure_module.subprocess, "run", sync)

    with pytest.raises(
        MechanicalClosureError,
        match=r"blueprint synchronizer check changed shadow: \.agents",
    ):
        close_projected_relocation(changes, manifest)


def _extractor_acceptance_manifest() -> RelocationManifest:
    """Build the narrow declarations needed for one complete source transfer."""

    return RelocationManifest(
        moves=(
            Move(
                "src/officina/common/standard_extractor.py",
                "src/officina/standards/extractor.py",
            ),
            Move(
                "src/officina/common/blueprints/standard-extractor.yaml",
                "src/officina/standards/blueprints/extractor.yaml",
            ),
        ),
        renames={
            "python_modules": (
                Rename(
                    "officina.common.standard_extractor",
                    "officina.standards.extractor",
                ),
            ),
            "source_ids": (
                Rename(
                    "common.source.standard-extractor",
                    "standards.source.extractor",
                ),
            ),
            "interface_ids": (
                Rename(
                    "common.source.standard-extractor.interface.python-api",
                    "standards.source.extractor.interface.python-api",
                ),
                Rename(
                    "common.interface.standard-extractor",
                    "standards.interface.extractor",
                ),
            ),
        },
        blueprint_documents=(
            (
                "src/officina/standards/blueprint.yaml",
                {
                    "authority": {"owns_filesystem": []},
                    "children": {},
                    "content": [r"__init__\.py"],
                    "description": (
                        "Pinned-standard closure validation and deterministic "
                        "standard queries."
                    ),
                    "exports": {},
                    "gateway": {"language": "Python", "path": "__init__.py"},
                    "id": "standards",
                    "namespace_exports": {},
                    "node_type": "module",
                    "maturity": "stable",
                    "schema_version": 6,
                    "sources": {},
                    "version": 1,
                },
            ),
        ),
        ownership_transfers=(
            OwnershipTransfer(
                from_blueprint="src/officina/common/blueprint.yaml",
                to_blueprint="src/officina/standards/blueprint.yaml",
                source=Rename(
                    "common.source.standard-extractor",
                    "standards.source.extractor",
                ),
                export=Rename(
                    "common.interface.standard-extractor",
                    "standards.interface.extractor",
                ),
                content=Rename(r"standard_extractor\.py", r"extractor\.py"),
            ),
        ),
        exact_rewrites=(
            ExactRewrite(
                "src/officina/standards/blueprints/extractor.yaml",
                r"standard_extractor\.py",
                r"extractor\.py",
                count=2,
            ),
            ExactRewrite(
                "src/officina/standards/blueprints/extractor.yaml",
                "path: standard_extractor.py",
                "path: extractor.py",
            ),
        ),
        package_catalogs=(
            PackageCatalog(
                path="src/officina/standards",
                summary="Pinned-standard extraction and querying.",
                description=(
                    "This package validates standard import closures and exposes "
                    "deterministic queries over them. Callers import concrete "
                    "owning modules."
                ),
                roles={
                    "extractor.py": (
                        "Resolves a standard and its pinned import closure into "
                        "one validated view."
                    ),
                    "query.py": (
                        "Answers deterministic task and requirements queries over "
                        "extracted standards."
                    ),
                },
            ),
        ),
        package_boundaries=(
            PackageBoundary(
                path="src/officina/standards",
                disposition="registered-module",
                module_id="standards",
                blueprint="src/officina/standards/blueprint.yaml",
            ),
        ),
    )


def _write_extractor_acceptance_fixture(tmp_path: Path) -> dict[str, bytes]:
    """Create only the canonical inputs for one real Officina extractor relocation."""

    for schema in (REPO_ROOT / "references/blueprint").glob("*.json"):
        destination = tmp_path / "references/blueprint" / schema.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _write(destination, schema.read_text(encoding="utf-8"))
    _write(
        tmp_path / "references/blueprint/config.yaml",
        (REPO_ROOT / "references/blueprint/config.yaml").read_text(encoding="utf-8"),
    )
    _write(tmp_path / "src/officina/__init__.py", 'ORIGIN = "shadow"\n')
    _write(tmp_path / "src/officina/common/__init__.py", '"""Common fixture."""\n')
    _write(tmp_path / "src/officina/common/standard_extractor.py", "VALUE = 1\n")
    _write(
        tmp_path / "src/officina/common/blueprint.yaml",
        yaml.safe_dump(
            {
                "authority": {"owns_filesystem": []},
                "children": {},
                "content": [r"__init__\.py", r"standard_extractor\.py"],
                "description": "Fixture source owner.",
                "exports": {
                    "common.interface.standard-extractor": {
                        "access": {"allow_all_modules": True, "allowed_callers": []},
                        "source_interface": "common.source.standard-extractor.interface.python-api",
                    }
                },
                "gateway": {"language": "Python", "path": "__init__.py"},
                "id": "common",
                "namespace_exports": {},
                "node_type": "module",
                "maturity": "stable",
                "schema_version": 6,
                "sources": {
                    "common.source.standard-extractor": {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/standard-extractor.yaml",
                        }
                    }
                },
                "version": 1,
            },
            sort_keys=False,
        ),
    )
    _write(
        tmp_path / "src/officina/common/blueprints/standard-extractor.yaml",
        yaml.safe_dump(
            {
                "content": [r"standard_extractor\.py"],
                "dependencies": [],
                "description": "Fixture extractor source.",
                "gateway": {"language": "Python", "path": "standard_extractor.py"},
                "id": "common.source.standard-extractor",
                "interfaces": {
                    "common.source.standard-extractor.interface.python-api": {
                        "version": 1,
                        "content": [r"standard_extractor\.py"],
                        "uses_interfaces": [],
                    }
                },
                "node_type": "behavioral_source",
                "maturity": "stable",
                "schema_version": 6,
                "uses_interfaces": [],
                "version": 1,
            },
            sort_keys=False,
        ),
    )
    _write(
        tmp_path / "consumer.py",
        "from officina.common.standard_extractor import VALUE\n",
    )
    _write(tmp_path / "unrelated-dirty.md", "do not relocate\n")
    _write(
        tmp_path / "references/certification/certification-basis-roots.json",
        json.dumps(["src/officina/__init__.py"], indent=2) + "\n",
    )
    _write(
        tmp_path / "references/blueprint/runtime_dependencies.json",
        '{"stale": true}\n',
    )
    return {
        relative: (tmp_path / relative).read_bytes()
        for relative in (
            "src/officina/common/standard_extractor.py",
            "src/officina/common/blueprints/standard-extractor.yaml",
            "src/officina/common/blueprint.yaml",
            "consumer.py",
            "unrelated-dirty.md",
            "references/certification/certification-basis-roots.json",
            "references/blueprint/runtime_dependencies.json",
        )
    }


def test_one_preflight_closes_real_extractor_relocation_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """One v2 preflight carries the move, closure artifacts, and no-op rerun."""

    before = _write_extractor_acceptance_fixture(tmp_path)

    def synchronize(repository: Path, *, check: bool) -> None:
        target = repository / "references/blueprint/runtime_dependencies.json"
        expected = _empty_runtime_dependencies()
        if not check:
            target.write_text(expected, encoding="utf-8")

    changes = plan_relocation(
        tmp_path,
        _extractor_acceptance_manifest(),
        synchronize=synchronize,
    )

    assert {
        relative: (tmp_path / relative).read_bytes() for relative in before
    } == before
    assert not (tmp_path / "src/officina/standards/extractor.py").exists()
    assert changes.report()["moves"] == [
        {
            "from": "src/officina/common/blueprints/standard-extractor.yaml",
            "to": "src/officina/standards/blueprints/extractor.yaml",
        },
        {
            "from": "src/officina/common/standard_extractor.py",
            "to": "src/officina/standards/extractor.py",
        },
    ]
    assert changes.read_text("consumer.py") == "from officina.standards.extractor import VALUE\n"
    standards = yaml.safe_load(changes.read_text("src/officina/standards/blueprint.yaml"))
    extractor = yaml.safe_load(
        changes.read_text("src/officina/standards/blueprints/extractor.yaml")
    )
    assert standards["sources"] == {
        "standards.source.extractor": {
            "blueprint": {"base": "module-root", "path": "blueprints/extractor.yaml"}
        }
    }
    assert extractor["id"] == "standards.source.extractor"
    assert extractor["gateway"]["path"] == "extractor.py"
    assert extractor["dependencies"] == []
    assert "common.source.standard-extractor" not in json.dumps(standards)
    assert "standard_extractor.py" not in json.dumps(extractor)
    assert json.loads(
        changes.read_text("references/certification/certification-basis-roots.json")
    ) == ["src/officina/__init__.py", "src/officina/standards/__init__.py"]
    runtime_dependencies = json.loads(
        changes.read_text("references/blueprint/runtime_dependencies.json")
    )
    assert json.dumps(runtime_dependencies, indent=2) + "\n" == _empty_runtime_dependencies()
    assert changes.report()["certification_basis_changes"] == [
        "references/certification/certification-basis-roots.json"
    ]
    assert changes.report()["generated_artifact_changes"] == [
        "references/blueprint/runtime_dependencies.json"
    ]
    assert changes.report()["validation_results"] == [
        "blueprint synchronizer check",
        "blueprint synchronizer synchronize",
        "repository blueprint graph",
    ]
    assert (tmp_path / "unrelated-dirty.md").read_bytes() == before["unrelated-dirty.md"]

    apply_change_set(changes)

    assert not (tmp_path / "src/officina/common/standard_extractor.py").exists()
    assert not (
        tmp_path / "src/officina/common/blueprints/standard-extractor.yaml"
    ).exists()
    assert (tmp_path / "src/officina/standards/extractor.py").is_file()
    assert (tmp_path / "src/officina/standards/blueprints/extractor.yaml").is_file()
    assert (tmp_path / "unrelated-dirty.md").read_bytes() == before["unrelated-dirty.md"]

    second = plan_relocation(
        tmp_path,
        _extractor_acceptance_manifest(),
        synchronize=synchronize,
    ).report()

    for category in (
        "moves",
        "writes",
        "deletes",
        "certification_basis_changes",
        "generated_artifact_changes",
    ):
        assert second[category] == []
