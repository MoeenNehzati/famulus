# Skill Smells

Visible symptoms that signal a skill needs refactoring. Each smell maps to one or more moves in `skill-refactoring-catalog.md`.

---

## Bloated SKILL.md
**Signal:** SKILL.md is long, covers multiple distinct responsibilities, or mixes orchestration with implementation detail.
**Analog:** God Class / Long Method.
**Moves:** Extract Sub-skill, Extract Script, Purge Dead Content.

## Executable logic in SKILL.md
**Signal:** Shell commands, Python snippets, or other runnable code appears inline in SKILL.md rather than in `scripts/`.
**Analog:** Wrong layer of abstraction.
**Moves:** Extract Script.

## Missing or incomplete `permissions.json`
**Signal:** `permissions.json` is absent, or scripts the skill calls are not listed in it.
**Analog:** Missing interface declaration.
**Moves:** Add/fix `permissions.json`.

## Duplicated guidelines
**Signal:** Conventions, rules, or reference content that already exist in another skill or `references/` file are copy-pasted here.
**Analog:** Duplicated Code.
**Moves:** Extract Reference, Inline to Reference.

## Mixed abstraction levels
**Signal:** SKILL.md both directs high-level orchestration ("invoke the lists skill") and describes low-level implementation ("the script reads line 3 of the file").
**Analog:** Mixed Levels of Abstraction.
**Moves:** Clarify Interface, Extract Script.

## Dead content
**Signal:** Motivational paragraphs, restatements of why the skill exists, "this is important because…" passages that add length without adding instruction.
**Analog:** Comments that restate the code.
**Moves:** Purge Dead Content.

## Undeclared interface
**Signal:** SKILL.md never states what inputs it expects or what outputs it produces, making it hard for other skills to depend on it cleanly.
**Analog:** Undocumented public API.
**Moves:** Clarify Interface.

## Wrong or missing Category
**Signal:** No `Category:` line, or the declared category doesn't match `references/skill-categories.md`.
**Moves:** Declare/fix Category.

## State in wrong location
**Signal:** Skill writes persistent data (logs, cache, watermarks) to `/tmp`, `~/.config`, or anywhere outside the skill's own directory.
**Analog:** Feature Envy / wrong module.
**Moves:** Relocate State.

## Credentials in skill directory
**Signal:** Passwords, tokens, or API keys are stored under the skill's own directory and may be committed to git.
**Moves:** Relocate Credentials.

## Monolithic script
**Signal:** A single script under `scripts/` handles multiple unrelated responsibilities, making it hard to invoke or test one part independently.
**Analog:** Long Method.
**Moves:** Decompose Script.

## God skill
**Signal:** The skill's trigger conditions cover several unrelated use cases that could each stand alone as independent skills.
**Analog:** God Class.
**Moves:** Extract Sub-skill.

## Thin skill
**Signal:** The skill adds almost no logic on top of a sub-skill it invokes — it exists only as a pass-through with no real added convention or behavior.
**Analog:** Middle Man.
**Moves:** Inline Thin Skill.

## Leaky internals
**Signal:** SKILL.md references another skill's scripts directly (e.g. `../lists/scripts/lists.sh`) instead of invoking the skill.
**Analog:** Inappropriate Intimacy.
**Moves:** Depend on Interface (replace script call with skill invocation).
