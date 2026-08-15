# Personal Assistance

This domain covers day-to-day workflows that read or update personal planning
information: daily plans, inbox triage, calendars, weather, lists, feedback,
and end-of-day wrap-up.

Google-backed workflows are experimental in the first public release. Any
agent-driven recurring use of `daily-plan`, `email-triage`, or related skills
is also experimental and must be enabled explicitly. See the
[security and privacy boundary](../security-and-privacy.md) before connecting
an account or scheduling a job.

Example prompts:

- `Plan my day.`
- `Triage my inbox.`
- `Show my todo list.`
- `What is the weather before my afternoon meeting?`
- `Wrap up today.`

Typical flow:

1. Start with `daily-plan` to combine calendar, weather, todos, and triage items.
2. Work from that plan during the day.
3. Finish with `wrap-up` so the plan is updated and follow-up items land in the
   appropriate lists.

<!-- BEGIN AUTO-GENERATED DOCS: personal-assistance -->
> Generated from live blueprints. Do not edit this block by hand.

- `daily-plan` — Generate today's plan from calendar, todos, and weather
- `email-client` — Read, search, and send email across configured accounts
- `email-triage` — Triage the inbox into todo and triage lists since the last run
- `g-calendar` — Read and modify Google Calendar via a local OAuth CLI
- `get-weather` — Fetch weather for a location, day, or date range
- `list-manager` — Manage personal YAML lists in cloud storage
- `send-feedback` — Send feedback, report a problem, or describe a failed Famulus workflow to its maintainer
- `wrap-up` — Review the day, record completions, and capture follow-up items
<!-- END AUTO-GENERATED DOCS: personal-assistance -->
