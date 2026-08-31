"""Tests for validators/platform_neutral.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators import platform_neutral as module_under_test  # noqa: E402
from validators.platform_neutral import validate  # noqa: E402


def _validate_text(tmp_path: Path, monkeypatch) -> list[str]:
    monkeypatch.setattr(
        module_under_test,
        "_git_ignored_paths",
        lambda _repo_root: frozenset(),
    )
    return module_under_test._validate(tmp_path, frozenset())


def test_scanned_path_is_relativized_once(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "skills" / "demo" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("first\nsecond\nthird\n", encoding="utf-8")
    original_relative_to = Path.relative_to
    calls = 0

    def counting_relative_to(path, other, *args, **kwargs):
        nonlocal calls
        if path == source and other == tmp_path.resolve():
            calls += 1
        return original_relative_to(path, other, *args, **kwargs)

    monkeypatch.setattr(Path, "relative_to", counting_relative_to)
    monkeypatch.setattr(
        module_under_test,
        "_git_ignored_paths",
        lambda _repo_root: frozenset(),
    )

    assert module_under_test._validate(tmp_path, frozenset()) == []
    assert calls == 1


def test_standalone_validate_keeps_relative_finding_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root_file = tmp_path / "CLAUDE.md"
    root_file.write_text("Use Codex here.\n", encoding="utf-8")
    monkeypatch.setattr(
        module_under_test,
        "_canonical_blueprint_paths",
        lambda _repo_root: frozenset(),
    )
    monkeypatch.setattr(
        module_under_test,
        "_git_ignored_paths",
        lambda _repo_root: frozenset(),
    )

    assert validate(tmp_path) == ["CLAUDE.md:1: Use Codex here."]


def test_text_scan_reports_governed_content_and_skips_exclusions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Use Claude.\nAlso codex.\n")
    tests = skill / "tests"
    tests.mkdir()
    (tests / "test_something.py").write_text("# test for Claude or Codex\n")

    excluded_install = tmp_path / "skills" / "install-launchers"
    excluded_install.mkdir(parents=True)
    (excluded_install / "SKILL.md").write_text("Install Claude Code here.\n")

    references = tmp_path / "references"
    references.mkdir()
    (references / "guide.md").write_text("Use Claude Code to run this.\n")
    standards = references / "node-standards"
    standards.mkdir()
    (standards / "node.standard.yaml").write_text(
        "Use Windows, macOS, Linux, Claude, and Codex here.\n"
    )

    assert set(_validate_text(tmp_path, monkeypatch)) == {
        "skills/my-skill/SKILL.md:1: Use Claude.",
        "skills/my-skill/SKILL.md:2: Also codex.",
        "references/guide.md:1: Use Claude Code to run this.",
    }


def test_generated_skill_interface_tokens_are_ignored_but_authored_tokens_fail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = tmp_path / "skills" / "my-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    platform_line = (
        "- `dispatcher --caller-skill my-skill my-skill.interface.run "
        "<claude|codex>`"
    )
    skill.write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        f"{platform_line}\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        f"{platform_line}\n",
        encoding="utf-8",
    )

    assert _validate_text(tmp_path, monkeypatch) == [
        f"skills/my-skill/SKILL.md:4: {platform_line}"
    ]


def test_git_ignored_paths_are_not_scanned(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "skills" / "my-skill" / "runtime.log"
    source.parent.mkdir(parents=True)
    source.write_text("Use Claude here.\n", encoding="utf-8")
    monkeypatch.setattr(
        module_under_test,
        "_git_ignored_paths",
        lambda _repo_root: frozenset({Path("skills/my-skill/runtime.log")}),
    )

    assert module_under_test._validate(tmp_path, frozenset()) == []


def test_runner_rejects_non_git_repository(tmp_path: Path) -> None:
    runner = Path(__file__).resolve().parents[1] / "repo_checks.py"
    result = subprocess.run(
        [
            "python3",
            str(runner),
            "--suite",
            "validators",
            "--repo-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "cannot enumerate staged files" in result.stderr


def test_filename_exemptions_and_nearby_rejections_share_one_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "skills" / "a-skill" / "_rtx"
    runtime.mkdir(parents=True)
    allowed = {
        "claude_parser.py": "# Handles Claude Code's transcript format.\n",
        "codex_parser.py": "# Handles Codex's transcript format.\n",
        "Claude_Parser.py": "# Handles Claude Code's transcript format.\n",
        "__init__.py": "from claude_parser import ClaudeParser\nfrom codex_parser import CodexParser\n",
    }
    for name, content in allowed.items():
        (runtime / name).write_text(content)
    (runtime / "claude_bridge.py").write_text("# Also handles Codex.\n")
    (runtime / "scan.py").write_text(
        "# Scans Claude transcripts.\n# Scans Codex transcripts.\n"
        'home = os.environ.get("CLAUDE_HOME")\n'
    )

    secrets = tmp_path / "src" / "officina" / "common" / "secrets"
    secrets.mkdir(parents=True)
    for name, content in {
        "windows.py": "# Handles Windows win32 credential storage.\n",
        "osx.py": "# Handles macOS and darwin credential storage.\n",
        "linux.py": "# Handles Linux credential storage.\n",
    }.items():
        (secrets / name).write_text(content)
    generic = secrets.parent / "secret_store.py"
    generic.write_text("# Uses Windows, macOS, and Linux stores.\n")

    assert set(_validate_text(tmp_path, monkeypatch)) == {
        "skills/a-skill/_rtx/claude_bridge.py:1: # Also handles Codex.",
        "skills/a-skill/_rtx/scan.py:1: # Scans Claude transcripts.",
        "skills/a-skill/_rtx/scan.py:2: # Scans Codex transcripts.",
        'skills/a-skill/_rtx/scan.py:3: home = os.environ.get("CLAUDE_HOME")',
        "src/officina/common/secret_store.py:1: # Uses Windows, macOS, and Linux stores.",
    }


def test_exact_cross_host_allowlists_and_nearby_rejections_share_one_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    binding_paths = {
        Path("skills/relocate-nodes/_rtx/_relocation_engine.py"),
        Path("skills/dev-activation/_rtx/_development_activation.py"),
        Path("src/officina/launchers/agent.py"),
        Path("src/officina/recurring/runtime.py"),
        Path("src/officina/recurring/healthcheck.py"),
        Path("src/officina/recurring/jobs.py"),
        Path("src/officina/recurring/executor.py"),
        Path("src/officina/recurring/native.py"),
        Path("src/officina/recurring/state.py"),
        Path("src/officina/recurring/default_jobs.yaml"),
        Path("src/officina/configuration/schema.json"),
    }
    milestone_paths = {
        Path("skills/milestone-logging/_rtx/_milestone_writer.py"),
        Path("skills/milestone-logging/_rtx/_agent_timeline.py"),
    }
    assert binding_paths == module_under_test._BINDING_CROSS_HOST_ORCHESTRATION_PATHS
    assert milestone_paths == module_under_test._MILESTONE_COMPATIBILITY_RUNTIME_PATHS

    content = "# Coordinates Claude, Codex, Windows, macOS, and Linux.\n"
    for relative_path in binding_paths | milestone_paths:
        source = tmp_path / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(content)

    rejected = (
        Path("src/officina/common/context_helper.py"),
        Path("src/officina/recurring/helper.py"),
        Path("skills/milestone-logging/_rtx/_helper.py"),
    )
    for relative_path in rejected:
        source = tmp_path / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(content)

    assert set(_validate_text(tmp_path, monkeypatch)) == {
        f"{relative_path}:1: {content.rstrip()}" for relative_path in rejected
    }


def test_blueprint_metadata_exemptions_and_prose_rejection_share_one_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = tmp_path / "skills" / "a-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "platform_support:\n  linux: true\n  macos: false\n  windows: false\n"
        "description: Uses Linux-specific paths.\n"
    )
    (runtime / "._worker_file.py.blueprint.yaml").write_text(
        "platform_support:\n  linux: true\n  macos: true\n  windows: true\n"
    )

    refs = tmp_path / "references" / "blueprint-schema"
    refs.mkdir(parents=True)
    (refs / "README.md").write_text(
        "Use `linux`/`macos`/`windows` booleans for support metadata.\n"
        "Use Codex for this flow.\n"
    )
    syncer = tmp_path / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
    syncer.parent.mkdir(parents=True)
    syncer.write_text('PLATFORM_NAMES = ("linux", "macos", "windows")\n')

    assert set(_validate_text(tmp_path, monkeypatch)) == {
        "skills/a-skill/blueprint.yaml:5: description: Uses Linux-specific paths.",
        "references/blueprint-schema/README.md:2: Use Codex for this flow.",
    }


def test_validated_v5_child_blueprint_metadata_is_excluded_from_text_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    blueprint = tmp_path / "skills" / "demo" / "_rtx" / "blueprint.yaml"
    blueprint.parent.mkdir(parents=True)
    blueprint.write_text(
        "description: Runtime selects Windows adapters.\n",
        encoding="utf-8",
    )
    graph = SimpleNamespace(
        nodes={"demo-rtx": SimpleNamespace(blueprint_path=blueprint)}
    )
    monkeypatch.setattr(
        module_under_test,
        "_git_ignored_paths",
        lambda _repo_root: frozenset(),
    )

    assert module_under_test.validate_with_graph(tmp_path, graph) == []


def test_frozen_v4_blueprint_metadata_and_owned_content_are_line_checked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = tmp_path / "skills" / "a-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Host-neutral instructions.\n")
    (runtime / "_claude_parser.py").write_text("# Host-specific implementation.\n")
    (runtime / "shared.py").write_text("# Calls Claude directly from shared code.\n")
    (skill / "blueprint.yaml").write_text(
        "schema_version: 4\n"
        "node_type: module\n"
        "id: a-skill\n"
        "version: 1\n"
        "gateway:\n"
        "  path: SKILL.md\n"
        "  language: Markdown\n"
        "content:\n"
        "  - SKILL\\.md\n"
        "  - _rtx/_claude_parser\\.py\n"
        "  - _rtx/shared\\.py\n"
        "authority:\n"
        "  owns_filesystem: []\n"
        "sources: {}\n"
        "exports: {}\n"
    )

    errors = _validate_text(tmp_path, monkeypatch)

    assert len(errors) == 2
    assert any("skills/a-skill/blueprint.yaml:10" in error for error in errors)
    assert any(
        "skills/a-skill/_rtx/shared.py" in error
        and "Calls Claude directly" in error
        for error in errors
    )
