# Personal Assistance

The personal-assistance domain is organized around `list-manager` and two core
lists: `todo` for committed actions and `triage` for possibilities awaiting a
decision. Other skills produce, consume, or reconcile this shared state:
`email-triage` captures actions, `daily-plan` uses list items in planning, and
`wrap-up` records outcomes and unresolved handoffs. These model-directed skills
use Python tools for repeatable operations such as validation, persistence,
ordering, filtering, and run-state tracking.

For first-time setup, skill routing, and the typical daily sequence, see the
[Personal Assistance Quickstart](../quickstarts/personal-assistance.md).

<!-- BEGIN AUTO-GENERATED DOCS: personal-assistance -->
> Generated from live blueprints. Do not edit this block by hand.

- `daily-plan` — Generate today's plan from calendar, todos, and weather
- `email-client` — Read, search, and send email across configured accounts
- `email-triage` — Triage the inbox into todo and triage lists since the last run
- `get-weather` — Fetch weather for a location, day, or date range
- `list-manager` — Manage personal YAML lists in cloud storage
- `online-calendar` — Read and modify Google Calendar via a local OAuth CLI
- `send-feedback` — Send feedback, report a problem, or describe a failed Famulus workflow to its maintainer
- `wrap-up` — Review the day, update plans and lists, and find handoff candidates via find-handoff-candidates
<!-- END AUTO-GENERATED DOCS: personal-assistance -->
