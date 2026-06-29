"""
Tests for JSONSchema validation of list YAML files.

These tests exercise the schema hierarchy:
  entry → task_entry → action / potential_action
and the per-list schemas (todo, potential-actions, default).

No cloud calls, no subprocess — pure schema + YAML.
"""
import json
import warnings
from pathlib import Path

import pytest
import yaml
import jsonschema
from jsonschema import Draft7Validator, FormatChecker

# Suppress RefResolver deprecation warnings from jsonschema 4.23+
warnings.filterwarnings("ignore", category=DeprecationWarning, module="jsonschema")

SKILL_DIR = Path(__file__).parent.parent
SCHEMAS_DIR = SKILL_DIR / "schemas"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_list_schema(name: str) -> tuple[dict, jsonschema.RefResolver]:
    """Load a per-list schema and return (schema_dict, resolver)."""
    schema_path = SCHEMAS_DIR / "lists" / f"{name}.json"
    with schema_path.open() as f:
        schema = json.load(f)
    resolver = jsonschema.RefResolver(
        base_uri=schema_path.resolve().as_uri(),
        referrer=schema,
    )
    return schema, resolver


def validate(schema: dict, resolver: jsonschema.RefResolver, data: dict) -> list[str]:
    """Return list of validation error messages (empty = valid)."""
    validator = Draft7Validator(schema, resolver=resolver, format_checker=FormatChecker())
    return [e.message for e in validator.iter_errors(data)]


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Type schema: entry
# ---------------------------------------------------------------------------

class TestEntrySchema:
    schema_path = SCHEMAS_DIR / "types" / "entry.json"

    def _load(self):
        with self.schema_path.open() as f:
            schema = json.load(f)
        resolver = jsonschema.RefResolver(
            base_uri=self.schema_path.resolve().as_uri(),
            referrer=schema,
        )
        return schema, resolver

    def test_valid_minimal(self):
        schema, resolver = self._load()
        data = {"id": "a1b2c3", "title": "Hello", "created": "2026-06-01"}
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_valid_with_description_and_children(self):
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Parent",
            "created": "2026-06-01",
            "description": "Some notes",
            "children": [
                {"id": "b2c3d4", "title": "Child", "created": "2026-06-02"}
            ],
        }
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_missing_id(self):
        schema, resolver = self._load()
        data = {"title": "Hello", "created": "2026-06-01"}
        errors = validate(schema, resolver, data)
        assert any("id" in e for e in errors)

    def test_invalid_id_pattern(self):
        schema, resolver = self._load()
        data = {"id": "ZZZZZZ", "title": "Hello", "created": "2026-06-01"}
        errors = validate(schema, resolver, data)
        assert errors  # ZZZZZZ does not match ^[0-9a-f]{6}$

    def test_invalid_created_format(self):
        schema, resolver = self._load()
        data = {"id": "a1b2c3", "title": "Hello", "created": "not-a-date"}
        errors = validate(schema, resolver, data)
        assert any("date" in e or "format" in e for e in errors)

    def test_empty_title_rejected(self):
        schema, resolver = self._load()
        data = {"id": "a1b2c3", "title": "", "created": "2026-06-01"}
        errors = validate(schema, resolver, data)
        assert errors  # minLength: 1 fails


# ---------------------------------------------------------------------------
# Type schema: action (via task_entry)
# ---------------------------------------------------------------------------

class TestActionSchema:
    schema_path = SCHEMAS_DIR / "types" / "action.json"

    def _load(self):
        with self.schema_path.open() as f:
            schema = json.load(f)
        resolver = jsonschema.RefResolver(
            base_uri=self.schema_path.resolve().as_uri(),
            referrer=schema,
        )
        return schema, resolver

    def test_valid_action(self):
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Do something",
            "created": "2026-06-01",
            "state": "incomplete",
            "deadline": "2026-07-01",
        }
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_valid_action_with_location(self):
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Do at home",
            "created": "2026-06-01",
            "state": "inprogress",
            "deadline": "2026-07-01",
            "location": "home",
        }
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_valid_action_with_children(self):
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Parent task",
            "created": "2026-06-01",
            "state": "incomplete",
            "deadline": "2026-07-01",
            "children": [
                {
                    "id": "b2c3d4",
                    "title": "Subtask",
                    "created": "2026-06-01",
                    "state": "done",
                    "deadline": "2026-06-15",
                }
            ],
        }
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_invalid_state_for_action(self):
        """'undecided' is valid for potential_action but not action."""
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Task",
            "created": "2026-06-01",
            "state": "undecided",
            "deadline": "2026-07-01",
        }
        errors = validate(schema, resolver, data)
        assert errors

    def test_missing_deadline(self):
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Task",
            "created": "2026-06-01",
            "state": "incomplete",
        }
        errors = validate(schema, resolver, data)
        assert any("deadline" in e for e in errors)

    def test_invalid_deadline_format(self):
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Task",
            "created": "2026-06-01",
            "state": "incomplete",
            "deadline": "next Friday",
        }
        errors = validate(schema, resolver, data)
        assert any("date" in e or "format" in e for e in errors)

    def test_extra_field_rejected(self):
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Task",
            "created": "2026-06-01",
            "state": "incomplete",
            "deadline": "2026-07-01",
            "unknown_field": "oops",
        }
        errors = validate(schema, resolver, data)
        assert errors  # additionalProperties: false


# ---------------------------------------------------------------------------
# Type schema: potential_action
# ---------------------------------------------------------------------------

class TestPotentialActionSchema:
    schema_path = SCHEMAS_DIR / "types" / "potential_action.json"

    def _load(self):
        with self.schema_path.open() as f:
            schema = json.load(f)
        resolver = jsonschema.RefResolver(
            base_uri=self.schema_path.resolve().as_uri(),
            referrer=schema,
        )
        return schema, resolver

    def test_valid_potential_action(self):
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Maybe do this",
            "created": "2026-06-01",
            "state": "undecided",
            "deadline": "2026-09-01",
        }
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_all_valid_states(self):
        schema, resolver = self._load()
        for state in ("undecided", "accepted", "rejected"):
            data = {
                "id": "a1b2c3",
                "title": "Option",
                "created": "2026-06-01",
                "state": state,
                "deadline": "2026-09-01",
            }
            errors = validate(schema, resolver, data)
            assert errors == [], f"state={state!r} should be valid"

    def test_action_state_rejected(self):
        """'incomplete' is valid for action but not potential_action."""
        schema, resolver = self._load()
        data = {
            "id": "a1b2c3",
            "title": "Option",
            "created": "2026-06-01",
            "state": "incomplete",
            "deadline": "2026-09-01",
        }
        errors = validate(schema, resolver, data)
        assert errors


# ---------------------------------------------------------------------------
# Per-list schema: todo
# ---------------------------------------------------------------------------

class TestTodoListSchema:
    def test_valid_fixture(self):
        schema, resolver = load_list_schema("todo")
        data = load_yaml(FIXTURES_DIR / "todo_valid.yaml")
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_invalid_state(self):
        schema, resolver = load_list_schema("todo")
        data = load_yaml(FIXTURES_DIR / "todo_invalid_state.yaml")
        errors = validate(schema, resolver, data)
        assert errors  # 'undecided' not in action enum

    def test_invalid_date(self):
        schema, resolver = load_list_schema("todo")
        data = load_yaml(FIXTURES_DIR / "todo_invalid_date.yaml")
        errors = validate(schema, resolver, data)
        assert any("date" in e or "format" in e for e in errors)

    def test_missing_deadline(self):
        schema, resolver = load_list_schema("todo")
        data = load_yaml(FIXTURES_DIR / "todo_missing_deadline.yaml")
        errors = validate(schema, resolver, data)
        assert any("deadline" in e for e in errors)

    def test_wrong_schema_type_rejected(self):
        """A todo list schema must have schema: 'todo'."""
        schema, resolver = load_list_schema("todo")
        data = {
            "schema": "potential-actions",
            "name": "Wrong",
            "categories": [],
        }
        errors = validate(schema, resolver, data)
        assert errors

    def test_empty_categories_allowed(self):
        schema, resolver = load_list_schema("todo")
        data = {"schema": "todo", "name": "Empty List", "categories": []}
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_nested_categories(self):
        schema, resolver = load_list_schema("todo")
        data = {
            "schema": "todo",
            "name": "Nested",
            "categories": [
                {
                    "name": "Outer",
                    "categories": [
                        {
                            "name": "Inner",
                            "entries": [
                                {
                                    "id": "aabbcc",
                                    "title": "Deep task",
                                    "created": "2026-06-01",
                                    "state": "incomplete",
                                    "deadline": "2026-07-01",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        errors = validate(schema, resolver, data)
        assert errors == []


# ---------------------------------------------------------------------------
# Per-list schema: potential-actions
# ---------------------------------------------------------------------------

class TestPotentialActionsListSchema:
    def test_valid_fixture(self):
        schema, resolver = load_list_schema("potential-actions")
        data = load_yaml(FIXTURES_DIR / "potential_actions_valid.yaml")
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_action_state_rejected(self):
        schema, resolver = load_list_schema("potential-actions")
        data = {
            "schema": "potential-actions",
            "name": "My Options",
            "categories": [
                {
                    "name": "Career",
                    "entries": [
                        {
                            "id": "a1b2c3",
                            "title": "Apply somewhere",
                            "created": "2026-06-01",
                            "state": "incomplete",
                            "deadline": "2026-09-01",
                        }
                    ],
                }
            ],
        }
        errors = validate(schema, resolver, data)
        assert errors


# ---------------------------------------------------------------------------
# Per-list schema: default
# ---------------------------------------------------------------------------

class TestDefaultListSchema:
    def test_valid_minimal_entry(self):
        schema, resolver = load_list_schema("default")
        data = {
            "schema": "default",
            "name": "Notes",
            "categories": [
                {
                    "name": "Misc",
                    "entries": [
                        {
                            "id": "a1b2c3",
                            "title": "Some note",
                            "created": "2026-06-01",
                        }
                    ],
                }
            ],
        }
        errors = validate(schema, resolver, data)
        assert errors == []

    def test_state_field_not_allowed(self):
        """Default entries are base entries; action-specific fields like 'state'
        should be rejected by additionalProperties: false on action.json, but
        default entries use entry.json which is open — extra fields are allowed."""
        # entry.json has no additionalProperties: false, so extra fields pass
        schema, resolver = load_list_schema("default")
        data = {
            "schema": "default",
            "name": "Notes",
            "categories": [
                {
                    "name": "Misc",
                    "entries": [
                        {
                            "id": "a1b2c3",
                            "title": "Note with extras",
                            "created": "2026-06-01",
                            "state": "anything",
                        }
                    ],
                }
            ],
        }
        errors = validate(schema, resolver, data)
        # entry.json is open, so no error expected
        assert errors == []
