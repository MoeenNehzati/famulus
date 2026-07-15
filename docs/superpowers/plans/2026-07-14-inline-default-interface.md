# Inline Default LLM Interface Implementation Plan

**Goal:** Make a skill's canonical default LLM interface an inline contract in `blueprint.yaml`, implicitly bound to `SKILL.md`, without a separate sidecar or health identity.

**Compatibility:** Continue loading existing typed skills whose default interface is declared through `.SKILL.md.blueprint.yaml`. Do not migrate those skills in this change. Apply the inline representation only to `connect-google`.

**Out of scope:** Bulk blueprint migration, generated graph artifacts, and changes to unrelated skill behavior.

## Task 1: Define and normalize the inline contract

- Add failing schema and graph-loader tests for an inline `default_interface`.
- Add a schema for the inline contract fields shared with an LLM interface, excluding the separately implied ID and `SKILL.md` binding.
- Update the skill-root schema to accept exactly one representation: inline default or legacy default sidecar.
- Make the graph loader synthesize the logical `<skill>.llm.default` node and its edges from the inline contract while preserving sidecar loading.
- Verify both representations expand to the existing legacy/generated interface view.

## Task 2: Fold inline-default health into skill health

- Add failing health tests proving an inline default has no separate health record or health path.
- Hash `blueprint.yaml`, `SKILL.md`, and the inline default contract as part of the root skill node.
- Fold the inline default's downstream dependencies into root health so dependency changes still invalidate the skill.
- Keep health behavior unchanged for legacy sidecar defaults and all other subordinate nodes.

## Task 3: Align standards, validation, and synchronization

- Update the skill-writing guideline, blueprint documentation, schema metadata, and template to define the inline default as canonical and the sidecar form as compatibility-only.
- Update blueprint validation and synchronization logic to consume the normalized graph rather than requiring a physical default sidecar.
- Check all skill Git hooks; change only hooks whose enforcement text or behavior is no longer accurate.
- Add regression tests for validation and generated `SKILL.md` interface blocks.

## Task 4: Apply the model to `connect-google`

- Add `skills/connect-google/blueprint.yaml` with its default interface inline.
- Add sidecars only for additional named LLM interfaces, machine interfaces, and behavior sources.
- Do not add `.SKILL.md.blueprint.yaml`.
- Update the routing tests to assert the default is inline and delegates to the named setup interfaces.

## Task 5: Verify scope and behavior

- Run focused schema, graph, health, validator, sync, and `connect-google` tests.
- Run the repository validator suite and relevant pre-commit checks.
- Confirm the diff contains no bulk skill migration or generated graph files.
