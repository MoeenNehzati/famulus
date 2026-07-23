<!-- Generated from references/skill-standards/skill-refactoring.standard.yaml; do not edit. -->

# Skill Refactoring

Diagnose visible skill smells and apply behavior-preserving refactoring moves in risk order.

## Diagnostic signals

Visible symptoms that signal a skill needs refactoring.

### Bloated SKILL.md

#### Signal

SKILL.md is long, covers multiple distinct responsibilities, or mixes orchestration with implementation detail.

#### Analog

God Class / Long Method.

### Executable logic in SKILL.md

#### Signal

Shell commands, Python snippets, or other runnable code appears inline in SKILL.md rather than behind a blueprint interface and private `_rtx/` runtime file.

#### Analog

Wrong layer of abstraction.

### Missing or incomplete contract artifacts

#### Signal

A migrated skill is missing `blueprint.yaml`, its generated `SKILL.md` contract/interface blocks are stale, or repo-level blueprint manifests are out of sync.

#### Analog

Missing interface declaration.

### Duplicated guidelines

#### Signal

Conventions, rules, or reference content that already exist in another skill or `references/` file are copy-pasted here.

#### Analog

Duplicated Code.

### Mixed abstraction levels

#### Signal

SKILL.md both directs high-level orchestration ("invoke the lists skill") and describes low-level implementation ("the script reads line 3 of the file").

#### Analog

Mixed Levels of Abstraction.

### Dead content

#### Signal

Motivational paragraphs, restatements of why the skill exists, "this is important because…" passages that add length without adding instruction.

#### Analog

Comments that restate the code.

### Undeclared interface

#### Signal

SKILL.md never states what inputs it expects or what outputs it produces, making it hard for other skills to depend on it cleanly.

#### Analog

Undocumented public API.

### Wrong or missing Category

#### Signal

`category` missing from `blueprint.yaml`, or the value is not in the typed enum in `references/blueprint/schema.json`.

### State in wrong location

#### Signal

Skill writes persistent data (logs, cache, watermarks) to `/tmp`, `~/.config`, or anywhere outside the skill's own directory.

#### Analog

Feature Envy / wrong module.

### Credentials in skill directory

#### Signal

Passwords, tokens, or API keys are stored under the skill's own directory and may be committed to git.

### Monolithic script

#### Signal

A single private runtime file under `_rtx/` handles multiple unrelated responsibilities, making it hard to invoke or test one part independently.

#### Analog

Long Method.

### God skill

#### Signal

The skill's trigger conditions cover several unrelated use cases that could each stand alone as independent skills.

#### Analog

God Class.

### Thin skill

#### Signal

The skill adds almost no logic on top of a sub-skill it invokes — it exists only as a pass-through with no real added convention or behavior.

#### Analog

Middle Man.

### Leaky internals

#### Signal

SKILL.md or a script references another skill's raw script path directly, or bypasses the dispatcher and calls a non-exported invocation form.

#### Analog

Inappropriate Intimacy.

## Refactoring moves

Ordered from safest to most structural. Apply safe moves first and verify behavior after each move.

### Safe moves — apply first

#### Purge Dead Content

Remove motivational paragraphs, restatements of purpose, and “why this matters” prose from SKILL.md.

**Steps**

1. Remove content that describes rather than directs.

**Invariants**
- Every instruction that directs behavior must be preserved.

**Risk (low):** None — removes words and changes no behavior.

#### Tighten Description

Rewrite the YAML `description` field to state only trigger conditions (“Use when…”), not workflow steps.

**Steps**

1. Remove process summary from the description without narrowing its triggers.

**Invariants**
- All existing trigger conditions must be preserved.

**Risk (low):** None if triggers are preserved.

#### Declare/fix Category

Set or correct `category` in `blueprint.yaml`.

**Steps**

1. Use a typed enum value from `references/blueprint/schema.json`; see `docs/skill-blueprints.md` for the architecture overview.

**Risk (low):** None.

#### Sync generated contract artifacts

Update `blueprint.yaml` and regenerate the generated contract/interface blocks in `SKILL.md` plus repo-level blueprint manifests for a blueprint-migrated skill.

**Steps**

1. Regenerate all contract artifacts from the updated blueprint.

**Risk (low):** None — purely additive.

#### Add/fix blueprint

Create or correct `blueprint.yaml` for a migrated skill, preserving the source smell’s explicit unmatched remedy.

**Steps**

1. Create or correct `blueprint.yaml`, then synchronize its generated contract artifacts.

**Invariants**
- Existing contract artifacts remain complete and synchronized.

**Risk (low):** None when the blueprint records the existing contract without behavioral change.

### Medium moves — apply after safe moves are done

#### Clarify Interface

Add a brief SKILL.md section stating what inputs the skill expects (if any), what it produces, and what side effects it has (files written, commands run). Use this to make the skill dependable by other skills.

**Steps**

1. Document inputs, outputs, and side effects so other skills can depend on the interface.

**Invariants**
- Actual behavior must remain unchanged; this is documentation only.

**Risk (low):** Low.

#### Extract Reference

Identify repeated content or reference material and move it to top-level `references/<name>.md`.

**Steps**

1. Move repeated content, tables, guidelines, or schemas to a top-level reference file.

2. Use an `@` include only when every instruction route through the source-owned interface needs the reference; otherwise name the file and its observable read condition in the selected route.
   - Requires: move-reference

3. Exercise unconditional or conditional loading as applicable.
   - Verification: For an unconditional include, invoke the skill and confirm the reference content is loaded. For conditional routing, exercise both paths and confirm the file is loaded only on the route that needs it.
   - Requires: choose-loading-route

**Invariants**
- Content must be identical before and after; only the location changes.

**Risk (low):** Low, but test the loading behavior.

#### Inline to Reference

Replace duplicated local text with use of the existing canonical reference, preserving the source smell’s explicit unmatched remedy.

**Steps**

1. Replace duplicated local text with an appropriate unconditional include or conditional reference route.
   - Verification: Exercise the affected route and confirm the canonical reference is loaded when needed.

**Invariants**
- The reference content remains available on every route that previously carried the duplicated text.

**Risk (low):** Low, but incorrect routing can hide required guidance.

#### Extract Script

Move any executable logic from SKILL.md into a new private runtime file under `_rtx/`.

**Steps**

1. Move executable logic into a private runtime file.

2. Update SKILL.md to describe the public interface and output interpretation; add the interface to `blueprint.yaml` and generated permissions as appropriate.
   - Verification: Invoke the interface and confirm it produces the same result.
   - Requires: move-logic

**Invariants**
- The script must implement exactly the same logic that was inline; SKILL.md instructions for invoking it must produce the same result.

**Risk (medium):** Medium — logic moves and a subtle change is easy to introduce.

#### Relocate State

Move persistent data files from wrong locations such as `/tmp` or `~/.config` to the skill's own directory.

**Steps**

1. Move the files and update all scripts that read or write those paths atomically.

**Invariants**
- Data format and content must be preserved.

**Risk (medium):** Medium — path changes can break scripts silently.

#### Relocate Credentials

Move credentials from the skill directory to `~/.config/<name>/` with mode 600.

**Steps**

1. Move credentials, update all references, and verify scripts still authenticate.

**Risk (medium):** Medium — every reference must be updated.

#### Depend on Interface

Replace direct access to another skill's private runtime files with its exported interface.

**Steps**

1. Identify the raw script access and the skill that owns it.

2. Understand the exported interface and select the matching skill invocation or dispatcher mode.
   - Requires: identify-owner

3. Replace the raw file call in SKILL.md or `_rtx/` with the selected exported invocation.
   - Requires: select-interface

4. Verify output is equivalent.
   - Requires: replace-call

**Invariants**
- Output and side effects must be preserved.

**Risk (medium):** Medium — skill invocation may produce more or different output than the raw script.

### Structural moves — apply last, one at a time

#### Extract Sub-skill

Identify a coherent sub-responsibility with its own trigger that can be invoked independently and reused.

**Steps**

1. Write a characterization of the sub-responsibility's inputs, outputs, and behavior.

2. Create the new skill directory and SKILL.md.
   - Requires: characterize

3. Move the relevant SKILL.md sections and private runtime files.
   - Requires: create-skill

4. Replace moved content in the original skill with a skill invocation.
   - Requires: move-content

5. Verify aggregate behavior is unchanged.
   - Requires: delegate

**Invariants**
- The original skill's aggregate behavior must be identical; delegation must not change the user's result.

**Risk (high):** High. Test carefully.

#### Decompose Script

Split a monolithic script that handles multiple unrelated responsibilities into focused scripts, one per responsibility.

**Steps**

1. Identify the distinct responsibilities in the script.

2. Create one private `_rtx/` runtime file per responsibility.
   - Requires: identify-responsibilities

3. Update SKILL.md to invoke the corresponding interfaces in the original order.
   - Requires: create-files

4. Add new interfaces to `blueprint.yaml` and generated permissions as appropriate.
   - Requires: update-invocations

5. Delete the original script once all responsibilities are covered.
   - Verification: Run the original first, record outputs, then verify the decomposed scripts match.
   - Requires: add-interfaces

**Invariants**
- Each responsibility must produce the same output as before.

**Risk (high):** High. Do one responsibility at a time.

#### Inline Thin Skill

If a skill is a near-empty pass-through with no real added logic, merge it into the calling skill.

**Steps**

1. Identify all skills that invoke the thin skill.

2. Replace each invocation with a direct invocation of the underlying skill plus any additions the thin skill provided.
   - Requires: find-callers

3. Delete the thin skill directory.
   - Requires: replace-invocations

**Invariants**
- Callers must see the same behavior.

**Risk (high):** High — requires knowing all callers.

## Ordering rules
- **required** — Always characterize before starting.
- **required** — Apply all safe moves first, verify, then medium, then structural.
- **prohibited** — Never apply two structural moves in the same pass without verifying between them.
- **required** — If any move breaks behavior, revert immediately — don't patch forward.

## Remedy relationships
- Bloated SKILL.md
  - Remedies: Extract Script
  - Remedies: Extract Sub-skill
  - Remedies: Purge Dead Content
- Credentials in skill directory
  - Remedies: Relocate Credentials
- Dead content
  - Remedies: Purge Dead Content
- Duplicated guidelines
  - Remedies: Extract Reference
  - Remedies: Inline to Reference
- Executable logic in SKILL.md
  - Remedies: Extract Script
- God skill
  - Remedies: Extract Sub-skill
- Leaky internals
  - Remedies: Depend on Interface
- Missing or incomplete contract artifacts
  - Remedies: Add/fix blueprint
  - Remedies: Sync generated contract artifacts
- Mixed abstraction levels
  - Remedies: Clarify Interface
  - Remedies: Extract Script
- Monolithic script
  - Remedies: Decompose Script
- State in wrong location
  - Remedies: Relocate State
- Thin skill
  - Remedies: Inline Thin Skill
- Undeclared interface
  - Remedies: Clarify Interface
- Wrong or missing Category
  - Remedies: Declare/fix Category
- Add/fix blueprint
  - Addresses: Missing or incomplete contract artifacts
- Clarify Interface
  - Addresses: Mixed abstraction levels
  - Addresses: Undeclared interface
- Declare/fix Category
  - Addresses: Wrong or missing Category
- Decompose Script
  - Addresses: Monolithic script
- Depend on Interface
  - Addresses: Leaky internals
- Extract Reference
  - Addresses: Duplicated guidelines
- Extract Script
  - Addresses: Bloated SKILL.md
  - Addresses: Executable logic in SKILL.md
  - Addresses: Mixed abstraction levels
- Extract Sub-skill
  - Addresses: Bloated SKILL.md
  - Addresses: God skill
- Inline Thin Skill
  - Addresses: Thin skill
- Inline to Reference
  - Addresses: Duplicated guidelines
- Purge Dead Content
  - Addresses: Bloated SKILL.md
  - Addresses: Dead content
- Relocate Credentials
  - Addresses: Credentials in skill directory
- Relocate State
  - Addresses: State in wrong location
- Sync generated contract artifacts
  - Addresses: Missing or incomplete contract artifacts
