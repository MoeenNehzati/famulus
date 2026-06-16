# Claude Config

My personal [Claude Code](https://claude.ai/code) setup — working conventions, skills, and configuration.

## Superpowers

Several skills in this repo extend or override skills from the [superpowers-marketplace](https://github.com/obra/superpowers-marketplace) plugin. Install it before using this config:

```bash
claude plugin marketplace add obra/superpowers-marketplace
claude plugin install superpowers@superpowers-marketplace
```

## Skills

Skills are on-demand instruction sets loaded when invoked. They live in `skills/` and cover:

**Personal assistant & automation**
- `daily-plan` — generate a daily plan from calendar, todos, and weather
- `g-calendar` — read and write Google Calendar via a local OAuth CLI
- `lists` — manage personal Markdown checklists stored on Google Drive
- `weather` — fetch today's weather with a day-planning summary
- `fix-bisync` — diagnose and repair rclone bisync failures

**Writing & documents**
- `formal-prose-review` — polish grammar, tone, and clarity in technical prose
- `technical-flow-review` — review structure, flow, and readability of a document
- `notation-review` — audit and unify mathematical notation
- `proof-audit` — audit a proof for soundness, coherence, and redundancy
- `tool-applicability` — check whether a mathematical tool applies in a given setting
- `math-dependency-graph` — extract a dependency graph from a LaTeX math document
- `make-tex-docstring` — add a top-of-document profile comment to a TeX file
- `latex-workshop` — compile LaTeX matching VS Code LaTeX Workshop settings
- `bib-audit` — audit a `.bib` file for syntax, style, and duplicate issues

**Meta**
- `my-writing-skills` — personal conventions for writing and maintaining skills
- `initialize-tdd` — scaffold a new project with a staged TDD workflow

## Installing the plugin

### Via marketplace (recommended)

Add the `nullmarket` marketplace and install the `moeen` plugin:

```
/plugin marketplace add MoeenNehzati/claude-config
/plugin install moeen@nullmarket
```

Skills are namespaced under `/moeen:` (e.g. `/moeen:daily-plan`, `/moeen:wrap-up`).

To get updates after a new push:

```
/plugin marketplace update
/plugin update moeen@nullmarket
```

### Via direct load (no install)

Clone anywhere and load for a single session:

```bash
git clone git@github.com:MoeenNehzati/claude-config.git ~/moeen-claude
claude --plugin-dir ~/moeen-claude
```

Or pull updates and reload without restarting:

```bash
cd ~/moeen-claude && git pull
# then inside Claude:
/reload-plugins
```

### As a full setup (clone to ~/.claude)

Replaces your `~/.claude` entirely — use only on a fresh machine or if you want to adopt this config as your own base:

```bash
git clone git@github.com:MoeenNehzati/claude-config.git ~/.claude
```

Skills load without namespacing (e.g. `/daily-plan`, `/wrap-up`) since they're in your personal skills directory. Then install the superpowers plugin (see above) and configure any credentials needed by individual skills (e.g. `g-calendar/scripts/setup_oauth.py`).
