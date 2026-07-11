# Launchers

This page covers the user-facing launcher commands installed by Famulus.

## What They Are

Famulus ships three agent launchers:

- `assistant`
- `collab`
- `coauthor`

Those launchers work with both Claude Code and Codex.
On Windows, the installed commands are `.bat` wrappers that delegate to the
Python launcher bundle copied into the managed bin directory.

By default, they use the backend selected at install time through `ASSISTANT_DEFAULT`, but you can override that per run:

- `assistant --claude`
- `assistant --codex`
- `collab --claude`
- `collab --codex`
- `coauthor --claude`
- `coauthor --codex`

Each launcher starts in its default worker directory under `workers/<agent>`. Use `-l` or `--local` to stay in the current directory instead.

Examples:

- `assistant`
- `assistant --codex`
- `collab --local --claude`
- `coauthor --codex`

Profiles and model settings for these launchers are summarized in [PROFILES.md](../PROFILES.md).

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

`tw` / `tmux-workspace` is Unix-only. On Windows it is skipped by the installer because tmux is not available there.

## Installation

These launchers are installed through the Phase 1 installer described in [docs/installation.md](installation.md).

That installer:

- writes the launcher commands into your bin directory
- copies Windows launcher bundles or symlinks Unix launcher bundles as
  appropriate for the host
- installs the Claude/Codex profile files they rely on
- creates the default worker directories for `assistant`, `collab`, and `coauthor`
- installs `tw` / `tmux-workspace` when the platform supports tmux

If you want the installation and repair details, use [docs/installation.md](installation.md). This page is about how to use the launchers once they exist.
