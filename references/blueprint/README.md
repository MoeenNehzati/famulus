# Blueprint System

Blueprints define the contract of skills: dependencies, interfaces, and invocation constraints.

## Files

- **`guide.md`** — Complete guide (start here)
  - Overview and two-layer validation architecture
  - How to create a new blueprint
  - Common patterns and examples
  - Migration from `exported` → `allow_all_skills`
  - Validation and error reference
  - IDE setup
  - Best practices

- **`schema.json`** — JSON Schema
  - Formal definition of blueprint structure
  - Used for YAML validation in IDEs and at commit time
  - Property descriptions and constraints

- **`template.yaml`** — Annotated template
  - Reference implementation with 4 detailed examples
  - Covers all feature categories
  - Used when creating new blueprints

## Quick Start

1. **Read:** `guide.md` (5-10 min overview)
2. **Reference:** `template.yaml` (for examples)
3. **Create:** Copy template structure into `skills/<name>/blueprint.yaml`
4. **Validate:** `python3 tools/check_skill_blueprints.py`

## Validation

**Two-layer approach:**

| Layer | Tool | When | What |
|-------|------|------|------|
| Schema | `schema.json` + IDE | Edit time | YAML structure (types, enums, constraints) |
| Relationships | `tools/validate_blueprint_relationships.py` | Commit time | Cross-blueprint constraints (versions, access control) |

**Both run together:**
```bash
python3 tools/check_skill_blueprints.py
```

## Key Concept: `allow_all_skills`

Controls who can use an interface:

```yaml
allow_all_skills: true   # Any skill that declares this as a dependency can use it
allowed_callers: []
```

```yaml
allow_all_skills: false  # Only skills in allowed_callers can use it
allowed_callers: [daily-plan, email-triage]
```

**Constraint:** If `allow_all_skills: true`, `allowed_callers` must be empty.

## Recent Change

`exported` field renamed to `allow_all_skills` (v2.0). See `guide.md` → "Migration" section.

All existing blueprints migrated. Old field name will fail validation.
