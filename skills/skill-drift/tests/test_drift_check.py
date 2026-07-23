from __future__ import annotations

import importlib.util
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_check_drift_state.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
TEST_ROOT = MODULE_PATH.parents[3] / "tests"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from officina.common.artifact_health import (
    CANONICAL_GRAPH_SCHEMA_INPUTS,
    POOLED_REVIEW_SCHEMA_INPUTS,
    blueprint_schema_hash,
    certify_graph,
    health_path_for_node,
)
from officina.common.audit_records import attach_record_digest
from officina.common.pooled_blueprint import (
    certify_pooled_review,
    pooled_review_health_path,
    pooled_review_path,
    render_pooled_review,
)
from v4_certification_fixtures import create_certified_fixture

SPEC = importlib.util.spec_from_file_location("skill_check_drift_state", MODULE_PATH)
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def _v4_certified_fixture(
    repo: Path,
    *,
    extra_modules: tuple[str, ...] = (),
):
    graph, _states, _commit, _public_key_root, _backend, _key = (
        create_certified_fixture(repo, extra_modules=extra_modules)
    )
    return graph


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def copy_schema_bundle(repo: Path) -> None:
    source_root = MODULE_PATH.parents[3] / "references" / "blueprint"
    target_root = repo / "references" / "blueprint"
    for relative_text in (
        *CANONICAL_GRAPH_SCHEMA_INPUTS,
        *POOLED_REVIEW_SCHEMA_INPUTS,
    ):
        source = source_root / relative_text
        write(target_root / relative_text, source.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def install_policy_manifest(repo: Path, patterns: list[str] | None = None) -> Path:
    manifest = repo / "skills" / "skill-drift" / "references" / "certification-basis-roots.json"
    if patterns is None:
        patterns = ["references/certification/fixture-policy.md"]
        write(repo / patterns[0], "fixture policy\n")
    write_json(manifest, patterns)
    return manifest


def materialize_canonical_policy_basis(repo: Path) -> Path:
    source_root = MODULE_PATH.parents[3]
    relative_manifest = Path(
        "skills/skill-drift/references/certification-basis-roots.json"
    )
    source_manifest = source_root / relative_manifest
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    assert isinstance(manifest, list)
    write_json(repo / relative_manifest, manifest)
    for pattern in manifest:
        assert isinstance(pattern, str)
        matches = (
            sorted(source_root.glob(pattern))
            if any(character in pattern for character in "*?[]")
            else [source_root / pattern]
        )
        for match in matches:
            sources = sorted(match.rglob("*")) if match.is_dir() else [match]
            for source in sources:
                if not source.is_file():
                    continue
                destination = repo / source.relative_to(source_root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    return repo / relative_manifest


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
    copy_schema_bundle(repo)
    return skill


def inline_typed_default(skill: Path) -> None:
    root = yaml.safe_load((skill / "blueprint.yaml").read_text(encoding="utf-8"))
    sidecar_path = skill / ".SKILL.md.blueprint.yaml"
    sidecar = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    root["default_interface"] = {
        key: value
        for key, value in sidecar.items()
        if key not in {"schema_version", "blueprint_type", "id", "binding"}
    }
    root["interfaces"] = []
    write(skill / "blueprint.yaml", yaml.safe_dump(root, sort_keys=False))
    sidecar_path.unlink()


def write_typed_health(repo: Path, skill_name: str = "demo-skill") -> None:
    graph = checker.load_validated_skill_blueprint_graph(
        repo / "skills" / skill_name,
        repo / "references" / "blueprint",
    )
    key = b"k" * 32
    key_path = repo / "skills" / "skill-certifier" / ".health-authentication-key"
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


def test_matching_inline_default_health_graph_is_current(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    inline_typed_default(skill)
    write_typed_health(tmp_path)

    report = checker.check_skill(source_for(tmp_path), "demo-skill")

    assert report.derived_status == "audit-current"
    assert report.concerns == []
    assert not (skill / ".SKILL.md.health.json").exists()


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
        tmp_path / "skills" / "skill-certifier" / ".health-authentication-key"
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


def test_policy_hash_changes_when_skill_certifier_changes(tmp_path: Path) -> None:
    materialize_canonical_policy_basis(tmp_path)
    write(tmp_path / "references" / "skill-standards" / "skill-guidelines.standard.yaml", "guidelines\n")
    write(tmp_path / "references" / "blueprint" / "schema.json", "{}\n")
    write(tmp_path / "references" / "blueprint" / "template.yaml", "template\n")
    write(tmp_path / "skills" / "skill-certifier" / "_rtx" / "_audit_certifier.py", "one\n")

    first = checker.compute_policy_hash(tmp_path)
    write(tmp_path / "skills" / "skill-certifier" / "_rtx" / "_audit_certifier.py", "two\n")
    second = checker.compute_policy_hash(tmp_path)

    assert first != second


def test_policy_hash_changes_when_canonical_skill_guidelines_change(tmp_path: Path) -> None:
    materialize_canonical_policy_basis(tmp_path)
    guidelines = tmp_path / "references" / "skill-standards" / "skill-guidelines.standard.yaml"
    write(guidelines, "one\n")

    first = checker.compute_policy_hash(tmp_path)
    write(guidelines, "two\n")
    second = checker.compute_policy_hash(tmp_path)

    assert first != second


def test_policy_hash_ignores_explanatory_blueprint_document(tmp_path: Path) -> None:
    materialize_canonical_policy_basis(tmp_path)
    document = tmp_path / "docs" / "skill-blueprints.md"
    write(document, "one\n")

    first = checker.compute_policy_hash(tmp_path)
    write(document, "two\n")
    second = checker.compute_policy_hash(tmp_path)

    assert first == second


def test_certification_basis_hash_canonicalizes_parsed_node_policy(tmp_path: Path) -> None:
    install_policy_manifest(
        tmp_path, ["references/certification/node-hash-policy.yaml"]
    )
    policy = tmp_path / "references" / "certification" / "node-hash-policy.yaml"
    write(
        policy,
        "policy_version: 1\n"
        "path_syntax: gitignore\n"
        "starting_set: git-tracked-directly-owned-regular-files\n"
        "rules:\n  - action: exclude\n    pattern: '**/*.log'\n",
    )

    first = checker.compute_certification_basis_hash(tmp_path)
    write(policy, policy.read_text(encoding="utf-8") + "# explanation only\n")
    comments_changed = checker.compute_certification_basis_hash(tmp_path)
    write(
        policy,
        policy.read_text(encoding="utf-8").replace("exclude", "include"),
    )
    semantics_changed = checker.compute_certification_basis_hash(tmp_path)

    assert comments_changed == first
    assert semantics_changed != first


def test_live_certification_basis_covers_every_canonical_graph_schema_input() -> None:
    repo_root = MODULE_PATH.parents[3]
    covered = set(checker.certification_basis_paths(repo_root))

    assert {
        repo_root / "references" / "blueprint" / relative
        for relative in CANONICAL_GRAPH_SCHEMA_INPUTS
    } <= covered


@pytest.mark.parametrize(
    "relative",
    [
        "v2/skill.schema.json",
        "conformance-operations/filesystem.schema.json",
        "schema.annotated-draft.json",
    ],
)
def test_certification_basis_hash_tracks_nested_schema_families(
    tmp_path: Path,
    relative: str,
) -> None:
    materialize_canonical_policy_basis(tmp_path)
    copy_schema_bundle(tmp_path)
    first = checker.compute_certification_basis_hash(tmp_path)

    path = tmp_path / "references" / "blueprint" / relative
    write(path, path.read_text(encoding="utf-8") + "\n")

    assert checker.compute_certification_basis_hash(tmp_path) != first


@pytest.mark.parametrize("module_name", ["atomic_files.py", "git_provenance.py"])
def test_committed_shared_trust_boundary_change_invalidates_current_record(
    tmp_path: Path,
    module_name: str,
) -> None:
    make_typed_skill(tmp_path)
    materialize_canonical_policy_basis(tmp_path)
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
    assert before.derived_status == "audit-current", before.concerns

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


def test_installed_sources_ignore_codex_plugin_cache_without_active_registry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    make_skill(codex_home, "direct-skill")
    make_skill(codex_home / "plugins" / "cache" / "market" / "stale" / "1.0", "stale-skill")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    sources = checker.observed_skill_sources()

    assert [(source.source, source.skills_root) for source in sources] == [
        ("codex", (codex_home / "skills").resolve())
    ]


def test_installed_sources_use_only_claude_registry_named_plugin_versions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    active = claude_home / "plugins" / "cache" / "market" / "demo" / "2.0"
    stale = claude_home / "plugins" / "cache" / "market" / "demo" / "1.0"
    make_skill(active, "active-skill")
    make_skill(stale, "stale-skill")
    write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@market": [
                    {"version": "2.0", "scope": "user", "installPath": str(active)}
                ]
            },
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    sources = checker.observed_skill_sources()

    assert [
        (source.source, source.skills_root, source.plugin_id, source.plugin_version)
        for source in sources
    ] == [
        ("claude", (active / "skills").resolve(), "demo@market", "2.0")
    ]


def test_installed_source_deduplication_rejects_conflicting_plugin_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin" / "skills"
    root.mkdir(parents=True)
    first = checker.SkillSource(
        source="claude",
        package_root=root.parent,
        skills_root=root,
        plugin_id="first@market",
        plugin_version="1",
    )
    second = checker.SkillSource(
        source="claude",
        package_root=root.parent,
        skills_root=root,
        plugin_id="second@market",
        plugin_version="2",
    )

    with pytest.raises(checker.SkillSourceDiscoveryError, match="metadata conflict"):
        checker.dedupe_skill_sources([first, second])


def test_active_claude_plugin_with_malformed_version_metadata_fails_with_exact_remediation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    active = claude_home / "plugins" / "cache" / "market" / "demo" / "unknown"
    make_skill(active, "active-skill")
    write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@market": [
                    {"version": 7, "scope": "user", "installPath": str(active)}
                ]
            },
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    with pytest.raises(checker.SkillSourceDiscoveryError) as captured:
        checker.observed_skill_sources()

    message = str(captured.value)
    assert "demo@market" in message
    assert '"version": 7' in message
    assert "repair installed_plugins.json or pass --skill-root, --skills-root, or --repo-root" in message


@pytest.mark.parametrize(
    "command",
    [
        ["status", "--all", "--json"],
        ["compute-hashes", "--json"],
    ],
)
def test_active_plugin_legacy_blueprint_never_reaches_legacy_execution(
    tmp_path: Path,
    monkeypatch,
    capsys,
    command: list[str],
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    active = claude_home / "plugins" / "cache" / "market" / "demo" / "7"
    make_skill(active, "active-skill")
    write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@market": [
                    {
                        "version": "7",
                        "scope": "user",
                        "installPath": str(active),
                    }
                ]
            },
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))
    monkeypatch.setattr(
        checker,
        "compute_audit_hashes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("active plugin reached legacy execution")
        ),
    )

    exit_code = checker.main(command)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'plugin "demo@market" version "7"' in captured.err
    assert str(active) in captured.err
    assert (
        "repair installed_plugins.json or pass --skill-root, --skills-root, "
        "or --repo-root"
    ) in captured.err


def test_active_v4_plugin_ignores_stale_cached_legacy_version(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    active = claude_home / "plugins" / "cache" / "market" / "demo" / "2"
    stale = claude_home / "plugins" / "cache" / "market" / "demo" / "1"
    _v4_certified_fixture(active)
    make_skill(stale, "stale-skill")
    write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@market": [
                    {
                        "version": "2",
                        "scope": "user",
                        "installPath": str(active),
                    }
                ]
            },
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    exit_code = checker.main(["compute-hashes", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    observed = {report["skill"] for report in payload["skills"]}
    assert "demo-skill" in observed
    assert "stale-skill" not in observed
    assert "stale-skill" not in captured.out


def test_malformed_claude_plugin_registry_fails_with_remediation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {"version": 1, "plugins": {}},
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    exit_code = checker.main(["status", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "installed_plugins.json" in captured.err
    assert "--skill-root, --skills-root, or --repo-root" in captured.err


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

    first = checker.compute_policy_hash(tmp_path)
    write(tmp_path / "target-policy.md", "changed target policy\n")
    second = checker.compute_policy_hash(tmp_path)

    assert first != second


def test_target_policy_manifest_symlink_is_rejected(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path, "demo-skill")
    manifest = tmp_path / "skills" / "skill-drift" / "references" / "certification-basis-roots.json"
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
    key = tmp_path / "skills" / "skill-certifier" / ".health-authentication-key"
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


def test_v4_drift_source_is_consistently_windows_unsupported() -> None:
    source = MODULE_PATH.parents[1] / "blueprints" / "rtx-check-drift-state.yaml"
    declaration = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert declaration["platform_support"]["windows"] is False
    assert declaration["runtime_dependencies"]
    assert all(
        dependency["platforms"]["windows"] is False
        for dependency in declaration["runtime_dependencies"]
    )


def test_v4_drift_source_declares_timestamped_report_writer() -> None:
    source = MODULE_PATH.parents[1] / "blueprints" / "rtx-check-drift-state.yaml"
    declaration = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert "cryptography" not in {
        dependency["name"] for dependency in declaration["runtime_dependencies"]
        if dependency["kind"] == "python-package"
    }
    audit_records = (
        MODULE_PATH.parents[3]
        / "src"
        / "officina"
        / "common"
        / "blueprints"
        / "audit-records.yaml"
    )
    common_declaration = yaml.safe_load(audit_records.read_text(encoding="utf-8"))
    assert "cryptography" in {
        dependency["name"]
        for dependency in common_declaration["runtime_dependencies"]
        if dependency["kind"] == "python-package"
    }

    interface = declaration["interfaces"][
        "skill-drift.source.rtx-check-drift-state.interface.drift-status"
    ]
    assert interface["contract"]["direct_io"]["writes"][0] == {
        "id": "write-1",
        "medium": "local-filesystem",
        "access": "write",
        "system": "filesystem",
        "content": "drift-report",
        "formats": ["markdown"],
        "path": "_build/*.md",
        "path_match": "glob",
        "sensitivity": "derived-private",
        "reason": "Write the timestamped human-readable drift report.",
    }
    assert declaration["platform_support"]["windows"] is False
    module = yaml.safe_load(
        (MODULE_PATH.parents[1] / "blueprint.yaml").read_text(encoding="utf-8")
    )
    assert module["authority"]["owns_filesystem"] == [
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


def test_legacy_basis_wrappers_delegate_to_the_shared_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_paths = (tmp_path / "canonical-one", tmp_path / "canonical-two")
    calls: list[tuple[str, Path]] = []

    def shared_hash(root: Path) -> str:
        calls.append(("hash", root))
        return "sha256:" + "a" * 64

    def shared_resolve(root: Path) -> tuple[Path, ...]:
        calls.append(("paths", root))
        return shared_paths

    monkeypatch.setattr(checker, "compute_shared_certification_basis_hash", shared_hash)
    monkeypatch.setattr(checker, "resolve_shared_certification_basis_paths", shared_resolve)

    assert checker.compute_certification_basis_hash(tmp_path) == "sha256:" + "a" * 64
    assert checker.certification_basis_paths(tmp_path) == shared_paths
    assert calls == [("hash", tmp_path.resolve()), ("paths", tmp_path.resolve())]


def test_private_v4_drift_is_public_key_only_read_only_and_exact(tmp_path: Path) -> None:
    graph = _v4_certified_fixture(tmp_path)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    report = checker._check_v4_repository(
        tmp_path,
        target_node_ids=("demo-skill.source.gateway",),
        public_key_root=tmp_path / "public-keys",
    )

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert set(report.nodes) == {"demo-skill.source.gateway"}
    assert report.nodes["demo-skill.source.gateway"].current
    assert before == after
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "load_or_create_certificate_signing_key" not in source
    assert "sign_certificate_payload" not in source
    assert "rotate_certificate_signing_key" not in source


def test_public_v4_status_and_hash_routes_use_certificate_state(
    tmp_path: Path,
) -> None:
    graph = _v4_certified_fixture(tmp_path)
    public_key_root = checker.certificate_public_key_root(tmp_path)
    public_key_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(tmp_path / "public-keys", public_key_root)
    source = source_for(tmp_path)

    status = checker.check_skill(source, "demo-skill")
    hashes = checker.hash_report_for_skill(source, "demo-skill")

    owned = {
        node_id
        for node_id, node in graph.nodes.items()
        if node.skill_root == tmp_path / "skills" / "demo-skill"
    }
    assert status.derived_status == "audit-current"
    assert status.concerns == []
    assert set(status.current_hashes["nodes"]) == owned
    assert set(status.recorded_hashes["nodes"]) == owned
    assert set(hashes.hashes["nodes"]) == owned
    assert all(
        value["node_hash"].startswith("sha256:")
        for value in hashes.hashes["nodes"].values()
    )


def test_private_v4_drift_propagates_explicit_non_atomic_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _v4_certified_fixture(tmp_path)
    real_evaluate = checker.evaluate_certificate_currentness
    real_resolve_basis = checker.resolve_shared_certification_basis_paths
    real_compute_basis = checker.compute_shared_certification_basis_hash
    observed: list[str] = []

    def resolve_basis_with_fallback(*args: object, **kwargs: object):
        assert kwargs["allow_non_atomic"] is True
        observed.append("basis-paths")
        return real_resolve_basis(*args, **kwargs)

    def compute_basis_with_fallback(*args: object, **kwargs: object):
        assert kwargs["allow_non_atomic"] is True
        observed.append("basis-hash")
        return real_compute_basis(*args, **kwargs)

    def evaluate_with_fallback(*args: object, **kwargs: object):
        assert kwargs["allow_non_atomic"] is True
        observed.append("evaluate")
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        checker,
        "resolve_shared_certification_basis_paths",
        resolve_basis_with_fallback,
    )
    monkeypatch.setattr(
        checker,
        "compute_shared_certification_basis_hash",
        compute_basis_with_fallback,
    )
    monkeypatch.setattr(
        checker, "evaluate_certificate_currentness", evaluate_with_fallback
    )

    report = checker._check_v4_repository(
        tmp_path,
        target_node_ids=("demo-skill.source.gateway",),
        public_key_root=tmp_path / "public-keys",
        allow_non_atomic=True,
    )

    assert report.nodes["demo-skill.source.gateway"].current
    assert observed == ["basis-paths", "basis-hash", "evaluate"]


def test_private_v4_drift_reports_precise_suspect_log_concern(tmp_path: Path) -> None:
    graph = _v4_certified_fixture(tmp_path)
    path = checker.certificate_log_path(graph.nodes["demo-skill.source.gateway"])
    with path.open("ab") as stream:
        stream.write(b"{}\n")

    report = checker._check_v4_repository(
        tmp_path,
        target_node_ids=("demo-skill.source.gateway",),
        public_key_root=tmp_path / "public-keys",
    )

    status = report.nodes["demo-skill.source.gateway"]
    assert not status.current
    assert status.concerns == ("suspect-certificate-log",)


def test_private_v4_drift_derives_canonical_basis_and_certifier(tmp_path: Path) -> None:
    _v4_certified_fixture(tmp_path)
    policy = tmp_path / "references" / "certification" / "node-hash-policy.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("**/*.log", "**/*.changed"),
        encoding="utf-8",
    )

    report = checker._check_v4_repository(
        tmp_path,
        target_node_ids=("demo-skill.source.gateway",),
        public_key_root=tmp_path / "public-keys",
    )

    status = report.nodes["demo-skill.source.gateway"]
    assert not status.current
    assert "certification-basis-mismatch" in status.concerns


def test_private_v4_drift_rejects_mode_only_source_commit_drift(
    tmp_path: Path,
) -> None:
    _v4_certified_fixture(tmp_path)
    gateway = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    gateway.chmod(gateway.stat().st_mode | stat.S_IXUSR)

    report = checker._check_v4_repository(
        tmp_path,
        target_node_ids=("demo-skill.source.gateway",),
        public_key_root=tmp_path / "public-keys",
    )

    status = report.nodes["demo-skill.source.gateway"]
    assert not status.current
    assert "source-commit-input-mismatch" in status.concerns


def test_private_v4_drift_keeps_unrelated_content_isolated(
    tmp_path: Path,
) -> None:
    _v4_certified_fixture(tmp_path, extra_modules=("unrelated-skill",))
    unrelated = tmp_path / "skills" / "unrelated-skill" / "SKILL.md"
    unrelated.chmod(unrelated.stat().st_mode | stat.S_IXUSR)

    report = checker._check_v4_repository(
        tmp_path,
        target_node_ids=("demo-skill.source.gateway",),
        public_key_root=tmp_path / "public-keys",
    )

    assert report.nodes["demo-skill.source.gateway"].current
