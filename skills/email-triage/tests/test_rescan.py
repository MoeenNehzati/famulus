"""Tests for source-identity tracking and deduplicated historical rescan
(feedback item 27):

- The `action`/`triage_action` schemas accept an optional structured `source`
  (message_id + optional mailbox) and reject a `source` missing message_id.
- `_rescan.filter_destination_duplicates` deterministically drops rescan
  candidates already present in a destination list, by source.message_id.
- `--rescan-after`/`--dedup-against` on the fetch-filtered-envelopes runtime
  fetch from an explicit cutoff and exclude already-triaged message_ids,
  via a declared dispatch to list-manager._rtx.interface.cloud-read (never a
  direct import of list-manager's _rtx internals).
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import warnings
from pathlib import Path

import jsonschema
import pytest
import yaml
from jsonschema import Draft7Validator, FormatChecker

warnings.filterwarnings("ignore", category=DeprecationWarning, module="jsonschema")

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = Path(__file__).resolve().parents[1]
LIST_MANAGER_SCHEMAS = REPO_ROOT / "skills" / "list-manager" / "_rtx" / "schemas" / "types"
REPO_SRC = REPO_ROOT / "src"


def _load_schema(name: str):
    schema_path = LIST_MANAGER_SCHEMAS / name
    schema = json.loads(schema_path.read_text())
    resolver = jsonschema.RefResolver(
        base_uri=schema_path.resolve().as_uri(),
        referrer=schema,
    )
    return schema, resolver


def _validate(schema, resolver, data):
    validator = Draft7Validator(schema, resolver=resolver, format_checker=FormatChecker())
    return [e.message for e in validator.iter_errors(data)]


BASE_ACTION = {
    "id": "a1b2c3",
    "title": "Reply to Bob",
    "created": "2026-06-01",
    "state": "incomplete",
    "deadline": "2026-07-01",
}

BASE_TRIAGE_ACTION = {
    "id": "a1b2c3",
    "title": "Maybe attend",
    "created": "2026-06-01",
    "state": "undecided",
    "deadline": "2026-07-01",
}


# ── schema: source field ────────────────────────────────────────────────────


class TestActionSourceField:
    def test_accepts_source_with_message_id(self):
        schema, resolver = _load_schema("action.json")
        data = {**BASE_ACTION, "source": {"message_id": "<abc123@example.com>"}}
        assert _validate(schema, resolver, data) == []

    def test_accepts_source_with_message_id_and_mailbox(self):
        schema, resolver = _load_schema("action.json")
        data = {
            **BASE_ACTION,
            "source": {"message_id": "<abc123@example.com>", "mailbox": "work"},
        }
        assert _validate(schema, resolver, data) == []

    def test_rejects_source_missing_message_id(self):
        schema, resolver = _load_schema("action.json")
        data = {**BASE_ACTION, "source": {"mailbox": "work"}}
        errors = _validate(schema, resolver, data)
        assert any("message_id" in e for e in errors)

    def test_rejects_source_with_unknown_field(self):
        schema, resolver = _load_schema("action.json")
        data = {
            **BASE_ACTION,
            "source": {"message_id": "<abc123@example.com>", "extra": "nope"},
        }
        errors = _validate(schema, resolver, data)
        assert errors  # additionalProperties: false on source


class TestTriageActionSourceField:
    def test_accepts_source_with_message_id(self):
        schema, resolver = _load_schema("triage_action.json")
        data = {**BASE_TRIAGE_ACTION, "source": {"message_id": "<def456@example.com>"}}
        assert _validate(schema, resolver, data) == []

    def test_rejects_source_missing_message_id(self):
        schema, resolver = _load_schema("triage_action.json")
        data = {**BASE_TRIAGE_ACTION, "source": {"mailbox": "personal"}}
        errors = _validate(schema, resolver, data)
        assert any("message_id" in e for e in errors)


# ── _rescan.filter_destination_duplicates ───────────────────────────────────


def _load_rescan_module():
    repo_src = str(REPO_SRC)
    if repo_src not in sys.path:
        sys.path.insert(0, repo_src)
    skill_path = str(SKILL_ROOT)
    if skill_path in sys.path:
        sys.path.remove(skill_path)
    sys.path.insert(0, skill_path)
    for name in tuple(sys.modules):
        if name == "_rtx" or name.startswith("_rtx."):
            sys.modules.pop(name, None)
    return importlib.import_module("_rtx._rescan_filter")


EXISTING_TRIAGE_DOC = {
    "schema": "triage",
    "name": "Triage",
    "categories": [
        {
            "name": "Replies",
            "items": [
                {
                    "id": "111111",
                    "title": "reply to Bob",
                    "created": "2026-06-01",
                    "state": "undecided",
                    "deadline": "2026-07-01",
                    "source": {"message_id": "abc123", "mailbox": "work"},
                }
            ],
        }
    ],
}


def test_filter_destination_duplicates_drops_matching_message_id():
    rescan = _load_rescan_module()
    candidates = [
        {"title": "reply to Bob (dup)", "source": {"message_id": "abc123"}},
        {"title": "reply to Alice", "source": {"message_id": "def456"}},
    ]
    filtered = rescan.filter_destination_duplicates(
        candidates, existing_entries=EXISTING_TRIAGE_DOC
    )
    assert [c["source"]["message_id"] for c in filtered] == ["def456"]


def test_filter_destination_duplicates_accepts_envelope_shaped_candidates():
    """Real rescan candidates are email envelopes with a top-level
    message_id, not pre-shaped list entries."""
    rescan = _load_rescan_module()
    candidates = [
        {"id": "1", "subject": "re: bob", "message_id": "abc123"},
        {"id": "2", "subject": "re: alice", "message_id": "def456"},
    ]
    filtered = rescan.filter_destination_duplicates(
        candidates, existing_entries=EXISTING_TRIAGE_DOC
    )
    assert [c["message_id"] for c in filtered] == ["def456"]


def test_filter_destination_duplicates_passes_through_non_matches_untouched():
    rescan = _load_rescan_module()
    candidates = [{"id": "2", "message_id": "def456", "subject": "unrelated"}]
    filtered = rescan.filter_destination_duplicates(
        candidates, existing_entries=EXISTING_TRIAGE_DOC
    )
    assert filtered == candidates


def test_filter_destination_duplicates_keeps_candidates_with_no_message_id():
    rescan = _load_rescan_module()
    candidates = [{"title": "no source info"}]
    filtered = rescan.filter_destination_duplicates(
        candidates, existing_entries=EXISTING_TRIAGE_DOC
    )
    assert filtered == candidates


def test_filter_destination_duplicates_empty_destination_keeps_everything():
    rescan = _load_rescan_module()
    candidates = [{"message_id": "abc123"}, {"message_id": "def456"}]
    filtered = rescan.filter_destination_duplicates(candidates, existing_entries={})
    assert filtered == candidates


def test_collect_existing_message_ids_recurses_into_children():
    rescan = _load_rescan_module()
    doc = {
        "categories": [
            {
                "items": [
                    {
                        "id": "1",
                        "source": {"message_id": "top"},
                        "children": [
                            {"id": "2", "source": {"message_id": "nested"}},
                        ],
                    }
                ]
            }
        ]
    }
    assert rescan.collect_existing_message_ids(doc) == {"top", "nested"}


# ── --rescan-after / --dedup-against integration ────────────────────────────


def _load_runtime():
    repo_src = str(REPO_SRC)
    if repo_src not in sys.path:
        sys.path.insert(0, repo_src)
    skill_path = str(SKILL_ROOT)
    if skill_path in sys.path:
        sys.path.remove(skill_path)
    sys.path.insert(0, skill_path)
    for name in tuple(sys.modules):
        if name == "_rtx" or name.startswith("_rtx."):
            sys.modules.pop(name, None)
    return importlib.import_module("_rtx._mail_envelope_stream")


def _isolate_filter_state(module, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    module.envelope_gate.default_state_dir = lambda **kwargs: state_dir
    module.envelope_gate.WATERMARK = state_dir / "last_run"
    module.envelope_gate.STATUS_FILE = state_dir / "status.json"


ENVELOPES = [
    {
        "id": "old",
        "flags": [],
        "subject": "already triaged",
        "from": "bob@example.com",
        "date": "2026-01-01T09:00:00-04:00",
        "message_id": "abc123",
    },
    {
        "id": "new",
        "flags": [],
        "subject": "not yet triaged",
        "from": "alice@example.com",
        "date": "2026-01-02T09:00:00-04:00",
        "message_id": "def456",
    },
]


def test_rescan_after_overrides_watermark_without_touching_it(tmp_path, capsys):
    module = _load_runtime()
    _isolate_filter_state(module, tmp_path)
    # No watermark file at all -- --rescan-after must still work and must not
    # write one.

    class RecordingInterface(module.Interface):
        def dispatch(self, key, **kwargs):
            assert key == "mail-list"
            return subprocess.CompletedProcess([], 0, json.dumps(ENVELOPES), "")

    result = RecordingInterface().run(
        argparse.Namespace(
            account="work",
            after="2025-12-01",
            rescan_after="2025-12-31T00:00:00+00:00",
            dedup_against=None,
        )
    )
    captured = capsys.readouterr()
    assert result == 0
    ids = [e["id"] for e in json.loads(captured.out)]
    assert ids == ["old", "new"]
    assert not (tmp_path / "state" / "last_run").exists()


def test_dedup_against_excludes_already_triaged_message_id(tmp_path, capsys):
    module = _load_runtime()
    _isolate_filter_state(module, tmp_path)
    destination_yaml = yaml.safe_dump(EXISTING_TRIAGE_DOC)

    calls = []

    class RecordingInterface(module.Interface):
        def dispatch(self, key, **kwargs):
            calls.append((key, kwargs))
            if key == "mail-list":
                return subprocess.CompletedProcess([], 0, json.dumps(ENVELOPES), "")
            if key == "list-read":
                return subprocess.CompletedProcess([], 0, destination_yaml, "")
            raise AssertionError(f"unexpected dispatch key {key!r}")

    result = RecordingInterface().run(
        argparse.Namespace(
            account="work",
            after="2025-12-01",
            rescan_after="2025-12-31T00:00:00+00:00",
            dedup_against="triage",
        )
    )
    captured = capsys.readouterr()
    assert result == 0
    ids = [e["id"] for e in json.loads(captured.out)]
    assert ids == ["new"]
    assert ("list-read", {"args": ["triage", "--cloud"], "capture_output": True, "text": True}) in calls


def test_dedup_against_reports_delegated_cloud_read_failure(tmp_path, capsys):
    module = _load_runtime()
    _isolate_filter_state(module, tmp_path)

    class FailingInterface(module.Interface):
        def dispatch(self, key, **kwargs):
            if key == "mail-list":
                return subprocess.CompletedProcess([], 0, json.dumps(ENVELOPES), "")
            return subprocess.CompletedProcess([], 3, "", "boom")

    result = FailingInterface().run(
        argparse.Namespace(
            account="work",
            after="2025-12-01",
            rescan_after=None,
            dedup_against="triage",
        )
    )
    captured = capsys.readouterr()
    assert result == 3
    assert captured.out == ""
    assert "cloud-read" in captured.err


def test_dedup_against_reports_malformed_yaml_from_cloud_read(tmp_path, capsys):
    module = _load_runtime()
    _isolate_filter_state(module, tmp_path)

    class MalformedYamlInterface(module.Interface):
        def dispatch(self, key, **kwargs):
            if key == "mail-list":
                return subprocess.CompletedProcess([], 0, json.dumps(ENVELOPES), "")
            if key == "list-read":
                # Exit 0 but garbage stdout that yaml.safe_load cannot parse.
                return subprocess.CompletedProcess(
                    [], 0, "categories: [unclosed", ""
                )
            raise AssertionError(f"unexpected dispatch key {key!r}")

    result = MalformedYamlInterface().run(
        argparse.Namespace(
            account="work",
            after="2025-12-01",
            rescan_after=None,
            dedup_against="triage",
        )
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert "cloud-read" in captured.err
    assert "invalid YAML" in captured.err


def test_declares_dispatch_to_list_manager_cloud_read():
    module = _load_runtime()
    call = module.Interface.dispatches["list-read"]
    assert call.caller_module_id in {"email-triage", "email-triage._rtx"}
    assert (call.target_module_id, call.interface) == (
        "list-manager._rtx",
        "cloud-read",
    )


def test_blueprint_declares_new_flags_and_uses_interfaces():
    blueprint_path = SKILL_ROOT / "_rtx" / "blueprints" / "rtx-mail-envelope-stream.yaml"
    source = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    assert {"interface": "list-manager._rtx.interface.cloud-read", "version": 1} in source[
        "uses_interfaces"
    ]

    list_manager_blueprint = yaml.safe_load(
        (REPO_ROOT / "skills" / "list-manager" / "_rtx" / "blueprint.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert "email-triage" in list_manager_blueprint["exports"][
        "list-manager._rtx.interface.cloud-read"
    ]["access"]["allowed_callers"]
