# Moeen Skills Config

Personal skill library and agent configuration for Claude Code and Codex.

The repository follows the same broad design as Superpowers: keep one canonical
`skills/` tree and expose it through thin platform-specific plugin manifests.
There is no Claude-to-Codex skill-body conversion step.

## Layout

```text
skills/                 # canonical skill source, one directory per skill
profiles/               # optional Codex profile files, one file per profile
references/             # shared support docs used by multiple skills
.claude/                # repo-local Claude mirrors and ignored local settings
.codex/                 # repo-local Codex mirrors
.claude-plugin/         # Claude plugin metadata
.codex-plugin/          # Codex plugin metadata
CLAUDE.md               # shared repo instructions
AGENTS.md -> CLAUDE.md  # symlink for agents that read AGENTS.md
tests/                  # install/visibility checks
.githooks/              # local Git hooks for mechanical checks
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

```text
scripts/invoke_skill_export.py
```

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

The blueprint-related checks live in the tracked hook path:

- `.githooks/skill/check-blueprints` — blueprint presence, injection layout, and artifact sync
- `.githooks/skill/check-boundaries` — prevents blueprint skills from reaching into another skill's scripts
- `.githooks/skill/check-blueprint-tooling` — regression tests for template, dispatcher, and injection behavior

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
documented user-level skill path remains `~/.agents/skills`.

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

- Existing symlinks are replaced.
- Existing real files or directories are skipped with a warning.
- There is currently no interactive merge / backup / rollback flow.

Run `python3 scripts/install.py --help` for flags, or see the
`install-assistant-tools` skill for the current conflict-handling behavior.

Codex reads user-level skills from `~/.agents/skills`. Link that directory to
the canonical checkout:

```text
~/.agents/skills -> /home/moeen/Documents/AI/skills
```

Create or repair the link with:

```bash
mkdir -p ~/.agents
ln -sfn /home/moeen/Documents/AI/skills ~/.agents/skills
```

Codex must keep `~/.codex` itself as a real directory. Do not make
`~/.codex` a symlink to this repository or to another writable tree: Codex's
Linux sandbox may reject read-only mounts that cross a writable symlink at the
home-directory boundary.

Inside that real `~/.codex` directory, keep Codex-managed state directly in
place. Link only shared non-skill support files back to the canonical checkout
as needed:

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

Do not rely on `~/.codex/skills` for personal skills. Some Codex surfaces may
see it, but `~/.agents/skills` is the documented user-level skill location and
keeps the CLI and IDE extension aligned. After changing skill links, restart
Codex or open a new Codex panel/thread if the skills do not appear.

With this direct user-level setup, Codex skills are invoked by bare skill name
such as `$proof-audit`. When installed through the Codex plugin manifest instead,
the same skills are namespaced as `moeen:proof-audit`.

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

Codex rejects skill descriptions longer than 1024 characters. Enforce that
before install or push with:

```bash
tests/test_skill_metadata.py
```

The Codex install test runs this metadata check first.

The `install-assistant-tools` skill configures the tracked hook path. To set it
manually:

```bash
git config core.hooksPath .githooks
```

The tracked `.githooks/pre-commit` hook blocks commits in detached HEAD state
and dispatches to categorized Git and skill checks under `.githooks/git/` and
`.githooks/skill/`.

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

## Superpowers Dependency

Several skills extend or expect skills from the
[superpowers-marketplace](https://github.com/obra/superpowers-marketplace)
plugin. Install it for Claude before relying on those overrides:

```bash
claude plugin marketplace add obra/superpowers-marketplace
claude plugin install superpowers@superpowers-marketplace
```

For Codex, install the corresponding Superpowers plugin/marketplace if you want
skills such as `my-writing-skills` to invoke their upstream Superpowers
counterparts.

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
