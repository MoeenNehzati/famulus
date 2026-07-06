<div align="center">

# 🧙 Famulus

**A research-assistant skill library for Claude Code and Codex.**

*A famulus is the scholar's assistant of old — it does the prep work,
keeps the notebooks, and runs the lab while you think.*

[![Install Tests](https://github.com/MoeenNehzati/famulus/actions/workflows/test-install.yml/badge.svg)](https://github.com/MoeenNehzati/famulus/actions/workflows/test-install.yml)

</div>

Daily planning · calendar & email automation · mathematical writing and
review · LaTeX tooling · recurring background jobs — plus the framework that
keeps all of it consistent. One repository serves both hosts: the same
skills, agents, and hooks work in Claude Code and Codex, installed as a
plugin or wired directly into a development checkout.

**Contents:** [Installation](#-installation) ·
[Skills](#-skills) ·
[Design](#-design)

---

## 📦 Installation

There are three ways to use this repository. All of them expose the same
skills; they differ in isolation and update mechanics.

### 1 · Plugin — *recommended for consumption*

Install through the built-in plugin managers. Skills are namespaced
(`famulus:proof-audit`), content is copied into the host's plugin cache, and
updates are explicit.

Claude Code:

```text
/plugin marketplace add MoeenNehzati/famulus
/plugin install famulus@nullkit
# later:
/plugin update famulus@nullkit
```

Codex: add the repo as a plugin marketplace snapshot and `codex plugin add
famulus@<marketplace>`.

Uninstalling is host-managed (`/plugin uninstall`, `codex plugin remove`).
Note an asymmetry verified against both CLIs: Codex removes its cache
directory; Claude deregisters the plugin but deliberately retains the cache.

### 2 · Developer install — *a full workstation setup*

The `install-assistant-tools` skill (or `python3
skills/install-assistant-tools/scripts/install.py`) wires a checkout into
both hosts and sets up the surrounding machinery:

- **Symlinks** for read-only shared content: `~/.claude/{skills,references,agents,CLAUDE.md}`
  and `~/.codex/{skills,references,agents,AGENTS.md}` point into the checkout.
- **Copies** for files the hosts write back to: `profiles/*.config.toml` are
  copied (not symlinked) into both homes, because Codex records machine-local
  state (project trust, hook hashes) into its config file — a symlink would
  leak that state into git.
- **Generated launchers**: `assistant`, `collab`, `coauthor`, `tw` are linked
  into a bin dir on `PATH`; `dispatcher` is generated to run the shared
  dispatcher package directly from the repo (`$AI`) — first-party code is
  never pip-installed, so there is no second copy to drift or break.
- **Session hooks** registered for both hosts (see Hook system below).
- **Shell environment**: a managed rc block exports `PATH`,
  `ASSISTANT_DEFAULT`, and `$AI` (on Windows, the user registry is used
  instead of an rc file).
- **Git hooks**: `core.hooksPath` is set to `.githooks` (skipped when the
  install source is not a git checkout, e.g. a plugin cache).
- An **install manifest** recording every side effect, persisted after each
  step so even an interrupted install can be cleanly reversed.

Key options (`install.py --help` for the full list): `--home`, `--bin-dir`,
`--claude-home`, `--codex-home`, `--shell-rc`, `--default-llm claude|codex`,
`--no-claude` / `--no-codex`, `--dry-run`. Optional Google Drive / Calendar
OAuth setup is offered interactively and skipped in non-interactive runs.

Pre-existing user content is never clobbered: real files are skipped with a
warning, and an existing real `~/.claude/skills` directory has its unique
entries migrated into the repo tree before the directory is replaced by a
symlink (name conflicts are left in place for manual resolution).

**Uninstall** is manifest-based only: `uninstall.py` replays the recorded
side effects in reverse and reports every action. If the manifest is missing
(pre-manifest install, or deleted by hand), it refuses and asks for one
idempotent re-run of the installer to regenerate it — guessing at installed
artifacts by pattern is deliberately not supported. OAuth/service configs
survive unless `--purge` is passed. An install→uninstall round trip is tested
to restore the home byte-for-byte.

<details>
<summary><b>Codex wiring notes</b></summary>

`~/.codex` itself must stay a real directory (Codex's sandbox may reject a
symlinked home boundary); only its contents are linked. After changing
`~/.codex/skills`, restart Codex if skills do not appear. In dev mode skills
are invoked by bare name (`$proof-audit`); in plugin mode they are namespaced
(`famulus:proof-audit`).

</details>

### 3 · Direct plugin dir — *just trying it out*

```bash
git clone git@github.com:MoeenNehzati/famulus.git ~/famulus
claude --plugin-dir ~/famulus
```

No installation, no wiring; skills load for that session only.

---

## 🛠 Skills

The table below is generated from `skills/*/blueprint.yaml` (category) and
each skill's `SKILL.md` description by `scripts/generate-skills-table.py`,
run automatically by the pre-commit hook — it cannot go stale.

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
| `my-writing-skills` | Author new skills that conform to the repo's skill-writing guideline |
| `refactor-skills` | Audit and refactor existing skills against local conventions |
| `update-skill-guidelines` | Change the skill-writing standard and its mechanical checks in lockstep |
<!-- END SKILLS TABLE -->

---

## 🏛 Design

### Layout

```text
skills/        one directory per skill: SKILL.md + blueprint.yaml + scripts/ + tests/
agents/        agent definitions (assistant, collab, coauthor)
profiles/      per-agent host configs (see PROFILES.md for the comparison table)
references/    shared reference documents, including the skill-writing guideline
llmhooks/      cross-host session-hook implementations + registry
hooks/         plugin-mode hook glue (thin shims around llmhooks)
script_dispatcher/  the shared dispatcher package (run from the repo, never installed)
validators/    repo-wide pre-commit validators
.githooks/     the pre-commit entrypoint
scripts/       repo maintenance generators (PROFILES.md, skills table)
tests/         smoke tests for the validators
```

### Blueprints and the dispatcher: the access model

Every skill carries a hand-authored `blueprint.yaml` — its canonical
contract: category, interface version, declared dependencies, and the script
interfaces it exports. From that contract, sync tooling generates
`depends_on_skills`, `permissions.json`, and the contract block at the top of
`SKILL.md` (see `references/blueprint/template.yaml` and
`references/blueprint/guide.md`).

Cross-skill access is allowed in exactly two forms, and blocked in every
other:

1. **Skill invocation** — one skill invoking another as a skill, if declared
   in `depends_on`.
2. **Exported script interfaces via the dispatcher** — `dispatcher
   --caller-skill <caller> <callee> <interface> ...` (CLI) or
   `script_dispatcher.dispatch()` (Python). The dispatcher reads the
   *callee's* blueprint, validates that the caller declares the dependency,
   that the interface is exported at a compatible version, and only then
   executes the underlying command.

What is blocked — mechanically, not by convention: a skill reaching into
another skill's `scripts/` directory, sourcing its files, adding it to
`sys.path`, or invoking the dispatcher CLI from Python code (the library call
carries the caller identity; the CLI from Python would launder it). The
pre-commit validators reject all of these.

### Pre-commit gates

`.githooks/pre-commit` runs on every commit (and CI re-runs the same
validators on every push, so `--no-verify` buys nothing):

- **gitleaks** scans staged changes for secrets; the commit is blocked
  fail-closed if gitleaks is missing.
- **`validators/runner.py`** executes every validator module against a mirror
  of *tracked* files only (untracked local clutter is invisible), including:
  - `personal_info` — no personal name tokens or home paths anywhere in
    tracked content (a small documented allowlist covers author signatures in
    plugin manifests and the repo's public GitHub handle)
  - `platform_neutral` — shared content stays host-agnostic
  - `blueprints` / `blueprint_relationships` / `dependencies` /
    `interface_ids` — blueprint contracts are valid, in sync with generated
    artifacts, and dependency declarations match reality
  - `dispatcher_usage` / `dispatch_caller_skill` / `boundaries` /
    `skill_md_dispatch` — the access model above, enforced
  - `names` / `skill_metadata` — naming and frontmatter conventions
- **Generators** — `PROFILES.md` and the README skills table are regenerated
  and auto-staged if their sources changed.

### Skill guidelines and the skill-making skills

`references/skill-guidelines.md` is the single written standard for what a
skill in this framework looks like — structure, naming, dependency rules,
dispatcher usage, documentation conventions. It is machine-consumed, not just
documentation:

- **`my-writing-skills`** reads the guideline when creating or editing a
  skill, so new skills conform by construction; its validator package
  (`skills/my-writing-skills/validators/`) is where most of the mechanical
  enforcement above lives.
- **`refactor-skills`** audits existing skills against the same guideline.
- **`update-skill-guidelines`** is the change-management skill: any edit to
  the guideline must keep the mechanical checks in lockstep — a rule that is
  added gets a check, a rule that is removed loses its stale check. The
  standard and its enforcement cannot drift apart silently.

### Cross-host hooks and hook-maker

Session hooks live under `llmhooks/` as host-neutral implementations with a
shared scaffold (`llmhooks/lib/cross_host.py`) that handles input parsing,
host-specific output shaping, and install metadata. `llmhooks/registry.py` is
the canonical list; the installer registers dev-mode hooks from it
(`~/.claude/settings.local.json`, `~/.codex/config.toml`), while plugin
installs use the `hooks/hooks.json` glue. The live example is
`inject_dispatcher_context`, which tells every session that blueprint skills
must be called through the dispatcher.

The **`hook-maker`** skill is how new hooks get written: it separates a
hook's *purpose* (the semantic action) from its per-host *bindings*
(lifecycle event, invocation, output schema), so one implementation serves
Claude, Codex, and future hosts without forking logic.

### Profiles and agents

Three agents — `assistant` (day-to-day productivity), `collab` (long project
sessions), `coauthor` (writing) — each with a Claude settings file and a
Codex profile under `profiles/`. `PROFILES.md` (generated) compares model and
capability settings across them. Launchers of the same names start the right
agent with the right profile on either backend (`ASSISTANT_DEFAULT` picks the
backend; `--claude` / `--codex` override).

### Testing and CI

```bash
python3 -m pytest            # everything collected per pytest.ini
python3 validators/runner.py # the pre-commit battery, standalone
```

GitHub Actions runs on every push across ubuntu, macOS, and Windows: the
validators, the hermetic unit suites, and the full install/uninstall/e2e
matrix with real `claude` and `codex` CLIs — plugin install/uninstall on both
hosts, dev-mode skill accessibility, launcher execution, user-skill survival,
and a byte-exact install→uninstall round trip. See `TESTING.md` for suite
layout and known hazards.
