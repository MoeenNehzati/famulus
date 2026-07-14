from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
MODULE_PATH = SKILL_ROOT / "_rtx" / "_category_cache.py"
TODO_YAML = """\
schema: todo
name: todo
categories:
- name: Dev
  categories:
  - name: Tasks
    entries: []
"""


def category_cache_module():
    assert MODULE_PATH.is_file(), "category-cache interface module has not been created"
    sys.path.insert(0, str(REPO_ROOT / "src"))
    sys.path.insert(0, str(SKILL_ROOT))
    sys.modules.pop("_rtx._category_cache", None)
    return importlib.import_module("_rtx._category_cache")


def fake_download(calls: list[str]):
    def download(name: str, destination: Path) -> None:
        calls.append(name)
        destination.write_text(TODO_YAML, encoding="utf-8")

    return download


def test_first_lookup_downloads_paths_and_leaves_nineteen_uses(tmp_path, monkeypatch):
    category_cache = category_cache_module()
    calls: list[str] = []
    monkeypatch.setattr(category_cache.cloud_transport, "download_list", fake_download(calls))

    paths, remaining_uses = category_cache.load_paths("todo", cache_dir=tmp_path)

    assert paths == ["Dev", "Dev/Tasks"]
    assert remaining_uses == 19
    assert calls == ["todo"]
    assert yaml.safe_load((tmp_path / "categories.todo.yaml").read_text(encoding="utf-8")) == {
        "name": "todo",
        "paths": ["Dev", "Dev/Tasks"],
        "remaining_uses": 19,
        "reset_uses": 20,
    }


def test_cached_lookup_decrements_without_downloading(tmp_path, monkeypatch):
    category_cache = category_cache_module()
    category_cache.write_cache(tmp_path / "categories.todo.yaml", "todo", ["Dev"], 2)
    monkeypatch.setattr(category_cache.cloud_transport, "download_list", pytest.fail)

    paths, remaining_uses = category_cache.load_paths("todo", cache_dir=tmp_path)

    assert paths == ["Dev"]
    assert remaining_uses == 1


def test_zero_countdown_refreshes_and_explicit_refresh_resets_counter(tmp_path, monkeypatch):
    category_cache = category_cache_module()
    category_cache.write_cache(tmp_path / "categories.todo.yaml", "todo", ["Old"], 0)
    calls: list[str] = []
    monkeypatch.setattr(category_cache.cloud_transport, "download_list", fake_download(calls))

    paths, remaining_uses = category_cache.load_paths("todo", cache_dir=tmp_path)
    refreshed_paths, refreshed_remaining_uses = category_cache.load_paths(
        "todo", refresh=True, cache_dir=tmp_path
    )

    assert paths == ["Dev", "Dev/Tasks"]
    assert remaining_uses == 19
    assert refreshed_paths == ["Dev", "Dev/Tasks"]
    assert refreshed_remaining_uses == 20
    assert calls == ["todo", "todo"]


def test_cache_files_are_isolated_by_list_name(tmp_path, monkeypatch):
    category_cache = category_cache_module()
    calls: list[str] = []
    monkeypatch.setattr(category_cache.cloud_transport, "download_list", fake_download(calls))

    category_cache.load_paths("todo", cache_dir=tmp_path)
    category_cache.load_paths("triage", cache_dir=tmp_path)

    assert calls == ["todo", "triage"]
    assert (tmp_path / "categories.todo.yaml").is_file()
    assert (tmp_path / "categories.triage.yaml").is_file()


def test_skill_requires_cached_category_lookup_and_stale_path_prompt():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "cloud-list-categories" in body
    assert "refresh" in body.lower()
    assert "Do not infer a replacement category" in body
