# LLM wakeup

`officina.wakeup` schedules guarded continuation messages for rate-limited
Claude and Codex sessions. It is deterministic Python infrastructure, not a
skill, and has no database or third-party runtime dependency.

## Interfaces

Explicit scheduling:

```bash
llm-wakeup schedule claude SESSION TIME [--message MESSAGE]
llm-wakeup schedule codex SESSION TIME [--message MESSAGE]
```

Inference from timeout output, piped stdin, or the newest structured provider
rate-limit record:

```bash
llm-wakeup infer [--text TIMEOUT_OUTPUT] [--message MESSAGE]
```

When `--message` is omitted, the helper asks the session to resume its previous
task and revive any stale subagents.

## Guard and persistence

Each job stores the hash of the session's latest meaningful user or assistant
turn. At delivery time, a changed hash means the conversation progressed after
scheduling. The worker removes that job without sending its message.

Jobs live in `~/.local/share/llm-wakeup/jobs.json` by default. Writes are
serialized with `flock` and committed through atomic replacement. Set
`LLM_WAKEUP_HOME` to override the state directory.

## Worker and logging

`llm-wakeup run-due` is the internal worker interface. The supplied systemd
timer invokes it once per minute with `Persistent=true`, so a deadline missed
while the computer is asleep or powered off is processed after startup. Worker
events are written to standard output for journald:

```bash
journalctl --user-unit llm-wakeup.service
```

Expected events include `sent`, `skipped reason=session-progressed`,
`delivery-error`, `transcript-error`, and `scanner-busy`. Failed delivery and
transcript reads remain queued and retry after five minutes.

## Environment overrides

| Variable | Purpose |
| --- | --- |
| `LLM_WAKEUP_HOME` | Queue and lock directory |
| `LLM_WAKEUP_CLAUDE_DIR` | Claude transcript root |
| `LLM_WAKEUP_CODEX_DIR` | Codex transcript root |
| `LLM_WAKEUP_CODEX_INDEX` | Codex session-name index |
| `LLM_WAKEUP_CLAUDE_BIN` | Explicit Claude executable |
| `LLM_WAKEUP_CODEX_BIN` | Explicit Codex executable |
| `LLM_WAKEUP_NOW` | Deterministic reference time for tests |
