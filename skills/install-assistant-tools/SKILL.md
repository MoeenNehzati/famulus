---
name: install-assistant-tools
description: Install or update the assistant, collab, coauthor, and tw/tmux-workspace helpers on a machine. Use when the user wants these commands installed, repaired, refreshed, or propagated to another system; when a system lacks them; or when the helper definitions should be updated in shell startup files.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Dependencies:
- cloud-files
- g-calendar

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `scripts-install` — Run the combined installer — wires config dirs to the repo, then installs bin scripts, rc block, and git hooks.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools scripts-install [--dry-run] [--no-claude] [--no-codex] [--bin-dir DIR] [--shell-rc FILE] [--system-shell-rc FILE] [--no-system-shell-rc] [--default-llm {claude,codex}] [--cloud-files-remote-llm-root PATH] [--home DIR] [--claude-home DIR] [--codex-home DIR]`
- `scripts-setup-symlinks` — Wire Claude and Codex config dirs to the canonical AI repo with symlinks, preserving any unique local skill entries.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools scripts-setup-symlinks [--dry-run] [--no-claude] [--no-codex] [--home DIR] [--claude-home DIR] [--codex-home DIR]`
- `scripts-setup-tools` — Install or update bin launchers, worker dirs, profile copies, git hooks, and the managed shell rc block.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools scripts-setup-tools [--dry-run] [--no-system-shell-rc] [--bin-dir DIR] [--shell-rc FILE] [--system-shell-rc FILE] [--default-llm {claude,codex}] [--cloud-files-remote-llm-root PATH] [--home DIR] [--codex-home DIR] [--claude-home DIR]`
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
  _agent_launch.py   Shared launcher logic imported by the three launchers above
  assistant.bat      Windows wrapper (delegates to assistant via py.exe)
  collab.bat         Windows wrapper (delegates to collab via py.exe)
  coauthor.bat       Windows wrapper (delegates to coauthor via py.exe)
scripts/
  install.py         Combined entry point — runs setup_symlinks then setup_tools
  setup_symlinks.py  Wires Claude and Codex config dirs to the repo
  setup_tools.py     Installs bin scripts, profiles, rc block, git hooks
  uninstall.py       Reverses install side effects; best-effort with a final
                     removed/skipped/left/FAILED report (exit 1 on failures).
                     Leaves OAuth credentials unless --purge; supports
                     --dry-run, --no-pip, --no-git-hooks.
  install_manifest.py Home-scoped record of install side effects
                     (~/.local/state/assistant-tools/install-manifest.json).
                     install/setup_* record into it; uninstall.py replays it
                     in reverse (exact even across plugin-cache version
                     drift), falling back to heuristics when absent.
```

## Workflow

### 1. Tell the user what will happen

Before running anything, summarize:

- Claude and Codex config dirs will be wired back to this repo with symlinks.
- Claude and Codex skills will both be wired through `~/.{claude,codex}/skills -> <repo>/skills`.
- If either user `skills/` directory already exists as a real directory, the installer will preserve unique local entries by moving them into the canonical repo `skills/` tree before replacing the directory with a symlink.
- Launcher scripts will be symlinked into a bin dir on `PATH`.
- A `dispatcher` launcher is **generated** into the managed bin dir: it runs
  `script_dispatcher` directly from the repo (`$AI`, with the install-time
  path baked in as fallback for systemd/cron). First-party code is never
  pip-installed — no second copy to drift or break.
- Profile `.config.toml` files are **copied** (not symlinked) into the Codex
  and Claude homes: Codex writes machine-local state (project trust levels,
  trusted hook hashes, keyed by absolute personal paths) back into its config
  file, and a symlink would leak that state into the tracked repo. Existing
  copies are kept as-is to preserve accumulated local state; legacy symlinks
  are replaced by copies.
- A managed rc block or Windows user-environment entry will set `PATH`,
  `ASSISTANT_DEFAULT`, and `$AI`.
- Worker directories will be created if absent.
- Git hooks will be configured.
- LLM session hooks will be registered in `~/.claude/settings.local.json` and `~/.codex/config.toml` for dev-mode operation.
- The live cross-host hook logic lives under `llmhooks/`; plugin installs still use `hooks/` as a compatibility shim.
- `~/.config/cloud-files/config.json` will be written or updated.
- After the core install, the installer can optionally walk through Google
  Drive (`cloud-files`) and Google Calendar (`g-calendar`) OAuth setup.
- Existing symlinks may be replaced if they already point somewhere else.
- Existing real files or directories are **not** overwritten; they are skipped
  with a warning.
- The installer runs lightweight verification at the end.

Ask for confirmation before proceeding.

### 2. Run the combined installer

Use the `scripts-install` interface. On an unfamiliar machine, pass `--dry-run`
to preview without writing anything. Other commonly used flags:

- `--no-claude` / `--no-codex` — skip symlinks for one tool
- `--bin-dir DIR` / `--shell-rc FILE` — override default paths
- `--default-llm {claude,codex}` — set the default backend non-interactively

See the `scripts-install` usage string for the full flag list.

The installer auto-detects the user's shell on Unix (`zsh` → `.zshrc`, else
`.bashrc`). On Windows it writes PATH and env vars to the user registry
(`HKEY_CURRENT_USER\Environment`) and broadcasts `WM_SETTINGCHANGE` so new
terminals pick up the change immediately.

Optional Google services step:

- If `credentials.json` already exists for a service, the installer reports it
  as already configured.
- If `client.json` exists and the user chooses that service, the installer runs
  the service's `setup_oauth.py`.
- If `client.json` is missing, the installer prints where to save it.
- In non-interactive mode, optional Google service setup is skipped.
- Keeping a Google OAuth app in **Testing** may require repeated
  re-authorization; **Publish app** / **In production** is preferred.

### 3. What the current implementation really does on conflicts

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

- `setup_symlinks.py` warns and skips Codex directory links if `CODEX_HOME`
  itself is a symlink.

Do **not** promise merge, backup, replace/keep menus, rollback, or
non-interactive conflict-policy flags unless the code has actually gained them.

### 4. Sanity check

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

### 5. Basic smoke test

```bash
assistant --help
collab --help
coauthor --help
tw -h       # Unix only; skip on Windows
```

Each help command should exit 0.

### 6. If something fails

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
| AI root | Two levels above the skill dir (for example `~/Documents/AI`), exported as `$AI` |
| Workers | `$AI/workers/{assistant,collab,coauthor}` |
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
python3 tests/test_codex_install.py
python3 tests/test_claude_install.py
python3 skills/install-assistant-tools/tests/test_setup_symlinks.py
python3 skills/install-assistant-tools/tests/test_setup_tools_cloud_files.py
python3 skills/install-assistant-tools/tests/test_setup_tools_recurring_env.py
```

What they cover:

- `test_codex_install.py`: isolated Codex marketplace install, skill visibility,
  installed package contents, and running `install.py` from the installed plugin
  into a fresh temp environment.
- `test_claude_install.py`: isolated Claude marketplace install, installed
  package contents, and Claude's plugin inventory/details output.
- `test_setup_symlinks.py`: dry-run behavior, conflict preservation,
  `skills/` migration, symlink replacement, idempotent already-linked paths,
  and symlinked-`CODEX_HOME` skipping.
- `test_setup_tools_cloud_files.py`: cloud-files config writing and optional
  Google OAuth decision paths.

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
is missing from the bin dir. Re-run the installer and check `BIN_SCRIPTS` in
`setup_tools.py`.

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
3. add `researcher` to `AGENTS`, `BIN_SCRIPTS`, `BAT_WRAPPERS`, and
   `VERIFY_CMDS` in `setup_tools.py`
4. add `profiles/researcher.config.toml`
5. add `profiles/researcher_claude_setting.json`
6. re-run the installer

### Key contracts

- `$AI` points to the repo root and controls the default worker directories.
- `profiles/*.config.toml` are linked into both Codex and Claude homes.
- `profiles/*_claude_setting.json` are linked into Claude home only.
- `workers/` contains one default working directory per agent.
