# Famulus

[![Python Tests](https://github.com/MoeenNehzati/famulus/actions/workflows/python-tests.yml/badge.svg)](https://github.com/MoeenNehzati/famulus/actions/workflows/python-tests.yml)
[![Documentation](https://github.com/MoeenNehzati/famulus/actions/workflows/pages.yml/badge.svg)](https://moeennehzati.github.io/famulus/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/MoeenNehzati/famulus/blob/master/LICENSE)

Famulus is a collection of skills for Claude Code and Codex. You can ask it to
plan your day, review a paper or proof, work safely in a codebase, schedule
recurring tasks, or preserve the context needed for the next session.

Once it is installed, you just ask:

```text
Plan my day.
Audit this bibliography before submission.
Build a math dependency graph for paper.tex.
Prepare a handoff.
```

See the [Skill Index](docs/skills.md) for the complete list. The
[examples](#examples) below show what a few of the skills do.

## What It Is Good At

For personal organization, Famulus can:

- connect to your email and calendar
- manage your todo list and a triage list for items that may become todos
- extract triage items from your email
- close loose ends by updating project documentation and preserving useful lessons

It can use your calendar and lists to plan the day, record your progress when
you wrap up, and flag sessions that still need a handoff.

For research and writing, Famulus can review document flow and prose, check
notation consistency across a paper, audit mathematical proofs, draw dependency
graphs for mathematical results, and inspect bibliographies for version
mismatches, hallucinated metadata, and newer available versions.

## Requirements

Famulus is under active development. Using it as a personal assistant requires
a Google account: plans and lists are stored in Google Drive, while daily
planning and email triage read information from Google Calendar and Gmail. The
research, writing, and software-development skills do not require Google
access.

Before connecting an account or enabling unattended recurring jobs, read
[Security and Privacy](docs/security-and-privacy.md).

To install Famulus you need:

- a plugin-capable Claude Code or Codex installation; there is no published
  minimum host version
- Python 3.11 or newer, available as `python`, with pip enabled and permission
  to install the required packages

## Quick Start

### 1. Install the plugin

Famulus ships as the `famulus` plugin inside the `nullkit` marketplace, so you
add the marketplace first and then install the plugin from it by its
`plugin@marketplace` id.

Claude Code, from inside a session:

```text
/plugin marketplace add MoeenNehzati/famulus
/plugin install famulus@nullkit
```

Claude Code, from a terminal:

```bash
claude plugin marketplace add MoeenNehzati/famulus
claude plugin install famulus@nullkit
```

Codex:

```bash
codex plugin marketplace add MoeenNehzati/famulus --json
codex plugin add famulus@nullkit --json
```

Restart the host afterwards so it loads the newly installed plugin.

### 2. Check Python

Confirm that `python` is Python 3.11 or newer and that `python -m pip` works. If
Famulus reports a missing core dependency, ask it to use
`setup-python-environment`.

### 3. Set up optional features

Some parts of Famulus need additional setup, such as connecting a Google
account or scheduling recurring tasks. You do not need to configure them when
you first install Famulus. Famulus keeps track of what is ready and guides you
through any required setup when you request a feature that needs it.

See [Setting up Famulus](docs/setup.md) for details.

### 4. Choose a quickstart

Start with the quickstart closest to what you want to do:

- [Personal Assistance](docs/quickstarts/personal-assistance.md) — plan the day,
  manage lists, triage email, and wrap up
- [Research](docs/quickstarts/research.md) — choose the right research review,
  editing, conversion, or build skill
- [Software Development](docs/quickstarts/development.md) — work safely with
  repositories, CI, TDD, integration, and handoffs
- [Automation](docs/quickstarts/automation.md) — schedule, inspect, change, or
  disable recurring assistant jobs
- [Skill Development](docs/quickstarts/skill-development.md) — create,
  refactor, maintain, and certify skills with
  [Officina](docs/officina/README.md), the framework behind Famulus

See [Security and Privacy](docs/security-and-privacy.md) before connecting an
account or enabling unattended work.

## Examples

### Plan my day

Use `daily-plan` when you want the assistant to pull together your calendar, weather, todos, and triage list into one concrete plan.

Asking for a plan is always something you initiate. You can also put
`daily-plan`, `email-triage`, or another workflow on a schedule, but that runs
unattended and has to be enabled deliberately — the core installation sets up
no recurring jobs on its own.

Example prompts:

- `Plan my day.`
- `What's on the plan?`

Result:
Famulus assembles a current plan, highlights what fits into the day, and stores the plan so later workflows can build on it.

### Prepare a handoff

Use `prepare-handoff` when you are stopping, switching tracks, or handing a project to another session and want the important context preserved in the repo rather than stranded in chat history.

Example prompts:

- `Prepare a handoff`
- `I'm switching tracks.`

Result:
Famulus updates the relevant project documentation and records useful lessons
so another session can pick up the work.

### Wrap up today

Use `wrap-up` when you want to close the day cleanly, mark what happened, and capture follow-up items without losing context from the plan you actually worked from.

Example prompts:

- `Wrap up.`
- `Review today's plan and wrap up.`

Result:
Famulus records completed and unplanned work, captures follow-up items, and
flags sessions that still need a handoff. It adds possible missing handoffs to
the triage list.

### Build a math dependency graph

Use `math-dependency-graph` when you want a structured view of how assumptions, definitions, lemmas, and theorems depend on each other in a LaTeX document.

Example prompts:

- `Build a math dependency graph for paper.tex.`
- `Render the dependency graph for this LaTeX manuscript.`

Result:
Famulus extracts a canonical dependency graph and can render it as a standalone interactive HTML view.

Public example:
The graph for *Inference From Random Restarts* is available at <https://moeennehzati.github.io/assets/html/nehzati2026inference.html>, and the paper is at <https://arxiv.org/abs/2602.13450>.

### Audit a bibliography

Use `bib-audit` when you want to check a `.bib` file for syntax issues, duplicate or conflicting entries, metadata problems, and newer-version mismatches before submission.

Example prompts:

- `Audit this bibliography file before submission.`
- `Check this .bib file for duplicates and version conflicts.`

Result:
Famulus inspects the bibliography for structural problems and consistency issues, then flags concrete entries that need correction or verification.

### Connect Google

The calendar, mail, and cloud-list flows above need Google access. Connecting it
grants Famulus broad OAuth scopes, so read the
[security and privacy boundary](docs/security-and-privacy.md) first.

After installation, ask:

```text
Connect Famulus to Google.
```

Famulus recommends Drive, Calendar, and Gmail, but you choose which services to
connect. `connect-google` guides you through creating a Google Cloud project
and Desktop OAuth client. Never commit the client JSON to GitHub. Each service
stores its user tokens locally.

For a broader list of skills and prompt ideas, see the
[Skill Index](docs/skills.md).

## Platform Support

Famulus is designed to be cross-platform. The plugin is written to the
intersection of the Claude Code and Codex plugin standards, so one package
serves both hosts, and almost all of the logic lives in Python rather than in
shell, so the same code runs on every operating system. CI covers the install
and packaging paths on Linux, macOS, and Windows.

That said, Famulus has only been thoroughly exercised by hand on Linux, so
installation and day-to-day behavior on macOS and Windows may be rougher. The
likeliest trouble spots are the parts that reach into operating-system
scheduling — recurring tasks and wakeups in particular.

If you hit one, you can usually just ask your assistant to fix it; the code is
Python sitting on your own machine. Then tell us what happened: ask for the
`send-feedback` skill, which drafts a redacted report, shows it to you, and
files it on the [issue tracker](https://github.com/MoeenNehzati/famulus/issues)
once you approve. We will fix it upstream.

## Agents and Launchers

`install-launchers` can set up any of three main agent launchers:

- `assistant` for day-to-day personal assistant work
- `collab` for longer project sessions with continuity and handoff behavior
- `coauthor` for writing-focused sessions

It can also prepare background support for recurring jobs. Review the
[unattended execution boundary](docs/security-and-privacy.md#authorization-and-confirmation-boundaries)
before enabling any recurring job.

Those launchers work with both Claude Code and Codex. A separate `tw` /
`tmux-workspace` wrapper can launch them inside a prearranged tmux workspace
with assistant, terminal, scratch, and logs panes/windows.

Usage details, backend selection, and documentation for the launchers are in
[docs/launchers.md](docs/launchers.md).

## Maintenance

### Update Famulus

Refresh the host plugin first:

```bash
# Claude Code
claude plugin marketplace update nullkit
claude plugin update famulus@nullkit

# Codex
codex plugin marketplace upgrade nullkit --json
```

Then restart the host. In this first release, you may need to rerun setup for
optional features after an update.

### Remove Famulus

There is no single command that removes Famulus and all of its optional
features. Disable or remove optional features individually; for example,
disable recurring jobs before removing their scheduler support. Removing the
plugin does not revoke Google access or delete service data.

Famulus does not automatically remove installations that predate the plugin.
Inspect and remove an older installation separately, and revoke Google access
through your Google account if you no longer want Famulus to use it.

## Learn More

- [Documentation Index](docs/README.md) — the entry point for all Famulus and
  Officina documentation
- [Skill Index](docs/skills.md) — the complete list of available skills
- [Security and Privacy](docs/security-and-privacy.md) — permissions,
  credentials, model data, destructive actions, and removal
- [Launchers](docs/launchers.md) — agent launchers, backend selection, and the
  `tw` tmux wrapper
- [Setup](docs/setup.md) — selected-Python requirements, shared MCP routing,
  and demand-driven feature setup
- [Officina](docs/officina/README.md) — the framework for developing and
  operating mixed LLM and code systems

## Support

Report non-sensitive bugs and documentation problems through the
[public issue tracker](https://github.com/MoeenNehzati/famulus/issues).
Use the private route in [SECURITY.md](SECURITY.md) for vulnerabilities, and
never include credentials, tokens, private documents, or personal data in an
issue.

## For Maintainers

Start with the [Contributor Guide](docs/contributors/README.md) for skill
development, repository checks, documentation, testing, and framework
maintenance.

## License

[MIT](LICENSE) for Famulus itself. Vendored components retain their own
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
