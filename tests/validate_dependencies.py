"""Smoke tests for skills/skill-maker/validators/dependencies.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR = (
    _REPO_ROOT
    / "skills" / "skill-maker" / "validators" / "dependencies.py"
)
_spec = importlib.util.spec_from_file_location("dependencies", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_blueprint(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def _copy_v4_schema_bundle(repo_root: Path) -> None:
    shutil.copytree(
        _REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )


def _copy_v4_loose_mode(repo_root: Path, name: str) -> Path:
    target = repo_root / "skills" / name
    shutil.copytree(_REPO_ROOT / "skills" / "loose-mode", target)
    for path in target.rglob("*"):
        if path.is_file():
            path.write_text(
                path.read_text(encoding="utf-8").replace("loose-mode", name),
                encoding="utf-8",
            )
    return target


def _skill(
    tmp_path: Path,
    name: str,
    body: str = "",
    blueprint_uses: list[str] | None = None,
) -> Path:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_md = f"---\nname: {name}\n---\n{body}\n"
    (skill_dir / "SKILL.md").write_text(skill_md)
    uses = blueprint_uses or []
    default = {
        "version": 1,
        "description": "Primary LLM-facing skill instructions.",
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "behavior_sources": [],
    }
    if uses:
        default["uses_interfaces"] = [
            {"interface": f"{dep}.llm.default", "version": 1} for dep in uses
        ]
    (skill_dir / "blueprint.yaml").write_text(yaml.dump({"interfaces": {"llm": {"default": default}}}))
    return skill_dir


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_valid_no_deps_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "my-skill")
    assert _mod.validate(tmp_path) == []


def test_missing_dependency_block_is_allowed(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    (skill_dir / "blueprint.yaml").write_text("interfaces: {}\n")
    assert _mod.validate(tmp_path) == []


def test_body_mentions_not_in_sidecar_flagged(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "my-skill",
        body="Use other-skill for this.",
    )
    # Create other-skill so it appears in skill_names
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)
    errors = _mod.validate(tmp_path)
    assert any("exact skill-name mentions" in e for e in errors)


def test_body_mentions_declared_interface_use_pass(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "my-skill",
        body="Use other-skill for this.",
        blueprint_uses=["other-skill"],
    )
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)
    assert _mod.validate(tmp_path) == []


def test_typed_default_llm_rejects_bare_skill_name_even_when_declared(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: my-skill\n---\nUse other-skill for this.\n"
    )
    _write_blueprint(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "my-skill",
            "interfaces": [
                {
                    "interface": "my-skill.llm.default",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": ".SKILL.md.blueprint.yaml",
                    },
                }
            ],
        },
    )
    _write_blueprint(
        skill / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "my-skill.llm.default",
            "version": 1,
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "uses_interfaces": [
                {"interface": "other-skill.llm.default", "version": 1}
            ],
        },
    )
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)

    errors = _mod.validate(tmp_path)
    assert any("must use canonical interface IDs" in error for error in errors)


def test_typed_default_llm_canonical_interface_must_be_declared_on_that_node(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: my-skill\n---\nUse other-skill.llm.default for this.\n"
    )
    _write_blueprint(
        skill / "blueprint.yaml",
        {"schema_version": 2, "blueprint_type": "skill", "id": "my-skill", "interfaces": [{"interface": "my-skill.llm.default", "version": 1, "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"}}]},
    )
    sidecar = {
        "schema_version": 2,
        "blueprint_type": "llm-interface",
        "id": "my-skill.llm.default",
        "version": 1,
        "binding": {"kind": "instruction-file", "path": "SKILL.md"},
        "uses_interfaces": [],
    }
    _write_blueprint(skill / ".SKILL.md.blueprint.yaml", sidecar)
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)

    errors = _mod.validate(tmp_path)
    assert any("is not declared in my-skill.llm.default.uses_interfaces" in error for error in errors)

    sidecar["uses_interfaces"] = [{"interface": "other-skill.llm.default", "version": 1}]
    _write_blueprint(skill / ".SKILL.md.blueprint.yaml", sidecar)
    assert _mod.validate(tmp_path) == []


def test_behavior_source_body_uses_canonical_declared_interfaces(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "my-skill"
    references = skill / "references"
    references.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Body.\n")
    policy = references / "policy.md"
    policy.write_text("Use other-skill.machine.run.\n")
    _write_blueprint(skill / "blueprint.yaml", {"schema_version": 2, "blueprint_type": "skill", "id": "my-skill", "interfaces": [{"interface": "my-skill.llm.default", "version": 1, "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"}}]})
    _write_blueprint(skill / ".SKILL.md.blueprint.yaml", {"schema_version": 2, "blueprint_type": "llm-interface", "id": "my-skill.llm.default", "version": 1, "binding": {"kind": "instruction-file", "path": "SKILL.md"}, "behavior_sources": [{"source": "my-skill.source.policy", "version": 1, "blueprint": {"base": "skill-root", "path": "references/.policy.md.blueprint.yaml"}, "reason": "Policy."}]})
    source_sidecar = {"schema_version": 2, "blueprint_type": "behavior-source", "id": "my-skill.source.policy", "version": 1, "binding": {"kind": "file", "path": "references/policy.md"}, "uses_behavior_sources": [], "uses_interfaces": [{"interface": "other-skill.machine.run", "version": 1}]}
    _write_blueprint(references / ".policy.md.blueprint.yaml", source_sidecar)
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)

    assert _mod.validate(tmp_path) == []

    policy.write_text("Use other-skill.\n")
    errors = _mod.validate(tmp_path)
    assert any("must use canonical interface IDs" in error for error in errors)

    policy.write_text("The command implementation is _cx/run-task.\n")
    errors = _mod.validate(tmp_path)
    assert any(
        "opaque runtime path" in error and "_cx/run-task" in error
        for error in errors
    )


def test_typed_machine_interface_declares_skill_mentions(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: my-skill\n---\nUse other-skill for this.\n"
    )
    (skill / "_rtx").mkdir()
    (skill / "_rtx" / "_worker.py").write_text("class Interface: pass\n")
    _write_blueprint(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "my-skill",
            "interfaces": [
                {
                    "interface": "my-skill.machine.worker",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "_rtx/._worker.py.blueprint.yaml",
                    },
                }
            ],
        },
    )
    _write_blueprint(
        skill / "_rtx" / "._worker.py.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "machine-interface",
            "id": "my-skill.machine.worker",
            "version": 1,
            "binding": {
                "kind": "python-entrypoint",
                "path": "_rtx/_worker.py",
                "symbol": "Interface",
            },
            "uses_interfaces": [
                {"interface": "other-skill.machine.worker", "version": 1}
            ],
        },
    )
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)

    assert _mod.validate(tmp_path) == []


def test_v4_skill_body_uses_module_aggregate_interface_dependencies(
    tmp_path: Path,
) -> None:
    _copy_v4_schema_bundle(tmp_path)
    _copy_v4_loose_mode(tmp_path, "other-skill")
    consumer = _copy_v4_loose_mode(tmp_path, "my-skill")
    (consumer / "SKILL.md").write_text(
        "---\nname: my-skill\n---\n"
        "<!-- BEGIN BLUEPRINT CONTRACT -->\ncontract\n"
        "<!-- END BLUEPRINT CONTRACT -->\n"
        "Use other-skill for this.\n",
        encoding="utf-8",
    )
    (consumer / "_rtx").mkdir()
    (consumer / "_rtx" / "_worker.py").write_text(
        "class Interface: pass\n",
        encoding="utf-8",
    )
    module = yaml.safe_load((consumer / "blueprint.yaml").read_text(encoding="utf-8"))
    module["content"].append(r"_rtx/_worker\.py")
    module["sources"]["my-skill.source.worker"] = {
        "blueprint": {
            "base": "module-root",
            "path": "blueprints/rtx-worker.yaml",
        }
    }
    _write_blueprint(consumer / "blueprint.yaml", module)
    _write_blueprint(
        consumer / "blueprints" / "rtx-worker.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": "my-skill.source.worker",
            "version": 1,
            "gateway": {"path": "_rtx/_worker.py", "language": "Python"},
            "content": [r"_rtx/_worker\.py"],
            "dependencies": [],
            "uses_interfaces": [
                {"interface": "other-skill.interface.default", "version": 1}
            ],
            "interfaces": {},
        },
    )

    assert _mod.validate(tmp_path) == []
