from __future__ import annotations

import importlib.util
import json
import shutil
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import yaml
import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_audit_certifier.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
TEST_ROOT = MODULE_PATH.parents[3] / "tests"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from officina.common import artifact_health, git_provenance
from officina.common.audit_records import (
    certificate_entry_hash,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    record_authentication_matches,
    record_digest_matches,
    rotate_certificate_signing_key,
)
from officina.common.artifact_health import (
    CANONICAL_GRAPH_SCHEMA_INPUTS,
    POOLED_REVIEW_SCHEMA_INPUTS,
)
from officina.common.git_provenance import check_commit_readiness as real_check_commit_readiness
from v4_certification_fixtures import (
    MemorySecretBackend as _MemorySecretBackend,
    contract as _v4_contract,
    create_v4_repository,
    write_yaml as _write_v4_yaml,
)

SPEC = importlib.util.spec_from_file_location("skill_audit_certifier", MODULE_PATH)
certifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = certifier
SPEC.loader.exec_module(certifier)


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


def make_skill(repo: Path, name: str = "demo-skill", *, implicit_uncovered: bool = False) -> Path:
    skill = repo / "skills" / name
    instruction = "demo skill\n"
    if implicit_uncovered:
        instruction += "Look under tools for executables.\n"
        write(skill / "tools" / "runner", "#!/bin/sh\n")
    write(skill / "SKILL.md", instruction)
    write(skill / "_rtx" / "_audit_worker.py", "VALUE = 'one'\n")
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
                "      invocation:",
                "        kind: python_machine_interface",
                "        entrypoint: _rtx/_audit_worker.py:Interface",
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
    skill = repo / "skills" / name
    write(skill / "SKILL.md", "demo skill\n")
    write(
        skill / "blueprint.yaml",
        "\n".join(
            [
                "schema_version: 2",
                "blueprint_type: skill",
                f"id: {name}",
                "category: development-assistant",
                "role: automation",
                "kind: tool",
                "interfaces:",
                f"  - interface: {name}.llm.default",
                "    version: 1",
                "    blueprint:",
                "      base: skill-root",
                "      path: .SKILL.md.blueprint.yaml",
                "",
            ]
        ),
    )
    write(
        skill / ".SKILL.md.blueprint.yaml",
        "\n".join(
            [
                "schema_version: 2",
                "blueprint_type: llm-interface",
                f"id: {name}.llm.default",
                "version: 1",
                "description: Primary.",
                "binding:",
                "  kind: instruction-file",
                "  path: SKILL.md",
                "behavior_sources: []",
                "direct_io:",
                "  reads: []",
                "  writes: []",
                "  network: []",
                "owns_filesystem: []",
                "",
            ]
        ),
    )
    copy_schema_bundle(repo)
    (repo / "skills" / "skill-audit").mkdir(parents=True, exist_ok=True)
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


def make_shared_source_consumers(repo: Path) -> tuple[Path, Path, Path]:
    consumers = (
        make_typed_skill(repo, "first-skill"),
        make_typed_skill(repo, "second-skill"),
    )
    write(repo / "references" / "shared.md", "Shared policy.\n")
    write(
        repo / "references" / ".shared.md.blueprint.yaml",
        yaml.safe_dump(
            {
                "schema_version": 2,
                "blueprint_type": "behavior-source",
                "id": "references.source.shared",
                "version": 1,
                "description": "Shared policy.",
                "binding": {"kind": "file", "path": "references/shared.md"},
                "content": "config",
                "format": "markdown",
                "uses_behavior_sources": [],
            },
            sort_keys=False,
        ),
    )
    for skill in consumers:
        sidecar = skill / ".SKILL.md.blueprint.yaml"
        declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        declaration["behavior_sources"] = [
            {
                "source": "references.source.shared",
                "version": 1,
                "blueprint": {
                    "base": "repository-root",
                    "path": "references/.shared.md.blueprint.yaml",
                },
                "reason": "Uses shared policy.",
            }
        ]
        write(sidecar, yaml.safe_dump(declaration, sort_keys=False))
    return consumers[0], consumers[1], repo / "references" / ".shared.md.health.json"


def make_commit_backed(repo: Path) -> None:
    source_root = MODULE_PATH.parents[3]
    copy_schema_bundle(repo)
    policy_files = [
        "skills/skill-drift/references/certification-basis-roots.json",
        "skills/skill-audit/_rtx/_audit_certifier.py",
        "src/officina/common/artifact_health.py",
        "src/officina/common/atomic_files.py",
        "src/officina/common/audit_records.py",
        "src/officina/common/blueprint_graph.py",
        "src/officina/common/blueprint_template.py",
        "src/officina/common/git_provenance.py",
        "src/officina/common/pooled_blueprint.py",
    ]
    for relative in policy_files:
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    manifest = json.loads(
        (source_root / "skills" / "skill-drift" / "references" / "certification-basis-roots.json").read_text(
            encoding="utf-8"
        )
    )
    for pattern in manifest:
        matches = (
            sorted(source_root.glob(pattern))
            if any(char in pattern for char in "*?[]")
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
    write(
        repo / ".gitignore",
        "\n".join(
            [
                "**/.last_audit.json",
                "**/.*.health.json",
                "**/.pooled-blueprint-review.yaml",
                "**/.health-authentication-key",
                "",
            ]
        ),
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)


def target_for(repo: Path, skill_root: Path) -> object:
    return certifier.TargetHash(
        skill=skill_root.name,
        source="test",
        package_root=repo,
        skills_root=skill_root.parent,
        skill_root=skill_root,
        hashes={"skill": "sha256:skill", "policy": "sha256:policy", "interfaces": {}},
    )


def finding_kinds(findings: list[object]) -> set[str]:
    return {finding.kind for finding in findings}


class FakeDispatcher:
    def __init__(
        self,
        repo: Path,
        *,
        post_write_current: bool = True,
        failing_status_skill: str | None = None,
        status_skill_override: str | None = None,
        hash_skill_roots: Sequence[Path] | None = None,
    ) -> None:
        self.repo = repo
        self.post_write_current = post_write_current
        self.failing_status_skill = failing_status_skill
        self.status_skill_override = status_skill_override
        self.hash_skill_roots = tuple(hash_skill_roots) if hash_skill_roots is not None else None
        self.calls: list[tuple[str, list[str]]] = []

    def dispatch(
        self,
        key: str,
        *,
        args: Sequence[str] | None = None,
        stdin: str | bytes | None = None,
        timeout: float | None = None,
        capture_output: bool = True,
        check: bool = False,
        text: bool | None = None,
        repo_root: Path | None = None,
    ) -> Any:
        del stdin, timeout, capture_output, check, text, repo_root
        argv = list(args or [])
        self.calls.append((key, argv))
        if key == "sync-blueprints":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if key == "compute-hashes":
            skill_roots = (
                self.hash_skill_roots
                if self.hash_skill_roots is not None
                else (self._target_skill_root(argv),)
            )
            payload = {
                "schema_version": 1,
                "computed_at": "2026-07-11T12:00:00-04:00",
                "skills": [
                    {
                        "skill": skill_root.name,
                        "source": "path" if "--skill-root" in argv else "test",
                        "package_root": str(self.repo),
                        "skills_root": str(skill_root.parent),
                        "hashes": {
                            "skill": "sha256:" + "3" * 64,
                            "policy": "sha256:" + "1" * 64,
                            "interfaces": {"llm.default": "sha256:" + "4" * 64},
                        },
                    }
                    for skill_root in skill_roots
                ],
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if key == "drift-status":
            skill_root = self._target_skill_root(argv)
            current = self.post_write_current and skill_root.name != self.failing_status_skill
            status = "audit-current" if current else "audit-stale"
            payload = {
                "skills": [
                    {
                        "skill": self.status_skill_override or skill_root.name,
                        "derived_status": status,
                    }
                ]
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        raise AssertionError(f"unexpected dispatch key {key}")

    def _target_skill_root(self, argv: list[str]) -> Path:
        if "--skill-root" in argv:
            return Path(argv[argv.index("--skill-root") + 1])
        targets = [arg for arg in argv[1:] if not arg.startswith("--")]
        if targets:
            return self.repo / "skills" / targets[0]
        return self.repo / "skills" / "demo-skill"


def outcomes_by_node(outcome: object) -> dict[str, object]:
    return {node.node_id: node for node in outcome.nodes}


def test_dirty_leaf_finishes_semantics_without_stamp(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    write(skill / "SKILL.md", "changed but semantically valid\n")

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
        timestamp="2026-07-13T12:00:00-04:00",
    )

    leaf = outcomes_by_node(outcomes[0])["demo-skill.llm.default"]
    assert leaf.semantic_status == "passed"
    assert not leaf.stamp_worthy
    assert leaf.stamp_status == "not-written"
    assert not (skill / ".SKILL.md.health.json").exists()
    assert outcomes[0].semantic_status == "passed"
    assert outcomes[0].stamp_status == "not-written"


def test_healthy_child_is_reused_without_git_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)
    certifier.certify(
        fake,
        targets=[str(skill)],
        skip_mechanical=True,
        timestamp="2026-07-13T12:00:00-04:00",
    )
    child_path = skill / ".SKILL.md.health.json"
    first_bytes = child_path.read_bytes()
    checked_path_sets: list[set[Path]] = []

    def readiness_spy(snapshot: object, input_paths: Sequence[Path], expected_hashes: object) -> object:
        checked_path_sets.append(set(input_paths))
        return real_check_commit_readiness(snapshot, input_paths, expected_hashes)

    monkeypatch.setattr(certifier, "check_commit_readiness", readiness_spy, raising=False)

    _evidence, outcomes = certifier.certify(
        fake,
        targets=[str(skill)],
        skip_mechanical=True,
        timestamp="2026-07-13T13:00:00-04:00",
    )

    leaf = outcomes_by_node(outcomes[0])["demo-skill.llm.default"]
    assert leaf.stamp_status == "reused"
    assert child_path.read_bytes() == first_bytes
    assert not any(skill / "SKILL.md" in paths for paths in checked_path_sets)


def test_sequential_consumer_audits_reuse_shared_health_and_keep_both_roots_healthy(
    tmp_path: Path,
) -> None:
    first, second, shared_health = make_shared_source_consumers(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)

    _evidence, first_outcomes = certifier.certify(
        fake,
        targets=[str(first)],
        skip_mechanical=True,
        timestamp="2026-07-13T12:00:00-04:00",
    )
    shared_bytes = shared_health.read_bytes()
    assert outcomes_by_node(first_outcomes[0])["references.source.shared"].stamp_status == "written"

    _evidence, second_outcomes = certifier.certify(
        fake,
        targets=[str(second)],
        skip_mechanical=True,
        timestamp="2026-07-13T13:00:00-04:00",
    )
    assert outcomes_by_node(second_outcomes[0])["references.source.shared"].stamp_status == "reused"
    assert shared_health.read_bytes() == shared_bytes

    for consumer in (first, second):
        _evidence, rechecked = certifier.certify(
            fake,
            targets=[str(consumer)],
            skip_mechanical=True,
            timestamp="2026-07-13T14:00:00-04:00",
        )
        nodes = outcomes_by_node(rechecked[0])
        assert nodes[consumer.name].health_status == "healthy"
        assert nodes["references.source.shared"].stamp_status == "reused"
        assert shared_health.read_bytes() == shared_bytes


def test_second_target_failure_keeps_first_and_reports_partial_success(
    tmp_path: Path,
    capsys,
) -> None:
    first = make_typed_skill(tmp_path, "first-skill")
    second = make_typed_skill(tmp_path, "second-skill")
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path, failing_status_skill="second-skill")

    exit_code = certifier.main(
        [
            "certify",
            str(first),
            str(second),
            "--skip-mechanical",
            "--timestamp",
            "2026-07-13T12:00:00-04:00",
            "--json",
        ],
        dispatcher=fake,
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["certified"][0]["status"] == "audit-current"
    assert payload["certified"][0]["skill"] == "first-skill"
    assert payload["failed"][0]["skill"] == "second-skill"
    assert (first / ".last_audit.json").is_file()


def test_certify_writes_commit_backed_audit_record_for_skill_name(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)

    _mechanical, outcomes = certifier.certify(
        fake,
        targets=["demo-skill"],
        skip_mechanical=True,
        timestamp="2026-07-11T12:00:00-04:00",
    )

    record = json.loads((skill / ".last_audit.json").read_text(encoding="utf-8"))
    assert outcomes[0].skill == "demo-skill"
    assert "writer" not in record
    assert "schema_version" not in record
    assert record["timestamp"] == "2026-07-11T12:00:00-04:00"
    assert record["audit_policy_hash"] == "sha256:" + "1" * 64
    assert record["git_commit"] == record["source"]["commit"]
    assert record["source"]["vcs"] == "git"
    assert record["hashes"]["skill"] == "sha256:" + "3" * 64
    assert "policy" not in record["hashes"]
    assert record_digest_matches(record)


def test_certify_resolves_exact_skill_root_target(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)

    certifier.certify(fake, targets=[str(skill)], skip_mechanical=True)

    assert ("compute-hashes", ["compute-hashes", "--skill-root", str(skill.resolve()), "--json"]) in fake.calls
    assert (skill / ".last_audit.json").is_file()


def test_semantic_findings_return_structured_failure_without_write(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, implicit_uncovered=True)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)

    _evidence, outcomes = certifier.certify(
        fake,
        targets=["demo-skill"],
        skip_mechanical=True,
    )

    assert outcomes[0].semantic_status == "failed"
    assert outcomes[0].stamp_status == "not-written"
    assert not (skill / ".last_audit.json").exists()


def test_generated_dispatcher_examples_do_not_count_as_execution_logic(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write(
        skill / "SKILL.md",
        "\n".join(
            [
                "---",
                "name: demo-skill",
                "description: Use when testing.",
                "---",
                "<!-- BEGIN BLUEPRINT INTERFACES -->",
                "  - `dispatcher --caller-skill demo-skill demo-skill.machine.worker worker`",
                "<!-- END BLUEPRINT INTERFACES -->",
                "Use the worker interface when the user asks for work.",
                "",
            ]
        ),
    )

    findings = certifier.semantic_findings(target_for(tmp_path, skill))

    assert "unencapsulated-execution" not in finding_kinds(findings)


def test_hand_authored_command_instruction_is_unencapsulated_execution(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write(skill / "SKILL.md", "When asked, run `python3 scripts/do_work.py`.\n")

    findings = certifier.semantic_findings(target_for(tmp_path, skill))

    assert "unencapsulated-execution" in finding_kinds(findings)


def test_hand_authored_script_path_is_unencapsulated_execution(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write(skill / "SKILL.md", "Use scripts/do-work.sh for the operation.\n")

    findings = certifier.semantic_findings(target_for(tmp_path, skill))

    assert "unencapsulated-execution" in finding_kinds(findings)


def test_interface_orchestration_wording_is_allowed(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    write(skill / "SKILL.md", "Use the worker interface when the user asks for work.\n")

    findings = certifier.semantic_findings(target_for(tmp_path, skill))

    assert "unencapsulated-execution" not in finding_kinds(findings)


def test_legacy_post_write_failure_keeps_new_atomic_record(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    make_commit_backed(tmp_path)
    old_record = {"schema_version": 1, "skill": "demo-skill", "hashes": {}}
    write(skill / ".last_audit.json", json.dumps(old_record) + "\n")
    fake = FakeDispatcher(tmp_path, post_write_current=False)

    _evidence, outcomes = certifier.certify(
        fake,
        targets=["demo-skill"],
        skip_mechanical=True,
    )

    record = json.loads((skill / ".last_audit.json").read_text(encoding="utf-8"))
    assert record != old_record
    assert record_digest_matches(record)
    assert outcomes[0].stamp_status == "failed"


def test_typed_certification_writes_authenticated_graph_and_pooled_health(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)

    _mechanical, outcomes = certifier.certify(
        fake,
        targets=["demo-skill"],
        skip_mechanical=True,
        timestamp="2026-07-13T12:00:00-04:00",
    )

    key = (tmp_path / "skills" / "skill-audit" / ".health-authentication-key").read_bytes()
    root = json.loads((skill / ".last_audit.json").read_text(encoding="utf-8"))
    interface = json.loads((skill / ".SKILL.md.health.json").read_text(encoding="utf-8"))
    pooled = json.loads(
        (skill / ".pooled-blueprint-review.health.json").read_text(encoding="utf-8")
    )

    assert outcomes_by_node(outcomes[0])["demo-skill"].record_path == skill / ".last_audit.json"
    assert root["record_type"] == "skill-health"
    assert interface["record_type"] == "node-health"
    assert record_authentication_matches(root, key)
    assert record_authentication_matches(interface, key)
    assert record_authentication_matches(pooled, key)
    assert (skill / ".pooled-blueprint-review.yaml").is_file()


def test_inline_default_certification_uses_only_skill_health_identity(
    tmp_path: Path,
) -> None:
    skill = make_typed_skill(tmp_path)
    inline_typed_default(skill)
    make_commit_backed(tmp_path)

    _mechanical, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=["demo-skill"],
        skip_mechanical=True,
        timestamp="2026-07-13T12:00:00-04:00",
    )

    assert outcomes_by_node(outcomes[0]).keys() == {"demo-skill"}
    assert (skill / ".last_audit.json").is_file()
    assert not (skill / ".SKILL.md.health.json").exists()
    pooled = yaml.safe_load(
        (skill / ".pooled-blueprint-review.yaml").read_text(encoding="utf-8")
    )
    assert [node["id"] for node in pooled["nodes"]] == [
        "demo-skill",
        "demo-skill.llm.default",
    ]
    assert pooled["nodes"][0]["health"] == pooled["nodes"][1]["health"]


def test_typed_certification_uses_target_installation_schema_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)
    observed: dict[str, Path] = {}
    real_blueprint_schema_hash = certifier.blueprint_schema_hash

    def capture_schema_root(schema_root: Path) -> str:
        observed["schema_root"] = Path(schema_root)
        return real_blueprint_schema_hash(schema_root)

    monkeypatch.setattr(certifier, "blueprint_schema_hash", capture_schema_root)

    certifier.certify(
        fake,
        targets=["demo-skill"],
        skip_mechanical=True,
        timestamp="2026-07-13T12:00:00-04:00",
    )

    assert observed["schema_root"] == tmp_path / "references" / "blueprint"


def test_typed_post_write_failure_keeps_completed_generated_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path, post_write_current=False)

    _evidence, outcomes = certifier.certify(
        fake,
        targets=["demo-skill"],
        skip_mechanical=True,
    )

    assert outcomes[0].stamp_status == "failed"
    assert (skill / ".last_audit.json").exists()
    assert (skill / ".SKILL.md.health.json").exists()
    assert (skill / ".pooled-blueprint-review.yaml").exists()
    assert (skill / ".pooled-blueprint-review.health.json").exists()


def test_typed_certification_rejects_preexisting_health_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    (skill / ".last_audit.json").symlink_to(victim)
    fake = FakeDispatcher(tmp_path)
    _evidence, outcomes = certifier.certify(
        fake,
        targets=["demo-skill"],
        skip_mechanical=True,
    )

    assert outcomes[0].stamp_status == "failed"
    assert victim.read_text(encoding="utf-8") == "keep me\n"
    assert (skill / ".last_audit.json").is_symlink()


def test_non_git_typed_target_finishes_semantics_without_stamp(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].semantic_status == "passed"
    assert outcomes[0].stamp_status == "not-written"
    assert all(node.stamp_status == "not-written" for node in outcomes[0].nodes)
    assert not (skill / ".last_audit.json").exists()


def test_dirty_policy_gate_marks_every_refresh_node_not_written(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    policy_implementation = tmp_path / "src" / "officina" / "common" / "artifact_health.py"
    policy_implementation.write_text(
        policy_implementation.read_text(encoding="utf-8") + "\n# dirty\n",
        encoding="utf-8",
    )

    evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    gate = next(item for item in evidence if item.name == "policy-readiness")
    assert not gate.passed
    assert all(node.stamp_status == "not-written" for node in outcomes[0].nodes)
    assert all("policy-not-commit-backed" in node.reasons for node in outcomes[0].nodes)
    assert outcomes[0].semantic_status == "passed"


def test_dirty_manifest_declared_policy_document_blocks_all_new_stamps(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    policy_document = tmp_path / "docs" / "audit_and_drift.md"
    policy_document.write_text(
        policy_document.read_text(encoding="utf-8") + "\nDirty policy text.\n",
        encoding="utf-8",
    )

    evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert not next(item for item in evidence if item.name == "policy-readiness").passed
    assert all(node.stamp_status == "not-written" for node in outcomes[0].nodes)
    assert all("policy-not-commit-backed" in node.reasons for node in outcomes[0].nodes)


def test_missing_target_schema_finishes_semantics_without_key_or_stamp(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    (tmp_path / "references" / "blueprint" / "health.schema.json").unlink()
    key_path = tmp_path / "skills" / "skill-audit" / ".health-authentication-key"

    evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert not next(item for item in evidence if item.name == "policy-readiness").passed
    assert outcomes[0].semantic_status == "passed"
    assert all(node.stamp_status == "not-written" for node in outcomes[0].nodes)
    assert all("policy-not-commit-backed" in node.reasons for node in outcomes[0].nodes)
    assert not key_path.exists()


def test_invalid_target_schema_fails_before_certification(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    (tmp_path / "references" / "blueprint" / "health.schema.json").write_text(
        "{ invalid json\n",
        encoding="utf-8",
    )

    evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert evidence == []
    assert outcomes[0].semantic_status == "failed"
    assert outcomes[0].stamp_status == "failed"
    assert any("cannot load schema" in reason for reason in outcomes[0].nodes[0].reasons)
    assert not (skill / ".last_audit.json").exists()


def test_moving_head_stops_further_node_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    calls = 0

    def moving_head(_snapshot: object) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    monkeypatch.setattr(certifier, "snapshot_head_matches", moving_head)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    leaf = outcomes_by_node(outcomes[0])["demo-skill.llm.default"]
    assert leaf.stamp_status == "not-written"
    assert leaf.reasons == ("head-changed",)
    assert not (skill / ".SKILL.md.health.json").exists()
    assert not (skill / ".last_audit.json").exists()


def test_final_node_readiness_prevents_write_after_mid_audit_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    changed = False

    def dirty_after_first_node_check(
        snapshot: object,
        input_paths: Sequence[Path],
        expected_hashes: object,
    ) -> object:
        nonlocal changed
        result = real_check_commit_readiness(snapshot, input_paths, expected_hashes)
        if skill / "SKILL.md" in input_paths and not changed:
            changed = True
            write(skill / "SKILL.md", "changed during audit\n")
        return result

    monkeypatch.setattr(certifier, "check_commit_readiness", dirty_after_first_node_check)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    leaf = outcomes_by_node(outcomes[0])["demo-skill.llm.default"]
    assert leaf.stamp_status == "not-written"
    assert any("worktree-differs-from-commit" in reason for reason in leaf.reasons)
    assert not (skill / ".SKILL.md.health.json").exists()


def test_legacy_atomic_write_rejects_symlink_destination(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    make_commit_backed(tmp_path)
    victim = tmp_path / "victim.json"
    victim.write_text("keep\n", encoding="utf-8")
    (skill / ".last_audit.json").symlink_to(victim)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].stamp_status == "failed"
    assert victim.read_text(encoding="utf-8") == "keep\n"
    assert (skill / ".last_audit.json").is_symlink()


def test_post_write_requires_exact_requested_skill_identity(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path, status_skill_override="unrelated-skill")

    _evidence, outcomes = certifier.certify(
        fake,
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].stamp_status == "failed"
    root = outcomes_by_node(outcomes[0])["demo-skill"]
    assert root.health_status == "healthy"
    assert "exact requested skill" in root.reasons[0]
    assert (skill / ".SKILL.md.health.json").exists()


def test_raw_command_evidence_stays_outside_node_health(tmp_path: Path, capsys) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)

    exit_code = certifier.main(
        ["certify", str(skill), "--skip-mechanical", "--json"],
        dispatcher=fake,
    )
    payload = json.loads(capsys.readouterr().out)
    root_record = json.loads((skill / ".last_audit.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["evidence"]
    assert root_record["checks"] == [
        {"id": "semantic-exactness", "version": 1, "passed": True, "findings": []}
    ]
    assert not any(
        key in root_record["checks"][0]
        for key in ("command", "stdout_tail", "stderr_tail", "exit_code")
    )


def test_noop_typed_audit_preserves_all_health_bytes_and_mtimes(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)
    certifier.certify(fake, targets=[str(skill)], skip_mechanical=True)
    paths = [
        skill / ".SKILL.md.health.json",
        skill / ".last_audit.json",
        skill / ".pooled-blueprint-review.yaml",
        skill / ".pooled-blueprint-review.health.json",
    ]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    _evidence, outcomes = certifier.certify(
        fake,
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert all(node.stamp_status == "reused" for node in outcomes[0].nodes)
    assert outcomes[0].pool_status == "reused"
    assert outcomes[0].pool_status in {"reused", "written", "not-written", "failed"}
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before


def test_outcome_payload_uses_exact_task_6_fields(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    payload = outcomes[0].as_payload()
    assert set(payload) == {
        "skill",
        "source",
        "skill_root",
        "semantic_status",
        "stamp_worthy",
        "stamp_status",
        "nodes",
        "pool_status",
    }
    assert set(payload["nodes"][0]) == {
        "node_id",
        "semantic_status",
        "health_status",
        "stamp_worthy",
        "stamp_status",
        "reasons",
        "record_path",
    }


def test_exact_path_rejects_wrong_all_reused_provider_result(tmp_path: Path) -> None:
    requested = make_typed_skill(tmp_path, "requested-skill")
    unrelated = make_typed_skill(tmp_path, "unrelated-skill")
    make_commit_backed(tmp_path)
    normal = FakeDispatcher(tmp_path)
    certifier.certify(normal, targets=[str(requested)], skip_mechanical=True)
    certifier.certify(normal, targets=[str(unrelated)], skip_mechanical=True)
    paths = [
        requested / ".last_audit.json",
        unrelated / ".last_audit.json",
    ]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path, hash_skill_roots=[unrelated]),
        targets=[str(requested)],
        skip_mechanical=True,
    )

    assert len(outcomes) == 1
    assert outcomes[0].skill == requested.name
    assert outcomes[0].stamp_status == "failed"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before


def test_exact_path_rejects_multiple_provider_results_before_writes(tmp_path: Path) -> None:
    requested = make_typed_skill(tmp_path, "requested-skill")
    unrelated = make_typed_skill(tmp_path, "unrelated-skill")
    make_commit_backed(tmp_path)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path, hash_skill_roots=[requested, unrelated]),
        targets=[str(requested)],
        skip_mechanical=True,
    )

    assert len(outcomes) == 1
    assert outcomes[0].skill == requested.name
    assert outcomes[0].stamp_status == "failed"
    assert not (requested / ".last_audit.json").exists()
    assert not (unrelated / ".last_audit.json").exists()


def test_exact_name_rejects_wrong_provider_identity(tmp_path: Path) -> None:
    requested = make_typed_skill(tmp_path, "requested-skill")
    unrelated = make_typed_skill(tmp_path, "unrelated-skill")
    make_commit_backed(tmp_path)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path, hash_skill_roots=[unrelated]),
        targets=[requested.name],
        skip_mechanical=True,
    )

    assert len(outcomes) == 1
    assert outcomes[0].skill == requested.name
    assert outcomes[0].stamp_status == "failed"
    assert not (unrelated / ".last_audit.json").exists()


def test_exact_request_rejects_zero_provider_results(tmp_path: Path) -> None:
    requested = make_typed_skill(tmp_path, "requested-skill")
    make_commit_backed(tmp_path)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path, hash_skill_roots=[]),
        targets=[str(requested)],
        skip_mechanical=True,
    )

    assert len(outcomes) == 1
    assert outcomes[0].skill == requested.name
    assert outcomes[0].stamp_status == "failed"
    assert not (requested / ".last_audit.json").exists()


def test_written_child_with_dirty_parent_remains_partial_not_written(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    with (skill / "blueprint.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# dirty parent\n")

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    nodes = outcomes_by_node(outcomes[0])
    assert nodes["demo-skill.llm.default"].stamp_status == "written"
    assert nodes["demo-skill"].stamp_status == "not-written"
    assert outcomes[0].stamp_status == "not-written"
    assert (skill / ".SKILL.md.health.json").exists()
    assert not (skill / ".last_audit.json").exists()


def test_all_reused_graph_change_before_finalization_is_not_certified(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)
    certifier.certify(fake, targets=[str(skill)], skip_mechanical=True)
    real_check = certifier.check_graph_health_from_disk
    checks = 0

    def change_before_finalization(context: object) -> object:
        nonlocal checks
        checks += 1
        if checks == 2:
            write(skill / "SKILL.md", "changed before finalization\n")
        return real_check(context)

    monkeypatch.setattr(certifier, "check_graph_health_from_disk", change_before_finalization)

    _evidence, outcomes = certifier.certify(
        fake,
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].stamp_status == "not-written"
    assert outcomes_by_node(outcomes[0])["demo-skill.llm.default"].stamp_status == "not-written"


def test_comment_only_blueprint_change_before_finalization_is_not_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)
    certifier.certify(fake, targets=[str(skill)], skip_mechanical=True)
    real_check = certifier.check_graph_health_from_disk
    checks = 0

    def change_before_finalization(context: object) -> object:
        nonlocal checks
        checks += 1
        if checks == 2:
            with (skill / ".SKILL.md.blueprint.yaml").open("a", encoding="utf-8") as handle:
                handle.write("# formatting-only change\n")
        report = real_check(context)
        if checks == 2:
            status = report.nodes["demo-skill.llm.default"]
            assert status.healthy
            assert certifier.node_requires_refresh(status)
        return report

    monkeypatch.setattr(certifier, "check_graph_health_from_disk", change_before_finalization)

    _evidence, outcomes = certifier.certify(
        fake,
        targets=[str(skill)],
        skip_mechanical=True,
    )

    nodes = outcomes_by_node(outcomes[0])
    assert outcomes[0].stamp_status == "not-written"
    assert nodes["demo-skill.llm.default"].stamp_status == "not-written"
    assert "health-changed-before-finalization" in nodes["demo-skill.llm.default"].reasons


def test_child_change_after_step_blocks_parent_stamp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)
    certifier.certify(fake, targets=[str(skill)], skip_mechanical=True)
    root_record = skill / ".last_audit.json"
    root_bytes = root_record.read_bytes()
    root_mtime = root_record.stat().st_mtime_ns
    write(
        skill / "blueprint.yaml",
        (skill / "blueprint.yaml").read_text(encoding="utf-8") + "\n",
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "skills/demo-skill/blueprint.yaml"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "root update"],
        check=True,
    )
    real_audit_node = certifier.audit_and_maybe_stamp_node

    def change_before_parent(context: object, node_id: str, outcomes: object) -> object:
        if node_id == "demo-skill":
            write(skill / "SKILL.md", "changed after child step\n")
        return real_audit_node(context, node_id, outcomes)

    monkeypatch.setattr(certifier, "audit_and_maybe_stamp_node", change_before_parent)

    _evidence, outcomes = certifier.certify(
        fake,
        targets=[str(skill)],
        skip_mechanical=True,
    )

    nodes = outcomes_by_node(outcomes[0])
    assert nodes["demo-skill.llm.default"].stamp_status == "not-written"
    assert nodes["demo-skill"].stamp_status == "not-written"
    assert root_record.read_bytes() == root_bytes
    assert root_record.stat().st_mtime_ns == root_mtime


def test_comment_only_child_blueprint_change_blocks_parent_stamp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    fake = FakeDispatcher(tmp_path)
    certifier.certify(fake, targets=[str(skill)], skip_mechanical=True)
    root_record = skill / ".last_audit.json"
    root_bytes = root_record.read_bytes()
    root_mtime = root_record.stat().st_mtime_ns
    write(
        skill / "blueprint.yaml",
        (skill / "blueprint.yaml").read_text(encoding="utf-8") + "\n",
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "skills/demo-skill/blueprint.yaml"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "root update"],
        check=True,
    )
    real_audit_node = certifier.audit_and_maybe_stamp_node

    def change_before_parent(context: object, node_id: str, outcomes: object) -> object:
        if node_id == "demo-skill":
            with (skill / ".SKILL.md.blueprint.yaml").open("a", encoding="utf-8") as handle:
                handle.write("# comment-only child change\n")
            status = certifier.check_graph_health_from_disk(context).nodes[
                "demo-skill.llm.default"
            ]
            assert status.healthy
            assert certifier.node_requires_refresh(status)
        return real_audit_node(context, node_id, outcomes)

    monkeypatch.setattr(certifier, "audit_and_maybe_stamp_node", change_before_parent)

    _evidence, outcomes = certifier.certify(
        fake,
        targets=[str(skill)],
        skip_mechanical=True,
    )

    nodes = outcomes_by_node(outcomes[0])
    assert nodes["demo-skill.llm.default"].stamp_status == "not-written"
    assert nodes["demo-skill"].stamp_status == "not-written"
    assert "child-not-current:demo-skill.llm.default" in nodes["demo-skill"].reasons
    assert root_record.read_bytes() == root_bytes
    assert root_record.stat().st_mtime_ns == root_mtime


def test_legacy_final_readiness_blocks_mid_audit_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = make_skill(tmp_path)
    make_commit_backed(tmp_path)
    changed = False

    def mutate_after_first_legacy_readiness(
        snapshot: object,
        input_paths: Sequence[Path],
        expected_hashes: object,
    ) -> object:
        nonlocal changed
        result = real_check_commit_readiness(snapshot, input_paths, expected_hashes)
        if skill / "SKILL.md" in input_paths and not changed:
            changed = True
            write(skill / "SKILL.md", "changed during legacy audit\n")
        return result

    monkeypatch.setattr(certifier, "check_commit_readiness", mutate_after_first_legacy_readiness)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].semantic_status == "passed"
    assert outcomes[0].stamp_status == "not-written"
    assert not (skill / ".last_audit.json").exists()


def test_unsafe_key_preserves_valid_semantics_as_not_written(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    victim = tmp_path / "external-key"
    victim.write_bytes(bytes(range(32)))
    key_path = tmp_path / "skills" / "skill-audit" / ".health-authentication-key"
    key_path.symlink_to(victim)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].semantic_status == "passed"
    assert outcomes[0].stamp_status == "not-written"
    assert any("key" in reason for node in outcomes[0].nodes for reason in node.reasons)
    assert not (skill / ".last_audit.json").exists()


def test_symlinked_key_parent_is_a_not_written_readiness_reason(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    key_root = tmp_path / "skills" / "skill-audit"
    real_key_root = tmp_path / "real-key-root"
    key_root.rename(real_key_root)
    key_root.symlink_to(real_key_root, target_is_directory=True)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].semantic_status == "passed"
    assert outcomes[0].stamp_status == "not-written"
    assert any(
        "symlink key path component" in reason
        for node in outcomes[0].nodes
        for reason in node.reasons
    )
    assert not (skill / ".last_audit.json").exists()


def test_malformed_key_preserves_failed_semantic_result(tmp_path: Path) -> None:
    skill = make_typed_skill(tmp_path)
    make_commit_backed(tmp_path)
    write(skill / "SKILL.md", "Run `python3 scripts/do_work.py`.\n")
    key_path = tmp_path / "skills" / "skill-audit" / ".health-authentication-key"
    key_path.write_bytes(b"short")

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].semantic_status == "failed"
    assert outcomes[0].stamp_status == "not-written"
    assert any("key" in reason for node in outcomes[0].nodes for reason in node.reasons)


def test_schema_invalid_reachable_sidecar_fails_semantics_without_stamp(
    tmp_path: Path,
) -> None:
    skill = make_typed_skill(tmp_path)
    sidecar = skill / ".SKILL.md.blueprint.yaml"
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration.pop("direct_io")
    write(sidecar, yaml.safe_dump(declaration, sort_keys=False))
    make_commit_backed(tmp_path)

    _evidence, outcomes = certifier.certify(
        FakeDispatcher(tmp_path),
        targets=[str(skill)],
        skip_mechanical=True,
    )

    assert outcomes[0].semantic_status == "failed"
    assert outcomes[0].stamp_status == "failed"
    assert any("schema error" in reason for node in outcomes[0].nodes for reason in node.reasons)
    assert not list(skill.rglob("*.health.json"))
    assert not (skill / ".last_audit.json").exists()


def test_malformed_yaml_target_reports_partial_success(tmp_path: Path, capsys) -> None:
    valid = make_typed_skill(tmp_path, "valid-skill")
    malformed = make_typed_skill(tmp_path, "malformed-skill")
    make_commit_backed(tmp_path)
    write(malformed / "blueprint.yaml", "schema_version: [\n")

    exit_code = certifier.main(
        ["certify", str(valid), str(malformed), "--skip-mechanical", "--json"],
        dispatcher=FakeDispatcher(tmp_path),
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["certified"][0]["skill"] == valid.name
    assert payload["failed"][0]["skill"] == malformed.name
    assert (valid / ".last_audit.json").exists()


def test_skill_audit_contract_declares_runtime_io_and_partial_results() -> None:
    skill_root = MODULE_PATH.parents[1]
    sidecar = yaml.safe_load(
        (skill_root / "_rtx" / "._audit_certifier.py.blueprint.yaml").read_text(
            encoding="utf-8"
        )
    )
    root = yaml.safe_load((skill_root / "blueprint.yaml").read_text(encoding="utf-8"))

    read_contents = {item["content"] for item in sidecar["direct_io"]["reads"]}
    write_contents = {item["content"] for item in sidecar["direct_io"]["writes"]}
    assert "cryptography" in {
        dependency["name"] for dependency in sidecar["dependencies"]
        if dependency["kind"] == "python-package"
    }
    assert {"blueprint", "config", "repository"} <= read_contents
    assert {"credential", "audit-record", "generated-artifact"} <= write_contents
    assert sidecar["owns_filesystem"]
    outputs = " ".join(root["skill_interface"]["outputs"])
    assert "independent" in outputs
    assert "not-written" in outputs


def _v4_fixture(repo: Path):
    return create_v4_repository(repo)


def _commit_v4_change(repo: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-qm", message],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _commit_v4_review(repo: Path, message: str) -> str:
    graph = certifier.load_repository_blueprint_graph(
        repo, schema_root=repo / "references" / "blueprint"
    )
    context = certifier._v4_legacy_review_context(repo, graph)
    digest = certifier._v4_reconciliation_digest(repo.resolve(), context)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "--allow-empty",
            "-qm",
            message + f"\n\nFamulus-Legacy-Claims-Reconciled: {digest}",
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def _set_v4_mechanical_baseline_for_test(repo: Path, commit: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "update-ref",
            certifier.BLUEPRINT_V4_MECHANICAL_REF,
            commit,
        ],
        check=True,
    )


def _add_v4_cross_owner_contract(repo: Path):
    module_root = repo / "skills" / "demo-skill"
    contract_path = module_root / "contracts" / "shared.schema.json"
    write(contract_path, '{"type":"string"}\n')
    contract_node_id = "demo-skill.source.contract"
    contract_interface_id = f"{contract_node_id}.interface.read"
    _write_v4_yaml(
        module_root / "blueprints" / "contract.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": contract_node_id,
            "version": 1,
            "description": "Shared contract source.",
            "gateway": {
                "path": "contracts/shared.schema.json",
                "language": "JSON",
            },
            "content": [r"contracts/shared\.schema\.json"],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {
                contract_interface_id: {
                    "version": 1,
                    "description": "Read the shared contract.",
                    "contract": _v4_contract(),
                }
            },
        },
    )
    module_path = module_root / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["sources"][contract_node_id] = {
        "blueprint": {
            "base": "module-root",
            "path": "blueprints/contract.yaml",
        }
    }
    module["content"].append(r"contracts/shared\.schema\.json")
    _write_v4_yaml(module_path, module)
    gateway_path = module_root / "blueprints" / "gateway.yaml"
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    interface = gateway["interfaces"]["demo-skill.source.gateway.interface.run"]
    output = interface["contract"]["outputs"][0]
    output.pop("type")
    output["schema"] = {
        "path": "contracts/shared.schema.json",
        "fragment": "#",
    }
    _write_v4_yaml(gateway_path, gateway)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    mechanical_commit = _commit_v4_change(repo, "add contract source")
    _set_v4_mechanical_baseline_for_test(repo, mechanical_commit)
    return certifier.load_repository_blueprint_graph(
        repo,
        schema_root=repo / "references" / "blueprint",
    )


def _v4_certify(repo: Path, graph: object, **overrides: object):
    (repo / "public-keys").mkdir(exist_ok=True)
    snapshot = certifier.capture_git_snapshot(repo)
    assert snapshot is not None
    options = {
        "target_node_ids": ("demo-skill",),
        "public_key_root": repo / "public-keys",
        "secret_backend": _MemorySecretBackend(),
        "reviewed_commit": snapshot.commit,
        "certified_at": "2026-07-20T12:00:00Z",
    }
    options.update(overrides)
    return certifier._certify_v4_repository(repo, **options)


def test_v4_completeness_findings_block_signing_structural_drafts(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    module_path = tmp_path / "skills" / "demo-skill" / "blueprint.yaml"
    source_path = (
        tmp_path / "skills" / "demo-skill" / "blueprints" / "gateway.yaml"
    )
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    module.pop("description")
    source.pop("description")
    interface = source["interfaces"][
        "demo-skill.source.gateway.interface.run"
    ]
    interface.pop("description")
    interface["contract"] = {"direct_io": interface["contract"]["direct_io"]}
    _write_v4_yaml(module_path, module)
    _write_v4_yaml(source_path, source)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "structural draft"],
        check=True,
    )
    graph = certifier.load_repository_blueprint_graph(
        tmp_path,
        schema_root=tmp_path / "references" / "blueprint",
    )

    findings = certifier.v4_certification_completeness_findings(graph)

    assert {finding.field for finding in findings} == {
        "description",
        "contract.arguments",
        "contract.preconditions",
        "contract.interaction",
        "contract.caller_warnings",
        "contract.outputs",
        "contract.outcomes",
        "contract.execution",
        "contract.helpers",
    }
    with pytest.raises(certifier.AuditError, match="certification completeness"):
        _v4_certify(tmp_path, graph)


def test_v4_candidate_inspection_reports_findings_without_repair_or_certificates(
    tmp_path: Path,
) -> None:
    _graph, _states, commit = _v4_fixture(tmp_path)
    source_path = (
        tmp_path / "skills" / "demo-skill" / "blueprints" / "gateway.yaml"
    )
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source.pop("description")
    interface = source["interfaces"][
        "demo-skill.source.gateway.interface.run"
    ]
    interface.pop("description")
    interface["contract"] = {"direct_io": interface["contract"]["direct_io"]}
    _write_v4_yaml(source_path, source)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "structural draft"],
        check=True,
    )
    before = source_path.read_bytes()

    inspection = certifier.inspect_v4_migration_candidate(tmp_path)

    assert inspection.source_commit != commit
    assert inspection.node_ids == (
        "demo-skill",
        "demo-skill.source.gateway",
        "skill-certifier",
        "skill-certifier.source.gateway",
    )
    assert {finding.field for finding in inspection.findings} >= {
        "description",
        "contract.arguments",
        "contract.execution",
    }
    assert source_path.read_bytes() == before
    assert not tuple(tmp_path.rglob("*.certificates.jsonl"))


def test_v4_candidate_finalization_requires_exact_reviewed_commit(
    tmp_path: Path,
) -> None:
    _graph, _states, commit = _v4_fixture(tmp_path)

    with pytest.raises(certifier.AuditError, match="reviewed commit"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            reviewed_commit="0" * len(commit),
            certified_at="2026-07-21T12:00:00Z",
        )

    assert not tuple(tmp_path.rglob("*.certificates.jsonl"))


def test_v4_candidate_finalization_accepts_blueprint_only_semantic_review(
    tmp_path: Path,
) -> None:
    graph, _states, _mechanical_commit = _v4_fixture(tmp_path)
    source_path = graph.nodes["demo-skill.source.gateway"].blueprint_path
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["description"] = "Reviewed gateway behavior."
    _write_v4_yaml(source_path, source)
    reviewed_commit = _commit_v4_review(tmp_path, "semantic review")

    result = certifier.certify_v4_migration_candidate(
        tmp_path,
        reviewed_commit=reviewed_commit,
        certified_at="2026-07-21T12:00:00Z",
    )

    assert result.source_commit == reviewed_commit


def test_v4_candidate_finalization_rejects_unrecorded_claim_reconciliation(
    tmp_path: Path,
) -> None:
    _graph, _states, mechanical_commit = _v4_fixture(tmp_path)
    with pytest.raises(certifier.AuditError, match="strictly descend"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            reviewed_commit=mechanical_commit,
            certified_at="2026-07-21T12:00:00Z",
        )
    reviewed_commit = _commit_v4_change(tmp_path, "audit without reconciliation")
    with pytest.raises(certifier.AuditError, match="unresolved legacy claims"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            reviewed_commit=reviewed_commit,
            certified_at="2026-07-21T12:00:00Z",
        )


def test_v4_candidate_review_may_repair_direct_io(tmp_path: Path) -> None:
    graph, _states, _mechanical_commit = _v4_fixture(tmp_path)
    source_path = graph.nodes["demo-skill.source.gateway"].blueprint_path
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    direct_io = source["interfaces"][
        "demo-skill.source.gateway.interface.run"
    ]["contract"]["direct_io"]
    direct_io["writes"][0]["content"] = "Reviewed result content."
    _write_v4_yaml(source_path, source)
    reviewed_commit = _commit_v4_review(tmp_path, "review direct I/O")

    result = certifier.certify_v4_migration_candidate(
        tmp_path,
        reviewed_commit=reviewed_commit,
        certified_at="2026-07-21T12:00:00Z",
    )

    assert result.source_commit == reviewed_commit


def test_v4_candidate_finalization_rejects_non_blueprint_review_change(
    tmp_path: Path,
) -> None:
    _graph, _states, _mechanical_commit = _v4_fixture(tmp_path)
    (tmp_path / "review-notes.txt").write_text("not a blueprint\n", encoding="utf-8")
    reviewed_commit = _commit_v4_review(tmp_path, "non-blueprint review change")

    with pytest.raises(certifier.AuditError, match="only blueprint files"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            reviewed_commit=reviewed_commit,
            certified_at="2026-07-21T12:00:00Z",
        )

    assert not tuple(tmp_path.rglob("*.certificates.jsonl"))


def test_v4_candidate_finalization_rejects_protected_projection_change(
    tmp_path: Path,
) -> None:
    graph, _states, _mechanical_commit = _v4_fixture(tmp_path)
    source_path = graph.nodes["demo-skill.source.gateway"].blueprint_path
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["gateway"]["language"] = "Reviewed-Markdown"
    _write_v4_yaml(source_path, source)
    reviewed_commit = _commit_v4_review(tmp_path, "change protected gateway")

    with pytest.raises(certifier.AuditError, match="protected projection"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            reviewed_commit=reviewed_commit,
            certified_at="2026-07-21T12:00:00Z",
        )
    with pytest.raises(TypeError, match="mechanical_commit"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            mechanical_commit=reviewed_commit,
            reviewed_commit=reviewed_commit,
            certified_at="2026-07-21T12:00:00Z",
        )

    assert not tuple(tmp_path.rglob("*.certificates.jsonl"))


def test_v4_candidate_finalization_rejects_helper_binding_change(
    tmp_path: Path,
) -> None:
    graph, _states, _initial_commit = _v4_fixture(tmp_path)
    source_path = graph.nodes["demo-skill.source.gateway"].blueprint_path
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    certifier_interface = "skill-certifier.interface.certify"
    source["uses_interfaces"] = [
        {"interface": certifier_interface, "version": 1}
    ]
    interface = source["interfaces"]["demo-skill.source.gateway.interface.run"]
    interface["contract"]["helpers"] = [
        {
            "id": "certifier-result",
            "role": "Read the certifier result.",
            "interface": certifier_interface,
            "version": 1,
            "inputs": {},
            "result": {
                "output_ref": "result",
                "selector": {"kind": "whole-output"},
            },
            "route": {"kind": "output", "target": "result"},
            "empty": {"outcome": "success", "caller_action": "Continue."},
            "failure": {"outcome": "success"},
        }
    ]
    _write_v4_yaml(source_path, source)
    mechanical_commit = _commit_v4_change(tmp_path, "mechanical helper binding")
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "update-ref",
            certifier.BLUEPRINT_V4_MECHANICAL_REF,
            mechanical_commit,
        ],
        check=True,
    )
    source["interfaces"]["demo-skill.source.gateway.interface.run"]["contract"][
        "helpers"
    ][0]["role"] = "Changed helper role."
    _write_v4_yaml(source_path, source)
    reviewed_commit = _commit_v4_review(tmp_path, "review helper binding")

    with pytest.raises(certifier.AuditError, match="protected projection"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            reviewed_commit=reviewed_commit,
            certified_at="2026-07-21T12:00:00Z",
        )


def test_v4_candidate_finalization_requires_reserved_mechanical_ref(
    tmp_path: Path,
) -> None:
    _graph, _states, reviewed_commit = _v4_fixture(tmp_path)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "update-ref",
            "-d",
            certifier.BLUEPRINT_V4_MECHANICAL_REF,
        ],
        check=True,
    )

    with pytest.raises(certifier.AuditError, match="mechanical baseline"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            reviewed_commit=reviewed_commit,
            certified_at="2026-07-21T12:00:00Z",
        )


def test_v4_inspection_separates_legacy_review_context_from_blocking_findings(
    tmp_path: Path,
) -> None:
    graph, _states, _initial_commit = _v4_fixture(tmp_path)
    module_path = graph.nodes["demo-skill"].blueprint_path
    source_path = graph.nodes["demo-skill.source.gateway"].blueprint_path
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    interface = source["interfaces"].pop(
        "demo-skill.source.gateway.interface.run"
    )
    source[
        "interfaces"
    ]["demo-skill.source.gateway.interface.default"] = interface
    _write_v4_yaml(source_path, source)
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    export = module["exports"].pop("demo-skill.interface.run")
    export["source_interface"] = "demo-skill.source.gateway.interface.default"
    module["exports"]["demo-skill.interface.default"] = export
    _write_v4_yaml(module_path, module)
    reviewed_v4 = module_path.read_bytes()
    _write_v4_yaml(
        module_path,
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "skill_interface": {
                "inputs": ["An exact legacy input claim."],
                "outputs": [],
                "side_effects": [],
            },
        },
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--amend", "-qm", "legacy root"],
        check=True,
    )
    legacy_commit = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "update-ref",
            certifier.BLUEPRINT_V4_SOURCE_OVERLAY_REF,
            legacy_commit,
        ],
        check=True,
    )
    module_path.write_bytes(reviewed_v4)
    mechanical_commit = _commit_v4_change(
        tmp_path, "materialize mechanical v4 blueprint candidate"
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "update-ref",
            certifier.BLUEPRINT_V4_MECHANICAL_REF,
            mechanical_commit,
        ],
        check=True,
    )

    inspection = certifier.inspect_v4_migration_candidate(tmp_path)
    relocated = tmp_path.parent / f"{tmp_path.name}-relocated"
    shutil.copytree(tmp_path, relocated)
    relocated_inspection = certifier.inspect_v4_migration_candidate(relocated)

    assert inspection.findings == ()
    assert relocated_inspection.reconciliation_digest == inspection.reconciliation_digest
    assert len(inspection.review_context) == 1
    assert inspection.review_context[0].field == "legacy.skill_interface.inputs[0]"
    assert "exact legacy input claim" in inspection.review_context[0].message
    result = certifier.certify_v4_migration_candidate(
        tmp_path,
        reviewed_commit=_commit_v4_review(tmp_path, "claim reconciliation audit"),
        certified_at="2026-07-21T12:00:00Z",
    )
    assert result.source_commit != mechanical_commit


def test_v4_candidate_finalization_rejects_dirty_worktree(tmp_path: Path) -> None:
    _graph, _states, commit = _v4_fixture(tmp_path)
    (tmp_path / "unreviewed.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(certifier.AuditError, match="must be clean"):
        certifier.certify_v4_migration_candidate(
            tmp_path,
            reviewed_commit=commit,
            certified_at="2026-07-21T12:00:00Z",
        )

    assert not tuple(tmp_path.rglob("*.certificates.jsonl"))


def test_v4_final_completeness_requires_network_endpoint(tmp_path: Path) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    source = graph.nodes["demo-skill.source.gateway"]
    interface = source.declaration["interfaces"][
        "demo-skill.source.gateway.interface.run"
    ]
    interface["contract"]["direct_io"]["network"] = [
        {
            "id": "network-1",
            "medium": "network-request",
            "access": "request",
            "content": "Remote result.",
            "sensitivity": "public",
        }
    ]

    findings = certifier.v4_certification_completeness_findings(graph)

    assert any(
        finding.field == "contract.direct_io.network[0].endpoint"
        for finding in findings
    )


def test_v4_candidate_finalization_uses_owned_checks_for_reviewed_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _states, mechanical_commit = _v4_fixture(tmp_path)
    commit = _commit_v4_review(tmp_path, "explicit audit")
    real_deterministic = certifier._v4_deterministic_check
    real_semantic = certifier._v4_semantic_attestation
    events: list[tuple[str, str, str]] = []

    def deterministic(snapshot: object, *args: object, **kwargs: object):
        events.append(("deterministic", snapshot.node_id, snapshot.source_commit))
        return real_deterministic(snapshot, *args, **kwargs)

    def semantic(snapshot: object, *args: object, **kwargs: object):
        events.append(("semantic", snapshot.node_id, snapshot.source_commit))
        return real_semantic(snapshot, *args, **kwargs)

    monkeypatch.setattr(certifier, "_v4_deterministic_check", deterministic)
    monkeypatch.setattr(certifier, "_v4_semantic_attestation", semantic)

    result = certifier.certify_v4_migration_candidate(
        tmp_path,
        reviewed_commit=commit,
        certified_at="2026-07-21T12:00:00Z",
    )

    assert set(result.node_ids) == set(graph.nodes)
    assert all(event[2] == commit for event in events)
    assert [event[:2] for event in events] == [
        pair
        for node_id in result.node_ids
        for pair in (("deterministic", node_id), ("semantic", node_id))
    ]


def test_private_v4_writer_builds_payload_bottom_up_and_verifies(tmp_path: Path) -> None:
    graph, _states, commit = _v4_fixture(tmp_path)
    (tmp_path / "public-keys").mkdir()

    result = _v4_certify(tmp_path, graph)

    assert result.source_commit == commit
    assert result.node_ids == ("demo-skill.source.gateway", "demo-skill")
    assert all(
        certifier.certificate_log_path(graph.nodes[node_id]).is_file()
        for node_id in result.node_ids
    )
    for node_id in result.node_ids:
        entries = parse_certificate_log(
            certifier.certificate_log_path(graph.nodes[node_id]).read_bytes(),
            tmp_path / "public-keys",
        )
        assert entries[-1]["payload"]["checks"] == list(
            certifier.expected_certifier_checks()
        )


def test_private_v4_writer_propagates_explicit_non_atomic_fallback(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    with pytest.raises(certifier.AuditError, match="diagnostic-only"):
        _v4_certify(tmp_path, graph, allow_non_atomic=True)
    assert not tuple((tmp_path / "public-keys").iterdir())
    assert not tuple(tmp_path.rglob("*.certificates.jsonl"))


def test_private_v4_writer_rejects_non_atomic_candidate_provenance(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    subprocess.run(
        [
            "git", "-C", str(tmp_path), "config",
            "famulus.candidateAtomicGuarantee", "false",
        ],
        check=True,
    )

    with pytest.raises(certifier.AuditError, match="non-certifiable"):
        _v4_certify(tmp_path, graph)
    assert not tuple((tmp_path / "public-keys").iterdir())
    assert not tuple(tmp_path.rglob("*.certificates.jsonl"))


def test_private_v4_writer_accepts_native_confined_commit_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    monkeypatch.setattr(git_provenance, "_use_native_confined_read", lambda: True)
    with pytest.raises(certifier.AuditError, match="diagnostic-only"):
        _v4_certify(tmp_path, graph, allow_non_atomic=True)


def test_private_v4_writer_exact_source_target_does_not_write_parent(tmp_path: Path) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)

    result = _v4_certify(
        tmp_path,
        graph,
        target_node_ids=("demo-skill.source.gateway",),
    )

    assert result.node_ids == ("demo-skill.source.gateway",)
    assert not certifier.certificate_log_path(graph.nodes["demo-skill"]).exists()


def test_private_v4_writer_orders_cross_owner_contract_before_exact_target(
    tmp_path: Path,
) -> None:
    _v4_fixture(tmp_path)
    graph = _add_v4_cross_owner_contract(tmp_path)
    target = "demo-skill.source.gateway"
    dependency = "demo-skill.source.contract"
    basis_paths = certifier.resolve_certification_basis_paths(tmp_path)
    states = certifier.compute_node_hash_states(
        graph,
        repo_root=tmp_path,
        policy_path=tmp_path / certifier.CANONICAL_NODE_HASH_POLICY,
        certification_basis_hash=certifier.compute_certification_basis_hash(tmp_path),
        certification_basis_paths=basis_paths,
    )
    assert any(
        item["relation"] == "references-cross-owner-contract"
        and item["target"] == dependency
        for item in states[target].dependency_hashes
    )
    backend = _MemorySecretBackend()
    attempted: list[str] = []

    def stop_dependency(node_id: str) -> None:
        attempted.append(node_id)
        if node_id == dependency:
            raise certifier.AuditError("stop dependency issuance")

    with pytest.raises(certifier.AuditError, match="stop dependency issuance"):
        _v4_certify(
            tmp_path,
            graph,
            target_node_ids=(target,),
            secret_backend=backend,
            before_append=stop_dependency,
        )

    assert attempted == [dependency]
    assert not certifier.certificate_log_path(graph.nodes[target]).exists()
    assert not certifier.certificate_log_path(graph.nodes[dependency]).exists()

    result = _v4_certify(
        tmp_path,
        graph,
        target_node_ids=(target,),
        secret_backend=backend,
    )

    assert result.node_ids == (dependency, target)


@pytest.mark.parametrize(
    "race", ["tracked-input", "worktree-mode", "index-mode", "head", "log"]
)
def test_private_v4_writer_aborts_pre_append_races(tmp_path: Path, race: str) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)

    def mutate(node_id: str) -> None:
        if race == "tracked-input":
            (tmp_path / "skills" / "demo-skill" / "SKILL.md").write_text("changed\n")
        elif race == "worktree-mode":
            path = tmp_path / "skills" / "demo-skill" / "SKILL.md"
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        elif race == "index-mode":
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(tmp_path),
                    "update-index",
                    "--chmod=+x",
                    "skills/demo-skill/SKILL.md",
                ],
                check=True,
            )
        elif race == "head":
            (tmp_path / "race.txt").write_text("race\n")
            subprocess.run(["git", "-C", str(tmp_path), "add", "race.txt"], check=True)
            subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "race"], check=True)
        else:
            path = certifier.certificate_log_path(graph.nodes[node_id])
            path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(certifier.AuditError, match="changed"):
        _v4_certify(tmp_path, graph, before_append=mutate)


def test_private_v4_writer_reports_post_write_verification_failure(tmp_path: Path) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)

    def corrupt(node_id: str) -> None:
        path = certifier.certificate_log_path(graph.nodes[node_id])
        with path.open("ab") as stream:
            stream.write(b"{}\n")

    with pytest.raises(certifier.AuditError, match="post-write certificate log changed"):
        _v4_certify(tmp_path, graph, after_append=corrupt)


def test_private_v4_writer_rejects_caller_supplied_gate_callbacks(tmp_path: Path) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)

    with pytest.raises(TypeError, match="semantic_audit"):
        _v4_certify(
            tmp_path,
            graph,
            semantic_audit=lambda _snapshot: None,
        )


def test_private_v4_writer_certifies_the_certifier_through_the_same_path(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)

    result = _v4_certify(
        tmp_path,
        graph,
        target_node_ids=("skill-certifier",),
    )

    assert result.node_ids == (
        "skill-certifier.source.gateway",
        "skill-certifier",
    )


def test_private_v4_writer_fails_closed_without_current_certifier(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    shutil.rmtree(tmp_path / "skills" / "skill-certifier")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "remove certifier"],
        check=True,
    )

    with pytest.raises(certifier.AuditError, match="certifier"):
        _v4_certify(tmp_path, graph)


def test_private_v4_writer_reissues_same_key_against_complete_predecessor(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    backend = _MemorySecretBackend()
    target = "demo-skill.source.gateway"

    _v4_certify(
        tmp_path,
        graph,
        target_node_ids=(target,),
        secret_backend=backend,
    )
    _v4_certify(
        tmp_path,
        graph,
        target_node_ids=(target,),
        secret_backend=backend,
    )

    path = certifier.certificate_log_path(graph.nodes[target])
    entries = parse_certificate_log(path.read_bytes(), tmp_path / "public-keys")
    assert len(entries) == 2
    assert entries[1]["payload"]["key_id"] == entries[0]["payload"]["key_id"]
    assert entries[1]["payload"]["previous_entry_hash"] == certificate_entry_hash(
        entries[0]
    )


def test_private_v4_writer_appends_after_rotation_against_old_signed_envelope(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    backend = _MemorySecretBackend()
    target = "demo-skill.source.gateway"

    _v4_certify(
        tmp_path,
        graph,
        target_node_ids=(target,),
        secret_backend=backend,
    )
    new_key = rotate_certificate_signing_key(
        tmp_path / "public-keys", secret_backend=backend
    )
    _v4_certify(
        tmp_path,
        graph,
        target_node_ids=(target,),
        secret_backend=backend,
    )

    path = certifier.certificate_log_path(graph.nodes[target])
    entries = parse_certificate_log(path.read_bytes(), tmp_path / "public-keys")
    assert len(entries) == 2
    assert entries[0]["payload"]["key_id"] != new_key.key_id
    assert entries[1]["payload"]["key_id"] == new_key.key_id
    assert entries[1]["payload"]["previous_entry_hash"] == certificate_entry_hash(
        entries[0]
    )


@pytest.mark.parametrize(
    "race",
    [
        "content",
        "worktree-mode",
        "index-mode",
        "head",
        "basis",
        "checks",
        "log-replacement",
    ],
)
def test_private_v4_writer_rederives_every_final_state_after_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    target = "demo-skill.source.gateway"

    def mutate(node_id: str) -> None:
        assert node_id == target
        if race == "content":
            (tmp_path / "skills" / "demo-skill" / "SKILL.md").write_text(
                "changed after append\n",
                encoding="utf-8",
            )
        elif race == "worktree-mode":
            path = tmp_path / "skills" / "demo-skill" / "SKILL.md"
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        elif race == "index-mode":
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(tmp_path),
                    "update-index",
                    "--chmod=+x",
                    "skills/demo-skill/SKILL.md",
                ],
                check=True,
            )
        elif race == "head":
            (tmp_path / "head-race.txt").write_text("race\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(tmp_path), "add", "head-race.txt"], check=True
            )
            subprocess.run(
                ["git", "-C", str(tmp_path), "commit", "-qm", "head race"],
                check=True,
            )
        elif race == "basis":
            policy = (
                tmp_path
                / "references"
                / "certification"
                / "node-hash-policy.yaml"
            )
            policy.write_text(
                policy.read_text(encoding="utf-8").replace(
                    "**/*.log", "**/*.changed"
                ),
                encoding="utf-8",
            )
        elif race == "checks":
            monkeypatch.setitem(
                certifier.CERTIFIER_CHECK_REGISTRY,
                "deterministic",
                ("v4-deterministic", 2),
            )
        else:
            path = certifier.certificate_log_path(graph.nodes[node_id])
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)

    with pytest.raises(certifier.AuditError, match="post-write|changed"):
        _v4_certify(
            tmp_path,
            graph,
            target_node_ids=(target,),
            after_append=mutate,
        )


def test_private_v4_writer_rechecks_dependency_certificate_agreement_after_append(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)

    def corrupt_dependency(node_id: str) -> None:
        if node_id != "demo-skill":
            return
        dependency = certifier.certificate_log_path(
            graph.nodes["demo-skill.source.gateway"]
        )
        with dependency.open("ab") as stream:
            stream.write(b"{}\n")

    with pytest.raises(certifier.AuditError, match="post-write"):
        _v4_certify(tmp_path, graph, after_append=corrupt_dependency)


def test_private_v4_writer_rechecks_forced_untracked_input_after_append(
    tmp_path: Path,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    source_blueprint = (
        tmp_path / "skills" / "demo-skill" / "blueprints" / "gateway.yaml"
    )
    declaration = yaml.safe_load(source_blueprint.read_text(encoding="utf-8"))
    declaration["content"].append(r"local\.txt")
    source_blueprint.write_text(
        yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8"
    )
    module_blueprint = tmp_path / "skills" / "demo-skill" / "blueprint.yaml"
    module_declaration = yaml.safe_load(
        module_blueprint.read_text(encoding="utf-8")
    )
    module_declaration["content"].append(r"local\.txt")
    module_blueprint.write_text(
        yaml.safe_dump(module_declaration, sort_keys=False), encoding="utf-8"
    )
    policy_path = (
        tmp_path / "references" / "certification" / "node-hash-policy.yaml"
    )
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["rules"].append(
        {
            "action": "include",
            "pattern": "skills/demo-skill/local.txt",
            "require_match": True,
        }
    )
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    local_input = tmp_path / "skills" / "demo-skill" / "local.txt"
    local_input.write_text("forced local input\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "add",
            str(source_blueprint),
            str(module_blueprint),
            str(policy_path),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "add local input policy"],
        check=True,
    )
    mechanical_commit = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    _set_v4_mechanical_baseline_for_test(tmp_path, mechanical_commit)

    def mutate(_node_id: str) -> None:
        local_input.write_text("changed local input\n", encoding="utf-8")

    with pytest.raises(certifier.AuditError, match="local input changed"):
        _v4_certify(
            tmp_path,
            graph,
            target_node_ids=("demo-skill.source.gateway",),
            after_append=mutate,
        )


def test_private_v4_writers_cannot_append_against_one_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, _states, _commit = _v4_fixture(tmp_path)
    target = "demo-skill.source.gateway"
    backend = _MemorySecretBackend()
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    )
    certifier.certificate_log_path(graph.nodes[target]).parent.mkdir()
    compare_barrier = threading.Barrier(2)
    real_compare = certifier.atomic_compare_and_append_bytes

    def synchronized_compare(*args: object, **kwargs: object) -> None:
        compare_barrier.wait()
        real_compare(*args, **kwargs)

    monkeypatch.setattr(
        certifier,
        "atomic_compare_and_append_bytes",
        synchronized_compare,
    )

    def issue() -> object:
        try:
            return _v4_certify(
                tmp_path,
                graph,
                target_node_ids=(target,),
                secret_backend=backend,
            )
        except certifier.AuditError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: issue(), range(2)))

    assert sum(isinstance(outcome, certifier.V4CertificationResult) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, certifier.AuditError) for outcome in outcomes) == 1
    entries = parse_certificate_log(
        certifier.certificate_log_path(graph.nodes[target]).read_bytes(),
        public_key_root,
    )
    assert len(entries) == 1
