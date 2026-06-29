# List Manager YAML + JSONSchema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Markdown-based list-manager with a YAML-based system validated against JSONSchema, using a pure-Python local file operator (`lists.py`) with a clean subcommand API.

**Architecture:** `lists.py` is a pure local file operator (no cloud knowledge). The skill downloads lists via cloud-files skill, calls `lists.py` subcommands, then uploads back. JSONSchema type hierarchy (`entry → task_entry → action / potential_action`) enforces structure; per-list schemas (`schemas/lists/*.json`) constrain which entry type each list accepts. Validation runs inside `lists.py` before any file write.

**Tech Stack:** Python 3.10+, `pyyaml`, `jsonschema`, `dateparser` (migration only), pytest

**Spec:** `docs/superpowers/specs/2026-06-29-list-manager-yaml-schema-design.md`

---

## Task 0: Install Python dependencies

`pyyaml` and `jsonschema` are already available in the environment. `dateparser`
is needed for `migrate-md` and must be installed as part of the skill setup.

**Files:**
- Modify: `install-assistant-tools` script (or wherever the assistant installs skill deps)

- [ ] **Step 1: Confirm existing deps**

```bash
python3 -c "import yaml, jsonschema; print('pyyaml and jsonschema: ok')"
```

Expected: `pyyaml and jsonschema: ok`

- [ ] **Step 2: Install dateparser**

```bash
pip install dateparser
```

Expected: installs successfully.

- [ ] **Step 3: Verify dateparser**

```bash
python3 -c "import dateparser; print(dateparser.parse('next Friday'))"
```

Expected: prints a datetime object (not None).

- [ ] **Step 4: Add dateparser to the install-assistant-tools script**

Find the install-assistant-tools script:

```bash
find ~/.claude -name "install-assistant-tools*" -o -name "setup.py" 2>/dev/null | head -5
```

Open the script and add `pip install dateparser` (or add `dateparser` to whatever
requirements list it manages) alongside any other skill Python deps. The exact
location depends on the install-assistant-tools skill's structure — check
`~/.claude/plugins/` or the skills directory for the install script.

- [ ] **Step 5: Commit**

```bash
git add <install-script-path>
git commit -m "feat(list-manager): add dateparser to install deps"
```

---

## File Map

**Create:**
- `schemas/types/entry.json` — base entry type (id, title, created, description, children)
- `schemas/types/task_entry.json` — extends entry: state (string), deadline, location
- `schemas/types/action.json` — extends task_entry: state ∈ {incomplete, inprogress, done}
- `schemas/types/potential_action.json` — extends task_entry: state ∈ {undecided, accepted, rejected}
- `schemas/lists/todo.json` — list schema; categories contain action entries
- `schemas/lists/potential-actions.json` — list schema; categories contain potential_action entries
- `schemas/lists/default.json` — list schema; categories contain base entries
- `scripts/lists.py` — main CLI (replaces lists.sh)
- `scripts/beautify.py` — pipe-able human-readable formatter
- `tests/test_validation.py` — schema validation unit tests
- `tests/test_lists.py` — subcommand integration tests
- `tests/fixtures/todo_valid.yaml`
- `tests/fixtures/todo_invalid_state.yaml`
- `tests/fixtures/todo_invalid_date.yaml`
- `tests/fixtures/todo_missing_deadline.yaml`
- `tests/fixtures/potential_actions_valid.yaml`
- `tests/fixtures/sample.md` — for migrate-md tests

**Modify:**
- `SKILL.md` — major rewrite: YAML format, new subcommand API, cloud-files skill invocation pattern
- `permissions.json` — update permission prefix from `lists.sh` to `lists.py`
- `references/list-structure.md` — update to reflect YAML structure

**Delete:**
- `scripts/lists.sh`
- `scripts/number-unchecked.py`
- `tests/test_lists.sh`

---

## Task 1: JSONSchema type files

**Files:**
- Create: `schemas/types/entry.json`
- Create: `schemas/types/task_entry.json`
- Create: `schemas/types/action.json`
- Create: `schemas/types/potential_action.json`

- [ ] **Step 1: Create schemas/types/ directory**

```bash
mkdir -p schemas/types
```

- [ ] **Step 2: Write entry.json**

`schemas/types/entry.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "entry.json",
  "type": "object",
  "required": ["id", "title", "created"],
  "properties": {
    "id": {
      "type": "string",
      "pattern": "^[0-9a-f]{6}$"
    },
    "title": {
      "type": "string",
      "minLength": 1
    },
    "created": {
      "type": "string",
      "format": "date"
    },
    "description": {
      "type": "string"
    },
    "children": {
      "type": "array",
      "items": {"$ref": "entry.json"}
    }
  }
}
```

Note: No `additionalProperties: false` — base types are open for extension via `allOf`.

- [ ] **Step 3: Write task_entry.json**

`schemas/types/task_entry.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "task_entry.json",
  "allOf": [{"$ref": "entry.json"}],
  "required": ["state", "deadline"],
  "properties": {
    "state": {"type": "string"},
    "deadline": {
      "type": "string",
      "format": "date"
    },
    "location": {
      "type": "string"
    }
  }
}
```

- [ ] **Step 4: Write action.json**

`schemas/types/action.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "action.json",
  "allOf": [{"$ref": "task_entry.json"}],
  "type": "object",
  "required": ["id", "title", "created", "state", "deadline"],
  "properties": {
    "id": {"type": "string", "pattern": "^[0-9a-f]{6}$"},
    "title": {"type": "string", "minLength": 1},
    "created": {"type": "string", "format": "date"},
    "description": {"type": "string"},
    "children": {
      "type": "array",
      "items": {"$ref": "action.json"}
    },
    "state": {"enum": ["incomplete", "inprogress", "done"]},
    "deadline": {"type": "string", "format": "date"},
    "location": {"type": "string"}
  },
  "additionalProperties": false
}
```

Note: All fields listed in `properties` here so `additionalProperties: false` works correctly in draft-07 (it only sees this schema's own `properties`, not those from `allOf` refs).

- [ ] **Step 5: Write potential_action.json**

`schemas/types/potential_action.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "potential_action.json",
  "allOf": [{"$ref": "task_entry.json"}],
  "type": "object",
  "required": ["id", "title", "created", "state", "deadline"],
  "properties": {
    "id": {"type": "string", "pattern": "^[0-9a-f]{6}$"},
    "title": {"type": "string", "minLength": 1},
    "created": {"type": "string", "format": "date"},
    "description": {"type": "string"},
    "children": {
      "type": "array",
      "items": {"$ref": "potential_action.json"}
    },
    "state": {"enum": ["undecided", "accepted", "rejected"]},
    "deadline": {"type": "string", "format": "date"},
    "location": {"type": "string"}
  },
  "additionalProperties": false
}
```

- [ ] **Step 6: Smoke test — verify schemas load and resolve cross-refs**

```bash
python3 - <<'EOF'
import json
from pathlib import Path
import jsonschema
from jsonschema import RefResolver, FormatChecker

schemas_dir = Path("schemas")
for name in ["action", "potential_action"]:
    p = schemas_dir / "types" / f"{name}.json"
    schema = json.loads(p.read_text())
    resolver = RefResolver(base_uri=p.as_uri(), referrer=schema)
    # Valid action
    data = {"id": "a3f2b9", "title": "Test", "created": "2026-06-29",
            "state": "incomplete", "deadline": "2026-07-04"}
    jsonschema.validate(data, schema, resolver=resolver, format_checker=FormatChecker())
    print(f"{name}: valid entry passes ✓")
    # Invalid state
    try:
        jsonschema.validate({**data, "state": "WRONG"}, schema, resolver=resolver,
                            format_checker=FormatChecker())
        print(f"{name}: ERROR — invalid state should have failed")
    except jsonschema.ValidationError:
        print(f"{name}: invalid state rejected ✓")
EOF
```

Expected output:
```
action: valid entry passes ✓
action: invalid state rejected ✓
potential_action: valid entry passes ✓
potential_action: invalid state rejected ✓
```

- [ ] **Step 7: Commit**

```bash
git add schemas/types/
git commit -m "feat(list-manager): add JSONSchema type hierarchy (entry, task_entry, action, potential_action)"
```

---

## Task 2: Per-list schema files

**Files:**
- Create: `schemas/lists/todo.json`
- Create: `schemas/lists/potential-actions.json`
- Create: `schemas/lists/default.json`

- [ ] **Step 1: Create schemas/lists/ directory**

```bash
mkdir -p schemas/lists
```

- [ ] **Step 2: Write todo.json**

`schemas/lists/todo.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["schema", "name", "categories"],
  "properties": {
    "schema": {"type": "string", "const": "todo"},
    "name": {"type": "string", "minLength": 1},
    "categories": {
      "type": "array",
      "items": {"$ref": "#/$defs/category"}
    }
  },
  "additionalProperties": false,
  "$defs": {
    "category": {
      "type": "object",
      "required": ["name"],
      "properties": {
        "name": {"type": "string", "minLength": 1},
        "categories": {
          "type": "array",
          "items": {"$ref": "#/$defs/category"}
        },
        "entries": {
          "type": "array",
          "items": {"$ref": "../types/action.json"}
        }
      },
      "additionalProperties": false
    }
  }
}
```

- [ ] **Step 3: Write potential-actions.json**

`schemas/lists/potential-actions.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["schema", "name", "categories"],
  "properties": {
    "schema": {"type": "string", "const": "potential-actions"},
    "name": {"type": "string", "minLength": 1},
    "categories": {
      "type": "array",
      "items": {"$ref": "#/$defs/category"}
    }
  },
  "additionalProperties": false,
  "$defs": {
    "category": {
      "type": "object",
      "required": ["name"],
      "properties": {
        "name": {"type": "string", "minLength": 1},
        "categories": {
          "type": "array",
          "items": {"$ref": "#/$defs/category"}
        },
        "entries": {
          "type": "array",
          "items": {"$ref": "../types/potential_action.json"}
        }
      },
      "additionalProperties": false
    }
  }
}
```

- [ ] **Step 4: Write default.json**

`schemas/lists/default.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["schema", "name", "categories"],
  "properties": {
    "schema": {"type": "string", "const": "default"},
    "name": {"type": "string", "minLength": 1},
    "categories": {
      "type": "array",
      "items": {"$ref": "#/$defs/category"}
    }
  },
  "additionalProperties": false,
  "$defs": {
    "category": {
      "type": "object",
      "required": ["name"],
      "properties": {
        "name": {"type": "string", "minLength": 1},
        "categories": {
          "type": "array",
          "items": {"$ref": "#/$defs/category"}
        },
        "entries": {
          "type": "array",
          "items": {"$ref": "../types/entry.json"}
        }
      },
      "additionalProperties": false
    }
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add schemas/lists/
git commit -m "feat(list-manager): add per-list JSONSchema files (todo, potential-actions, default)"
```

---

## Task 3: Test fixtures and schema validation tests

**Files:**
- Create: `tests/fixtures/todo_valid.yaml`
- Create: `tests/fixtures/todo_invalid_state.yaml`
- Create: `tests/fixtures/todo_invalid_date.yaml`
- Create: `tests/fixtures/todo_missing_deadline.yaml`
- Create: `tests/fixtures/todo_extra_field.yaml`
- Create: `tests/fixtures/potential_actions_valid.yaml`
- Create: `tests/test_validation.py`

- [ ] **Step 1: Create test fixtures**

`tests/fixtures/todo_valid.yaml`:
```yaml
schema: todo
name: todo
categories:
  - name: Work
    categories:
      - name: Writing
        entries:
          - id: a3f2b9
            title: Reply to Diego
            state: incomplete
            created: "2026-06-29"
            deadline: "2026-07-04"
            description: Follow up on the appendix draft.
            location: home
```

`tests/fixtures/todo_invalid_state.yaml`:
```yaml
schema: todo
name: todo
categories:
  - name: Work
    entries:
      - id: a3f2b9
        title: Do something
        state: undecided
        created: "2026-06-29"
        deadline: "2026-07-04"
```

`tests/fixtures/todo_invalid_date.yaml`:
```yaml
schema: todo
name: todo
categories:
  - name: Work
    entries:
      - id: a3f2b9
        title: Do something
        state: incomplete
        created: "29/06/2026"
        deadline: "2026-07-04"
```

`tests/fixtures/todo_missing_deadline.yaml`:
```yaml
schema: todo
name: todo
categories:
  - name: Work
    entries:
      - id: a3f2b9
        title: Do something
        state: incomplete
        created: "2026-06-29"
```

`tests/fixtures/todo_extra_field.yaml`:
```yaml
schema: todo
name: todo
categories:
  - name: Work
    entries:
      - id: a3f2b9
        title: Do something
        state: incomplete
        created: "2026-06-29"
        deadline: "2026-07-04"
        quantity: 3
```

`tests/fixtures/potential_actions_valid.yaml`:
```yaml
schema: potential-actions
name: potential-actions
categories:
  - name: Work
    entries:
      - id: c3d1e5
        title: Explore new API
        state: undecided
        created: "2026-06-29"
        deadline: "2026-07-15"
```

- [ ] **Step 2: Write test_validation.py**

`tests/test_validation.py`:
```python
"""Schema validation tests — validates JSONSchema files against fixture YAML data."""
import json
import pytest
from pathlib import Path
import jsonschema
from jsonschema import RefResolver, FormatChecker
import yaml

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def validate_list(schema_name: str, data: dict) -> None:
    schema_path = SCHEMAS_DIR / "lists" / f"{schema_name}.json"
    with open(schema_path) as f:
        schema = json.load(f)
    resolver = RefResolver(base_uri=schema_path.as_uri(), referrer=schema)
    jsonschema.validate(data, schema, resolver=resolver, format_checker=FormatChecker())


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return yaml.safe_load(f)


# ── todo list ────────────────────────────────────────────────────────────────

def test_todo_valid_passes():
    validate_list("todo", load_fixture("todo_valid.yaml"))


def test_todo_invalid_state_rejected():
    with pytest.raises(jsonschema.ValidationError, match="undecided"):
        validate_list("todo", load_fixture("todo_invalid_state.yaml"))


def test_todo_invalid_date_rejected():
    with pytest.raises(jsonschema.ValidationError):
        validate_list("todo", load_fixture("todo_invalid_date.yaml"))


def test_todo_missing_deadline_rejected():
    with pytest.raises(jsonschema.ValidationError, match="deadline"):
        validate_list("todo", load_fixture("todo_missing_deadline.yaml"))


def test_todo_extra_field_rejected():
    with pytest.raises(jsonschema.ValidationError, match="quantity"):
        validate_list("todo", load_fixture("todo_extra_field.yaml"))


def test_todo_nested_categories_valid():
    data = {
        "schema": "todo",
        "name": "todo",
        "categories": [
            {
                "name": "Work",
                "categories": [
                    {
                        "name": "Writing",
                        "entries": [
                            {
                                "id": "a3f2b9",
                                "title": "Draft",
                                "state": "incomplete",
                                "created": "2026-06-29",
                                "deadline": "2026-07-04"
                            }
                        ]
                    }
                ]
            }
        ]
    }
    validate_list("todo", data)


def test_todo_nested_children_valid():
    data = {
        "schema": "todo",
        "name": "todo",
        "categories": [
            {
                "name": "Work",
                "entries": [
                    {
                        "id": "a3f2b9",
                        "title": "Parent task",
                        "state": "incomplete",
                        "created": "2026-06-29",
                        "deadline": "2026-07-04",
                        "children": [
                            {
                                "id": "b7c1e2",
                                "title": "Child task",
                                "state": "inprogress",
                                "created": "2026-06-29",
                                "deadline": "2026-07-02"
                            }
                        ]
                    }
                ]
            }
        ]
    }
    validate_list("todo", data)


def test_todo_children_wrong_state_rejected():
    data = {
        "schema": "todo",
        "name": "todo",
        "categories": [
            {
                "name": "Work",
                "entries": [
                    {
                        "id": "a3f2b9",
                        "title": "Parent task",
                        "state": "incomplete",
                        "created": "2026-06-29",
                        "deadline": "2026-07-04",
                        "children": [
                            {
                                "id": "b7c1e2",
                                "title": "Child with wrong state",
                                "state": "undecided",  # wrong for action
                                "created": "2026-06-29",
                                "deadline": "2026-07-02"
                            }
                        ]
                    }
                ]
            }
        ]
    }
    with pytest.raises(jsonschema.ValidationError):
        validate_list("todo", data)


# ── potential-actions list ───────────────────────────────────────────────────

def test_potential_actions_valid_passes():
    validate_list("potential-actions", load_fixture("potential_actions_valid.yaml"))


def test_potential_actions_wrong_state_rejected():
    data = {
        "schema": "potential-actions",
        "name": "potential-actions",
        "categories": [
            {
                "name": "Work",
                "entries": [
                    {
                        "id": "c3d1e5",
                        "title": "Some action",
                        "state": "incomplete",  # wrong for potential_action
                        "created": "2026-06-29",
                        "deadline": "2026-07-15"
                    }
                ]
            }
        ]
    }
    with pytest.raises(jsonschema.ValidationError):
        validate_list("potential-actions", data)


# ── default list ─────────────────────────────────────────────────────────────

def test_default_valid_no_state():
    data = {
        "schema": "default",
        "name": "groceries",
        "categories": [
            {
                "name": "Produce",
                "entries": [
                    {
                        "id": "f1a2b3",
                        "title": "Apples",
                        "created": "2026-06-29"
                    }
                ]
            }
        ]
    }
    validate_list("default", data)
```

- [ ] **Step 3: Run tests — expect all to pass**

```bash
cd /path/to/skills/list-manager
python3 -m pytest tests/test_validation.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/ tests/test_validation.py
git commit -m "test(list-manager): add schema validation tests and fixtures"
```

---

## Task 4: `lists.py` — core + `init` + `gen-id`

**Files:**
- Create: `scripts/lists.py`
- Create: `tests/test_lists.py` (partial — init and gen-id tests)

- [ ] **Step 1: Write failing tests for init and gen-id**

`tests/test_lists.py` (initial):
```python
"""Integration tests for lists.py subcommands. All tests operate on local temp files."""
import subprocess
import sys
from pathlib import Path
import yaml
import pytest

LISTS_PY = Path(__file__).parent.parent / "scripts" / "lists.py"


def run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LISTS_PY)] + args,
        input=stdin,
        capture_output=True,
        text=True,
    )


# ── init ─────────────────────────────────────────────────────────────────────

def test_init_creates_valid_yaml(tmp_path):
    f = tmp_path / "todo.yaml"
    result = run(["init", str(f), "--schema", "todo"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    assert data["schema"] == "todo"
    assert data["name"] == "todo"
    assert data["categories"] == []


def test_init_fails_if_file_exists(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text("schema: todo\nname: todo\ncategories: []\n")
    result = run(["init", str(f), "--schema", "todo"])
    assert result.returncode != 0
    assert "exists" in result.stderr


def test_init_unknown_schema_fails(tmp_path):
    f = tmp_path / "mylist.yaml"
    result = run(["init", str(f), "--schema", "nonexistent"])
    assert result.returncode != 0


# ── gen-id ───────────────────────────────────────────────────────────────────

def test_gen_id_returns_six_char_hex(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text("schema: todo\nname: todo\ncategories: []\n")
    result = run(["gen-id", str(f)])
    assert result.returncode == 0, result.stderr
    id_ = result.stdout.strip()
    assert len(id_) == 6
    assert all(c in "0123456789abcdef" for c in id_)


def test_gen_id_count(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text("schema: todo\nname: todo\ncategories: []\n")
    result = run(["gen-id", str(f), "--count", "5"])
    assert result.returncode == 0
    ids = result.stdout.strip().splitlines()
    assert len(ids) == 5
    assert len(set(ids)) == 5  # all unique


def test_gen_id_avoids_collisions(tmp_path):
    # Pre-seed a file with a known ID, confirm gen-id doesn't return it
    existing_id = "aabbcc"
    f = tmp_path / "todo.yaml"
    f.write_text(
        f"schema: todo\nname: todo\ncategories:\n"
        f"  - name: Work\n    entries:\n"
        f"      - id: {existing_id}\n        title: X\n"
        f"        state: incomplete\n        created: '2026-06-29'\n"
        f"        deadline: '2026-07-01'\n"
    )
    # Generate 20 IDs — none should equal existing_id (probability of all 20
    # matching by chance: (1/16^6)^20 ≈ 0)
    result = run(["gen-id", str(f), "--count", "20"])
    ids = result.stdout.strip().splitlines()
    assert existing_id not in ids
```

- [ ] **Step 2: Run tests — expect ImportError / file-not-found failures**

```bash
python3 -m pytest tests/test_lists.py -v 2>&1 | head -20
```

Expected: tests fail because `scripts/lists.py` doesn't exist yet.

- [ ] **Step 3: Write lists.py scaffold + init + gen-id**

`scripts/lists.py`:
```python
#!/usr/bin/env python3
"""list-manager: pure local YAML file operator.

Subcommands:
  init          <file> --schema <name>
  read          <file> [key=value | key~=value ...]
  create-entry  <file> <target> [--entries <file>]
  update        <file> [--file <file>]
  gen-id        <file> [--count <n>]
  migrate-md    <src.md> <dst.yaml> --schema <name>
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml
import jsonschema
from jsonschema import RefResolver, FormatChecker

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
IMMUTABLE_FIELDS = frozenset({"id", "created"})
HEX6_RE = re.compile(r"^[0-9a-f]{6}$")


# ── I/O helpers ──────────────────────────────────────────────────────────────

def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ── ID generation ─────────────────────────────────────────────────────────────

def gen_ids(existing_content: str, count: int = 1) -> list[str]:
    """Return `count` collision-free 6-char lowercase hex IDs."""
    ids: list[str] = []
    while len(ids) < count:
        candidate = os.urandom(3).hex()
        if candidate not in existing_content and candidate not in ids:
            ids.append(candidate)
    return ids


# ── Validation ───────────────────────────────────────────────────────────────

def validate_list(data: dict) -> None:
    schema_name = data.get("schema")
    if not schema_name:
        die("list file missing 'schema' field")

    schema_path = SCHEMAS_DIR / "lists" / f"{schema_name}.json"
    if not schema_path.exists():
        die(f"unknown schema '{schema_name}' (no file at {schema_path})")

    with open(schema_path) as f:
        schema = json.load(f)

    resolver = RefResolver(base_uri=schema_path.as_uri(), referrer=schema)

    try:
        jsonschema.validate(data, schema, resolver=resolver, format_checker=FormatChecker())
    except jsonschema.ValidationError as e:
        die(f"validation failed: {e.message}")


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> None:
    file = Path(args.file)
    if file.exists():
        die(f"file already exists: {file}")

    data: dict = {
        "schema": args.schema,
        "name": file.stem,
        "categories": [],
    }

    validate_list(data)  # ensures schema name is valid before writing
    save_yaml(file, data)
    print(f"created {file}")


def cmd_gen_id(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)
    existing = yaml.dump(data)
    count = args.count
    ids = gen_ids(existing, count)
    for id_ in ids:
        print(id_)


# ── Argument parsing + dispatch ───────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lists.py")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init")
    p_init.add_argument("file")
    p_init.add_argument("--schema", required=True)

    # gen-id
    p_genid = sub.add_parser("gen-id")
    p_genid.add_argument("file")
    p_genid.add_argument("--count", type=int, default=1)

    # Placeholders for later tasks (prevents argparse errors if called early)
    for cmd in ("read", "create-entry", "update", "migrate-md"):
        sub.add_parser(cmd, add_help=False)

    return parser


def main() -> None:
    parser = build_parser()
    args, _ = parser.parse_known_args()

    dispatch = {
        "init": cmd_init,
        "gen-id": cmd_gen_id,
    }

    fn = dispatch.get(args.command)
    if fn is None:
        die(f"subcommand '{args.command}' not yet implemented")
    fn(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run init and gen-id tests — expect all to pass**

```bash
python3 -m pytest tests/test_lists.py::test_init_creates_valid_yaml \
    tests/test_lists.py::test_init_fails_if_file_exists \
    tests/test_lists.py::test_init_unknown_schema_fails \
    tests/test_lists.py::test_gen_id_returns_six_char_hex \
    tests/test_lists.py::test_gen_id_count \
    tests/test_lists.py::test_gen_id_avoids_collisions -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/lists.py tests/test_lists.py
git commit -m "feat(list-manager): add lists.py scaffold with init and gen-id subcommands"
```

---

## Task 5: `read` subcommand

**Files:**
- Modify: `scripts/lists.py`
- Modify: `tests/test_lists.py`

- [ ] **Step 1: Add read tests to test_lists.py**

Append to `tests/test_lists.py`:
```python
# ── read ──────────────────────────────────────────────────────────────────────

TODO_YAML = """\
schema: todo
name: todo
categories:
  - name: Work
    categories:
      - name: Writing
        entries:
          - id: a3f2b9
            title: Reply to Diego
            state: incomplete
            created: '2026-06-29'
            deadline: '2026-07-04'
            location: home
          - id: b7c1e2
            title: Review draft
            state: inprogress
            created: '2026-06-28'
            deadline: '2026-07-10'
  - name: Personal
    entries:
      - id: c3d1e5
        title: Book dentist
        state: done
        created: '2026-06-20'
        deadline: '2026-06-30'
"""


@pytest.fixture
def todo_file(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    return f


def test_read_no_filter_returns_full_yaml(todo_file):
    result = run(["read", str(todo_file)])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(result.stdout)
    assert data["schema"] == "todo"
    assert len(data["categories"]) == 2


def test_read_exact_filter(todo_file):
    result = run(["read", str(todo_file), "state=incomplete"])
    assert result.returncode == 0
    entries = yaml.safe_load(result.stdout)
    assert isinstance(entries, list)
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_multi_value_filter_or_semantics(todo_file):
    result = run(["read", str(todo_file), "state=incomplete,inprogress"])
    entries = yaml.safe_load(result.stdout)
    assert len(entries) == 2
    ids = {e["id"] for e in entries}
    assert ids == {"a3f2b9", "b7c1e2"}


def test_read_substring_filter(todo_file):
    result = run(["read", str(todo_file), "title~=Diego"])
    entries = yaml.safe_load(result.stdout)
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_multi_key_filter_and_semantics(todo_file):
    result = run(["read", str(todo_file), "state=incomplete", "location=home"])
    entries = yaml.safe_load(result.stdout)
    assert len(entries) == 1
    assert entries[0]["id"] == "a3f2b9"


def test_read_no_matches_returns_empty_list(todo_file):
    result = run(["read", str(todo_file), "state=nonexistent"])
    assert result.returncode == 0
    data = yaml.safe_load(result.stdout)
    assert data == [] or data is None


def test_read_filter_raw_yaml_includes_ids(todo_file):
    result = run(["read", str(todo_file), "state=incomplete"])
    entries = yaml.safe_load(result.stdout)
    assert "id" in entries[0]
```

- [ ] **Step 2: Run tests — expect failures (read not implemented)**

```bash
python3 -m pytest tests/test_lists.py -k "read" -v 2>&1 | tail -10
```

Expected: 7 failures with "not yet implemented".

- [ ] **Step 3: Implement read in lists.py**

Add these functions and update `build_parser` and `main` in `scripts/lists.py`:

```python
# ── Filter helpers ────────────────────────────────────────────────────────────

def parse_filter(arg: str) -> tuple[str, str, str]:
    if "~=" in arg:
        key, val = arg.split("~=", 1)
        return key.strip(), "~=", val
    elif "=" in arg:
        key, val = arg.split("=", 1)
        return key.strip(), "=", val
    else:
        die(f"invalid filter '{arg}' — use key=value or key~=value")


def entry_matches(entry: dict, filters: list[tuple]) -> bool:
    for key, op, val in filters:
        field_val = str(entry.get(key, ""))
        if op == "=":
            allowed = {v.strip() for v in val.split(",")}
            if field_val not in allowed:
                return False
        elif op == "~=":
            if val.lower() not in field_val.lower():
                return False
    return True


def collect_entries(categories: list[dict], filters: list[tuple]) -> list[dict]:
    """Recursively collect matching entries from a categories list (flat output)."""
    results: list[dict] = []
    for cat in categories:
        results.extend(collect_entries(cat.get("categories", []), filters))
        for entry in cat.get("entries", []):
            if not filters or entry_matches(entry, filters):
                results.append(entry)
            results.extend(_collect_from_children(entry.get("children", []), filters))
    return results


def _collect_from_children(children: list[dict], filters: list[tuple]) -> list[dict]:
    results: list[dict] = []
    for child in children:
        if not filters or entry_matches(child, filters):
            results.append(child)
        results.extend(_collect_from_children(child.get("children", []), filters))
    return results


# ── cmd_read ──────────────────────────────────────────────────────────────────

def cmd_read(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)

    filter_args: list[str] = args.filters or []

    if not filter_args:
        yaml.dump(data, sys.stdout, allow_unicode=True,
                  default_flow_style=False, sort_keys=False)
        return

    filters = [parse_filter(f) for f in filter_args]
    matches = collect_entries(data.get("categories", []), filters)
    yaml.dump(matches or [], sys.stdout, allow_unicode=True,
              default_flow_style=False, sort_keys=False)
```

In `build_parser`, replace the read placeholder:
```python
    # read
    p_read = sub.add_parser("read")
    p_read.add_argument("file")
    p_read.add_argument("filters", nargs="*",
                        help="key=value or key~=value filters (AND-ed)")
```

In `main`, add to dispatch:
```python
        "read": cmd_read,
```

- [ ] **Step 4: Run read tests — expect all to pass**

```bash
python3 -m pytest tests/test_lists.py -k "read" -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/lists.py tests/test_lists.py
git commit -m "feat(list-manager): add read subcommand with filter support"
```

---

## Task 6: `create-entry` subcommand

**Files:**
- Modify: `scripts/lists.py`
- Modify: `tests/test_lists.py`

- [ ] **Step 1: Add create-entry tests**

Append to `tests/test_lists.py`:
```python
# ── create-entry ──────────────────────────────────────────────────────────────

NEW_ENTRY_YAML = """\
- title: New task
  state: incomplete
  created: '2026-06-29'
  deadline: '2026-07-15'
"""

NEW_ENTRY_WITH_LOCATION = """\
- title: Home task
  state: incomplete
  created: '2026-06-29'
  deadline: '2026-07-15'
  location: home
"""


def test_create_entry_category_path(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    result = run(["create-entry", str(f), "Work/Writing"], stdin=NEW_ENTRY_YAML)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    writing_cat = data["categories"][0]["categories"][0]
    assert writing_cat["name"] == "Writing"
    titles = [e["title"] for e in writing_cat["entries"]]
    assert "New task" in titles


def test_create_entry_auto_assigns_id(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    run(["create-entry", str(f), "Work/Writing"], stdin=NEW_ENTRY_YAML)
    data = yaml.safe_load(f.read_text())
    writing_cat = data["categories"][0]["categories"][0]
    new_entry = next(e for e in writing_cat["entries"] if e["title"] == "New task")
    assert len(new_entry["id"]) == 6
    assert all(c in "0123456789abcdef" for c in new_entry["id"])


def test_create_entry_validates_before_write(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    original = f.read_text()
    # Invalid: state "undecided" is wrong for todo list
    bad_entry = "- title: Bad\n  state: undecided\n  created: '2026-06-29'\n  deadline: '2026-07-01'\n"
    result = run(["create-entry", str(f), "Work/Writing"], stdin=bad_entry)
    assert result.returncode != 0
    assert f.read_text() == original  # file unchanged


def test_create_entry_missing_category_fails(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    result = run(["create-entry", str(f), "Work/Nonexistent"], stdin=NEW_ENTRY_YAML)
    assert result.returncode != 0
    assert "not found" in result.stderr


def test_create_entry_entry_id_target_adds_child(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    result = run(["create-entry", str(f), "a3f2b9"], stdin=NEW_ENTRY_YAML)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    parent = data["categories"][0]["categories"][0]["entries"][0]
    assert parent["id"] == "a3f2b9"
    assert "children" in parent
    assert parent["children"][0]["title"] == "New task"


def test_create_entry_bulk(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    bulk = (
        "- title: Task A\n  state: incomplete\n  created: '2026-06-29'\n  deadline: '2026-07-01'\n"
        "- title: Task B\n  state: incomplete\n  created: '2026-06-29'\n  deadline: '2026-07-02'\n"
    )
    result = run(["create-entry", str(f), "Personal"], stdin=bulk)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    personal = data["categories"][1]
    titles = [e["title"] for e in personal["entries"]]
    assert "Task A" in titles and "Task B" in titles


def test_create_entry_from_file(tmp_path):
    f = tmp_path / "todo.yaml"
    f.write_text(TODO_YAML)
    entries_file = tmp_path / "new_entries.yaml"
    entries_file.write_text(NEW_ENTRY_YAML)
    result = run(["create-entry", str(f), "Personal", "--entries", str(entries_file)])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(f.read_text())
    personal = data["categories"][1]
    titles = [e["title"] for e in personal["entries"]]
    assert "New task" in titles
```

- [ ] **Step 2: Run tests — expect failures**

```bash
python3 -m pytest tests/test_lists.py -k "create_entry" -v 2>&1 | tail -5
```

Expected: all fail with "not yet implemented".

- [ ] **Step 3: Implement create-entry in lists.py**

Add these tree-navigation helpers and `cmd_create_entry`:

```python
# ── Tree navigation ───────────────────────────────────────────────────────────

def find_category(categories: list[dict], path_parts: list[str]) -> dict | None:
    for cat in categories:
        if cat["name"] == path_parts[0]:
            if len(path_parts) == 1:
                return cat
            return find_category(cat.get("categories", []), path_parts[1:])
    return None


def list_category_paths(categories: list[dict], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for cat in categories:
        full = f"{prefix}/{cat['name']}" if prefix else cat["name"]
        paths.append(full)
        paths.extend(list_category_paths(cat.get("categories", []), full))
    return paths


def find_entry_by_id(categories: list[dict], entry_id: str) -> dict | None:
    for cat in categories:
        for entry in cat.get("entries", []):
            if entry.get("id") == entry_id:
                return entry
            found = _find_in_children(entry.get("children", []), entry_id)
            if found:
                return found
        found = find_entry_by_id(cat.get("categories", []), entry_id)
        if found:
            return found
    return None


def _find_in_children(children: list[dict], entry_id: str) -> dict | None:
    for child in children:
        if child.get("id") == entry_id:
            return child
        found = _find_in_children(child.get("children", []), entry_id)
        if found:
            return found
    return None


# ── cmd_create_entry ──────────────────────────────────────────────────────────

def cmd_create_entry(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)

    # Load new entries
    if args.entries:
        with open(args.entries, encoding="utf-8") as f:
            new_entries = yaml.safe_load(f)
    else:
        new_entries = yaml.safe_load(sys.stdin.read())

    if not isinstance(new_entries, list):
        new_entries = [new_entries]

    target = args.target
    categories = data.setdefault("categories", [])

    # Determine target container and insertion key
    if HEX6_RE.match(target):
        # Entry ID — add as children
        container = find_entry_by_id(categories, target)
        if container is None:
            die(f"entry ID '{target}' not found")
        insert_key = "children"
    else:
        # Category path
        path_parts = [p.strip() for p in target.split("/")]
        container = find_category(categories, path_parts)
        if container is None:
            available = ", ".join(list_category_paths(categories)) or "(none)"
            die(f"category '{target}' not found. Available: {available}")
        insert_key = "entries"

    # Generate IDs and assign
    existing_content = yaml.dump(data)
    ids = gen_ids(existing_content, len(new_entries))
    for entry, new_id in zip(new_entries, ids):
        entry["id"] = new_id

    container.setdefault(insert_key, []).extend(new_entries)

    validate_list(data)
    save_yaml(file, data)
    n = len(new_entries)
    print(f"added {n} entr{'y' if n == 1 else 'ies'}")
```

In `build_parser`, replace the create-entry placeholder:
```python
    # create-entry
    p_create = sub.add_parser("create-entry")
    p_create.add_argument("file")
    p_create.add_argument("target", help="category path (Work/Writing) or 6-char entry ID")
    p_create.add_argument("--entries", help="YAML file with list of entries (default: stdin)")
```

Add to `main` dispatch:
```python
        "create-entry": cmd_create_entry,
```

- [ ] **Step 4: Run create-entry tests — expect all to pass**

```bash
python3 -m pytest tests/test_lists.py -k "create_entry" -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/lists.py tests/test_lists.py
git commit -m "feat(list-manager): add create-entry subcommand"
```

---

## Task 7: `update` subcommand

**Files:**
- Modify: `scripts/lists.py`
- Modify: `tests/test_lists.py`

- [ ] **Step 1: Add update tests**

Append to `tests/test_lists.py`:
```python
# ── update ────────────────────────────────────────────────────────────────────

def test_update_single_field(todo_file):
    updates = "- id: a3f2b9\n  state: done\n"
    result = run(["update", str(todo_file)], stdin=updates)
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][0]["entries"][0]
    assert entry["state"] == "done"
    assert entry["title"] == "Reply to Diego"  # other fields preserved


def test_update_multiple_fields(todo_file):
    updates = "- id: a3f2b9\n  state: inprogress\n  deadline: '2026-08-01'\n"
    result = run(["update", str(todo_file)], stdin=updates)
    assert result.returncode == 0
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][0]["entries"][0]
    assert entry["state"] == "inprogress"
    assert entry["deadline"] == "2026-08-01"


def test_update_bulk_from_file(tmp_path, todo_file):
    updates_file = tmp_path / "updates.yaml"
    updates_file.write_text(
        "- id: a3f2b9\n  state: done\n"
        "- id: b7c1e2\n  state: done\n"
    )
    result = run(["update", str(todo_file), "--file", str(updates_file)])
    assert result.returncode == 0
    data = yaml.safe_load(todo_file.read_text())
    entries = data["categories"][0]["categories"][0]["entries"]
    assert all(e["state"] == "done" for e in entries)


def test_update_invalid_state_rejected(todo_file):
    original = todo_file.read_text()
    updates = "- id: a3f2b9\n  state: undecided\n"
    result = run(["update", str(todo_file)], stdin=updates)
    assert result.returncode != 0
    assert todo_file.read_text() == original  # file unchanged


def test_update_immutable_id_rejected(todo_file):
    updates = "- id: a3f2b9\n  id: zzzzzz\n"
    result = run(["update", str(todo_file)], stdin=updates)
    # id is the lookup key — changing it is nonsensical but the value
    # under key "id" in the update dict is the lookup, the second "id"
    # would be a YAML duplicate key; just ensure it doesn't corrupt the file
    data = yaml.safe_load(todo_file.read_text())
    entry = data["categories"][0]["categories"][0]["entries"][0]
    assert entry["id"] == "a3f2b9"  # original ID preserved


def test_update_immutable_created_rejected(todo_file):
    original = todo_file.read_text()
    updates = "- id: a3f2b9\n  created: '2020-01-01'\n"
    result = run(["update", str(todo_file)], stdin=updates)
    assert result.returncode != 0
    assert "immutable" in result.stderr
    assert todo_file.read_text() == original


def test_update_unknown_id_fails(todo_file):
    updates = "- id: ffffff\n  state: done\n"
    result = run(["update", str(todo_file)], stdin=updates)
    assert result.returncode != 0
    assert "ffffff" in result.stderr
```

- [ ] **Step 2: Run tests — expect failures**

```bash
python3 -m pytest tests/test_lists.py -k "update" -v 2>&1 | tail -5
```

- [ ] **Step 3: Implement update in lists.py**

```python
# ── cmd_update ────────────────────────────────────────────────────────────────

def _apply_updates_to_categories(categories: list[dict], update_map: dict[str, dict]) -> set[str]:
    updated: set[str] = set()
    for cat in categories:
        for entry in cat.get("entries", []):
            if entry.get("id") in update_map:
                eid = entry["id"]
                entry.update(update_map[eid])
                updated.add(eid)
            updated |= _apply_updates_to_children(entry.get("children", []), update_map)
        updated |= _apply_updates_to_categories(cat.get("categories", []), update_map)
    return updated


def _apply_updates_to_children(children: list[dict], update_map: dict[str, dict]) -> set[str]:
    updated: set[str] = set()
    for child in children:
        if child.get("id") in update_map:
            eid = child["id"]
            child.update(update_map[eid])
            updated.add(eid)
        updated |= _apply_updates_to_children(child.get("children", []), update_map)
    return updated


def cmd_update(args: argparse.Namespace) -> None:
    file = Path(args.file)
    data = load_yaml(file)

    if args.file_input:
        with open(args.file_input, encoding="utf-8") as f:
            updates = yaml.safe_load(f)
    else:
        updates = yaml.safe_load(sys.stdin.read())

    if not isinstance(updates, list):
        updates = [updates]

    # Validate immutable fields before touching the file
    for u in updates:
        for field in IMMUTABLE_FIELDS - {"id"}:  # "id" is the lookup key, not a change
            if field in u:
                die(f"field '{field}' is immutable and cannot be updated")

    # Build update map: id → fields to set
    update_map: dict[str, dict] = {}
    for u in updates:
        uid = u.get("id")
        if not uid:
            die("each update must have an 'id' field")
        update_map[uid] = {k: v for k, v in u.items() if k != "id"}

    updated = _apply_updates_to_categories(data.get("categories", []), update_map)

    missing = set(update_map) - updated
    if missing:
        die(f"IDs not found: {sorted(missing)}")

    validate_list(data)
    save_yaml(file, data)
    n = len(updated)
    print(f"updated {n} entr{'y' if n == 1 else 'ies'}")
```

In `build_parser`, replace update placeholder:
```python
    # update
    p_update = sub.add_parser("update")
    p_update.add_argument("file")
    p_update.add_argument("--file", dest="file_input",
                          help="YAML file with list of {id, field: value} updates (default: stdin)")
```

Add to dispatch: `"update": cmd_update`

- [ ] **Step 4: Run update tests — expect all to pass**

```bash
python3 -m pytest tests/test_lists.py -k "update" -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/lists.py tests/test_lists.py
git commit -m "feat(list-manager): add update subcommand"
```

---

## Task 8: `migrate-md` subcommand

**Files:**
- Modify: `scripts/lists.py`
- Create: `tests/fixtures/sample.md`
- Modify: `tests/test_lists.py`

- [ ] **Step 1: Create sample.md fixture**

`tests/fixtures/sample.md`:
```markdown
[todo] [ ] incomplete · [x] done · [+] inprogress

- Work
  - Writing
    - [ ] (06/13/26) Reply to Diego <!-- #a3f2 -->
      Follow up on the appendix draft.
      deadline: 2026-07-04
    - [x] (06/10/26) Submit report <!-- #b7c1 -->
      deadline: 2026-06-15
  - Admin
    - [ ] (06/20/26) Book flight <!-- #c3d1 -->
      deadline: 2026-07-01
- Personal
  - [ ] (06/25/26) Buy groceries <!-- #d4e5 -->
    deadline: 2026-06-30
```

- [ ] **Step 2: Add migrate-md tests**

Append to `tests/test_lists.py`:
```python
# ── migrate-md ────────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_migrate_md_produces_valid_yaml(tmp_path):
    src = FIXTURES_DIR / "sample.md"
    dst = tmp_path / "todo.yaml"
    result = run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    assert result.returncode == 0, result.stderr
    assert dst.exists()
    data = yaml.safe_load(dst.read_text())
    assert data["schema"] == "todo"
    assert data["name"] == "todo"


def test_migrate_md_converts_categories(tmp_path):
    src = FIXTURES_DIR / "sample.md"
    dst = tmp_path / "todo.yaml"
    run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    data = yaml.safe_load(dst.read_text())
    cat_names = [c["name"] for c in data["categories"]]
    assert "Work" in cat_names
    assert "Personal" in cat_names


def test_migrate_md_converts_dates(tmp_path):
    src = FIXTURES_DIR / "sample.md"
    dst = tmp_path / "todo.yaml"
    run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    data = yaml.safe_load(dst.read_text())
    work = next(c for c in data["categories"] if c["name"] == "Work")
    writing = next(c for c in work.get("categories", []) if c["name"] == "Writing")
    entry = writing["entries"][0]
    assert entry["created"] == "2026-06-13"  # 06/13/26 → YYYY-MM-DD
    assert entry["deadline"] == "2026-07-04"


def test_migrate_md_preserves_ids(tmp_path):
    src = FIXTURES_DIR / "sample.md"
    dst = tmp_path / "todo.yaml"
    run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    data = yaml.safe_load(dst.read_text())
    work = next(c for c in data["categories"] if c["name"] == "Work")
    writing = next(c for c in work["categories"] if c["name"] == "Writing")
    assert writing["entries"][0]["id"] == "a3f2"


def test_migrate_md_maps_states(tmp_path):
    src = FIXTURES_DIR / "sample.md"
    dst = tmp_path / "todo.yaml"
    run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    data = yaml.safe_load(dst.read_text())
    work = next(c for c in data["categories"] if c["name"] == "Work")
    writing = next(c for c in work["categories"] if c["name"] == "Writing")
    assert writing["entries"][0]["state"] == "incomplete"  # [ ] → incomplete
    assert writing["entries"][1]["state"] == "done"        # [x] → done


def test_migrate_md_does_not_overwrite_existing_dst(tmp_path):
    src = FIXTURES_DIR / "sample.md"
    dst = tmp_path / "todo.yaml"
    dst.write_text("existing content")
    result = run(["migrate-md", str(src), str(dst), "--schema", "todo"])
    assert result.returncode != 0
    assert dst.read_text() == "existing content"
```

- [ ] **Step 3: Run migrate tests — expect failures**

```bash
python3 -m pytest tests/test_lists.py -k "migrate" -v 2>&1 | tail -5
```

- [ ] **Step 4: Implement migrate-md in lists.py**

Add to `scripts/lists.py`:

```python
# ── Markdown migration ────────────────────────────────────────────────────────

_TASK_LINE_RE = re.compile(
    r"^(\s*)- \[(.)\] \((\d{2}/\d{2}/\d{2})\) (.*?)(?:\s+<!-- #([0-9a-f]+) -->)?$"
)
_TITLE_LINE_RE = re.compile(r"^(\s*)- ([^\[\(].*\S)\s*$")
_DEADLINE_RE = re.compile(r"^\s*deadline:\s*(.+)$")
_HEADER_RE = re.compile(r"^\[(.+?)\]")


def _md_convert_date(date_str: str) -> str:
    """MM/DD/YY → YYYY-MM-DD (assumes 2000s)."""
    m, d, y = date_str.split("/")
    return f"20{y}-{m}-{d}"


def _md_resolve_deadline(raw: str) -> tuple[str | None, str | None]:
    """Return (resolved_date, error_msg). resolved_date is None if unresolvable."""
    # Already ISO date?
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw, None
    import dateparser
    parsed = dateparser.parse(
        raw,
        settings={"PREFER_DAY_OF_MONTH": "first", "RETURN_AS_TIMEZONE_AWARE": False},
    )
    if parsed:
        return parsed.strftime("%Y-%m-%d"), None
    return None, f"Could not resolve deadline '{raw}'"


def _md_parse_header(line: str) -> tuple[str, dict[str, str]]:
    """Parse '[name] [state] meaning · ...' → (name, {bracket: state_name})."""
    name_m = _HEADER_RE.match(line)
    name = name_m.group(1) if name_m else ""
    state_map: dict[str, str] = {}
    for m in re.finditer(r"\[(.)\]\s+(\w+)", line):
        state_map[f"[{m.group(1)}]"] = m.group(2)
    state_map.setdefault("[ ]", "incomplete")
    state_map.setdefault("[x]", "done")
    return name, state_map


def _md_parse_body(
    lines: list[str], state_map: dict[str, str], flagged: list[str]
) -> list[dict]:
    """Parse Markdown body lines into a categories list."""
    categories: list[dict] = []
    current_area: dict | None = None
    current_action: dict | None = None
    # Stack of (indent_level, entry_dict) for tracking nesting
    task_stack: list[tuple[int, dict]] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        i += 1

        if not line.strip():
            continue

        task_m = _TASK_LINE_RE.match(line)
        if task_m:
            task_indent = len(task_m.group(1))
            checkbox = f"[{task_m.group(2)}]"
            date_str = task_m.group(3)
            raw_title = task_m.group(4).strip()
            existing_id = task_m.group(5) or ""

            entry: dict = {
                "id": existing_id,
                "title": raw_title,
                "state": state_map.get(checkbox, "incomplete"),
                "created": _md_convert_date(date_str),
            }

            # Look ahead for continuation lines (description, deadline)
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    i += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent <= task_indent:
                    break
                if _TASK_LINE_RE.match(nxt) or _TITLE_LINE_RE.match(nxt):
                    break
                dl_m = _DEADLINE_RE.match(nxt)
                if dl_m:
                    resolved, err = _md_resolve_deadline(dl_m.group(1).strip())
                    if resolved:
                        entry["deadline"] = resolved
                    else:
                        flagged.append(err or f"unresolvable deadline on '{raw_title}'")
                else:
                    desc = nxt.strip()
                    if desc:
                        existing = entry.get("description", "")
                        entry["description"] = f"{existing}\n{desc}".strip() if existing else desc
                i += 1

            # Pop task stack to find parent
            while task_stack and task_stack[-1][0] >= task_indent:
                task_stack.pop()

            if task_stack:
                parent = task_stack[-1][1]
                parent.setdefault("children", []).append(entry)
            else:
                target = current_action if current_action is not None else current_area
                if target is not None:
                    target.setdefault("entries", []).append(entry)

            task_stack.append((task_indent, entry))
            continue

        title_m = _TITLE_LINE_RE.match(line)
        if title_m:
            title_indent = len(title_m.group(1))
            title_text = title_m.group(2).strip()
            task_stack = []

            if title_indent == 0:
                current_area = {"name": title_text}
                current_action = None
                categories.append(current_area)
            elif title_indent == 2:
                if current_area is None:
                    current_area = {"name": "General"}
                    categories.append(current_area)
                current_action = {"name": title_text}
                current_area.setdefault("categories", []).append(current_action)

    return categories


def cmd_migrate_md(args: argparse.Namespace) -> None:
    src = Path(args.src)
    dst = Path(args.dst)

    if dst.exists():
        die(f"destination already exists: {dst}")

    content = src.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Parse header
    list_name = dst.stem
    state_map: dict[str, str] = {"[ ]": "incomplete", "[x]": "done"}
    body_start = 0
    if lines and _HEADER_RE.match(lines[0]):
        list_name, state_map = _md_parse_header(lines[0])
        body_start = 1

    flagged: list[str] = []
    categories = _md_parse_body(lines[body_start:], state_map, flagged)

    if flagged:
        print("WARNING — could not resolve the following deadlines:", file=sys.stderr)
        for msg in flagged:
            print(f"  • {msg}", file=sys.stderr)
        print("Fix these before uploading the file.", file=sys.stderr)

    data = {
        "schema": args.schema,
        "name": list_name or dst.stem,
        "categories": categories,
    }

    validate_list(data)
    save_yaml(dst, data)
    print(f"migrated {src} → {dst}")
    if flagged:
        print(f"  {len(flagged)} deadline(s) need manual resolution", file=sys.stderr)
```

In `build_parser`, replace migrate-md placeholder:
```python
    # migrate-md
    p_migrate = sub.add_parser("migrate-md")
    p_migrate.add_argument("src", help="source .md file")
    p_migrate.add_argument("dst", help="destination .yaml file")
    p_migrate.add_argument("--schema", required=True,
                           help="list schema name (e.g. todo, potential-actions)")
```

Add to dispatch: `"migrate-md": cmd_migrate_md`

- [ ] **Step 5: Run migrate tests — expect all to pass**

```bash
python3 -m pytest tests/test_lists.py -k "migrate" -v
```

Expected: 6 tests pass.

- [ ] **Step 6: Run all tests to confirm no regressions**

```bash
python3 -m pytest tests/test_validation.py tests/test_lists.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/lists.py tests/test_lists.py tests/fixtures/sample.md
git commit -m "feat(list-manager): add migrate-md subcommand for Markdown→YAML conversion"
```

---

## Task 9: `beautify.py`

**Files:**
- Create: `scripts/beautify.py`

- [ ] **Step 1: Write beautify.py**

`scripts/beautify.py`:
```python
#!/usr/bin/env python3
"""Pipe-able formatter: reads raw YAML from stdin, prints human-readable output.

Strips IDs, adds hierarchical numbering (1, 1.1, 2, ...), indents children.
Usage: lists.py read /tmp/todo.yaml state=incomplete | python3 scripts/beautify.py
"""

import sys
import yaml


def format_entry(entry: dict, prefix: str, indent: int) -> list[str]:
    lines: list[str] = []
    state = entry.get("state", "")
    title = entry.get("title", "")
    deadline = entry.get("deadline", "")
    location = entry.get("location", "")
    description = entry.get("description", "")
    created = entry.get("created", "")

    meta_parts = [created]
    if deadline:
        meta_parts.append(f"due {deadline}")
    if location:
        meta_parts.append(f"@ {location}")
    meta = "  " + "  ".join(meta_parts) if meta_parts else ""

    pad = "  " * indent
    lines.append(f"{pad}{prefix}. [{state}] {title}{meta}")

    if description:
        for desc_line in description.splitlines():
            lines.append(f"{pad}    {desc_line}")

    for i, child in enumerate(entry.get("children", []), start=1):
        child_prefix = f"{prefix}.{i}"
        lines.extend(format_entry(child, child_prefix, indent + 1))

    return lines


def format_list(data) -> list[str]:
    """Format either a full list document or a flat list of entries."""
    lines: list[str] = []

    if isinstance(data, list):
        # Flat filtered output from `read` with filters
        for i, entry in enumerate(data, start=1):
            lines.extend(format_entry(entry, str(i), 0))
        return lines

    # Full document
    counter = 0
    for cat in data.get("categories", []):
        lines.append(f"\n{cat['name']}")
        for subcat in cat.get("categories", []):
            lines.append(f"  {subcat['name']}")
            for entry in subcat.get("entries", []):
                counter += 1
                lines.extend(format_entry(entry, str(counter), 2))
        for entry in cat.get("entries", []):
            counter += 1
            lines.extend(format_entry(entry, str(counter), 1))

    return lines


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return
    data = yaml.safe_load(raw)
    if data is None:
        return
    for line in format_list(data):
        print(line)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test beautify.py**

```bash
python3 scripts/beautify.py <<'EOF'
- id: a3f2b9
  title: Reply to Diego
  state: incomplete
  created: '2026-06-29'
  deadline: '2026-07-04'
  location: home
- id: b7c1e2
  title: Review draft
  state: inprogress
  created: '2026-06-28'
  deadline: '2026-07-10'
EOF
```

Expected output:
```
1. [incomplete] Reply to Diego  2026-06-29  due 2026-07-04  @ home
2. [inprogress] Review draft  2026-06-28  due 2026-07-10
```

- [ ] **Step 3: Commit**

```bash
git add scripts/beautify.py
git commit -m "feat(list-manager): add beautify.py pipe formatter"
```

---

## Task 10: SKILL.md rewrite + permissions + cleanup

**Files:**
- Modify: `SKILL.md`
- Modify: `permissions.json`
- Modify: `references/list-structure.md`
- Delete: `scripts/lists.sh`, `scripts/number-unchecked.py`, `tests/test_lists.sh`

- [ ] **Step 1: Update permissions.json**

`permissions.json`:
```json
{
  "bash": [
    "Bash(python3 scripts/lists.py:*)",
    "Bash(python3 scripts/beautify.py:*)"
  ],
  "network": []
}
```

- [ ] **Step 2: Rewrite SKILL.md**

Replace the full contents of `SKILL.md` with:

```markdown
---
name: list-manager
description: |
  Manage personal lists stored as YAML files validated against JSONSchema,
  under assistant cloud storage. Use when the user asks to show, create,
  or delete a list, or to add, update, or remove an entry on a list.
---

When this skill is used, begin with:

Skill: list-manager

Category: automation

Dependencies:
- cloud-files

## 0. Commands

All list operations go through `python3 scripts/lists.py`. Lists are
**local files** — the skill downloads/uploads via the cloud-files skill.
Never call cloud-files scripts directly.

| Subcommand | Signature |
|---|---|
| `init` | `init <file> --schema <name>` |
| `read` | `read <file> [key=value \| key~=value ...]` |
| `create-entry` | `create-entry <file> <target> [--entries <file>]` |
| `update` | `update <file> [--file <file>]` |
| `gen-id` | `gen-id <file> [--count <n>]` |
| `migrate-md` | `migrate-md <src.md> <dst.yaml> --schema <name>` |

Non-zero exit from any subcommand → stop; do not upload; do not infer state.

## 0.1 Cloud sync wrapper

Every operation on a named list follows this pattern:

```
1. Invoke cloud-files skill: download lists/<name>.yaml → /tmp/<name>.yaml
2. python3 scripts/lists.py <subcommand> /tmp/<name>.yaml [args]
3. On exit 0: invoke cloud-files skill: upload /tmp/<name>.yaml → lists/<name>.yaml
4. Clean up /tmp/<name>.yaml
```

For new lists, skip step 1 and use `init` in step 2.

## 0.2 Context efficiency

- `read` with filters returns a flat YAML list — only matching entries.
  The LLM sees only what it needs.
- Write updates to a temp file and pass `--file` / `--entries` instead of
  piping large payloads through stdin.
- `read` output includes IDs — pass directly to `update` or `create-entry`.

## 1. Storage model

- Each list is `<name>.yaml` under `assistant/lists/` in cloud storage.
- List names: lowercase, spaces → hyphens.
- A list comes into existence via `init`; it disappears when deleted via
  the cloud-files skill.
- Each list file declares `schema: <name>` which selects its JSONSchema.

## 2. File format

```yaml
schema: todo
name: todo
categories:
  - name: Work
    categories:
      - name: Writing
        entries:
          - id: a3f2b9
            title: Reply to Diego
            state: incomplete
            created: "2026-06-29"
            deadline: "2026-07-04"
            description: Follow up on appendix draft.
            location: home
```

### Entry type hierarchy

```
entry          id, title, created, description, children
└── task_entry   + state (string), deadline (date), location
    ├── action           state ∈ {incomplete, inprogress, done}
    └── potential_action state ∈ {undecided, accepted, rejected}
```

### Date fields

Both `created` and `deadline` are `YYYY-MM-DD`. **When the user gives a
free-form date ("by Friday", "end of month"), resolve it to `YYYY-MM-DD`
using today's date before calling `lists.py`.** Never pass free-form strings
to the script.

### IDs

6-char lowercase hex, auto-assigned by `create-entry`. IDs are never shown
to the user — strip them from any text displayed to the user. Use `read`
(which includes IDs) to get them for `update` targets.

### Children

Omit `children` when absent. Same entry type as the parent.

## 3. Operations

### 3.1 List all lists

```bash
# Invoke cloud-files skill to list files under lists/
```

### 3.2 Show a list

```bash
# Download, then:
python3 scripts/lists.py read /tmp/<name>.yaml | python3 scripts/beautify.py
```

For filtered read (only show certain entries):
```bash
python3 scripts/lists.py read /tmp/<name>.yaml state=incomplete
python3 scripts/lists.py read /tmp/<name>.yaml state=incomplete,inprogress
python3 scripts/lists.py read /tmp/<name>.yaml title~=Diego
```

Filtered output is a flat YAML list including IDs. Pipe through
`scripts/beautify.py` before displaying to the user.

### 3.3 Create a new list

```bash
python3 scripts/lists.py init /tmp/<name>.yaml --schema todo
# Then upload via cloud-files skill
```

### 3.4 Delete a whole list

Confirm with the user (destructive), then invoke cloud-files skill to delete
`lists/<name>.yaml`. No `lists.py` call needed.

### 3.5 Add an entry

1. Resolve today's date: use the current date as `created`.
2. If the user gave a deadline in any form, resolve it to `YYYY-MM-DD`.
3. If no deadline was given, ask; if the user says none, you cannot add the
   entry (deadline is required for action/potential_action entries).
4. Write entries to a temp file and call `create-entry`:

```bash
# target = category path (Work/Writing) or 6-char entry ID (adds as child)
python3 scripts/lists.py create-entry /tmp/<name>.yaml <target> \
    --entries /tmp/new_entries.yaml
```

### 3.6 Update an entry

1. Use `read` with filters to find the entry and get its ID.
2. Write updates to a temp file:

```yaml
- id: a3f2b9
  state: done
```

3. Apply:
```bash
python3 scripts/lists.py update /tmp/<name>.yaml --file /tmp/updates.yaml
```

`id` and `created` are immutable — the script rejects attempts to change them.

### 3.7 Remove an entry

There is no `delete-entry` subcommand. To remove an entry:
1. `read` the full file.
2. Remove the entry (and its children) from the YAML.
3. `write` the full updated YAML back via cloud-files skill.

Confirm with the user if the cascade set is larger than the matched entry.

## 4. Structured lists

`todo` and `potential-actions` are structured lists. Their schemas enforce
the category/entry hierarchy.

### potential-actions ↔ todo dependency

- `undecided` → reviewed
- **`accepted`**: mark accepted in potential-actions AND add the entry to todo
  (under the inferred best-fit category), using today's date as `created`,
  `state: incomplete`, same `deadline`. Generate a new ID for the todo entry.
- **`rejected`**: mark rejected in potential-actions. Nothing added to todo.

Never delete from potential-actions — accepted/rejected is the audit trail.
Never add to potential-actions if a matching title already exists.

## 5. Migration

To convert an existing Markdown list to YAML:

```bash
# 1. Download .md via cloud-files skill
python3 scripts/lists.py migrate-md /tmp/<name>.md /tmp/<name>.yaml --schema todo
# 2. Review any flagged deadlines (printed to stderr)
# 3. Upload .yaml via cloud-files skill
# 4. Delete .md via cloud-files skill
```
```

- [ ] **Step 3: Update references/list-structure.md**

Replace contents with:

```markdown
# List Structure

Lists are stored as YAML files validated against JSONSchema
(`schemas/lists/<schema-name>.json`).

## Top-level fields

| Field | Required | Description |
|---|---|---|
| `schema` | yes | Schema name; selects `schemas/lists/<name>.json` |
| `name` | yes | Human-readable list name |
| `categories` | yes | Array of category objects (may be empty) |

## Category fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Category label |
| `categories` | no | Nested sub-categories (omit if absent) |
| `entries` | no | Array of entries (omit if absent) |

## Entry type hierarchy

See `schemas/types/` for JSONSchema definitions.

```
entry          id (6-char hex), title, created (YYYY-MM-DD), description?, children?
└── task_entry   + state (string), deadline (YYYY-MM-DD), location?
    ├── action           state ∈ {incomplete, inprogress, done}
    └── potential_action state ∈ {undecided, accepted, rejected}
```

Omit optional fields when absent — no nulls or empty arrays.
```

- [ ] **Step 4: Delete obsolete files**

```bash
git rm scripts/lists.sh scripts/number-unchecked.py tests/test_lists.sh
```

- [ ] **Step 5: Run full test suite — confirm all pass**

```bash
python3 -m pytest tests/test_validation.py tests/test_lists.py -v
```

Expected: all tests pass, no failures.

- [ ] **Step 6: Commit everything**

```bash
git add SKILL.md permissions.json references/list-structure.md
git commit -m "feat(list-manager): rewrite SKILL.md for YAML API, update permissions, remove old scripts"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| entry / task_entry / action / potential_action schemas | Task 1 |
| Per-list schemas (todo, potential-actions, default) | Task 2 |
| Schema validation tests | Task 3 |
| `init` subcommand | Task 4 |
| `read` subcommand (unfiltered + filtered) | Task 5 |
| `create-entry` (category + ID target, bulk, --entries) | Task 6 |
| `update` (bulk, --file, immutable fields, unknown ID) | Task 7 |
| `migrate-md` with date conversion + flagging | Task 8 |
| `beautify.py` pipe formatter | Task 9 |
| SKILL.md rewrite | Task 10 |
| Deadline required on task_entry | Tasks 1-2 (schema), 8 (migration) |
| `gen-id --count n` | Task 4 |
| 6-char hex IDs | Tasks 1, 4 |
| Filtered read returns flat list | Task 5 |
| Missing category fails with available list | Task 6 |
| Immutable `id` and `created` | Task 7 |
| `dateparser` for deadline resolution in migration | Task 8 |
| Validation before write (file unchanged on failure) | Tasks 6, 7 |
| `beautify.py` as pipe, not subcommand | Task 9 |
| Cloud-files via skill invocation (not direct script call) | Task 10 (SKILL.md) |
| Old scripts deleted | Task 10 |

**No gaps found.**

**Placeholder scan:** All steps contain actual code or commands. No TBDs.

**Type consistency:**
- `gen_ids(existing_content: str, count: int)` defined in Task 4, used in Tasks 6 with `yaml.dump(data)` as content — consistent ✓
- `find_category` / `find_entry_by_id` / `collect_entries` defined in Tasks 5-6, not referenced elsewhere — no cross-task name drift ✓
- `_apply_updates_to_categories` / `_apply_updates_to_children` defined in Task 7 — internal only ✓
- `IMMUTABLE_FIELDS` defined in Task 4 scaffold, used in Task 7 — consistent ✓
