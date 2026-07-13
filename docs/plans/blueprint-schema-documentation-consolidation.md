# Blueprint Schema Documentation Consolidation Plan

Status: prototype draft created; not approved for implementation.

## Goal

Make the repo-level blueprint schema the single source of truth for blueprint
shape, field-level semantics, authoring guidance, red flags, and validator
coverage. Remove hand-maintained duplicate explanations from the guide,
template, and skill guidelines.

## Target End State

- `references/blueprint/schema.json` is canonical for blueprint fields.
- The schema begins with a short top-level orientation using JSON Schema fields
  such as `description` and `$comment`, replacing the useful high-level parts
  of the current guide.
- Field-level schema nodes carry the relevant documentation next to the actual
  constraints.
- `references/blueprint/template.yaml` is removed as a hand-maintained file.
- A shared Python generator emits a sample blueprint from the current schema.
- `references/blueprint/guide.md` is removed, unless a tiny index is still
  needed temporarily during migration.
- `references/skill-guidelines.md` says nothing substantive about blueprint
  fields or schema rules, but remains the repo-level skill-writing standard.
- `skills/skill-maker/SKILL.md` says to create the skill file according to
  `references/skill-guidelines.md` and create the proper blueprint from
  `references/blueprint/schema.json` plus the generated sample.

## Prototype Status

A first draft of the consolidated schema exists at:

```text
references/blueprint/schema.annotated-draft.json
```

This file is intentionally separate from the live schema. It does not replace:

- `references/blueprint/schema.json`
- `references/blueprint/template.yaml`
- `references/blueprint/guide.md`

Verified prototype properties:

- The draft parses as JSON and passes Draft 7 schema checks.
- If annotation-only keys (`description`, `$comment`, `examples`,
  `x-famulus`) are stripped, its validation structure matches the live
  `references/blueprint/schema.json`.
- It currently uses root-level `x-famulus.validation_rules` plus field-level
  `x-famulus.related_validation_rules`, not per-field validator lists.

Blind review result from four fresh subagents:

- The draft is useful as a consolidation prototype and maintainer reference.
- It is not yet safe as the sole authoring source.
- It should not replace the template or guide until a generator, generated
  author-facing views, and a meta-validator exist.

Implemented prototype support:

- `src/officina/common/blueprint_template.py` provides shared rendering
  functions:
  - `render_blueprint_template(schema)` creates a documented sample from schema
    examples/defaults/template hints.
  - `refresh_blueprint_documentation(schema, blueprint_yaml)` preserves existing
    YAML values while replacing generated schema documentation comments.
  - `render_blueprint_from_schema(schema, values)` is the common renderer behind
    both modes.
- Generated comments are intentionally disposable and marked with
  `@schema-doc path=...`. The renderer does not try to preserve arbitrary old
  comments.
- Generated documentation comments use typed tags such as `@summary`,
  `@status`, `@authoring`, `@red-flag`, and `@validator` so the YAML remains
  readable while the comment layer is still machine-parseable enough for future
  tooling.
- Blueprint values are user-owned; blueprint comments are schema-owned.
  Refreshing documentation intentionally discards all existing YAML comments.
- The renderer supports `doc_mode="full"` for teaching/template output and
  `doc_mode="compact"` for refreshing existing large blueprints. Compact mode
  keeps summaries, statuses, and validator pointers while omitting authoring
  guidance and red flags.
- Long single-line strings render as folded YAML scalars when this preserves the
  parsed value, avoiding very long generated lines in real blueprints.
- Subagent rechecks found two value-preservation bugs in the first folded-scalar
  implementation:
  - hyphenated strings such as `diff-fenced` could become `diff- fenced`;
  - dynamic-map keys such as `positional_patterns: {0: ...}` could be
    normalized from integer keys to string keys.
- These were fixed by proving folded-scalar output round-trips before using it
  and by preserving dynamic mapping keys during rendering. Focused tests now
  cover hyphenated strings, numeric dynamic keys, generated-template schema
  validation, and real-blueprint refresh preservation for representative
  existing skills.
- A new callable skill prototype exists at `skills/regenerate-blueprints/`.
  It exposes one machine interface, `regenerate-blueprint <skill-name>`, which
  writes `/tmp/<skill-name>_blueprint.yaml` without editing the source
  blueprint. The private runtime implementation lives under that skill's
  `_rtx/` directory and calls the shared renderer.
- The first implementation avoids a new `ruamel.yaml` dependency. It parses
  values with PyYAML and emits deterministic schema-ordered YAML with generated
  comments.
- Focused tests cover template generation, stale-doc refresh after schema
  documentation changes, extra-field preservation, and live-schema template
  parsing.
- Manual validation confirmed generated templates from both the live schema and
  the annotated draft parse as YAML and validate against their schemas.

## Ownership And Access Model

Keep blueprint-spec ownership in repo-level references:

```text
references/blueprint/schema.json
references/skill-guidelines.md
```

Rationale:

- The schema and skill guidelines are repo-wide policy surfaces.
- Multiple skills and repo validators need to reason about blueprint shape.
- A repo-level schema avoids making consumers treat `skill-maker` references as
  private internals.
- `skill-maker` still owns skill creation, blueprint sync, and skill-system
  validators, but it should consume the shared schema rather than own a private
  copy.

Move only the sample/template generation implementation into shared code so it
is easy for `skill-maker`, validators, tests, and future consumers to invoke.

Likely shared implementation:

```text
src/officina/common/blueprint_template.py
```

Likely thin entrypoints:

- a script or console-style wrapper for humans;
- a `skill-maker` machine interface that calls the shared generator;
- tests that call the shared Python API directly.

Known consumer pattern:

- `skill-audit` can continue to depend on `skill-maker.machine.sync-blueprints`
  for certification gates.
- If `skill-audit` or another skill only needs blueprint-schema information, it
  can either read the repo-level schema as policy data or call a future
  `skill-maker` interface. Direct reads of `references/blueprint/schema.json`
  are acceptable because the schema remains a public repo reference, not a
  private skill runtime file.

## Schema Annotation Convention

Use standard JSON Schema annotation fields wherever possible:

- `description`: user-facing meaning and requirement for the field.
- `$comment`: maintainer rationale, migration notes, and why the rule exists.
- `examples`: valid example values or fragments.
- `default`: only when omission is actually treated as that value by tooling or
  already documented schema behavior.

Use one repo-specific extension key for documentation that JSON Schema itself
does not interpret:

```json
"x-famulus": {
  "doc": {
    "authoring": [],
    "red_flags": []
  },
  "validation": {
    "schema": [],
    "validators": [],
    "human_review": []
  },
  "template": {
    "include": true,
    "example": null
  },
  "related_validation_rules": []
}
```

Meanings:

- `x-famulus.doc.authoring`: normative authoring guidance for this field.
- `x-famulus.doc.red_flags`: suspicious patterns that may be valid YAML but
  should trigger review.
- `x-famulus.validation.schema`: constraints enforced directly at this schema
  node.
- `x-famulus.related_validation_rules`: stable IDs from the root
  `x-famulus.validation_rules` registry that constrain this node.
- `x-famulus.template.include`: whether the generated sample should include the
  field.
- `x-famulus.template.example`: preferred sample value or fragment.

Root-level `x-famulus.validation_rules` is the canonical registry for
non-schema, generated-artifact, runtime-boundary, and human-review rules. Use
it for cross-field, cross-interface, cross-file, and validator-backed checks
that cannot be represented cleanly at one field node.

Each validation rule should include:

- `id`: stable kebab-case ID.
- `scope`: one of `file`, `field`, `cross-field`, `cross-interface`,
  `cross-file`, `generated-artifact`, `runtime-boundary`, or `human-review`.
- `summary`: one-sentence rule.
- `applies_to`: schema paths or repo-path globs.
- `enforced_by`: validator/generated-check/human-review pointers.
- `not_schema_reason`: why JSON Schema alone does not enforce it.

This root-registry design supersedes the earlier idea of putting full validator
path/function/test lists under each field. Field nodes should point to rule IDs
instead of repeating enforcement metadata.

## Generator Design

Add a shared Python generator, likely:

```text
src/officina/common/blueprint_template.py
```

Responsibilities:

- read `references/blueprint/schema.json`;
- follow `x-famulus.template` and `examples`;
- provide a Python API that returns a complete sample `blueprint.yaml` string;
- include concise comments derived from schema annotations;
- avoid adding hand-authored semantics not present in the schema.

Add a thin command wrapper only if useful for humans or hooks. Add a
`skill-maker` machine interface for normal skill-authoring workflows if the
generated sample should be available through dispatcher.

The generated sample replaces the current hand-maintained
`references/blueprint/template.yaml`.

The generator contract must be deterministic before this plan can replace the
template. Required details:

- `$ref` traversal rules.
- Required-object inclusion rules.
- Precedence among `x-famulus.template.example`, `examples`, `default`,
  `const`, `enum`, and synthesized placeholders.
- `oneOf` branch selection rules, including preferred modern branches and
  legacy fallbacks.
- Whether every `template.include: true` node must have an explicit
  `x-famulus.template.example`.

## Documentation Cleanup

After schema annotations exist:

1. Delete or shrink `references/blueprint/guide.md`.
2. Update `references/blueprint/README.md` to point to:
   - `references/blueprint/schema.json` as the canonical spec;
   - the shared generator or `skill-maker` generator interface for a sample
     blueprint;
   - validation and sync commands.
3. Remove blueprint field explanations from `references/skill-guidelines.md`.
4. Update contributor/scaffolding docs so they no longer say schema, template,
   guide, and guidelines must all be updated as peer sources.
5. Update `skills/skill-maker/SKILL.md` to say:
   - make the skill file according to `references/skill-guidelines.md`;
   - create the proper blueprint according to `references/blueprint/schema.json`
     and the generated sample.

## Validation And Drift Prevention

Add or update validators so the new convention stays true:

- annotation protocol shape is validated by a meta-validator or meta-schema;
- every `field_status` uses the approved vocabulary;
- every `related_validation_rules` ID resolves to a root validation rule;
- schema annotations are present on required public fields;
- validator references inside `x-famulus.validation_rules[].enforced_by` point
  to real files, and function names are checked where practical;
- docs do not reintroduce field-level blueprint rules outside the schema;
- no docs or skill bodies point at the old repo-level schema/template/guide as
  canonical;
- direct schema access points to the public repo reference
  `references/blueprint/schema.json`, not to generated samples or stale
  template paths;
- generated sample output is stable and checked in only if we decide to keep a
  generated artifact.

Keep generated artifacts marked clearly if any are checked in.

Known prototype blockers from blind review:

- `field_status` currently has a vocabulary mismatch: the protocol mentions
  `recommended`, while the draft uses `repo-recommended`.
- The root `validation_rules` registry is not self-validating.
- `route-smoke-covers-lazy-runtime-dependencies` overstates enforcement:
  current runtime supports route smoke, but no validator proves lazy import
  coverage.
- `runtime-dispatch-calls-are-declared` still has a known gap: current
  validators do not prove every `DispatchCall` target is declared in
  `uses_interfaces`.
- Dependency platform compatibility is enforced by the syncer but still needs
  an explicit registry rule and field pointer.
- `names.py` and `skill_metadata.py` are not represented if the registry is
  intended to be a full skill-system validator inventory.
- `dependencies.py` is under-described; it also checks parent-path and
  deprecated-marker rules.
- `uses_interfaces` namespace rules and the `direct_io.path_match: glob`
  mini-language need to be stated explicitly, not only hidden behind generic
  validator summaries.
- `interfaces.llm.default` backed by `SKILL.md` needs clearer machine-readable
  status: schema-required, repo-required, or generated-template requirement.
- Maintainer metadata may be too noisy for author-facing docs; generated views
  must filter it.

## Proposed Implementation Phases

1. Stabilize the annotation protocol in the draft schema:
   - root `validation_rules` registry;
   - field-level `related_validation_rules`;
   - `field_status` vocabulary;
   - generator metadata;
   - oneOf/legacy metadata.
2. Add a meta-validator for `x-famulus` annotations:
   - shape checks;
   - rule-ID resolution;
   - validator path/function/test existence;
   - required annotation coverage for public fields;
   - equality between stripped annotated draft and live `schema.json`.
3. Add missing or corrected validation rules from blind review:
   - dependency platform compatibility;
   - precise route-smoke status;
   - precise DispatchCall declaration gap;
   - names/metadata decision if the registry is a full inventory;
   - parent-path/deprecated marker checks from `dependencies.py`;
   - explicit `uses_interfaces` namespace rules;
   - explicit glob mini-language rules.
4. Define and implement the shared generator under `src/officina/common/` with
   tests against a small schema fixture plus the real schema.
5. Generate candidate author-facing views:
   - minimal valid blueprint;
   - recommended full blueprint;
   - field reference or guide;
   - maintainer validation matrix.
6. Compare generated views against current `template.yaml` and `guide.md`, then
   decide whether the generated views preserve enough information to replace
   the hand-maintained files.
7. Only after generated views and meta-validation pass, replace
   `template.yaml` usage in docs and tooling.
8. Delete or shrink `guide.md`.
9. Trim blueprint field rules out of `skill-guidelines.md` while keeping
   skill-level rules there.
10. Update `skill-maker/SKILL.md`.
11. Run blueprint sync, validators, and focused tests.

## Open Decisions

- Should the generated sample be checked in, or should the generator be the only
  source?
- Should `guide.md` be deleted entirely, or retained as a tiny README-style
  migration stub for one release?
- Should `x-famulus.template.example` be required on every field included in
  the sample, or can the generator synthesize obvious values from type/enum
  metadata?
- Should the generator have a checked-in CLI wrapper, or should it be exposed
  only through `skill-maker.machine.generate-blueprint-sample` and tests?
- Should `validation_rules` be blueprint-scoped only, or should it be a full
  inventory of skill-system validators?
- Should validator paths/functions live directly in the schema, or should the
  schema keep stable rule IDs while a generated maintainer matrix owns the
  churn-prone path/function/test details?
- Is `skill_interface` / `suggested_permissions` repo-required or
  repo-recommended?
- What is the preferred machine-readable status for legacy fields such as
  `interfaces.llm.*.file`?
