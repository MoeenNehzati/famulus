---
name: online-calendar
description: >-
  Use when the user asks to view or change their Google Calendar. Do not use for daily planning.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `online-calendar._rtx.interface.scripts-gcal@1` — Query or modify Google Calendar events via the Python calendar CLI (agenda, search, create, update, delete, etc.).
  - `dispatcher --caller-skill online-calendar online-calendar._rtx.interface.scripts-gcal <mode shown below>`
  - token-or-calendars: `token` — Mint a bearer token only for an explicitly scoped one-off API operation; `calendars` — list accessible calendar IDs, roles, and names.
  - create-calendar: `create-calendar --summary TEXT [--description TEXT] [--color-id ID] [--timezone TZ]` — Timezone defaults to the local IANA zone.
  - agenda: `agenda [--calendar ID] [--all-calendars] [--from ISO] [--to ISO] [--days N]` — Calendar defaults to primary and the window to today in local time; ISO datetimes require a UTC offset; --days extends from --from or today; --all-calendars merges and time-sorts.
  - search: `search QUERY [--calendar ID] [--all-calendars] [--from ISO] [--to ISO] [--days N]` — Calendar defaults to primary and the window to the previous 7 days through the next 30 days; ISO datetimes require a UTC offset; --days extends from --from or today; --all-calendars merges and time-sorts.
  - get: `get --event-id ID [--calendar ID]` — Calendar defaults to primary.
  - create: `create --summary TEXT --start ISO --end ISO [--calendar ID] [--description TEXT] [--location TEXT] [--timezone TZ] [--all-day]` — Calendar defaults to primary and timezone to the local IANA zone; timed ISO values require a UTC offset; all-day end dates are exclusive.
  - update: `update --event-id ID [--calendar ID] [--summary TEXT] [--description TEXT] [--location TEXT] [--start ISO] [--end ISO] [--timezone TZ]` — Calendar defaults to primary; timed ISO values require a UTC offset; supply at least one changed field.
  - delete: `delete --event-id ID [--calendar ID]` — Calendar defaults to primary.
  - move: `move --event-id ID --to CALENDAR_ID [--from CALENDAR_ID]` — Source calendar defaults to primary.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `connect-google.interface.default@1` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
<!-- END BLUEPRINT INTERFACES -->
# Google Calendar

Use `online-calendar._rtx.interface.scripts-gcal` for calendar reads and writes. Invoke one
interface call per operation, minimize network round trips, and issue independent calls
in parallel. Use the public process contract for complete subcommand and option shapes;
do not improvise invocation forms. Prefer the `--all-calendars` mode for schedule-wide
agenda or search requests.

Reads may proceed directly. Create, update, and delete requests may proceed when
the user supplied the necessary details for create, update, or delete. Confirm first
when adding attendees because it sends invitations, and before deleting an event that
has attendees or is far in the future. Treat `move` as authorized only when the user
explicitly asked to move the identified event to the named destination calendar.
Report each mutation's title, time, calendar, and link when available.

## Routing calendar operations

Use `calendars` when the target calendar is unknown. Before `create`, match the event to
the available calendar names. Use a clear match without asking; ask when multiple or no
calendars plausibly fit. Do not silently use `primary` when another calendar is clearly
more appropriate.

Agenda and search results are bounded to 50 events and do not paginate. Disclose that
bound when the requested range may contain more results. Recurrence, attendees, and
free-busy lack dedicated modes. For a genuine one-off unsupported API operation, use
the declared token mode only when direct API access is explicitly in scope, keep the
token secret, and apply the same confirmation rules. Prefer extending the public
interface for repeated use.

## Verify writes

Through `online-calendar._rtx.interface.scripts-gcal`, verify every create and delete and every
nontrivial update. The only exception is a metadata-only update limited to summary,
description, or location.

- After `create`, fetch the returned event ID and compare start, end, summary,
  location, and description with the request. On mismatch, delete the new event and
  report the intended and observed values.
- Before `update`, fetch and retain the fields being changed. Fetch again afterward.
  On mismatch, restore the retained values and report the discrepancy.
- After `delete`, fetch the event and require status `cancelled`; Google soft-deletes
  events rather than returning not-found. If it remains confirmed, report failure.

When completion is uncertain, inspect current event state before retrying; repeated
creates can produce duplicates.

## Google authorization

For setup or reauthorization, invoke `connect-google.interface.default`. Its
deterministic coordinator creates a credential file, asks Calendar's owner to
probe live access, and stores the path only after verification. Treat only
`complete: true` as successful setup; report any incomplete Calendar result and
retry through connect-google with the same file.

Existing legacy Calendar credentials remain runtime-readable until a verified
credential-file binding replaces them. Do not offer legacy setup as a new route.
Replacing the single active Calendar account requires explicit confirmation.
