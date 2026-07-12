---
name: get-weather
description: |
  Use when the user asks about weather for the current location or a named
  location, including a specific day or date range.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: productivity-general-assistant

Skill Version: 1

Uses Interfaces: none

Public Interfaces:
- `get-weather.machine.scripts-weather`
- `get-weather.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `scripts-weather` — Fetch weather data for a location and date range, returning hourly forecast JSON.
  - `dispatcher --caller-skill get-weather get-weather.machine.scripts-weather [--date <YYYY-MM-DD>] [--end-date <YYYY-MM-DD>] [--location <loc>]`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
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

The script returns one JSON object with:
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
