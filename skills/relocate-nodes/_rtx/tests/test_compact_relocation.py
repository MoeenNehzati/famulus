from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from test_support.git_repository import GitTestRepository
from .. import _compact_relocation as relocation
from .._compact_relocation import RelocationError, apply, build_packet, plan


REPO_ROOT = Path(__file__).resolve().parents[4]
RUNTIME_PATH = Path(__file__).resolve().parents[1] / "_compact_relocation.py"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(root: Path) -> dict[str, object]:
    _write(root / "officina.toml", 'schema_version = 1\n[modules]\nroots = ["skills"]\n')
    _write(
        root / "skills/old-node/blueprint.yaml",
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "old-node",
                "children": {},
                "sources": {},
                "exports": {},
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "skills/old-node/tool.py",
        "from old_node.helper import value\nfrom old_node_extra import untouched\n",
    )
    _write(
        root / "skills/consumer/blueprint.yaml",
        "schema_version: 6\nnode_type: module\nid: consumer\nchildren: {}\n"
        "sources: {}\nexports: {}\ndependencies: [old-node]\n",
    )
    _write(root / "notes.md", "Use old-node when demonstrating relocation.\n")
    return {
        "schema_version": 3,
        "relocations": [{"from": "skills/old-node", "to": "skills/new-node"}],
        "python_modules": [{"from": "old_node", "to": "new_node"}],
    }


def _public_fixture(root: Path, manifest_path: Path) -> dict[str, object]:
    _write(root / "officina.toml", 'schema_version = 1\n[modules]\nroots = ["skills"]\n')
    _write(root / "skills/old-node/__init__.py", "VALUE = 1\n")
    _write(
        root / "skills/old-node/blueprint.yaml",
        """schema_version: 6
node_type: module
id: old-node
version: 1
maturity: stable
gateway: {path: __init__.py, language: Python}
content: [__init__\\.py]
authority: {owns_filesystem: []}
sources: {}
children: {}
namespace_exports: {}
exports: {}
""",
    )
    manifest = {
        "schema_version": 3,
        "relocations": [{"from": "skills/old-node", "to": "skills/new-node"}],
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return manifest


def _dispatcher_env() -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str(REPO_ROOT / "src")
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not inherited else os.pathsep.join((source_root, inherited))
    )
    return environment


def _run_public_route(
    root: Path, manifest: Path, report: Path, *, publish: bool = False
) -> subprocess.CompletedProcess[str]:
    arguments = [
        sys.executable,
        "-m",
        "officina.dispatcher.cli",
        "--repository-config",
        str(REPO_ROOT / "officina.toml"),
        "--caller-skill",
        "relocate-nodes",
        "relocate-nodes._rtx.interface.relocate@2",
        "--root",
        str(root),
        "--manifest",
        str(manifest),
        "--report",
        str(report),
    ]
    if publish:
        arguments.append("--apply")
    return subprocess.run(
        arguments,
        cwd=REPO_ROOT,
        env=_dispatcher_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=30,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_skill_relocation_rewrites_through_generated_interface_block() -> None:
    payload = (b"Legacy old-node.\n<!-- BEGIN BLUEPRINT INTERFACES -->\n"
               b"`old-node.interface.run@1`\n<!-- END BLUEPRINT INTERFACES -->\nBody old-node.\n")
    updated = relocation._mechanical(
        "skills/old-node/SKILL.md", payload, [("old-node", "new-node")], []
    )
    expected = (b"Legacy new-node.\n<!-- BEGIN BLUEPRINT INTERFACES -->\n"
                b"`new-node.interface.run@1`\n<!-- END BLUEPRINT INTERFACES -->\nBody old-node.\n")
    assert updated == expected


def test_plan_mechanically_rewrites_blueprints_and_python_only(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    manifest["inventory_exclusions"] = [".claude", ".codex", ".superpowers"]
    consumer = tmp_path / "skills/consumer/blueprint.yaml"
    consumer.write_text(
        "# old-node historical comment\n" + consumer.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write(tmp_path / ".claude/log.md", "old-node\n")
    _write(tmp_path / ".codex/log.md", "old-node\n")
    _write(tmp_path / ".superpowers/log.md", "old-node\n")
    (tmp_path / "AGENTS.md").symlink_to("README.md")

    recipe = plan(tmp_path, manifest)

    assert "skills/new-node/blueprint.yaml" in recipe.writes
    assert "skills/old-node/blueprint.yaml" in recipe.deletes
    assert b"id: new-node" in recipe.writes["skills/new-node/blueprint.yaml"]
    assert b"dependencies: [new-node]" in recipe.writes[
        "skills/consumer/blueprint.yaml"
    ]
    assert b"# old-node historical comment" in recipe.writes[
        "skills/consumer/blueprint.yaml"
    ]
    assert b"from new_node.helper" in recipe.writes["skills/new-node/tool.py"]
    assert b"from old_node_extra" in recipe.writes["skills/new-node/tool.py"]
    assert [item.path for item in recipe.occurrences] == [
        "notes.md",
        "skills/consumer/blueprint.yaml",
    ]
    assert not any(
        item.path.startswith((".claude/", ".codex/", ".superpowers/"))
        for item in recipe.occurrences
    )
    assert "AGENTS.md" not in recipe.writes
    json.dumps(recipe.report())


def test_reviewed_recipe_applies_once_and_has_empty_postflight(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    first = plan(tmp_path, manifest)
    occurrence = first.occurrences[0]
    manifest["semantic_decisions"] = [
        {
            "occurrence_id": occurrence.occurrence_id,
            "disposition": "rewrite",
            "replacement": "new-node",
        }
    ]
    reviewed = plan(tmp_path, manifest)

    apply(reviewed, verify=lambda: None)

    assert not (tmp_path / "skills/old-node").exists()
    assert (tmp_path / "skills/new-node").is_dir()
    assert (tmp_path / "notes.md").read_text() == (
        "Use new-node when demonstrating relocation.\n"
    )
    assert plan(tmp_path, manifest).empty


def test_compact_dispositions_and_supplemental_edit_apply_once(
    tmp_path: Path,
) -> None:
    manifest = _fixture(tmp_path)
    consumer = tmp_path / "skills/consumer/blueprint.yaml"
    consumer.write_text(
        "# old-node historical comment\n" + consumer.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write(tmp_path / "tests/test_error.py", 'pattern = "certifier"\n')
    manifest["default_disposition"] = "rewrite"
    manifest["disposition_overrides"] = [
        {"path": "notes.md", "disposition": "preserve"}
    ]
    manifest["supplemental_edits"] = [
        {
            "path": "tests/test_error.py",
            "expected": 'pattern = "certifier"',
            "replacement": 'pattern = "node-certify"',
        }
    ]

    recipe = plan(tmp_path, manifest)

    assert recipe.occurrences == []
    assert "notes.md" not in recipe.writes
    assert b"# new-node historical comment" in recipe.writes[
        "skills/consumer/blueprint.yaml"
    ]
    assert recipe.writes["tests/test_error.py"] == b'pattern = "node-certify"\n'

    apply(recipe, verify=lambda: None)

    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == (
        "Use old-node when demonstrating relocation.\n"
    )
    assert consumer.read_text(encoding="utf-8").startswith(
        "# new-node historical comment\n"
    )
    assert (tmp_path / "tests/test_error.py").read_bytes() == (
        b'pattern = "node-certify"\n'
    )
    assert plan(tmp_path, manifest).empty


def test_failed_verification_rolls_back_every_change(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    source_directory = tmp_path / "skills/old-node"
    source_directory.chmod(0o750)
    unrelated_empty = tmp_path / "keep-empty"
    unrelated_empty.mkdir()
    recipe = plan(tmp_path, manifest)
    manifest["semantic_decisions"] = [
        {
            "occurrence_id": recipe.occurrences[0].occurrence_id,
            "disposition": "preserve",
        }
    ]
    reviewed = plan(tmp_path, manifest)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(RelocationError, match="postflight"):
        apply(
            reviewed,
            verify=lambda: (_ for _ in ()).throw(RelocationError("postflight")),
        )

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert source_directory.stat().st_mode & 0o777 == 0o750
    assert unrelated_empty.is_dir()


def test_review_packet_renderer_emits_every_unit_without_llm_reformatting(
    tmp_path: Path,
) -> None:
    manifest = _fixture(tmp_path)
    _write(tmp_path / "notes.md", "# Current\nUse old-node.\n")
    _write(tmp_path / "guide.md", "# Guide\nPrefer old-node.\n")
    packet = build_packet(tmp_path, plan(tmp_path, manifest).report())

    rendered = relocation.render_packet(packet)

    assert packet["summary"] == {"occurrences": 2, "review_units": 2}
    assert [unit["path"] for unit in packet["review_units"]] == [
        "guide.md",
        "notes.md",
    ]
    assert [unit["section"] for unit in packet["review_units"]] == [
        "Guide",
        "Current",
    ]
    assert [unit["suggestion"] for unit in packet["review_units"]] == [
        "rewrite",
        "rewrite",
    ]
    assert [unit["decision"] for unit in packet["review_units"]] == [None, None]
    assert "2 occurrences in 2 review units" in rendered
    assert "`guide.md` — Guide — suggested `rewrite` — 1 occurrence" in rendered
    assert "`notes.md` — Current — suggested `rewrite` — 1 occurrence" in rendered
    assert rendered.count("`old-node` → `new-node` ×1") == 2


def test_plan_rejects_existing_destination(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    _write(tmp_path / "skills/new-node/owned.txt", "collision\n")

    with pytest.raises(RelocationError, match="destination already exists"):
        plan(tmp_path, manifest)


def test_mid_publication_failure_restores_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _fixture(tmp_path)
    first = plan(tmp_path, manifest)
    manifest["semantic_decisions"] = [
        {"occurrence_id": first.occurrences[0].occurrence_id, "disposition": "preserve"}
    ]
    recipe = plan(tmp_path, manifest)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    actual = relocation.atomic_replace_bytes
    writes = 0

    def fail_second_repository_write(path: Path, data: bytes, **options: object) -> None:
        nonlocal writes
        if options.get("allowed_root") == tmp_path:
            writes += 1
            if writes == 2:
                raise OSError("injected publication failure")
        actual(path, data, **options)

    monkeypatch.setattr(relocation, "atomic_replace_bytes", fail_second_repository_write)
    with pytest.raises(OSError, match="injected publication failure"):
        apply(recipe, verify=lambda: None)

    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    } == before


def test_stale_preflight_causes_zero_writes(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    first = plan(tmp_path, manifest)
    manifest["semantic_decisions"] = [
        {"occurrence_id": first.occurrences[0].occurrence_id, "disposition": "preserve"}
    ]
    recipe = plan(tmp_path, manifest)
    changed = tmp_path / "skills/old-node/tool.py"
    changed.write_text("changed after preflight\n", encoding="utf-8")

    with pytest.raises(RelocationError, match="changed after preflight"):
        apply(recipe, verify=lambda: None)

    assert not (tmp_path / "skills/new-node").exists()
    assert changed.read_text(encoding="utf-8") == "changed after preflight\n"


def test_next_invocation_cleans_pre_marker_interruption(tmp_path: Path) -> None:
    _fixture(tmp_path)
    _base, _lock, state = relocation._state(tmp_path.resolve())
    state.mkdir(mode=0o700)

    assert relocation.recover(tmp_path) is False
    assert not state.exists()


def test_public_route_applies_and_then_reports_empty_postflight(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    manifest_path = tmp_path / "manifest.yaml"
    apply_report = tmp_path / "apply-report.json"
    git = GitTestRepository.create(repository)
    manifest = _public_fixture(repository, manifest_path)
    _write(repository / ".gitignore", "_build/\n")
    git.git("add", ".")
    git.git("commit", "--quiet", "-m", "fixture")
    ignored_state = repository / "skills/old-node/_build/state.json"
    _write(ignored_state, "{}\n")

    published = _run_public_route(
        repository, manifest_path, apply_report, publish=True
    )

    assert published.returncode == 0, published.stderr
    assert not (repository / "skills/old-node/__init__.py").exists()
    assert not (repository / "skills/old-node/blueprint.yaml").exists()
    assert ignored_state.read_text(encoding="utf-8") == "{}\n"
    assert (repository / "skills/new-node/__init__.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 1\n"
    apply_timings = json.loads(apply_report.read_text(encoding="utf-8"))["timings"]
    assert set(apply_timings) == {
        "graph_verification_seconds",
        "planning_seconds",
        "postflight_seconds",
        "total_seconds",
        "transactional_writes_seconds",
    }
    assert all(value >= 0 for value in apply_timings.values())
    assert plan(repository, manifest, recover_interrupted=False).empty


def test_public_route_recovers_after_process_exit_mid_publication(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    manifest_path = tmp_path / "manifest.yaml"
    recovery_report = tmp_path / "recovery-report.json"
    _public_fixture(repository, manifest_path)
    baseline = _snapshot(repository)
    crash_probe = """
import importlib.util
import os
from pathlib import Path
import sys
import yaml

runtime_path, root_path, manifest_path, source_root = sys.argv[1:]
sys.path.insert(0, source_root)
spec = importlib.util.spec_from_file_location("relocation_crash_probe", runtime_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
root = Path(root_path).resolve()
manifest = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
recipe = module.plan(root, manifest)
actual_replace = module.atomic_replace_bytes

def replace_then_exit(path, data, **options):
    actual_replace(path, data, **options)
    if Path(options["allowed_root"]).resolve() == root:
        os._exit(73)

module.atomic_replace_bytes = replace_then_exit
module.apply(recipe, verify=lambda: None)
"""

    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            crash_probe,
            str(RUNTIME_PATH),
            str(repository),
            str(manifest_path),
            str(REPO_ROOT / "src"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert crashed.returncode == 73
    assert _snapshot(repository) != baseline
    recovered = _run_public_route(repository, manifest_path, recovery_report)
    assert recovered.returncode == 0, recovered.stderr
    assert _snapshot(repository) == baseline
    report = json.loads(recovery_report.read_text(encoding="utf-8"))
    assert report["writes"] == [
        "skills/new-node/__init__.py",
        "skills/new-node/blueprint.yaml",
    ]
    assert report["deletes"] == [
        "skills/old-node/__init__.py",
        "skills/old-node/blueprint.yaml",
    ]
