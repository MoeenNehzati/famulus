# Machine Path Hygiene — Design

Date: 2026-06-24

## Problem

The `recurring-tasks` skill generates runner shell scripts into
`skills/recurring-tasks/scripts/runners/` — inside the git repo. These scripts
contain machine-specific absolute paths (`/home/moeen/...`, `/run/user/1000/...`)
because `sync-units.py` writes them with hardcoded paths at generation time.

The root cause is a design choice: runner scripts were introduced as an
indirection layer so systemd `ExecStart` had a file to point at. They are
generated runtime artifacts that were placed in the wrong location (inside the
source tree instead of outside it). The `check-platform-neutral` pre-commit hook
now catches them, blocking all new commits.

More broadly, there is no enforcement preventing future skills or scripts from
introducing hardcoded machine paths into committed source.

---

## Goals

1. Eliminate runner scripts entirely — no generated files inside the skill
   directory.
2. Detect and store all machine-specific addresses at install time in
   `environment.d`, where they belong.
3. Add a pre-commit hook that enforces no machine-level absolute paths in
   committed source files — making the quality bar automatic for all future
   skills.

---

## Design

### 1. Remove runner scripts from `recurring-tasks`

`sync-units.py` currently generates two artifacts per job:
- systemd unit files → `~/.config/systemd/user/` ✓ (outside repo, correct)
- runner shell scripts → `skills/recurring-tasks/scripts/runners/` ✗ (inside
  repo, wrong)

Runner scripts exist purely because `ExecStart` needs an executable. They can
be eliminated by pointing `ExecStart` directly at `run-skill.sh` and moving
environment setup into the service file.

**Updated `service_content()` in `sync-units.py`:**

```ini
[Unit]
Description=AI recurring job: {description}

[Service]
Type=oneshot
EnvironmentFile={env_file}
ExecStart=/bin/sh -c '"$AI_RUN_SKILL" {job_name}'
StandardOutput=append:{log_path}
StandardError=append:{log_path}
```

Where:
- `{env_file}` — absolute path to `~/.config/environment.d/20-ai-agent.conf`,
  written at generation time by `sync-units.py` (service files live outside the
  repo, machine-specific paths are fine there)
- `$AI_RUN_SKILL` — resolved at runtime from `environment.d` (see below)
- `{log_path}` — absolute path to the job's log file, also written by
  `sync-units.py` at generation time

`/bin/sh` is a system-level POSIX contract, not a machine path.

**Delete `scripts/runners/`** from the repo entirely.

### 2. `run-skill.sh` — self-contained

`run-skill.sh` derives its own location via `readlink -f "${BASH_SOURCE[0]}"`.
No paths are hardcoded. It sets `PATH` internally to include `~/.local/bin` and
`AI_BIN_DIR` (from environment).

### 3. Installer writes machine addresses to `environment.d`

`install_assistant_tools.sh` detects and writes the following to
`~/.config/environment.d/20-ai-agent.conf`:

| Variable | Detection method | Purpose |
|---|---|---|
| `AI_AGENT_COMMAND_TEMPLATE` | already set | invoke Claude/Codex with skill |
| `AI_BIN_DIR` | `$(dirname "$(readlink -f "$0")")`-derived | bin dir on PATH |
| `AI_RUN_SKILL` | resolved path to `run-skill.sh` at install time | called by service ExecStart |
| `DBUS_SESSION_BUS_ADDRESS` | `unix:path=/run/user/$(id -u)/bus` | DBUS socket for user session |

The installer optionally prompts the user to confirm or override each detected
value, for non-standard setups.

`environment.d` is the single source of truth for machine-specific addresses.
Committed source never contains them.

### 4. New pre-commit hook: `check-no-machine-paths`

A new hook in `.githooks/skill/check-no-machine-paths` scans committed source
files for absolute paths that are machine-specific:

**Forbidden patterns in committed source:**
- `/home/<anything>` — user home directory paths
- `/run/user/<digits>` — per-UID runtime directories
- `/root/` — root home directory

**Allowed:**
- `/bin/`, `/usr/bin/`, `/usr/local/bin/`, `/etc/`, `/tmp/` — system paths
- `/run/` (without `/user/<digits>`) — generic runtime paths
- Files in `EXCLUDED_PATHS` — same exclusions as `check-platform-neutral`
- Generated files outside the repo — not committed, not scanned

**Implementation:** Python test in `tests/test_no_machine_paths.py`, called from
`.githooks/skill/check-no-machine-paths`, registered in `.githooks/pre-commit`.

This hook makes the constraint automatic: any future skill or script that
accidentally introduces a machine path is caught at commit time, not discovered
later.

---

## `environment.d` as the contract

After this change, `~/.config/environment.d/20-ai-agent.conf` is the canonical
location for all machine-specific runtime addresses. The pattern is:

- **Installer detects** → writes to `environment.d`
- **Service files reference** via `EnvironmentFile=` (generated, outside repo)
- **Scripts reference** via environment variables (no hardcoded paths)
- **Pre-commit hook enforces** no leakage of machine paths into source

---

## Files changed

```
skills/recurring-tasks/scripts/sync-units.py     update: remove write_runner(), update service_content()
skills/recurring-tasks/scripts/run-skill.sh      update: self-contained PATH/DBUS handling
skills/recurring-tasks/scripts/runners/          delete entirely
skills/install-assistant-tools/scripts/
  install_assistant_tools.sh                     update: write AI_BIN_DIR, AI_RUN_SKILL, DBUS to environment.d
.githooks/skill/check-no-machine-paths           new: scans for /home/, /run/user/<uid>/ in committed source
tests/test_no_machine_paths.py                   new: implementation of the check
.githooks/pre-commit                             update: register new hook
```

---

## What is NOT changed

- The shape of `jobs.yaml` — job definitions unchanged.
- `invoke-agent.sh` — already outside the runner pattern.
- `check-platform-neutral` — complementary hook, not replaced.
- Other skills — unaffected unless they already contain machine paths, in which
  case the new hook will surface them on their next commit.
