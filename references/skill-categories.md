# Skill Categories

Canonical list of skill categories and declaration rules.

## Taxonomy tree

```
assistant  (structural root — not a valid category value)
├── research-assistant
├── general-assistant  (structural)
│   ├── productivity-general-assistant
│   └── workflow-general-assistant
├── development-assistant  (structural)
│   ├── skill-making-development-assistant
│   └── coding-development-assistant
└── system-assistant
```

## Naming convention

Category names encode their position in the tree via postfix:
`workflow-general-assistant` ends with `-general-assistant`, so its parent is `general-assistant`.

The rule: strip the shortest prefix (up to the first `-`) that yields a known category — that is the immediate parent. The validator enforces this mechanically.

## Declaration format

Declare a single `category` value in `blueprint.yaml`.

- A skill may be placed at any node — leaf or intermediate.
- Use the most specific node that accurately describes the skill.
- Do not invent new category names without updating this file and the schema enum.

## Categories

### `research-assistant`

Skills that support academic and technical intellectual work: analysis, proof auditing,
writing review, and document preparation.

Typical skills: proof-audit, math-dependency-graph, notation-review, formal-prose-review,
latex-workshop, make-tex-docstring, bib-audit, technical-flow-review.

Implications:
- if applied to a `.tex` file, check whether a top-of-document profile comment exists;
  if not, use `make-tex-docstring` first
- prefer showing findings before proposing edits

---

### `general-assistant` *(structural)*

Parent of `productivity-general-assistant` and `workflow-general-assistant`.
Assign a skill here only if it genuinely spans both children.

---

### `productivity-general-assistant`

Skills that handle everyday personal management on behalf of the user.

Typical skills: email-client, email-triage, g-calendar, get-weather, list-manager.

---

### `workflow-general-assistant`

Skills that govern recurring session rituals — opening the day, closing the session,
or adjusting how the assistant operates. Conceptually a subset of `general-assistant`.

Typical skills: daily-plan, wrap-up, prepare-handoff, loose-mode, tight-mode,
tool-applicability.

Implications:
- these skills run as structured rituals, not ad-hoc responses
- prefer preserving session continuity and process invariants

---

### `development-assistant` *(structural)*

Parent of `skill-making-development-assistant` and `coding-development-assistant`.
Assign a skill here if it is general development tooling that spans both children
(e.g. git workflow used across both skill and code development).

---

### `skill-making-development-assistant`

Skills that build, validate, refactor, or maintain other skills and assistant
infrastructure.

Typical skills: my-writing-skills, refactor-skills, update-skill-guidelines,
install-assistant-tools.

Implications:
- documentation, validation, and handoff quality are part of the skill's behavior
- prefer preserving process invariants over adding execution behavior

---

### `coding-development-assistant`

Skills that assist with general software development work, independent of skill
development.

Typical skills: initialize-tdd.

---

### `system-assistant`

Skills that manage system-level concerns: file conversion, storage, scheduling,
and sync pipelines. Typically invoked by other skills rather than directly by the user.

Typical skills: cloud-files, fix-bisync, recurring-tasks, pdf-to-markdown.

Implications:
- inspect live configuration, logs, and state before proposing fixes
- distinguish diagnosis from state-changing repair actions
- do not apply preventive changes without user approval
- prefer showing repair commands unless the user explicitly asks to run them

## Notes

- Keep this list small and stable.
- Add a new category only when it carries real organizational or behavioral consequences.
- Categories describe what the skill is for, not how it is implemented.
