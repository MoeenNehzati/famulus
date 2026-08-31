---
name: get-weather
description: >-
  Use when the user asks about weather for the current location or a named location, including a specific day or date range.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: planning, external-integrations; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `get-weather.source.gateway -> get-weather._rtx.interface.scripts-weather@1`

Public Interfaces:
- `get-weather.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `get-weather._rtx.interface.scripts-weather` — Fetch weather data for a location and date range, returning hourly forecast JSON.
  - Caller: `get-weather`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--date": "YYYY-MM-DD", "--end-date": "YYYY-MM-DD", "--location": "loc"}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `get-weather.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: get-weather

## Workflow

Invoke the `scripts-weather` interface with the requested `--date`,
`--end-date`, and `--location` arguments. The interface resolves the location,
fetches hourly Open-Meteo data, and prints one JSON object to stdout.

Translate natural-language requests such as "tomorrow", "next week", or
"this weekend" into concrete dates before invoking the interface. When the date
arithmetic is simple, do it directly instead of invoking an extra tool just to
add days.

If the interface exits nonzero, report the error plainly and stop. Do not retry
with guessed dates or locations.

## Route by user intent

- Current location weather → invoke with no `--location`.
- Named place weather → pass `--location`.
- Single-day weather → pass `--date`.
- Multi-day weather → pass both `--date` and `--end-date`.

If the user specified a date or location, echo back the resolved
`start_date`/`end_date` and `location_query` so they can catch geocoding or
calendar mismatches.

## Output contract

The interface returns one JSON object with:
- `start_date`, `end_date`, `location_query`, `timezone`
- `hourly.time`
- `hourly.temperature_2m`
- `hourly.precipitation_probability`
- `hourly.precipitation`
- `hourly.wind_speed_10m`
- `hourly.weather_code`

Interpret that JSON into a concise user-facing weather summary.

## Summary rules

- For 1-2 days, give each day its own summary with min/max temperature,
  overall conditions, notable rain/wind windows, and practical clothing or
  activity guidance.
- For 3+ days, give one short line per day with overall conditions and min/max
  temperature; skip the detailed time-of-day breakdown.
- Use the hourly arrays to identify the main conditions and the most relevant
  transitions during the day.
- Keep the answer practical and planning-oriented.
