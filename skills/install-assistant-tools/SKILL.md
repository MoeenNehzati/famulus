---
name: install-assistant-tools
description: Install or update the user's assistant shell wrapper and tw/tmux-workspace helper on a machine. Use when the user wants assistant, assistant -c, tw, or tw -c installed, repaired, refreshed, or propagated to another system; when a system lacks these commands; or when the helper definitions should be updated in user and system bash startup files.
---

# Install Assistant Tools

## Workflow

Use the bundled installer script:

```bash
scripts/install_assistant_tools.sh
```

The script installs or updates:

- `assistant` as a bash function.
- `assistant -c` and `assistant --codex`, which run `codex` from the assistant project directory.
- Plain `assistant`, which runs `claude --agent assistant` from the assistant project directory.
- `assistant -c` and `assistant --codex`, which run `codex --profile assistant`
  from the assistant project directory.
- `tmux-workspace` and the `tw` symlink.
- `tw -c` and `tw --codex`, which start the assistant pane with `assistant -c`.
- `tw -c [name]`, which attaches to the existing Codex-backed session
  `[name]-codex` when present, or creates it when absent, without attaching to
  a same-name Claude-backed session.
- Each repo-owned Codex profile under `profiles/*.config.toml`, linked into
  the Codex home so `codex --profile <name>` can load it.
- A PATH entry for the helper bin directory.
- A managed block in the user shell rc and, when writable, the system bash rc.
- `~/.config/environment.d/20-ai-agent.conf` — sets `AI_AGENT_COMMAND_TEMPLATE`
  for the systemd user environment, required by the `recurring-tasks` skill so
  automated jobs (email-triage, daily-plan, etc.) know how to invoke Claude.

Default targets:

- User rc: `$HOME/.bashrc`
- System rc: `/etc/bash.bashrc`
- Helper bin dir: `$HOME/Documents/scripts/bin`
- Assistant project dir: `$HOME/Documents/assistant`
- Codex home: `$CODEX_HOME`, or `$HOME/.codex` when `CODEX_HOME` is unset

## Install Or Update

From the skill directory, run:

```bash
bash scripts/install_assistant_tools.sh
```

If the system rc is not writable, the script updates the user rc and prints a warning. To update `/etc/bash.bashrc`, rerun with appropriate privileges, preserving the intended user's paths:

```bash
sudo bash scripts/install_assistant_tools.sh --home /home/USER
```

Use `--dry-run` before writing on unfamiliar machines:

```bash
bash scripts/install_assistant_tools.sh --dry-run
```

Use explicit paths when installing for a nonstandard layout:

```bash
bash scripts/install_assistant_tools.sh \
  --assistant-dir /path/to/assistant \
  --bin-dir /path/to/bin \
  --codex-home /path/to/codex-home \
  --shell-rc /path/to/user/.bashrc \
  --system-shell-rc /path/to/system/bashrc
```

Pass `--no-system-shell-rc` when the user only wants the current user's shell updated.

## Validation

After installation, run:

```bash
source ~/.bashrc
type assistant
tw -h
```

Expected behavior:

- `type assistant` reports a function.
- `tw -h` documents `-c|--codex`.
- `assistant -c` launches Codex from the configured assistant directory with
  `--profile assistant`.
- `tw -c` creates or attaches to the Codex-specific tmux workspace and starts
  `assistant -c` in the assistant pane when creating a new session.
- Each repo-owned profile in `profiles/*.config.toml` has a matching symlink
  under `$CODEX_HOME` or `~/.codex`.

Do not run `assistant -c` or `tw -c` as validation unless the user wants an interactive Codex/tmux session launched.
