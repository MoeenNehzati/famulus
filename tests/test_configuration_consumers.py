"""Integration tests for repository configuration consumers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml

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


def test_repository_configuration_documents_use_central_schema() -> None:
    relative_paths = (
        "src/officina/docstring/config.yaml",
        "references/blueprint-schema/config.yaml",
        "references/certification-policy/node-hash-policy.yaml",
        "src/officina/recurring/default_jobs.yaml",
    )

    loaded = {
        relative_path: load_configuration(REPO_ROOT / relative_path)
        for relative_path in relative_paths
    }

    empty_paths = [path for path, document in loaded.items() if not document]
    assert empty_paths == []


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


def test_recurring_jobs_config_helper_declares_direct_owner_and_consumers() -> None:
    blueprint_dir = REPO_ROOT / "skills/recurring-tasks/_rtx/blueprints"
    source_id = "recurring-tasks._rtx.source.rtx-jobs-config"
    source = yaml.safe_load(
        (blueprint_dir / "rtx-jobs-config.yaml").read_text(encoding="utf-8")
    )

    assert source["id"] == source_id
    assert source["gateway"] == {
        "language": "Python",
        "path": "_jobs_config.py",
    }
    assert source["content"] == [r"_jobs_config\.py"]

    declarations = (
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in blueprint_dir.glob("*.yaml")
    )
    consumers = {
        declaration["id"]
        for declaration in declarations
        if any(
            dependency.get("source") == source_id
            for dependency in declaration.get("dependencies", ())
        )
    }
    assert consumers == {"recurring-tasks._rtx.source.rtx-job-executor"}


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
