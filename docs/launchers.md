# Launchers

This page covers the user-facing launcher commands configured by the optional
`install-launchers` feature.

## What They Are

Famulus ships three interactive agent launchers:

- `assistant`
- `collab`
- `coauthor`

Those launchers work with both Claude Code and Codex. Selected launcher setup
also prepares non-interactive background support for explicitly enabled
recurring work; it is not an ordinary interactive entry point. On Windows, the
feature-owned commands are `.bat` wrappers.

The context's `launchers.json` is the durable owner of the default backend.
`ASSISTANT_DEFAULT` is accepted only as an override for the current process;
do not persist it in shell or scheduler configuration. You can also override
the backend per run:

- `assistant --claude`
- `assistant --codex`
- `collab --claude`
- `collab --codex`
- `coauthor --claude`
- `coauthor --codex`

Each launcher starts in its context-owned worker directory. Standard workers
live below the platform Famulus state root; development workers live below the
checkout's `.famulus/` tree. Use `-l` or `--local` to stay in the current
directory instead.

Each command captures the selected plugin root and canonical Python executable
when `install-launchers` configures it. Launcher operation does not discover a
old installation state or choose another plugin from the caller's current directory.

Examples:

- `assistant`
- `assistant --codex`
- `collab --local --claude`
- `coauthor --codex`

Profiles and model settings for these launchers are summarized in [PROFILES.md](../PROFILES.md).

## Unattended launcher

`background_run` has its own instructions, profile, model settings, and worker
directory so scheduled work does not silently inherit an interactive
assistant's configuration. Launcher setup creates those resources but does not
create or enable a schedule.

When an explicitly enabled recurring job invokes an agent, it uses Claude's
`bypassPermissions` mode or Codex's
`--dangerously-bypass-approvals-and-sandbox` mode. This is necessary for an
unattended process that cannot answer approval prompts, but it also removes an
important interactive safety boundary. Review the job definition, skill,
working directory, account permissions, and schedule before enabling it. See
[Security and privacy](security-and-privacy.md#authorization-and-confirmation-boundaries).

## Tmux Wrapper

Famulus also ships a tmux wrapper:

- `tmux-workspace`
- `tw` — a short alias for the same command

This wrapper sits on top of the agent launchers. Its default `llm` template opens a customized tmux session with:

- an `assistant` pane on the left
- two terminal panes on the right
- a `scratch` window
- a `logs` window

You can choose the backend for the assistant pane with the same host selectors:

- `tw --claude`
- `tw --codex`

Examples:

- `tw`
- `tw --codex`
- `tw paper`
- `tw --claude paper ~/projects/paper`

The wrapper also has `shell` and `raw` templates:

- `tw shell scratch`
- `tw raw -- list-sessions`

`tw` / `tmux-workspace` is Unix-only. It is unavailable on Windows because
tmux is not available there.

## Installation

Ask the host to use `install-launchers` and name the exact subset you want.
The feature:

- writes the launcher commands into your bin directory
- writes the host-appropriate command form
- installs the host profile files they rely on
- creates context-owned interactive worker directories plus the required
  `background_run` worker directory
- installs `tw` / `tmux-workspace` when the platform supports tmux

It does not default to all launchers or enable recurring work. Rerun
`install-launchers` after a plugin cache or path update to refresh the selected
launchers' captured plugin root; there is no generic updater.
