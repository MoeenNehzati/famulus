---
name: g-calendar
description: Use when the user asks to read or change Google Calendar events, calendars, schedules, meetings, or availability.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: planning, personal-organization, external-integrations; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `g-calendar.source.gateway -> connect-google.interface.default@1`

Public Interfaces:
- `g-calendar.interface.default`
- `g-calendar.interface.ensure-oauth`
- `g-calendar.interface.scripts-gcal`
- `g-calendar.interface.setup-oauth`
- `g-calendar.interface.use-google-credential`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `g-calendar.interface.ensure-oauth` — Check g-calendar OAuth status; print setup guidance or launch browser authorization as needed. Relocated from install-assistant-tools — invoke directly (caller-skill g-calendar) as part of connecting remotes.
  - `dispatcher --caller-skill g-calendar g-calendar.interface.ensure-oauth --home <dir> [--dry-run]`
  - Check OAuth status and guide setup for g-calendar.
- `g-calendar.interface.scripts-gcal` — Query or modify Google Calendar events via the Python calendar CLI (agenda, search, create, update, delete, etc.).
  - `dispatcher --caller-skill g-calendar g-calendar.interface.scripts-gcal <mode shown below>`
  - token-or-calendars: `token` — Mint a bearer token only for an explicitly scoped one-off API operation; `calendars` — list accessible calendar IDs, roles, and names.
  - create-calendar: `create-calendar --summary TEXT [--description TEXT] [--color-id ID] [--timezone TZ]` — Timezone defaults to the local IANA zone.
  - agenda: `agenda [--calendar ID] [--all-calendars] [--from ISO] [--to ISO] [--days N]` — Calendar defaults to primary and the window to today in local time; ISO datetimes require a UTC offset; --days extends from --from or today; --all-calendars merges and time-sorts.
  - search: `search QUERY [--calendar ID] [--all-calendars] [--from ISO] [--to ISO] [--days N]` — Calendar defaults to primary and the window to the previous 7 days through the next 30 days; ISO datetimes require a UTC offset; --days extends from --from or today; --all-calendars merges and time-sorts.
  - get: `get --event-id ID [--calendar ID]` — Calendar defaults to primary.
  - create: `create --summary TEXT --start ISO --end ISO [--calendar ID] [--description TEXT] [--location TEXT] [--timezone TZ] [--all-day]` — Calendar defaults to primary and timezone to the local IANA zone; timed ISO values require a UTC offset; all-day end dates are exclusive.
  - update: `update --event-id ID [--calendar ID] [--summary TEXT] [--description TEXT] [--location TEXT] [--start ISO] [--end ISO] [--timezone TZ]` — Calendar defaults to primary; timed ISO values require a UTC offset; supply at least one changed field.
  - delete: `delete --event-id ID [--calendar ID]` — Calendar defaults to primary.
  - move: `move --event-id ID --to CALENDAR_ID [--from CALENDAR_ID]` — Source calendar defaults to primary.
- `g-calendar.interface.setup-oauth` — Run the OAuth setup flow to generate or refresh Google Calendar credentials.
  - `dispatcher --caller-skill g-calendar g-calendar.interface.setup-oauth [--from-json /path/to/client.json]`
  - OAuth setup for Google Calendar access.
- `g-calendar.interface.use-google-credential` — Bind g-calendar to a shared connect-google credential_id after validating it carries Calendar scope, storing only the opaque identifier (never the client secret or refresh token) in g-calendar's own config.json. The pre-existing per-service OAuth path (ensure-oauth) remains the unchanged fallback for callers who have not adopted the shared credential.
  - `dispatcher --caller-skill g-calendar g-calendar.interface.use-google-credential --credential-id <id> --home <dir>`
  - Bind g-calendar to a shared Google credential_id.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `g-calendar.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# Google Calendar

Use `g-calendar.interface.scripts-gcal` for calendar reads and writes. Invoke one
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

Through `g-calendar.interface.scripts-gcal`, verify every create and delete and every
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

Use the shared route first. Retain the two legacy routes only as the fallback.

| Situation | Route | Interpret the result |
|---|---|---|
| `Shared setup or reauthorization` | `connect-google.interface.default -> g-calendar.interface.use-google-credential` | Bind the Calendar-granted credential_id using the same registry home, then verify with calendars and agenda. |
| `Legacy status or recovery` | `g-calendar.interface.ensure-oauth` | Check readiness and guide or launch the fallback flow. |
| `Direct legacy setup` | `g-calendar.interface.setup-oauth` | Use only for a Calendar configuration that has not adopted a shared credential. |

Replacing the single active Calendar account requires explicit confirmation.
