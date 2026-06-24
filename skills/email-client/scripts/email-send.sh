#!/usr/bin/env bash
# email-send — send email via msmtp with optional PDF/file attachments and reply threading
# Usage: email-send.sh --from ACCT --to ADDR [--to ADDR...] --subject SUBJ
#                      [--in-reply-to <message-id>] [--references <refs>]
#                      [--attach /path[:DisplayName]] ... < body.txt
#
# ACCT: nyu  → sn3379@nyu.edu
#       personal → smnehzati@gmail.com
#
# Threading: pass --in-reply-to with the parent's Message-ID (angle brackets included,
# e.g. <abc123@mail.gmail.com>). References defaults to the same value if not given.

set -euo pipefail

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

[[ -z "$ACCOUNT" ]] && { echo "--from required (nyu|personal)" >&2; exit 1; }
[[ ${#TO_ADDRS[@]} -eq 0 ]] && { echo "--to required" >&2; exit 1; }
[[ -z "$SUBJECT" ]] && { echo "--subject required" >&2; exit 1; }

# Default References to In-Reply-To when threading a single-level reply
[[ -n "$IN_REPLY_TO" && -z "$REFERENCES" ]] && REFERENCES="$IN_REPLY_TO"

case "$ACCOUNT" in
    nyu)      FROM="sn3379@nyu.edu" ;;
    personal) FROM="smnehzati@gmail.com" ;;
    *) echo "Unknown account '$ACCOUNT'; use nyu or personal" >&2; exit 1 ;;
esac

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
fi | msmtp --account="$ACCOUNT" "${TO_ADDRS[@]}"
