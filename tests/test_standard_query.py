"""Behavioral tests for the explicit common standard-query interface."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import yaml

from officina.common.standard_query import Interface, query


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_MODULE_STANDARD = Path(
    "references/node-standards/python-module.standard.yaml"
)


def test_explicit_query_returns_the_complete_deduplicated_import_closure() -> None:
    """Dropping or duplicating a transitively imported document breaks the result."""

    result = query(
        REPO_ROOT,
        PYTHON_MODULE_STANDARD,
        facts={"task.kind": "refactor"},
    )

    assert result["standard"] == PYTHON_MODULE_STANDARD.as_posix()
    assert result["root_document"] == "node-standards.python-module"
    document_ids = [document["id"] for document in result["documents"]]
    assert set(document_ids) >= {
        "node-standards.python-module",
        "node-standards.module",
        "node-standards.python-node",
        "node-standards.node",
        "node-standards.refactoring",
    }
    assert len(document_ids) == len(set(document_ids))
    assert result["requirements"]["true"]
    assert all(
        requirement["document"] in document_ids
        for state in result["requirements"].values()
        for requirement in state
    )


def test_unrelated_invalid_blueprint_cannot_block_an_explicit_query(
    tmp_path: Path,
) -> None:
    """Reintroducing repository blueprint discovery would reject this fixture."""

    shutil.copytree(REPO_ROOT / "references", tmp_path / "references")
    unrelated = tmp_path / "skills" / "broken"
    unrelated.mkdir(parents=True)
    (unrelated / "blueprint.yaml").write_text("not: [valid\n", encoding="utf-8")

    result = query(
        tmp_path,
        PYTHON_MODULE_STANDARD,
        facts={"task.kind": "refactor"},
    )

    assert result["root_document"] == "node-standards.python-module"


def test_every_view_preserves_root_and_closure_metadata() -> None:
    """A projection must not hide which authoritative documents were queried."""

    facts = {"task.kind": "refactor"}
    selected = {
        "document": "node-standards.python-ood",
        "ref": "python-ood.behavioral-contract#preserve-observables",
    }
    results = [
        query(REPO_ROOT, PYTHON_MODULE_STANDARD, facts=facts),
        query(
            REPO_ROOT,
            PYTHON_MODULE_STANDARD,
            facts=facts,
            view="context",
            refs=[selected],
        ),
        query(
            REPO_ROOT,
            PYTHON_MODULE_STANDARD,
            facts=facts,
            view="remedies",
            refs=[selected],
        ),
        query(REPO_ROOT, PYTHON_MODULE_STANDARD, facts=facts, view="full"),
        query(
            REPO_ROOT,
            PYTHON_MODULE_STANDARD,
            facts=facts,
            record_query={
                "filter": {"path": "$kind", "op": "eq", "value": "definition"},
                "select": ["document", "id"],
            },
        ),
    ]

    expected_documents = results[0]["documents"]
    for result in results:
        assert result["root_document"] == "node-standards.python-module"
        assert result["documents"] == expected_documents


def test_process_interface_accepts_a_standard_path_not_a_target() -> None:
    """Renaming the positional back to a target would restore hidden selection."""

    interface = Interface()
    args = interface.build_parser().parse_args(
        [
            PYTHON_MODULE_STANDARD.as_posix(),
            "--repo-root",
            str(REPO_ROOT),
            "--facts-json",
            json.dumps({"task.kind": "refactor"}),
        ]
    )

    assert args.standard_path == PYTHON_MODULE_STANDARD.as_posix()
    assert not hasattr(args, "target")


def test_common_module_exports_the_process_interface_to_its_consumers() -> None:
    """A missing facade would make the working query unreachable via dispatcher."""

    common = yaml.safe_load(
        (REPO_ROOT / "src/officina/common/blueprint.yaml").read_text(encoding="utf-8")
    )
    source = yaml.safe_load(
        (
            REPO_ROOT
            / "src/officina/common/blueprints/standard-query.yaml"
        ).read_text(encoding="utf-8")
    )

    exported = common["exports"]["common.interface.query-standard"]
    assert exported["source_interface"] == (
        "common.source.standard-query.interface.query-standard"
    )
    assert set(exported["access"]["allowed_callers"]) == {
        "refactor-node",
        "skill-maker",
    }
    binding = source["interfaces"][
        "common.source.standard-query.interface.query-standard"
    ]["process_binding"]
    assert binding["entry"] == "Interface"
    assert binding["patterns"][0]["min_positionals"] == 1
    assert binding["patterns"][0]["max_positionals"] == 1
