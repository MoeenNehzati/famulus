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
| Python validators | `skills/skill-maker/validators/` | Commit time | Cross-field rules, sync drift, dependency constraints |

**Run validators manually:**
```bash
python3 skills/skill-maker/validators/blueprints.py
python3 skills/skill-maker/validators/skill_md_dispatch.py
python3 skills/skill-maker/validators/dependencies.py
```

All validators run automatically at commit via `validators/runner.py` (`.githooks/pre-commit`).

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
