---
name: g-calendar
description: |
  Read and modify the user's Google Calendar (list calendars, check agenda,
  search events, create/update/delete events) via a local OAuth-backed
  CLI - no MCP involved. Use when the user asks about their
  schedule, meetings, availability, or wants to add/move/cancel a calendar
  event.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: productivity-general-assistant

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `ensure-oauth` — Check g-calendar OAuth status; print setup guidance or launch browser authorization as needed. Relocated from install-assistant-tools — invoke directly (caller-skill g-calendar) as part of connecting remotes.
  - `dispatcher --caller-skill g-calendar g-calendar ensure-oauth --home <dir> [--dry-run]`
  - Check OAuth status and guide setup for g-calendar.
- `scripts-gcal` — Query or modify Google Calendar events via the gcal.sh CLI (agenda, search, create, update, delete, etc.).
  - `dispatcher --caller-skill g-calendar g-calendar scripts-gcal <command> [options]`
- `setup-oauth` — Run the OAuth setup flow to generate or refresh Google Calendar credentials.
  - `dispatcher --caller-skill g-calendar g-calendar setup-oauth [--from-json /path/to/client.json]`
  - OAuth setup for Google Calendar access.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: g-calendar

## 0. Read this first

- **Only use the `scripts-gcal` interface.** Every calendar operation must go
  through `scripts-gcal` with the appropriate subcommand — the entire call,
  nothing else on the line. No `cd`, `python3`, `date`, variable assignments,
  `&&`/`;`/pipes/loops. This is the only allow-listed pattern; anything else
  triggers a permission prompt. For N operations, issue N separate calls, one
  `scripts-gcal` invocation each.
- **Minimize invocations, then parallelize what's left.** Each call is a slow
  network round-trip. Prefer `--all-calendars` over looping per calendar.
  When you do need multiple independent calls (e.g. `get` on several event
  ids), issue them all in one message. Only sequence calls when one needs the
  result of a previous one.
- **Reads** (`calendars`, `agenda`, `search`, `get`, `token`): just run them.
- **`create`/`update`/`delete`**: just do it, then report what changed (title,
  time, link). Exceptions requiring confirmation first:
  - adding **attendees/guests** (sends email invitations - shared-state
    action), or
  - a `delete` that looks important (has attendees, far in the future).
- **QC every write** - see section 3. Not optional; catches silent
  wrong-day/time writes.

## 1. What this is

The `scripts-gcal` interface talks directly to the Google Calendar API v3,
using a locally stored refresh token. The exported interface is still
`gcal.sh`, but that shell entrypoint is now only a thin wrapper around the
stdlib Python runtime in `gcal.py`. It replaces the broken
`calendarmcp.googleapis.com` MCP connector (see project memory
`calendar-mcp-broken` - that connector's `tools/call` permanently fails with
"The caller does not have permission", independent of re-auth).

## 2. Commands

All subcommands are invoked via the `scripts-gcal` interface. Run `scripts-gcal --help` for
the full reference. Calendar IDs default to `primary`; use the `calendars` subcommand
for other IDs (e.g. shared calendars).

- `calendars` - list calendars (id, access role, name).
- `agenda [--calendar ID | --all-calendars] [--from ISO] [--to ISO] [--days N]` -
  list events. Defaults to `primary`, today (local time). `--days N` extends
  the window from `--from` (default: today). `--all-calendars` queries every
  calendar in parallel internally and returns one merged, time-sorted list -
  use this for "what's on my schedule" / "busiest day" questions instead of
  calling `agenda` per calendar.
- `search QUERY [--calendar ID | --all-calendars] [--from ISO] [--to ISO]` -
  text search. Defaults to `primary`, -7d..+30d window. `--all-calendars`
  searches every calendar in parallel, merged and time-sorted.
- `get --event-id ID [--calendar ID]` - fetch a single event (full
  start/end/summary/location/description/status). Used for QC (section 3).
- `create --summary TEXT --start ISO --end ISO [--calendar ID]
  [--description TEXT] [--location TEXT] [--timezone TZ] [--all-day]` -
  create an event. With `--all-day`, `--start`/`--end` are `YYYY-MM-DD` (end
  exclusive). `--timezone` defaults to the system's local IANA timezone.
- `update --event-id ID [--calendar ID] [--summary TEXT]
  [--description TEXT] [--location TEXT] [--start ISO] [--end ISO]
  [--timezone TZ]` - patch only the given fields.
- `delete --event-id ID [--calendar ID]` - delete an event.
- `move --event-id ID --to CALENDAR_ID [--from CALENDAR_ID]` - move an event
  between calendars (`--from` defaults to `primary`).
- `token` - print a fresh bearer token, for the raw-API fallback (section 4).

**Time format - the #1 source of errors**: any ISO timestamp passed to this
CLI (`create`/`update`'s `--start`/`--end`, and `agenda`/`search`'s
`--from`/`--to`) needs a full datetime *with a UTC offset*, e.g.
`2026-06-15T10:00:00-04:00`. A bare date (`2026-06-15`) or an offset-less
datetime returns `HTTP 400 Bad Request` with no more specific message.

Examples (use the `scripts-gcal` interface for each):
- "what's on my calendar today" -> `agenda`
- "what's on my NYU calendar this week" -> `calendars` (find the id),
  then `agenda --calendar <id> --days 7`
- "what's my busiest day next week" / "what's on my schedule this week"
  (spans *all* calendars) -> one call: `agenda --all-calendars --from <ISO-with-offset> --days 7`

## 2a. Which calendar to use for `create`

Before creating an event, use `scripts-gcal` with the `calendars` subcommand to see available calendars,
then match the event content against calendar names:

- Pick the calendar whose name best fits the event type (e.g. "Medical" for
  doctor visits, "Work" for meetings, "Classes" for coursework).
- If the match is clear, use it without asking.
- If 2+ calendars could plausibly fit, or none fit obviously, ask the user
  before creating.
- Never silently default to `primary` for an event that clearly belongs
  elsewhere.

## 3. QC after writes

Every `create`/`update`/`delete` ends with a read-back verification - this is
what catches a write that "succeeded" (HTTP 200) but landed on the wrong
day/time, e.g. a timezone or relative-date resolution mistake (see project
memory `uutils-date-dst-bug`).

- **create**: take the `id` from the response, then
  `get --event-id <id> --calendar <cal>`. Compare returned
  `start`/`end`/`summary`/`location`/`description` against intent.
  - Mismatch -> `delete --event-id <id> --calendar <cal>` (revert), then tell
    the user what was intended vs. created, and a likely cause (e.g. "off by
    exactly 1 hour - probably a DST/timezone resolution issue for this date").
- **update**: BEFORE patching, `get --event-id <id>` and note current values
  of every field you're about to change (revert target). Apply the update,
  then `get` again and compare.
  - Mismatch -> `update` again with the captured old values (revert), then
    flag + diagnose as above.
- **delete**: afterwards, `get --event-id <id> --calendar <cal>` still returns
  HTTP 200 (Google soft-deletes, no 404) but with `Status: cancelled` - that's
  the confirmation. If `Status` is still `confirmed`, the delete didn't take;
  flag this (no revert path for delete).

Skip QC only for trivial cases with no date/time component, e.g. an `update`
that changes only `--summary`/`--description`/`--location`.

## 4. Raw API fallback

For anything not covered above (recurring events / RRULEs, attendees,
free-busy queries, etc.) - last resort, since it requires a shell pipeline
(`TOKEN=$(...) && curl ...`) that doesn't match the allow-listed Bash pattern
and triggers a permission prompt every time. If a capability is needed
repeatedly, add a small subcommand to `gcal.py` and keep exposing it through
`scripts-gcal` instead (as was done for `get`, `create-calendar`, `move`).
For genuine one-offs, use `scripts-gcal`
with the `token` subcommand to obtain a bearer token, then call the API directly:

```bash
# Obtain a bearer token via scripts-gcal token, then:
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://www.googleapis.com/calendar/v3/calendars/primary/events" \
  -d '...' # see https://developers.google.com/calendar/api/v3/reference
```

## 5. Known limitations

- `agenda`/`search` fetch at most 50 events (no pagination).
- Only single (non-recurring) events; no attendee management via the CLI
  subcommands (use the raw-API fallback).
- If the OAuth consent screen is "Testing", the refresh token expires after 7
  days - `scripts-gcal` fails with `invalid_grant`; see section 6 to fix.

## 6. Private config files

Two files live at `~/.config/g-calendar/` (both `chmod 600`, outside git):

| File | Contents | Written by |
|------|----------|------------|
| `client.json` | Google Cloud Console OAuth client JSON (`client_id` + `client_secret`) | You (one-time copy from download) |
| `credentials.json` | Working credentials (`client_id` + `client_secret` + `refresh_token`) | `setup_oauth.py` — **overwrites on every run** |

`client.json` is the permanent source of truth. `credentials.json` is
regenerated whenever the refresh token expires. Never pass `credentials.json`
as `--from-json` input to `setup_oauth.py` — it uses a flat format that the
script cannot read as input.

`~/.config/<skill>/` is the convention for private per-skill data: it is
outside any git repo and never committed.

## 7. First-time / repeat setup

If `scripts-gcal` reports `invalid_grant` or `invalid_client`, (re)do this:

### First time only: create the Google OAuth client

In an existing Google Cloud project (console.cloud.google.com), signed in
as the Google account whose calendar will be used:
- **Enable the API**: APIs & Services -> Library -> "Google Calendar API"
  -> Enable (skip if already enabled).
- **OAuth consent screen**: User type External. Add scope
  `https://www.googleapis.com/auth/calendar`. Check **Publishing status** (in the current Google Cloud UI this is usually under **OAuth -> Audience**):
  - "Testing" -> refresh tokens expire after 7 days. Try "Publish App" to
    move to "In production": for an unverified app with this sensitive
    scope, Google may still allow publishing for a small number of users
    without full verification. During consent you'll see "Google hasn't
    verified this app" - click Advanced -> "Go to <app name> (unsafe)".
    Expected for a personal single-user app.
  - If publishing is blocked or undesired, staying in "Testing" works
    too - add the account as a test user, and just re-run setup
    whenever the refresh token expires.
- **Credentials**: Create Credentials -> OAuth client ID.
  - Application type **Desktop app** (Google auto-allows any
    `http://localhost:<port>` redirect for these), or
  - Application type **Web application** with an explicit Authorized
    redirect URI of `http://localhost:8765` (must match `setup_oauth.py`'s
    `--port`, default 8765, exactly — no trailing slash).
- Download the client JSON and save it as `~/.config/g-calendar/client.json`
  (mode 600). Keep this file — it is needed for every future re-auth.

### Every time: run setup to (re)generate credentials.json

Use the `setup-oauth` interface (no arguments needed) — it reads
`~/.config/g-calendar/client.json` automatically and writes
`~/.config/g-calendar/credentials.json`.

To use a different client JSON explicitly, pass `--from-json /path/to/other.json`
to the `setup-oauth` interface.

### Verify

Use `scripts-gcal` with `calendars`, then with `agenda`, to confirm the
credentials are working.

If Google omits `refresh_token` from the response (because access was already
granted without revoking), revoke prior access at
https://myaccount.google.com/permissions and retry.
