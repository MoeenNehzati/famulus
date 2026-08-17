# Famulus

Famulus is a cross-llm skills library for personal planning, research-heavy writing, and continuous skill development. It supports both Claude Code and Codex.

## Project Status and Requirements

Famulus is on the `0.1.0` development line. **No promoted stable release** or
long-term version-support policy exists yet. Credential-free research and
writing workflows are the lowest-risk starting point; Google integrations and
unattended recurring jobs remain experimental.

To install Famulus you need:

- a plugin-capable Claude Code or Codex installation; Famulus does not yet
  publish a minimum supported host-version matrix
- Python 3.11 or newer to launch the workstation installer
- network access during first setup so the installer can obtain its pinned
  `uv`, managed CPython, and hash-locked Python packages

## What It Is Good At

Famulus is meant to be a personal research assistant, with both personal and research workflows. On the personal side, it connects to your email and calendar, provides a cloud-backed list manager for your todo and triage lists, extracts triage items from your email, and prepares handoffs by updating session documentation and lessons. Most importantly, it can plan your day from your calendar and lists, then document your progress at the end of the day and remind you about sessions that still need handoff.

On the research side, it provides skills for reviewing document flow and prose, checking notation consistency across a paper, auditing mathematical proofs, drawing dependency graphs for mathematical results, and inspecting bibliographies for version mismatches, hallucinated metadata, and newer available versions.

At its core is a standard skill organization schema that keeps skills coherent and decoupled. Most of that structure is statically verifiable without LLM intervention through validators. Once those validators are enforced by git hooks, they keep ongoing skill development on track.

## Quick Start

### Recommended: plugin install

Start by installing Famulus as a plugin for your host. That is the fastest way to make the skill suite available. If you want to edit skills or share one live checkout across hosts, see [docs/officina/installation.md](docs/officina/installation.md).

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

Then run the `install-assistant-tools` skill to add the local scaffold that plugin installation does not create by itself: `dispatcher`, `invoke-skill`, optional agent launchers, profile files, and PATH wiring.

For minimum non-interactive setup, first locate the installed plugin directory
(`installPath` in `claude plugin list --json`, or `source.path` in
`codex plugin list --json`). Call that directory `<FAMULUS_DIR>`, then run the
Phase 1 orchestrator:

```bash
python3 <FAMULUS_DIR>/skills/install-assistant-tools/_rtx/_phase_entry.py \
  --non-interactive --no-dev-mode --no-optional-deps
```

Unlike the scaffold-only repair script, this command first builds and activates
the managed runtime and then writes `dispatcher`, `llm-wakeup`/`lw`,
`invoke-skill`, and its required `background_run` launcher. It installs no
optional interactive agent launchers. Pass `--agents assistant,collab,coauthor,tw`
to add them, and use `--home DIR`, `--bin-dir DIR`, or `--shell-rc FILE` when
provisioning a custom environment.

For development mode, repair flows, and more installation detail, see [docs/officina/installation.md](docs/officina/installation.md).

### Update or remove

Refresh the host plugin first:

```bash
# Claude Code
claude plugin marketplace update nullkit
claude plugin update famulus@nullkit

# Codex
codex plugin marketplace upgrade nullkit --json
```

After an update, restart the host and rerun the Phase 1 command above from the
refreshed `<FAMULUS_DIR>` so the managed runtime and local launchers match the
plugin source. Before removing the plugin, disable recurring jobs and run the
manifest-based workstation uninstaller while `<FAMULUS_DIR>` still exists.
Exact removal commands and the separate credential-revocation steps are in the
[installation lifecycle](docs/officina/installation.md#6-updating-repairing-and-removing-famulus).

## Platform Support

Famulus targets both Claude Code and Codex.

The install and packaging paths run in CI on Linux, macOS, and Windows through
[`.github/workflows/python-tests.yml`](.github/workflows/python-tests.yml).
Linux has the deepest real-world testing; macOS and Windows support remains
preliminary, and a green workflow should be checked for the commit you install.

## Featured Flows

### Experimental: Connect Google

Google integrations are experimental in the first public release. They use
broad OAuth grants and do not yet provide one complete disconnect, server-side
revocation, uninstall, and purge lifecycle. Use them only after reviewing the
[security and privacy boundary](docs/security-and-privacy.md).

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

### Plan my day

Use `daily-plan` when you want the assistant to pull together your calendar, weather, todos, and triage list into one concrete plan.

Interactive use is user-initiated. Scheduling `daily-plan`, `email-triage`, or
another agent-driven workflow is experimental and must be enabled explicitly;
Famulus does not set up recurring jobs during its core installation.

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

## More Examples

For a broader list of workflows and prompt ideas, see [docs/skills.md](docs/skills.md).


## Agents and Launchers

The workstation installer provides three main agent launchers:

- `assistant` for day-to-day personal assistant work
- `collab` for longer project sessions with continuity and handoff behavior
- `coauthor` for writing-focused sessions

It also installs `background_run`, a non-interactive launcher required by
`invoke-skill` for explicitly enabled recurring jobs. It is not an ordinary
interactive launcher. Scheduled invocations use host approval/sandbox bypass
modes so they cannot pause for a person who is not present; review the
[unattended execution boundary](docs/security-and-privacy.md#authorization-and-confirmation-boundaries)
before enabling any recurring job.

Those launchers work with both Claude Code and Codex. A separate `tw` / `tmux-workspace` wrapper can launch them inside a prearranged tmux workspace with assistant, terminal, scratch, and logs panes/windows.

Usage details and documentation for the launchers are in [docs/launchers.md](docs/launchers.md).

## Learn More

- [docs/security-and-privacy.md](docs/security-and-privacy.md) — permissions, credentials, model data, destructive actions, and removal
- [docs/dependency-and-bootstrap-audit.md](docs/dependency-and-bootstrap-audit.md) — release dependency, bootstrap, and vendored-asset audit
- [SECURITY.md](SECURITY.md) — private vulnerability reporting
- [docs/domains/personal-assistance.md](docs/domains/personal-assistance.md) — planning, wrap-up, inbox, lists, calendar, and weather workflows
- [docs/domains/assistant-interaction.md](docs/domains/assistant-interaction.md) — reasoning modes, session continuity, handoffs, and wakeups
- [docs/domains/research.md](docs/domains/research.md) — research and writing workflows, including dependency graphs
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
