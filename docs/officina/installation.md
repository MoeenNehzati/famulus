# Installation Guide

This document is the detailed, user-facing companion to
[skills/install-assistant-tools/SKILL.md](../../skills/install-assistant-tools/SKILL.md). That file tells the assistant how
to *run* the installer conversationally; this file is for a human (or an
assistant debugging a broken install) who wants to understand exactly what
each script does, what every flag means, and how to diagnose a problem.

If you just want the installer to walk you through setup, ask your assistant
to install or repair the assistant tools. On a fresh machine it runs the Phase
1 entry point directly from the plugin; after installation, targeted repairs
can also route through `dispatcher`. Read this file directly when something
needs closer inspection than the conversational flow gives you.

If the commands are already installed and you just want to know how to use
`assistant`, `collab`, `coauthor`, or `tw`, start with [docs/launchers.md](../launchers.md).

## Before you begin

Famulus is on the pre-stable `0.1.0` development line. It requires a
plugin-capable Claude Code or Codex host and Python 3.11 or newer to launch the
installer. Famulus does not yet publish a minimum supported host-version
matrix. Initial setup requires network access to download pinned `uv`, managed
CPython 3.11.15, and the hash-locked package set; later no-op repairs can reuse
the managed artifacts already present.

In command examples, `<FAMULUS_DIR>` means the installed plugin directory or a
live checkout. Claude reports its plugin `installPath` through
`claude plugin list --json`; Codex reports the plugin `source.path` through
`codex plugin list --json`.

Important distinction:

- installing the Famulus plugin makes the skill package available to Claude Code or Codex
- running the Phase 1 installer below is what writes local launchers such as
  `dispatcher`, `llm-wakeup`/`lw`, and `invoke-skill`, and adds them to `PATH`

So even in plugin mode, the local scaffold step is still the route that gives
you working bare `dispatcher`, `llm-wakeup`, and `lw` commands on your machine.

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

The installer is split into three scripts, chained by `_phase_entry.py`:

```
_phase_entry.py
  ├─ _install_scaffold.py  (always runs)
  ├─ _config_bridge.py     (only if dev mode was chosen)
  └─ _agent_launchers.py   (required background launcher plus selections)
```

Before these steps, `_phase_entry.py` builds and activates the managed runtime.
Calling `_install_scaffold.py` alone is a targeted repair and is not a complete
fresh installation.

### `_install_scaffold.py` — the universal floor

Runs in every install, regardless of mode or which agents you want. Installs:

- The `dispatcher` launcher — generated into the managed bin directory as
  `<bin-dir>/dispatcher` on Unix-like hosts and `<bin-dir>/dispatcher.bat` on
  Windows. Every skill's `SKILL.md` invokes its own scripts through this
  command (`dispatcher --caller-skill <caller> <module>.interface.<name> ...`), so
  this is the one piece of scaffolding almost everything else structurally
  depends on.
- The `llm-wakeup` launcher and its `lw` alias — generated as extensionless
  executable shims on Linux/macOS and as `llm-wakeup.bat`/`lw.bat` on Windows.
  Both invoke `officina.wakeup.cli` through the same stable managed-runtime
  resolver as `dispatcher`, so they follow the active Officina release without
  embedding the checkout or a release-specific interpreter.
- The `invoke-skill` launcher — used by `recurring-tasks` scheduler jobs to
  invoke a skill by name without hardcoding an absolute path. This is generated
  as `<bin-dir>/invoke-skill` on Unix-like hosts and
  `<bin-dir>/invoke-skill.bat` on Windows.
- The versioned first-party Officina wheel and the hash-checked core dependency
  lock at `references/runtime/requirements-core.lock`. That lock is generated
  from the pooled executable behavioral-source declarations in
  `references/blueprint/runtime_dependencies.json`; it is not a second
  handwritten dependency inventory. Before creating a release, the installer
  checks that the generated input matches the manifest, that the lock headers
  identify that input plus pinned `uv 0.11.29` and managed CPython `3.11.15`,
  that the complete lock body matches its recorded SHA-256 digest, and that
  each parsed record has a concrete `==` version and a SHA-256 hash. Wildcard
  versions are rejected. It then installs the lock with `--require-hashes` and
  installs the locally built Officina wheel with dependency resolution
  disabled. Candidate construction verifies the wheel before activation.
  Artifact metadata records the lock and wheel SHA-256 digests, the resolved
  Python identity, and a source identity: the Git commit
  in a checkout, or a deterministic fingerprint of
  `pyproject.toml` and `src/officina/**` when a plugin manager has copied the
  package without `.git`. The stable launcher reads `current.json`, injects
  its exact repository `officina.toml`, and enters that release's interpreter;
  it does not embed a checkout path or use ambient `PYTHONPATH`. Linux recurring
  units use that runtime and capture the installed launcher directory ahead of
  other PATH entries.

- `PATH` — adds `<bin-dir>` to your shell rc (or the Windows registry) so
  `dispatcher`, `llm-wakeup`/`lw`, `invoke-skill`, and the agent launchers
  resolve as bare commands.

At the end of scaffold, the installer prints a capability report for shared
launchers. If a required capability such as `dispatcher` fails or is skipped,
scaffold exits nonzero and the phase-1 orchestrator stops before
`_config_bridge.py` or `_agent_launchers.py` runs. Platform-scoped capabilities that are unsupported on a
host are reported with the affected workflows named, but they do not block the
universal managed-command floor. `--dry-run` prints the same capability report
without writing files.

### `_config_bridge.py` — dev mode only

Only runs if you said yes to dev mode and gave a repo path. Installs:

- Symlinks: `~/.claude/skills → <repo>/skills`, plus the shared `references`,
  `agents`, and instruction files. Codex keeps `~/.codex/skills` as a real
  directory and links each repo skill into it individually, preserving its
  runtime-owned `.system` directory. Codex profile configs are also linked.
- Dev-mode session hooks in `~/.claude/settings.local.json` and
  `~/.codex/config.toml`, driven by the registry in [llmhooks/registry.py](../../llmhooks/registry.py).
- `git config core.hooksPath .githooks` in the repo (skipped with a note if
  the given path isn't actually a git checkout).
- `$AI` in your shell rc, pointing at the repo root.

`~/.codex` itself must be a real directory, not a symlink — Codex's sandbox
can reject mounts that cross a writable symlink at the home-directory
boundary. `_config_bridge.py` detects this and warns rather than failing silently.

### `_agent_launchers.py` — required background launcher plus optional agents

You choose the optional interactive launchers from `assistant`, `collab`,
`coauthor`, and `tw`. Phase 1 also installs `background_run` even when none of
those optional launchers is selected, because the universally installed
`invoke-skill` command depends on it. For every installed agent, the installer
adds:

- The bin launcher. Unix-like hosts install editable symlinks into the repo's
  `bin/`; Windows copies the launcher bundle into the managed bin directory so
  supported launchers do not depend on Developer Mode or administrator symlink
  privileges.
- Its profile (`profiles/<agent>.config.toml`) copied — not symlinked — into
  both `~/.codex` and `~/.claude`. Copied because Codex writes machine-local
  state (project trust levels, trusted hook hashes) back into that file; a
  symlink would leak that state into the tracked repo. An existing copy is
  left alone to preserve accumulated local state.
- Its worker directory. Development mode uses
  `<repo>/workers/<agent>`; plugin mode uses the platform Famulus state
  directory listed in [Default paths](#5-default-paths).
- `ASSISTANT_DEFAULT` in your shell rc (which backend — `claude` or `codex`
  — a launcher uses when you don't pass `--claude`/`--codex` explicitly).
- A post-install verification pass: runs `<agent> --help` for each agent you
  just installed and reports `OK`/`FAIL` per command.

`tw` installs both `tmux-workspace` and the `tw` alias on Unix-like hosts. It
is skipped on Windows because tmux is not available there.

`background_run` is reserved for unattended work. `invoke-skill` uses it to
launch explicitly enabled recurring jobs with Claude's `bypassPermissions`
mode or Codex's `--dangerously-bypass-approvals-and-sandbox` mode. Core
installation creates the launcher, profile, and worker directory, but it does
not create or enable a scheduled job. Review the
[security boundary](../security-and-privacy.md#authorization-and-confirmation-boundaries)
before enabling recurring automation.

---

## 3. What Phase 1 deliberately does *not* do

By design, `_phase_entry.py` stops once scaffold, configuration bridge, and
launcher installation finish. It does not:

- Install `marker-pdf` or its OCR/ML dependency closure. That dependency is
  excluded from the first supported release lock; `--include-optional-deps`
  exits without installing rather than escaping the reviewed lock.
- Connect any external account. `connect-google` prepares shared Google OAuth
  client configuration afterward; each service initiates and owns its OAuth
  exchange and credentials.
- Set up recurring automation (scheduled triage, daily planning). That's the
  automation skill's own lazy, on-demand responsibility — it checks/writes
  its own prerequisites the first time you actually ask for a scheduled job,
  not upfront during install.

This is deliberate: `install-assistant-tools` shouldn't need to know about
every skill that might eventually want post-install setup. If you're
debugging "why didn't the installer connect my calendar" — it isn't supposed
to. That happens afterward, conversationally, on request.

### Connect Google after Phase 1

Google integrations are experimental in the first public release. Their OAuth
grants are broader than the runtime operations Famulus advertises, and the
current implementation does not provide one complete disconnect, server-side
revocation, uninstall, and purge lifecycle. Review
[`docs/security-and-privacy.md`](../security-and-privacy.md) before connecting
an account.

Ask the assistant:

```text
Connect Famulus to Google.
```

The workflow recommends Drive, Calendar, and Gmail and allows any subset.
`connect-google` guides you through creating a Google Cloud project and
Desktop OAuth client, prepares the downloaded client configuration, then hands
each selected service back to cloud-files, g-calendar, or email-client for
service-owned authorization.

Never commit the client JSON to GitHub, paste its contents into an issue, or
store it in the Famulus checkout. The shared client configuration is copied to
private local configuration; Drive, Calendar, and Gmail tokens remain local to
their corresponding service and are not shared between services.

Agent-driven recurring jobs are also experimental. Core installation does not
create them. `recurring-tasks` creates or enables a job only after an explicit
request to schedule that workflow; inspect the job definition and schedule
before enabling it.

---

## 4. Full CLI reference

Every subordinate script below can also be run directly for a targeted repair
instead of going through `_phase_entry.py`. All accept `--dry-run` to preview
without writing anything.

### `_phase_entry.py` (orchestrator)

```
_phase_entry.py [--home DIR] [--bin-dir DIR] [--shell-rc FILE]
                [--codex-home DIR] [--claude-home DIR] [--dry-run]
                [--non-interactive]
                [--dev-mode | --no-dev-mode] [--repo-path DIR]
                [--include-optional-deps | --no-optional-deps]
                [--agents LIST] [--default-llm {claude,codex}]
```

| Flag | Meaning |
|---|---|
| `--home DIR` | Home directory override (default: platform home) |
| `--bin-dir DIR` | Where launchers go (default: `~/.local/bin` on Linux/macOS; `%LOCALAPPDATA%\Famulus\bin` on Windows) |
| `--shell-rc FILE` | Shell rc file to manage (default: auto-detected `~/.zshrc` or `~/.bashrc`; Windows uses the registry instead) |
| `--codex-home DIR` / `--claude-home DIR` | Override Codex/Claude config dirs (default: `$CODEX_HOME`/`$CLAUDE_HOME`, else `~/.codex`/`~/.claude`) |
| `--dry-run` | Print planned actions, write nothing |
| `--non-interactive` | Never prompt. Requires `--dev-mode`/`--no-dev-mode` explicitly, and `--repo-path` if dev mode is chosen. Without this flag, missing choices are prompted for interactively |
| `--dev-mode` / `--no-dev-mode` | Explicit mode choice (mutually exclusive). Omit to be prompted |
| `--repo-path DIR` | Repo checkout path, required if `--dev-mode` is chosen non-interactively |
| `--include-optional-deps` | Unsupported in the first release; exits without installing. `--no-optional-deps` is accepted for compatibility and matches the only supported behavior |
| `--agents LIST` | Comma-separated subset of `assistant,collab,coauthor,background_run,tw`. Omit to be prompted; empty in non-interactive mode installs no optional agents, but Phase 1 still installs required `background_run` |
| `--default-llm {claude,codex}` | Default backend for the chosen agents. Omit to be prompted; defaults to `claude` in non-interactive mode |

**Non-interactive example** (e.g. a provisioning script):

```bash
python3 <FAMULUS_DIR>/skills/install-assistant-tools/_rtx/_phase_entry.py \
  --non-interactive --no-dev-mode --no-optional-deps \
  --agents assistant,collab,coauthor,tw --default-llm claude
```

**Dev-mode non-interactive example:**

```bash
python3 <FAMULUS_DIR>/skills/install-assistant-tools/_rtx/_phase_entry.py \
  --non-interactive --dev-mode --no-optional-deps \
  --repo-path ~/Documents/AI --agents assistant --default-llm claude
```

### `_install_scaffold.py`

```
_install_scaffold.py --repo-root DIR [--home DIR] [--bin-dir DIR]
                     [--shell-rc FILE] [--dry-run]
```

`--repo-root` is required — this is the one script argument `_phase_entry.py`
always supplies for you (auto-derived in plugin mode, user-supplied in dev
mode). Run it standalone only if you need to repair the `dispatcher`,
`llm-wakeup`/`lw`, or `invoke-skill` launchers or PATH without touching anything
else. It does not build a missing managed runtime, so it is not a fresh-install
entry point.

### `_config_bridge.py`

```
_config_bridge.py --repo-root DIR [--home DIR] [--claude-home DIR]
                  [--codex-home DIR] [--shell-rc FILE]
                  [--no-claude] [--no-codex] [--dry-run]
```

`--repo-root` is required and must be a real path you provide — this script
never guesses it from its own location. `--no-claude`/`--no-codex` skip one
side if you only use one host.

### `_agent_launchers.py`

```
_agent_launchers.py --repo-root DIR [--agents LIST] [--home DIR]
                    [--bin-dir DIR] [--codex-home DIR] [--claude-home DIR]
                    [--shell-rc FILE] [--default-llm {claude,codex}]
                    [--mode {development,plugin}] [--dry-run]
```

`--agents` defaults to none when this subordinate repair is invoked directly —
you must pass it explicitly to install a launcher. The full Phase 1
orchestrator always adds `background_run` because it also installs
`invoke-skill`. The direct repair is safe to re-run with a different
`--agents` list to add more agents later; already-installed agents are left
alone where their conflict policy requires preserving machine-local state.

### `_install_uninstall.py`

```
_install_uninstall.py [--home DIR] [--claude-home DIR] [--codex-home DIR]
                      [--bin-dir DIR] [--shell-rc FILE]
                      [--system-shell-rc FILE] [--no-system-shell-rc]
                      [--repo-root DIR] [--manifest FILE] [--no-pip]
                      [--no-git-hooks] [--purge] [--dry-run]
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
| `--no-pip` | Skip cleanup of obsolete separately installed dispatcher packages |
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
| Bin dir | Linux/macOS: `~/.local/bin`; Windows: `%LOCALAPPDATA%\Famulus\bin` |
| User shell rc | `~/.zshrc` if `$SHELL` contains `zsh`, else `~/.bashrc`. Windows: user registry (`HKEY_CURRENT_USER\Environment`) instead |
| Codex home | `$CODEX_HOME`, else `~/.codex` |
| Claude home | `$CLAUDE_HOME`, else `~/.claude` |
| Plugin worker dirs | Linux: `$XDG_STATE_HOME/famulus/workers`, else `~/.local/state/famulus/workers`; macOS: `~/Library/Application Support/Famulus/state/workers`; Windows: `%LOCALAPPDATA%\Famulus\state\workers` |
| Development worker dirs | `<repo-root>/workers/{assistant,collab,coauthor,background_run}` |
| Git hooks | `<repo-root>/.githooks` (dev mode only) |
| Install manifest | `<home>/.local/state/assistant-tools/install-manifest.json` |

`$AI` is **not** in this table on purpose — it's only ever set by `_config_bridge.py`
(dev mode). Plugin-mode installs never export it; `dispatcher` and
`_agent_launch.py` each resolve their own repo root from their own file
location instead of depending on it.

---

## 6. Updating, repairing, and removing Famulus

Plugin files and workstation files have separate lifecycles. Updating or
removing a host plugin does not rebuild or remove the managed runtime,
launchers, shell changes, scheduler jobs, credentials, or remote data.

### Update

Refresh the plugin source with the command for your host:

```bash
# Claude Code
claude plugin marketplace update nullkit
claude plugin update famulus@nullkit

# Codex: refreshes the configured Git marketplace snapshot
codex plugin marketplace upgrade nullkit --json
```

Restart the host so it loads the refreshed plugin. Locate the refreshed
`<FAMULUS_DIR>` again, then rerun Phase 1 with the same mode and optional-agent
choices you used originally. For a minimum plugin-mode installation:

```bash
python3 <FAMULUS_DIR>/skills/install-assistant-tools/_rtx/_phase_entry.py \
  --non-interactive --no-dev-mode --no-optional-deps
```

This rebuilds and activates the managed runtime from the refreshed source
before repairing the shared launchers. Existing machine-local profile copies
are preserved rather than overwritten. Use the narrower subordinate scripts
only for a targeted repair after a managed runtime already exists.

### Remove

Use this order so the workstation uninstaller still exists when you need it:

1. Ask `recurring-tasks` to disable and synchronize every Famulus job. Verify
   that no enabled job still invokes `invoke-skill`.
2. Preview the manifest-owned workstation removal:

   ```bash
   python3 <FAMULUS_DIR>/skills/install-assistant-tools/_rtx/_install_uninstall.py --dry-run
   ```

3. Run the same command without `--dry-run`. Add `--purge` only if you also
   want the configuration directories recorded by the installer removed:

   ```bash
   python3 <FAMULUS_DIR>/skills/install-assistant-tools/_rtx/_install_uninstall.py
   ```

4. Remove the host plugin:

   ```bash
   # Claude Code
   claude plugin uninstall famulus@nullkit

   # Codex
   codex plugin remove famulus@nullkit --json
   ```

5. If external accounts were connected, separately revoke their server-side
   authority and remove service-owned local secrets. Follow
   [Disconnect, revoke, uninstall, and purge](../security-and-privacy.md#disconnect-revoke-uninstall-and-purge).

The uninstaller is manifest-based and deliberately leaves worker directories,
installed Python dependencies, remote data, and some service-owned or shared
credentials for explicit review. `--purge` is not equivalent to OAuth
revocation and is not a complete shared-credential eraser.

---

## 7. Verifying an install

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

# Confirm both wakeup command names resolve
llm-wakeup --help
lw --help

# In a repo checkout, also verify dispatcher can route every converted
# Python process-bound interface to its subprocess entrypoint.
python3 -m pytest -q tests/test_dispatcher_route_smoke.py
```

`_agent_launchers.py` already runs this same `--help` check automatically for every
agent it just installed and prints `OK`/`FAIL` per command — if it printed
`FAIL`, that command will also fail here; the section below explains why.

---

## 8. Troubleshooting

**`assistant: command not found` (or `collab`, `coauthor`, `background_run`, `tw`, `dispatcher`, `llm-wakeup`, `lw`)**
The bin dir isn't on `PATH` yet.
1. Check which rc file the installer said it updated (or check the Windows
   registry `PATH` entry).
2. Open a **new** shell / terminal — rc files aren't re-sourced automatically.
3. If it's still missing, run `_install_scaffold.py` (for `dispatcher`, `llm-wakeup`,
   `lw`, or `invoke-skill`) or
   `_agent_launchers.py --agents <name>` (for an agent) again directly and read its
   output for `SKIP`/`ERROR` lines.

**`ModuleNotFoundError: No module named '_agent_launch'`**
`_agent_launch.py` didn't get symlinked into the bin dir alongside the
launcher itself. Re-run `_agent_launchers.py --agents <name>` and inspect its
reported launcher-copy or link actions if it still doesn't appear.

**An agent launcher runs but the model gets the wrong instructions, or Codex
complains about a missing `agents/<name>.md`**
Check the installed profile's `model_instructions_file` value:

```bash
grep model_instructions_file ~/.codex/assistant.config.toml
```

It must be an **absolute path** to `<repo-root>/agents/assistant.md`. If it's
a relative `"agents/assistant.md"` instead, delete the file and re-run
`_agent_launchers.py` to get a fresh copy — an existing file is left alone on
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
Windows. `_config_bridge.py` reports this clearly rather than a raw traceback —
enable Developer Mode (Settings → Update & Security → For developers) or run
as Administrator, then retry.

**`~/.codex` warning about being a symlink**
Codex requires a real directory at `$CODEX_HOME`, not a symlink — its sandbox
can reject mounts that cross a writable symlink at the home boundary.
`_config_bridge.py` detects and skips Codex linking with a warning in this case;
remove the symlink and replace it with a real directory, then re-run.

**A pre-existing real file/directory is in the way**
The installer never overwrites a real (non-symlink) file or directory — you
will see `SKIP (already exists as real path, not a symlink): <path>` and
nothing else happens there. There is no merge/backup/rollback UI. Move the
conflicting path aside yourself, then re-run.

**A pre-existing skills directory has your own content in it**
For Claude, `_config_bridge.py` migrates unique local entries into the repo's
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

## 9. Adding a new agent (maintainer note)

To add an agent beyond `assistant`/`collab`/`coauthor`/`tw` (e.g. `researcher`):

1. Copy `bin/assistant` to `bin/researcher`, updating the agent name and
   default-backend env var fallback inside it.
2. Copy `bin/assistant.bat` to `bin/researcher.bat`.
3. Add `researcher` to `ALL_AGENTS` and `WORKER_AGENTS` in `_agent_launchers.py`.
4. Add `profiles/researcher.config.toml` and
   `profiles/researcher_claude_setting.json`.
5. Add `agents/researcher.md` with a `description:` frontmatter field.
6. Re-run the installer with `--agents researcher` (or pick it interactively).
