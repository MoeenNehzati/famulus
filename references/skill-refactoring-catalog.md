# Skill Refactoring Catalog

Ordered from safest to most structural. Apply safe moves first; verify behavior preserved after each move before proceeding to the next.

---

## Safe moves — apply first

### Purge Dead Content
Remove motivational paragraphs, restatements of purpose, and "why this matters" prose from SKILL.md.
**Preserve:** Every instruction that directs behavior. Only remove content that describes rather than directs.
**Risk:** None — removes words, changes no behavior.

### Tighten Description
Rewrite the YAML `description` field to state only trigger conditions ("Use when…"), not workflow steps.
**Preserve:** All existing trigger conditions — don't narrow the triggers, just remove process summary.
**Risk:** None if triggers are preserved.

### Declare/fix Category
Set or correct `category` in `blueprint.yaml`. Must be one of the typed enum values in `references/blueprint/schema.json`. See the taxonomy tree in `references/blueprint/guide.md`.
**Risk:** None.

### Sync generated contract artifacts
If the skill is blueprint-migrated, update `blueprint.yaml` and regenerate `depends_on_skills`, `permissions.json`, and the generated contract block in `SKILL.md`.
**Risk:** None — purely additive.

---

## Medium moves — apply after safe moves are done

### Clarify Interface
Add a brief section to SKILL.md stating: what inputs the skill expects (if any), what it produces, what side effects it has (files written, commands run). Use this to make the skill dependable by other skills.
**Preserve:** Actual behavior — this is documentation only.
**Risk:** Low.

### Extract Reference
Identify content that is repeated across multiple skills or that is reference material (tables, guidelines, schemas). Move it to top-level `references/<name>.md`. Replace inline content with a relative reference such as `@./../../references/<name>.md` from a skill directory.
**Preserve:** Content must be identical before and after — only the location changes.
**Verify:** Invoke the skill and confirm the reference content is loaded (visible in system-reminder Read call).
**Risk:** Low, but test the `@` include.

### Extract Script
Move any executable logic from SKILL.md into a new file under `scripts/`. Update SKILL.md to describe when to call the script and how to interpret its output. Add the script path to `permissions.json`.
**Preserve:** The script must implement exactly the same logic that was inline. SKILL.md instructions for invoking it must produce the same result.
**Risk:** Medium — logic moves, easy to introduce a subtle change.

### Relocate State
Move persistent data files from wrong locations (e.g. `/tmp`, `~/.config`) to the skill's own directory. Update any scripts that write or read those paths.
**Preserve:** Data format and content. Update all read/write paths atomically.
**Risk:** Medium — path changes can break scripts silently.

### Relocate Credentials
Move credentials from the skill directory to `~/.config/<name>/` (mode 600). Update scripts that reference them.
**Risk:** Medium — update all references, verify scripts still authenticate.

---

## Structural moves — apply last, one at a time

### Extract Sub-skill
Identify a coherent sub-responsibility within the skill that:
- Has its own clear trigger condition
- Could be invoked independently
- Would be reusable by other skills

Steps:
1. Write a characterization of the sub-responsibility (inputs, outputs, behavior).
2. Create the new skill directory and SKILL.md.
3. Move the relevant content (SKILL.md sections + scripts) to the new skill.
4. In the original skill, replace the moved content with a skill invocation.
5. Verify aggregate behavior is unchanged.

**Preserve:** The original skill's aggregate behavior must be identical — it now delegates to the sub-skill, but the user sees the same result.
**Risk:** High. Test carefully.

### Decompose Script
Split a monolithic script that handles multiple unrelated responsibilities into focused scripts, one per responsibility.

Steps:
1. Identify the distinct responsibilities in the script.
2. Create one new script per responsibility under `scripts/`.
3. Update SKILL.md to invoke them in the same order as the original script.
4. Add new scripts to `permissions.json`.
5. Delete the original script once all responsibilities are covered.

**Preserve:** Each responsibility must produce the same output as before. Run the original first, record outputs, then verify the decomposed scripts match.
**Risk:** High. Do one responsibility at a time.

### Inline Thin Skill
If a skill is a near-empty pass-through to a sub-skill with no real added logic, merge it into the calling skill.

Steps:
1. Identify all skills that invoke the thin skill.
2. Replace each invocation with a direct invocation of the underlying skill, plus any additions the thin skill provided.
3. Delete the thin skill directory.

**Preserve:** Callers must see the same behavior.
**Risk:** High — requires knowing all callers.

### Depend on Interface
If a skill calls another skill's scripts directly, replace the raw script call with either a proper skill invocation or a dispatcher call to the dependency's exported interface.

Steps:
1. Identify the raw script access and which skill owns it.
2. Understand the owning skill's exported interface and which invocation mode matches the current use.
3. Replace the raw script call in SKILL.md or scripts/ with either a skill invocation or a dispatcher call to the exported interface.
4. Verify output is equivalent.

**Preserve:** Output and side effects.
**Risk:** Medium — skill invocation may produce more/different output than the raw script.

---

## Ordering rules

1. Always characterize before starting.
2. Apply all safe moves first, verify, then medium, then structural.
3. Never apply two structural moves in the same pass without verifying between them.
4. If any move breaks behavior, revert immediately — don't patch forward.
