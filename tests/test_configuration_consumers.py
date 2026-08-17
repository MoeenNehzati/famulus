"""Integration tests for repository configuration consumers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from officina.blueprints.graph import load_repository_blueprint_graph
from officina.certification.hashing import (
    CertificationHashError,
    load_node_hash_policy,
)
from officina.configuration.configured_schema import ConfiguredSchemaError, load_configuration
from officina.docstring.policy import load_docstring_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/officina/docstring/config.yaml",
        "references/blueprint/config.yaml",
        "references/certification/node-hash-policy.yaml",
        "skills/recurring-tasks/_rtx/jobs.yaml",
    ],
)
def test_repository_configuration_documents_use_central_schema(
    relative_path: str,
) -> None:
    loaded = load_configuration(REPO_ROOT / relative_path)
    assert loaded


def test_docstring_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("allowed_abs: [officina]\nunknown_setting: true\n", encoding="utf-8")

    with pytest.raises(ConfiguredSchemaError, match="invalid configuration"):
        load_docstring_config(path)


def test_node_hash_policy_preserves_domain_error(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "policy_version: 1\n"
        "path_syntax: gitignore\n"
        "starting_set: git-tracked-directly-owned-regular-files\n"
        "rules: []\n",
        encoding="utf-8",
    )

    with pytest.raises(CertificationHashError, match="node hash policy"):
        load_node_hash_policy(path)


def test_recurring_jobs_shared_loader_validates_documents(tmp_path: Path) -> None:
    module_path = REPO_ROOT / "skills/recurring-tasks/_rtx/_jobs_config.py"
    assert module_path.is_file(), "recurring-tasks must provide one shared config loader"
    jobs_config = _load_module("recurring_jobs_config", module_path)
    path = tmp_path / "jobs.yaml"
    path.write_text("jobs:\n  - name: missing-fields\n", encoding="utf-8")

    with pytest.raises(ConfiguredSchemaError, match="invalid configuration"):
        jobs_config.load_jobs(path)


def test_recurring_jobs_config_helper_has_direct_owner_and_import_dependencies() -> None:
    graph = load_repository_blueprint_graph(REPO_ROOT)
    helper = REPO_ROOT / "skills/recurring-tasks/_rtx/_jobs_config.py"
    source_id = "recurring-tasks._rtx.source.rtx-jobs-config"

    assert graph.direct_file_owners[helper] == source_id
    consumers = {
        "recurring-tasks._rtx.source.rtx-healthcheck-probe",
        "recurring-tasks._rtx.source.rtx-job-control",
        "recurring-tasks._rtx.source.rtx-job-executor",
        "recurring-tasks._rtx.source.rtx-unit-writer",
    }
    actual = {
        edge.source_id
        for edge in graph.node_edges
        if edge.relation == "uses-source" and edge.target_id == source_id
    }
    assert actual == consumers


def test_cloud_files_config_rejects_unknown_fields(tmp_path: Path) -> None:
    module = _load_module(
        "cloud_drive_gateway",
        REPO_ROOT / "skills/cloud-files/_rtx/_drive_gateway.py",
    )
    config_dir = tmp_path / ".config" / "cloud-files"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"remote_llm_root": "assistant/", "unknown": True}),
        encoding="utf-8",
    )

    with pytest.raises(module.CloudFilesError, match="configuration"):
        module.load_config(tmp_path)
