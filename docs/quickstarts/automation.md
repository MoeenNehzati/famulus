# Automation Quickstart

Use `recurring-tasks` for assistant work that should run repeatedly without an
interactive request each time. Invoke the underlying skill directly for a
one-off run, and use `llm-wakeup` rather than recurring automation for resuming
one session after a usage reset or timeout.

## Before enabling a job

1. Configure the assistant launchers with `install-launchers` if the job needs
   unattended agent execution.
2. Configure and test the skill the job will invoke. For personal planning and
   inbox automation, complete the
   [Personal Assistance Quickstart](personal-assistance.md) first.
3. Review the [unattended execution boundary](../security-and-privacy.md#authorization-and-confirmation-boundaries).
   A scheduled job cannot stop for questions or approvals.

Recurring jobs are experimental in the first public release and are never
enabled merely because their underlying skill is installed.

## Platform support and help

Recurring scheduling is implemented for Linux, macOS, and Windows, but it has
been thoroughly tested only on Linux. Scheduler setup, triggering, status, or
health checks may therefore fail in platform-specific ways on macOS or
Windows.

If automation produces an error, show the exact error to the assistant and ask
it to diagnose and help resolve the problem. If the issue remains unresolved or
would be useful to the Famulus maintainer, invoke `send-feedback` in that same
session. It will prepare a redacted report from the established evidence and
send it only after you review and approve the complete message.

## What to use when

| Need | Skill |
|---|---|
| Configure assistant commands and background execution support | `install-launchers` |
| Connect Google services needed by a job | `connect-google` |
| Enable, disable, test, inspect, or repair a recurring assistant job | `recurring-tasks` |
| Run the work once right now | Invoke the underlying skill directly |
| Resume one assistant session after a reset or timeout | `llm-wakeup` |
| Follow what an unattended run actually did, during or after it | `milestone-logging` |

## Enable and verify

Ask `recurring-tasks` to enable the named job and schedule. After setup, test
the job once and confirm that it produced the expected result. If it fails or
stops running, use `recurring-tasks` to inspect the failure and help repair it.

Common recurring workflows include inbox triage and daily planning:
`email-triage` updates the personal lists, while `daily-plan` creates the day's
plan.

## Operate and troubleshoot

Use `recurring-tasks` to inspect a failed job, change its schedule, or disable
it when you no longer want it to run.

A failed job leaves a transcript of tool calls but no account of what the
assistant was trying to do, which is rarely enough to tell a broken job from a
job that ran and found nothing. `milestone-logging` is the other half: a job
that records its milestones under a run id can be read back through its
generated `timeline` interface via the shared `famulus` MCP server, even after
the session that started it has ended. See [Agent milestone
logging](../agent-milestone-logging.md).
