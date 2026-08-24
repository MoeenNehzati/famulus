"""Tests for validators/platform_neutral.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from test_support.git_repository import GitTestRepository

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators import platform_neutral as module_under_test  # noqa: E402
from validators.platform_neutral import validate  # noqa: E402


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

    assert module_under_test._validate(tmp_path, frozenset()) == []
    assert calls == 1


def test_standalone_check_root_keeps_relative_finding_path(tmp_path: Path) -> None:
    root_file = tmp_path / "CLAUDE.md"
    root_file.write_text("Use Codex here.\n", encoding="utf-8")

    assert module_under_test._validate(tmp_path, frozenset()) == [
        "CLAUDE.md:1: Use Codex here."
    ]


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_clean_skill_passes(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: my-skill\n---\nHello world.\n")
    assert validate(tmp_path) == []


def test_platform_reference_in_skill_detected(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: my-skill\n---\nUse Claude for this.\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "Claude" in errors[0]


def test_excluded_install_path_skipped(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "install-assistant-tools"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Install Claude Code here.\n")
    assert validate(tmp_path) == []


def test_standard_documents_are_excluded(
    tmp_path: Path,
) -> None:
    standards = tmp_path / "references" / "node-standards"
    standards.mkdir(parents=True)
    for name in (
        "node.standard.yaml",
        "python-node.standard.yaml",
    ):
        (standards / name).write_text(
            "Supports Linux, macOS, Windows, Codex, and Claude.\n",
            encoding="utf-8",
        )

    assert validate(tmp_path) == []


def test_tests_subdir_skipped(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill" / "tests"
    d.mkdir(parents=True)
    (d / "test_something.py").write_text("# test for claude or codex\n")
    assert validate(tmp_path) == []


def test_references_dir_scanned(tmp_path: Path) -> None:
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("Use Claude Code to run this.\n")
    errors = validate(tmp_path)
    assert any("Claude" in e for e in errors)


def test_multiple_violations_all_reported(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Use Claude.\nAlso codex.\n")
    errors = validate(tmp_path)
    assert len(errors) == 2


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


def test_runner_exits_nonzero_on_violation(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    repository = GitTestRepository.initialize_existing_empty(tmp_path)
    (tmp_path / "validators").mkdir()
    shutil.copy2(repo_root / "repo_checks.py", tmp_path / "repo_checks.py")
    shutil.copy2(
        repo_root / "validators" / "platform_neutral.py",
        tmp_path / "validators",
    )
    shutil.copytree(repo_root / "src", tmp_path / "src")
    d = tmp_path / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Use Claude here.\n")
    repository.git("add", ".")
    runner = tmp_path / "repo_checks.py"
    result = subprocess.run(
        [
            "python3",
            str(runner),
            "--suite",
            "validators",
            "--repo-root",
            str(tmp_path),
            "--validator",
            "repo/platform_neutral",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


def test_claude_named_file_may_mention_claude(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill" / "_rtx"
    d.mkdir(parents=True)
    (d / "claude_parser.py").write_text("# Handles Claude Code's transcript format.\n")
    assert validate(tmp_path) == []


def test_codex_named_file_may_mention_codex(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill" / "_rtx"
    d.mkdir(parents=True)
    (d / "codex_parser.py").write_text("# Handles Codex's transcript format.\n")
    assert validate(tmp_path) == []


def test_windows_named_file_may_mention_windows(tmp_path: Path) -> None:
    d = tmp_path / "src" / "officina" / "common" / "secrets"
    d.mkdir(parents=True)
    (d / "windows.py").write_text("# Handles Windows win32 credential storage.\n")
    assert validate(tmp_path) == []


def test_osx_named_file_may_mention_macos_and_darwin(tmp_path: Path) -> None:
    d = tmp_path / "src" / "officina" / "common" / "secrets"
    d.mkdir(parents=True)
    (d / "osx.py").write_text("# Handles macOS and darwin credential storage.\n")
    assert validate(tmp_path) == []


def test_linux_named_file_may_mention_linux(tmp_path: Path) -> None:
    d = tmp_path / "src" / "officina" / "common" / "secrets"
    d.mkdir(parents=True)
    (d / "linux.py").write_text("# Handles Linux credential storage.\n")
    assert validate(tmp_path) == []


def test_claude_named_file_may_not_mention_codex(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill" / "_rtx"
    d.mkdir(parents=True)
    (d / "claude_parser.py").write_text("# Also handles Codex, oddly.\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "Codex" in errors[0]


def test_generically_named_file_may_not_mention_either_host(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill" / "_rtx"
    d.mkdir(parents=True)
    (d / "scan.py").write_text("# Scans Claude transcripts.\n# Scans Codex transcripts.\n")
    errors = validate(tmp_path)
    assert len(errors) == 2


def test_generically_named_file_may_not_mention_operating_system(tmp_path: Path) -> None:
    d = tmp_path / "src" / "officina" / "common"
    d.mkdir(parents=True)
    (d / "secret_store.py").write_text("# Uses Windows, macOS, and Linux stores.\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "Windows" in errors[0]


@pytest.mark.parametrize(
    "relative_path",
    (
        Path("src/officina/install/context.py"),
        Path("src/officina/install/development_activation.py"),
        Path("src/officina/install/runtime_pointer.py"),
        Path("src/officina/install/resolvers/launch.py"),
        Path("src/officina/launchers/agent.py"),
        Path("src/officina/recurring/runtime.py"),
        Path("src/officina/recurring/healthcheck.py"),
        Path("src/officina/recurring/executor.py"),
        Path("src/officina/recurring/native.py"),
        Path("src/officina/recurring/state.py"),
        Path("src/officina/recurring/default_jobs.yaml"),
        Path("src/officina/configuration/schema.json"),
    ),
)
def test_binding_cross_host_orchestration_files_are_exactly_allowed(
    tmp_path: Path, relative_path: Path
) -> None:
    source = tmp_path / relative_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# Coordinates Claude, Codex, Windows, macOS, and Linux.\n")

    assert validate(tmp_path) == []


def test_nearby_generic_orchestration_file_remains_rejected(tmp_path: Path) -> None:
    source = tmp_path / "src" / "officina" / "install" / "context_helper.py"
    source.parent.mkdir(parents=True)
    source.write_text("# Coordinates Claude, Codex, Windows, macOS, and Linux.\n")

    errors = validate(tmp_path)
    assert errors == [
        "src/officina/install/context_helper.py:1: "
        "# Coordinates Claude, Codex, Windows, macOS, and Linux."
    ]


def test_nearby_recurring_file_remains_rejected(tmp_path: Path) -> None:
    source = tmp_path / "src" / "officina" / "recurring" / "helper.py"
    source.parent.mkdir(parents=True)
    source.write_text("# Coordinates Claude, Codex, Windows, macOS, and Linux.\n")

    errors = validate(tmp_path)
    assert errors == [
        "src/officina/recurring/helper.py:1: "
        "# Coordinates Claude, Codex, Windows, macOS, and Linux."
    ]


@pytest.mark.parametrize(
    "relative_path",
    (
        Path("skills/milestone-logging/_rtx/_milestone_writer.py"),
        Path("skills/milestone-logging/_rtx/_agent_timeline.py"),
    ),
)
def test_milestone_compatibility_runtime_paths_are_exactly_allowed(
    tmp_path: Path, relative_path: Path
) -> None:
    source = tmp_path / relative_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# Coordinates Claude, Codex, Windows, macOS, and Linux.\n")

    assert validate(tmp_path) == []


def test_nearby_milestone_runtime_file_remains_rejected(tmp_path: Path) -> None:
    source = tmp_path / "skills" / "milestone-logging" / "_rtx" / "_helper.py"
    source.parent.mkdir(parents=True)
    source.write_text("# Coordinates Claude, Codex, Windows, macOS, and Linux.\n")

    errors = validate(tmp_path)
    assert errors == [
        "skills/milestone-logging/_rtx/_helper.py:1: "
        "# Coordinates Claude, Codex, Windows, macOS, and Linux."
    ]


def test_blueprint_graph_shared_module_is_platform_neutral(tmp_path: Path) -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "officina"
        / "blueprints"
        / "graph.py"
    )
    target = tmp_path / "src" / "officina" / "blueprints" / "graph.py"
    target.parent.mkdir(parents=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    assert validate(tmp_path) == []


def test_blueprint_platform_support_metadata_is_allowed(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "blueprint.yaml").write_text(
        "interfaces:\n"
        "  machine:\n"
        "    run:\n"
        "      platform_support:\n"
        "        linux: true\n"
        "        macos: false\n"
        "        windows: false\n"
        "      dependencies:\n"
        "        - kind: binary\n"
        "          platforms:\n"
        "            linux: true\n"
        "            macos: false\n"
        "            windows: false\n"
    )
    assert validate(tmp_path) == []


def test_validated_v5_child_blueprint_metadata_is_excluded_from_text_scan(
    tmp_path: Path,
) -> None:
    blueprint = tmp_path / "skills" / "demo" / "_rtx" / "blueprint.yaml"
    blueprint.parent.mkdir(parents=True)
    blueprint.write_text(
        "description: Runtime selects Windows adapters.\n",
        encoding="utf-8",
    )
    graph = SimpleNamespace(
        nodes={
            "demo-rtx": SimpleNamespace(blueprint_path=blueprint),
        }
    )

    assert validate.__globals__["validate_with_graph"](tmp_path, graph) == []


def test_typed_blueprint_sidecar_platform_support_metadata_is_allowed(
    tmp_path: Path,
) -> None:
    d = tmp_path / "skills" / "a-skill" / "_rtx"
    d.mkdir(parents=True)
    (d / "._worker_file.py.blueprint.yaml").write_text(
        "platform_support:\n  linux: true\n  macos: true\n  windows: true\n"
    )

    assert validate(tmp_path) == []


def test_blueprint_generic_platform_prose_is_still_rejected(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "blueprint.yaml").write_text("description: Uses Linux-specific paths.\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "Linux-specific" in errors[0]


def test_frozen_v4_blueprint_metadata_and_owned_content_are_line_checked(
    tmp_path: Path,
) -> None:
    d = tmp_path / "skills" / "a-skill"
    runtime = d / "_rtx"
    runtime.mkdir(parents=True)
    (d / "SKILL.md").write_text("Host-neutral instructions.\n", encoding="utf-8")
    (runtime / "_claude_parser.py").write_text(
        "# Host-specific implementation.\n",
        encoding="utf-8",
    )
    (runtime / "shared.py").write_text(
        "# Calls Claude directly from shared code.\n",
        encoding="utf-8",
    )
    (d / "blueprint.yaml").write_text(
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
        "exports: {}\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert len(errors) == 2
    assert any("skills/a-skill/blueprint.yaml:10" in error for error in errors)
    assert any(
        "skills/a-skill/_rtx/shared.py" in error
        and "Calls Claude directly" in error
        for error in errors
    )


def test_blueprint_reference_docs_can_define_platform_metadata(tmp_path: Path) -> None:
    refs = tmp_path / "references" / "blueprint-schema"
    refs.mkdir(parents=True)
    (refs / "README.md").write_text("Use `linux`/`macos`/`windows` booleans for support metadata.\n")
    assert validate(tmp_path) == []


def test_blueprint_reference_docs_still_reject_host_names(tmp_path: Path) -> None:
    refs = tmp_path / "references" / "blueprint-schema"
    refs.mkdir(parents=True)
    (refs / "README.md").write_text("Use Codex for this flow.\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "Codex" in errors[0]


def test_blueprint_syncer_can_define_platform_keys(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "skill-maker" / "_rtx"
    d.mkdir(parents=True)
    (d / "_blueprint_syncer.py").write_text('PLATFORM_NAMES = ("linux", "macos", "windows")\n')
    assert validate(tmp_path) == []


def test_init_py_always_exempt(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill" / "_rtx"
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(
        "from claude_parser import ClaudeParser\nfrom codex_parser import CodexParser\n"
    )
    assert validate(tmp_path) == []


def test_node_standards_can_define_platform_rule(tmp_path: Path) -> None:
    refs = tmp_path / "references" / "node-standards"
    refs.mkdir(parents=True)
    (refs / "node.standard.yaml").write_text("Use Windows, macOS, Linux, Claude, and Codex here.\n")
    (refs / "python-node.standard.yaml").write_text("Use Windows, macOS, Linux, Claude, and Codex here.\n")
    assert validate(tmp_path) == []


def test_content_match_is_case_insensitive(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill" / "_rtx"
    d.mkdir(parents=True)
    (d / "scan.py").write_text('home = os.environ.get("CLAUDE_HOME")\n')
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "CLAUDE_HOME" in errors[0]


def test_filename_match_is_case_insensitive(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill" / "_rtx"
    d.mkdir(parents=True)
    (d / "Claude_Parser.py").write_text("# Handles Claude Code's transcript format.\n")
    assert validate(tmp_path) == []
