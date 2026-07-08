# Famulus

Famulus is a personal and research assistant skillset compatible with both Claude Code and Codex (here referred to as the **hosts**). It includes
skills for day-to-day planning, automation, research and writing, auditing and decomposing complex math, and skill development.
The repo also ships three agent configurations for different kinds of sessions — `assistant`, `collab`, and `coauthor` (see [Agents and launchers](#agents-and-launchers) below).

The package has mainly been tested on Linux, but CI runs end-to-end tests on macOS and Windows as well.

## Overview

At a glance — each of these is expanded with skill names and diagrams in [What is included](#what-is-included) below:

- **General assistant skills:** plays the role of a personal secretary, connected to your email, calendar, and cloud file system. Each morning it can:
  - check the weather and suggest what to wear;
  - fetch the day's calendar and upcoming events (birthdays and more);
  - suggest todo-list actions that fit your free time;
  - surface actionable items from incoming email.

- **Research and writing skills:** meant to help with writing and understanding academic research, mostly math-heavy papers. It can check proofs (more rigorously than LLMs do out of the box), review notation, assess whether a theorem or tool applies to a given setting, plot the dependency graph of a paper's results, audit a bibliography for hallucinations, newer versions, or duplicates, and review the prose and flow of a document.

- **Workflow skills:** improves day-to-day interaction with the LLM: git hygiene, explore-vs-accuracy modes, handoff preparation (making sure everything learned in a session is written down for future use), and tracing sessions that need a handoff at day's end.

- **System and automation skills:** `recurring-tasks` schedules email scans and daily-plan generation as systemd timers and continuously monitors its own health.

- **Skill-development tools:** tooling for LLM-assisted skill development — mainly pre-commit hooks and validators that keep skills small and their coupling explicit.

- **Installed command-line tools:** the workstation installer installs
  `assistant`, `collab`, `coauthor`, and `tw`/`tmux-workspace` launchers, plus a
  generated `dispatcher` launcher skills use to invoke their scripts.

### Runtime map
The diagrams below are rendered PNGs. Their Graphviz sources live under
`graphs/`; regenerate them with `python3 graphs/render-graphs.py`.

<img src="graphs/runtime-map.png" alt="Runtime map" style="max-width: 100%; width: auto; height: auto; display: block; margin: 1rem auto;">

## Install, update, and uninstall

Choose one install path: plugin mode for a host-managed install, or workstation
mode for one local checkout with launchers, hooks, profiles, OAuth helpers, and
recurring-task support.

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

It removes the managed Codex and Claude Code hook registrations, unlinks managed
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

Remote objects such as calendars, forecasts, and email accounts live outside
the repo. Cloud lists and cloud plans live under the configured Google Drive LLM
root. The skills fetch from or write to those remote sources. `recurring-tasks`
is one of the skills, but its external integration is the local OS: it writes and
syncs systemd user timers, runs selected skills on a schedule, and reports
through local logs and healthchecks. This scheduled layer is
Linux/systemd-specific; the rest of the repo's framework is tested
cross-platform.

<img src="graphs/daily-assistant-loop.png" alt="Daily assistant loop" style="max-width: 100%; width: auto; height: auto; display: block; margin: 1rem auto;">

### Research and writing

The research skills are mostly independent tools rather than one tightly coupled
workflow:

<img src="graphs/research-writing.png" alt="Research and writing skills" style="max-width: 100%; width: auto; height: auto; display: block; margin: 1rem auto;">

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

<img src="graphs/skill-development-framework.png" alt="Skill development framework" style="max-width: 100%; width: auto; height: auto; display: block; margin: 1rem auto;">

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

Each has Claude Code and Codex profile/config files under `profiles/`, and each gets
a worker directory under `workers/`. `PROFILES.md` is generated from those
profile files and summarizes the differences.

### Full skill list

The table below is generated from `skills/*/blueprint.yaml` and each skill's
`SKILL.md` description by `scripts/generate-skills-table.py`. The pre-commit
hook regenerates it when sources change.

<!-- BEGIN SKILLS TABLE (generated by scripts/generate-skills-table.py) -->
<div class="skills-tree">
  <div class="skills-tree-title">skills/</div>
  <div class="skills-tree-subtitle">31 skills arranged by blueprint taxonomy</div>
  <pre class="skills-tree-block">
    assistant/
        |__ research/
        |   |__ bib-audit/                <span class="skills-tree-note">Audit a `.bib` file for validity, style, external metadata, and duplicates</span>
        |   |__ formal-prose-review/      <span class="skills-tree-note">Polish grammar, tone, and concision in technical prose without touching the math</span>
        |   |__ latex-workshop/           <span class="skills-tree-note">Follow VS Code LaTeX Workshop build behavior for TeX/LaTeX documents</span>
        |   |__ make-tex-docstring/       <span class="skills-tree-note">Create or propose a top-of-document TeX comment block that records the document profile and intended use</span>
        |   |__ math-dependency-graph/    <span class="skills-tree-note">Extract an assumptions-to-results dependency graph from a LaTeX document</span>
        |   |__ notation-review/          <span class="skills-tree-note">Audit and improve mathematical notation for lightness, unification, reuse across scopes, and semantic transparency</span>
        |   |__ proof-audit/              <span class="skills-tree-note">Audit a proof for soundness, coherence, hidden assumptions, and redundancy</span>
        |   |__ technical-flow-review/    <span class="skills-tree-note">Review flow, structure, motivation, and readability of a technical document</span>
        |__ general/
        |   |__ productivity/
        |   |   |__ email-client/             <span class="skills-tree-note">Read, search, and send email across configured accounts</span>
        |   |   |__ email-triage/             <span class="skills-tree-note">Triage the inbox into todo and potential-action lists since the last run</span>
        |   |   |__ g-calendar/               <span class="skills-tree-note">Read and modify Google Calendar via a local OAuth CLI</span>
        |   |   |__ get-weather/              <span class="skills-tree-note">Fetch weather for a location, day, or date range</span>
        |   |   |__ list-manager/             <span class="skills-tree-note">Manage personal YAML lists (todo, shopping, reading, …) in cloud storage</span>
        |   |__ workflow/
        |   |   |__ daily-plan/               <span class="skills-tree-note">Generate today's plan from calendar, todos, and weather</span>
        |   |   |__ find-handoff-candidates/  <span class="skills-tree-note">You need a mechanical, non-interpretive scan of today's (or another day's) work sessions to find ones that had substantial activity but no …</span>
        |   |   |__ loose-mode/               <span class="skills-tree-note">Broad, fast exploration mode — breadth over certainty</span>
        |   |   |__ prepare-handoff/          <span class="skills-tree-note">Prepare a clean handoff: workflow updates, doc updates, residual lessons</span>
        |   |   |__ tight-mode/               <span class="skills-tree-note">Rigorous, verified output mode — certainty over speed</span>
        |   |   |__ tool-applicability/       <span class="skills-tree-note">Check whether a theorem or framework achieves a target in the current setting</span>
        |   |   |__ wrap-up/                  <span class="skills-tree-note">End-of-day wrap-up: review the plan, record completions, capture new items</span>
        |__ development/
        |   |__ skill/
        |   |   |__ hook-maker/               <span class="skills-tree-note">Design cross-host assistant hooks: one purpose, per-host bindings</span>
        |   |   |__ install-assistant-tools/  <span class="skills-tree-note">Install or update launchers, wiring, hooks, and environment on a machine</span>
        |   |   |__ refactor-skills/          <span class="skills-tree-note">Audit and refactor existing skills against local conventions</span>
        |   |   |__ skill-maker/              <span class="skills-tree-note">Author new skills that conform to the repo's skill-writing guideline</span>
        |   |   |__ update-skill-guidelines/  <span class="skills-tree-note">Change the skill-writing standard and its mechanical checks in lockstep</span>
        |   |__ coding/
        |   |   |__ initialize-tdd/           <span class="skills-tree-note">Scaffold a staged, approval-gated TDD project</span>
        |   |__ git-workflow/             <span class="skills-tree-note">Branch-safety checks and commit hygiene for any repo</span>
        |__ system/
        |   |__ cloud-files/              <span class="skills-tree-note">Bounded read/write of plain files under a configured Google Drive root</span>
        |   |__ fix-bisync/               <span class="skills-tree-note">Diagnose and repair rclone bisync failures</span>
        |   |__ pdf-to-markdown/          <span class="skills-tree-note">Convert a research-paper PDF into LLM-readable text</span>
        |   |__ recurring-tasks/          <span class="skills-tree-note">Manage AI-driven recurring jobs as systemd user timers, with healthcheck</span>
  </pre>
</div>
<!-- END SKILLS TABLE -->

## Repository layout

```text
skills/               one directory per skill
agents/               assistant, collab, and coauthor definitions
profiles/             host profile/config files for those agents
workers/              default local working directories for installed agents
references/           shared standards and blueprint references
script_dispatcher/    dispatcher package used by generated launcher
graphs/               Graphviz DOT sources and rendered PNG/SVG diagrams
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
python3 graphs/render-graphs.py --check
python3 validators/runner.py
python3 -m pytest
```

The pre-commit hook runs the validators, gitleaks, and the README/profile
generators. CI runs the validator and install test suites on Linux, macOS, and
Windows with real Claude Code and Codex CLIs where available. See `TESTING.md`
for details.

## License

[MIT](LICENSE).
