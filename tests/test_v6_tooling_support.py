from __future__ import annotations

from pathlib import Path

import yaml

from officina.blueprints.template import (
    write_repository_managed_skill_blueprints,
)
from validators.skill.dependencies import _CANONICAL_INTERFACE_RE


REPO_ROOT = Path(__file__).resolve().parents[1]
V6_SCHEMA_ROOT = REPO_ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v6"


def test_dependency_validator_recognizes_dotted_v6_interfaces() -> None:
    interface_id = "example._rtx.source.worker.interface.run"

    assert _CANONICAL_INTERFACE_RE.fullmatch(interface_id)


def test_repository_writer_emits_v6_blueprints(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "example"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("# Example\n", encoding="utf-8")

    outputs = write_repository_managed_skill_blueprints(
        "example",
        domain="software-development",
        topics=("repository-workflow",),
        visibility="listed",
        activated_by=("user-request",),
        persistent_modifier=False,
        repo_root=tmp_path,
        schema_root=V6_SCHEMA_ROOT,
        include_code_child=True,
    )

    parent = yaml.safe_load(outputs[0].read_text(encoding="utf-8"))
    child = yaml.safe_load(outputs[1].read_text(encoding="utf-8"))
    assert parent["schema_version"] == 6
    assert parent["children"] == {"_rtx": {}}
    assert child["schema_version"] == 6
    assert child["id"] == "example._rtx"
