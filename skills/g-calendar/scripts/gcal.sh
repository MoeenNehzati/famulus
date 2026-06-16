#!/usr/bin/env bash
# Minimal Google Calendar CLI for the g-calendar skill.
# Reads OAuth client/refresh token from ~/.config/g-calendar/credentials.json,
# mints a fresh access token per invocation, and calls the Calendar API v3.
set -euo pipefail

CREDS_FILE="$HOME/.config/g-calendar/credentials.json"
API_BASE="https://www.googleapis.com/calendar/v3"

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

get_timezone() {
  if command -v timedatectl >/dev/null 2>&1; then
    local tz
    tz=$(timedatectl show -p Timezone --value 2>/dev/null || true)
    [ -n "$tz" ] && { echo "$tz"; return; }
  fi
  if [ -f /etc/timezone ]; then
    cat /etc/timezone
    return
  fi
  echo "UTC"
}

get_access_token() {
  [ -f "$CREDS_FILE" ] || die "No credentials at $CREDS_FILE. Run setup_oauth.py first."

  local client_id client_secret refresh_token resp token
  client_id=$(jq -r .client_id "$CREDS_FILE")
  client_secret=$(jq -r .client_secret "$CREDS_FILE")
  refresh_token=$(jq -r .refresh_token "$CREDS_FILE")

  resp=$(curl -s -X POST https://oauth2.googleapis.com/token \
    -d client_id="$client_id" \
    -d client_secret="$client_secret" \
    -d refresh_token="$refresh_token" \
    -d grant_type=refresh_token)

  token=$(echo "$resp" | jq -r '.access_token // empty')
  if [ -z "$token" ]; then
    die "Failed to get access token. Response: $resp"$'\n'"If this says invalid_grant, re-run setup_oauth.py."
  fi
  echo "$token"
}

# api_call METHOD PATH [JSON_BODY]
# Prints the response body (may be empty for 204s). Exits non-zero on HTTP error.
api_call() {
  local method="$1" path="$2" body="${3:-}"
  local token resp status

  if [ -n "${GCAL_TOKEN:-}" ]; then
    token="$GCAL_TOKEN"
  else
    token=$(get_access_token)
  fi

  local args=(-s -X "$method" -H "Authorization: Bearer $token")
  if [ -n "$body" ]; then
    args+=(-H "Content-Type: application/json" -d "$body")
  fi

  resp=$(curl "${args[@]}" -w '\n%{http_code}' "$API_BASE$path")
  status="${resp##*$'\n'}"
  resp="${resp%$'\n'*}"

  if [ "$status" -ge 400 ]; then
    die "API error (HTTP $status): $resp"
  fi

  echo "$resp"
}

list_events() {
  local cal="$1" tmin="$2" tmax="$3" query="${4:-}"
  local path="/calendars/$(urlencode "$cal")/events?timeMin=$(urlencode "$tmin")&timeMax=$(urlencode "$tmax")&singleEvents=true&orderBy=startTime&maxResults=50"
  if [ -n "$query" ]; then
    path="$path&q=$(urlencode "$query")"
  fi
  api_call GET "$path"
}

print_events() {
  local resp="$1" cal="$2"
  local n
  n=$(echo "$resp" | jq '.items | length')
  if [ "$n" -eq 0 ]; then
    echo "(no events)"
    return
  fi
  echo "$resp" | jq -r --arg cal "$cal" \
    '.items[] | "\(.start.dateTime // .start.date) -> \(.end.dateTime // .end.date)  \(.summary // "(no title)")  [calendar: \($cal), id: \(.id)]"'
}

# list_events_all FROM TO [QUERY]
# Fetches events from every calendar in the user's calendarList, in
# parallel (one background gcal API call per calendar, sharing a single
# access token), and returns the merged items as a JSON array sorted by
# start time. Each item gains "_cal" (calendar id) and "_calName" fields.
list_events_all() {
  local from="$1" to="$2" query="${3:-}"
  local token tmpdir cal_list i=0

  token=$(get_access_token)
  export GCAL_TOKEN="$token"

  cal_list=$(api_call GET "/users/me/calendarList" | jq -r '.items[] | "\(.id)\t\(.summary)"')

  tmpdir=$(mktemp -d)
  while IFS=$'\t' read -r id name; do
    [ -z "$id" ] && continue
    (
      list_events "$id" "$from" "$to" "$query" \
        | jq --arg cal "$id" --arg name "$name" \
            '[.items[]? | . + {_cal: $cal, _calName: $name}]' \
        > "$tmpdir/$i.json" \
        || echo '[]' > "$tmpdir/$i.json"
    ) &
    i=$((i + 1))
  done <<< "$cal_list"
  wait

  jq -s 'add // [] | sort_by(.start.dateTime // .start.date)' "$tmpdir"/*.json
  rm -rf "$tmpdir"
  unset GCAL_TOKEN
}

print_events_multi() {
  local items="$1" n
  n=$(echo "$items" | jq 'length')
  if [ "$n" -eq 0 ]; then
    echo "(no events)"
    return
  fi
  echo "$items" | jq -r \
    '.[] | "\(.start.dateTime // .start.date) -> \(.end.dateTime // .end.date)  \(.summary // "(no title)")  [calendar: \(._calName), id: \(.id)]"'
}

cmd_token() {
  get_access_token
}

cmd_calendars() {
  api_call GET "/users/me/calendarList" | jq -r '.items[] | "\(.id)  (\(.accessRole))  \(.summary)"'
}

cmd_create_calendar() {
  local summary="" description="" color_id="" tz=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --summary) summary="$2"; shift 2 ;;
      --description) description="$2"; shift 2 ;;
      --color-id) color_id="$2"; shift 2 ;;
      --timezone) tz="$2"; shift 2 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [ -n "$summary" ] || die "--summary is required"
  [ -n "$tz" ] || tz=$(get_timezone)

  local body cal_id
  body=$(jq -n --arg summary "$summary" --arg description "$description" --arg tz "$tz" \
    '{summary: $summary, timeZone: $tz}
     + (if $description != "" then {description: $description} else {} end)')

  cal_id=$(api_call POST "/calendars" "$body" | jq -r '.id')

  if [ -n "$color_id" ]; then
    api_call PATCH "/users/me/calendarList/$(urlencode "$cal_id")" \
      "$(jq -n --arg c "$color_id" '{colorId: $c}')" >/dev/null
  fi

  echo "Created calendar: $summary  [id: $cal_id]"
}

cmd_agenda() {
  local cal="primary" from="" to="" days="1" all="false" cal_set="false"
  while [ $# -gt 0 ]; do
    case "$1" in
      --calendar) cal="$2"; cal_set="true"; shift 2 ;;
      --from) from="$2"; shift 2 ;;
      --to) to="$2"; shift 2 ;;
      --days) days="$2"; shift 2 ;;
      --all-calendars) all="true"; shift ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [ "$all" = "true" ] && [ "$cal_set" = "true" ] && die "--all-calendars and --calendar are mutually exclusive"

  [ -n "$from" ] || from=$(date -d "00:00" +%Y-%m-%dT%H:%M:%S%:z)
  [ -n "$to" ] || to=$(date -d "$from +$days days" +%Y-%m-%dT%H:%M:%S%:z)

  if [ "$all" = "true" ]; then
    print_events_multi "$(list_events_all "$from" "$to")"
  else
    print_events "$(list_events "$cal" "$from" "$to")" "$cal"
  fi
}

cmd_search() {
  [ $# -ge 1 ] || die "Usage: gcal.sh search QUERY [--calendar ID] [--all-calendars] [--from ISO] [--to ISO] [--days N]"
  local query="$1"; shift
  local cal="primary" from="" to="" days="" all="false" cal_set="false"
  while [ $# -gt 0 ]; do
    case "$1" in
      --calendar) cal="$2"; cal_set="true"; shift 2 ;;
      --from) from="$2"; shift 2 ;;
      --to) to="$2"; shift 2 ;;
      --days) days="$2"; shift 2 ;;
      --all-calendars) all="true"; shift ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [ "$all" = "true" ] && [ "$cal_set" = "true" ] && die "--all-calendars and --calendar are mutually exclusive"

  if [ -n "$days" ]; then
    [ -n "$from" ] || from=$(date -d "00:00" +%Y-%m-%dT%H:%M:%S%:z)
    [ -n "$to" ] || to=$(date -d "$from +$days days" +%Y-%m-%dT%H:%M:%S%:z)
  else
    [ -n "$from" ] || from=$(date -d "-7 days 00:00" +%Y-%m-%dT%H:%M:%S%:z)
    [ -n "$to" ] || to=$(date -d "+30 days 00:00" +%Y-%m-%dT%H:%M:%S%:z)
  fi

  if [ "$all" = "true" ]; then
    print_events_multi "$(list_events_all "$from" "$to" "$query")"
  else
    print_events "$(list_events "$cal" "$from" "$to" "$query")" "$cal"
  fi
}

cmd_get() {
  local cal="primary" event_id=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --calendar) cal="$2"; shift 2 ;;
      --event-id) event_id="$2"; shift 2 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [ -n "$event_id" ] || die "--event-id is required"

  api_call GET "/calendars/$(urlencode "$cal")/events/$(urlencode "$event_id")" \
    | jq -r --arg cal "$cal" '
        "\(.start.dateTime // .start.date) -> \(.end.dateTime // .end.date)  \(.summary // "(no title)")  [calendar: \($cal), id: \(.id)]"
        + (if .location then "\nLocation: \(.location)" else "" end)
        + (if .description then "\nDescription: \(.description)" else "" end)
        + "\nStatus: \(.status)\n\(.htmlLink)"'
}

cmd_create() {
  local cal="primary" summary="" start="" end="" description="" location="" all_day="false" tz=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --calendar) cal="$2"; shift 2 ;;
      --summary) summary="$2"; shift 2 ;;
      --start) start="$2"; shift 2 ;;
      --end) end="$2"; shift 2 ;;
      --description) description="$2"; shift 2 ;;
      --location) location="$2"; shift 2 ;;
      --all-day) all_day="true"; shift ;;
      --timezone) tz="$2"; shift 2 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [ -n "$summary" ] || die "--summary is required"
  [ -n "$start" ] || die "--start is required"
  [ -n "$end" ] || die "--end is required"
  [ -n "$tz" ] || tz=$(get_timezone)

  local body
  if [ "$all_day" = "true" ]; then
    body=$(jq -n --arg summary "$summary" --arg description "$description" --arg location "$location" \
      --arg start "$start" --arg end "$end" \
      '{summary: $summary, start: {date: $start}, end: {date: $end}}
       + (if $description != "" then {description: $description} else {} end)
       + (if $location != "" then {location: $location} else {} end)')
  else
    body=$(jq -n --arg summary "$summary" --arg description "$description" --arg location "$location" \
      --arg start "$start" --arg end "$end" --arg tz "$tz" \
      '{summary: $summary, start: {dateTime: $start, timeZone: $tz}, end: {dateTime: $end, timeZone: $tz}}
       + (if $description != "" then {description: $description} else {} end)
       + (if $location != "" then {location: $location} else {} end)')
  fi

  api_call POST "/calendars/$(urlencode "$cal")/events" "$body" \
    | jq -r '"Created: \(.summary)  [id: \(.id)]\n\(.htmlLink)"'
}

cmd_update() {
  local cal="primary" event_id="" tz=""
  local summary="" description="" location="" start="" end=""
  local set_summary=0 set_description=0 set_location=0 set_start=0 set_end=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --calendar) cal="$2"; shift 2 ;;
      --event-id) event_id="$2"; shift 2 ;;
      --summary) summary="$2"; set_summary=1; shift 2 ;;
      --description) description="$2"; set_description=1; shift 2 ;;
      --location) location="$2"; set_location=1; shift 2 ;;
      --start) start="$2"; set_start=1; shift 2 ;;
      --end) end="$2"; set_end=1; shift 2 ;;
      --timezone) tz="$2"; shift 2 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [ -n "$event_id" ] || die "--event-id is required"
  [ -n "$tz" ] || tz=$(get_timezone)

  local body="{}"
  [ "$set_summary" -eq 1 ] && body=$(echo "$body" | jq --arg v "$summary" '. + {summary: $v}')
  [ "$set_description" -eq 1 ] && body=$(echo "$body" | jq --arg v "$description" '. + {description: $v}')
  [ "$set_location" -eq 1 ] && body=$(echo "$body" | jq --arg v "$location" '. + {location: $v}')
  [ "$set_start" -eq 1 ] && body=$(echo "$body" | jq --arg v "$start" --arg tz "$tz" '. + {start: {dateTime: $v, timeZone: $tz}}')
  [ "$set_end" -eq 1 ] && body=$(echo "$body" | jq --arg v "$end" --arg tz "$tz" '. + {end: {dateTime: $v, timeZone: $tz}}')

  [ "$body" = "{}" ] && die "Nothing to update - pass at least one of --summary/--description/--location/--start/--end"

  api_call PATCH "/calendars/$(urlencode "$cal")/events/$(urlencode "$event_id")" "$body" \
    | jq -r '"Updated: \(.summary)  [id: \(.id)]\n\(.htmlLink)"'
}

cmd_delete() {
  local cal="primary" event_id=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --calendar) cal="$2"; shift 2 ;;
      --event-id) event_id="$2"; shift 2 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [ -n "$event_id" ] || die "--event-id is required"

  api_call DELETE "/calendars/$(urlencode "$cal")/events/$(urlencode "$event_id")" >/dev/null
  echo "Deleted event $event_id from calendar $cal"
}

cmd_move() {
  local event_id="" from="primary" to=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --event-id) event_id="$2"; shift 2 ;;
      --from) from="$2"; shift 2 ;;
      --to) to="$2"; shift 2 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [ -n "$event_id" ] || die "--event-id is required"
  [ -n "$to" ] || die "--to is required"

  api_call POST "/calendars/$(urlencode "$from")/events/$(urlencode "$event_id")/move?destination=$(urlencode "$to")" \
    | jq -r --arg to "$to" '"Moved: \(.summary)  [id: \(.id)]  -> calendar \($to)"'
}

usage() {
  cat <<'EOF'
Usage: gcal.sh <command> [options]

Commands:
  token
      Print a fresh OAuth access token (for ad-hoc curl calls).

  calendars
      List your calendars (id, access role, name).

  create-calendar --summary TEXT [--description TEXT] [--color-id N]
                   [--timezone TZ]
      Create a new secondary calendar. --color-id is 1-24, per the
      Calendar API's calendar color palette (GET /colors -> .calendar).
      --timezone defaults to the system's local IANA timezone.

  agenda [--calendar ID | --all-calendars] [--from ISO] [--to ISO] [--days N]
      List events. Defaults: calendar=primary, range=today (local time),
      or use --days N to extend the range from --from (default today).
      --all-calendars fetches every calendar in your calendarList in
      parallel and returns the merged, time-sorted result - use this
      instead of looping over calendars one at a time.

  search QUERY [--calendar ID | --all-calendars] [--from ISO] [--to ISO] [--days N]
      Text-search events. Defaults: calendar=primary, range = -7d..+30d.
      With --days N (and no --from/--to), range = today..+N days.
      --all-calendars searches every calendar in parallel, merged and
      time-sorted.

  get --event-id ID [--calendar ID]
      Fetch a single event by id (full start/end/summary/location/
      description/status). Use to verify a create/update result.

  create --summary TEXT --start ISO --end ISO [--calendar ID]
         [--description TEXT] [--location TEXT] [--timezone TZ] [--all-day]
      Create an event. ISO times need a timezone offset, e.g.
      2026-06-15T10:00:00-04:00. With --all-day, --start/--end are
      YYYY-MM-DD dates (end is exclusive, per Google Calendar).

  update --event-id ID [--calendar ID] [--summary TEXT] [--description TEXT]
         [--location TEXT] [--start ISO] [--end ISO] [--timezone TZ]
      Patch an existing event. Only given fields are changed.

  delete --event-id ID [--calendar ID]
      Delete an event.

  move --event-id ID --to CALENDAR_ID [--from CALENDAR_ID]
      Move an event to another calendar. --from defaults to "primary".

Calendar IDs default to "primary". Use `gcal.sh calendars` to find IDs
for other calendars (e.g. shared calendars).
EOF
}

main() {
  local cmd="${1:-}"
  [ -n "$cmd" ] || { usage; exit 1; }
  shift

  case "$cmd" in
    token) cmd_token "$@" ;;
    calendars) cmd_calendars "$@" ;;
    create-calendar) cmd_create_calendar "$@" ;;
    agenda) cmd_agenda "$@" ;;
    search) cmd_search "$@" ;;
    get) cmd_get "$@" ;;
    create) cmd_create "$@" ;;
    update) cmd_update "$@" ;;
    delete) cmd_delete "$@" ;;
    move) cmd_move "$@" ;;
    -h|--help|help) usage ;;
    *) die "Unknown command: $cmd (see --help)" ;;
  esac
}

main "$@"
