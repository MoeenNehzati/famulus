#!/usr/bin/env bash
# email-get-message-id.sh — fetch the Message-ID header of an email envelope
#
# Himalaya's formatted output omits Message-ID. This script fetches the raw
# headers directly via IMAP using curl and the App Password from GNOME keyring.
#
# Usage: email-get-message-id.sh [-a nyu|personal] [--folder FOLDER] <envelope-id>
# Output: raw Message-ID value with angle brackets, e.g. <abc123@mail.gmail.com>
#         (no trailing newline)
#
# Envelope IDs are the numeric IDs shown by `himalaya envelope list` — these
# correspond to IMAP UIDs.
#
# Default folder: [Gmail]/All Mail

set -euo pipefail

ACCOUNT="nyu"
FOLDER="[Gmail]/All Mail"
ID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -a|--account) ACCOUNT="$2"; shift 2 ;;
        --folder)     FOLDER="$2";  shift 2 ;;
        *)            ID="$1";      shift ;;
    esac
done

[[ -z "$ID" ]] && {
    echo "usage: email-get-message-id.sh [-a nyu|personal] [--folder FOLDER] <envelope-id>" >&2
    exit 1
}

case "$ACCOUNT" in
    nyu)      USER="sn3379@nyu.edu" ;;
    personal) USER="smnehzati@gmail.com" ;;
    *) echo "Unknown account '$ACCOUNT'; use nyu or personal" >&2; exit 1 ;;
esac

PASS=$(secret-tool lookup account "$ACCOUNT" service himalaya-imap 2>/dev/null)
[[ -z "$PASS" ]] && {
    echo "No IMAP password in keyring for account=$ACCOUNT service=himalaya-imap" >&2
    exit 1
}

# URL-encode the folder name (Gmail folders contain spaces and brackets)
FOLDER_ENC=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FOLDER")

# Fetch all message headers via IMAP UID fetch, then grep for Message-ID
curl -s \
    --url "imaps://imap.gmail.com/${FOLDER_ENC};UID=${ID};SECTION=HEADER" \
    --user "${USER}:${PASS}" \
    --ssl-reqd \
  | grep -i "^Message-ID:" \
  | head -1 \
  | sed 's/^[Mm]essage-[Ii][Dd]:[[:space:]]*//' \
  | tr -d '\r\n'
