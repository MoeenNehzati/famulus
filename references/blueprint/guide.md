# Skill Blueprint Guide

## Overview

A **blueprint** defines the contract of a skill: what it depends on, what interfaces it exports, and how those interfaces can be invoked.

Blueprints serve two purposes:
1. **Document** the skill's API and constraints
2. **Enable validation** to catch errors at development time, not runtime

---

## Architecture: Two-Layer Validation

Validation is split into two independent layers:

### Layer 1: YAML Structure (JSON Schema)
**File:** `blueprint/schema.json`

Validates individual blueprint files in isolation:
- Is `interface_version` a positive integer?
- Is `category` a known value from the taxonomy tree?
- Are patterns well-formed?
- **If `allow_all_skills: true`, is `allowed_callers` empty?**

**When it runs:**
- At edit time (IDE with schema integration)
- At commit time (pre-commit hook)

**Files checked:** Each `skills/<name>/blueprint.yaml`

### Layer 2: Relationships (Python Validator)
**Files:** `skills/skill-maker/validators/blueprint_relationships.py` and `skills/skill-maker/validators/interface_ids.py`

Validates constraints that span multiple blueprints:
1. No skill depends on itself
2. If dependency has a blueprint, `major_version` must be declared
3. `major_version` matches the dependency's `interface_version`
4. Exported interface id exists in the dependency
5. Non-public interfaces only export to skills in `allowed_callers`
6. Interface ids are unique within each skill, including named subinterfaces

**When it runs:**
- At commit time (pre-commit hook)

**Files checked:** All blueprints together

---

## Creating a New Skill Blueprint

### Step 1: Create the file
```bash
touch skills/<skill-name>/blueprint.yaml
```

### Step 2: Use the template
Copy the structure from `blueprint/template.yaml`:

```yaml
category: research-assistant
interface_version: 1
cross_platform: true

depends_on:
  dependency-skill:
    major_version: 1
    exports:
      - read-data
      - update-data

skill_interface:
  inputs:
    - User request
    - Local files
  outputs:
    - Primary artifact
  side_effects:
    - Files written to disk

script_interfaces:
  read-data:
    id: read-data
    cwd: skill_root
    command: ["python3", "scripts/tool.py", "read"]
    default:
      patterns:
        - min_positionals: 1
          allow_extra_positionals: true
          allow_stdin: false
      allow_all_skills: true
      allowed_callers: []

  internal-helper:
    id: internal-helper
    cwd: skill_root
    command: ["python3", "scripts/internal.py"]
    # No explicit default block here: the owner-facing default subinterface
    # still exists, uses id `internal-helper`, and has no declared restrictions.
```

### Step 3: Understand key fields

#### `category`
Required single string from the typed enum in `schema.json`. The taxonomy is a tree; names encode hierarchy via postfix — `workflow-general-assistant` is a child of `general-assistant`. A skill may be placed at any node (leaf or intermediate).

```
assistant  (structural root — not a valid value)
├── research-assistant
├── general-assistant
│   ├── productivity-general-assistant
│   └── workflow-general-assistant
├── development-assistant
│   ├── skill-making-development-assistant
│   └── coding-development-assistant
└── system-assistant
```

To add a new node: update the `category` enum in `schema.json` and the `_CATEGORY_NODES` set in `skills/skill-maker/validators/blueprints.py`.

#### `interface_version`
Positive integer. Increment when breaking changes occur. Dependents must match this version in their `depends_on.X.major_version`.

#### `cross_platform`
Optional boolean. Default behavior is `true`.

- `true` = the skill is expected to satisfy the shared cross-platform validator
- `false` = the skill is intentionally platform-specific and is exempt from that validator

Use `false` only when platform-specific behavior is part of the skill's contract, such as integration with a scheduler, service manager, or OS-specific runtime surface. Do not use it merely to postpone portability work.

#### `depends_on`
Map of skill name → dependency spec. For each dependency with a blueprint, declare:
- `major_version` — must match the dependency's `interface_version`
- `exports` — which interfaces you use (optional; empty means you don't call it)

#### `skill_interface`
High-level contract in plain language. Lists inputs, outputs, side effects.

#### `script_interfaces`
Map of interface-group name → invocation contract. Each group defines one owner-facing default id plus optional named subinterfaces for narrower external views.

**`id`** — required stable id of the owner-facing/default subinterface. This id is what the dispatcher resolves for the top/default surface. Ids must be unique within the skill; that uniqueness is enforced by `skills/skill-maker/validators/interface_ids.py`.

**`description`** — optional human-facing owner summary. If present, the sync tool injects this top/default interface into `SKILL.md` so the skill can see the useful owner-facing script surface without loading the full blueprint. Narrow named subinterfaces are not injected.

**`cwd`** — where the command runs (`skill_root` or `repo_root`)

**`command`** — argv prefix as list of tokens shared by the default surface and all named subinterfaces in the group

**`default`** — optional explicit override of the owner-facing default subinterface. The default subinterface shares the parent interface's `id`, so `default` must not define its own `id`.

If `default` is omitted, the owner-facing default subinterface still exists, still uses the parent `id`, and has no declared pattern restrictions unless you define them.

**`subinterfaces`** — optional named narrower views of the same command. Use these to restrict other skills without affecting the owner-facing default surface. Each named subinterface has its own required unique `id`.

**`patterns` / `allow_all_skills` / `allowed_callers` at the top level** — supported as legacy shorthand for the owner-facing default subinterface. For new blueprints, prefer `default.patterns`, `default.allow_all_skills`, and `default.allowed_callers`.

**`patterns`** — list of valid calling conventions
- `min_positionals`, `max_positionals` — positional argument count
- `allow_stdin`, `allow_extra_positionals` — input modes
- `positional_patterns` — regex validation for positional args (by index)
- `flag_patterns` — regex validation for flag values
- `required_flags`, `allowed_flags`, `forbidden_flags` — flag constraints
- `notes` — documentation for maintainers

**`allow_all_skills`** — boolean
- `true` = public; any dependent can use this interface
- `false` = restricted; only skills in `allowed_callers` can use it

**`allowed_callers`** — list of skill names
- Only meaningful if `allow_all_skills: false`
- If empty and `allow_all_skills: false`, interface is internal-only (owning skill only)
- **Constraint:** If `allow_all_skills: true`, must be empty

---

## Common Patterns

### Read-Only Default Interface
```yaml
read-data:
  id: read-data
  cwd: skill_root
  command: ["python3", "scripts/reader.py"]
  default:
    patterns:
      - min_positionals: 1
        positional_patterns:
          0: "^[a-z0-9_-]+$"  # validate first arg format
        allow_stdin: false
    allow_all_skills: true
    allowed_callers: []
```

### Internal-Only Interface
```yaml
internal-worker:
  id: internal-worker
  cwd: skill_root
  command: ["python3", "scripts/worker.py"]
  # No explicit default section: the owner-facing default subinterface still
  # exists, uses id `internal-worker`, and has no declared pattern restrictions.
```

### Restricted Named Subinterface (Without Affecting the Owner)
```yaml
read-lists:
  id: read-lists
  cwd: skill_root
  command: ["python3", "scripts/lists.py", "read"]
  description: "Read list data using the full owner-facing script surface."
  subinterfaces:
    planner-view:
      id: read-lists-planner
      patterns:
        - min_positionals: 1
          positional_patterns:
            0: "^lists/.*"  # only access lists/ directory
      allow_all_skills: false
      allowed_callers:
        - daily-plan
        - email-triage
```

### Multiple Calling Conventions
```yaml
update-data:
  id: update-data
  cwd: skill_root
  command: ["python3", "scripts/updater.py"]
  default:
    patterns:
      # PATTERN 1: File mode
      - name: "file-mode"
        min_positionals: 1
        max_positionals: 1
        required_flags: ["--file"]
        allow_stdin: false
        notes: "Caller supplies patch file via --file"

      # PATTERN 2: Stdin mode
      - name: "stdin-mode"
        min_positionals: 1
        max_positionals: 1
        forbidden_flags: ["--file"]
        allow_stdin: true
        notes: "Caller pipes patch data to stdin"
    allow_all_skills: true
    allowed_callers: []
```

---

## Migration: `exported` → `allow_all_skills`

### What Changed

The field `exported` has been renamed to `allow_all_skills` for clarity.

**Old:**
```yaml
exported: true       # "Is this exported?"
allowed_callers: []
```

**New:**
```yaml
allow_all_skills: true   # "Can all skills use this?"
allowed_callers: []
```

### Why

The new name explicitly answers: "Can all skills use this interface?" This is clearer than "is it exported?", which is ambiguous with "is it publicly visible?" vs "can dependents use it?".

### Migration Steps

**For skill creators:**
1. In your `blueprint.yaml`, replace `exported` with `allow_all_skills`
2. The value stays the same:
   - `exported: true` → `allow_all_skills: true`
   - `exported: false` → `allow_all_skills: false`
3. No logic changes; same semantics

**For the codebase:**
- All 20 existing skill blueprints have been migrated
- The JSON Schema enforces the new name
- Old blueprints using `exported` will fail validation

**Deadline:** Effective immediately. Use `allow_all_skills` in all new blueprints.

### Examples

**Before:**
```yaml
script_interfaces:
  public-api:
    command: ["python3", "scripts/api.py"]
    patterns: [...]
    exported: true
    allowed_callers: []
```

**After:**
```yaml
script_interfaces:
  public-api:
    command: ["python3", "scripts/api.py"]
    patterns: [...]
    allow_all_skills: true
    allowed_callers: []
```

---

## Validation

### Running Validation

**All validators (run individually or together):**
```bash
python3 skills/skill-maker/validators/blueprints.py
python3 skills/skill-maker/validators/skill_md_dispatch.py
python3 skills/skill-maker/validators/dependencies.py
```

**All validators automatically at commit** — run by `validators/runner.py` via `.githooks/pre-commit`.

### Common Errors

**Schema error: `allow_all_skills` must be boolean**
```
skills/my-skill/blueprint.yaml:
  script_interfaces.read-data: `allow_all_skills` must be a boolean
```
**Fix:** Ensure `allow_all_skills: true` or `allow_all_skills: false` (not a string).

**Schema error: if `allow_all_skills: true`, `allowed_callers` must be empty**
```
skills/my-skill/blueprint.yaml:
  script_interfaces.read-data: if allow_all_skills is true, allowed_callers must be empty
```
**Fix:** Remove `allowed_callers` or change it to an empty list `[]`.

**Relationship error: major_version must be declared**
```
skills/my-skill/blueprint.yaml: depends_on.other-skill must declare
  major_version because other-skill has a blueprint
```
**Fix:** Add `major_version` to match the dependency's `interface_version`:
```yaml
depends_on:
  other-skill:
    major_version: 1    # <- add this
    exports: [read-data]
```

**Relationship error: major_version does not match**
```
skills/my-skill/blueprint.yaml: depends_on.other-skill.major_version=1
  does not match other-skill interface_version=2
```
**Fix:** Update your declaration to match:
```yaml
depends_on:
  other-skill:
    major_version: 2    # <- update to 2
    exports: [read-data]
```

---

## IDE Integration

### VS Code / Editor Setup

To enable schema validation in your editor:

1. **Install JSON Schema extension** (if not already present)
2. **Add to your editor settings** (`.vscode/settings.json`):
   ```json
   {
     "json.schemas": [
       {
         "fileMatch": ["skills/*/blueprint.yaml"],
         "url": "./references/blueprint/schema.json"
       }
     ]
   }
   ```
3. **Reload editor** — you'll now get:
   - Real-time validation errors
   - Autocomplete hints
   - Schema documentation on hover

---

## Reference Files

- **Schema:** `blueprint/schema.json` — Full JSON Schema validation rules
- **Template:** `blueprint/template.yaml` — Annotated examples of all features
- **Validators:** `skills/skill-maker/validators/` — `blueprints.py`, `skill_md_dispatch.py`, `dependencies.py`, and others

---

## Best Practices

1. **Keep patterns focused.** One pattern per calling convention. Multiple patterns add complexity.

2. **Use positional_patterns for security.** Validate paths (e.g., `^lists/.*`) and formats at the schema level, not in your script.

3. **Document with notes.** Every pattern should have a `notes` field explaining its purpose.

4. **Use allowed_callers for ownership.** Restrict write interfaces to the skill that owns the resource:
   ```yaml
   # list-manager owns lists/
   write-list:
     allowed_callers: [daily-plan, email-triage]
   ```

5. **Match major versions carefully.** When a dependency increments `interface_version`, update your `depends_on.*.major_version` immediately.

6. **Use allow_all_skills: true sparingly.** Prefer restricted access (`allow_all_skills: false` with `allowed_callers`) for sensitive or resource-heavy interfaces.

---

## Questions?

Refer to the template (`blueprint/template.yaml`) for commented examples of every feature. It's the authoritative reference.
