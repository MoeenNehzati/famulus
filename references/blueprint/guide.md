# Skill Blueprint Guide

## Overview

A **blueprint** defines the contract of a skill: what interfaces it exposes,
which version-pinned interfaces those interfaces use, and how the interfaces
are validated and executed.

Blueprints serve two purposes:
1. **Document** the skill's API and constraints
2. **Enable validation** to catch errors at development time, not runtime

---

## Architecture: Two-Layer Validation

Validation is split into two independent layers.

### Layer 1: YAML structure (JSON Schema)
**File:** `blueprint/schema.json`

Validates individual blueprint files in isolation:

- Does every interface declare a positive integer `version`?
- Is `category` a known taxonomy node?
- Are `role` and `kind` known documentation taxonomy values?
- Does `interfaces.machine` / `interfaces.llm` have the right shape?
- Are interface names valid?
- Are patterns well-formed?
- If `allow_all_skills: true`, is `allowed_callers` empty?
- Is `invocation` used only under `interfaces.machine.*`?

### Layer 2: relationships (Python validators)
**Files:** `skills/skill-maker/validators/`

Validates constraints that span multiple blueprints:

1. Version-pinned `uses_interfaces` targets exist
2. Pinned versions match target interface versions
3. LLM interfaces only use same-skill machine interfaces or LLM interfaces
4. Machine interfaces only use machine interfaces
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
role: research-writing
kind: reviewer
cross_platform: true

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
      version: 1
      description: "Read an input file."
      usage: "<file>"
      allow_all_skills: true
      allowed_callers: []
      patterns:
        - min_positionals: 1
          allow_stdin: false
          notes: "First positional is the input file."
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/read_data.py:ReadData
        behavior_sources: []
      dependencies: []

  llm:
    default:
      version: 1
      description: "Primary LLM-facing skill instructions."
      binding:
        kind: skill_file
        path: SKILL.md

    summarize:
      version: 1
      description: "Summarize the collected records."
      binding:
        kind: markdown_file
        path: interfaces/summarize.md
```

---

## Key fields

### `category`

Required single string from the typed enum in `schema.json`.

### `role`

Required single string from the typed enum in `schema.json`. This is the
primary user-facing domain the skill serves, such as `productivity`,
`math-reasoning`, `document-processing`, `development-assistant`,
`system-operations`, `integration`, `meta-skill`, `automation`, or `mode`.

Use `role` for generated docs grouping and graph clustering. Keep `category`
during migration for compatibility with existing generated blocks and tools.

### `kind`

Required single string from the typed enum in `schema.json`. This is the
primary shape of help the skill provides, such as `reviewer`, `auditor`,
`planner`, `client`, `storage`, `scheduler`, `converter`, `renderer`,
`triager`, `analyzer`, or `maintenance`.

Use `kind` for user-facing filters like "show reviewer skills" or "show
scheduler skills". If a skill has many interface-level behaviors, choose the
dominant user-facing kind at the skill level and reserve finer interface kinds
for the later interface metadata migration.

### Interface `version`

Every machine and LLM interface declares a positive integer `version`.
Increment it when that interface's exported contract changes in a breaking way.
The skill version is the version of `interfaces.llm.default`; there is no
separate top-level skill version.

### `cross_platform`

Optional boolean. Default behavior is `true`.

- `true` = the skill is expected to satisfy the shared cross-platform validator
- `false` = the skill is intentionally platform-specific and is exempt

### `skill_interface`

High-level contract in plain language. Lists inputs, outputs, side effects.

### `interfaces.machine`

Map of machine-interface name → invocation contract.

Each machine interface owns:

- `version`
- `description`
- `usage`
- `patterns`
- `allow_all_skills`
- `allowed_callers`
- `invocation`
- `dependencies`
- `uses_interfaces`
- `invocation.behavior_sources`
- `direct_io`
- `owns_filesystem`

Machine interfaces are the executable interface model. The legacy
`script_interfaces` key is no longer accepted by the schema or sync validator.

### `dependencies`

Required list on every executable machine interface. Use `[]` when the
interface has no non-stdlib runtime dependencies.

Each dependency is a factual runtime requirement with:

- `kind`: `python-package` for installable Python packages, or `binary` for
  executable tools expected on `PATH`
- `name`: package or executable name
- `reason`: short human explanation used by docs and review

Examples:

```yaml
dependencies:
  - kind: python-package
    name: PyYAML
    reason: "Reads YAML list files."
  - kind: binary
    name: curl
    reason: "Fetches remote JSON from the weather API."
```

These declarations are not permission suggestions. Keep developer-selected
approval baselines in top-level `suggested_permissions`.

### `uses_interfaces`

Any machine or LLM interface may declare `uses_interfaces`, a list of
version-pinned canonical interfaces that this interface invokes or orchestrates:

```yaml
uses_interfaces:
  - interface: other-skill.machine.read-data
    version: 1
```

This is the only dependency declaration for blueprint interfaces. There is no
top-level `depends_on`.

Machine interfaces may use same-skill or cross-skill machine interfaces. They
must not use LLM interfaces.

LLM interfaces may use their own skill's machine interfaces and any skill's LLM
interfaces. They must not directly use another skill's machine interfaces.

Audit hashing includes these used interface hashes recursively. If an LLM
interface routes work through a machine interface and that machine interface
changes, the LLM interface hash changes as well.

### `behavior_sources`

Every interface must declare behavior sources. LLM interfaces use
`behavior_sources` directly. Machine interfaces use
`invocation.behavior_sources`.

Behavior sources are non-code files or directories the interface reads to
decide how to behave: instruction Markdown, schemas, prompt templates, examples,
parser tables, checklists, policies, validation rules, or config files. They are
not ordinary user subject input. For example, a PDF-to-markdown interface should
not list the PDF passed by the user, but it should list a schema, template, or
policy file loaded to decide how conversion works.

Python imports, `_rtx/` entrypoint files, and dispatcher targets are discovered
mechanically. Do not duplicate those implementation or dispatch edges in
`behavior_sources`.

Paths are relative to the directory containing `blueprint.yaml` by default. Use
`$repo/` for repository-root relative paths. Use `[]` only after checking that
the interface has no non-code behavior-shaping files.

```yaml
behavior_sources:
  - path: references/triage-policy.md
    content: config
    format: markdown
    reason: "Defines how the LLM classifies messages."

invocation:
  kind: python_machine_interface
  entrypoint: _rtx/validate.py:Interface
  behavior_sources:
    - path: schemas/input.schema.json
      content: validator
      format: json
      reason: "Defines accepted input structure."
```

### `direct_io`

Every machine and LLM interface must declare `direct_io` with all three lists:

```yaml
direct_io:
  reads: []
  writes: []
  network: []
```

`direct_io` is immediate-only semantic metadata for generated docs, search,
graphs, and safety summaries. It describes what the interface itself reads,
writes, sends, downloads, deletes, or requests. Do not copy IO from dependency
interfaces; transitive IO is generated by recursively following
`uses_interfaces`.

Use empty lists only after checking that the interface has no direct IO of that
kind. Keep direct IO separate from behavior sources: `direct_io` describes what
the interface does during an invocation, while `behavior_sources` describes the
files that shape the interface's behavior across invocations.

Use coarse `content` values. `content` names the user-meaningful object for
docs, search, and graphs, not an internal field of that object. Use
`content: email`, not separate values for subject, body, title, date, headers,
or IDs. Add a finer content value only when users will filter or visualize that
object independently.

### `owns_filesystem`

Every machine and LLM interface must declare `owns_filesystem`. Use `[]` when
the interface owns no filesystem paths.

Ownership is immediate and interface-scoped. If an interface owns a filesystem
path, only that interface may write matching `direct_io.writes` entries. Only
that interface and the canonical interfaces listed in `allowed_readers` may read
matching `direct_io.reads` entries.

Two different interfaces must not own overlapping filesystem paths. Ownership
is a single-writer authority, not a shared label.

Ownership entries support exact paths and regexes:

```yaml
owns_filesystem:
  - match: exact
    path: "$repo/data/private.yaml"
    allowed_readers:
      - other-skill.machine.read-private-data
    reason: "This interface is the sole writer for private data."

  - match: regex
    path: "_build/reports/.*\\.json"
    allowed_readers: []
```

Plain paths are skill-root-relative. Use `$repo/`, `$home/`, or `$tmp/` when the
owned filesystem path lives outside the skill directory. For `match: regex`,
the pattern is matched against the declared `direct_io` path string.

### `interfaces.llm`

Map of llm-interface name → documented prompt/interface contract.

Every blueprint must define `interfaces.llm.default`. It represents the skill's
ordinary `SKILL.md` prompt surface:

```yaml
interfaces:
  llm:
    default:
      description: "Primary LLM-facing skill instructions."
      binding:
        kind: skill_file
        path: SKILL.md
      direct_io:
        reads:
          - medium: prompt
            access: read
            content: document
            format: text
            sensitivity: user-private
        writes:
          - medium: prompt
            access: write
            content: response
            format: markdown
            sensitivity: derived-private
        network: []
      owns_filesystem: []
```

Each llm interface typically owns:

- `description`
- `binding`
- `allow_all_skills`
- `allowed_callers`
- `direct_io`
- `owns_filesystem`
- optional `routing_hints`

LLM interfaces are documented and routed by skill logic. The dispatcher never
executes them.

The local Markdown form is a relative path from the directory containing
`blueprint.yaml`:

```yaml
binding:
  kind: markdown_file
  path: interfaces/summarize.md
```

The default skill-file form explicitly names `SKILL.md`, also relative to the
directory containing `blueprint.yaml`:

```yaml
binding:
  kind: skill_file
  path: SKILL.md
```

For externally hosted interfaces, use:

```yaml
binding:
  kind: uri
  uri: https://example.com/interfaces/summarize.md
```

Use `binding` rather than `invocation` because an LLM interface points at a
descriptive prompt contract, not an executable program.

---

## Invocation Metadata

Invocation metadata lives inline under each machine interface:

```yaml
interfaces:
  machine:
    scan:
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/handoff_scan.py:HandoffScan
        behavior_sources: []
      dependencies: []
```

This is **internal metadata**. It belongs in the blueprint so the dispatcher
can execute the interface, but it is not part of the user-facing generated
documentation.

Current standard invocation kind:

### `python_machine_interface`

```yaml
invocation:
  kind: python_machine_interface
  entrypoint: _rtx/handoff_scan.py:HandoffScan
  behavior_sources: []
dependencies: []
```

Use for Python callable interfaces. The dispatcher runs the shared
`officina.runtime.python_machine_interface_runner`, which preserves normal
relative imports inside `_rtx/` and provides the standard route-smoke path.

Raw command invocation is intentionally not allowed. If an interface needs an
external binary, wrap that behavior in a `python_machine_interface`, declare
the binary under `dependencies`, and keep argument parsing, validation, and
cross-platform behavior in Python.

For `python_machine_interface`, route smoke is built into the shared runner.
The route-smoke test appends `--route-smoke`; the shared runner imports the
interface class, builds its parser, and exits before normal interface
execution.

The blueprint sync tool generates `references/blueprint/runtime_dependencies.json`
from all `interfaces.machine.<name>.dependencies` declarations. Installers
should use that JSON manifest, not PyYAML or direct blueprint parsing, when
installing declared runtime packages.

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
from skills.other_skill._rtx._foo_bar import bar
from validators.runner import main
```

Rules:

- same-skill imports: relative imports inside `_rtx/`
- first-party shared/runtime imports: `officina.*`
- cross-skill behavior: `dispatch(...)`
- other repo packages outside `src/officina/` are not part of the import surface

Runtime files currently remain direct children of `_rtx/`. Same-skill helpers
can still use relative imports between direct `_rtx` modules; nested package
directories need an explicit validator/policy update before use.

Class-backed Python machine interfaces are declared with
`invocation.kind: python_machine_interface`. That support is a contract
declaration only; it does not migrate existing skill runtime files.

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
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/read_data.py:ReadData
        behavior_sources: []
      dependencies: []
```

### Internal-only interface

```yaml
interfaces:
  machine:
    internal-worker:
      allow_all_skills: false
      allowed_callers: []
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/internal_worker.py:InternalWorker
        behavior_sources: []
      dependencies: []
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
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/read_lists.py:ReadLists
        behavior_sources: []
      dependencies: []
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
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/update_data.py:UpdateData
        behavior_sources: []
      dependencies: []
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
6. resolves inline `invocation`
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

**Relationship error: used interface does not exist**

```
skills/my-skill/blueprint.yaml: my-skill.machine.read-data
  uses_interfaces.0.interface targets unknown interface
  'other-skill.machine.read-data'
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
2. Keep `invocation` internal and user-facing docs external.
3. Use relative imports for same-skill code and `officina.*` for shared code.
4. Use `allow_all_skills: true` sparingly.
5. Match major versions carefully.
6. Use `python_machine_interface` for machine interfaces. Its shared runner
   preserves same-skill `_rtx` relative imports and provides the standard
   route-smoke path. Raw `command` runtimes are not allowed.

---

## Reference files

- `blueprint/schema.json`
- `blueprint/template.yaml`
- `skills/skill-maker/validators/`

Refer to the template (`blueprint/template.yaml`) for commented examples of the
full structure.
