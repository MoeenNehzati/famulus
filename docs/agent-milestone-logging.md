# Agent Milestone Logging

Every harness already records a mechanical trace of each agent: every tool call
with a real timestamp. That trace carries no intent. From it you cannot tell
what the agent was trying to do, or whether it worked. Milestone logging adds
the missing half, so a run can be followed while it happens and read afterwards
for where the time went and which of it was wasted.

## The Three Pieces

**The root bootstrap.** The `## Milestone logging` section of root `CLAUDE.md`
(also reached through the tracked `AGENTS.md` symlink and the installed root
instruction link) requires `milestone-logging` in every main-agent and
subagent session before substantive work begins. It is deliberately compact:
the skill owns the protocol details.

**The skill-owned protocol and runtime.** `milestone-logging` exposes
`milestone-logging.interface.default`, which uses the
`milestone-logging._rtx.interface.record@1` and
`milestone-logging._rtx.interface.timeline@1` runtime interfaces. The record
route composes the log path, timestamp, and JSON record; the timeline route
merges milestone logs with harness transcripts. Agents follow the skill through
its private `record` and `timeline` runtime routes; the public instruction
interface owns when and how those routes are used. The stable `milestone` and
`agent-timeline` commands are retained for compatibility and human diagnostics.

**The installer-owned projection.** `install-assistant-tools` projects those
stable commands into the selected installation context's command directory and
records the links in that context's install manifest. Its small launcher assets
dispatch through explicitly authorized skill runtime interfaces, so the
installer does not reach into another skill's private files. The installer owns
this compatibility layer; the skill owns the logging behavior. Apply the
context wherever the root instruction is delivered.

`CLAUDE.md`/`AGENTS.md` is delivered by the harness to every subagent on both
supported hosts, so no cooperation from the spawning parent is needed. Two
alternatives were tried and do not work: content injected by a `SessionStart`
hook is not delivered to subagents, and `@file` imports do not expand inside
agent definition files under `.claude/agents/`.

## Why A Script Instead Of More Instruction

The session identifier comes from an environment variable, and the variable
differs per host: the writer's `session_id()` reads `CLAUDE_CODE_SESSION_ID`,
then `CODEX_SESSION_ID`, and falls back to `unknown`. An earlier design had each
agent derive the session id from its scratchpad directory path instead. Real
logs show that failing silently — every such session landed in `unknown`. The
script exists so no agent ever composes a path or a timestamp by hand.

The two hosts also differ in self-knowledge. On the first, subagents are told
they are subagents; on the second they are not, and cannot see their own task
name even when spawn options set one. This is why `--role` is supplied by the
agent as free text rather than read from the environment.

## Calling It

```bash
milestone "<what you are starting now>" "<how the previous piece ended>"
milestone --role "trace config loading" "locate the loader"
milestone --done "<how the last piece ended>"
milestone --path          # print the log path and exit
```

A job that outlives the session that started it adds `--run`:

```bash
milestone --run nightly-01 --event run-start --step 1 "sweep the corpus" ""
milestone --run nightly-01 --event task --task extract --state failed --attempt 1 \
          "audit the failure" "schema audit rejected 3 records"
milestone --run nightly-01 --path    # print the run journal path and exit
```

## Log Format

One JSON object per line is appended below the active context's log root. The
standalone writer falls back to
`~/.assistant-logs/<YYYY-MM-DD>/<session>.<agent>.jsonl` when no context is
available. `ASSISTANT_LOGS` is a process-local compatibility override only;
the installer does not persist it, and shell or scheduler configuration should
not either.

The fields are `ts`, `role`, `cwd`, `doing`, and `prev`; a record written with
`--run` carries further optional keys, described under
[The Run Journal](#the-run-journal). `doing` is what is
starting now, and is the literal `(done)` on the final line. `prev` is how the
preceding piece ended — the outcome, not a restatement of the plan. Free-text
fields are truncated at 200 characters.

No duration is ever recorded. Consecutive timestamps bound each piece, which is
what the reader uses to flag slow stretches. Concurrent appends from parallel
agents are safe: each record is a single `O_APPEND` write and lines stay well
under 4KB.

On the second host the per-agent component is the thread id; on the first the
harness exposes no per-agent identifier and each tool call runs in a fresh
shell, so the whole session shares one file and `role` is what separates the
agents on read.

## Reading A Timeline

```bash
agent-timeline --list          # known sessions, oldest first
agent-timeline <session-id>    # merged timeline; default is the newest session
agent-timeline <session-id> --slow 30
agent-timeline --run <run-id>          # one run, across every session that wrote to it
agent-timeline --run <run-id> --json   # the same reconstruction, machine-readable
```

Milestone rows are marked `▸`; unmarked rows are tool calls from the transcript.
Each row shows wall-clock time, seconds since the session started, and the agent
label. A gap of at least `--slow` seconds (default 10) since the previous row is
marked `«` at the end of the line.

```
14:35:40 +    0s  canonical graph auditor  ▸ Verify outgoing uses of the Lipschitz result
                                             prev: six incoming proof dependencies found
14:35:52 +   12s  canonical graph auditor    Bash  grep -rn "lipschitz" src/ «
14:36:31 +   51s  canonical graph auditor    Read  model.tex «
14:50:01 +  861s  canonical graph auditor  ▸ (done) «
                                             prev: consumed once outside appendix scope
```

Read it as: the agent announced its question, spent about fifteen minutes on it,
and the `prev:` line on the last row says what came of it. The `«` marks are
where to look first — a long gap before a `(done)` whose `prev` reports nothing
useful is exactly the wasted effort the log is meant to surface.

## Changing The Instruction

The instruction file is read once, when a session starts. Editing the
`## Milestone logging` section has no effect on any agent in a session that was
already running, including subagents it spawns — they inherit the snapshot taken
at start. Test a wording change in a session started afterwards, and confirm
delivery by asking one agent to quote the section back before concluding that
agents are ignoring it.

## The Run Journal

A milestone log is addressed by session, and an overnight job outlives the
session that started it: the first assistant session ends, a later one resumes
the work, and each writes under its own session and thread id. `--run` adds the
second address. The record is written unchanged to the session log **and**
mirrored into one journal per run, below the same log root the session logs
resolve to (see [Log Format](#log-format)):

```
<log root>/runs/<run-id>.jsonl
```

That path sits beside the day directories rather than inside one, because a
long run spans midnight and splitting its journal by day would put recovery
back in the business of merging files. It is the same append-only, one-line
`O_APPEND` write, so several sessions writing at once is as safe here as it
already was for the session log. Append order *is* the order: recovery never
compares timestamps to sequence events that different sessions contributed.

A run id becomes a path component and a lookup key, so it must match
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}` — no separator, no leading dot, 64 characters
at most. Both the writer and the reader reject anything else rather than
normalizing it; `--run ""` is a rejected id, not an absent one.

### The Structured Fields

`doing` and `prev` stay exactly what they were, and recovery never reads them.
Anything a resuming agent must act on goes in a typed field instead:

| Flag | Key | Meaning |
| --- | --- | --- |
| `--run ID` | `run` | the durable run this belongs to |
| `--event NAME` | `event` | what kind of event this is, e.g. `run-start`, `task`, `run-end` |
| `--step N` | `step` | the current numbered algorithm step |
| `--task ID` | `task` | which task this concerns |
| `--state NAME` | `state` | the state that task is now in, e.g. `started`, `failed`, `skipped` |
| `--attempt N` | `attempt` | attempt or repair round for that task |
| `--evidence PATH` | `evidence` | supporting evidence; repeatable, becomes a list |

`--evidence` is the only repeatable field, so it is the only one that can push
a record past the roughly 4KB single write the interleave guarantee is stated
in terms of. When it would, the latest paths are dropped until the record fits
and `evidence_dropped` records how many went — the record never shrinks
silently. Every other field is capped individually, as before.

The vocabulary of `event` and `state` is deliberately *not* fixed here. This
layer records and returns what the caller supplied; whichever pipeline owns the
run owns its own state machine. Three consequences follow. A field that is not
passed leaves no key behind, so a record never claims a state it was not told.
Records that carry a run also carry `run`, `session` and `agent`, because the
journal is read without its filename for context. And passing any typed field
without `--run` is an error, not a silent human-only entry — structured data
outside a journal could never be recovered.

### One Run, Start To Finish

```bash
milestone --run nightly-01 --event run-start --step 1 "sweep the corpus" ""
milestone --run nightly-01 --event task --task extract --state started --attempt 1 \
          "extract entities" "step 1 opened 412 sources"
# ... the assistant session ends here; a later one picks the run back up ...
milestone --run nightly-01 --event task --task extract --state failed --attempt 1 \
          "audit the failure" "schema audit rejected 3 records"
milestone --run nightly-01 --event task --task extract --state succeeded --attempt 2 \
          --evidence out/extract.json "render the graph" "second attempt passed the audit"
milestone --run nightly-01 --event task --task preview --state skipped \
          "close out" "preview needs a browser this host lacks"
milestone --run nightly-01 --event run-end --step 9 --done "graph written to out/graph.json"
```

```
$ agent-timeline --run nightly-01
run nightly-01
----------------------------------------------------------------------
02:14:09 +     0s  sess-one/session  ▸ sweep the corpus
                                      event=run-start step=1
02:14:31 +    22s  sess-one/session  ▸ extract entities
                                      event=task task=extract state=started attempt=1
                                      prev: step 1 opened 412 sources
05:02:55 + 10126s  sess-two/session  ▸ audit the failure
                                      event=task task=extract state=failed attempt=1
                                      prev: schema audit rejected 3 records
05:31:02 + 11813s  sess-two/session  ▸ render the graph
                                      event=task task=extract state=succeeded attempt=2
                                      prev: second attempt passed the audit
                                      evidence: out/extract.json
05:44:18 + 12609s  sess-two/session  ▸ close out
                                      event=task task=preview state=skipped
                                      prev: preview needs a browser this host lacks
05:51:40 + 13051s  sess-two/session  ▸ (done)
                                      event=run-end step=9
                                      prev: graph written to out/graph.json

6 events from 2 session(s) — /home/you/.assistant-logs/runs/nightly-01.jsonl
```

`agent-timeline --run <id> --json` prints the same reconstruction as a
machine-readable object — `run`, `path`, `sessions`, `agents`, `events` (the records
verbatim, in append order) and `malformed` — which is what a resuming agent
reads instead of the rendering.

### Damage And Absence

A line that is not JSON, is not a JSON object, or carries no readable `ts` is
reported in `malformed` with its line number and the reason, and is counted in
the rendering. It is never dropped quietly: a recovering agent must not mistake
a damaged journal for a short one. Reading a run that was never logged exits
non-zero rather than rendering an empty run, so "nothing happened" and "I
cannot find it" stay distinguishable.

What this layer does *not* do is adjudicate the content of well-formed records.
It does not validate state transitions, reconcile two conflicting terminal
events, or pick between duplicate revisions. It returns every event in order
and leaves that judgment to whichever pipeline owns the run.

### What Did Not Change

Callers that pass no `--run` are unaffected: same flags, same log path, and a
record with exactly the original `ts`, `role`, `cwd`, `doing`, `prev` keys and
no others. Records written before run journals existed stay readable, since
every new key is optional and read with a default. `agent-timeline` without
`--run` behaves as it always did, and `--list` does not offer run journals as
sessions — `runs/` is excluded from the session glob.

## Limitations

- Installer projection and its extension-less compatibility launchers are
  POSIX-only. Windows support is not provided by this workflow.
- Sessions with no available session id are all named `unknown`. The reader
  keeps them apart by filename, so each renders alone, but they carry no id to
  tie them back to a harness transcript.
- On the second host the transcript file is named by thread id while the session
  id sits inside the file; the two coincide only for the root thread. The reader
  compensates by matching every thread id seen in the milestone logs, but a
  subagent that logged nothing cannot be found this way.
- There is no rotation or pruning. The log directory grows without bound.
- A checkout whose development context has not been applied may have the
  instruction but no `milestone` on its isolated command path. Apply that
  context before treating logging as available.
- A run journal is written only when a caller passes `--run`. Nothing infers
  one, so a run whose milestones were logged without it cannot be reassembled
  afterwards except by reading `doing` and `prev` by eye.
- The journal duplicates each record into a second file. A write that succeeds
  for the session log and fails for the journal reports the error, but leaves
  the two out of step by one line.
- Compliance is best-effort. The instruction is followed by a model, not enforced
  by the harness, so a missing milestone means nothing was logged — not that
  nothing happened.
