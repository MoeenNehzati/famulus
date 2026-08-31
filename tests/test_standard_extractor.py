from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
STANDARD_CLOSURE = (
    Path("references/standards-schema/validate_standard_v6.py"),
    Path("references/standards-schema/standard-v6.schema.json"),
    Path("references/node-standards/python-module.standard.yaml"),
    Path("references/node-standards/module.standard.yaml"),
    Path("references/node-standards/python-node.standard.yaml"),
    Path("references/node-standards/node.standard.yaml"),
    Path("references/node-standards/python-ood.standard.yaml"),
    Path("references/node-standards/refactoring.standard.yaml"),
    Path("references/node-standards/semantic-review.md"),
)
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _extract_standard(*args, **kwargs):
    from officina.standards.extractor import extract_standard

    return extract_standard(*args, **kwargs)


def test_importing_standard_extractor_does_not_load_unrelated_common_stacks() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SRC_ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import officina.standards.extractor; "
                "print('officina.docstring' in sys.modules, "
                "'officina.visualization' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.stdout.strip() == "False False"


def test_extract_standard_materializes_and_queries_the_validated_closure() -> None:
    result = _extract_standard(
        REPO_ROOT,
        REPO_ROOT
        / "references"
        / "node-standards"
        / "python-module.standard.yaml",
        facts={"task.kind": "refactor", "node.type": "module"},
        query={
            "filter": {
                "any": [
                    {
                        "all": [
                            {"path": "$kind", "op": "eq", "value": "rule"},
                            {
                                "path": "$id",
                                "op": "regex",
                                "pattern": r"^python-ood\.",
                            },
                            {
                                "path": "assertions.*.statement",
                                "op": "regex",
                                "pattern": "published names|subclass behavior",
                                "flags": "i",
                            },
                        ]
                    },
                    {
                        "path": "$id",
                        "op": "eq",
                        "value": "skill-guidelines.adding-validator",
                    },
                    {
                        "path": "$section",
                        "op": "regex",
                        "pattern": (
                            r"^(links|artifacts|checks|tests|assurances|"
                            r"semantic_reviews)$"
                        ),
                    },
                    {
                        "all": [
                            {
                                "path": "$section",
                                "op": "eq",
                                "value": "document",
                            },
                            {
                                "path": "title",
                                "op": "regex",
                                "pattern": r"^Python Module Standard$",
                            },
                        ]
                    },
                ]
            },
            "select": [
                "document",
                "id",
                "section",
                "kind",
                "ancestors",
                "applicability",
                "missing_facts",
                "title",
                "standard_version",
                "revision",
                {
                    "as": "statements",
                    "path": "assertions.*.statement",
                },
            ],
            "explain": True,
        },
    )

    document_ids = [document["id"] for document in result["documents"]]
    assert {
        "node-standards.refactoring",
        "node-standards.node",
        "node-standards.module",
        "node-standards.python-node",
        "node-standards.python-ood",
        "node-standards.python-module",
    }.issubset(document_ids)
    assert document_ids[-1] == "node-standards.python-module"

    sections = {record["section"] for record in result["records"]}
    assert {
        "standards",
        "links",
        "artifacts",
        "checks",
        "tests",
        "assurances",
        "semantic_reviews",
    }.issubset(sections)
    unknown = next(
        record
        for record in result["records"]
        if record["id"] == "skill-guidelines.adding-validator"
    )
    assert unknown["applicability"] == "unknown"
    assert unknown["missing_facts"] == ["node.is-repository-validator"]

    record = next(
        record
        for record in result["records"]
        if record["id"] == "python-ood.behavioral-contract"
    )
    assert record["document"] == "node-standards.python-ood"
    assert record["kind"] == "rule"
    assert record["ancestors"] == ["python-ood.behavior-preservation"]
    assert record["applicability"] == "true"
    assert record["values"]["statements"]
    assert {match["selector"] for match in record["matches"]} == {
        "$kind",
        "$id",
        "assertions.*.statement",
    }
    document_records = [
        record for record in result["records"] if record["section"] == "document"
    ]
    assert [record["id"] for record in document_records] == [
        "node-standards.python-module"
    ]
    [document] = document_records
    assert document["document"] == "node-standards.python-module"
    assert document["values"] == {
        "missing_facts": [],
        "title": "Python Module Standard",
        "standard_version": "1.0.0",
        "revision": 22,
        "statements": [],
    }


def test_extract_standard_rejects_a_stale_import_digest(tmp_path: Path) -> None:
    for relative_path in STANDARD_CLOSURE:
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative_path, destination)
    leaf_path = tmp_path / STANDARD_CLOSURE[2]
    leaf = yaml.safe_load(leaf_path.read_text(encoding="utf-8"))
    assert isinstance(leaf, dict)
    first_import = next(iter(leaf["imports"].values()))
    first_import["digest"] = "sha256:" + ("0" * 64)
    leaf_path.write_text(
        yaml.safe_dump(leaf, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="digest mismatch"):
        _extract_standard(tmp_path, leaf_path)


def test_extract_standard_rejects_a_leaf_outside_the_repository(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.standard.yaml"

    with pytest.raises(ValueError, match="outside repository root"):
        _extract_standard(tmp_path, outside)


def test_query_standard_records_reports_an_invalid_field_regex() -> None:
    from officina.standards.extractor import (
        StandardRecord,
        query_standard_records,
    )

    record = StandardRecord(
        document="standard",
        section="document",
        record_id="standard",
        kind="standard-document",
        path="document",
        ancestors=(),
        applicability="true",
        missing_facts=(),
        data={"title": "Standard"},
    )
    with pytest.raises(ValueError, match="invalid regex"):
        query_standard_records(
            [record],
            query={
                "filter": {
                    "path": "title",
                    "op": "regex",
                    "pattern": "[",
                }
            },
        )
