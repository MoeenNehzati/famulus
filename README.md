# Famulus

Famulus is a personal assistant skillset for Claude Code and Codex. It includes
skills for day-to-day assistance, research and writing, local workflow, recurring
automation, and skill development. The repo also contains the installer, agent
profiles, hooks, dispatcher, and validators that make the same skillset usable in
both hosts.

## Overview

What is included:

- **General assistant skills:** weather, Google Calendar, email, cloud-backed
  lists, email triage, daily planning, and end-of-day wrap-up.
- **Research and writing skills:** proof audit, notation review, theorem/tool
  applicability checks, bibliography audit, LaTeX Workshop builds, PDF-to-text
  conversion, mathematical dependency graphs, technical-flow review, and formal
  prose review.
- **Workflow skills:** git branch/commit hygiene, loose/tight operating modes,
  handoff preparation, and session wrap-up.
- **System and automation skills:** Google Drive-backed file storage,
  recurring jobs through systemd user timers, and rclone bisync diagnostics.
- **Skill-development tools:** skill authoring, skill refactoring, guideline
  updates, hook creation, blueprint contracts, dispatcher enforcement, and
  pre-commit validators.
- **Installed command-line tools:** the workstation installer installs
  `assistant`, `collab`, `coauthor`, and `tw`/`tmux-workspace` launchers, plus a
  generated `dispatcher` launcher used for approved skill-to-skill script calls.

The repo is meant to be usable in two ways:

1. **Plugin mode:** install the skillset into Claude Code or Codex as a plugin.
   Skills are namespaced, for example `famulus:proof-audit`.
2. **Workstation mode:** run `install-assistant-tools` to wire one checkout into
   both hosts, install launchers, register hooks, configure shell environment,
   and set up optional Google Drive / Calendar OAuth.

### Runtime map

The diagrams below are Mermaid diagrams. Their source files live under
`graphs/` and are synced into this README with
`python3 graphs/sync-readme-graphs.py`.

<!-- BEGIN GRAPH: graphs/runtime-map.mmd -->
```mermaid
flowchart LR
  Repo[Repo checkout]

  subgraph Plugin[Plugin mode]
    PluginCache[Host plugin cache]
    Namespaced[Namespaced skills]
  end

  subgraph Workstation[Workstation mode]
    Installer[install-assistant-tools]
    Homes[Claude and Codex homes]
    Launchers[assistant / collab / coauthor / tw]
    Profiles[profile config copies]
    Hooks[session hooks]
    Dispatcher[generated dispatcher]
    Workers[worker directories]
  end

  Repo --> PluginCache --> Namespaced
  Repo --> Installer
  Installer --> Homes
  Installer --> Launchers
  Installer --> Profiles
  Installer --> Hooks
  Installer --> Dispatcher
  Installer --> Workers
```
<!-- END GRAPH: graphs/runtime-map.mmd -->

## Install, update, and uninstall

Requirements:

- Python 3.10+ with PyYAML for the repo tooling.
- Git.
- At least one host CLI: Claude Code or Codex.
- Optional, depending on skills used: Google Drive OAuth, Google Calendar OAuth,
  email IMAP/SMTP credentials, `systemd --user`, tmux, rclone, LaTeX tools.

### Option A: install as a plugin

Claude Code:

```text
/plugin marketplace add MoeenNehzati/famulus
/plugin install famulus@nullkit
```

Update:

```text
/plugin update famulus@nullkit
```

Uninstall:

```text
/plugin uninstall famulus@nullkit
```

Codex, using a marketplace snapshot:

```bash
codex plugin marketplace add <marketplace-path-or-url> --json
codex plugin add famulus@<marketplace-name> --json
```

Uninstall through the Codex plugin manager:

```bash
codex plugin remove famulus@<marketplace-name> --json
codex plugin marketplace remove <marketplace-name> --json
```

Plugin installs copy the repo into the host's plugin cache. They do not wire the
local checkout into `~/.claude` or `~/.codex`, and they do not install shell
launchers.

### Option B: install the workstation tools

Use this when you want one local checkout to back both Claude Code and Codex and
when you want the launchers, hooks, profiles, and recurring-task support.

From an environment where the dispatcher is already available, preview the
installer with:

```bash
dispatcher --caller-skill install-assistant-tools install-assistant-tools scripts-install --dry-run
```

On a fresh checkout where the dispatcher is not yet installed, run the same
installer entry point directly:

```bash
python3 skills/install-assistant-tools/scripts/install.py --dry-run
python3 skills/install-assistant-tools/scripts/install.py
```

The installer does the following:

- wires `~/.claude` and `~/.codex` content back to this checkout;
- links shared skills, references, agents, and host context files;
- copies profile config files rather than symlinking them, because hosts may
  write machine-local state into those files;
- installs `assistant`, `collab`, `coauthor`, and `tw`/`tmux-workspace` into a
  bin directory on `PATH`;
- generates a `dispatcher` launcher that runs the repo dispatcher from `$AI`;
- creates worker directories for the installed agents;
- writes a managed shell rc block, or user environment entries on Windows;
- registers session hooks for Claude Code and Codex;
- configures git hooks for the checkout;
- writes the cloud-files config and optionally walks through Google Drive and
  Google Calendar OAuth setup.

Useful installer flags include `--dry-run`, `--no-claude`, `--no-codex`,
`--bin-dir`, `--shell-rc`, `--default-llm claude|codex`, `--home`,
`--claude-home`, and `--codex-home`.

To update a workstation install, pull the repo and rerun the installer. Installed
launchers are symlinks into the checkout, but rerunning the installer refreshes
hooks, profile copies, generated launchers, recurring-task environment files,
and shell setup.

### Workstation uninstall

The uninstall script reverses managed install side effects and prints a
removed/skipped/left/FAILED report:

```bash
python3 skills/install-assistant-tools/scripts/uninstall.py --dry-run
python3 skills/install-assistant-tools/scripts/uninstall.py
```

It removes the managed Codex and Claude hook registrations, unlinks managed
launchers and symlinks, and uses the install manifest when available. OAuth
credentials are left in place unless `--purge` is passed.

## What is included

### Daily assistant loop

Several productivity skills are designed to work together.

- `cloud-files` owns bounded Google Drive transport under the configured LLM
  root. Other skills use it instead of speaking to Drive directly.
- `list-manager` stores structured lists through `cloud-files`, including todo
  and triage lists.
- `email-client` reads and sends mail through configured IMAP/SMTP accounts.
- `email-triage` reads new mail through `email-client`, classifies possible
  actions, and writes concrete actions to `todo` or optional items to `triage`
  through `list-manager`.
- `g-calendar` reads and edits Google Calendar through a local OAuth-backed CLI.
- `get-weather` fetches forecast data for a location and date range.
- `daily-plan` combines calendar, weather, todo, and triage items into a plan
  stored through `cloud-files`.
- `wrap-up` reads the day's plan, asks what was completed, records unplanned
  work, and updates the plan and lists.

The intended loop is: triage the inbox, keep cloud lists current, generate or
refresh the daily plan, work from that plan, then run wrap-up at the end of the
day. `recurring-tasks` can keep the same loop warm in the background: it manages
AI jobs as systemd user timers, can run `email-triage` and `daily-plan` on a
schedule, and provides sync, enable/disable, status, log, and healthcheck
operations. This scheduled layer is Linux/systemd-specific; the rest of the
repo's framework is tested cross-platform.

<!-- BEGIN GRAPH: graphs/daily-assistant-loop.mmd -->
```mermaid
flowchart LR
  subgraph Remote[Remote data sources]
    Mail[Email accounts]
    Calendar[Google Calendar]
    Weather[Weather API]
    Drive[Google Drive LLM root]
  end

  subgraph ListsPlans[Cloud-backed state]
    CloudFiles[cloud-files]
    Lists[list-manager: todo and triage]
    Plan[stored daily plan]
  end

  subgraph DailyLoop[Daily assistant loop]
    EmailClient[email-client]
    EmailTriage[email-triage]
    GCal[g-calendar]
    GetWeather[get-weather]
    DailyPlan[daily-plan]
    WrapUp[wrap-up]
  end

  subgraph Scheduled[Scheduled automation]
    Recurring[recurring-tasks]
    Systemd[systemd user timers]
    Logs[logs and healthcheck]
  end

  Mail --> EmailClient --> EmailTriage
  EmailTriage --> Lists
  Calendar --> GCal --> DailyPlan
  Weather --> GetWeather --> DailyPlan
  Drive --> CloudFiles --> Lists
  CloudFiles --> Plan
  Lists --> DailyPlan --> Plan
  Plan --> WrapUp --> Lists
  WrapUp --> Plan
  Recurring --> Systemd
  Systemd --> EmailTriage
  Systemd --> DailyPlan
  Systemd --> Logs
```
<!-- END GRAPH: graphs/daily-assistant-loop.mmd -->

### Research and writing

The research skills are mostly independent tools rather than one tightly coupled
workflow:

<!-- BEGIN GRAPH: graphs/research-writing.mmd -->
```mermaid
flowchart TB
  subgraph Inputs[Research artifacts]
    Tex[TeX project]
    Pdf[Research PDF]
    Bib[BibTeX file]
    ProofText[proof or theorem text]
    Draft[paper draft]
  end

  subgraph Structural[Structural tools]
    Latex[latex-workshop]
    DepGraph[math-dependency-graph]
    TexDoc[make-tex-docstring]
    PdfMd[pdf-to-markdown]
    BibAudit[bib-audit]
  end

  subgraph Mathier[Math-focused review]
    ProofAudit[proof-audit]
    ToolApp[tool-applicability]
    Notation[notation-review]
  end

  subgraph Text[Text and presentation review]
    Flow[technical-flow-review]
    Prose[formal-prose-review]
  end

  Tex --> Latex
  Tex --> DepGraph
  Tex --> TexDoc
  Pdf --> PdfMd
  Bib --> BibAudit
  ProofText --> ProofAudit
  ProofText --> ToolApp
  ProofText --> Notation
  Draft --> Flow
  Draft --> Prose
  PdfMd --> ProofAudit
  PdfMd --> Flow
```
<!-- END GRAPH: graphs/research-writing.mmd -->

- `proof-audit` checks mathematical arguments for gaps, hidden assumptions,
  invalid theorem use, and redundancy.
- `tool-applicability` checks whether a theorem, framework, or method actually
  applies in the current setting.
- `notation-review` looks for heavy, inconsistent, or misleading notation and
  proposes a lighter scheme.
- `math-dependency-graph` extracts assumptions, definitions, lemmas, theorems,
  and direct dependency edges from a LaTeX math document.
- `latex-workshop` follows VS Code LaTeX Workshop settings when compiling or
  troubleshooting TeX documents.
- `bib-audit` checks `.bib` files for syntax, style, duplicates, and external
  metadata mismatches.
- `technical-flow-review` reviews document structure, motivation, section
  order, and readability.
- `formal-prose-review` polishes technical prose without changing the math.
- `pdf-to-markdown` converts research PDFs into LLM-readable text.
- `make-tex-docstring` records a TeX document's profile and intended use in a
  top-of-file comment block.

### Skill development framework

The skill-development part of the repo is about keeping skills small and their
coupling explicit.

<!-- BEGIN GRAPH: graphs/skill-development-framework.mmd -->
```mermaid
flowchart LR
  Guidelines[references/skill-guidelines.md]
  Blueprint[skill blueprint.yaml]
  Sync[blueprint sync tooling]
  Generated[generated SKILL.md blocks and permissions]
  Dispatcher[dispatcher]
  Validators[validators]
  PreCommit[pre-commit hook]
  CI[CI checks]

  SkillMaker[skill-maker]
  Refactor[refactor-skills]
  UpdateGuidelines[update-skill-guidelines]
  HookMaker[hook-maker]
  Hooks[llmhooks and plugin hook glue]

  Guidelines --> SkillMaker
  Guidelines --> Refactor
  Guidelines --> UpdateGuidelines
  UpdateGuidelines --> Validators
  SkillMaker --> Blueprint
  Refactor --> Blueprint
  Blueprint --> Sync --> Generated
  Blueprint --> Dispatcher
  Generated --> Validators
  Dispatcher --> Validators
  Validators --> PreCommit --> CI
  HookMaker --> Hooks
  Hooks --> Validators
```
<!-- END GRAPH: graphs/skill-development-framework.mmd -->

- Each skill has a `blueprint.yaml` contract: category, dependencies, interface
  version, and exported script interfaces.
- Generated blocks in `SKILL.md` expose the relevant contract to the assistant.
- The dispatcher is the only approved route for one skill to call another
  skill's script interface:

  ```bash
  dispatcher --caller-skill <caller> <callee> <interface-id> [args...]
  ```

- Validators reject undeclared dependencies, direct cross-skill script access,
  stale generated artifacts, invalid blueprint structure, host-specific content
  in shared files, and metadata drift.
- `skill-maker` creates or edits skills against the shared guideline.
- `refactor-skills` audits existing skills against that guideline.
- `update-skill-guidelines` keeps guideline edits and validator behavior in
  sync.
- `hook-maker` is for cross-host session hooks: one semantic hook purpose, with
  host-specific bindings handled by the hook framework.

Relevant repo references:

- `references/skill-guidelines.md` — skill-writing standard.
- `references/blueprint/guide.md` and `references/blueprint/template.yaml` —
  blueprint contract reference.
- `script_dispatcher/` — dispatcher implementation.
- `validators/` and `skills/skill-maker/validators/` — commit-time checks.
- `llmhooks/` — host-neutral hook implementations.
- `hooks/` — plugin-mode hook glue.

### Agents and launchers

The installer provides three agent launchers:

- `assistant` — day-to-day personal assistant work.
- `collab` — long project sessions with continuity and handoff behavior.
- `coauthor` — writing-focused sessions.

Each has Claude and Codex profile/config files under `profiles/`, and each gets
a worker directory under `workers/`. `PROFILES.md` is generated from those
profile files and summarizes the differences.

### Full skill list

The table below is generated from `skills/*/blueprint.yaml` and each skill's
`SKILL.md` description by `scripts/generate-skills-table.py`. The pre-commit
hook regenerates it when sources change.

<!-- BEGIN SKILLS TABLE (generated by scripts/generate-skills-table.py) -->
### Productivity

| Skill | What it does |
|---|---|
| `email-client` | Read, search, and send email across configured accounts |
| `email-triage` | Triage the inbox into todo and potential-action lists since the last run |
| `g-calendar` | Read and modify Google Calendar via a local OAuth CLI |
| `get-weather` | Fetch weather for a location, day, or date range |
| `list-manager` | Manage personal YAML lists (todo, shopping, reading, …) in cloud storage |

### Workflow

| Skill | What it does |
|---|---|
| `daily-plan` | Generate today's plan from calendar, todos, and weather |
| `loose-mode` | Broad, fast exploration mode — breadth over certainty |
| `prepare-handoff` | Prepare a clean handoff: workflow updates, doc updates, residual lessons |
| `tight-mode` | Rigorous, verified output mode — certainty over speed |
| `tool-applicability` | Check whether a theorem or framework achieves a target in the current setting |
| `wrap-up` | End-of-day wrap-up: review the plan, record completions, capture new items |

### Research & Writing

| Skill | What it does |
|---|---|
| `bib-audit` | Audit a `.bib` file for validity, style, external metadata, and duplicates |
| `formal-prose-review` | Polish grammar, tone, and concision in technical prose without touching the math |
| `latex-workshop` | Follow VS Code LaTeX Workshop build behavior for TeX/LaTeX documents |
| `make-tex-docstring` | Create or propose a top-of-document TeX comment block that records the document profile and intended use |
| `math-dependency-graph` | Extract an assumptions-to-results dependency graph from a LaTeX document |
| `notation-review` | Audit and improve mathematical notation for lightness, unification, reuse across scopes, and semantic transparency |
| `proof-audit` | Audit a proof for soundness, coherence, hidden assumptions, and redundancy |
| `technical-flow-review` | Review flow, structure, motivation, and readability of a technical document |

### System & Automation

| Skill | What it does |
|---|---|
| `cloud-files` | Bounded read/write of plain files under a configured Google Drive root |
| `fix-bisync` | Diagnose and repair rclone bisync failures |
| `pdf-to-markdown` | Convert a research-paper PDF into LLM-readable text |
| `recurring-tasks` | Manage AI-driven recurring jobs as systemd user timers, with healthcheck |

### Development

| Skill | What it does |
|---|---|
| `git-workflow` | Branch-safety checks and commit hygiene for any repo |
| `hook-maker` | Design cross-host assistant hooks: one purpose, per-host bindings |
| `initialize-tdd` | Scaffold a staged, approval-gated TDD project |

### Skill Framework

| Skill | What it does |
|---|---|
| `install-assistant-tools` | Install or update launchers, wiring, hooks, and environment on a machine |
| `refactor-skills` | Audit and refactor existing skills against local conventions |
| `skill-maker` | Author new skills that conform to the repo's skill-writing guideline |
| `update-skill-guidelines` | Change the skill-writing standard and its mechanical checks in lockstep |
<!-- END SKILLS TABLE -->

## Repository layout

```text
skills/               one directory per skill
agents/               assistant, collab, and coauthor definitions
profiles/             host profile/config files for those agents
workers/              default local working directories for installed agents
references/           shared standards and blueprint references
script_dispatcher/    dispatcher package used by generated launcher
graphs/              Mermaid diagram sources and README sync script
llmhooks/             cross-host hook implementations and registry
hooks/                plugin-mode hook glue
validators/           repo-wide validators
.githooks/            pre-commit entry point
scripts/              repo maintenance generators
TESTING.md            test-suite notes and known hazards
PROFILES.md           generated profile comparison
```

## Validation and testing

Useful checks:

```bash
python3 graphs/sync-readme-graphs.py --check
python3 validators/runner.py
python3 -m pytest
```

The pre-commit hook runs the validators, gitleaks, and the README/profile
generators. CI runs the validator and install test suites on Linux, macOS, and
Windows with real Claude Code and Codex CLIs where available. See `TESTING.md`
for details.

## License

[MIT](LICENSE).
