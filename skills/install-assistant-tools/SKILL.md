---
name: install-assistant-tools
description: Install or update the user's assistant and tw/tmux-workspace helpers on a machine. Use when the user wants assistant, assistant -c, tw, or tw -c installed, repaired, refreshed, or propagated to another system; when a system lacks these commands; or when the helper definitions should be updated in user and system bash startup files.
---

# Install Assistant Tools

## Overview

The skill ships two standalone scripts in `bin/`:

- `bin/assistant` — launches `claude --agent assistant` (or `codex --profile
  assistant` with `-c`) from the assistant project directory.
- `bin/tmux-workspace` — creates or attaches to a tmux workspace; `tw` is an
  alias symlink for it.

The installer symlinks these into a bin directory on PATH and writes a minimal
managed block to the shell rc (PATH export only — no inline function
definitions).

## Layout

```
bin/
  assistant          source script for the assistant command
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
- `$bin_dir/tmux-workspace` — symlink to `bin/tmux-workspace`.
- `$bin_dir/tw` — symlink to `bin/tmux-workspace` (alias).
- Each repo-owned Codex profile under `profiles/*.config.toml`, linked into
  the Codex home so `codex --profile <name>` can load it.
- A managed PATH block in the user shell rc (and system rc when writable).
- `~/.config/environment.d/20-ai-agent.conf` — sets `AI_AGENT_COMMAND_TEMPLATE`
  for the systemd user environment, required by the `recurring-tasks` skill so
  automated jobs (email-triage, daily-plan, etc.) know how to invoke Claude.

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
- `tw -h` documents `-c|--codex`.
- `assistant -c` launches Codex from the configured assistant directory with
  `--profile assistant`.
- `tw -c` creates or attaches to the Codex-specific tmux workspace.
- Each repo-owned profile in `profiles/*.config.toml` has a matching symlink
  under `$CODEX_HOME` or `~/.codex`.

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
