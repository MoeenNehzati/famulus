# Personal Assistance Quickstart

Famulus's personal-assistance subsystem is organized around two core lists
managed by `list-manager`: `todo`, for actions you have committed to, and
`triage`, for possibilities awaiting your decision. The surrounding skills
capture items, plan work from them, and reconcile outcomes back into the same
persistent state.

## Start here

1. Ask `connect-google` to connect the Google services you want to use.
2. Ask `list-manager` to initialize the `todo` and `triage` lists.
3. Ask `daily-plan` to plan the day.

Google-backed workflows are experimental in the first public release.
Recurring assistant jobs are also experimental and must be enabled explicitly.
Review the [security and privacy boundary](../security-and-privacy.md) before
connecting an account or scheduling a job.

## How a day fits together

### Lists and inbox triage

`list-manager` governs the two core lists. `todo` contains committed actions
and uses the states `incomplete`, `inprogress`, and `complete`. `triage`
contains suggestions awaiting a decision and uses `undecided`, `accepted`, and
`rejected`. Accepting a triage item creates a matching incomplete todo item.
Items have a title, deadline, and state, and may also include a description or
physical location.

`email-triage` reads new mail through `email-client` and identifies possible
actions. Explicit obligations such as bills, owed replies, and follow-up
commitments go to `todo`; optional opportunities such as seminars, calls for
papers, and signups go to `triage` for your decision.

### Daily planning

`daily-plan` combines today's calendar, upcoming birthday events, the weather,
deadline-prioritized todo items, and undecided triage items. It presents the
list items alongside an approximate free-time estimate; you decide what fits
into the day. List decisions and completed actions remain aligned with the
stored daily plan.

Use the plan to choose what to do today, then report progress as the day
continues. For a calendar-only, email-only, list-only, or weather-only request,
use the corresponding narrower skill from the table above.

### Session continuity and wrap-up

After a substantial work session, use `prepare-handoff` to check that the work
is preserved for a future session or collaborator. It can also record lessons
such as failed approaches and why they failed when that information would
change future work.

If a session must pause for a usage reset or timeout, use `llm-wakeup` to
schedule a guarded continuation of that session.

At the end of the day, use `wrap-up`. It reviews the daily plan, collects one
update about completed and unplanned work, and updates the plan and lists. It
also uses `find-handoff-candidates` to identify recent sessions that may still
need `prepare-handoff` and adds them to `triage` for review.

## What the model and Python each do

The model handles interpretation: for example, deciding whether an email
contains an obligation or merely an opportunity, and helping you choose what
to do today. Python tools handle the repeatable mechanics around that judgment,
including filtering previously processed mail, validating list changes,
ordering items, persisting plans, and tracking scheduled runs.

## Automate repeated steps

Use `recurring-tasks` when you want inbox triage or daily planning to run
automatically in the background. Scheduled `email-triage` runs can add items to
the two lists, and scheduled `daily-plan` runs can prepare a plan from their
current state. No recurring jobs are enabled by default: configure and test the
underlying skill first, then see the [Automation Quickstart](automation.md).

## What to use when

| Need | Skill |
|---|---|
| Connect or restore Google Drive, Calendar, or Gmail | `connect-google` |
| Initialize, inspect, or change a persistent list; accept a triage item | `list-manager` |
| Plan the day or review today's plan | `daily-plan` |
| Process the whole inbox for possible actions | `email-triage` |
| Read, reply to, send, or otherwise manage specific email | `email-client` |
| View or change calendar events without making a daily plan | `online-calendar` |
| Check weather without making a daily plan | `get-weather` |
| Schedule daily planning, inbox triage, or another assistant job | [`recurring-tasks`](automation.md) |
| Preserve decisions and lessons from a substantial work session | `prepare-handoff` |
| Resume a session after a usage reset or timeout | `llm-wakeup` |
| Review progress and close the day | `wrap-up` |
| Report a failed or incorrect Famulus workflow | `send-feedback` |

`cloud-files` provides storage beneath the lists and plans. Users normally
reach it through `list-manager` and `daily-plan` rather than invoking it
directly.

### Reporting problems

If a Famulus workflow fails or behaves incorrectly, use `send-feedback`. It
prepares a redacted report from the current session and sends it through
`email-client` only after the user reviews and approves the complete message.
