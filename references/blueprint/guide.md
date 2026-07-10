# Skill Blueprint Guide

## Overview

A **blueprint** defines the contract of a skill: what it depends on, what
interfaces it exports, and how those interfaces are validated and executed.

Blueprints serve two purposes:
1. **Document** the skill's API and constraints
2. **Enable validation** to catch errors at development time, not runtime

---

## Architecture: Two-Layer Validation

Validation is split into two independent layers.

### Layer 1: YAML structure (JSON Schema)
**File:** `blueprint/schema.json`

Validates individual blueprint files in isolation:

- Is `interface_version` a positive integer?
- Is `category` a known taxonomy node?
- Does `interfaces.machine` / `interfaces.llm` have the right shape?
- Are interface names valid?
- Are patterns well-formed?
- If `allow_all_skills: true`, is `allowed_callers` empty?
- Is `runtime` used only under `interfaces.machine.*`?

### Layer 2: relationships (Python validators)
**Files:** `skills/skill-maker/validators/`

Validates constraints that span multiple blueprints:

1. No skill depends on itself
2. `major_version` matches the dependency's `interface_version`
3. Exported canonical interface names exist
4. Restricted interfaces are only exported to allowed callers
5. Duplicate YAML keys are rejected before they can mask interfaces

---

## Core model

### Interface namespaces

Every blueprint may define two public interface namespaces:

- `interfaces.machine.<name>`
- `interfaces.llm.<name>`

Canonical external names are:

- machine: `skill.machine.name`
- llm: `skill.llm.name`

Examples:

- `list-manager.machine.read-list`
- `find-handoff-candidates.machine.scan`
- `prepare-handoff.llm.compose-note`

Local interface names are the mapping keys under `machine` or `llm`. They are
dash-separated and must not contain dots.

### Why mappings, not lists

Interfaces are mappings rather than `[{name: ...}, ...]` lists because:

- lookup is direct
- uniqueness is natural after parse
- validation is simpler
- the canonical name is already encoded by the key path

One caveat: JSON Schema validates the parsed mapping, not the raw YAML text, so
duplicate-key rejection must also exist in the Python validation/load path.

---

## Creating a new skill blueprint

### Step 1: Create the file

```bash
touch skills/<skill-name>/blueprint.yaml
```

### Step 2: Copy the template

Start from `references/blueprint/template.yaml`.

Representative structure:

```yaml
category: research-assistant
interface_version: 1
cross_platform: true

depends_on:
  list-manager:
    major_version: 1
    exports:
      - list-manager.machine.read-list

skill_interface:
  inputs:
    - User request
  outputs:
    - Primary artifact
  side_effects:
    - Files written to disk

interfaces:
  machine:
    read-data:
      description: "Read an input file."
      usage: "<file>"
      allow_all_skills: true
      allowed_callers: []
      patterns:
        - min_positionals: 1
          allow_stdin: false
          notes: "First positional is the input file."
      runtime:
        kind: python_module
        module: scripts.read_data
      dependencies: []

  llm:
    summarize:
      description: "Summarize the collected records."
      binding:
        kind: markdown_file
        path: interfaces/summarize.md
```

---

## Key fields

### `category`

Required single string from the typed enum in `schema.json`.

### `interface_version`

Positive integer. Increment when breaking changes occur. Dependents must match
this version in `depends_on.<skill>.major_version`.

### `cross_platform`

Optional boolean. Default behavior is `true`.

- `true` = the skill is expected to satisfy the shared cross-platform validator
- `false` = the skill is intentionally platform-specific and is exempt

### `depends_on`

Map of skill name → dependency spec.

For each dependency with a blueprint, declare:

- `major_version`
- `exports`

Each `exports` entry is the fully qualified canonical interface name, e.g.
`other-skill.machine.read-data`.

### `skill_interface`

High-level contract in plain language. Lists inputs, outputs, side effects.

### `interfaces.machine`

Map of machine-interface name → invocation contract.

Each machine interface owns:

- `description`
- `usage`
- `patterns`
- `allow_all_skills`
- `allowed_callers`
- `runtime`
- `dependencies`

Machine interfaces are the target executable interface model. Legacy
`script_interfaces` remain executable during migration and use the same
dependency declaration shape.

### `dependencies`

Required list on every executable interface: new-style machine interfaces and
legacy `script_interfaces`. Use `[]` when the interface has no non-stdlib
runtime dependencies.

Each dependency is a factual runtime requirement with:

- `kind`: `python` for installable Python packages, or `binary` for executable
  tools expected on `PATH`
- `name`: package or executable name
- `reason`: short human explanation used by docs and review

Examples:

```yaml
dependencies:
  - kind: python
    name: PyYAML
    reason: "Reads YAML list files."
  - kind: binary
    name: curl
    reason: "Fetches remote JSON from the weather API."
```

These declarations are not permission suggestions. Keep developer-selected
approval baselines in top-level `suggested_permissions`.

### `interfaces.llm`

Map of llm-interface name → documented prompt/interface contract.

Each llm interface typically owns:

- `description`
- `binding`
- `allow_all_skills`
- `allowed_callers`
- optional `routing_hints`

LLM interfaces are documented and routed by skill logic. The dispatcher never
executes them.

The local Markdown form is a relative path from the skill root:

```yaml
binding:
  kind: markdown_file
  path: interfaces/summarize.md
```

For externally hosted interfaces, use:

```yaml
binding:
  kind: uri
  uri: https://example.com/interfaces/summarize.md
```

Use `binding` rather than `runtime` because an LLM interface points at a
descriptive prompt contract, not an executable program.

---

## Runtime metadata

Runtime metadata lives inline under each machine interface:

```yaml
interfaces:
  machine:
    scan:
      runtime:
        kind: python_module
        module: scripts.scan
      dependencies: []
```

This is **internal metadata**. It belongs in the blueprint so the dispatcher
can execute the interface, but it is not part of the user-facing generated
documentation.

Current standard runtime kinds:

### `python_module`

```yaml
runtime:
  kind: python_module
  module: scripts.scan
dependencies: []
```

Use for Python skill code. The dispatcher runs it as a real module in a fresh
subprocess, which allows normal relative imports inside `scripts/`.

### `command`

```yaml
runtime:
  kind: command
  argv: ["maker", "--mode", "convert"]
dependencies:
  - kind: binary
    name: maker
    reason: "Performs the conversion invoked by this interface."
```

Use for non-Python tools.

The blueprint sync tool generates `references/blueprint/runtime_dependencies.json`
from all executable-interface `dependencies` declarations, including legacy
`script_interfaces` during migration. Installers should use that JSON manifest,
not PyYAML or direct blueprint parsing, when installing declared runtime
packages.

---

## Import model

Machine interfaces are executed without depending on the caller's working
directory.

The intended Python import model for a skill file is:

```python
from .storage import load_plan
from officina.common.paths import repo_root
from officina.dispatcher import dispatch
```

Not:

```python
from skills.other_skill.scripts.foo import bar
from validators.runner import main
```

Rules:

- same-skill imports: relative imports inside `scripts/`
- first-party shared/runtime imports: `officina.*`
- cross-skill behavior: `dispatch(...)`
- other repo packages outside `src/officina/` are not part of the import surface

Nested packages under `scripts/` are supported as long as they have
`__init__.py` files.

---

## Common machine-interface patterns

### Read-only interface

```yaml
interfaces:
  machine:
    read-data:
      allow_all_skills: true
      allowed_callers: []
      patterns:
        - min_positionals: 1
          positional_patterns:
            0: "^[a-z0-9_-]+$"
          allow_stdin: false
          notes: "First positional is the resource id."
      runtime:
        kind: python_module
        module: scripts.read_data
```

### Internal-only interface

```yaml
interfaces:
  machine:
    internal-worker:
      allow_all_skills: false
      allowed_callers: []
      runtime:
        kind: python_module
        module: scripts.internal_worker
```

### Restricted interface

```yaml
interfaces:
  machine:
    read-lists:
      allow_all_skills: false
      allowed_callers:
        - daily-plan
        - email-triage
      patterns:
        - min_positionals: 1
          positional_patterns:
            0: "^lists/.*"
          notes: "Only list paths under lists/ are allowed."
      runtime:
        kind: python_module
        module: scripts.read_lists
```

### Multiple calling conventions

```yaml
interfaces:
  machine:
    update-data:
      patterns:
        - name: "file-mode"
          min_positionals: 1
          max_positionals: 1
          required_flags: ["--file"]
          allow_stdin: false
          notes: "Caller supplies patch file via --file."
        - name: "stdin-mode"
          min_positionals: 1
          max_positionals: 1
          forbidden_flags: ["--file"]
          allow_stdin: true
          notes: "Caller pipes patch data to stdin."
      runtime:
        kind: python_module
        module: scripts.update_data
```

---

## Dispatcher resolution model

Canonical CLI form:

```bash
dispatcher --caller-skill daily-plan list-manager.machine.read-list /tmp/todo.yaml
```

Canonical Python form:

```python
dispatch(
    caller_skill="daily-plan",
    target="list-manager.machine.read-list",
    args=["/tmp/todo.yaml"],
)
```

At runtime the dispatcher:

1. parses `skill.machine.name`
2. resolves `<repo>/skills/<skill>/blueprint.yaml`
3. loads `interfaces.machine.<name>`
4. validates dependency/version/export/access rules
5. matches patterns
6. resolves inline `runtime`
7. executes it

No absolute path is stored in the blueprint. Absolute paths are derived at
dispatch time from the repo root and skill name.

---

## Validation

### Running validation

```bash
python3 skills/skill-maker/validators/blueprints.py
python3 skills/skill-maker/validators/skill_md_dispatch.py
python3 skills/skill-maker/validators/blueprint_relationships.py
```

All validators also run automatically at commit through `validators/runner.py`
via `.githooks/pre-commit`.

### Common errors

**Schema error: `allow_all_skills` must be boolean**

```
skills/my-skill/blueprint.yaml:
  interfaces.machine.read-data: `allow_all_skills` must be a boolean
```

**Schema error: if `allow_all_skills: true`, `allowed_callers` must be empty**

```
skills/my-skill/blueprint.yaml:
  interfaces.machine.read-data: if allow_all_skills is true, allowed_callers must be empty
```

**Relationship error: export does not exist**

```
skills/my-skill/blueprint.yaml: depends_on.other-skill.exports includes
  other-skill.machine.read-data, but that interface does not exist
```

**Validation error: duplicate YAML key masked an interface**

```
skills/my-skill/blueprint.yaml: duplicate key `scan` under interfaces.machine
```

---

## IDE integration

### VS Code / editor setup

To enable schema validation in your editor:

1. Install JSON Schema support if needed
2. Add to `.vscode/settings.json`:

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

3. Reload the editor

For Python import resolution, keep the repo runtime model in sync with the IDE:

- shared first-party packages live under `src/officina/`
- each skill root is its own Python execution environment
- repo root itself should not be added as a generic import root

---

## Best practices

1. Keep machine interfaces narrowly scoped: one canonical interface per public
   callable behavior.
2. Keep `runtime` internal and user-facing docs external.
3. Use relative imports for same-skill code and `officina.*` for shared code.
4. Use `allow_all_skills: true` sparingly.
5. Match major versions carefully.
6. Prefer `python_module` runtime for Python skill code so nested packages and
   relative imports work naturally.

---

## Reference files

- `blueprint/schema.json`
- `blueprint/template.yaml`
- `skills/skill-maker/validators/`

Refer to the template (`blueprint/template.yaml`) for commented examples of the
full structure.
