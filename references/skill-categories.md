# Skill Categories

Use this file as the canonical list of skill categories and the rules for declaring them.

All categories follow the `X-assistant` convention — every skill is a type of assistant,
differentiated by the domain it assists with.

## Declaration format

Declare `category` in `blueprint.yaml`. A skill may belong to more than one category.

Do not invent ad hoc category names. If none of the existing categories fits, propose a new
one here before using it.

## Hierarchy

`workflow-assistant` is a subset of `general-assistant`. A workflow skill is a general
assistant skill that governs a recurring session ritual (opening the day, closing the session,
managing modes) rather than responding to an ad-hoc user request.

## Categories

### `research-assistant`

Skills that support academic and technical intellectual work: analysis, writing, review,
and document preparation.

Typical examples:
- proof and argument auditing
- notation and prose review
- LaTeX editing and docstring creation
- mathematical structure analysis
- document conversion and bibliography auditing

Implications:
- if applied to a `.tex` file, check whether a suitable top-of-document profile comment
  exists before proceeding; if not, use `make-tex-docstring` first
- prefer showing findings before proposing edits

---

### `general-assistant`

Skills that handle everyday personal management on behalf of the user.

Typical examples:
- email composition and inbox triage
- calendar management
- task list management
- weather and context lookups

Implications:
- `workflow-assistant` is a subset of this category; see below

---

### `workflow-assistant`

Skills that govern recurring session rituals — opening the day, closing the session,
or adjusting how the assistant operates. Conceptually a subset of `general-assistant`.

Typical examples:
- daily plan generation and wrap-up
- session handoff and continuity
- mode switching (tight/loose)
- tool-applicability assessment

Implications:
- these skills run as structured rituals, not ad-hoc responses
- prefer preserving session continuity and process invariants

---

### `skill-making-assistant`

Skills that build, validate, refactor, or maintain other skills and the assistant
infrastructure itself.

Typical examples:
- skill authoring and validation tooling
- skill refactoring and guideline updates
- git workflow for skill development
- assistant tool installation

Implications:
- documentation, validation, and handoff quality are part of the skill's behavior
- prefer preserving process invariants over adding new execution behavior

---

### `coding-assistant`

Skills that assist with general software development work, independent of skill development.

Typical examples:
- TDD initialization
- general code workflow tooling

---

### `system-assistant`

Skills that manage system-level concerns: storage, scheduling, and sync pipelines.
These are typically invoked by other skills rather than directly by the user.

Typical examples:
- cloud file storage
- systemd timer management
- sync daemon repair

Implications:
- inspect live configuration, logs, and state before proposing fixes
- distinguish diagnosis from state-changing repair actions
- do not apply preventive changes without user approval
- prefer showing repair commands unless the user explicitly asks to run them

## Notes

- Keep this list small and stable.
- Add a new category only when it carries real behavioral or organizational consequences.
- Categories describe what the skill is for — they are not marketing labels.
