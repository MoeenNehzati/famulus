# Famulus Blueprint v2 Implementation Plan

Status: historical design note. This plan predates the current
`interfaces.machine` blueprint model; live blueprint syntax and validator
policy are documented in `docs/skill-blueprints.md` and
`references/skill-standards/skill-guidelines.md`.

## Goal

Implement a new `blueprint.yaml` contract model that cleanly separates:

1. **Skill-level dependencies** — one skill invokes or refers to another skill as an LLM skill.
2. **Interface-level dependencies** — one skill calls another skill’s exported machine interface through `dispatcher` or `script_dispatcher.dispatch()`.

The new blueprint should also introduce explicit versioning for skill bodies and callable interfaces, using integer versions for skill-level contracts and decimal string versions for interface-level contracts.

Example:

```yaml
version:
  skill: "3"

script_interfaces:
  read-list:
    id: read-list
    version: "3.2"
```

Here `3.2` means “interface revision 2 under skill major version 3.” Interface versions must be strings, not YAML numbers, so `3.10` does not become `3.1`.

## Problem being solved

The current blueprint model has one `depends_on` map keyed by skill name. A dependency may contain `major_version` and `exports`, where `exports` names callable script interfaces. This conflates two different relationships:

```yaml
depends_on:
  list-manager:
    major_version: 1
    exports:
      - cloud-read
```

This is really an **interface dependency**, not necessarily a skill invocation dependency. The dependent skill may never need to invoke or read `list-manager` as an LLM skill; it may only need to call `list-manager.cloud-read`.

But a dependency without exports:

```yaml
depends_on:
  proof-audit:
    major_version: 1
```

should mean something different: the skill may invoke or refer to `proof-audit` as a skill in its hand-authored `SKILL.md`.

Right now the docs say dependencies authorize both skill invocation and dispatcher calls, but they do not make this distinction structurally explicit. The current validator also treats dependency sidecars and exact skill-name mentions in `SKILL.md` as the same set, which makes interface-only dependencies look like skill-body invocation dependencies.

Blueprint v2 fixes this by making the distinction first-class.

## Target YAML shape

Use this structure:

```yaml
blueprint_schema_version: 2

category: productivity-general-assistant

version:
  skill: "3"

depends_on:
  skills:
    proof-audit: "2"

  interfaces:
    cloud-files:
      lists-read: "1.4"
      lists-write: "1.5"

owns_extra:
  cloud_paths:
    - "lists/"

requires_extra:
  binaries: []
  python_packages: []
  platforms: []

skill_interface:
  inputs:
    - User request.
  outputs:
    - User-visible result.
  side_effects:
    - Plain-language summary only; mechanically relevant effects belong on interfaces.

script_interfaces:
  read-list:
    id: read-list
    version: "3.2"
    description: "Read a local YAML list file."
    usage: "<file> [filters]"
    cwd: skill_root
    command: ["python3", "_rtx/_yaml_store.py", "read"]

    artifacts:
      inputs:
        list_file:
          transport: positional
          index: 0
          kind: file
          extensions: [".yaml", ".yml"]
          access: read
      outputs:
        yaml:
          transport: stdout
          kind: stream
          formats: ["yaml"]

    effects:
      - artifact: yaml
        operations: ["write_stdout"]

    default:
      patterns:
        - min_positionals: 1
          allow_extra_positionals: true
          allow_stdin: false
          notes: "First positional is the local YAML list file; remaining positionals are filters."
      allow_all_skills: false
      allowed_callers:
        - daily-plan
```

## Versioning rules

### Skill version

`version.skill` is a required string integer:

```yaml
version:
  skill: "3"
```

It changes when the skill-level LLM contract changes in a breaking way: trigger semantics, required workflow, assumptions, outputs, or how another skill should invoke it.

A dependency on a skill uses this version:

```yaml
depends_on:
  skills:
    proof-audit: "3"
```

### Interface version

Every callable interface id has a required decimal string version:

```yaml
script_interfaces:
  read-list:
    id: read-list
    version: "3.2"
```

The major part must equal the owning skill’s `version.skill`.

So this is valid:

```yaml
version:
  skill: "3"

script_interfaces:
  read-list:
    version: "3.2"
```

This is invalid:

```yaml
version:
  skill: "4"

script_interfaces:
  read-list:
    version: "3.2"
```

because the skill is version 4 but the interface is declared as belonging to skill version 3.

Use regex validation for shape:

```text
skill version:     ^[1-9][0-9]*$
interface version: ^[1-9][0-9]*\.[1-9][0-9]*$
```

Then a Python cross-field validator must enforce:

```text
interface_version.split(".")[0] == version.skill
```

### Named subinterfaces

Named subinterfaces are callable ids too, so they must also have versions.

Example:

```yaml
script_interfaces:
  read-list:
    id: read-list
    version: "3.2"
    command: ["python3", "_rtx/_yaml_store.py", "read"]
    subinterfaces:
      planner-view:
        id: read-list-planner
        version: "3.3"
        patterns:
          - min_positionals: 1
            positional_patterns:
              0: "^lists/.*"
        allow_all_skills: false
        allowed_callers:
          - daily-plan
```

Each callable id must have exactly one version.

## Dependency semantics

### `depends_on.skills`

A skill dependency means:

> The hand-authored `SKILL.md` body may invoke, mention, or instruct use of the callee skill as an LLM skill.

Example:

```yaml
depends_on:
  skills:
    proof-audit: "3"
```

This means the hand-authored body may say things like:

```markdown
Invoke `proof-audit` before accepting the proof as sound.
```

Validator rule:

```text
Exact skill-name mentions in the hand-authored SKILL.md body must match depends_on.skills.
```

The generated contract block and generated interface block must be ignored for this check.

### `depends_on.interfaces`

An interface dependency means:

> This skill may call the listed callable interface ids through `dispatcher` or `script_dispatcher.dispatch()`.

Example:

```yaml
depends_on:
  interfaces:
    cloud-files:
      lists-read: "1.4"
      lists-write: "1.5"
```

This does **not** mean the skill may invoke `cloud-files` as a skill. It only means the skill may call those machine interfaces.

Validator rule:

```text
Every cross-skill dispatcher call must be declared in depends_on.interfaces with the exact callee interface version.
```

A skill may appear in both sections:

```yaml
depends_on:
  skills:
    list-manager: "3"
  interfaces:
    list-manager:
      cloud-read: "3.4"
```

That means both things are true: the skill body may invoke `list-manager` as a skill, and scripts may call `list-manager.cloud-read`.

## Artifact contracts

Artifacts are typed objects that cross an interface boundary. They are not every temporary implementation file.

Examples:

- positional file argument
- flag-supplied patch file
- stdin YAML payload
- stdout JSON/YAML/Markdown stream
- remote Google Drive file handle
- generated diff/patch

Artifacts should live under individual `script_interfaces`, not at the root, because they describe the callable interface boundary.

Example:

```yaml
artifacts:
  inputs:
    patch:
      transport: flag
      flag: "--file"
      kind: file
      extensions: [".yaml", ".yml"]
      access: read

  outputs:
    result:
      transport: stdout
      kind: stream
      formats: ["text"]
```

Mechanically checkable rules:

1. `transport: positional` requires an `index`.
2. A positional artifact with `index: N` requires at least one pattern with `min_positionals > N`.
3. `transport: flag` requires `flag`.
4. A flag artifact’s flag must appear in at least one pattern’s `required_flags`, `allowed_flags`, `forbidden_flags`, or `flag_patterns`.
5. `schema:` paths, if present, must exist.
6. `extensions:` entries must start with `"."`.
7. `effects[*].artifact` must refer to an artifact declared under `artifacts.inputs` or `artifacts.outputs`.

Do not add a root-level `mutation_policy`. Effects belong to interfaces.

## Interface effects

Effects describe what a callable interface does to its declared artifacts or external state.

Example:

```yaml
effects:
  - artifact: target_list
    operations: ["update_file"]
  - artifact: result
    operations: ["write_stdout"]
```

Allowed initial operations:

```text
read_file
create_file
update_file
delete_file
read_remote
create_remote
update_remote
delete_remote
write_stdout
write_stderr
send_email
create_calendar_event
update_calendar_event
delete_calendar_event
```

Keep this list small and explicit. Add operations only when a validator or dispatcher behavior needs them.

## Extra ownership

A skill implicitly owns its own directory:

```text
skills/<skill-name>/
```

Do not write that in YAML.

Only declare ownership outside the skill’s own directory:

```yaml
owns_extra:
  repo_paths:
    - "references/blueprint/schema.json"
    - "references/blueprint/template.yaml"
  cloud_paths:
    - "lists/"
  generated_blocks:
    - "README.md#skills-table"
```

Validators should use this to decide whether writes outside the skill directory are legitimate.

## Extra runtime requirements

Do not repeat repo-global runtime assumptions in every skill.

Use `requires_extra` only for exceptional requirements:

```yaml
requires_extra:
  binaries:
    - rclone
  python_packages:
    - marker-pdf
  platforms:
    only: ["linux"]
    reason: "Uses systemd user timers."
  env:
    - GOOGLE_CALENDAR_CREDENTIALS
```

If `requires_extra` is absent or empty, the skill uses the repo default runtime.

## Fields to remove or avoid

Do not add:

```yaml
mutation_policy:
llm_contract:
tests:
lifecycle:
```

Reasons:

- `mutation_policy` is misleading at skill level; effects are per-interface.
- `llm_contract` is not mechanically checkable enough.
- `tests` belong in test files and CI, not the skill contract.
- `lifecycle` is mostly handled by versioning. If deprecation is needed later, make it interface-local and mechanically enforce it.

## Schema changes

Update:

```text
references/blueprint/schema.json
```

Required root keys should become:

```json
[
  "blueprint_schema_version",
  "category",
  "version"
]
```

Root-level allowed fields:

```text
blueprint_schema_version
category
version
depends_on
owns_extra
requires_extra
suggested_permissions
skill_interface
script_interfaces
```

Remove or deprecate:

```text
interface_version
depends_on.<skill>.major_version
depends_on.<skill>.exports
```

Add:

```yaml
version:
  skill: string

depends_on:
  skills:
    <skill-name>: string
  interfaces:
    <skill-name>:
      <interface-id>: string
```

Update `scriptInterface` definition:

```text
required: ["id", "version", "command"]
```

Add `version` to:

```text
definitions.scriptInterface.properties
definitions.namedSubinterface.properties
```

For named subinterfaces:

```text
required: ["id", "version"]
```

Add definitions for:

```text
artifactContract
artifactSpec
effectSpec
ownsExtra
requiresExtra
```

Keep `additionalProperties: false` everywhere.

## Sync script changes

Update:

```text
skills/skill-maker/_rtx/_blueprint_syncer.py
```

Required behavior:

1. Read blueprint v2.
2. Generate `depends_on_skills` from `depends_on.skills` only.
3. Do not include `depends_on.interfaces` in `depends_on_skills`.
4. Generate the top `Dependencies:` block in `SKILL.md` from `depends_on.skills` only.
5. Generate the blueprint contract block with separate sections:

   ```markdown
   Skill dependencies:
   - proof-audit @ 3

   Interface dependencies:
   - cloud-files.lists-read @ 1.4
   - cloud-files.lists-write @ 1.5
   ```

6. Generated owner-facing interface block should include interface versions:

   ```markdown
   - `read-list` @ `3.2`
   ```

7. Preserve comments in `blueprint.yaml`; do not rewrite blueprint files.

## Dispatcher changes

Update the dispatcher package so runtime checks match blueprint v2.

Search and update code under:

```text
script_dispatcher/
```

Required runtime behavior:

1. Resolve callee interface id.
2. Resolve callee callable version:
   - default interface: `script_interfaces.<name>.version`
   - named subinterface: `script_interfaces.<name>.subinterfaces.<subname>.version`
3. If caller is the owning skill, allow owner-facing/default usage as before.
4. If caller is external:
   - caller blueprint must declare:

     ```yaml
     depends_on:
       interfaces:
         <callee-skill>:
           <interface-id>: "<callee-interface-version>"
     ```

   - declared version must exactly equal callee callable version.
   - access control must still pass:
     - `allow_all_skills: true` allows any declared interface dependency.
     - `allow_all_skills: false` requires caller in `allowed_callers`.
     - empty `allowed_callers` with `allow_all_skills: false` means internal-only.

5. Skill-level dependencies must not authorize dispatcher calls.
6. Interface-level dependencies must not authorize skill invocation.

## Validator changes

### `blueprints.py`

Update:

```text
skills/skill-maker/validators/blueprints.py
```

Add checks:

1. `blueprint_schema_version` must be `2`.
2. `version.skill` must match `^[1-9][0-9]*$`.
3. Every callable interface version must match `^[1-9][0-9]*\.[1-9][0-9]*$`.
4. Every callable interface version’s major part must equal `version.skill`.
5. If interface `description` exists, `usage` must exist as today.
6. Artifact/effect contracts, if present, satisfy local consistency rules.
7. No legacy root `interface_version`.

### `blueprint_relationships.py`

Update:

```text
skills/skill-maker/validators/blueprint_relationships.py
```

New relationship rules:

1. No skill depends on itself under either `depends_on.skills` or `depends_on.interfaces`.
2. Every `depends_on.skills.<dep>` must refer to an existing blueprint skill unless explicitly legacy-supported.
3. `depends_on.skills.<dep>` must equal callee `version.skill`.
4. Every `depends_on.interfaces.<dep>.<interface_id>` must resolve to a callable interface id in the callee blueprint.
5. The declared interface version must equal the resolved callee callable version.
6. Restricted interfaces still require caller in `allowed_callers`.
7. Internal-only interfaces may not be depended on externally.
8. A dependency may appear in both `skills` and `interfaces`, but each section is checked separately.
9. Empty `depends_on.skills` and `depends_on.interfaces` maps are allowed only if semantically empty; normalize missing to `{}`.

### `dependencies.py`

Update:

```text
skills/skill-maker/validators/dependencies.py
```

Current behavior compares exact skill-name mentions in hand-authored `SKILL.md` to `depends_on_skills`.

Change it to:

1. Load `depends_on.skills` from `blueprint.yaml`.
2. Ignore `depends_on.interfaces` entirely for body mention checks.
3. Generated `depends_on_skills` must equal `depends_on.skills`.
4. Exact skill-name mentions in hand-authored body must match `depends_on.skills`.
5. A hand-authored body mention of an interface-only callee should fail unless that callee is also listed under `depends_on.skills`.

This enforces the intended semantics:

```text
Body mention => skill dependency.
Dispatcher call => interface dependency.
```

### New or updated dispatcher dependency validator

Create a new validator if no existing one fits cleanly:

```text
skills/skill-maker/validators/interface_dependencies.py
```

It should scan statically checkable cross-skill dispatcher calls and verify they are declared in `depends_on.interfaces`.

Check at least:

1. Python calls to `script_dispatcher.dispatch(...)` where callee skill and interface id are string literals.
2. Shell calls to `dispatcher --caller-skill ... <callee> <interface> ...` where statically parseable.
3. Generated interface blocks if they include dispatcher references.

If a call is dynamic and cannot be statically resolved, require a narrow comment marker or explicit allowlist in blueprint, but avoid this initially if no current skill needs it.

### `interface_ids.py`

Update:

```text
skills/skill-maker/validators/interface_ids.py
```

Continue enforcing callable id uniqueness. Also enforce:

1. Every callable id has a version.
2. No version is accidentally parsed as numeric by YAML:
   - loaded value must be `str`, not `float` or `int`.
3. Named subinterfaces have versions.

### `skill_md_dispatch.py`

Update if necessary so generated interface blocks are still the only place where dispatcher invocation forms appear.

The hand-authored body should still reference local interface ids only, not raw dispatcher invocations or script paths.

## Migration strategy

Write a migration script:

```text
skills/skill-maker/scripts/migrate_blueprints_v1_to_v2.py
```

The script should transform each v1 blueprint as follows.

### Root version

For every migrated skill:

```yaml
blueprint_schema_version: 2
version:
  skill: "1"
```

Reason: there was no previous skill-body version. All existing skill contracts start at skill version 1.

### Interface versions

Old:

```yaml
interface_version: 1
```

New:

```yaml
script_interfaces:
  read-list:
    version: "1.1"
```

Rule:

```text
new interface version = "1.<old interface_version>"
```

Apply this to every default interface and named subinterface.

### Dependencies

Old skill-level dependency:

```yaml
depends_on:
  proof-audit:
    major_version: 1
```

New:

```yaml
depends_on:
  skills:
    proof-audit: "1"
```

Old interface-level dependency:

```yaml
depends_on:
  cloud-files:
    major_version: 1
    exports:
      - lists-read
      - lists-write
```

New:

```yaml
depends_on:
  interfaces:
    cloud-files:
      lists-read: "1.1"
      lists-write: "1.1"
```

Rule:

- If old dependency has a nonempty `exports` list, migrate it to `depends_on.interfaces`.
- If old dependency has no `exports`, migrate it to `depends_on.skills`.
- If a dependency needs both, the migration script cannot infer that automatically; after migration, manually add the skill dependency where the hand-authored body actually invokes the skill.

### Post-migration repair

After the automated migration:

1. Run the sync script.
2. Run validators.
3. For each failure where `SKILL.md` body mentions a skill that was migrated as interface-only, either:
   - add it under `depends_on.skills`, or
   - rewrite the hand-authored body to avoid invoking that skill and rely on interface names instead.
4. For each interface dependency with the wrong version, read the callee blueprint and set the exact callable version.

## Documentation updates

Update:

```text
docs/skill-blueprints.md
references/blueprint/template.yaml
references/blueprint/README.md
references/skill-standards/skill-guidelines.md
README.md
```

### Required docs language

Add this exact conceptual rule somewhere prominent:

```markdown
A skill dependency and an interface dependency are different contracts.

A skill dependency means the hand-authored `SKILL.md` body may invoke or refer to the callee as an LLM skill.

An interface dependency means this skill may call the listed callable interface ids through `dispatcher` or `script_dispatcher.dispatch()`. It does not imply skill-level invocation, and it does not require the hand-authored body to mention the callee skill.
```

Add versioning language:

```markdown
Skill versions are integer strings, such as `"3"`.

Interface versions are decimal strings, such as `"3.2"`. The major part must equal the owning skill’s skill version. Never write interface versions as YAML numbers; always quote them.
```

Clarify generated artifacts:

```markdown
`depends_on_skills` is generated only from `depends_on.skills`. Interface dependencies are shown in the generated blueprint contract block but are not listed as skill dependencies.
```

Fix any stale template path references if present. The canonical template should be:

```text
references/blueprint/template.yaml
```

## Test plan

Add or update tests under:

```text
skills/skill-maker/tests/
tests/
```

Required pass cases:

1. Skill dependency only:
   - `depends_on.skills.foo: "1"`
   - body mentions `foo`
   - no dispatcher calls
   - validators pass.

2. Interface dependency only:
   - `depends_on.interfaces.foo.bar: "1.1"`
   - body does not mention `foo`
   - script calls `foo bar`
   - validators pass.

3. Mixed dependency:
   - `foo` appears under both `skills` and `interfaces`
   - body mentions `foo`
   - script calls `foo.bar`
   - validators pass.

4. Interface version with two-digit minor:
   - interface version `"1.10"`
   - dependency uses `"1.10"`
   - YAML loads as string
   - validators pass.

5. Artifact contract pass:
   - positional artifact index is within pattern positional bounds
   - flag artifact flag appears in pattern
   - effects reference declared artifacts.

Required fail cases:

1. Body mentions a skill but `depends_on.skills` omits it.
2. `depends_on.skills` lists a skill but body never mentions it.
3. Script calls `foo.bar` but `depends_on.interfaces.foo.bar` is missing.
4. `depends_on.interfaces.foo.bar` declares wrong version.
5. Skill dependency exists but dispatcher call relies on it without interface dependency.
6. Interface dependency exists but body invokes the callee skill without skill dependency.
7. Interface version is unquoted and loads as a float.
8. Interface version major part differs from root `version.skill`.
9. Named subinterface lacks version.
10. Artifact effect references an undeclared artifact.
11. Artifact flag is not present in any pattern.

## Implementation order

1. Create a branch:

   ```bash
   git checkout -b blueprint-v2-dependencies
   ```

2. Update schema first:

   ```text
   references/blueprint/schema.json
   ```

3. Update Python blueprint loading helpers so validators can read both v1 and v2 temporarily.

4. Implement the migration script.

5. Run migration on all skills.

6. Update sync script.

7. Run sync script:

   ```bash
   python3 skills/skill-maker/_rtx/_blueprint_syncer.py
   ```

8. Update validators:
   - `blueprints.py`
   - `blueprint_relationships.py`
   - `dependencies.py`
   - `interface_ids.py`
   - dispatcher-related validators

9. Update dispatcher runtime checks.

10. Update docs:
    - blueprint guide
    - blueprint template
    - skill guidelines
    - README design section

11. Add tests.

12. Run:

    ```bash
    python3 validators/runner.py
    python3 -m pytest
    ```

13. Inspect generated diffs carefully.

14. Commit only after validators and tests pass.

## Non-goals for this migration

Do not implement:

1. Full semantic static analysis of natural-language skill invocation.
2. Global mutation policies.
3. LLM contracts in YAML.
4. Test declarations in YAML.
5. Broad lifecycle/deprecation system.
6. Runtime package management.
7. Automatic backward compatibility for old interface versions.

Keep blueprint v2 focused on mechanically checkable contracts:

```text
who may invoke which skill,
who may call which interface,
which version they depend on,
what crosses each interface boundary,
and what extra resources the skill owns or requires.
```

## Acceptance criteria

The migration is complete when:

1. Every local skill has `blueprint_schema_version: 2`.
2. No blueprint uses root `interface_version`.
3. No blueprint uses old `depends_on.<skill>.major_version`.
4. No blueprint uses old `depends_on.<skill>.exports`.
5. `depends_on.skills` and `depends_on.interfaces` are separate.
6. `depends_on_skills` is generated only from `depends_on.skills`.
7. Hand-authored `SKILL.md` body mentions match `depends_on.skills`.
8. Dispatcher calls require matching `depends_on.interfaces`.
9. Interface versions are quoted decimal strings like `"3.2"`.
10. Interface version major part equals root `version.skill`.
11. Validators and tests pass.
12. The docs explain the problem, the new model, and the exact dependency semantics.
