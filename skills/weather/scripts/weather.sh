#!/usr/bin/env bash
# Weather CLI for the weather skill.
# Resolves a location (IP geolocation by default, or geocoded by name via
# --location) and a date range (today by default, or --date/--end-date
# YYYY-MM-DD), fetches that range's hourly forecast from Open-Meteo (no
# key), and prints combined JSON to stdout.
set -euo pipefail

die() {
  echo "Error: $*" >&2
  exit 1
}

for bin in curl jq; do
  command -v "$bin" >/dev/null 2>&1 || die "'$bin' is required but not found in PATH"
done

urlencode() {
  jq -rn --arg v "$1" '$v|@uri'
}

# 0. Parse arguments
date_arg=""
end_date_arg=""
location_arg=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date)
      [ $# -ge 2 ] || die "--date requires a value"
      date_arg="$2"
      shift 2
      ;;
    --end-date)
      [ $# -ge 2 ] || die "--end-date requires a value"
      end_date_arg="$2"
      shift 2
      ;;
    --location)
      [ $# -ge 2 ] || die "--location requires a value"
      location_arg="$2"
      shift 2
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[ -n "$date_arg" ] || date_arg=$(date +%F)
[ -n "$end_date_arg" ] || end_date_arg="$date_arg"

if [[ "$end_date_arg" < "$date_arg" ]]; then
  die "--end-date ($end_date_arg) is before --date ($date_arg)"
fi

# 1. Location resolution
if [ -n "$location_arg" ]; then
  geo=$(curl -s "https://geocoding-api.open-meteo.com/v1/search?name=$(urlencode "$location_arg")&count=1")
  result_count=$(echo "$geo" | jq -r '(.results // []) | length')
  [ "$result_count" != "0" ] || die "Location not found: $location_arg"

  lat=$(echo "$geo" | jq -r '.results[0].latitude')
  lon=$(echo "$geo" | jq -r '.results[0].longitude')
  city=$(echo "$geo" | jq -r '.results[0].name')
  tz=$(echo "$geo" | jq -r '.results[0].timezone')
else
  geo=$(curl -s 'http://ip-api.com/json/')
  geo_status=$(echo "$geo" | jq -r '.status // "fail"')
  [ "$geo_status" = "success" ] || die "Geolocation failed: $geo"

  lat=$(echo "$geo" | jq -r '.lat')
  lon=$(echo "$geo" | jq -r '.lon')
  city=$(echo "$geo" | jq -r '.city')
  tz=$(echo "$geo" | jq -r '.timezone')
fi

# 2. Open-Meteo hourly forecast for the resolved date range, in the
# location's local timezone
forecast=$(curl -s "https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&hourly=temperature_2m,precipitation_probability,precipitation,wind_speed_10m,weather_code&timezone=${tz}&start_date=${date_arg}&end_date=${end_date_arg}")

forecast_error=$(echo "$forecast" | jq -r '.error // false')
if [ "$forecast_error" != "false" ]; then
  die "Forecast API error: $(echo "$forecast" | jq -r '.reason // "unknown"')"
fi

# 3. Combine into one JSON object
echo "$forecast" | jq \
  --arg city "$city" \
  --arg lat "$lat" \
  --arg lon "$lon" \
  --arg start_date "$date_arg" \
  --arg end_date "$end_date_arg" \
  '{start_date: $start_date, end_date: $end_date, location_query: $city, latitude: $lat, longitude: $lon, timezone: .timezone, hourly: .hourly}'
