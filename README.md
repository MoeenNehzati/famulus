# Famulus

Famulus is a personal research assistant, delivered as a skills library that runs on both Claude Code and Codex. It covers day-to-day planning and inbox work on one side, and research-heavy reading and writing on the other.

**Documentation: <https://moeennehzati.github.io/famulus/>** — the full site, built and published from this repository on every change.

## Project Status and Requirements

Famulus is on the `0.1.0` development line. **No promoted stable release** or
long-term version-support policy exists yet, so expect interfaces to keep
moving. The research and writing skills need no credentials and are the easiest
place to start. The Google integrations and unattended recurring jobs are the
newest parts and the ones that ask most of your trust, so read
[security and privacy](docs/security-and-privacy.md) before enabling them.

To install Famulus you need:

- a plugin-capable Claude Code or Codex installation; Famulus does not yet
  publish a minimum supported host-version matrix
- Python 3.11 or newer to launch the workstation installer
- network access during first setup so the installer can obtain its pinned
  `uv`, managed CPython, and hash-locked Python packages

## What It Is Good At

On the personal side, Famulus connects to your email and calendar, provides a cloud-backed list manager for your todo and triage lists, extracts triage items from your email, and prepares handoffs by updating session documentation and lessons. Most importantly, it can plan your day from your calendar and lists, then document your progress at the end of the day and remind you about sessions that still need handoff.

On the research side, it provides skills for reviewing document flow and prose, checking notation consistency across a paper, auditing mathematical proofs, drawing dependency graphs for mathematical results, and inspecting bibliographies for version mismatches, hallucinated metadata, and newer available versions.

## Quick Start

Installing Famulus begins by registering the package with your host, then using
the package's five-stage apply workflow to create or repair one explicit
installation context. Both parts are described below.
In short: register the package, then run the workstation installer through the
assistant workflow below.

### Step 1: install the plugin

Famulus ships as the `famulus` plugin inside the `nullkit` marketplace, which
lives in this repository. That is why you add the marketplace by repository
name and then install the plugin from it by its `plugin@marketplace` id.

Claude Code:

```text
/plugin marketplace add MoeenNehzati/famulus
/plugin install famulus@nullkit
```

Codex:

```bash
codex plugin marketplace add MoeenNehzati/famulus --json
codex plugin add famulus@nullkit --json
```

Restart the host afterwards so it loads the newly installed plugin.

### Apply an installation context

Installing the plugin makes the skills visible, but it does not create the local
commands they depend on: `dispatcher`, `llm-wakeup`/`lw`, `invoke-skill`, the
required `background_run` launcher, profile files, and `PATH` wiring.

Ask your assistant to install the assistant tools. The
`install-assistant-tools` skill walks through exactly five stages: choose a
`standard` package context or a `development` checkout context, confirm the
resolved choices, apply once, diagnose that same context, and explain optional
Google connection and recurring-job next steps without running them. Open a new
shell afterwards when the standard installer reports a search-path change.

Development contexts keep their runtime, state, assistant homes, jobs, and logs
under the selected checkout's `.famulus/` tree. This isolation prevents
different checkouts from sharing state, but it is not a security sandbox: the
launched assistant retains its host-granted authority.

Once the tools are installed, later updates and repairs use the same apply
workflow. Installed commands and development adapters use a self-locating
resolver and the selected context's `current.json`; normal operation does not
depend on the original clone location. The context choices, diagnosis, removal,
and verification steps are all in
[docs/officina/installation.md](docs/officina/installation.md).

### Choose a workflow

Once Famulus is installed, start with the quickstart closest to what you want
to do:

- [Personal Assistance](docs/quickstarts/personal-assistance.md) — plan the day, manage lists, triage email, and wrap up
- [Research](docs/quickstarts/research.md) — choose the right research review, editing, conversion, or build skill
- [Software Development](docs/quickstarts/development.md) — work safely with repositories, CI, TDD, integration, and handoffs
- [Skill Development](docs/quickstarts/skill-development.md) — create, refactor, maintain, and certify Famulus skills
- [Automation](docs/quickstarts/automation.md) — schedule, inspect, change, or disable recurring assistant jobs

See [Security and Privacy](docs/security-and-privacy.md) before connecting an
account or enabling unattended work. The [Installation Guide](docs/officina/installation.md)
covers setup and repair, while the [Skill Index](docs/skills.md) lists every
available skill.

### Update or remove

Refresh the host plugin first:

```bash
# Claude Code
claude plugin marketplace update nullkit
claude plugin update famulus@nullkit

# Codex
codex plugin marketplace upgrade nullkit --json
```

After an update, restart the host and ask your assistant to apply the same
installation context again, so the managed runtime and local commands match the
refreshed package. Before removing a context, use `recurring-tasks` to disable
its jobs and run `scripts-remove-context`, then run the manifest-based
uninstaller while the source is still available.
Exact removal commands and the separate credential-revocation steps are in the
[installation lifecycle](docs/officina/installation.md#uninstall-versus-purge).

## Platform Support

Famulus is designed to be cross-platform. The plugin is written to the
intersection of the Claude Code and Codex plugin standards, so one package
serves both hosts, and almost all of the logic lives in Python rather than in
shell, so the same code runs on every operating system. CI covers the install
and packaging paths on Linux, macOS, and Windows through
[`.github/workflows/python-tests.yml`](.github/workflows/python-tests.yml).

That said, Famulus has only been thoroughly exercised by hand on Linux, so
installation and day-to-day behavior on macOS and Windows may not be entirely
without a hitch. The likeliest rough edges are the parts that reach into
operating-system scheduling — recurring tasks and wakeups in particular.

If you hit one, you can usually just ask your assistant to fix it; the code is
Python sitting on your own machine. Then tell us what happened, either by
asking for the `send-feedback` skill, which drafts a redacted report, shows it
to you, and files it on the
[issue tracker](https://github.com/MoeenNehzati/famulus/issues) once you
approve, and we will fix it upstream.

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
Cloud project and Desktop OAuth client, then cloud-files, g-calendar, and
email-client perform and own their respective authorizations. Never commit the
client JSON to GitHub. Each service keeps its resulting user tokens in its own
local credential storage.

## More Examples

For a broader list of workflows and prompt ideas, see [docs/skills.md](docs/skills.md).

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

Durable backend selection lives in the context's `launchers.json`;
`ASSISTANT_DEFAULT` is only a per-process override. Those launchers work with
both Claude Code and Codex. A separate `tw` / `tmux-workspace` wrapper can
launch them inside a prearranged tmux workspace with assistant, terminal,
scratch, and logs panes/windows.

Usage details and documentation for the launchers are in [docs/launchers.md](docs/launchers.md).

## Learn More

- [docs/security-and-privacy.md](docs/security-and-privacy.md) — permissions, credentials, model data, destructive actions, and removal
- [docs/dependency-and-bootstrap-audit.md](docs/dependency-and-bootstrap-audit.md) — release dependency, bootstrap, and vendored-asset audit
- [SECURITY.md](SECURITY.md) — private vulnerability reporting
- [docs/quickstarts/personal-assistance.md](docs/quickstarts/personal-assistance.md) — get started with planning, inbox triage, lists, calendar, weather, and wrap-up
- [docs/quickstarts/research.md](docs/quickstarts/research.md) — choose the right research review, editing, conversion, or build workflow
- [docs/quickstarts/development.md](docs/quickstarts/development.md) — choose the right repository, CI, TDD, integration, or handoff workflow
- [docs/quickstarts/automation.md](docs/quickstarts/automation.md) — enable, verify, inspect, and disable recurring assistant jobs
- [docs/quickstarts/skill-development.md](docs/quickstarts/skill-development.md) — choose the right skill-development and assurance workflow
- [docs/domains/assistant-interaction.md](docs/domains/assistant-interaction.md) — reasoning modes, session continuity, handoffs, and wakeups
- [docs/domains/assistant-operations.md](docs/domains/assistant-operations.md) — storage, authentication, automation, installation, and repair utilities
- [docs/launchers.md](docs/launchers.md) — agent launchers, backend selection, and the `tw` tmux wrapper
- [docs/skills.md](docs/skills.md) — generated full skill index

## For Maintainers

- [docs/contributors/documentation-system.md](docs/contributors/documentation-system.md) — documentation generation and validation
- [docs/contributors/README.md](docs/contributors/README.md) — maintainer and skill-extension entrypoint
- [docs/testing.md](docs/testing.md) — test commands, suite policy, hooks, CI, and parallel execution

## Support

Report non-sensitive bugs and documentation problems through the
[public issue tracker](https://github.com/MoeenNehzati/famulus/issues).
Use the private route in [SECURITY.md](SECURITY.md) for vulnerabilities, and
never include credentials, tokens, private documents, or personal data in an
issue.

## Maintainer Checks

These checks are for contributors working on the repository, not for ordinary plugin users.

- `python3 scripts/generate-doc-artifacts.py` — regenerate generated documentation artifacts and embedded coverage blocks.
- `python3 repo_checks.py --suite validators` — run the repository's documentation and contract validators.
- `python3 repo_checks.py --suite full --verbose` — run the full Python suite, including installation tests.

Hook order, CI behavior, suite boundaries, and benchmark guidance are documented
in [docs/testing.md](docs/testing.md).

## License

[MIT](LICENSE). Vendored components retain their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
