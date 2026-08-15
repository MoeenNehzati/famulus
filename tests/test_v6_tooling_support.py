from __future__ import annotations

from pathlib import Path

import yaml

from officina.common.blueprint_template import (
    write_repository_managed_skill_blueprints,
)
from validators.skill.blueprints import repository_schema_version
from validators.skill.dependencies import _CANONICAL_INTERFACE_RE
from validators.skill_runtime_doc_references import (
    _CANONICAL_INTERFACE_RE as _RUNTIME_INTERFACE_RE,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
V6_SCHEMA_ROOT = REPO_ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v6"


def test_repository_schema_marker_accepts_v6(tmp_path: Path) -> None:
    marker = tmp_path / "references" / "blueprint" / "blueprint.yaml"
    marker.parent.mkdir(parents=True)
    marker.write_text("schema_version: 6\n", encoding="utf-8")

    assert repository_schema_version(tmp_path) == 6


def test_dependency_validator_recognizes_dotted_v6_interfaces() -> None:
    interface_id = "example._rtx.source.worker.interface.run"

    assert _CANONICAL_INTERFACE_RE.fullmatch(interface_id)


def test_runtime_reference_validator_masks_dotted_v6_interfaces() -> None:
    interface_id = "example._rtx.interface.run@1"

    assert "_rtx" not in _RUNTIME_INTERFACE_RE.sub("", interface_id)


def test_repository_writer_can_emit_explicit_v6_blueprints(
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
        schema_version=6,
        include_code_child=True,
    )

    parent = yaml.safe_load(outputs[0].read_text(encoding="utf-8"))
    child = yaml.safe_load(outputs[1].read_text(encoding="utf-8"))
    assert parent["schema_version"] == 6
    assert parent["children"] == {"_rtx": {}}
    assert child["schema_version"] == 6
    assert child["id"] == "example._rtx"
