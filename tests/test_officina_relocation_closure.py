"""Focused behavioral tests for deterministic relocation closure."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from officina.refactor.closure import (
    MechanicalClosureError,
    MechanicalClosureResult,
    close_projected_relocation,
)
from officina.refactor.relocation import (
    ChangeSet,
    PackageCatalog,
    RelocationManifest,
    plan_relocation,
)


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
    syncer_content: str = '"""Fixture synchronizer."""\n',
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
        tmp_path / "skills/skill-maker/_rtx/_blueprint_syncer.py",
        syncer_content,
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

    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", sync)

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
    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", _sync_without_writes)

    with pytest.raises(MechanicalClosureError, match=r"unsafe shadow symlink: AGENTS\.md"):
        close_projected_relocation(changes, manifest)


def test_synchronizer_imports_officina_from_shadow_src(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copied synchronizer imports the shadow package before the live checkout."""

    changes, manifest = _closure_fixture(
        tmp_path,
        officina_content='ORIGIN = "shadow"\n',
        syncer_content=(
            "import officina\n"
            'if officina.ORIGIN != "shadow":\n'
            "    raise SystemExit(7)\n"
        ),
    )
    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)

    result = close_projected_relocation(changes, manifest)

    assert result.validation_results == (
        "blueprint synchronizer synchronize",
        "blueprint synchronizer check",
        "repository blueprint graph",
    )


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

    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", sync)

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
    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", _sync_without_writes)

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
    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", _sync_without_writes)

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

    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", sync)

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

    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", sync)

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

    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", sync)

    with pytest.raises(MechanicalClosureError, match=r"unexpected shadow write: unapproved\.txt"):
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

    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", fail_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", _sync_without_writes)

    with pytest.raises(MechanicalClosureError, match=r"repository graph validation failed: invalid graph"):
        close_projected_relocation(changes, manifest)

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


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
        match=r"missing closure input: skills/skill-maker/_rtx/_blueprint_syncer\.py",
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
        "officina.refactor.closure.load_repository_blueprint_graph",
        graph_must_not_run,
    )
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", _sync_without_writes)

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

    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", sync)

    with pytest.raises(
        MechanicalClosureError,
        match=r"unexpected shadow mode change: skills/demo/SKILL\.md",
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
        "officina.refactor.closure.close_projected_relocation",
        lambda changes, manifest: result,
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

    monkeypatch.setattr("officina.refactor.closure.load_repository_blueprint_graph", _pass_graph)
    monkeypatch.setattr("officina.refactor.closure.subprocess.run", sync)

    with pytest.raises(
        MechanicalClosureError,
        match=r"blueprint synchronizer check changed shadow: check-write\.txt",
    ):
        close_projected_relocation(changes, manifest)
