# Skill Index

> Generated from live blueprints and `SKILL.md` descriptions. Do not edit by hand.

This page is the complete skill inventory. For walkthroughs and examples, start from the user docs or contributor docs linked from [README.md](../README.md).

![Skill taxonomy](../graphs/skill-taxonomy.svg)

The graph gives a visual overview of the live skill set. The sections below are the complete text inventory.

## General Assistant

### Productivity

- `email-client` — Read, search, and send email across configured accounts
- `email-triage` — Triage the inbox into todo and triage lists since the last run
- `g-calendar` — Read and modify Google Calendar via a local OAuth CLI
- `get-weather` — Fetch weather for a location, day, or date range
- `list-manager` — Manage personal YAML lists in cloud storage

### Coordination

- `daily-plan` — Generate today's plan from calendar, todos, and weather
- `find-handoff-candidates` — You need a mechanical, non-interpretive scan of today's (or another day's) work sessions to find ones that had substantial activity but no completed handoff
- `loose-mode` — Broad, fast exploration mode with breadth over certainty
- `prepare-handoff` — Prepare a clean handoff with workflow and documentation updates
- `tight-mode` — Rigorous, verified output mode with certainty over speed
- `tool-applicability` — Check whether a theorem or framework achieves a target in the current setting
- `wrap-up` — Review the day, record completions, and capture follow-up items

## Research Assistant

### Skills

- `bib-audit` — Audit a `.bib` file for validity, style, external metadata, and duplicates
- `formal-prose-review` — Polish grammar, tone, and concision in technical prose without touching the math
- `latex-workshop` — Follow VS Code LaTeX Workshop build behavior for TeX/LaTeX documents
- `make-tex-docstring` — Create or propose a top-of-document TeX comment block that records the document profile and intended use
- `math-dependency-graph` — Extract an assumptions-to-results dependency graph from a LaTeX document
- `notation-review` — Audit and improve mathematical notation for lightness, unification, reuse across scopes, and semantic transparency
- `proof-audit` — Audit a proof for soundness, coherence, hidden assumptions, and redundancy
- `technical-flow-review` — Review flow, structure, motivation, and readability of a technical document

## System Assistant

### Skills

- `cloud-files` — Bounded read/write of plain files under a configured Google Drive root
- `fix-bisync` — Diagnose and repair rclone bisync failures
- `pdf-to-markdown` — Convert a research-paper PDF into LLM-readable text
- `recurring-tasks` — Manage AI-driven recurring jobs as systemd user timers with health checks

## Development Assistant

### Skill Making

- `hook-maker` — Design cross-host assistant hooks with one purpose and per-host bindings
- `install-assistant-tools` — Install or update launchers, wiring, hooks, and environment on a machine
- `refactor-skills` — Audit and refactor existing skills against local conventions
- `skill-drift` — Reading or checking the local audit state of Famulus skills
- `skill-maker` — Author new skills that conform to the repo's skill-writing guideline
- `update-skill-guidelines` — Change the skill-writing standard and its mechanical checks in lockstep

### Coding

- `initialize-tdd` — Scaffold a staged, approval-gated TDD project

### Development

- `git-workflow` — Branch-safety checks and commit hygiene for any repo
