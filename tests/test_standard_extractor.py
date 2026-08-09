from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _extract_standard(*args, **kwargs):
    from officina.common.standard_extractor import extract_standard

    return extract_standard(*args, **kwargs)


def test_importing_standard_extractor_does_not_load_unrelated_common_stacks() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SRC_ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import officina.common.standard_extractor; "
                "print('officina.common.docstring' in sys.modules, "
                "'officina.common.visualization' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.stdout.strip() == "False False"


def test_extract_standard_queries_the_validated_import_closure_by_field() -> None:
    result = _extract_standard(
        REPO_ROOT,
        REPO_ROOT
        / "references"
        / "node-standards"
        / "python-module.standard.yaml",
        facts={"task.kind": "refactor", "node.type": "module"},
        query={
            "filter": {
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
            "select": [
                "document",
                "id",
                "kind",
                "ancestors",
                "applicability",
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
    assert [record["id"] for record in result["records"]] == [
        "python-ood.behavioral-contract"
    ]
    [record] = result["records"]
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


def test_extract_standard_select_all_exposes_every_record_kind() -> None:
    result = _extract_standard(
        REPO_ROOT,
        Path("references/node-standards/python-module.standard.yaml"),
        facts={"task.kind": "refactor", "node.type": "module"},
        query={"select": "all"},
    )

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


def test_extract_standard_filters_standard_document_fields() -> None:
    result = _extract_standard(
        REPO_ROOT,
        Path("references/node-standards/python-module.standard.yaml"),
        query={
            "filter": {
                "all": [
                    {"path": "$section", "op": "eq", "value": "document"},
                    {
                        "path": "title",
                        "op": "regex",
                        "pattern": r"^Python Module Standard$",
                    },
                ]
            },
            "select": ["document", "title", "standard_version", "revision"],
        },
    )

    assert result["records"] == [
        {
            "document": "node-standards.python-module",
            "values": {
                "title": "Python Module Standard",
                "standard_version": "1.0.0",
                    "revision": 19,
            },
        }
    ]


def test_extract_standard_rejects_a_stale_import_digest(tmp_path: Path) -> None:
    standards_root = tmp_path / "references" / "standards"
    node_standards_root = tmp_path / "references" / "node-standards"
    shutil.copytree(REPO_ROOT / "references" / "standards", standards_root)
    shutil.copytree(
        REPO_ROOT / "references" / "node-standards", node_standards_root
    )
    leaf_path = node_standards_root / "python-module.standard.yaml"
    leaf = yaml.safe_load(leaf_path.read_text(encoding="utf-8"))
    assert isinstance(leaf, dict)
    mutated = deepcopy(leaf)
    first_import = next(iter(mutated["imports"].values()))
    first_import["digest"] = "sha256:" + ("0" * 64)
    leaf_path.write_text(
        yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="digest mismatch"):
        _extract_standard(tmp_path, leaf_path)


def test_extract_standard_rejects_a_leaf_outside_the_repository(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.standard.yaml"

    with pytest.raises(ValueError, match="outside repository root"):
        _extract_standard(tmp_path, outside)


def test_extract_standard_reports_an_invalid_field_regex() -> None:
    with pytest.raises(ValueError, match="invalid regex"):
        _extract_standard(
            REPO_ROOT,
            Path("references/node-standards/node.standard.yaml"),
            query={
                "filter": {
                    "path": "title",
                    "op": "regex",
                    "pattern": "[",
                }
            },
        )
