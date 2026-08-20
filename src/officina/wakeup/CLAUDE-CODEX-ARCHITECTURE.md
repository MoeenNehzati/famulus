# LLM Wakeup Architecture

This document describes the internal contracts of `officina.wakeup`. For
operator commands and installation guidance, see `CLAUDE-CODEX-README.md`.
The YAML files under `blueprints/` are the authoritative machine-readable
ownership and dependency declarations.

## Design goal

The module resumes an existing Claude or Codex session after a quota reset,
but only if that session has not progressed since the wakeup was scheduled.
It uses provider-owned local transcripts as the source of session identity,
reset information, and progress evidence. It does not send an LLM request to
measure quota usage.

The design is deliberately dependency-light:

- Python standard library for parsing, persistence, locking, and execution.
- Provider CLIs only when a due wakeup is delivered.
- systemd user units for persistent scheduling on supported hosts.
- `notify-send` only as an optional best-effort desktop notification.

## Behavioral sources

### Session data

Files: `providers/`, `claude_codex_sessions.py`, `claude_codex_cutoff.py`, and
`deadlines.py`.

Provider adapters normalize filesystem and transcript differences behind
`ProviderAdapter`. The session layer discovers logs, resolves UUIDs and aliases,
extracts rate-limit events, and computes a digest of meaningful progress.
Deadline parsing accepts explicit durations, local clock times, ISO-8601
instants, and reset expressions embedded in provider output.

### Persistence

Files: `store.py`, `locking.py`, `policies.py`, and
`linux_osx_windows.py`.

The queue and policy registry are JSON files under `LLM_WAKEUP_HOME`. Writers
use advisory file locks and atomic replacement. No database daemon, schema
migration service, or elevated privilege is required.

### Scheduler and delivery

File: `claude_codex_service.py`.

`schedule()` resolves the exact transcript, snapshots its meaningful state,
and stores a job. `run_due()` acquires a nonblocking worker lock and evaluates
every due job. A job is discarded when the current transcript state differs
from its saved state because that difference proves the user or another process
already resumed the session. A job created from refusal evidence is additionally
re-checked against that evidence, because a session can be refused, resumed, and
refused again without changing the state hash. Otherwise the worker invokes the
provider adapter's resume command in the session's recorded working directory.

Operational failures retain the job and move it five minutes into the future.
Successful, stale, and unrecoverable outcomes are emitted as line-oriented
events suitable for journald.

### Usage acquisition and monitoring

Files: `claude_codex_usage.py` and `claude_codex_monitor.py`.

`UsageSnapshot` is the provider-neutral quota record:

| Field | Meaning |
| --- | --- |
| `provider` | `claude` or `codex` |
| `session_id` | Canonical provider session UUID |
| `window` | Provider quota-window name |
| `used_percentage` | Percentage consumed in that window |
| `resets_at` | Reset instant as a Unix timestamp |
| `observed_at` | Observation instant as a Unix timestamp |
| `transcript_path` | Exact transcript associated with the session |

Claude usage is captured from its local status-line JSON payload. If Claude is
already exhausted and no status payload is available, the acquisition layer can
normalize the limit/reset warning in the transcript. Codex usage is read from
the newest valid local `token_count` record. Corrupt independent snapshots and
unrelated transcript records are ignored rather than poisoning the whole pass.

The monitor groups near-limit windows by session, ignores expired or sub-90%
windows, and uses the latest reset among constraining windows. A session enabled
at `force` gets one wakeup at that reset plus one minute. A session with no
policy gets one reminder. A session enabled at `interrupted` takes no part in
this route at all: it gets neither a schedule nor a reminder from percentages. Persistent event markers deduplicate repeated
minute-level checks; a failed side effect removes its marker so a later pass can
retry.

#### Refusal evidence

File: `claude_codex_cutoff.py`, part of the session-data source.

A percentage says a session may soon be refused. It does not say the session was
refused, and it does not say the session stopped. `detect_cutoff()` answers the
narrower question: did the provider refuse a turn for lack of quota, and is that
refusal still the last thing that happened?

Each adapter identifies its own refusal record and its own self-resume notices;
the abandonment rule is provider-neutral and positional. Position rather than
elapsed time is what separates the cases: Claude records its refusal as an
`assistant` row, so a timestamp comparison would read the refusal as progress
past itself, and a session that retries and is refused again shows a
one-second gap while a genuine post-reset resume shows a multi-hour one.

Every enabled session is scheduled from this evidence, using the reset time
stated by the refusal itself. `force` adds the percentage route on top rather
than replacing this one, so it is a superset of `interrupted`.

### CLI

Files: `cli.py`, `claude_codex_cli.py`, and `doctor.py`.

The public commands are `schedule`, `infer`, `auto`,
`capture-claude-usage`, `monitor`, and `doctor`. An empty argument list maps to
`infer`, enabling the `lw` post-timeout workflow. `run-due` is intentionally an
undocumented worker command used by the systemd service.

The CLI source exports three versioned process bindings:
`wakeup.interface.auto-policy`, `wakeup.interface.infer-schedule`, and
`wakeup.interface.explicit-schedule`. Each binding injects its own subcommand
prefix and exposes only that operation's arguments. The gateway's `Interface`
adapter delegates compiled argv to the same parser used by `llm-wakeup`, so the
human and dispatcher surfaces cannot drift into separate implementations.

Expected operational errors raise `WakeupError`; the CLI prints a concise
message and exits with status 2. Unexpected programming errors retain their
tracebacks.

## Job lifecycle

1. Resolve the provider session token to one canonical session and transcript.
2. Parse the reset instant and add the requested delay, one minute by default.
3. Hash meaningful transcript events and persist that state with the job.
4. Coalesce duplicate jobs for the same provider, session, and timer minute.
5. Let the persistent timer call `run-due` after the deadline, including after
   sleep or reboot.
6. Compare the current transcript hash with the scheduled hash.
7. Remove a stale job without invoking the provider when progress is detected.
8. For a job recorded at the `interrupted` level, re-detect the refusal in the
   transcript as it stands now and drop the job when it no longer holds.
9. Otherwise resume the exact session and remove the successfully delivered job.
10. On a transient operational failure, emit an event and retry in five minutes.

## Safety invariants

- A wakeup targets a canonical existing session, never a newly created chat.
- Transcript progress suppresses delivery.
- A conditional wakeup is delivered only while the refusal that created it is
  still the last thing in the transcript and the provider has not armed its own
  resume.
- Session aliases must resolve unambiguously.
- Duplicate commands in the same timer minute produce one persisted job.
- Only one due-job worker processes the queue at a time.
- Queue and policy writes are atomic and lock-protected.
- Quota monitoring reads local data and consumes no model context or quota.
- Automatic scheduling is opt-in per provider session.
- Provider resume commands receive argv arrays directly; no shell is involved.
- Monitor notification failure is retryable and cannot silently consume an
  event marker.

## Persistent state

The default root is `~/.local/share/llm-wakeup`; set `LLM_WAKEUP_HOME` to move
it for tests or a portable installation.

| Path | Purpose |
| --- | --- |
| `jobs.json` | Persistent wakeup queue; each job records the level that created it |
| `jobs.lock` | Queue writer lock |
| `session-policies.json` | Per-session auto-schedule policy and level |
| `session-policies.lock` | Policy writer lock |
| `usage-snapshots/` | Independent normalized Claude quota windows |
| `monitor-events/` | Deduplication markers, keyed `reminded:`, `scheduled:`, or `cutoff:` by provider, session, and reset |

Provider transcripts remain in provider-owned directories and are never copied
into this state root.

## Provider execution

Claude is resumed with `--print`, permission mode `auto`, and `WebFetch` plus
`WebSearch` pre-authorized. Codex is resumed through `exec resume` with approval
policy `never`, workspace-write sandboxing, outbound network access, and search
enabled. These choices make a background wakeup useful while retaining Codex's
workspace boundary.

Executable resolution checks `CLAUDE_EXECUTABLE` or `CODEX_EXECUTABLE`, then
`PATH`, then known user installation locations. An invalid explicit override is
reported rather than silently replaced.

## Extension points

Adding a provider requires a `ProviderAdapter` implementation for transcript
discovery, identity, aliases, rate limits, progress, working directory,
executable resolution, and resume argv. Register the adapter in
`providers/__init__.py`, add provider fixtures, and verify both inferred and
explicit scheduling. Provider-specific parsing belongs in the adapter; policy,
queueing, progress guards, retries, and monitoring remain provider-neutral.

## Test strategy

- Unit tests cover deadline formats, adapters, queue coalescing, locks, policy
  mutation, progress suppression, retries, monitoring, and CLI behavior.
- Refusal-evidence tests use record shapes copied from provider transcripts,
  including a forked rollout replaying its parent's refusal, and cover level
  migration, level precedence, and the delivery-time re-check.
- Synthetic transcript tests cover malformed and evolving provider records.
- Opt-in client integration tests invoke installed Claude and Codex clients and
  verify that their current local output remains parseable.
- Blueprint relationship validation checks source ownership and dependency
  edges.

Real-client tests are opt-in because they consume provider quota. Commands are
listed in the operator guide.
