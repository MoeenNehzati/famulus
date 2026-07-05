# Moeen Skills Config

Personal skill library and agent configuration for Claude Code and Codex.

The repository keeps one canonical `skills/` tree and exposes it through thin
platform-specific plugin manifests. There is no Claude-to-Codex skill-body
conversion step.

## Layout

```text
skills/                 # canonical skill source, one directory per skill
profiles/               # optional Codex profile files, one file per profile
references/             # shared support docs used by multiple skills
validators/             # repo-wide conformance validators (Python); run by pre-commit
scripts/                # repo-wide utility scripts (blueprint sync, skill dispatch, etc.)
tests/                  # unit tests for validators in validators/
.claude/                # repo-local Claude mirrors and ignored local settings
.codex/                 # repo-local Codex mirrors
.claude-plugin/         # Claude plugin metadata
.codex-plugin/          # Codex plugin metadata
hooks/                  # SessionStart hooks injected into every LLM session
CLAUDE.md               # shared repo instructions
AGENTS.md -> CLAUDE.md  # symlink for agents that read AGENTS.md
.githooks/              # local Git hooks (pre-commit delegates to validators/runner.py)
```

Each skill lives at `skills/<name>/SKILL.md` and may include local
`scripts/`, `references/`, `assets/`, tests, `permissions.json`, and for
blueprint-migrated skills a `blueprint.yaml`.
`permissions.json` is repo metadata for expected runtime needs; it is kept with
the skill and copied by plugin installation, but it is not a Codex permission
grant.

Shared reference material that is not itself a skill lives in top-level
`references/`. This keeps `skills/` compatible with Codex plugin validation,
which treats every immediate child of `skills/` as a skill directory.

## Blueprint Migration

This repository now uses a blueprint-based contract system for all local
skills.

- Every local skill has a hand-authored `blueprint.yaml` as the canonical
  contract.
- Generated artifacts for blueprint skills are:
  - `depends_on_skills`
  - `permissions.json`
  - the generated contract block injected near the top of `SKILL.md`

The reference starting point for a new migrated skill is:

```text
references/blueprint/template.yaml
```

Copy that file into `skills/<name>/blueprint.yaml` and edit it in place. The
template is intentionally comment-heavy and explains what each field means and
what kind of input it accepts.

For a complete guide on blueprints, including examples, patterns, and the
two-layer validation approach, see:

```text
references/blueprint/guide.md
```

### Dispatcher

Blueprint-migrated skills may depend on other skills at two levels:

- skill-to-skill invocation
- exported script-interface invocation through:

`dispatcher`

That dispatcher reads the callee's `blueprint.yaml`, validates the requested
interface and mode, checks dependency/version/export declarations, and only then
executes the concrete command.

### Current Migration Status

All local skills under `skills/` are expected to be blueprint-migrated. If a
new local skill appears without `blueprint.yaml`, the tracked hooks should fail.

### Maintainer Workflow

For a new blueprint-migrated skill or an interface change:

1. Copy `references/skill-blueprint-template.yaml` to `skills/<name>/blueprint.yaml`.
2. Edit the blueprint and keep its comments unless they are no longer accurate.
3. Run:

```bash
python3 skills/my-writing-skills/scripts/sync_skill_blueprints.py
```

4. Review the generated updates to `depends_on_skills`, `permissions.json`, and
   the injected contract block in `SKILL.md`.
5. Run:

```bash
python3 skills/my-writing-skills/tests/test_blueprint_tools.py
bash .githooks/pre-commit
```

### Hook Coverage

`.githooks/pre-commit` runs `validators/runner.py`, which auto-discovers and runs every module in:

- `validators/` — repo-wide checks (platform neutrality)
- `skills/my-writing-skills/validators/` — skill-system checks (names, metadata, blueprints, boundaries, dependencies, blueprint relationships)

Each validator module exports `validate(repo_root: Path) -> list[str]`. Adding a new `.py` file to either package with that signature is enough to have it enforced on every commit. Unit tests for validators live in `tests/validate_*.py`.

## Shared Instructions

`AGENTS.md` is a tracked symlink to `CLAUDE.md`:

```bash
AGENTS.md -> CLAUDE.md
```

This keeps Claude-facing and Codex-facing repository instructions mechanically
identical. On Unix-like systems Git tracks the symlink directly. On systems
without symlink support, Git may check out `AGENTS.md` as a text file containing
`CLAUDE.md`.

## Repo-Local Mirrors

This checkout also exposes the canonical top-level directories through thin
repo-local mirrors:

```text
.claude/agents      -> ../agents
.claude/references  -> ../references
.claude/skills      -> ../skills
.codex/agents       -> ../agents
.codex/references   -> ../references
.codex/skills       -> ../skills
```

These mirrors are a convenience layer for agents or tools that look for
repo-local `.claude/` or `.codex/` context. They are not the canonical source
of truth; the real content still lives in top-level `skills/`, `references/`,
`agents/`, and `CLAUDE.md` / `AGENTS.md`.

For Codex in particular, `.codex/skills` is only a repo-local mirror. The
user-level runtime skill path is `~/.codex/skills`.

`.claude/settings.local.json` is machine-local state and is ignored by Git.

## Systemwide Local Setup

On this machine, the canonical checkout lives at:

```text
/home/moeen/Documents/AI
```

Claude and Codex are wired to this checkout with symlinks, so both tools use the
same skill files.

Claude symlinks the shared directories directly:

```text
~/.claude/skills     -> /home/moeen/Documents/AI/skills
~/.claude/references -> /home/moeen/Documents/AI/references
~/.claude/agents     -> /home/moeen/Documents/AI/agents
~/.claude/CLAUDE.md  -> /home/moeen/Documents/AI/CLAUDE.md
```

Before creating each symlink the installer inspects the destination.

- Existing correct symlinks are kept as-is.
- Existing wrong symlinks are replaced.
- Existing real `skills/` directories are treated specially: unique local
  entries are migrated into the canonical `skills/` tree, redundant per-skill
  symlinks are removed, and then the user directory is replaced with a
  top-level symlink.
- Existing real files or directories at other destinations are skipped with a
  warning.
- `skills/` entries preserved from the user directories are added to the
  repo-local Git exclude file when possible, so developer-only local skills do
  not pollute `git status`.
- There is currently no interactive merge / backup / rollback flow. Conflicting
  `skills/` entry names are left for manual resolution.

Run `python3 scripts/install.py --help` for flags, or see the
`install-assistant-tools` skill for the current conflict-handling behavior.

The installer wires both skill paths to the same canonical tree:

```text
~/.claude/skills -> /home/moeen/Documents/AI/skills
~/.codex/skills -> /home/moeen/Documents/AI/skills
```

If you need to repair those links manually, link both `~/.claude/skills` and
`~/.codex/skills` to the
canonical checkout:

```text
~/.claude/skills -> /home/moeen/Documents/AI/skills
~/.codex/skills -> /home/moeen/Documents/AI/skills
```

Create or repair the link with:

```bash
ln -sfn /home/moeen/Documents/AI/skills ~/.claude/skills
ln -sfn /home/moeen/Documents/AI/skills ~/.codex/skills
```

Codex must keep `~/.codex` itself as a real directory. Do not make
`~/.codex` a symlink to this repository or to another writable tree: Codex's
Linux sandbox may reject read-only mounts that cross a writable symlink at the
home-directory boundary.

Inside that real `~/.codex` directory, keep Codex-managed state other than
`skills/` directly in place. Shared support files are linked back to the
canonical checkout:

```text
~/.codex/references     -> /home/moeen/Documents/AI/references
~/.codex/agents         -> /home/moeen/Documents/AI/agents
~/.codex/AGENTS.md      -> /home/moeen/Documents/AI/AGENTS.md
```

Codex profile files are loaded only when they live directly under
`$CODEX_HOME` as `<profile-name>.config.toml`. This repository keeps canonical
profile sources under `profiles/`; to use them in Codex, copy or symlink them
into the root of `~/.codex`:

```text
~/.codex/assistant.config.toml -> /home/moeen/Documents/AI/profiles/assistant.config.toml
```

Create or repair links for all repo-owned profiles with:

```bash
for f in /home/moeen/Documents/AI/profiles/*.config.toml; do
  ln -sfn "$f" ~/.codex/"$(basename "$f")"
done
```

After changing `~/.codex/skills`, restart Codex or open a new Codex
panel/thread if the skills do not appear.

With this direct user-level setup, Codex skills are invoked by bare skill name
such as `$proof-audit`. When installed through the Codex plugin manifest instead,
the same skills are namespaced as `moeen:proof-audit`.

## Session Hooks

The `hooks/` directory contains LLM `SessionStart` hooks injected at the start
of every session. The primary hook (`inject_dispatcher_context.py`) checks
whether the dispatcher is installed and injects context into the session about
the blueprint contract system — specifically that blueprint-managed skills must
be invoked through `dispatcher`, not directly via their `scripts/` directories.

Hooks are registered through two independent paths:

| Mode | Mechanism | When it applies |
|------|-----------|----------------|
| Plugin | `hooks/hooks.json` (read by the platform) | Repo installed as a plugin; `CLAUDE_PLUGIN_ROOT` or `CODEX_PLUGIN_ROOT` is set by the host |
| Dev / direct | `~/.claude/settings.local.json` and `~/.codex/config.toml` (written by installer) | Repo symlinked to `~/.claude` / `~/.codex`; platform variables are not set |

The installer (`install-assistant-tools`) handles dev-mode registration automatically.
Both paths call the same script via absolute path.

To add a new hook, update all three together: `hooks/hooks.json`,
`install_claude_hooks()`, and `install_codex_hooks()` in
`skills/install-assistant-tools/scripts/setup_tools.py`.

## Skills

Skills are on-demand instruction sets loaded when invoked. They cover:

**Personal assistant & automation**
- `daily-plan` — generate a daily plan from calendar, todos, and weather
- `g-calendar` — read and write Google Calendar via a local OAuth CLI
- `list-manager` — manage personal structured YAML lists stored in assistant cloud storage
- `get-weather` — fetch weather for a day or date range with a planning summary
- `fix-bisync` — diagnose and repair rclone bisync failures

**Writing & documents**
- `formal-prose-review` — polish grammar, tone, and clarity in technical prose
- `technical-flow-review` — review structure, flow, and readability
- `notation-review` — audit and unify mathematical notation
- `proof-audit` — audit a proof for soundness, coherence, and redundancy
- `tool-applicability` — check whether a mathematical tool applies
- `math-dependency-graph` — extract a dependency graph from a LaTeX document
- `make-tex-docstring` — propose a top-of-document profile comment
- `latex-workshop` — compile LaTeX matching VS Code LaTeX Workshop settings
- `bib-audit` — audit a `.bib` file for syntax, style, and duplicate issues

**Meta**
- `my-writing-skills` — personal conventions for writing and maintaining skills
- `refactor-skills` — audit/refactor skills against local conventions
- `initialize-tdd` — scaffold a staged TDD project

## Profiles & Agent Configuration

Three agent profiles are configured for both Claude and Codex. See [PROFILES.md](PROFILES.md) for a complete comparison of model selection and capability levels.

Profile-specific settings are stored in:
- **Codex**: `profiles/*.config.toml`
- **Claude**: `profiles/*_claude_setting.json`

The `PROFILES.md` comparison table is auto-generated from these source files and updated automatically before each commit by the pre-commit hook.

## Codex

Codex uses `.codex-plugin/plugin.json`, which points directly at:

```json
"skills": "./skills/"
```

Installed skills are namespaced by plugin name, for example:

```text
moeen:proof-audit
moeen:latex-workshop
moeen:technical-flow-review
```

No generated Codex copy is committed. Platform-specific Codex behavior belongs
in `.codex-plugin/plugin.json`, optional Codex hooks, or skill-local
`agents/openai.yaml` files if added later.

### Codex Local Install Test

Run:

```bash
python3 skills/install-assistant-tools/tests/test_codex_install.py
```

The test creates an isolated temporary `CODEX_HOME`, a temporary local
marketplace, and an empty work directory. It first confirms that this repo's
skills are not visible before installation. It then installs the plugin,
checks every packaged skill and key shared assets, and runs the packaged
`install-assistant-tools` installer into a fresh temporary home to verify the
installed launchers, profiles, and symlink wiring.

The test uses `codex debug prompt-input`; it does not call a model.

### Metadata Guard

Codex rejects skill descriptions longer than 1024 characters. The `skill_metadata`
validator enforces this on every commit via `validators/runner.py`. To run it manually:

```bash
python3 skills/my-writing-skills/validators/skill_metadata.py
```

The `install-assistant-tools` skill configures the tracked hook path. To set it
manually:

```bash
git config core.hooksPath .githooks
```

The tracked `.githooks/pre-commit` hook blocks commits in detached HEAD state
and runs all conformance validators through `validators/runner.py`.

## Claude

Claude uses `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.

### Via Marketplace

Add the marketplace and install the plugin:

```bash
/plugin marketplace add MoeenNehzati/claude-config
/plugin install moeen@nullmarket
```

Skills are namespaced under `/moeen:` when installed as a plugin, for example:

```text
/moeen:daily-plan
/moeen:wrap-up
```

To update after a push:

```bash
/plugin marketplace update
/plugin update moeen@nullmarket
```

### Via Direct Load

Clone anywhere and load for one Claude session:

```bash
git clone git@github.com:MoeenNehzati/claude-config.git ~/moeen-claude
claude --plugin-dir ~/moeen-claude
```

Or update the clone and reload plugins:

```bash
cd ~/moeen-claude
git pull
# then inside Claude:
/reload-plugins
```

### As Full `~/.claude`

Use this only on a fresh machine or when intentionally replacing the full
Claude config:

```bash
git clone git@github.com:MoeenNehzati/claude-config.git ~/.claude
```

In this layout, skills load without plugin namespacing.

### Claude Install Test

Run:

```bash
python3 skills/install-assistant-tools/tests/test_claude_install.py
```

The test creates an isolated temporary Claude home, validates the local plugin
and marketplace manifests, installs the plugin from the local marketplace, and
checks that the installed cache contains every packaged skill plus the expected
agents and shared files. It also verifies Claude's local `plugins details`
inventory for the full packaged skill and agent set.

The test uses Claude's local plugin-management commands only; it does not call
a model.

Note: `claude plugins validate --strict` still warns because the repository root
contains `CLAUDE.md`; Claude's validator treats that as plugin-root context that
is packaged but not loaded as project context. The install test therefore runs
non-strict validation and then asserts the installed cache contents directly.

TODO for future cleanup:

- Decide whether plugin-root `CLAUDE.md` should remain packaged.
- If we want `claude plugins validate --strict` to pass cleanly, restructure or
  relocate that context instead of relying on plugin-root `CLAUDE.md`.
- We are intentionally **not** changing it yet because current install/runtime
  behavior is acceptable, and the stronger Python install test now checks the
  installed cache contents directly.

## Development Checks

Validate the Codex plugin manifest:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Run the isolated install tests:

```bash
python3 skills/install-assistant-tools/tests/test_codex_install.py
python3 skills/install-assistant-tools/tests/test_claude_install.py
```

Run focused installer unit tests:

```bash
python3 skills/install-assistant-tools/tests/test_setup_symlinks.py
python3 skills/install-assistant-tools/tests/test_setup_tools_cloud_files.py
```

Run blueprint tooling regression tests:

```bash
python3 skills/my-writing-skills/tests/test_blueprint_tools.py
python3 skills/my-writing-skills/validators/blueprints.py
python3 skills/my-writing-skills/validators/boundaries.py
```

Check Python syntax for install tests:

```bash
python3 -m py_compile skills/install-assistant-tools/tests/test_codex_install.py skills/install-assistant-tools/tests/test_claude_install.py
```

After a coherent skill or plugin change, review the diff and commit so the
cross-LLM configuration remains reproducible.
