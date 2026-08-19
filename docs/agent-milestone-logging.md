# Agent Milestone Logging

Every harness already records a mechanical trace of each agent: every tool call
with a real timestamp. That trace carries no intent. From it you cannot tell
what the agent was trying to do, or whether it worked. Milestone logging adds
the missing half, so a run can be followed while it happens and read afterwards
for where the time went and which of it was wasted.

## The Three Pieces

**The instruction.** The `## Milestone logging` section of `CLAUDE.md` at the
repository root (the same file is reachable as `AGENTS.md` and as
`~/.claude/CLAUDE.md` through symlinks). It tells an agent to call the writer
before each distinct piece of work and once at the end.

**The writer**, `scripts/milestone.py`, on PATH as `milestone`. Agents call it;
it composes the log path, the timestamp, and the JSON record itself.

**The reader**, `scripts/agent-timeline.py`, on PATH as `agent-timeline`. It
merges the milestone logs with the harness transcripts into one chronological
timeline.

Both are linked into the user's bin directory by `install-assistant-tools`, and
those links are recorded in the install manifest, so uninstall removes them.
The instruction names `milestone` as a command, so the link must exist wherever
the instruction is delivered.

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

## Log Format

One JSON object per line, appended to
`$ASSISTANT_LOGS/<YYYY-MM-DD>/<session>.<agent>.jsonl`. `ASSISTANT_LOGS` is set
by the installer (`skills/install-assistant-tools/_rtx/_config_bridge.py`) and
defaults to `~/.assistant-logs`.

The fields are `ts`, `role`, `cwd`, `doing`, and `prev`. `doing` is what is
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

## Limitations

- Sessions with no available session id are all named `unknown`. The reader
  keeps them apart by filename, so each renders alone, but they carry no id to
  tie them back to a harness transcript.
- On the second host the transcript file is named by thread id while the session
  id sits inside the file; the two coincide only for the root thread. The reader
  compensates by matching every thread id seen in the milestone logs, but a
  subagent that logged nothing cannot be found this way.
- There is no rotation or pruning. The log directory grows without bound.
- A checkout whose install has not run yet has the instruction from `CLAUDE.md`
  but no `milestone` on PATH, and the calls fail quietly — a non-zero exit inside
  one shell call, with nothing logged to say why.
- Compliance is best-effort. The instruction is followed by a model, not enforced
  by the harness, so a missing milestone means nothing was logged — not that
  nothing happened.
