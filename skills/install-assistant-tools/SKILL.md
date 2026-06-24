---
name: install-assistant-tools
description: Install or update the user's assistant and tw/tmux-workspace helpers on a machine. Use when the user wants assistant, assistant -c, tw, or tw -c installed, repaired, refreshed, or propagated to another system; when a system lacks these commands; or when the helper definitions should be updated in user and system bash startup files.
---

# Install Assistant Tools

When this skill is used, begin with:

Skill: install-assistant-tools

Category: automation

Dependencies: none

## Overview

The skill ships three standalone scripts in `bin/`:

- `bin/assistant` — launches `claude --agent assistant` (or `codex --profile
  assistant` with `-c`) from the assistant project directory.
- `bin/collab` — launches `claude --agent collab` or `codex --profile collab`
  from the current directory.
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
  tmux-workspace     source script for tw / tmux-workspace
scripts/
  install_assistant_tools.sh
```

## Workflow

Run the bundled installer script from anywhere:

```bash
bash scripts/install_assistant_tools.sh
```

The script installs or updates:

- `$bin_dir/assistant` — symlink to `bin/assistant` in this skill directory.
- `$bin_dir/collab` — symlink to `bin/collab`.
- `$bin_dir/tmux-workspace` — symlink to `bin/tmux-workspace`.
- `$bin_dir/tw` — symlink to `bin/tmux-workspace` (alias).
- Each repo-owned profile under `profiles/*.config.toml`, linked into both the
  Codex home and Claude home.
- The repo's Git hook path: `git config core.hooksPath .githooks`, after
  verifying that `.githooks/pre-commit`, `.githooks/git/check-not-detached`,
  `.githooks/skill/check-names`, `.githooks/skill/check-dependencies`, and
  `.githooks/pre-push` exist and are executable.
- Legacy repo-owned `coder` launcher/profile symlinks, if present, are removed
  during install.
- A managed PATH block in the user shell rc (and system rc when writable).
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
- `tw -h` documents `-c|--codex`.
- `assistant -c` launches Codex from the configured assistant directory with
  `--profile assistant`.
- `collab --codex` launches Codex from the current directory with
  `--profile collab`.
- `tw -c` creates or attaches to the Codex-specific tmux workspace.
- Each repo-owned profile in `profiles/*.config.toml` has matching symlinks
  under `$CODEX_HOME` or `~/.codex`, and under `$CLAUDE_HOME` or `~/.claude`.
- `git -C <repo-root> config --get core.hooksPath` prints `.githooks`.

Do not run `assistant -c` or `tw -c` as validation unless the user wants an
interactive Codex/tmux session launched.

## Optional: Export Package Path Variable

It is useful to export a shell variable pointing to the AI package root (the
directory containing this skill, references, profiles, and related assets):

```bash
export AI="$HOME/Documents/AI"
```

Add this to `~/.bashrc` outside the managed block. Then `ls $AI/skills`,
`cd $AI`, etc. work without typing the full path. Adjust if the package lives
elsewhere.
