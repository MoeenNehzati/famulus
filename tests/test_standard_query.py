"""Behavioral tests for the explicit common standard-query interface."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

import pytest
import yaml

from officina.standards.extractor import extract_standard
from officina.standards.query import (
    Interface,
    query,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_MODULE_STANDARD = Path(
    "references/node-standards/python-module.standard.yaml"
)
REFACTORING_STANDARD = Path("references/node-standards/refactoring.standard.yaml")
REFACTOR_FACTS = {"task.kind": "refactor"}
REFACTOR_QUERY = {
    "filter": {
        "path": "$section",
        "op": "regex",
        "pattern": (
            r"^(standards|imports|links|artifacts|checks|tests|assurances|"
            r"semantic_reviews|evidence_claims)$"
        ),
    },
    "select": "all",
}
ISOLATED_REFACTORING_QUERY_FILES = (
    Path("references/standards-schema/validate_standard_v6.py"),
    Path("references/standards-schema/standard-v6.schema.json"),
    Path("references/node-standards/refactoring.standard.yaml"),
)


def test_unrelated_invalid_blueprint_cannot_block_an_explicit_query(
    tmp_path: Path,
) -> None:
    """Reintroducing repository blueprint discovery would reject this fixture."""

    for relative_path in ISOLATED_REFACTORING_QUERY_FILES:
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative_path, destination)
    unrelated = tmp_path / "skills" / "broken"
    unrelated.mkdir(parents=True)
    (unrelated / "blueprint.yaml").write_text("not: [valid\n", encoding="utf-8")

    result = query(
        tmp_path,
        REFACTORING_STANDARD,
        facts=REFACTOR_FACTS,
    )

    assert result["standard"] == REFACTORING_STANDARD.as_posix()
    assert result["root_document"] == "node-standards.refactoring"
    assert [document["id"] for document in result["documents"]] == [
        "node-standards.refactoring"
    ]


def test_every_view_preserves_root_and_closure_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every projection preserves one complete, immutable live closure."""

    selected = {
        "document": "node-standards.python-ood",
        "ref": "python-ood.behavioral-contract#preserve-observables",
    }
    record_query = {
        "filter": {"path": "$kind", "op": "eq", "value": "definition"},
        "select": ["document", "id"],
    }
    extraction_calls: list[
        tuple[Path, Path, dict[str, Any] | None, dict[str, Any]]
    ] = []
    captured_extraction: dict[str, dict[str, Any]] = {}

    def capture_python_module_extraction(
        repo_root: Path,
        standard_path: Path,
        *,
        facts: dict[str, Any] | None,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        extraction_calls.append((repo_root, standard_path, facts, query))
        extracted = extract_standard(
            repo_root,
            standard_path,
            facts=facts,
            query=query,
        )
        captured_extraction["value"] = extracted
        return extracted

    monkeypatch.setattr(
        "officina.standards.query.extract_standard",
        capture_python_module_extraction,
    )
    requirements = query(
        REPO_ROOT,
        PYTHON_MODULE_STANDARD,
        facts=REFACTOR_FACTS,
    )
    extraction = captured_extraction["value"]
    extraction_snapshot = json.dumps(extraction, sort_keys=True)

    def reuse_python_module_extraction(
        repo_root: Path,
        standard_path: Path,
        *,
        facts: dict[str, Any] | None,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        extraction_calls.append((repo_root, standard_path, facts, query))
        return extraction

    monkeypatch.setattr(
        "officina.standards.query.extract_standard",
        reuse_python_module_extraction,
    )
    results = [
        requirements,
        query(
            REPO_ROOT,
            PYTHON_MODULE_STANDARD,
            facts=REFACTOR_FACTS,
            view="context",
            refs=[selected],
        ),
        query(
            REPO_ROOT,
            PYTHON_MODULE_STANDARD,
            facts=REFACTOR_FACTS,
            view="remedies",
            refs=[selected],
        ),
        query(
            REPO_ROOT,
            PYTHON_MODULE_STANDARD,
            facts=REFACTOR_FACTS,
            view="full",
        ),
        query(
            REPO_ROOT,
            PYTHON_MODULE_STANDARD,
            facts=REFACTOR_FACTS,
            record_query=record_query,
        ),
    ]

    assert requirements["standard"] == PYTHON_MODULE_STANDARD.as_posix()
    document_ids = [document["id"] for document in requirements["documents"]]
    assert set(document_ids) >= {
        "node-standards.python-module",
        "node-standards.module",
        "node-standards.python-node",
        "node-standards.node",
        "node-standards.refactoring",
    }
    assert len(document_ids) == len(set(document_ids))
    assert requirements["requirements"]["true"]
    assert all(
        requirement["document"] in document_ids
        for state in requirements["requirements"].values()
        for requirement in state
    )

    expected_documents = extraction["documents"]
    for result in results:
        assert result["repository_root"] == str(REPO_ROOT.resolve())
        assert result["root_document"] == "node-standards.python-module"
        assert result["documents"] == expected_documents

    requirements, context, remedies, full, records = results
    assert requirements["view"] == "requirements" and {
        "requirements",
        "context_index",
    } <= requirements.keys()
    assert context["view"] == "context" and "context" in context
    assert remedies["view"] == "remedies" and {
        "remedies",
        "procedures",
    } <= remedies.keys()
    assert full["view"] == "full" and {
        "items",
        "remedies",
        "evidence",
        "artifacts",
    } <= full.keys()
    assert records["view"] == "query" and "records" in records
    assert extraction_calls == [
        (REPO_ROOT.resolve(), PYTHON_MODULE_STANDARD, REFACTOR_FACTS, query_spec)
        for query_spec in (
            REFACTOR_QUERY,
            REFACTOR_QUERY,
            REFACTOR_QUERY,
            REFACTOR_QUERY,
            record_query,
        )
    ]
    assert json.dumps(extraction, sort_keys=True) == extraction_snapshot


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
        (REPO_ROOT / "src/officina/standards/blueprint.yaml").read_text(encoding="utf-8")
    )
    source = yaml.safe_load(
        (
            REPO_ROOT
            / "src/officina/standards/blueprints/query.yaml"
        ).read_text(encoding="utf-8")
    )

    exported = common["exports"]["standards.interface.query-standard"]
    assert exported["source_interface"] == (
        "standards.source.query.interface.query-standard"
    )
    assert set(exported["access"]["allowed_callers"]) == {
        "refactor-node",
        "skill-maker",
    }
    binding = source["interfaces"][
        "standards.source.query.interface.query-standard"
    ]["process_binding"]
    assert binding["entry"] == "Interface"
    assert binding["patterns"][0]["min_positionals"] == 1
    assert binding["patterns"][0]["max_positionals"] == 1
