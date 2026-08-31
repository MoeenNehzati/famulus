---
name: online-calendar
description: >-
  Use when the user asks to view or change their Google Calendar. Do not use for daily planning.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: planning, personal-organization, external-integrations; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `online-calendar.source.gateway -> connect-google.interface.default@1`
- `online-calendar.source.gateway -> online-calendar._rtx.interface.scripts-gcal@1`
- `online-calendar.source.gateway -> setup-python-environment.interface.repair-selected-packages@1`

Setup Requires Setup Of:
- `connect-google.interface.setup@1`
Setup Order:
1. `connect-google.interface.setup`
2. `online-calendar.interface.setup`

Public Interfaces:
- `online-calendar.interface.default`
- `online-calendar.interface.setup`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `online-calendar._rtx.interface.ensure-oauth` — Check online-calendar OAuth status; print setup guidance or launch browser authorization as needed. Relocated from install-assistant-tools — invoke directly (caller-skill online-calendar) as part of connecting remotes.
  - Caller: `online-calendar`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--dry-run": true, "--home": "dir"}, "positionals": [], "stdin": null}
    Required options: ["--home"]; positional arity: 0..0; stdin: forbidden
- `online-calendar._rtx.interface.scripts-gcal` — Query or modify Google Calendar events via the Python calendar CLI (agenda, search, create, update, delete, etc.).
  - Caller: `online-calendar`
  - Version: 1
  - Alternative: `token-or-calendars`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["token"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
  - Alternative: `create-calendar`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--color-id": "ID", "--description": "TEXT", "--summary": "TEXT", "--timezone": "TZ"}, "positionals": ["create-calendar"], "stdin": null}
    Required options: ["--summary"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `agenda`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--all-calendars": true, "--calendar": "ID", "--days": "N", "--from": "ISO", "--to": "ISO"}, "positionals": ["agenda"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
  - Alternative: `search`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--all-calendars": true, "--calendar": "ID", "--days": "N", "--from": "ISO", "--to": "ISO"}, "positionals": ["search", "QUERY"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: forbidden
  - Alternative: `get`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--calendar": "ID", "--event-id": "ID"}, "positionals": ["get"], "stdin": null}
    Required options: ["--event-id"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `create`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--all-day": true, "--calendar": "ID", "--description": "TEXT", "--end": "ISO", "--location": "TEXT", "--start": "ISO", "--summary": "TEXT", "--timezone": "TZ"}, "positionals": ["create"], "stdin": null}
    Required options: ["--end", "--start", "--summary"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `update`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--calendar": "ID", "--description": "TEXT", "--end": "ISO", "--event-id": "ID", "--location": "TEXT", "--start": "ISO", "--summary": "TEXT", "--timezone": "TZ"}, "positionals": ["update"], "stdin": null}
    Required options: ["--event-id"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `delete`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--calendar": "ID", "--event-id": "ID"}, "positionals": ["delete"], "stdin": null}
    Required options: ["--event-id"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `move`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--event-id": "ID", "--from": "CALENDAR_ID", "--to": "CALENDAR_ID"}, "positionals": ["move"], "stdin": null}
    Required options: ["--event-id", "--to"]; positional arity: 1..1; stdin: forbidden
- `online-calendar._rtx.interface.setup-oauth` — Run the OAuth setup flow to generate or refresh Google Calendar credentials.
  - Caller: `online-calendar`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--from-json": "/path/to/client.json"}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `online-calendar._rtx.interface.use-google-credential` — Bind online-calendar to a shared connect-google credential_id after validating it carries Calendar scope, storing only the opaque identifier (never the client secret or refresh token) in online-calendar's own config.json. The pre-existing per-service OAuth path (ensure-oauth) remains the unchanged fallback for callers who have not adopted the shared credential.
  - Caller: `online-calendar`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--credential-id": "id", "--home": "dir"}, "positionals": [], "stdin": null}
    Required options: ["--credential-id", "--home"]; positional arity: 0..0; stdin: forbidden
- `online-calendar._rtx.interface.use-google-credential-file` — Validate and live-probe a Calendar credential descriptor before storing only its normalized absolute path in online-calendar config.
  - Caller: `online-calendar`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--allow-account-change": true, "--credential-file": "path", "--home": "dir"}, "positionals": [], "stdin": null}
    Required options: ["--credential-file", "--home"]; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `online-calendar.interface.default` — Primary LLM-facing skill instructions.
- `online-calendar.interface.setup` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# Google Calendar

Before any Calendar or credential action, use the host-loaded
`setup-python-environment.interface.repair-selected-packages` procedure for
feature `online-calendar` and its exact declaration `["keyring"]`. Require its
complete selected-Python preflight and byte-equal final fingerprint. On failure,
stop before OAuth, network, configuration, or other owner activity; never repair
another feature's declaration.

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
