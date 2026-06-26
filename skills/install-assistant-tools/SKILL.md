---
name: install-assistant-tools
description: Install or update the user's assistant and tw/tmux-workspace helpers on a machine. Use when the user wants assistant, assistant -c, tw, or tw -c installed, repaired, refreshed, or propagated to another system; when a system lacks these commands; or when the helper definitions should be updated in user and system bash startup files.
---

# Install Assistant Tools

When this skill is used, begin with:

Skill: install-assistant-tools

Category: automation

Dependencies: none

## Platform Support

This installation is designed for **Linux systems** with bash and standard Unix tools.

If you're installing on a different operating system, you should try to replicate
the same logic in your environment (e.g., updating the appropriate shell rc file,
adjusting paths for your system layout).

## Overview

The skill ships four standalone scripts in `bin/`:

- `bin/assistant` — launches `claude --agent assistant` (secretary: fetch info,
  write, implement easy logic) or `codex --profile assistant` from
  `$AI/workers/assistant`. Pass `-l/--local` to stay in the current directory.
- `bin/collab` — launches `claude --agent collab` (serious coding,
  documentation/learning) or `codex --profile collab` from
  `$AI/workers/collab`. Pass `-l/--local` to stay in the current directory.
- `bin/coauthor` — launches `claude --agent coauthor` (math/research, deep
  thinking) or `codex --profile coauthor` from `$AI/workers/coauthor`.
  Pass `-l/--local` to stay in the current directory.
- `bin/tmux-workspace` — creates or attaches to a tmux workspace; `tw` is an
  alias symlink for it.

The installer symlinks these into a bin directory on PATH and writes a minimal
managed block to the shell rc (PATH export only — no inline function
definitions).

## Layout

```
bin/
  assistant          source script for the assistant command
  collab             source script for the collab command
  coauthor           source script for the coauthor command
  tmux-workspace     source script for tw / tmux-workspace
scripts/
  install_assistant_tools.sh   install bin scripts, profiles, rc block, git hooks
  setup_symlinks.py            wire Claude and Codex config dirs to the repo
```

## Workflow

**Step 1 — Set up config dir symlinks** (new machine or after repo move):

```bash
python3 scripts/setup_symlinks.py
```

This wires the Claude and Codex config directories to the repo so both tools
share the same skills, references, agents, and profiles without separate copies.
Use `--dry-run` to preview, or `--no-claude`/`--no-codex` to skip one tool.
Claude and Codex config dirs are auto-detected from `$CLAUDE_HOME`/`$CODEX_HOME`;
if neither is set nor found at the default path, the script prompts interactively.

**Step 2 — Install bin scripts, rc block, and git hooks:**

```bash
bash scripts/install_assistant_tools.sh
```

The script installs or updates:

- `$bin_dir/assistant` — symlink to `bin/assistant` in this skill directory.
- `$bin_dir/collab` — symlink to `bin/collab`.
- `$bin_dir/coauthor` — symlink to `bin/coauthor`.
- `$bin_dir/tmux-workspace` — symlink to `bin/tmux-workspace`.
- `$bin_dir/tw` — symlink to `bin/tmux-workspace` (alias).
- Each repo-owned profile under `profiles/*.config.toml`, linked into both the
  Codex home and Claude home.
- Each Claude settings file under `profiles/*_claude_setting.json`, linked into
  the Claude home (`$CLAUDE_HOME` or `~/.claude`).
- The repo's Git hook path: `git config core.hooksPath .githooks`, after
  verifying that `.githooks/` exists and marking all files in it executable.
- Legacy repo-owned `coder` launcher/profile symlinks, if present, are removed
  during install.
- A managed PATH block in the user shell rc (and system rc when writable),
  including `export AI=<repo-root>` so all scripts can locate worker dirs.
- Worker directories `$AI/workers/{assistant,collab,coauthor}` (created if absent).
- `~/.config/environment.d/20-ai-agent.conf` — sets `AI_AGENT_COMMAND_TEMPLATE`
  for the systemd user environment, required by automated skill jobs so they
  know how to invoke Claude.

The managed block written to the rc file is intentionally minimal:

```bash
# >>> assistant-tools >>>
export PATH="/path/to/bin:$PATH"
# <<< assistant-tools <<<
```

After symlinking, the installer runs `assistant --help` and `tw --help` to
verify the installed scripts are reachable and executable. Failures are
reported as warnings.

## Default Targets

- User rc: `$HOME/.bashrc`
- System rc: `/etc/bash.bashrc`
- Bin dir: `$HOME/Documents/scripts/bin`
- Source bin: `<skill-dir>/bin/`
- AI root: two levels above the skill dir (e.g. `~/Documents/AI`); exported as `$AI`
- Workers: `$AI/workers/{assistant,collab,coauthor}`
- Codex home: `$CODEX_HOME`, or `$HOME/.codex` when unset
- Claude home: `$CLAUDE_HOME`, or `$HOME/.claude` when unset
- Git hooks path: `<repo-root>/.githooks`

## Install or Update

From the skill directory, run:

```bash
bash scripts/install_assistant_tools.sh
```

If the system rc is not writable, the script updates the user rc and prints a
warning. To update `/etc/bash.bashrc`, rerun with appropriate privileges:

```bash
sudo bash scripts/install_assistant_tools.sh --home /home/USER
```

Use `--dry-run` before writing on unfamiliar machines:

```bash
bash scripts/install_assistant_tools.sh --dry-run
```

Use explicit paths for a nonstandard layout:

```bash
bash scripts/install_assistant_tools.sh \
  --bin-dir /path/to/bin \
  --codex-home /path/to/codex-home \
  --claude-home /path/to/claude-home \
  --shell-rc /path/to/user/.bashrc \
  --system-shell-rc /path/to/system/bashrc
```

Pass `--no-system-shell-rc` to update only the current user's shell rc.

## Updating Scripts

Because the installed commands are symlinks into `bin/`, editing
`bin/assistant` or `bin/tmux-workspace` in place takes effect immediately with
no reinstall needed. Re-run the installer only when adding a new machine,
repairing broken symlinks, or updating the rc block.

## Validation

After installation the installer runs `assistant --help` and `tw --help`
automatically. To validate manually:

```bash
source ~/.bashrc
type assistant
assistant --help
tw -h
```

Expected behavior:

- `type assistant` reports a file (not a function).
- `assistant --help` prints usage and exits 0.
- `collab --help` prints usage and exits 0.
- `coauthor --help` prints usage and exits 0.
- `tw -h` documents `-c|--codex`.
- `assistant -c` launches Codex from the configured assistant directory with
  `--profile assistant`.
- `collab --codex` launches Codex from the current directory with
  `--profile collab`.
- `coauthor --codex` launches Codex from the current directory with
  `--profile coauthor`.
- `tw -c` creates or attaches to the Codex-specific tmux workspace.
- Each repo-owned profile in `profiles/*.config.toml` has matching symlinks
  under `$CODEX_HOME` or `~/.codex`, and under `$CLAUDE_HOME` or `~/.claude`.
- Each Claude settings file in `profiles/*_claude_setting.json` has a matching
  symlink under `$CLAUDE_HOME` or `~/.claude`.
- `git -C <repo-root> config --get core.hooksPath` prints `.githooks`.

Do not run `assistant -c` or `tw -c` as validation unless the user wants an
interactive Codex/tmux session launched.

## AI Root Variable

The installer exports `AI=<repo-root>` in the managed shell rc block, making
`$AI/skills`, `$AI/workers`, etc. available in any terminal. The value is the
absolute path two levels above the `install-assistant-tools` skill directory.
No manual configuration is needed.
