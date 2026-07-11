from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence


MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_audit_certifier.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
SPEC = importlib.util.spec_from_file_location("skill_audit_certifier", MODULE_PATH)
certifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = certifier
SPEC.loader.exec_module(certifier)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
                "      runtime:",
                "        kind: python_machine_interface",
                "        entrypoint: _rtx/_audit_worker.py:Interface",
                "      dependencies: []",
                "      directly_reads: []",
                "      directly_executes:",
                "        - _rtx/_audit_worker.py",
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
    def __init__(self, repo: Path, *, post_write_current: bool = True) -> None:
        self.repo = repo
        self.post_write_current = post_write_current
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
            skill_root = self._target_skill_root(argv)
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
                            "skill": "sha256:skill",
                            "policy": "sha256:policy",
                            "interfaces": {"llm.default": "sha256:llm"},
                        },
                    }
                ],
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if key == "drift-status":
            status = "audit-current" if self.post_write_current else "audit-stale"
            payload = {"skills": [{"skill": "demo-skill", "derived_status": status}]}
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        raise AssertionError(f"unexpected dispatch key {key}")

    def _target_skill_root(self, argv: list[str]) -> Path:
        if "--skill-root" in argv:
            return Path(argv[argv.index("--skill-root") + 1])
        targets = [arg for arg in argv[1:] if not arg.startswith("--")]
        if targets:
            return self.repo / "skills" / targets[0]
        return self.repo / "skills" / "demo-skill"


def test_certify_writes_audit_record_for_skill_name(tmp_path: Path, monkeypatch) -> None:
    skill = make_skill(tmp_path)
    fake = FakeDispatcher(tmp_path)
    monkeypatch.setattr(certifier, "git_commit", lambda repo_root=certifier.REPO_ROOT: "abc123")

    _mechanical, outcomes = certifier.certify(
        fake,
        targets=["demo-skill"],
        skip_mechanical=True,
        recorded_at="2026-07-11T12:00:00-04:00",
    )

    record = json.loads((skill / ".last_audit.json").read_text(encoding="utf-8"))
    assert outcomes[0].skill == "demo-skill"
    assert record["writer"] == "skill-audit@1"
    assert record["git_commit"] == "abc123"
    assert record["hashes"]["skill"] == "sha256:skill"


def test_certify_resolves_exact_skill_root_target(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    fake = FakeDispatcher(tmp_path)

    certifier.certify(fake, targets=[str(skill)], skip_mechanical=True)

    assert ("compute-hashes", ["compute-hashes", "--skill-root", str(skill.resolve()), "--json"]) in fake.calls
    assert (skill / ".last_audit.json").is_file()


def test_semantic_findings_stop_before_write(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, implicit_uncovered=True)
    fake = FakeDispatcher(tmp_path)

    try:
        certifier.certify(fake, targets=["demo-skill"], skip_mechanical=True)
    except certifier.AuditError as exc:
        assert "semantic exactness check failed" in str(exc)
    else:
        raise AssertionError("expected semantic exactness failure")

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


def test_post_write_failure_rolls_back_previous_record(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    old_record = {"schema_version": 1, "skill": "demo-skill", "hashes": {}}
    write(skill / ".last_audit.json", json.dumps(old_record) + "\n")
    fake = FakeDispatcher(tmp_path, post_write_current=False)

    try:
        certifier.certify(fake, targets=["demo-skill"], skip_mechanical=True)
    except certifier.AuditError as exc:
        assert "post-write drift verification failed" in str(exc)
    else:
        raise AssertionError("expected post-write failure")

    record = json.loads((skill / ".last_audit.json").read_text(encoding="utf-8"))
    assert record == old_record
