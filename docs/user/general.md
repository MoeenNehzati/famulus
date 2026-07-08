# General Assistant Workflows

This page covers the day-to-day assistant workflows: planning, wrap-up, inbox triage, calendar lookups, weather context, and list-backed follow-up.

## Productivity

Use these skills when you want the assistant to fetch or update concrete personal information such as calendars, inbox state, weather, or structured lists.

Example prompts:

- `Triage my inbox.`
- `Show my todo list.`
- `What is the weather before my afternoon meeting?`
- `Add this to my shopping list.`

<!-- BEGIN AUTO-GENERATED DOCS: productivity-general-assistant -->
> Generated from live blueprints. Do not edit this block by hand.

- `email-client` — Read, search, and send email across configured accounts
- `email-triage` — Triage the inbox into todo and triage lists since the last run
- `g-calendar` — Read and modify Google Calendar via a local OAuth CLI
- `get-weather` — Fetch weather for a location, day, or date range
- `list-manager` — Manage personal YAML lists in cloud storage
<!-- END AUTO-GENERATED DOCS: productivity-general-assistant -->

## Coordination

Use these skills when you want the assistant to coordinate a session or a day rather than fetch one isolated piece of information.

Example prompts:

- `Plan my day.`
- `Refresh today's plan from my current lists.`
- `Wrap up today.`
- `Prepare a handoff before I stop.`

Typical flow:

1. Start with `daily-plan` to combine calendar, weather, todos, and triage items.
2. Work from that plan during the day.
3. Finish with `wrap-up` so the plan is updated, follow-up items land in the right lists, and sessions missing a proper handoff are surfaced for review.
4. Use `prepare-handoff` when a session produced decisions, failed paths, or repo changes that the next session should inherit directly from the repo.

<!-- BEGIN AUTO-GENERATED DOCS: workflow-general-assistant -->
> Generated from live blueprints. Do not edit this block by hand.

- `daily-plan` — Generate today's plan from calendar, todos, and weather
- `find-handoff-candidates` — You need a mechanical, non-interpretive scan of today's (or another day's) work sessions to find ones that had substantial activity but no completed handoff
- `loose-mode` — Broad, fast exploration mode with breadth over certainty
- `prepare-handoff` — Prepare a clean handoff with workflow and documentation updates
- `tight-mode` — Rigorous, verified output mode with certainty over speed
- `tool-applicability` — Check whether a theorem or framework achieves a target in the current setting
- `wrap-up` — Review the day, record completions, and capture follow-up items
<!-- END AUTO-GENERATED DOCS: workflow-general-assistant -->
