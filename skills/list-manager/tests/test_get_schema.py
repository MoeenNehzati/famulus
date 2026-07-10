"""Tests for get_schema.py: the sole schema-extraction entry point other
scripts in this skill are supposed to use instead of reading schemas/*.json
directly.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "_rtx"
sys.path.insert(0, str(SCRIPTS_DIR))

import _get_schema as get_schema  # noqa: E402


def test_get_schema_whole_returns_properties_and_required():
    whole = get_schema.get_schema("todo", "*")
    assert "properties" in whole
    assert "required" in whole
    assert set(whole["required"]) >= {"id", "title", "created", "state", "deadline"}


def test_get_schema_field_returns_just_that_fields_spec():
    state_spec = get_schema.get_schema("todo", "state")
    assert state_spec == {"enum": ["incomplete", "inprogress", "complete"]}


def test_get_schema_field_differs_by_list_schema():
    """triage has a different state enum than todo -- the resolver
    must pick the right entry type per list schema, not share one cached
    result across schema names."""
    todo_state = get_schema.get_schema("todo", "state")
    triage_state = get_schema.get_schema("triage", "state")
    assert todo_state != triage_state
    assert triage_state == {"enum": ["undecided", "accepted", "rejected"]}


def test_get_schema_unknown_field_returns_none():
    assert get_schema.get_schema("todo", "not_a_real_field") is None


def test_get_schema_default_schema_has_no_state_enum():
    """The generic 'default' list schema's entries (entry.json) carry no
    state field at all -- get_schema must reflect that rather than falling
    back to todo's enum."""
    assert get_schema.get_schema("default", "state") is None


def test_list_schema_exists():
    assert get_schema.list_schema_exists("todo")
    assert not get_schema.list_schema_exists("not-a-schema")


def test_validate_document_accepts_valid_minimal_todo():
    data = {"schema": "todo", "name": "Todo", "categories": []}
    get_schema.validate_document(data, "todo")  # must not raise


def test_validate_document_rejects_bad_schema():
    import jsonschema

    data = {"schema": "todo", "name": "Todo", "categories": "not-a-list"}
    try:
        get_schema.validate_document(data, "todo")
        assert False, "expected ValidationError"
    except jsonschema.ValidationError:
        pass
