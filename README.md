# Famulus

[![Python Tests](https://github.com/MoeenNehzati/famulus/actions/workflows/python-tests.yml/badge.svg)](https://github.com/MoeenNehzati/famulus/actions/workflows/python-tests.yml)
[![Documentation](https://github.com/MoeenNehzati/famulus/actions/workflows/pages.yml/badge.svg)](https://moeennehzati.github.io/famulus/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Famulus is a personal research assistant, delivered as a skills library that
runs on both Claude Code and Codex. It covers day-to-day planning and inbox work
on one side, and research-heavy reading and writing on the other.

Once it is installed, you just ask:

```text
Plan my day.
Audit this bibliography before submission.
Build a math dependency graph for paper.tex.
Prepare a handoff.
```

[40 skills](docs/skills.md) are available. The
[featured flows](#featured-flows) below show what a few of them do.

## What It Is Good At

On the personal side, Famulus:

- connects to your email and calendar
- manages your todo and triage lists in a cloud-backed list manager
- extracts triage items from your email
- prepares handoffs by updating session documentation and lessons

Most importantly, it can plan your day from your calendar and lists, then
document your progress at the end of the day and remind you about sessions that
still need handoff.

On the research side, it provides skills for reviewing document flow and prose,
checking notation consistency across a paper, auditing mathematical proofs,
drawing dependency graphs for mathematical results, and inspecting bibliographies
for version mismatches, hallucinated metadata, and newer available versions.

## Requirements

Famulus is on the `0.1.0` development line. The research and writing skills need
no credentials and are the easiest place to start. The Google integrations and
unattended recurring jobs are the newest parts and the ones that ask most of
your trust, so read [security and privacy](docs/security-and-privacy.md) before
enabling them.

To install Famulus you need:

- a plugin-capable Claude Code or Codex installation; there is no published
  minimum host version
- Python 3.11 or newer to launch the workstation installer
- network access during first setup so the installer can obtain its pinned
  `uv`, managed CPython, and hash-locked Python packages

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

### 2. Install the assistant tools

Installing the plugin makes the skills visible, but it does not create the local
commands they depend on: `dispatcher`, `llm-wakeup`/`lw`, `invoke-skill`, the
required `background_run` launcher, profile files, and `PATH` wiring.

Ask your assistant:

```text
Install the assistant tools.
```

The `install-assistant-tools` skill walks you through the setup, confirms what
it resolved before changing anything, then checks its own work and explains the
optional Google and recurring-job steps without running them. Choose `standard`
unless you are developing Famulus itself from a checkout. Open a new shell
afterwards when the installer reports a search-path change.

Your installation does not depend on where you cloned or unpacked anything, so
you can move or delete the original directory afterwards.

Later updates and repairs use this same workflow. Setup options, diagnosis,
removal, and verification are covered in the
[Installation Guide](docs/officina/installation.md).

### 3. Choose a workflow

Start with the quickstart closest to what you want to do:

- [Personal Assistance](docs/quickstarts/personal-assistance.md) — plan the day, manage lists, triage email, and wrap up
- [Research](docs/quickstarts/research.md) — choose the right research review, editing, conversion, or build skill
- [Software Development](docs/quickstarts/development.md) — work safely with repositories, CI, TDD, integration, and handoffs
- [Skill Development](docs/quickstarts/skill-development.md) — create, refactor, maintain, and certify Famulus skills
- [Automation](docs/quickstarts/automation.md) — schedule, inspect, change, or disable recurring assistant jobs

See [Security and Privacy](docs/security-and-privacy.md) before connecting an
account or enabling unattended work.

## What Leaves Your Machine

Famulus is a collection of LLM instructions and local programs. It does not add
a separate Famulus account or hosted runtime service, and it has no
Famulus-operated telemetry endpoint.

It does connect your host agent to services you select, and network requests
happen only as part of functionality you chose: your Claude or Codex provider
processes model-session content, Google processes OAuth and Drive, Calendar and
Gmail requests, a registered non-Gmail mail provider processes IMAP/SMTP,
Open-Meteo receives weather locations, and the configured feedback mailbox
receives reports you approve. Anything returned to Claude or Codex becomes part
of that provider's model session.

Full detail, including what each connected service can reach, is in
[Security and Privacy](docs/security-and-privacy.md).

## Featured Flows

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
Famulus separates workflow updates, documentation updates, and residual lessons so the next session will not miss any context the current session has.

### Wrap up today

Use `wrap-up` when you want to close the day cleanly, mark what happened, and capture follow-up items without losing context from the plan you actually worked from.

Example prompts:

- `Wrap up.`
- `Review today's plan and wrap up.`

Result:
Famulus updates the day's plan with completions and unplanned work, closes the loop on follow-up items, and surfaces sessions that still need handoff attention so work does not disappear into an undocumented transcript. The potential missing handoffs are added to the triage.

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
grants Famulus broad OAuth scopes, and disconnect, server-side revocation,
uninstall, and purge are not yet one command, so read the
[security and privacy boundary](docs/security-and-privacy.md) first.

After installation, ask:

```text
Connect Famulus to Google.
```

Famulus recommends Drive, Calendar, and Gmail, while letting you connect only
the subset you want. `connect-google` guides you through creating a Google
Cloud project and Desktop OAuth client, then cloud-files, online-calendar, and
email-client perform and own their respective authorizations. Never commit the
client JSON to GitHub. Each service keeps its resulting user tokens in its own
local credential storage.

For a broader list of workflows and prompt ideas, see the
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

The installer can provide three main agent launchers:

- `assistant` for day-to-day personal assistant work
- `collab` for longer project sessions with continuity and handoff behavior
- `coauthor` for writing-focused sessions

It also installs `background_run`, a non-interactive launcher required by
`invoke-skill` for explicitly enabled recurring jobs. It is not an ordinary
interactive launcher. Scheduled invocations use host approval/sandbox bypass
modes so they cannot pause for a person who is not present; review the
[unattended execution boundary](docs/security-and-privacy.md#authorization-and-confirmation-boundaries)
before enabling any recurring job.

Those launchers work with both Claude Code and Codex. A separate `tw` /
`tmux-workspace` wrapper can launch them inside a prearranged tmux workspace
with assistant, terminal, scratch, and logs panes/windows.

Usage details, backend selection, and documentation for the launchers are in
[docs/launchers.md](docs/launchers.md).

## Update

Refresh the host plugin first:

```bash
# Claude Code
claude plugin marketplace update nullkit
claude plugin update famulus@nullkit

# Codex
codex plugin marketplace upgrade nullkit --json
```

Then restart the host and ask your assistant to install the assistant tools
again, so the managed runtime and local commands match the refreshed package.

## Uninstall

Removing Famulus takes three steps, and the order matters.

1. **Disable recurring jobs first.** Ask your assistant to disable this
   installation's recurring jobs and remove their scheduler registration.
   Removal reaches your operating system's scheduler, so it has to happen while
   the runtime that knows how to reach it is still present. Uninstall refuses
   to proceed if registrations remain.
2. **Remove the installation.** Ask your assistant to run the uninstaller while
   the source is still available. This removes installer-owned files and leaves
   your credentials, worker content, and recurring history in place.
3. **Revoke Google access yourself.** Uninstalling does not revoke anything.
   Remove Famulus from your Google account's third-party access settings and
   delete the local credential files. Famulus does not do this for you.

Exact commands, the difference between uninstall and purge, and the separate
credential-revocation steps are in the
[installation lifecycle](docs/officina/installation.md#uninstall-versus-purge).

## Learn More

- [Skill Index](docs/skills.md) — the complete list of available skills
- [Security and Privacy](docs/security-and-privacy.md) — permissions, credentials, model data, destructive actions, and removal
- [Installation Guide](docs/officina/installation.md) — setup, diagnosis, repair, and removal
- [Launchers](docs/launchers.md) — agent launchers, backend selection, and the `tw` tmux wrapper
- [Dependency and Bootstrap Audit](docs/dependency-and-bootstrap-audit.md) — release dependency, bootstrap, and vendored-asset audit
- [Assistant Interaction](docs/domains/assistant-interaction.md) — reasoning modes, session continuity, handoffs, and wakeups
- [Assistant Operations](docs/domains/assistant-operations.md) — storage, authentication, automation, installation, and repair utilities

## Support

Report non-sensitive bugs and documentation problems through the
[public issue tracker](https://github.com/MoeenNehzati/famulus/issues).
Use the private route in [SECURITY.md](SECURITY.md) for vulnerabilities, and
never include credentials, tokens, private documents, or personal data in an
issue.

## For Maintainers

- [docs/contributors/README.md](docs/contributors/README.md) — maintainer and skill-extension entrypoint
- [docs/contributors/documentation-system.md](docs/contributors/documentation-system.md) — documentation generation and validation
- [docs/testing.md](docs/testing.md) — repository checks, test commands, suite policy, hooks, CI, and benchmarks

## License

[MIT](LICENSE) for Famulus itself. Vendored components retain their own
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
