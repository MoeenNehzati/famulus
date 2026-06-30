---
name: get-weather
description: |
  Fetch weather for the user's current location or a named location, including
  today, a specific date, or a supported date range. Present a
  day-planning-oriented summary: temperature range in Celsius, how conditions
  change over the requested period, rain/wind windows, and what to wear/which
  activities fit which parts of the day. Use when the user asks about weather,
  whether it'll rain, what to wear, or how to plan around weather.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: automation

Dependencies: none

Interface Version: 1

Exported Script Interfaces:
- `scripts-weather`
<!-- END BLUEPRINT CONTRACT -->
When this skill is used, begin with:

Skill: get-weather

## 0. What this does

`scripts/weather.sh` geolocates the current IP (via ip-api.com, no key) or
geocodes a named location, then fetches hourly weather from Open-Meteo (no key)
for the requested date range, printing combined JSON to stdout. The agent then
interprets that JSON into a day-planning summary. Read-only — makes no changes
to anything.

## 1. Run the script

```bash
scripts/weather.sh [--date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--location "place name"]
```

All flags are optional:

- `--date YYYY-MM-DD`: start of the range, defaults to today (system local
  date).
- `--end-date YYYY-MM-DD`: end of the range (inclusive), defaults to
  `--date`'s value (i.e. a single day). Must not be before `--date`.
- `--location "place name"`: defaults to the current IP-geolocated location.
  When given, the place name is geocoded via Open-Meteo's geocoding API
  (takes the top match).

Both `--date` and `--end-date` must be within Open-Meteo's forecast window
(~92 days in the past to ~16 days in the future) — outside that range, the
script errors out.

**Translating natural-language requests:**

- "weather tomorrow" → compute tomorrow's date from today's date, pass
  `--date <that-date>`.
- "weather in Boston" → `--location "Boston"`.
- "weather in Paris next Tuesday" → compute next Tuesday's date and pass
  both `--date <that-date> --location "Paris"`.
- "weather next week" → compute the date range for next week (e.g.
  Monday-Sunday of the following week) from today's date, pass
  `--date <start> --end-date <end>`.
- "weather this weekend" → compute the upcoming Saturday-Sunday range, pass
  `--date <saturday> --end-date <sunday>`.
- Plain "weather" / "weather today" → no flags (defaults).

Compute these relative dates (tomorrow, next week, this weekend, etc.) by
hand from today's date — don't invoke a tool (e.g. `python3`, `date -d`) just
to add or subtract days. Only reach for a script if the arithmetic is
genuinely tricky (e.g. month/year rollovers or DST-sensitive calculations).

This prints one JSON object:

```json
{
  "start_date": "2026-06-14",
  "end_date": "2026-06-14",
  "location_query": "New York",
  "latitude": "40.7",
  "longitude": "-73.9",
  "timezone": "America/New_York",
  "hourly": {
    "time": ["2026-06-14T00:00", "2026-06-14T01:00", ...],
    "temperature_2m": [18.2, 17.9, ...],
    "precipitation_probability": [10, 10, ...],
    "precipitation": [0.0, 0.0, ...],
    "wind_speed_10m": [8.1, 7.5, ...],
    "weather_code": [1, 1, ...]
  }
}
```

`hourly.*` arrays span the full range: 24 entries per day from `start_date`
through `end_date` inclusive (e.g. 72 entries for a 3-day range), local time
per `timezone`, all aligned by index with `hourly.time`.

When `--date`, `--end-date`, or `--location` was given, mention the resolved
`start_date`/`end_date` and `location_query` back to the user (e.g. "weather
for Berlin, 2026-06-15 to 2026-06-19") so they can confirm it matches what
they meant — geocoding can resolve ambiguous names to an unexpected place.

- If the script exits non-zero, it prints `Error: ...` to stderr:
  - Geolocation/forecast API failure (as before).
  - `Location not found: <place>` — the geocoding API had no match. Report
    this plainly; don't guess a different location.
  - `Forecast API error: ...` for a date outside the supported window.
    Report plainly; don't retry with a different date.
  In all cases: report the error to the user and stop. Don't retry
  automatically or fall back to a guessed location/date.

## 2. Weather code reference (WMO codes)

Use this to describe overall conditions from `weather_code` values:

| Code(s)   | Meaning                  |
|-----------|--------------------------|
| 0         | Clear sky                |
| 1, 2, 3   | Mainly clear / partly cloudy / overcast |
| 45, 48    | Fog                      |
| 51, 53, 55| Drizzle (light/moderate/dense) |
| 56, 57    | Freezing drizzle         |
| 61, 63, 65| Rain (slight/moderate/heavy) |
| 66, 67    | Freezing rain            |
| 71, 73, 75| Snow (slight/moderate/heavy) |
| 77        | Snow grains              |
| 80, 81, 82| Rain showers (slight/moderate/violent) |
| 85, 86    | Snow showers             |
| 95, 96, 99| Thunderstorm (possibly with hail) |

For the day's overall description, summarize the dominant codes across all
24 hours (e.g. mostly 1-2 with a few 61s → "partly sunny with some rain").

## 3. Compute the summary

- **Min/max temperature**: min and max of `hourly.temperature_2m` across all
  24 hours, rounded to whole °C.
- **Overall conditions**: a short phrase from the weather-code reference
  above, reflecting the most common/significant codes of the day.
- **Timeline of changes**: scan the hourly arrays and call out notable
  transitions by approximate local time, e.g.:
  - precipitation_probability crossing ~40%+ → "40% chance of rain around
    10am"
  - precipitation > 0 for a sustained run of hours → "rain likely from
    1pm-4pm"
  - wind_speed_10m notably higher than the day's average → "windy in the
    evening"

## 4. Time-of-day buckets and suggestions

Split the 24 hours into the same buckets used by the daily planning workflow:

- **Morning**: hours with local time before 12:00
- **Afternoon**: hours from 12:00 up to (not including) 17:00
- **Evening**: hours from 17:00 onward

For each bucket, using that bucket's temperature/precipitation/wind values,
give:

- **Clothing suggestion** (e.g. "light jacket", "bring an umbrella", "t-shirt
  weather", "windbreaker").
- **Activity fit** — whether outdoor activities/errands are favorable in that
  bucket, or whether indoor activities are a better fit (and why, referencing
  the rain/wind/temperature for that bucket).

## 5. Output format

```
Today: <overall conditions>, <min>-<max>°C.
- Morning: <temp/conditions summary> — <clothing>, <activity fit>.
- Afternoon: <temp/conditions summary> — <clothing>, <activity fit>.
- Evening: <temp/conditions summary> — <clothing>, <activity fit>.
```

Example:

```
Today: partly sunny, 18-20°C.
- Morning: cool (18°C), 40% chance of rain around 10am — bring a light
  jacket, good time for outdoor errands before 10.
- Afternoon: rain likely from ~1pm-4pm — better for indoor activities.
- Evening: clearing up, ~19°C, light wind — fine for a walk.
```

## 6. Presenting a date range

Compute the number of days in the range: `end_date - start_date + 1` (by
date, not hours — e.g. `start_date`/`end_date` both `2026-06-14` is 1 day;
`2026-06-15` to `2026-06-19` is 5 days).

- **1-2 days**: for each day in the range, slice that day's 24 hours out of
  the `hourly.*` arrays (by index, 24 per day in order) and apply the full
  Section 3-5 breakdown (overall conditions, min/max °C, time-of-day
  buckets, clothing/activity suggestions) to each day separately.
- **3+ days**: present a one-line-per-day overview instead — for each day,
  compute min/max °C (Section 3) and a short overall-conditions phrase
  (Section 2), and note any notable rain/wind for that day in a few words.
  Skip the time-of-day bucket/clothing breakdown in this mode. Example:

  ```
  Mon 6/15: partly sunny, 18-24°C
  Tue 6/16: rain likely, 15-19°C
  Wed 6/17: sunny, 19-25°C
  ```

## Out of scope

- No caching/persistence between invocations — always fetches fresh data.
- No write-back integration with the daily planning workflow yet (read-only standalone skill).
- No range-length limit beyond Open-Meteo's existing forecast window (a
  range extending past it errors via the existing forecast-error check).
