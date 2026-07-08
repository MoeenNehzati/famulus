---
name: install-assistant-tools
description: Install or update the assistant, collab, coauthor, and tw/tmux-workspace helpers on a machine. Use when the user wants these commands installed, repaired, refreshed, or propagated to another system; when a system lacks them; or when the helper definitions should be updated in shell startup files.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Dependencies: none

Interface Version: 2

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `scripts-dev-link` — Symlink Claude/Codex config dirs to a live repo checkout, register dev-mode hooks, set git hooksPath, export $AI. Requires an explicit repo path.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools scripts-dev-link --repo-root DIR [--no-claude] [--no-codex] [--home DIR] [--claude-home DIR] [--codex-home DIR] [--shell-rc FILE] [--dry-run]`
- `scripts-install` — Phase-1 orchestrator: asks the dev-mode question, then runs scaffold, optionally dev-link, then launchers.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools scripts-install [--dry-run] [--non-interactive] [--dev-mode|--no-dev-mode] [--repo-path DIR] [--agents LIST] [--default-llm {claude,codex}] [--home DIR] [--bin-dir DIR] [--shell-rc FILE] [--codex-home DIR] [--claude-home DIR]`
- `scripts-launchers` — Install per-agent bin launcher, profile config, worker dir, and ASSISTANT_DEFAULT for the given agents.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools scripts-launchers --repo-root DIR --agents LIST [--home DIR] [--bin-dir DIR] [--codex-home DIR] [--claude-home DIR] [--shell-rc FILE] [--default-llm {claude,codex}] [--dry-run]`
- `scripts-scaffold` — Install the dispatcher + invoke-skill launchers and put the bin dir on PATH. Universal floor, mode-independent.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools scripts-scaffold --repo-root DIR [--home DIR] [--bin-dir DIR] [--shell-rc FILE] [--dry-run]`
<!-- END BLUEPRINT INTERFACES -->
# Install Assistant Tools

When this skill is used, begin with:

Skill: install-assistant-tools

## Platform Support

The installer and launchers run on **Linux, macOS, and Windows** (Python 3.6+
required). One exception: `tmux-workspace` is Unix-only (tmux does not exist on
Windows).

If any step fails or a command is not usable on the user's platform, **ask
whether they want the skill to adapt the relevant scripts** before attempting
any changes.

## Layout

```
bin/
  assistant          Python launcher — claude --agent assistant or codex --profile assistant
  collab             Python launcher — claude --agent collab or codex --profile collab
  coauthor           Python launcher — claude --agent coauthor or codex --profile coauthor
  tmux-workspace     Bash script for tw / tmux-workspace (Unix only)
  _agent_launch.py   Shared launcher logic imported by the three launchers above.
                     Resolves its own repo root via Path(__file__).resolve()
                     (works in plugin mode too, no $AI dependency); builds the
                     Claude --agents JSON inline from agents/<agent>.md instead
                     of requiring $CLAUDE_HOME/agents/<agent>.md.
  assistant.bat      Windows wrapper (delegates to assistant via py.exe)
  collab.bat         Windows wrapper (delegates to collab via py.exe)
  coauthor.bat       Windows wrapper (delegates to coauthor via py.exe)
scripts/
  install.py         Phase-1 orchestrator — asks the dev-mode question, then
                     chains scaffold -> [dev_link] -> launchers
  scaffold.py        Universal floor: dispatcher + invoke-skill launchers, PATH,
                     required Python packages. Mode-independent, always runs.
  dev_link.py        Dev-mode only: Claude/Codex config-dir symlinks, dev-mode
                     hook registration, git hooksPath, $AI export. Requires an
                     explicit repo path — never inferred.
  launchers.py       Per selected agent: bin launcher, profile config (with an
                     absolute model_instructions_file rewrite so Codex doesn't
                     need $CODEX_HOME/agents either), worker dir,
                     ASSISTANT_DEFAULT. No agents preselected.
  link_utils.py      Shared make_link/make_copy used by scaffold/launchers/dev_link.
  rc_block.py        Merge-capable managed-block writer: scaffold owns PATH,
                     launchers owns ASSISTANT_DEFAULT, dev_link owns $AI — all
                     three share one physical rc block without clobbering
                     each other on repeated runs.
  uninstall.py       Reverses install side effects; best-effort with a final
                     removed/skipped/left/FAILED report (exit 1 on failures).
                     Leaves OAuth credentials unless --purge; supports
                     --dry-run, --no-pip, --no-git-hooks.
  install_manifest.py Home-scoped record of install side effects
                     (~/.local/state/assistant-tools/install-manifest.json).
                     scaffold/dev_link/launchers record into it; uninstall.py
                     replays it in reverse (exact even across plugin-cache
                     version drift), falling back to heuristics when absent.
```

## Workflow

### 1. Ask the mode question

Before running anything, ask explicitly:

> "Do you want development mode? This wires `~/.claude`/`~/.codex` to a live
> repo checkout so skill/hook edits take effect immediately, instead of a
> static plugin install."

Never infer this from filesystem probes (e.g. whether some assumed path is a
git checkout) — dev mode is an explicit user choice. If yes, ask for the repo
path directly; do not assume it matches wherever this skill's own code is
currently running from. Plugin-mode installs don't need a repo path — the
repo root is derived from wherever the plugin itself is running from, since
there's no separate "live checkout" concept to get wrong there.

### 2. Run Phase 1 (installation)

Use the `scripts-install` interface (`install.py`), which chains:

1. `scaffold` — dispatcher + invoke-skill launchers, PATH. Always runs,
   regardless of mode.
2. `dev-link` — only if dev mode was chosen in step 1. Symlinks, dev-mode
   hooks, git hooksPath, `$AI`.
3. `launchers` — asks which of `assistant`/`collab`/`coauthor`/`tw` to
   install (none preselected — explicit opt-in), then installs the bin
   launcher/profile/worker-dir/`ASSISTANT_DEFAULT` for each chosen agent.

Each of these three is also independently runnable via `scripts-scaffold`,
`scripts-dev-link`, `scripts-launchers` for targeted repairs. On an
unfamiliar machine, pass `--dry-run` to preview without writing anything.

Claude and Codex skill/reference visibility in **plugin mode** already comes
from the plugin loader itself — `dev-link`'s symlinks are a dev-mode
convenience, not something plugin-mode installs need or run.

The installer auto-detects the user's shell on Unix (`zsh` → `.zshrc`, else
`.bashrc`). On Windows it writes PATH and env vars to the user registry
(`HKEY_CURRENT_USER\Environment`) and broadcasts `WM_SETTINGCHANGE` so new
terminals pick up the change immediately.

### 3. Phase 2 — connect remotes, then offer recurring automation

This phase is deliberately not this skill's job to script — no dependency on
any other skill is declared here, and none should be added just to name one
in prose. It's the assistant's own conversational follow-through, using
whatever cloud-storage, calendar, and email skills are actually configured
in this environment.

After Phase 1 completes, ask whether the user wants to connect their cloud
storage, calendar, and email accounts now — framing it as worthwhile in this
session because it unlocks recurring email triage and daily planning
afterward, rather than something to leave for later. If yes, walk through
each account's own setup flow directly (each such skill owns its own OAuth
or credential guidance) — do not duplicate that guidance here.

Then, whether or not remotes were connected now, ask whether the user wants
recurring triage and daily planning set up. If yes, hand this off entirely
to the recurring-automation system's own workflow — it is responsible for
lazily and idempotently ensuring its own prerequisites the first time it's
used. This skill has no further role at that point.

### 4. What the current implementation really does on conflicts

This is important for reliable operator expectations.

#### Existing symlink at destination

- If the destination is already a symlink to the desired target, the installer
  leaves it in place and logs `OK (already linked): ...`.
- If the destination is a symlink to a different target, the installer removes
  it and creates the new symlink.
- There is no interactive prompt.

#### Existing real `skills/` directory at destination

- This case is special for developer-mode skill installs.
- If the existing directory contains redundant per-skill symlinks that already
  point into the canonical repo `skills/` tree, the installer removes those
  redundant entries.
- If it contains unique local entries, the installer moves them into the
  canonical repo `skills/` tree, then replaces the user directory with a
  top-level symlink.
- When possible, preserved local entries are added to the repo-local Git
  exclude file so they do not pollute `git status`.
- If the existing directory contains a conflicting entry name that already
  exists in the canonical repo `skills/` tree with different content/identity,
  the installer leaves the directory in place and reports the conflict for
  manual resolution.

#### Existing real file or directory at destination

- For destinations other than `skills/`, the installer does **not** overwrite
  real files or directories.
- The installer does **not** overwrite it.
- It logs `SKIP (real path exists, not a symlink): ...` and leaves it alone.
- There is no backup, merge, rollback, or conflict-resolution UI in the current
  implementation.

#### Missing source path

- The installer skips that item and logs `SKIP (missing source): ...`.

#### Symlinked `~/.codex`

- `dev_link.py` warns and skips Codex directory links if `CODEX_HOME`
  itself is a symlink.

Do **not** promise merge, backup, replace/keep menus, rollback, or
non-interactive conflict-policy flags unless the code has actually gained them.

### 5. Sanity check

After the installer finishes, reload the environment and verify:

**Linux / macOS**
```bash
source ~/.zshrc    # or ~/.bashrc — the installer prints which one it used
type assistant
```

**Windows**
```cmd
where assistant
```

If the command is not found, the bin dir is not on `PATH`. Check installer
output and open a fresh terminal.

### 6. Basic smoke test

```bash
assistant --help
collab --help
coauthor --help
tw -h       # Unix only; skip on Windows
```

Each help command should exit 0.

### 7. If something fails

If a command fails or is not available on the user's platform:

1. Report the exact error.
2. **Ask the user:** "Would you like me to adapt the scripts for your platform?"
3. If yes, inspect the failing script and propose the minimal change needed.

Do not modify scripts speculatively.

## Default Targets

| Item | Default |
|---|---|
| User rc | `~/.zshrc` (zsh) or `~/.bashrc` (bash/other) — auto-detected; Windows uses registry |
| System rc | `/etc/bash.bashrc` (skipped on Windows) |
| Bin dir | `$HOME/Documents/scripts/bin` |
| Repo root | Dev mode: the path the user supplied. Plugin mode: derived from wherever the plugin is running from. `$AI` itself is only exported by `dev-link` (dev-mode only) — plugin-mode installs never set it; `_agent_launch.py` and `dispatcher` resolve their own repo root from their own file location instead. |
| Workers | `<repo-root>/workers/{assistant,collab,coauthor}` |
| Codex home | `$CODEX_HOME`, or `$HOME/.codex` |
| Claude home | `$CLAUDE_HOME`, or `$HOME/.claude` |
| Git hooks | `<repo-root>/.githooks` |
| Claude session hook | entry merged into `<claude-home>/settings.local.json` |
| Codex session hook | managed block appended to `<codex-home>/config.toml` |

All targets can be overridden with flags — see the `scripts-install` usage string
for the full list.

## Hook Architecture Notes

- Dev-mode hook installation is registry-driven from `llmhooks/registry.py`.
- The shared hook base class is `llmhooks/lib/cross_host.py`.
- The current dispatcher-context hook is `llmhooks/inject_dispatcher_context.py`.
- Dev-mode install writes explicit host selectors such as `--claude` and `--codex`.
- Plugin-mode installs still go through `hooks/hooks.json` and `hooks/inject_dispatcher_context.py`, because one shared plugin hook file serves multiple hosts.

## Uninstall

The uninstall entry point removes the current managed hook registrations too:

- the managed Codex marker block in `config.toml`
- managed Claude hook commands in `settings.local.json`

It understands both the legacy `hooks/inject_dispatcher_context.py` command and
the current explicit `llmhooks/inject_dispatcher_context.py --claude/--codex`
commands.

## Updating Scripts

Because installed commands are symlinks into `bin/`, editing `bin/assistant` or
`bin/tmux-workspace` takes effect immediately. Re-run the installer when:

- setting up a new machine
- repairing broken symlinks
- updating the rc block or git hooks
- adding new profile or launcher links

## Tests and Handoff Checks

For cross-platform validation, use the Python tests rather than shell wrappers:

```bash
python3 -m pytest skills/install-assistant-tools/tests/
```

What they cover:

- `test_codex_install.py`: isolated Codex marketplace install, skill visibility,
  installed package contents, and running `install.py` from the installed plugin
  into a fresh temp environment.
- `test_claude_install.py`: isolated Claude marketplace install, installed
  package contents, and Claude's plugin inventory/details output.
- `test_link_utils.py`: shared `make_link`/`make_copy` helpers.
- `test_rc_block.py`: merge-capable managed-block writer shared by
  `scaffold`/`launchers`/`dev_link`.
- `test_scaffold.py`: dispatcher/invoke-skill launcher installation and PATH.
- `test_launchers.py`: per-agent bin/profile/worker-dir/`ASSISTANT_DEFAULT`
  installation.
- `test_agent_launch.py`: agent `.md` frontmatter/prompt parsing and repo-root
  resolution used by the installed `assistant`/`collab`/`coauthor` launchers.
- `test_dev_link.py`: dry-run behavior, conflict preservation, `skills/`
  migration, symlink replacement, idempotent already-linked paths,
  symlinked-`CODEX_HOME` skipping, git hooks, dev-mode hook registration,
  and the `$AI` export.
- `test_install.py`: Phase-1 orchestration (mode selection, chaining
  `scaffold`/`dev_link`/`launchers`).

Known handoff caveat / TODO:

- `claude plugins validate --strict` still warns about plugin-root `CLAUDE.md`.
- The warning means Claude packages that file but does not treat it as plugin
  project context in the way the validator wants.
- For now, keep it documented rather than silently ignoring it: the current
  Python Claude install test uses non-strict validation and then verifies the
  installed cache contents directly.
- Future cleanup should decide whether to remove or relocate that context so
  strict validation passes cleanly.

## Troubleshooting

**`assistant: command not found`** — bin dir not on PATH. Check the managed rc
block or Windows user environment and then open a new shell.

**`ModuleNotFoundError: No module named '_agent_launch'`** — `_agent_launch.py`
is missing from the bin dir. Re-run the installer and check
`install_bin_for_agent` in `launchers.py`.

**`tw: command not found` on Windows** — expected; tmux is not available on
Windows.

**`.bat` wrappers on Windows** — `assistant.bat`, `collab.bat`, and
`coauthor.bat` are installed automatically alongside the Python launchers.
With the bin dir on `%PATH%`, bare `assistant`, `collab`, and `coauthor` work
in `cmd.exe` and PowerShell without typing `.bat`.

**`py.exe` not found** — install Python from python.org and ensure the Python
Launcher is included.

**Codex warns about temporary-dir PATH aliases in tests** — the Codex CLI may
warn when helper binaries are created under `/tmp`. The Python tests still use
isolated temporary homes and assert real installed-path behavior around that
warning.

## Developer Notes

### Adding a new agent

To add an agent (for example `researcher`):

1. copy `bin/assistant` to `bin/researcher` and update `agent=` / env fallback
2. copy `bin/assistant.bat` to `bin/researcher.bat`
3. add `researcher` to `ALL_AGENTS` and `WORKER_AGENTS` in `launchers.py`
4. add `profiles/researcher.config.toml`
5. add `profiles/researcher_claude_setting.json`
6. re-run the installer (`--agents researcher` or pick it interactively)

### Key contracts

- `<repo-root>` (dev mode: user-supplied; plugin mode: derived from the
  running plugin's own location) controls the default worker directories —
  not `$AI`, which is only exported by `dev-link` as a dev-mode convenience.
- `profiles/*.config.toml` are copied (not linked) into both Codex and Claude
  homes, with `model_instructions_file` rewritten to an absolute path.
- `profiles/*_claude_setting.json` are linked into Claude home only.
- `workers/` contains one default working directory per agent.
