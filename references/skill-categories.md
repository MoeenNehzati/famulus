# Skill Categories

Use this file as the canonical list of local skill categories and the rules for declaring them.

## Declaration format

Declare categories near the top of a `SKILL.md` body using plain lines such as:

- `Category: document-oriented`
- `Category: mathematical-analysis`
- `Category: automation`

If a skill genuinely belongs to more than one category, use multiple `Category:` lines.
Do not invent ad hoc category names when an existing one already fits.

## Categories

### `document-oriented`

Use for skills that operate on documents, sections, or text with document-level context.

Typical examples:
- document flow review
- prose review
- notation review tied to document context
- docstring/profile creation

Implications:
- if applied to a `.tex` file, first check whether a suitable top-of-document profile comment exists
- if not, and the skill would benefit from document-profile information, use `make-tex-docstring` first

### `mathematical-analysis`

Use for skills that analyze mathematical arguments, tools, assumptions, or objectives without relying primarily on document structure.

Typical examples:
- tool applicability
- theorem applicability
- proof construction
- proof audit

Implications:
- no document-profile check is required by default

### `automation`

Use for skills that operate on local machine workflows, scheduled jobs, sync or backup pipelines, wrapper scripts, and system configuration behavior.

Typical examples:
- bisync and rclone failure diagnosis
- systemd user service troubleshooting
- cron or timer workflow repair
- local backup and sync wrapper hardening

Implications:
- inspect the live local configuration, logs, state files, and scripts before proposing fixes
- distinguish diagnosis from state-changing repair actions
- do not apply preventive changes without user approval
- prefer showing repair commands unless the user explicitly asks to run them

### `workflow`

Use for skills that primarily govern agent working style, repo process,
handoff discipline, or skill-authoring/refactoring conventions rather than a
document domain or machine-facing automation task.

Typical examples:
- mode-switching skills such as tight/loose mode
- skill-authoring and skill-refactoring guides
- repo workflow and knowledge-distillation procedures

Implications:
- prefer preserving process invariants and operator clarity over adding new execution behavior
- documentation, checks, and handoff quality are part of the skill's behavior

## Notes

- Keep this list small and stable.
- Add a new category only when it carries real behavioral or orchestration consequences.
- Categories are metadata for coordination and routing, not marketing labels.
