# Famulus

Famulus is a cross-llm skills library for personal planning, research-heavy writing, and continuous skill development. It supports both Claude Code and Codex.
## What It Is Good At

Famulus is meant to be a personal research assistant, with both personal and research workflows. On the personal side, it connects to your email and calendar, provides a cloud-backed list manager for your todo and triage lists, extracts triage items from your email, and prepares handoffs by updating session documentation and lessons. Most importantly, it can plan your day from your calendar and lists, then document your progress at the end of the day and remind you about sessions that still need handoff.

On the research side, it provides skills for reviewing document flow and prose, checking notation consistency across a paper, auditing mathematical proofs, drawing dependency graphs for mathematical results, and inspecting bibliographies for version mismatches, hallucinated metadata, and newer available versions.

At its core is a standard skill organization schema that keeps skills coherent and decoupled. Most of that structure is statically verifiable without LLM intervention through validators. Once those validators are enforced by git hooks, they keep ongoing skill development on track.

## Quick Start

### Recommended: plugin install

Start by installing Famulus as a plugin for your host. That is the fastest way to make the skill suite available. If you want to edit skills or share one live checkout across hosts, see [docs/installation.md](docs/installation.md).

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

Then run `install-assistant-tools` to add the local scaffold that plugin installation does not create by itself: `dispatcher`, `invoke-skill`, optional agent launchers, profile files, and PATH wiring.

For development mode, repair flows, and more installation detail, see [docs/installation.md](docs/installation.md).

## Platform Support

Famulus works with both Claude Code and Codex.

The install and packaging paths have CI coverage on Linux, macOS, and Windows through [`.github/workflows/test-install.yml`](.github/workflows/test-install.yml). In practice, it has only been tested thoroughly on Linux.

## Featured Flows

### Plan my day

Use `daily-plan` when you want the assistant to pull together your calendar, weather, todos, and triage list into one concrete plan.

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

Those launchers work with both Claude Code and Codex. A separate `tw` / `tmux-workspace` wrapper can launch them inside a prearranged tmux workspace with assistant, terminal, scratch, and logs panes/windows.

Usage details and documentation for the launchers are in [docs/launchers.md](docs/launchers.md).

## Learn More

- [docs/user/general.md](docs/user/general.md) — planning, wrap-up, inbox, lists, calendar, and weather workflows
- [docs/user/research.md](docs/user/research.md) — research and writing workflows, including dependency graphs
- [docs/user/system.md](docs/user/system.md) — storage, automation, and system-facing utilities
- [docs/launchers.md](docs/launchers.md) — agent launchers, backend selection, and the `tw` tmux wrapper
- [docs/skills.md](docs/skills.md) — generated full skill index

## For Maintainers

- [docs/contributors/documentation-system.md](docs/contributors/documentation-system.md) — documentation generation and validation
- [docs/contributors/README.md](docs/contributors/README.md) — maintainer and skill-extension entrypoint


## Maintainer Checks

These checks are for contributors working on the repository, not for ordinary plugin users.

- `python3 scripts/generate-doc-artifacts.py` — regenerate generated documentation artifacts and embedded coverage blocks.
- `python3 validators/runner.py` — run the repository's documentation and contract validators.
- `python3 -m pytest` — run the Python test suite.

The pre-commit hook regenerates documentation artifacts, the README preview, and `PROFILES.md`, then runs the repo validators and secret scan before commit.

## License

[MIT](LICENSE)
