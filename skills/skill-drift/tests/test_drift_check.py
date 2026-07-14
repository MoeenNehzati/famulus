from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_check_drift_state.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from officina.common.artifact_health import (
    blueprint_schema_hash,
    certify_graph,
    health_path_for_node,
)
from officina.common.audit_records import attach_record_digest
from officina.common.blueprint_graph import (
    load_repository_blueprint_graphs,
    resolve_repository_skill_graph,
)
from officina.common.pooled_blueprint import (
    certify_pooled_review,
    pooled_review_health_path,
    pooled_review_path,
    render_pooled_review,
)

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


def install_policy_manifest(repo: Path, patterns: list[str] | None = None) -> Path:
    manifest = repo / "skills" / "skill-drift" / "references" / "policy-hash-roots.json"
    if patterns is None:
        source = MODULE_PATH.parents[1] / "references" / "policy-hash-roots.json"
        write(manifest, source.read_text(encoding="utf-8"))
    else:
        write_json(manifest, patterns)
    return manifest


def make_skill(repo: Path, name: str = "demo-skill") -> Path:
    install_policy_manifest(repo)
    skill = repo / "skills" / name
    write(skill / "SKILL.md", "demo skill\n")
    write(
        skill / "_rtx" / "_worker.py",
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n\n"
        "VALUE = 'one'\n\n"
        "class Interface(PythonMachineInterface):\n"
        "    pass\n",
    )
    write(
        skill / "blueprint.yaml",
        "\n".join(
            [
                "category: development-assistant",
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
                "      invocation:",
                "        kind: python_machine_interface",
                "        entrypoint: _rtx/_worker.py:Interface",
                "        behavior_sources: []",
                "      dependencies: []",
                "  llm:",
                "    default:",
                "      description: Primary.",
                "      binding:",
                "        kind: skill_file",
                "        path: SKILL.md",
                "      behavior_sources: []",
                "",
            ]
        ),
    )
    return skill


def make_typed_skill(repo: Path, name: str = "demo-skill") -> Path:
    install_policy_manifest(repo)
    skill = repo / "skills" / name
    write(skill / "SKILL.md", "demo skill\n")
    write(
        skill / "blueprint.yaml",
        "schema_version: 2\n"
        "blueprint_type: skill\n"
        f"id: {name}\n"
        "category: development-assistant\n"
        "role: automation\n"
        "kind: tool\n"
        "interfaces:\n"
        f"  - interface: {name}.llm.default\n"
        "    version: 1\n"
        "    blueprint:\n"
        "      base: skill-root\n"
        "      path: .SKILL.md.blueprint.yaml\n",
    )
    write(
        skill / ".SKILL.md.blueprint.yaml",
        "schema_version: 2\n"
        "blueprint_type: llm-interface\n"
        f"id: {name}.llm.default\n"
        "version: 1\n"
        "description: Primary.\n"
        "binding:\n"
        "  kind: instruction-file\n"
        "  path: SKILL.md\n"
        "behavior_sources:\n"
        f"  - source: {name}.source.policy\n"
        "    version: 1\n"
        "    blueprint:\n"
        "      base: skill-root\n"
        "      path: references/.policy.md.blueprint.yaml\n"
        "    reason: Defines policy.\n"
        "direct_io:\n"
        "  reads: []\n"
        "  writes: []\n"
        "  network: []\n"
        "owns_filesystem: []\n",
    )
    write(skill / "references" / "policy.md", "target policy\n")
    write(
        skill / "references" / ".policy.md.blueprint.yaml",
        "schema_version: 2\n"
        "blueprint_type: behavior-source\n"
        f"id: {name}.source.policy\n"
        "version: 1\n"
        "description: Policy.\n"
        "binding:\n"
        "  kind: file\n"
        "  path: references/policy.md\n"
        "content: config\n"
        "format: markdown\n"
        "uses_behavior_sources: []\n",
    )
    schema_root = repo / "references" / "blueprint"
    source_schema_root = MODULE_PATH.parents[3] / "references" / "blueprint"
    for source in [
        *source_schema_root.glob("*.schema.json"),
        source_schema_root / "schema.annotated-draft.json",
        source_schema_root / "schema.json",
        source_schema_root / "schema-meta.json",
        source_schema_root / "template.yaml",
    ]:
        write(schema_root / source.name, source.read_text(encoding="utf-8"))
    return skill


def write_typed_health(repo: Path, skill_name: str = "demo-skill") -> None:
    graph = resolve_repository_skill_graph(
        load_repository_blueprint_graphs(repo),
        skill_name,
    )
    key = b"k" * 32
    key_path = repo / "skills" / "skill-audit" / ".health-authentication-key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    records = certify_graph(
        graph,
        policy_hash=checker.compute_policy_hash(repo),
        schema_hash=blueprint_schema_hash(repo / "references" / "blueprint"),
        schema_root=repo / "references" / "blueprint",
        checks=[{"id": "semantic-exactness", "passed": True}],
        key=key,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    for node_id, record in records.items():
        write_json(health_path_for_node(graph.nodes[node_id]), record)
    pool = pooled_review_path(graph.skill_root)
    write(pool, render_pooled_review(graph, records))
    write_json(
        pooled_review_health_path(graph.skill_root),
        certify_pooled_review(
            pool,
            records[graph.root.node_id],
            key=key,
            certified_at="2026-07-13T12:00:00-04:00",
        ),
    )


def source_for(repo: Path) -> object:
    return checker.SkillSource(source="test", package_root=repo, skills_root=repo / "skills")


def matching_record(repo: Path, skill_name: str = "demo-skill") -> dict[str, object]:
    hashes = dict(checker.compute_audit_hashes(repo, repo / "skills", skill_name))
    audit_policy_hash = hashes.pop("policy")
    return attach_record_digest(
        {
            "skill": skill_name,
            "timestamp": "2026-07-11T16:10:00-04:00",
            "audit_policy_hash": audit_policy_hash,
            "checks": {
                "mechanical": [
                    {"name": "validators", "passed": True},
                    {"name": "tests", "passed": True},
                ],
                "semantic": {"passed": True, "findings": []},
            },
            "hashes": hashes,
        }
    )


def write_validator_runner(repo: Path, *, passing: bool = True) -> None:
    exit_code = 0 if passing else 1
    write(
        repo / "validators" / "runner.py",
        "from __future__ import annotations\n"
        "import sys\n"
        f"print('validator exit {exit_code}')\n"
        f"raise SystemExit({exit_code})\n",
    )


def write_skill_test(repo: Path, skill_name: str = "demo-skill", *, passing: bool = True) -> None:
    assertion = "assert True" if passing else "assert False"
    write(
        repo / "skills" / skill_name / "tests" / "test_health.py",
        f"def test_health() -> None:\n    {assertion}\n",
    )


def concern_kinds(report: object) -> set[str]:
    return {concern.kind for concern in report.concerns}


def test_matching_audit_record_is_current(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write_json(skill / ".last_audit.json", matching_record(tmp_path))

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-current"
    assert report.concerns == []


def test_matching_typed_health_graph_is_current(tmp_path: Path) -> None:
    make_typed_skill(tmp_path)
    write_typed_health(tmp_path)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-current"
    assert report.concerns == []


def test_typed_drift_uses_target_installation_schema_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    make_typed_skill(tmp_path)
    write_typed_health(tmp_path)
    observed: dict[str, object] = {}
    real_check_graph_health = checker.check_graph_health

    def capture_schema_root(*args: object, **kwargs: object) -> object:
        schema_root = Path(kwargs.pop("schema_root"))
        observed["schema_root"] = schema_root
        observed["schema_meta"] = (schema_root / "schema-meta.json").read_bytes()
        return real_check_graph_health(*args, **kwargs)

    monkeypatch.setattr(checker, "check_graph_health", capture_schema_root)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-current"
    target_schema_root = tmp_path / "references" / "blueprint"
    assert observed["schema_root"] != target_schema_root
    assert observed["schema_meta"] == (target_schema_root / "schema-meta.json").read_bytes()


def test_typed_bound_file_change_makes_root_stale(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    write_typed_health(tmp_path)
    write(skill / "SKILL.md", "changed instructions\n")

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert "artifact-stale" in concern_kinds(report)
    assert "downstream-unhealthy" in concern_kinds(report)


def test_unauthenticated_typed_root_fields_are_not_reported(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    write_typed_health(tmp_path)
    record_path = skill / ".last_audit.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["certification"]["certified_at"] = "attacker-controlled"
    record["hashes"]["policy_hash"] = "sha256:" + "f" * 64
    write_json(record_path, record)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert report.timestamp is None
    assert report.recorded_hashes is None


def test_typed_drift_does_not_create_missing_authentication_key(tmp_path: Path) -> None:
    make_typed_skill(tmp_path)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert "missing-authentication-key" in concern_kinds(report)
    assert not (
        tmp_path / "skills" / "skill-audit" / ".health-authentication-key"
    ).exists()


def test_stale_pooled_review_does_not_make_typed_root_stale(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    write_typed_health(tmp_path)
    write(skill / ".pooled-blueprint-review.yaml", "manually changed\n")

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-current"
    assert "invalid-pooled-review" in concern_kinds(report)


def test_symlinked_pooled_review_is_pool_only_concern(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    write_typed_health(tmp_path)
    pool = pooled_review_path(skill)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-pooled-review.yaml"
    write(outside, pool.read_text(encoding="utf-8"))
    pool.unlink()
    pool.symlink_to(outside)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-current"
    assert report.current_hashes["root_certified_health"].startswith("sha256:")
    assert "invalid-pooled-review" in concern_kinds(report)
    assert "hash-unavailable" not in concern_kinds(report)


def test_symlinked_pooled_health_is_pool_only_concern(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    write_typed_health(tmp_path)
    pool_health = pooled_review_health_path(skill)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-pooled-health.json"
    write(outside, pool_health.read_text(encoding="utf-8"))
    pool_health.unlink()
    pool_health.symlink_to(outside)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-current"
    assert report.current_hashes["root_certified_health"].startswith("sha256:")
    assert "invalid-pooled-review-health" in concern_kinds(report)
    assert "hash-unavailable" not in concern_kinds(report)


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
    write(
        skill / "_rtx" / "_worker.py",
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n\n"
        "VALUE = 'two'\n\n"
        "class Interface(PythonMachineInterface):\n"
        "    pass\n",
    )

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    changed = [concern for concern in report.concerns if concern.kind == "changed-hash"]
    assert {concern.key for concern in changed} >= {"skill", "interfaces.machine.worker"}


def test_changed_check_status_without_new_digest_is_stale(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    record = matching_record(tmp_path)
    record["checks"]["semantic"]["passed"] = False  # type: ignore[index]
    write_json(skill / ".last_audit.json", record)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert "record-digest-mismatch" in concern_kinds(report)


def test_regenerated_digest_with_failed_check_is_stale(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    record = matching_record(tmp_path)
    record["checks"]["semantic"]["passed"] = False  # type: ignore[index]
    write_json(skill / ".last_audit.json", attach_record_digest(record))

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-stale"
    assert "failed-check" in concern_kinds(report)


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


def test_policy_hash_changes_when_skill_audit_changes(tmp_path: Path) -> None:
    install_policy_manifest(tmp_path)
    write(tmp_path / "references" / "skill-guidelines.md", "guidelines\n")
    write(tmp_path / "references" / "blueprint" / "schema.json", "{}\n")
    write(tmp_path / "references" / "blueprint" / "template.yaml", "template\n")
    write(tmp_path / "references" / "blueprint" / "guide.md", "guide\n")
    write(tmp_path / "skills" / "skill-audit" / "_rtx" / "_audit_certifier.py", "one\n")

    first = checker.compute_policy_hash(tmp_path)
    write(tmp_path / "skills" / "skill-audit" / "_rtx" / "_audit_certifier.py", "two\n")
    second = checker.compute_policy_hash(tmp_path)

    assert first != second


@pytest.mark.parametrize("module_name", ["atomic_files.py", "git_provenance.py"])
def test_committed_shared_trust_boundary_change_invalidates_current_record(
    tmp_path: Path,
    module_name: str,
) -> None:
    make_typed_skill(tmp_path)
    module = tmp_path / "src" / "officina" / "common" / module_name
    write(module, "one\n")
    write(
        tmp_path / ".gitignore",
        "**/.last_audit.json\n**/.*.health.json\n**/.pooled-blueprint-review.yaml\n"
        "**/.health-authentication-key\n",
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "baseline"],
        check=True,
    )
    write_typed_health(tmp_path)

    before = checker.check_typed_skill(source_for(tmp_path), "demo-skill")
    assert before.derived_status == "audit-current"

    write(module, "two\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", module.relative_to(tmp_path).as_posix()],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", f"change {module_name}"],
        check=True,
    )

    after = checker.check_typed_skill(source_for(tmp_path), "demo-skill")

    assert after.derived_status == "audit-stale"
    assert after.current_hashes["policy"] != before.current_hashes["policy"]


@pytest.mark.parametrize("command", ["compute-hashes", "status"])
def test_schema_invalid_reachable_sidecar_blocks_typed_hash_and_status(
    tmp_path: Path,
    capsys,
    command: str,
) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    sidecar = skill / "references" / ".policy.md.blueprint.yaml"
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration.pop("content")
    write(sidecar, yaml.safe_dump(declaration, sort_keys=False))

    exit_code = checker.main([command, "--skill-root", str(skill), "--json"])

    payload = json.loads(capsys.readouterr().out)
    if command == "compute-hashes":
        assert exit_code == 2
        assert payload["skills"] == []
        message = payload["errors"][0]["error"]["message"]
    else:
        assert exit_code == 0
        assert payload["summary"] == {"audit-current": 0, "audit-stale": 1}
        concerns = payload["skills"][0]["concerns"]
        message = next(item["message"] for item in concerns if item["kind"] == "hash-unavailable")
    assert "schema error" in message
    assert "$.content" in message
    assert not list(skill.rglob("*.health.json"))
    assert not (skill / ".last_audit.json").exists()


def test_status_json_reports_stale_without_writing(tmp_path: Path, capsys) -> None:
    make_skill(tmp_path)

    exit_code = checker.main(["status", "demo-skill", "--json", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"audit-current": 0, "audit-stale": 1}
    assert payload["skills"][0]["derived_status"] == "audit-stale"
    assert "overall_status" not in payload["skills"][0]
    assert not (tmp_path / "skills" / "demo-skill" / ".last_audit.json").exists()


def test_status_with_test_validate_reports_overall_ok_when_audit_and_health_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_skill(tmp_path)
    write_validator_runner(tmp_path, passing=True)
    write_skill_test(tmp_path, passing=True)
    write_json(skill / ".last_audit.json", matching_record(tmp_path))

    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    report = checker.check_skill(source_for(tmp_path), "demo-skill", with_test_validate=True)

    payload = checker.build_payload([report])
    assert payload["summary"] == {
        "audit-current": 1,
        "audit-stale": 0,
        "health-passed": 1,
        "health-failed": 0,
        "needs-attention": 0,
        "ok": 1,
    }
    report = payload["skills"][0]
    assert report["derived_status"] == "audit-current"
    assert report["health_status"] == "health-passed"
    assert report["overall_status"] == "ok"


def test_status_with_test_validate_ors_failing_tests_with_current_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_skill(tmp_path)
    write_validator_runner(tmp_path, passing=True)
    write_skill_test(tmp_path, passing=False)
    write_json(skill / ".last_audit.json", matching_record(tmp_path))

    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    report = checker.check_skill(source_for(tmp_path), "demo-skill", with_test_validate=True)

    payload = checker.build_payload([report])
    item = payload["skills"][0]
    assert item["derived_status"] == "audit-current"
    assert item["health_status"] == "health-failed"
    assert item["overall_status"] == "needs-attention"
    assert payload["summary"]["needs-attention"] == 1
    failed = [check for check in item["health_checks"] if not check["passed"]]
    assert [check["name"] for check in failed] == ["skill-tests"]


def test_status_with_test_validate_ors_failing_validators_with_current_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_skill(tmp_path)
    write_validator_runner(tmp_path, passing=False)
    write_skill_test(tmp_path, passing=True)
    write_json(skill / ".last_audit.json", matching_record(tmp_path))

    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    report = checker.check_skill(source_for(tmp_path), "demo-skill", with_test_validate=True)

    item = checker.build_payload([report])["skills"][0]
    assert item["derived_status"] == "audit-current"
    assert item["health_status"] == "health-failed"
    assert item["overall_status"] == "needs-attention"
    failed = [check for check in item["health_checks"] if not check["passed"]]
    assert [check["name"] for check in failed] == ["validators"]


def test_status_accepts_exact_skill_root_as_target(tmp_path: Path, capsys) -> None:
    skill = make_skill(tmp_path)

    exit_code = checker.main(["status", str(skill), "--json", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skills"][0]["skill"] == "demo-skill"
    assert payload["skills"][0]["source"] == "path"


def test_compute_hashes_accepts_exact_skill_root_as_target(tmp_path: Path, capsys) -> None:
    skill = make_skill(tmp_path)

    exit_code = checker.main(["compute-hashes", str(skill), "--json", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skills"][0]["skill"] == "demo-skill"
    assert payload["skills"][0]["source"] == "path"


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


def test_skill_root_selects_exactly_one_skill(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    make_typed_skill(tmp_path, "unrelated-skill")

    exit_code = checker.main(["status", "--skill-root", str(skill), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["skill"] for item in payload["skills"]] == ["demo-skill"]


def test_compute_hashes_skill_root_selects_exactly_one_skill(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    make_typed_skill(tmp_path, "unrelated-skill")

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["skill"] for item in payload["skills"]] == ["demo-skill"]


def test_unrelated_malformed_skill_does_not_block_exact_target(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    broken = tmp_path / "skills" / "broken"
    write(broken / "SKILL.md", "broken\n")
    write(broken / "blueprint.yaml", "interfaces: [\n")

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["skill"] for item in payload["skills"]] == ["demo-skill"]


def test_copied_typed_target_skips_validator_and_tests(
    tmp_path: Path,
    capsys,
) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    validator_marker = tmp_path / "validator-executed"
    test_marker = tmp_path / "test-executed"
    write(
        tmp_path / "validators" / "runner.py",
        "from pathlib import Path\n"
        f"Path({str(validator_marker)!r}).write_text('executed')\n"
        "raise SystemExit(1)\n",
    )
    write(
        skill / "tests" / "test_target.py",
        "from pathlib import Path\n"
        f"Path({str(test_marker)!r}).write_text('executed')\n"
        "raise RuntimeError('copied target test executed')\n",
    )

    exit_code = checker.main(
        ["status", "--skill-root", str(skill), "--with-test-validate", "--json"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    checks = payload["skills"][0]["health_checks"]
    assert [check["name"] for check in checks] == ["validators", "skill-tests"]
    assert all(check["skipped"] and check["passed"] for check in checks)
    assert not validator_marker.exists()
    assert not test_marker.exists()


def test_target_policy_hash_uses_target_manifest(tmp_path: Path) -> None:
    install_policy_manifest(tmp_path, ["target-policy.md"])
    write(tmp_path / "target-policy.md", "target-only policy\n")

    expected = checker.digest_entries(
        [
            *checker.entries_for_path(
                tmp_path / "skills" / "skill-drift" / "references" / "policy-hash-roots.json",
                tmp_path,
            ),
            *checker.entries_for_path(tmp_path / "target-policy.md", tmp_path),
        ]
    )

    assert checker.compute_policy_hash(tmp_path) == expected


def test_target_policy_manifest_symlink_is_rejected(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    manifest = tmp_path / "skills" / "skill-drift" / "references" / "policy-hash-roots.json"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-policy-manifest.json"
    write_json(outside, [])
    manifest.unlink()
    manifest.symlink_to(outside)

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    assert exit_code == 2
    assert "symbolic link" in capsys.readouterr().err


def test_target_policy_entry_symlink_is_rejected(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    install_policy_manifest(tmp_path, ["policy/input.md"])
    outside = tmp_path.parent / f"{tmp_path.name}-outside-policy.md"
    write(outside, "outside policy\n")
    entry = tmp_path / "policy" / "input.md"
    entry.parent.mkdir(parents=True)
    entry.symlink_to(outside)

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    assert exit_code == 2
    assert "symbolic link" in capsys.readouterr().err


def test_target_schema_root_symlink_is_rejected(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    schema_root = tmp_path / "references" / "blueprint"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-schema"
    shutil.copytree(schema_root, outside)
    shutil.rmtree(schema_root)
    schema_root.symlink_to(outside, target_is_directory=True)

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    assert exit_code == 2
    assert "symbolic link" in capsys.readouterr().err


def test_target_schema_file_symlink_is_rejected(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    schema = tmp_path / "references" / "blueprint" / "health.schema.json"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-health.schema.json"
    write(outside, schema.read_text(encoding="utf-8"))
    schema.unlink()
    schema.symlink_to(outside)

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    assert exit_code == 2
    assert "symbolic link" in capsys.readouterr().err


def test_target_health_key_symlink_is_rejected(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    write_typed_health(tmp_path, "demo-skill")
    key = tmp_path / "skills" / "skill-audit" / ".health-authentication-key"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-health-key"
    outside.write_bytes(b"x" * 32)
    key.unlink()
    key.symlink_to(outside)

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    assert exit_code == 2
    assert "symbolic link" in capsys.readouterr().err


def test_target_health_record_symlink_is_rejected(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    write_typed_health(tmp_path, "demo-skill")
    record = skill / ".last_audit.json"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-health-record.json"
    write(outside, record.read_text(encoding="utf-8"))
    record.unlink()
    record.symlink_to(outside)

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    assert exit_code == 2
    assert "symbolic link" in capsys.readouterr().err


def test_typed_hashes_are_graph_native_and_complete(tmp_path: Path, capsys) -> None:
    make_typed_skill(tmp_path, "demo-skill")
    write_typed_health(tmp_path, "demo-skill")

    exit_code = checker.main(
        ["compute-hashes", "demo-skill", "--json", "--repo-root", str(tmp_path)]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    report = payload["skills"][0]
    assert report["skill"] == "demo-skill"
    assert report["package_root"] == tmp_path.as_posix()
    assert report["skills_root"] == (tmp_path / "skills").as_posix()
    assert set(report["hashes"]) == {"policy", "schema", "nodes"}
    assert set(report["hashes"]["nodes"]) == {
        "demo-skill",
        "demo-skill.llm.default",
        "demo-skill.source.policy",
    }
    for node in report["hashes"]["nodes"].values():
        assert set(node) == {
            "blueprint_type",
            "local_hash",
            "artifact_graph_hash",
            "expected_certified_health_hash",
        }
        assert node["local_hash"].startswith("sha256:")
        assert node["artifact_graph_hash"].startswith("sha256:")
        assert node["expected_certified_health_hash"].startswith("sha256:")


def test_typed_hash_text_reports_every_field_for_every_node(tmp_path: Path, capsys) -> None:
    make_typed_skill(tmp_path, "demo-skill")
    write_typed_health(tmp_path, "demo-skill")

    exit_code = checker.main(["compute-hashes", "demo-skill", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    expected = [
        "demo-skill [skill]",
        "demo-skill.llm.default [llm-interface]",
        "demo-skill.source.policy [behavior-source]",
    ]
    assert [output.index(label) for label in expected] == sorted(
        output.index(label) for label in expected
    )
    for label in expected:
        line = next(line for line in output.splitlines() if label in line)
        assert "local_hash=sha256:" in line
        assert "artifact_graph_hash=sha256:" in line
        assert "expected_certified_health_hash=sha256:" in line


def test_typed_compute_contains_malformed_reachable_graph(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    write(skill / "references" / ".policy.md.blueprint.yaml", "schema_version: [\n")

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["skills"] == []
    assert payload["errors"][0]["skill"] == "demo-skill"
    assert payload["errors"][0]["error"]["kind"] == "hash-unavailable"
    assert "cannot load blueprint" in payload["errors"][0]["error"]["message"]
    assert "Traceback" not in captured.err


def test_typed_compute_contains_malformed_schema(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    write(tmp_path / "references" / "blueprint" / "health.schema.json", "{\n")

    exit_code = checker.main(["compute-hashes", "--skill-root", str(skill), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["skills"] == []
    assert payload["errors"][0]["skill"] == "demo-skill"
    assert payload["errors"][0]["error"]["kind"] == "hash-unavailable"
    assert "schema" in payload["errors"][0]["error"]["message"]
    assert "Traceback" not in captured.err


def test_typed_compute_continues_after_bad_independent_scope(
    tmp_path: Path,
    capsys,
) -> None:
    bad_repo = tmp_path / "bad-package"
    good_repo = tmp_path / "good-package"
    bad = make_typed_skill(bad_repo, "bad-skill")
    good = make_typed_skill(good_repo, "good-skill")
    write(bad / "references" / ".policy.md.blueprint.yaml", "schema_version: [\n")
    write_typed_health(good_repo, "good-skill")

    exit_code = checker.main(["compute-hashes", str(bad), str(good), "--json"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert [item["skill"] for item in payload["skills"]] == ["good-skill"]
    assert payload["skills"][0]["package_root"] == good_repo.as_posix()
    assert payload["skills"][0]["skills_root"] == (good_repo / "skills").as_posix()
    assert set(payload["skills"][0]["hashes"]) == {"policy", "schema", "nodes"}
    assert payload["errors"] == [
        {
            "skill": "bad-skill",
            "source": "path",
            "package_root": bad_repo.as_posix(),
            "skills_root": (bad_repo / "skills").as_posix(),
            "error": {
                "kind": "hash-unavailable",
                "message": payload["errors"][0]["error"]["message"],
            },
        }
    ]
    assert "cannot load blueprint" in payload["errors"][0]["error"]["message"]


def test_typed_status_contains_malformed_schema_as_hash_unavailable(
    tmp_path: Path,
    capsys,
) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    write(tmp_path / "references" / "blueprint" / "health.schema.json", "{\n")

    exit_code = checker.main(["status", "--skill-root", str(skill), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    report = payload["skills"][0]
    assert report["derived_status"] == "audit-stale"
    assert any(concern["kind"] == "hash-unavailable" for concern in report["concerns"])


def test_typed_status_continues_after_bad_independent_scope(tmp_path: Path, capsys) -> None:
    bad_repo = tmp_path / "bad-package"
    good_repo = tmp_path / "good-package"
    bad = make_typed_skill(bad_repo, "bad-skill")
    good = make_typed_skill(good_repo, "good-skill")
    write(bad_repo / "references" / "blueprint" / "health.schema.json", "{\n")
    write_typed_health(good_repo, "good-skill")

    exit_code = checker.main(["status", str(bad), str(good), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["skill"] for item in payload["skills"]] == ["bad-skill", "good-skill"]
    bad_report, good_report = payload["skills"]
    assert any(concern["kind"] == "hash-unavailable" for concern in bad_report["concerns"])
    assert good_report["derived_status"] == "audit-current"


def test_typed_hashes_do_not_enter_legacy_target_execution_path(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    make_typed_skill(tmp_path, "demo-skill")

    def reject_legacy_hashing(*args: object, **kwargs: object) -> str:
        raise AssertionError("typed target entered legacy hashing")

    monkeypatch.setattr(checker, "hash_skill", reject_legacy_hashing)

    exit_code = checker.main(
        ["compute-hashes", "demo-skill", "--json", "--repo-root", str(tmp_path)]
    )

    assert exit_code == 0


def test_compute_hashes_json_reports_current_hashes_without_reading_record(tmp_path: Path, capsys) -> None:
    make_skill(tmp_path)

    exit_code = checker.main(["compute-hashes", "demo-skill", "--json", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == checker.OUTPUT_SCHEMA_VERSION
    assert len(payload["skills"]) == 1
    assert "errors" not in payload
    report = payload["skills"][0]
    assert report["skill"] == "demo-skill"
    assert report["source"] == "override"
    assert set(report["hashes"]) == {"skill", "policy", "interfaces"}
    assert report["hashes"]["skill"].startswith("sha256:")
    assert report["hashes"]["interfaces"]["llm.default"].startswith("sha256:")
    assert report["hashes"]["interfaces"]["machine.worker"].startswith("sha256:")
    assert not (tmp_path / "skills" / "demo-skill" / ".last_audit.json").exists()


def test_task_7_machine_sidecars_are_consistently_windows_unsupported() -> None:
    sidecars = [
        MODULE_PATH.parent / "._check_drift_state.py.compute-hashes.blueprint.yaml",
        MODULE_PATH.parent / "._check_drift_state.py.drift-status.blueprint.yaml",
    ]

    for sidecar in sidecars:
        declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        assert declaration["platform_support"]["windows"] is False
        assert declaration["dependencies"]
        assert all(
            dependency["platforms"]["windows"] is False
            for dependency in declaration["dependencies"]
        )


def test_drift_status_sidecar_declares_timestamped_report_writer() -> None:
    sidecar = MODULE_PATH.parent / "._check_drift_state.py.drift-status.blueprint.yaml"
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))

    assert declaration["direct_io"]["writes"] == [
        {
            "medium": "local-filesystem",
            "access": "write",
            "system": "filesystem",
            "content": "drift-report",
            "format": "markdown",
            "path": "_build/*.md",
            "path_match": "glob",
            "sensitivity": "derived-private",
            "reason": "Write the timestamped human-readable drift report.",
        }
    ]
    assert declaration["owns_filesystem"] == [
        {
            "match": "regex",
            "path": (
                r"^_build/[0-9]{4}-[0-9]{2}-[0-9]{2}_"
                r"[0-9]{2}-[0-9]{2}-[0-9]{2}\.md$"
            ),
            "allowed_readers": [],
            "reason": "Drift status is the sole writer of its generated Markdown reports.",
        }
    ]


def test_compute_hashes_text_does_not_write_markdown_report(tmp_path: Path, capsys, monkeypatch) -> None:
    make_skill(tmp_path)
    build_dir = tmp_path / "report-build"
    monkeypatch.setattr(checker, "BUILD_DIR", build_dir)

    exit_code = checker.main(["compute-hashes", "demo-skill", "--repo-root", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "# Skill Hash Report" in output
    assert "| override | demo-skill | sha256:" in output
    assert not build_dir.exists()


def test_compute_hashes_fails_when_blueprint_is_missing(tmp_path: Path, capsys) -> None:
    write(tmp_path / "skills" / "plugin-skill" / "SKILL.md", "plugin skill\n")

    exit_code = checker.main(["compute-hashes", "plugin-skill", "--json", "--repo-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "plugin-skill: missing blueprint.yaml" in captured.err
