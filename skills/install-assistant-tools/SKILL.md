---
name: install-assistant-tools
description: Install or update the assistant, collab, coauthor, and tw/tmux-workspace helpers on a machine. Use when the user wants these commands installed, repaired, refreshed, or propagated to another system; when a system lacks them; or when the helper definitions should be updated in shell startup files.
---

# Install Assistant Tools

When this skill is used, begin with:

Skill: install-assistant-tools

Category: automation

Dependencies:
- cloud-files

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
```

## Workflow

### 1. Tell the user what will happen

Before running anything, summarize:

- Config dir symlinks will be created so Claude and Codex share skills, agents,
  and profiles from this repo (no duplicate copies).
- Launcher scripts will be symlinked into a bin directory on PATH.
- A minimal block will be written to the shell rc exporting PATH and `$AI`.
- Worker directories will be created if absent.
- Git hooks will be configured.
- `~/.config/cloud-files/config.json` will be written.
- If `~/.config/cloud-files/client.json` already exists, the installer will
  launch the browser-based Google Drive authorization step; otherwise it will
  tell the user how to get that file and where to save it.
- The installer will run basic checks at the end to confirm everything works.

Ask for confirmation before proceeding.

### 2. Run the combined installer

| Platform | Command |
|---|---|
| Linux / macOS | `python3 scripts/install.py` |
| Windows | `py scripts\install.py` |

Use `--dry-run` on an unfamiliar machine to preview without writing:

```bash
python3 scripts/install.py --dry-run   # Linux/macOS
py scripts\install.py --dry-run        # Windows
```

The installer auto-detects the user's shell on Unix (`zsh` → `.zshrc`, else
`.bashrc`). On Windows it writes PATH and env vars to the user registry
(`HKEY_CURRENT_USER\Environment`) and broadcasts `WM_SETTINGCHANGE` so new
terminals pick up the change immediately — no reboot needed.

The scripts are self-documenting — check their inline comments for what each
step does and why.

If the Google Drive credentials are missing, the installer handles first-run
setup like this:

- If `~/.config/cloud-files/client.json` is missing, it tells the user to
  download a Google OAuth client JSON for a Desktop app and save it there.
- On interactive runs, it then waits for that file and launches the browser
  authorization step in the same install session. On non-interactive runs, it
  stops after printing the instructions.
- Once the client JSON exists, it runs the browser-based authorization helper,
  which writes `~/.config/cloud-files/credentials.json`.
- The upload/download/delete smoke test stays under the file-storage skill's
  own tests, not in this installer.

### 3. Sanity check

After the installer finishes, reload the shell environment and verify:

**Linux / macOS**
```bash
source ~/.zshrc    # or ~/.bashrc — the installer prints which one it used
type assistant     # should report a file, not a function
```

**Windows** (open a new terminal, then):
```cmd
where assistant
```

If the command is not found, the bin dir is not on PATH. Check the installer
output for errors, then open a fresh terminal (the registry update requires a
new session).

### 4. Basic smoke test

```bash
assistant --help
collab --help
coauthor --help
tw -h       # Unix only; skip on Windows
```

Each `--help` should print usage and exit 0. If any command is not found or
errors, see "Troubleshooting" below.

### 5. If something fails

If a command fails or is not available on the user's platform:

1. Report the exact error.
2. **Ask the user:** "Would you like me to adapt the scripts for your platform?"
3. If yes, inspect the failing script and propose the minimal change needed.

Do not modify scripts speculatively — only on explicit user approval.

## Default Targets

| Item | Default |
|---|---|
| User rc | `~/.zshrc` (zsh) or `~/.bashrc` (bash/other) — auto-detected; Windows uses registry |
| System rc | `/etc/bash.bashrc` (skipped on Windows) |
| Bin dir | `$HOME/Documents/scripts/bin` |
| AI root | Two levels above the skill dir (e.g. `~/Documents/AI`), exported as `$AI` |
| Workers | `$AI/workers/{assistant,collab,coauthor}` |
| Codex home | `$CODEX_HOME`, or `$HOME/.codex` |
| Claude home | `$CLAUDE_HOME`, or `$HOME/.claude` |
| Git hooks | `<repo-root>/.githooks` |

All targets can be overridden with flags — run `python3 scripts/install.py --help`
for the full list.

## Updating Scripts

Because installed commands are symlinks into `bin/`, editing `bin/assistant` or
`bin/tmux-workspace` in place takes effect immediately — no reinstall needed.
Re-run the installer only when:

- Setting up a new machine
- Repairing broken symlinks
- Updating the rc block or git hooks

## Troubleshooting

**`assistant: command not found`** — bin dir not on PATH. Check rc block was
written and a new terminal or `source ~/.bashrc` was run.

**`ModuleNotFoundError: No module named '_agent_launch'`** — `_agent_launch.py`
is missing from the bin dir. Re-run the installer; check `BIN_SCRIPTS` in
`setup_tools.py` includes it.

**`tw: command not found` on Windows** — expected; tmux is not available on
Windows. Skip `tw` checks on that platform.

**`.bat` wrappers on Windows** — `assistant.bat`, `collab.bat`, and
`coauthor.bat` are installed automatically alongside the Python launchers. With
the bin dir on `%PATH%`, bare `assistant`, `collab`, and `coauthor` work in
`cmd.exe` and PowerShell without typing `.bat`. The Python Launcher (`py.exe`)
must be available — it ships with standard Python installs from python.org.

**`py.exe` not found** — install Python from python.org and ensure "Install
Python Launcher" is checked during setup.

## Developer Notes

### Adding a new agent

To add an agent (e.g. `researcher`):

1. **`bin/researcher`** — copy `bin/assistant`, change `agent=` and the env var
2. **`bin/researcher.bat`** — copy `bin/assistant.bat`, change the script name
3. **`setup_tools.py`** — add `"researcher"` to `AGENTS`, `BIN_SCRIPTS`,
   `BAT_WRAPPERS`, and `VERIFY_CMDS`
4. **`profiles/researcher.config.toml`** — Codex profile (linked by installer)
5. **`profiles/researcher_claude_setting.json`** — Claude settings (linked by installer)

Re-run the installer after to create the new symlinks and worker directory.

### Key contracts

- **`$AI`** — set by the installer to the repo root. All bin scripts default the
  working directory to `$AI/workers/<agent>`. Pass `-l/--local` to stay in the
  current directory instead.
- **`profiles/`** — the installer links every `*.config.toml` file into both
  Codex and Claude homes, and every `*_claude_setting.json` into Claude home
  only. Naming convention: `<agent>.config.toml` and `<agent>_claude_setting.json`.
- **`workers/`** — one subdirectory per agent, created empty by the installer.
  These are the agents' default working directories and can accumulate project
  files over time.
