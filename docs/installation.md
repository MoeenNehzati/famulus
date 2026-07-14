# Installation Guide

This document is the detailed, user-facing companion to
[skills/install-assistant-tools/SKILL.md](../skills/install-assistant-tools/SKILL.md). That file tells the assistant how
to *run* the installer conversationally; this file is for a human (or an
assistant debugging a broken install) who wants to understand exactly what
each script does, what every flag means, and how to diagnose a problem.

If you just want the installer to walk you through setup, ask your assistant
to install/repair the assistant tools — it will invoke the scripts described
here via `dispatcher`. Read this file directly only when something needs
closer inspection than the conversational flow gives you.

If the commands are already installed and you just want to know how to use
`assistant`, `collab`, `coauthor`, or `tw`, start with [docs/launchers.md](launchers.md).

Important distinction:

- installing the Famulus plugin makes the skill package available to Claude Code or Codex
- running the Phase 1 installer below is what writes local launchers such as `dispatcher` and `invoke-skill`, and adds them to `PATH`

So even in plugin mode, the local scaffold step is still the route that gives you a working bare `dispatcher` command on your machine.

---

## 1. The two install modes

Every install is either **plugin mode** or **dev mode**. This choice is never
inferred from the filesystem — you (or the assistant on your behalf) are
always asked explicitly.

| | Plugin mode | Dev mode |
|---|---|---|
| What it's for | Using the skill suite as an installed package | Editing skills/hooks and seeing changes take effect immediately |
| Repo root | Derived automatically from wherever the plugin itself is running from | You supply the path explicitly |
| `~/.claude`/`~/.codex` config-dir symlinks | Not created — the plugin loader already provides skill/reference visibility | Created (`skills`, `references`, `agents`, `CLAUDE.md`/`AGENTS.md`) |
| Dev-mode hooks, git `core.hooksPath`, `$AI` env var | Not set | Set |
| Agent launchers (`assistant`/`collab`/`coauthor`/`tw`) | Work the same either way | Work the same either way |

Agent launchers work identically in both modes: Codex profile files get an
**absolute** `model_instructions_file` path baked in at install time (instead
of a path resolved relative to `$CODEX_HOME`), and the Claude launcher builds
its `--agents` JSON definition from the repo's `agents/<name>.md` directly —
neither needs `$CODEX_HOME/agents` or `$CLAUDE_HOME/agents` to exist. Only the
*symlink-based* dev convenience (editing a skill file and having it reflected
without a copy step) is dev-mode-only.

---

## 2. What actually gets installed (Phase 1)

The installer is split into three scripts, chained by `install.py`:

```
install.py
  ├─ scaffold.py    (always runs)
  ├─ dev_link.py    (only if dev mode was chosen)
  └─ launchers.py   (installs whichever agents you selected)
```

### `scaffold.py` — the universal floor

Runs in every install, regardless of mode or which agents you want. Installs:

- The `dispatcher` launcher — generated into the managed bin directory as
  `<bin-dir>/dispatcher` on Unix-like hosts and `<bin-dir>/dispatcher.bat` on
  Windows. Every skill's `SKILL.md` invokes its own scripts through this
  command (`dispatcher --caller-skill <skill> <skill> <interface> ...`), so
  this is the one piece of scaffolding almost everything else structurally
  depends on.
- The `invoke-skill` launcher — used by `recurring-tasks` scheduler jobs to
  invoke a skill by name without hardcoding an absolute path. This is generated
  as `<bin-dir>/invoke-skill` on Unix-like hosts and
  `<bin-dir>/invoke-skill.bat` on Windows.
- Required third-party Python packages from
  `references/blueprint/runtime_dependencies.json`, generated from executable
  interface dependency declarations. First-party code (`script_dispatcher`
  itself) is deliberately **not** pip-installed — it runs straight from the
  repo via a path baked into the `dispatcher` launcher at generation time, so
  there's never a second copy to drift out of sync. Unix launchers re-exec the
  Python runtime used by the installer, keeping those packages and dispatcher
  execution on the same interpreter. Linux recurring units use that runtime
  and capture the installed launcher directory ahead of other PATH entries.
- `PATH` — adds `<bin-dir>` to your shell rc (or the Windows registry) so
  `dispatcher` and the agent launchers resolve as bare commands.

At the end of scaffold, the installer prints a capability report for shared
launchers. If a required capability such as `dispatcher` fails or is skipped,
scaffold exits nonzero and the phase-1 orchestrator stops before `dev_link.py`
or `launchers.py` runs. Platform-scoped capabilities that are unsupported on a
host are reported with the affected workflows named, but they do not block the
universal dispatcher floor. `--dry-run` prints the same capability report
without writing files.

### `dev_link.py` — dev mode only

Only runs if you said yes to dev mode and gave a repo path. Installs:

- Symlinks: `~/.claude/skills → <repo>/skills`, plus the shared `references`,
  `agents`, and instruction files. Codex keeps `~/.codex/skills` as a real
  directory and links each repo skill into it individually, preserving its
  runtime-owned `.system` directory. Codex profile configs are also linked.
- Dev-mode session hooks in `~/.claude/settings.local.json` and
  `~/.codex/config.toml`, driven by the registry in [llmhooks/registry.py](../llmhooks/registry.py).
- `git config core.hooksPath .githooks` in the repo (skipped with a note if
  the given path isn't actually a git checkout).
- `$AI` in your shell rc, pointing at the repo root.

`~/.codex` itself must be a real directory, not a symlink — Codex's sandbox
can reject mounts that cross a writable symlink at the home-directory
boundary. `dev_link.py` detects this and warns rather than failing silently.

### `launchers.py` — per agent, explicit opt-in

No agent is preselected — you choose from `assistant`, `collab`, `coauthor`,
`tw`. For each one chosen, installs:

- The bin launcher. Unix-like hosts install editable symlinks into the repo's
  `bin/`; Windows copies the launcher bundle into the managed bin directory so
  supported launchers do not depend on Developer Mode or administrator symlink
  privileges.
- Its profile (`profiles/<agent>.config.toml`) copied — not symlinked — into
  both `~/.codex` and `~/.claude`. Copied because Codex writes machine-local
  state (project trust levels, trusted hook hashes) back into that file; a
  symlink would leak that state into the tracked repo. An existing copy is
  left alone to preserve accumulated local state.
- Its worker directory (`<repo>/workers/<agent>`).
- `ASSISTANT_DEFAULT` in your shell rc (which backend — `claude` or `codex`
  — a launcher uses when you don't pass `--claude`/`--codex` explicitly).
- A post-install verification pass: runs `<agent> --help` for each agent you
  just installed and reports `OK`/`FAIL` per command.

`tw` installs both `tmux-workspace` and the `tw` alias on Unix-like hosts. It
is skipped on Windows because tmux is not available there.

---

## 3. What Phase 1 deliberately does *not* do

By design, `install.py` stops once scaffold/dev-link/launchers finish. It
does not:

- Connect any external account (Google Drive, Google Calendar, email). Each
  of those has its own OAuth/credential setup, owned by that skill itself.
- Set up recurring automation (scheduled triage, daily planning). That's the
  automation skill's own lazy, on-demand responsibility — it checks/writes
  its own prerequisites the first time you actually ask for a scheduled job,
  not upfront during install.

This is deliberate: `install-assistant-tools` shouldn't need to know about
every skill that might eventually want post-install setup. If you're
debugging "why didn't the installer connect my calendar" — it isn't supposed
to. That happens afterward, conversationally, on request.

---

## 4. Full CLI reference

Every script below can also be run directly for a targeted repair instead of
going through `install.py`. All accept `--dry-run` to preview without writing
anything.

### `install.py` (orchestrator)

```
install.py [--home DIR] [--bin-dir DIR] [--shell-rc FILE]
           [--codex-home DIR] [--claude-home DIR] [--dry-run]
           [--non-interactive]
           [--dev-mode | --no-dev-mode] [--repo-path DIR]
           [--agents LIST] [--default-llm {claude,codex}]
```

| Flag | Meaning |
|---|---|
| `--home DIR` | Home directory override (default: platform home) |
| `--bin-dir DIR` | Where launchers go (default: `~/Documents/scripts/bin`) |
| `--shell-rc FILE` | Shell rc file to manage (default: auto-detected `~/.zshrc` or `~/.bashrc`; Windows uses the registry instead) |
| `--codex-home DIR` / `--claude-home DIR` | Override Codex/Claude config dirs (default: `$CODEX_HOME`/`$CLAUDE_HOME`, else `~/.codex`/`~/.claude`) |
| `--dry-run` | Print planned actions, write nothing |
| `--non-interactive` | Never prompt. Requires `--dev-mode`/`--no-dev-mode` explicitly, and `--repo-path` if dev mode is chosen. Without this flag, missing choices are prompted for interactively |
| `--dev-mode` / `--no-dev-mode` | Explicit mode choice (mutually exclusive). Omit to be prompted |
| `--repo-path DIR` | Repo checkout path, required if `--dev-mode` is chosen non-interactively |
| `--agents LIST` | Comma-separated subset of `assistant,collab,coauthor,tw`. Omit to be prompted; empty in non-interactive mode installs no agents |
| `--default-llm {claude,codex}` | Default backend for the chosen agents. Omit to be prompted; defaults to `claude` in non-interactive mode |

**Non-interactive example** (e.g. a provisioning script):

```bash
python3 _rtx/_phase_entry.py --non-interactive --no-dev-mode \
  --agents assistant,collab,coauthor,tw --default-llm claude
```

**Dev-mode non-interactive example:**

```bash
python3 _rtx/_phase_entry.py --non-interactive --dev-mode \
  --repo-path ~/Documents/AI --agents assistant --default-llm claude
```

### `scaffold.py`

```
scaffold.py --repo-root DIR [--home DIR] [--bin-dir DIR]
            [--shell-rc FILE] [--dry-run]
```

`--repo-root` is required — this is the one script argument `install.py`
always supplies for you (auto-derived in plugin mode, user-supplied in dev
mode). Run it standalone only if you need to repair the `dispatcher`/
`invoke-skill` launchers or PATH without touching anything else.

### `dev_link.py`

```
dev_link.py --repo-root DIR [--home DIR] [--claude-home DIR]
            [--codex-home DIR] [--shell-rc FILE]
            [--no-claude] [--no-codex] [--dry-run]
```

`--repo-root` is required and must be a real path you provide — this script
never guesses it from its own location. `--no-claude`/`--no-codex` skip one
side if you only use one host.

### `launchers.py`

```
launchers.py --repo-root DIR [--agents LIST] [--home DIR]
             [--bin-dir DIR] [--codex-home DIR] [--claude-home DIR]
             [--shell-rc FILE] [--default-llm {claude,codex}] [--dry-run]
```

`--agents` defaults to none — you must pass it explicitly to install any
launcher. Safe to re-run with a different `--agents` list to add more agents
later; already-installed agents are left alone (idempotent).

### `uninstall.py`

```
uninstall.py [--home DIR] [--claude-home DIR] [--codex-home DIR]
             [--bin-dir DIR] [--shell-rc FILE] [--system-shell-rc FILE]
             [--no-system-shell-rc] [--repo-root DIR] [--manifest FILE]
             [--no-pip] [--no-git-hooks] [--purge] [--dry-run]
```

Manifest-based only — every install step above records what it did in
`<home>/.local/state/assistant-tools/install-manifest.json`, and uninstall
replays exactly those entries in reverse. If that manifest is missing (e.g.
deleted by hand, or a pre-manifest install), uninstall refuses outright and
asks you to re-run the installer once first (idempotently) to regenerate it,
rather than guessing at what to remove by filename pattern.

| Flag | Meaning |
|---|---|
| `--manifest FILE` | Use a manifest at a non-default path |
| `--no-pip` | Don't uninstall the `script_dispatcher` pip package (irrelevant here since it's never pip-installed by this installer, but the flag exists for compatibility) |
| `--no-git-hooks` | Don't unset `git config core.hooksPath` |
| `--purge` | Also remove OAuth credentials/configs under `~/.config/cloud-files` and `~/.config/g-calendar` (left alone by default) |

**Never reversed** (reported at the end, not silently dropped): local skills
that were migrated into the repo's `skills/` tree during install (your
content, not the installer's), worker directories (may contain session
data), and installed Python dependencies.

Exits non-zero if any step failed — always check the final report, which
lists every action as removed / skipped / left / **FAILED**.

---

## 5. Default paths

| Item | Default |
|---|---|
| Bin dir | `$HOME/Documents/scripts/bin` |
| User shell rc | `~/.zshrc` if `$SHELL` contains `zsh`, else `~/.bashrc`. Windows: user registry (`HKEY_CURRENT_USER\Environment`) instead |
| Codex home | `$CODEX_HOME`, else `~/.codex` |
| Claude home | `$CLAUDE_HOME`, else `~/.claude` |
| Worker dirs | `<repo-root>/workers/{assistant,collab,coauthor}` |
| Git hooks | `<repo-root>/.githooks` (dev mode only) |
| Install manifest | `<home>/.local/state/assistant-tools/install-manifest.json` |

`$AI` is **not** in this table on purpose — it's only ever set by `dev_link.py`
(dev mode). Plugin-mode installs never export it; `dispatcher` and
`_agent_launch.py` each resolve their own repo root from their own file
location instead of depending on it.

---

## 6. Verifying an install

After any install, confirm the basics:

```bash
# Is the bin dir on PATH?
type assistant        # macOS/Linux
where assistant        # Windows (cmd/PowerShell)

# Smoke test each installed agent
assistant --help
collab --help
coauthor --help
tw -h                   # Unix only

# Confirm dispatcher resolves
dispatcher --help

# In a repo checkout, also verify dispatcher can route every converted
# Python machine interface to its subprocess entrypoint.
python3 -m pytest -q tests/test_dispatcher_route_smoke.py
```

`launchers.py` already runs this same `--help` check automatically for every
agent it just installed and prints `OK`/`FAIL` per command — if it printed
`FAIL`, that command will also fail here; the section below explains why.

---

## 7. Troubleshooting

**`assistant: command not found` (or `collab`, `coauthor`, `tw`, `dispatcher`)**
The bin dir isn't on `PATH` yet.
1. Check which rc file the installer said it updated (or check the Windows
   registry `PATH` entry).
2. Open a **new** shell / terminal — rc files aren't re-sourced automatically.
3. If it's still missing, run `scaffold.py` (for `dispatcher`) or
   `launchers.py --agents <name>` (for an agent) again directly and read its
   output for `SKIP`/`ERROR` lines.

**`ModuleNotFoundError: No module named '_agent_launch'`**
`_agent_launch.py` didn't get symlinked into the bin dir alongside the
launcher itself. Re-run `launchers.py --agents <name>` — check
`install_bin_for_agent` in `launchers.py` if it still doesn't appear.

**An agent launcher runs but the model gets the wrong instructions, or Codex
complains about a missing `agents/<name>.md`**
Check the installed profile's `model_instructions_file` value:

```bash
grep model_instructions_file ~/.codex/assistant.config.toml
```

It must be an **absolute path** to `<repo-root>/agents/assistant.md`. If it's
a relative `"agents/assistant.md"` instead, delete the file and re-run
`launchers.py` to get a fresh copy — an existing file is left alone on
purpose (to protect any machine-local edits), so a wrong value never gets
silently corrected on its own.

**Claude launcher fails to find the agent (`--agent 'x' not found`)**
This means `_agent_launch.py` failed to parse `agents/<name>.md` — usually a
missing or malformed YAML frontmatter block (`---\ndescription: ...\n---`) at
the top of that file. `_agent_launch.py` builds Claude's `--agents` JSON
definition from that frontmatter/body directly; it doesn't need
`$CLAUDE_HOME/agents/<name>.md` to exist at all.

**Symlink creation fails on Windows**
Symlinks require either Developer Mode or administrator privileges on
Windows. `dev_link.py` reports this clearly rather than a raw traceback —
enable Developer Mode (Settings → Update & Security → For developers) or run
as Administrator, then retry.

**`~/.codex` warning about being a symlink**
Codex requires a real directory at `$CODEX_HOME`, not a symlink — its sandbox
can reject mounts that cross a writable symlink at the home boundary.
`dev_link.py` detects and skips Codex linking with a warning in this case;
remove the symlink and replace it with a real directory, then re-run.

**A pre-existing real file/directory is in the way**
The installer never overwrites a real (non-symlink) file or directory — you
will see `SKIP (already exists as real path, not a symlink): <path>` and
nothing else happens there. There is no merge/backup/rollback UI. Move the
conflicting path aside yourself, then re-run.

**A pre-existing skills directory has your own content in it**
For Claude, `dev_link.py` migrates unique local entries into the repo's
`skills/` tree, records them in the repo-local git exclude file, and replaces
the directory with a top-level symlink. A conflicting same-name skill is left
for manual resolution.

For Codex, the directory is never replaced. Runtime-owned and local entries
stay in place, while each non-conflicting repo skill is linked beside them. A
legacy top-level link to the same repo is converted automatically. A local
same-name skill wins and is reported as a conflict.

**Uninstall refuses with "no manifest found"**
This is intentional, not a bug — uninstall only ever trusts its own manifest;
it never guesses at what to remove by filename pattern, since a live
generated file can share a name with something safe to delete. Run the
installer once (any mode, even with `--dry-run` off) to regenerate a
manifest, then uninstall again.

**Uninstall reports `FAILED` for something**
Check the reason printed next to it — the run still completes best-effort for
everything else, but exits non-zero. Common cause: a file permission issue
(e.g. a read-only rc file) — fix the underlying permission and re-run
uninstall; it's idempotent against partially-completed runs.

**Recurring-tasks jobs aren't picking up `assistant`/`codex`/`claude` on PATH**
Recurring-tasks owns that setup, not this installer. Its environment setup
interface writes a generated PATH bootstrap and the systemd
`AI_AGENT_COMMAND_TEMPLATE` environment file, run automatically as part of
that skill's own setup interface. If jobs still can't find a command, re-run
recurring-tasks setup and inspect the generated PATH bootstrap.

---

## 8. Adding a new agent (maintainer note)

To add an agent beyond `assistant`/`collab`/`coauthor`/`tw` (e.g. `researcher`):

1. Copy `bin/assistant` to `bin/researcher`, updating the agent name and
   default-backend env var fallback inside it.
2. Copy `bin/assistant.bat` to `bin/researcher.bat`.
3. Add `researcher` to `ALL_AGENTS` and `WORKER_AGENTS` in `launchers.py`.
4. Add `profiles/researcher.config.toml` and
   `profiles/researcher_claude_setting.json`.
5. Add `agents/researcher.md` with a `description:` frontmatter field.
6. Re-run the installer with `--agents researcher` (or pick it interactively).
