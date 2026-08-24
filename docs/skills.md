# Skill Index

> Generated from live blueprints and `SKILL.md` descriptions. Do not edit by hand.

This page is the complete skill inventory. For workflows and examples, start from the quickstarts, domain guides, or contributor docs linked from [README.md](../README.md).

## Personal Assistance

### Featured

- `daily-plan` — Generate today's plan from calendar, todos, and weather _(topics: planning, personal-organization; activated by: user request, skill workflow)_
- `email-client` — Read, search, and send email across configured accounts _(topics: communications, external-integrations; activated by: user request, skill workflow)_
- `email-triage` — Triage the inbox into todo and triage lists since the last run _(topics: communications, personal-organization; activated by: user request, skill workflow)_
- `get-weather` — Fetch weather for a location, day, or date range _(topics: planning, external-integrations; activated by: user request, skill workflow)_
- `list-manager` — Manage personal YAML lists in cloud storage _(topics: personal-organization, storage-and-sync; activated by: user request, skill workflow)_
- `online-calendar` — Read and modify Google Calendar via a local OAuth CLI _(topics: planning, personal-organization, external-integrations; activated by: user request, skill workflow)_
- `send-feedback` — Send feedback, report a problem, or describe a failed Famulus workflow to its maintainer _(topics: communications, assistant-assurance; activated by: user request, skill workflow)_
- `wrap-up` — Review the day, update plans and lists, and find handoff candidates via find-handoff-candidates _(topics: planning, personal-organization, session-management; activated by: user request, skill workflow)_

## Research

### Featured

- `bib-audit` — Audit a `.bib` file for validity, style, external metadata, and duplicates _(topics: scholarly-documents, research-writing; activated by: user request, skill workflow)_
- `formal-prose-review` — Polish grammar, tone, and concision in technical prose without touching the math _(topics: research-writing, scholarly-documents; activated by: user request, skill workflow)_
- `latex-workshop` — Compiling or troubleshooting a LaTeX document inside a VS Code project whose build is governed by LaTeX Workshop _(topics: scholarly-documents, research-writing; activated by: user request, skill workflow)_
- `math-dependency-graph` — Extract an assumptions-to-results dependency graph from a LaTeX document _(topics: mathematical-reasoning, visualization, scholarly-documents; activated by: user request, skill workflow)_
- `notation-review` — Review, simplify, or standardize mathematical notation _(topics: mathematical-reasoning, research-writing; activated by: user request, skill workflow)_
- `proof-audit` — Audit a proof for soundness, coherence, hidden assumptions, and redundancy _(topics: mathematical-reasoning, research-writing, scholarly-documents; activated by: user request, skill workflow)_
- `technical-flow-review` — For document-level review of technical structure, motivation, or reader flow _(topics: research-writing, scholarly-documents; activated by: user request, skill workflow)_
- `tool-applicability` — Check whether a theorem or framework achieves a target in the current setting _(topics: mathematical-reasoning; activated by: user request, skill workflow)_

### Listed

- `make-tex-docstring` — Create or standardize a TeX document-profile comment, or when a selected TeX task requires profile information that the document does not state clearly _(topics: scholarly-documents, research-writing; activated by: user request, skill workflow)_
- `pdf-to-markdown` — Convert a research-paper PDF into LLM-readable text _(topics: scholarly-documents; activated by: user request, skill workflow)_

## Software Development

### Featured

- `semantic-integration` — Integrating substantially diverged Git branches and merge or rebase is inadequate because it produces broad structural conflicts, or because mechanical application would place source changes into structures the target architecture has replaced and thereby lose their intent _(topics: repository-workflow; activated by: user request, skill workflow)_

### Listed

- `ci-debug` — GitHub Actions CI is red, matrix failures need isolated repair, or repeated full reruns make remote diagnosis inefficient _(topics: repository-workflow, task-automation, assistant-assurance; activated by: user request, skill workflow)_
- `git-workflow` — Branch-safety checks and commit hygiene for any repo _(topics: repository-workflow; activated by: user request, skill workflow)_
- `initialize-tdd` — Scaffold a staged, approval-gated TDD project _(topics: repository-workflow, assistant-assurance; activated by: user request)_

## Assistant Development

### Featured

- `refactor-node` — Refactor whole repository nodes or owned sub-scopes by gateway language _(topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; activated by: user request, skill workflow)_
- `skill-maker` — Author new skills that conform to the repo's skill-writing guideline _(topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; activated by: user request, skill workflow)_

### Listed

- `hook-maker` — Design cross-host assistant hooks with one purpose and per-host bindings _(topics: assistant-authoring, assistant-architecture; activated by: user request, skill workflow)_
- `regenerate-blueprints` — An existing skill blueprint needs regeneration, whether requested directly or required by another skill _(topics: assistant-authoring, assistant-architecture; activated by: user request, skill workflow)_
- `relocate-nodes` — Registered Officina nodes or their owned files must be moved while mechanically updating blueprint ownership, references, generated artifacts, and callers _(topics: assistant-architecture, repository-workflow, assistant-assurance; activated by: user request, skill workflow)_
- `skill-certifier` — Fresh certificates are requested for one or more Officina nodes _(topics: assistant-assurance, assistant-architecture; activated by: user request, skill workflow)_
- `skill-drift` — Whether Officina node certificates are current or stale, or asks for canonical node hashes _(topics: assistant-assurance, assistant-architecture; activated by: user request, skill workflow)_
- `update-standards` — Change canonical standards and keep their pinned closures aligned _(topics: assistant-authoring, assistant-architecture, assistant-assurance; activated by: user request, skill workflow)_

## Assistant Operations

### Featured

- `recurring-tasks` — Manage recurring AI jobs through the host's native per-user scheduler _(topics: task-automation, system-maintenance; activated by: user request, skill workflow, scheduled job)_

### Listed

- `cloud-files` — Bounded read/write of plain files under a configured Google Drive root _(topics: external-integrations, storage-and-sync; activated by: user request, skill workflow)_
- `connect-google` — The user needs to set up or restore Google authentication for Famulus _(topics: external-integrations; activated by: user request, skill workflow)_
- `install-assistant-tools` — Install or update launchers, wiring, hooks, and environment on a machine _(topics: assistant-installation, system-maintenance; activated by: user request)_
- `milestone-logging` — Starting or completing substantive agent work that needs durable, role-labelled progress records and optional run recovery _(topics: task-automation, assistant-assurance; activated by: user request, skill workflow)_
- `using-compass` — A user or another skill directs the agent to use a named compass _(topics: task-automation, session-management; activated by: user request, skill workflow)_

## Assistant Interaction

### Featured

- `loose-mode` — Broad, fast exploration mode with breadth over certainty _(topics: reasoning-control; activated by: user request; persistent modifier)_
- `tight-mode` — Rigorous, verified output mode with certainty over speed _(topics: reasoning-control; activated by: user request; persistent modifier)_

### Listed

- `llm-wakeup` — Schedule or manage an automatic assistant-session wakeup after a usage reset or timeout _(topics: session-management, task-automation; activated by: user request, skill workflow, scheduled job)_
- `prepare-handoff` — Prepare a clean handoff with workflow and documentation updates _(topics: session-management, repository-workflow; activated by: user request, skill workflow)_
