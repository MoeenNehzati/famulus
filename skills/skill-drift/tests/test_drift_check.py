from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_check_drift_state.py"
SPEC = importlib.util.spec_from_file_location("skill_check_drift_state", MODULE_PATH)
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def make_skill(repo: Path, name: str = "demo-skill") -> Path:
    skill = repo / "skills" / name
    write(skill / "SKILL.md", "demo skill\n")
    write(skill / "_rtx" / "_worker.py", "VALUE = 'one'\n")
    write(
        skill / "blueprint.yaml",
        "\n".join(
            [
                "category: development-assistant",
                "interface_version: 1",
                "depends_on: {}",
                "interfaces:",
                "  machine:",
                "    worker:",
                "      description: Worker.",
                "      usage: ''",
                "      allow_all_skills: true",
                "      allowed_callers: []",
                "      patterns:",
                "        - min_positionals: 0",
                "          max_positionals: 0",
                "          allow_stdin: false",
                "      runtime:",
                "        kind: command",
                "        argv:",
                "          - _rtx/_worker.py",
                "      dependencies: []",
                "      directly_reads: []",
                "      directly_executes:",
                "        - _rtx/_worker.py",
                "      directly_writes: []",
                "  llm:",
                "    default:",
                "      description: Primary.",
                "      binding:",
                "        kind: skill_file",
                "        path: SKILL.md",
                "      directly_reads:",
                "        - SKILL.md",
                "      directly_executes: []",
                "      directly_writes: []",
                "",
            ]
        ),
    )
    return skill


def source_for(repo: Path) -> object:
    return checker.SkillSource(source="test", package_root=repo, skills_root=repo / "skills")


def matching_record(repo: Path, skill_name: str = "demo-skill") -> dict[str, object]:
    return {
        "schema_version": checker.SCHEMA_VERSION,
        "skill": skill_name,
        "recorded_at": "2026-07-11T16:10:00-04:00",
        "writer": "skill-doctor@first-pass",
        "hashes": checker.compute_audit_hashes(repo, repo / "skills", skill_name),
    }


def concern_kinds(report: object) -> set[str]:
    return {concern.kind for concern in report.concerns}


def test_matching_audit_record_is_current(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write_json(skill / ".last_audit.json", matching_record(tmp_path))

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-current"
    assert report.concerns == []


def test_missing_audit_record_is_stale(tmp_path: Path) -> None:
    make_skill(tmp_path)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert concern_kinds(report) == {"missing-record"}


def test_corrupt_audit_record_is_stale(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write(skill / ".last_audit.json", "{not json\n")

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert "corrupt-record" in concern_kinds(report)


def test_different_record_shape_is_stale(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write_json(skill / ".last_audit.json", {"schema_version": 1, "skill": "demo-skill"})

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert "corrupt-record" in concern_kinds(report)


def test_skill_mismatch_is_stale(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    record = matching_record(tmp_path)
    record["skill"] = "other-skill"
    write_json(skill / ".last_audit.json", record)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert "skill-mismatch" in concern_kinds(report)


def test_hash_change_is_stale(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write_json(skill / ".last_audit.json", matching_record(tmp_path))
    write(skill / "_rtx" / "_worker.py", "VALUE = 'two'\n")

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    changed = [concern for concern in report.concerns if concern.kind == "changed-hash"]
    assert {concern.key for concern in changed} >= {"skill", "interfaces.machine.worker"}


def test_missing_recorded_hash_is_stale(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    record = matching_record(tmp_path)
    del record["hashes"]["interfaces"]["machine.worker"]  # type: ignore[index]
    write_json(skill / ".last_audit.json", record)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert any(
        concern.kind == "missing-hash" and concern.key == "interfaces.machine.worker"
        for concern in report.concerns
    )


def test_extra_recorded_hash_is_stale(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    record = matching_record(tmp_path)
    record["hashes"]["interfaces"]["machine.old"] = "sha256:old"  # type: ignore[index]
    write_json(skill / ".last_audit.json", record)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert any(
        concern.kind == "extra-recorded-hash" and concern.key == "interfaces.machine.old"
        for concern in report.concerns
    )


def test_status_json_reports_stale_without_writing(tmp_path: Path, capsys) -> None:
    make_skill(tmp_path)

    exit_code = checker.main(["status", "demo-skill", "--json", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"audit-current": 0, "audit-stale": 1}
    assert payload["skills"][0]["derived_status"] == "audit-stale"
    assert not (tmp_path / "skills" / "demo-skill" / ".last_audit.json").exists()


def test_status_text_reports_markdown_table(tmp_path: Path, capsys) -> None:
    first = make_skill(tmp_path, "first-skill")
    make_skill(tmp_path, "second-skill")
    write_json(first / ".last_audit.json", matching_record(tmp_path, "first-skill"))

    exit_code = checker.main(["status", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "# Skill Drift Report" in output
    assert "| Source | Skill | Audit status | Record | Concerns |" in output
    assert "| override | first-skill | audit-current | skills/first-skill/.last_audit.json | none |" in output
    assert (
        "| override | second-skill | audit-stale | skills/second-skill/.last_audit.json | "
        "missing-record:"
    ) in output


def test_status_text_saves_markdown_report_by_default(tmp_path: Path, capsys, monkeypatch) -> None:
    make_skill(tmp_path)
    build_dir = tmp_path / "report-build"
    monkeypatch.setattr(checker, "BUILD_DIR", build_dir)

    exit_code = checker.main(["status", "demo-skill", "--repo-root", str(tmp_path)])

    output = capsys.readouterr().out
    saved_files = list(build_dir.glob("*.md"))
    assert exit_code == 0
    assert len(saved_files) == 1
    saved = saved_files[0]
    assert f"Saved report: {saved.as_posix()}" in output
    assert saved.read_text(encoding="utf-8").startswith("# Skill Drift Report\n")


def test_status_json_accepts_multiple_explicit_skills(tmp_path: Path, capsys) -> None:
    first = make_skill(tmp_path, "first-skill")
    second = make_skill(tmp_path, "second-skill")
    write_json(first / ".last_audit.json", matching_record(tmp_path, "first-skill"))
    write_json(second / ".last_audit.json", matching_record(tmp_path, "second-skill"))

    exit_code = checker.main(
        [
            "status",
            "second-skill",
            "first-skill",
            "--json",
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"audit-current": 2, "audit-stale": 0}
    assert [skill["skill"] for skill in payload["skills"]] == ["second-skill", "first-skill"]


def test_status_json_without_skill_checks_all_observed_skills(tmp_path: Path, capsys) -> None:
    first = make_skill(tmp_path, "first-skill")
    second = make_skill(tmp_path, "second-skill")
    write_json(first / ".last_audit.json", matching_record(tmp_path, "first-skill"))
    write(tmp_path / "skills" / ".system" / "hidden" / "SKILL.md", "hidden\n")
    write(tmp_path / "skills" / "not-a-skill" / "README.md", "missing SKILL.md\n")

    exit_code = checker.main(["status", "--json", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"audit-current": 1, "audit-stale": 1}
    assert [skill["skill"] for skill in payload["skills"]] == ["first-skill", "second-skill"]


def test_skill_without_blueprint_is_reported_stale_instead_of_aborting(tmp_path: Path, capsys) -> None:
    make_skill(tmp_path, "normal-skill")
    write(tmp_path / "skills" / "plugin-skill" / "SKILL.md", "plugin skill\n")

    exit_code = checker.main(["status", "--json", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    reports = {skill["skill"]: skill for skill in payload["skills"]}
    assert payload["summary"] == {"audit-current": 0, "audit-stale": 2}
    assert reports["plugin-skill"]["derived_status"] == "audit-stale"
    assert any(
        concern["kind"] == "hash-unavailable" and "missing blueprint.yaml" in concern["message"]
        for concern in reports["plugin-skill"]["concerns"]
    )


def test_status_rejects_all_with_explicit_skills(tmp_path: Path, capsys) -> None:
    make_skill(tmp_path)

    exit_code = checker.main(["status", "demo-skill", "--all", "--repo-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "either skill names or --all" in captured.err


def test_status_without_skill_checks_codex_and_claude_skill_roots(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_skill = make_skill(codex_home, "codex-skill")
    make_skill(claude_home, "claude-skill")
    write_json(codex_skill / ".last_audit.json", matching_record(codex_home, "codex-skill"))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    exit_code = checker.main(["status", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"audit-current": 1, "audit-stale": 1}
    assert [(skill["source"], skill["skill"]) for skill in payload["skills"]] == [
        ("codex", "codex-skill"),
        ("claude", "claude-skill"),
    ]


def test_status_with_explicit_skill_checks_matching_installed_roots(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_skill = make_skill(codex_home, "shared-skill")
    claude_skill = make_skill(claude_home, "shared-skill")
    write_json(codex_skill / ".last_audit.json", matching_record(codex_home, "shared-skill"))
    write_json(claude_skill / ".last_audit.json", matching_record(claude_home, "shared-skill"))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    exit_code = checker.main(["status", "shared-skill", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"audit-current": 2, "audit-stale": 0}
    assert [(skill["source"], skill["skill"]) for skill in payload["skills"]] == [
        ("codex", "shared-skill"),
        ("claude", "shared-skill"),
    ]


def test_status_with_skill_root_uses_that_skill_install_root(tmp_path: Path, capsys) -> None:
    skill = make_skill(tmp_path, "demo-skill")
    write_json(skill / ".last_audit.json", matching_record(tmp_path))

    exit_code = checker.main(["status", "demo-skill", "--json", "--skill-root", str(skill)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"audit-current": 1, "audit-stale": 0}
    assert payload["skills"][0]["source"] == "override"
