#!/usr/bin/env bash
# email-send — send email via msmtp with optional PDF/file attachments and reply threading
# Usage: email-send.sh --from ACCT --to ADDR [--to ADDR...] --subject SUBJ
#                      [--in-reply-to <message-id>] [--references <refs>]
#                      [--attach /path[:DisplayName]] ... < body.txt
#
# ACCT: any nickname registered in accounts.py's registry
#       (~/.config/email-client/accounts.json) — run `accounts.py list` to see them.
#
# Connection settings (host/port/starttls) and the SMTP credential service name
# come from that registry, not from ~/.config/msmtp/config — this script passes
# them to msmtp as explicit flags (--host=... makes msmtp ignore its config
# file entirely), so accounts.py is the single source of truth for both
# reading (mail.py) and sending.
#
# Threading: pass --in-reply-to with the parent's Message-ID (angle brackets included,
# e.g. <abc123@mail.gmail.com>). References defaults to the same value if not given.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ACCOUNT=""
TO_ADDRS=()
SUBJECT=""
ATTACHMENTS=()
IN_REPLY_TO=""
REFERENCES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)        ACCOUNT="$2";        shift 2 ;;
        --to)          TO_ADDRS+=("$2");    shift 2 ;;
        --subject)     SUBJECT="$2";        shift 2 ;;
        --attach)      ATTACHMENTS+=("$2"); shift 2 ;;
        --in-reply-to) IN_REPLY_TO="$2";    shift 2 ;;
        --references)  REFERENCES="$2";     shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

[[ -z "$ACCOUNT" ]] && { echo "--from required (run accounts.py list to see registered nicknames)" >&2; exit 1; }
[[ ${#TO_ADDRS[@]} -eq 0 ]] && { echo "--to required" >&2; exit 1; }
[[ -z "$SUBJECT" ]] && { echo "--subject required" >&2; exit 1; }

# Default References to In-Reply-To when threading a single-level reply
[[ -n "$IN_REPLY_TO" && -z "$REFERENCES" ]] && REFERENCES="$IN_REPLY_TO"

# Resolve connection settings from the shared registry (accounts.py). This is
# a JSON blob; pull out the fields we need with small python3 one-liners
# rather than adding a YAML/JSON parsing dependency to bash.
ACCOUNT_JSON="$("$SCRIPT_DIR/accounts.py" resolve --nickname "$ACCOUNT" 2>&1)" || {
    echo "$ACCOUNT_JSON" >&2
    exit 1
}
jget() { python3 -c "import json,sys; print(json.loads(sys.argv[1])$1)" "$ACCOUNT_JSON"; }

FROM="$(jget "['email']")"
SMTP_HOST="$(jget "['smtp']['host']")"
SMTP_PORT="$(jget "['smtp']['port']")"
SMTP_STARTTLS="$(jget "['smtp']['starttls']")"
SMTP_SERVICE="$(jget "['smtp_service']")"
[[ "$SMTP_STARTTLS" == "True" ]] && STARTTLS_FLAG=on || STARTTLS_FLAG=off

BODY=$(cat)
BOUNDARY="=====$(date +%s%N)====="
TO_HEADER=$(IFS=', '; echo "${TO_ADDRS[*]}")

write_base_headers() {
    printf 'MIME-Version: 1.0\r\n'
    printf 'From: %s\r\n' "$FROM"
    printf 'To: %s\r\n' "$TO_HEADER"
    printf 'Subject: %s\r\n' "$SUBJECT"
    if [[ -n "$IN_REPLY_TO" ]]; then printf 'In-Reply-To: %s\r\n' "$IN_REPLY_TO"; fi
    if [[ -n "$REFERENCES" ]];  then printf 'References: %s\r\n'  "$REFERENCES";  fi
}

build_plain() {
    write_base_headers
    printf 'Content-Type: text/plain; charset=utf-8\r\n\r\n'
    printf '%s\r\n' "$BODY"
}

build_multipart() {
    write_base_headers
    printf 'Content-Type: multipart/mixed; boundary="%s"\r\n\r\n' "$BOUNDARY"

    printf -- '--%s\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n' "$BOUNDARY"
    printf '%s\r\n\r\n' "$BODY"

    for entry in "${ATTACHMENTS[@]}"; do
        IFS=':' read -r path name <<< "$entry"
        [[ -z "$name" ]] && name=$(basename "$path")
        encoded=$(base64 -w 76 "$path")
        mime=$(file --mime-type -b "$path" 2>/dev/null || echo "application/octet-stream")
        printf -- '--%s\r\n' "$BOUNDARY"
        printf 'Content-Type: %s; name="%s"\r\n' "$mime" "$name"
        printf 'Content-Disposition: attachment; filename="%s"\r\n' "$name"
        printf 'Content-Transfer-Encoding: base64\r\n\r\n'
        printf '%s\r\n\r\n' "$encoded"
    done
    printf -- '--%s--\r\n' "$BOUNDARY"
}

if [[ ${#ATTACHMENTS[@]} -gt 0 ]]; then
    build_multipart
else
    build_plain
fi | msmtp \
    --host="$SMTP_HOST" --port="$SMTP_PORT" \
    --auth=on --user="$FROM" \
    --passwordeval="secret-tool lookup account $ACCOUNT service $SMTP_SERVICE" \
    --tls=on --tls-starttls="$STARTTLS_FLAG" \
    --tls-trust-file=/etc/ssl/certs/ca-certificates.crt \
    --from="$FROM" \
    "${TO_ADDRS[@]}"
