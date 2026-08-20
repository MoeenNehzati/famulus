# LLM Wakeup

`llm-wakeup` schedules guarded messages for existing Claude Code and Codex
sessions. Its short alias is `lw`.

A wakeup is guarded: immediately before delivery, the worker checks whether the
session transcript progressed after scheduling. If it did, the queued message is
discarded because the user already resumed the session.

## Quick start

Immediately after leaving a rate-limited Claude or Codex terminal session, run:

```bash
lw
```

With no arguments, `lw` runs inference. It searches recent local provider logs
for the latest limit event, session identity, and reset time, then schedules the
wakeup one minute after the reset.

For an explicit target:

```bash
lw schedule claude dispatcher-certification-gates 1:40pm
lw schedule codex 019c1234-5678-7000-8000-123456789abc 20minutes
```

Use an ISO timestamp when timezone precision matters:

```bash
lw schedule claude SESSION 2026-08-03T13:40:00-04:00
```

All displayed scheduled times use the machine's local timezone.

## Installation check

The Officina installer installs `llm-wakeup`, creates the `lw` alias, and can
install the user-level systemd service and timer. Check the current machine with:

```bash
llm-wakeup doctor
command -v llm-wakeup
command -v lw
systemctl --user status llm-wakeup.timer
```

`doctor` reports provider executables, transcript roots, queue writability,
locking support, notification support, and persistent scheduler capability.
The command requires no root privileges; the systemd units are user units.

## Commands

### Infer everything

```bash
lw
llm-wakeup infer
llm-wakeup infer --text "You've hit your session limit; resets 1:40pm (America/New_York)"
llm-wakeup infer --message "Continue the report and verify all subagents."
llm-wakeup infer --delay 2minutes
```

Inference uses the following precedence:

1. `--text`, when supplied.
2. Standard input, when piped.
3. The newest unambiguous local Claude or Codex limit event.

Text may include a provider resume command. For example, this identifies the
same Claude session represented by the alias:

```text
claude --resume "dispatcher-certification-gates"
```

The alias is resolved through provider metadata to its canonical UUID before a
job is stored. The eventual wakeup therefore resumes that exact session; it does
not create a second session.

Inference refuses ambiguous provider or session matches. Use `schedule` with an
explicit provider and session when that happens.

### Schedule explicitly

```bash
llm-wakeup schedule PROVIDER SESSION RESET_TIME [--delay DURATION] [--message MESSAGE]
```

`PROVIDER` is `claude` or `codex`. `SESSION` may be a canonical UUID or an
unambiguous provider alias. Accepted reset forms include:

- Duration from now: `20minutes` or `2 hours`.
- Local clock time: `1:40pm`.
- ISO-8601 instant: `2026-08-03T13:40:00-04:00`.
- Provider reset text passed through `infer`, including dated or weekday Claude
  reset expressions.

The default delay is one minute after the parsed reset. Override it when the
provider commonly restores access late:

```bash
lw schedule claude SESSION 1:40pm --delay 3minutes
```

Commands that target the same provider, canonical session, and timer minute are
coalesced. The CLI prints `Already scheduled` and the existing job ID rather
than creating a duplicate.

### Customize the wakeup message

Both explicit and inferred interfaces accept `--message`:

```bash
lw --message "Continue the previous task. Check subagents before reporting."
lw schedule codex SESSION 20minutes --message "Resume the test investigation."
```

When omitted, the exact default is:

> Your usage limit has reset. Resume the previous task, wake any subagents, and
> ensure they are progressing rather than stale.

### Manage automatic scheduling

Automatic scheduling is opt-in per session, at one of two levels:

```bash
lw auto on claude SESSION      # only when a usage limit stopped the session
lw auto force claude SESSION   # whenever usage nears the limit
lw auto status claude SESSION
lw auto off claude SESSION
```

The provider and session are optional when the newest local session is
unambiguous:

```bash
lw auto on
lw auto force
lw auto status
lw auto off
```

`auto on` records policy rather than scheduling a job. At this level a wakeup is
created only once the provider has actually refused a turn for lack of quota and
nothing has happened in that session since. A conversation that merely ended
near its limit is left alone: resuming it hands an idle agent no task, which it
then invents one to fill.

`auto force` keeps the older behavior, scheduling at reset whenever a window is
at or above 90% regardless of whether the session was ever stopped. Use it when
you want the session woken either way.

Policies recorded before levels existed are read as `on`, not `force`.

### Run one monitor pass

```bash
llm-wakeup monitor
```

The monitor runs two independent routes.

For sessions enabled at the conditional level it looks for a refusal record and
schedules only when one is present, is still the last thing in the transcript,
and states a reset time in the future. It stands down when the provider has
armed its own automatic resume.

For every other session it takes the older percentage route:

1. Reads local Claude status snapshots, Claude exhaustion messages, and Codex
   transcript quota records.
2. Ignores expired windows and windows below 90% usage.
3. Groups limiting windows by provider session.
4. Uses the latest reset across the constraining windows.
5. Schedules reset plus one minute for a session enabled at `force`.
6. Otherwise prints and optionally displays a reminder to enable automation.
7. Deduplicates the outcome across repeated minute-level passes.

A conditionally scheduled job is checked against the transcript again at
delivery. The queued snapshot alone cannot see a session that was refused,
resumed, and refused again, nor one whose provider has since armed its own
resume.

The check is local. It does not ask either model a question and therefore does
not consume LLM context or quota.

## Claude usage capture

Claude exposes quota data to its status-line command. Configure Claude to pass
that payload to the helper every 60 seconds:

```json
{
  "statusLine": {
    "type": "command",
    "command": "llm-wakeup capture-claude-usage",
    "refreshInterval": 60
  }
}
```

The capture command reads one JSON payload from standard input, stores normalized
quota windows, and prints a concise usage line for Claude's UI.

If a status-line command already exists, preserve its output by chaining it:

```bash
llm-wakeup capture-claude-usage --chain-command 'existing-status-command'
```

Installers should prefer `--chain-command-base64` when embedding an existing
command containing quotes or shell syntax. The encoded value is decoded by the
helper before execution, avoiding nested configuration quoting errors.

If Claude rejects a request before providing status-line quota data, the monitor
also recognizes transcript warnings such as a weekly limit with a dated reset.

Status-line percentages describe consumption, not permission. A rejection can
arrive while the reported utilization is well below 100, so a refusal is
identified from the transcript row Claude writes when it happens:
`error == "rate_limit"` with `isApiErrorMessage`, and, on recent versions, a
`quotaLimits` object carrying the authoritative reset epoch.

## Codex usage acquisition

Codex records quota windows in local session transcript `token_count` events.
The monitor reads the newest valid record for each relevant local session. No
Codex hook or status-line configuration is required.

A refusal, however, is not a percentage. Codex marks it on the `task_complete`
record of the refused turn, as `error.codex_error_info == "usage_limit_exceeded"`
with a null `last_agent_message`, and by then both quota windows read `null`.
The reset time appears only as English prose in the error message, stated in
local time. A forked or resumed rollout replays its parent's history under fresh
timestamps, so a copied refusal is rejected by comparing the record timestamp
with the payload's own `completed_at`.

## Persistent systemd operation

The supplied user timer runs every minute and invokes the internal worker:

```text
llm-wakeup run-due
```

The worker performs a monitor pass first and then processes due jobs. Monitor
failure is logged but does not block delivery of already queued jobs.

Useful commands:

```bash
systemctl --user status llm-wakeup.timer
systemctl --user start llm-wakeup.timer
systemctl --user enable --now llm-wakeup.timer
systemctl --user list-timers llm-wakeup.timer
journalctl --user-unit llm-wakeup.service
journalctl --user-unit llm-wakeup.service --since today
journalctl --user-unit llm-wakeup.service -f
```

The timer is persistent. If the machine sleeps or is powered off at a deadline,
systemd starts the worker after the machine resumes or boots. The transcript
progress guard still runs before delivery, so a session resumed elsewhere while
the machine was unavailable is not blindly awakened.

## Logs and outcomes

Worker and monitor output is line-oriented for journald. Common outcomes include:

| Event | Meaning |
| --- | --- |
| `usage-reminded` | Near-limit session requires manual opt-in |
| `usage-scheduled` | Monitor created an automatic wakeup |
| `wakeup-delivered` | Provider resume command completed successfully |
| `wakeup-skipped-progress` | Transcript changed; stale job removed |
| `transcript-error` | Transcript could not be checked; retry scheduled |
| `provider-error` | Provider command failed; retry scheduled |
| `worker-busy` | Another process already owns the worker lock |
| `usage-monitor-error` | Monitor failed; due-job processing continued |

Desktop notification uses `notify-send` when available. It is best-effort;
journald output is the authoritative operational record.

## State and portability

Persistent state defaults to:

```text
~/.local/share/llm-wakeup/
```

Override it without privileges:

```bash
export LLM_WAKEUP_HOME=/path/to/portable/state
```

The directory contains the queue, policy registry, usage snapshots, locks, and
monitor deduplication markers. It contains mutable machine/user state and should
not live inside the source repository.

Core scheduling and monitoring are standard-library Python and provider-adapter
based. The included persistent scheduler integration is systemd-specific;
`llm-wakeup doctor` reports the capability available on the current platform.
A different host scheduler only needs to invoke `llm-wakeup run-due`
periodically and after missed deadlines.

## Unattended provider permissions

Background wakeups cannot answer permission prompts. Delivery therefore uses:

- Claude: `--print --permission-mode auto --allowedTools WebFetch,WebSearch`.
- Codex: `--ask-for-approval never --sandbox workspace-write`, outbound network
  access, search, and `exec resume`.

Claude and Codex are launched in the working directory recorded by the original
session. Review these unattended permissions before enabling automatic policy on
a sensitive session.

Override provider executables when they are not discoverable:

```bash
export CLAUDE_EXECUTABLE=/absolute/path/to/claude
export CODEX_EXECUTABLE=/absolute/path/to/codex
```

## Troubleshooting

### `lw: command not found`

Run `command -v llm-wakeup`. If the long command exists, the installer did not
create the alias beside it or that directory is not on `PATH`. Re-run the
Officina installer or create an equivalent `lw` symlink in the same executable
directory.

### Inference cannot identify a provider or session

Include timeout or resume text explicitly:

```bash
llm-wakeup infer --text 'claude --resume "SESSION" ... resets 1:40pm'
```

Or bypass inference:

```bash
llm-wakeup schedule claude SESSION 1:40pm
```

### Scheduled time looks wrong

The CLI displays local machine time. Use an ISO-8601 timestamp with an explicit
UTC offset to remove timezone ambiguity.

### A wakeup did not appear in the chat

Inspect the journal first:

```bash
journalctl --user-unit llm-wakeup.service --since today
```

Look for progress suppression, a provider/transcript error with retry time, or a
successful provider exit. Also run `llm-wakeup doctor` and confirm the timer is
active.

### Desktop reminder failed

This does not imply that monitoring failed. Desktop notifications depend on the
graphical-session bus. The same event is printed for journald before
`notify-send` is attempted.

### Inspect isolated test state

Use a temporary state directory to avoid touching the production queue:

```bash
LLM_WAKEUP_HOME=/tmp/llm-wakeup-test llm-wakeup doctor
LLM_WAKEUP_HOME=/tmp/llm-wakeup-test llm-wakeup monitor
```

## Development and tests

From the repository root:

```bash
python3 -m pytest -q -o pythonpath=src src/officina/wakeup/tests
python3 validators/skill/blueprint_relationships.py
```

Real installed-client tests are opt-in because they invoke Claude and Codex and
may consume quota:

```bash
LLM_WAKEUP_RUN_CLIENT_TESTS=1 \
  python3 -m pytest -q -o pythonpath=src \
  src/officina/wakeup/tests/test_client_integration.py
```

Use `LLM_WAKEUP_HOME` with a temporary directory for manual queue tests. Do not
point destructive test fixtures at the production state root.

## Blueprint interfaces

The module exports three dispatcher-callable interfaces:

| Interface | Purpose |
| --- | --- |
| `wakeup.interface.auto-policy` | Enable, disable, or inspect per-session automatic scheduling |
| `wakeup.interface.infer-schedule` | Infer provider, session, and reset before scheduling |
| `wakeup.interface.explicit-schedule` | Schedule with an explicit provider, session, and reset |

A caller must declare the interface in its behavioral source before dispatcher
authorization succeeds:

```yaml
uses_interfaces:
- interface: wakeup.interface.explicit-schedule
  version: 1
```

The caller then supplies only caller-visible arguments. The binding injects the
corresponding `auto`, `infer`, or `schedule` subcommand:

```bash
dispatcher --caller-skill CALLER wakeup.interface.auto-policy on claude SESSION
dispatcher --caller-skill CALLER wakeup.interface.infer-schedule --delay 2minutes
dispatcher --caller-skill CALLER wakeup.interface.explicit-schedule claude SESSION 1:40pm
```

`allow_all_modules` permits any declared module caller; it does not bypass the
required `uses_interfaces` edge.

## Internal documentation

See `CLAUDE-CODEX-ARCHITECTURE.md` for component contracts, lifecycle, safety invariants,
persistent record ownership, extension points, and the test strategy.
