#!/usr/bin/env python3
"""Read-side email client: list/read/folders over IMAP, stdlib only.

Replaces himalaya for reading. himalaya's SMTP is already bypassed (msmtp,
see email-send.sh) due to upstream bugs; this does the same for the read
path, using only imaplib/email/ssl from Python's standard library — no pip
installs, no external binary, one less moving part to break.

Accounts are resolved by nickname through accounts.py's registry
(~/.config/email-client/accounts.json), not hardcoded here. Credentials come
from the GNOME keyring via secret-tool, using the imap_service name recorded
for that account.

Subcommands:
  list    -a <nickname> [--folder FOLDER] [--after YYYY-MM-DD] [FILTER...] [--limit N]
  read    -a <nickname> [--folder FOLDER] <uid>
  folders -a <nickname>

Structured output is JSON so callers don't need to parse a text table.
Every envelope from `list` includes message_id, so a separate lookup (as
email-get-message-id.sh used to require) is no longer needed for replies.

FILTER arguments use the same DSL as list-manager's `read` filters, so there's
one filtering language across the toolkit instead of a second one just for
mail: `key=value` (exact, comma-separated = OR) or `key~=value` (regex
search, case-insensitive). Fields: id, subject, from, date, message_id, flags.
IMAP SEARCH itself can't do regex, so `--after` narrows the candidate set on
the server (day-level, cheap) and filters are applied client-side in Python
against the fetched headers — the tradeoff is one header fetch per candidate
in the `--after` window, not per final match.
"""
import argparse
import email
import email.message
import email.utils
import imaplib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

FOLDER_ALIASES = {
    "sent": "[Gmail]/Sent Mail",
    "trash": "[Gmail]/Trash",
    "drafts": "[Gmail]/Drafts",
    "inbox": "INBOX",
    "all": "[Gmail]/All Mail",
}


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def resolve_account(nickname: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "accounts.py"), "resolve", "--nickname", nickname],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        die(result.stderr.strip() or f"could not resolve account '{nickname}'")
    return json.loads(result.stdout)


def get_password(nickname: str, service: str) -> str:
    result = subprocess.run(
        ["secret-tool", "lookup", "account", nickname, "service", service],
        capture_output=True, text=True,
    )
    pw = result.stdout.strip()
    if result.returncode != 0 or not pw:
        die(f"no credential in keyring for account={nickname} service={service}")
    return pw


def connect(nickname: str) -> tuple[imaplib.IMAP4_SSL, dict]:
    account = resolve_account(nickname)
    password = get_password(nickname, account["imap_service"])
    conn = imaplib.IMAP4_SSL(account["imap"]["host"], account["imap"]["port"])
    conn.login(account["email"], password)
    return conn, account


def resolve_folder(folder: str) -> str:
    return FOLDER_ALIASES.get(folder.lower(), folder)


def decode_mime_words(raw: str | None) -> str:
    if not raw:
        return ""
    # Unfold RFC 5322 header folding (CRLF followed by whitespace continuation)
    # before decoding, or the literal "\r\n " ends up embedded in the value.
    raw = re.sub(r"\r\n[ \t]+", " ", raw)
    parts = decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    dt = email.utils.parsedate_to_datetime(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── client-side filtering (same DSL as list-manager's `read` filters) ──────

def parse_filters(filter_args: list[str]) -> list[tuple[str, str, str]]:
    """Parse 'key=value' / 'key~=value' strings into (key, op, value) tuples."""
    filters = []
    for f in filter_args:
        m = re.match(r"^([^~=]+)(~=|=)(.+)$", f)
        if not m:
            die(f"invalid filter '{f}': expected key=value or key~=value")
        filters.append((m.group(1), m.group(2), m.group(3)))
    return filters


def envelope_matches(envelope: dict, filters: list[tuple[str, str, str]]) -> bool:
    """AND across distinct keys, OR within repeats of the same key."""
    from collections import defaultdict
    by_key: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, op, val in filters:
        by_key[key].append((op, val))

    for key, conditions in by_key.items():
        raw = envelope.get(key, "")
        field_val = ",".join(raw) if isinstance(raw, list) else str(raw)
        matched_any = False
        for op, val in conditions:
            if op == "=":
                if field_val in [v.strip() for v in val.split(",")]:
                    matched_any = True
                    break
            elif op == "~=":
                try:
                    if re.search(val, field_val, re.IGNORECASE):
                        matched_any = True
                        break
                except re.error:
                    if val in field_val:
                        matched_any = True
                        break
        if not matched_any:
            return False
    return True


# ── list ──────────────────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> None:
    filters = parse_filters(args.filters)

    conn, _ = connect(args.account)
    try:
        folder = resolve_folder(args.folder)
        status, _ = conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            die(f"cannot select folder '{folder}'")

        if args.after:
            imap_date = datetime.strptime(args.after, "%Y-%m-%d").strftime("%d-%b-%Y")
            search_expr = f"SINCE {imap_date}"
        else:
            search_expr = "ALL"

        status, data = conn.uid("search", None, search_expr)
        if status != "OK":
            die(f"IMAP search failed: {data}")
        uids = data[0].split()

        envelopes = []
        # One FETCH per batch of UIDs, not one round-trip per message — with
        # no --after, "ALL" can mean the whole mailbox (thousands of UIDs),
        # and one round-trip each was slow enough to time out in practice.
        batch_size = 300
        for start in range(0, len(uids), batch_size):
            batch = uids[start:start + batch_size]
            uid_set = b",".join(batch) if isinstance(batch[0], bytes) else ",".join(batch)
            status, msg_data = conn.uid(
                "fetch", uid_set, "(UID FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])"
            )
            if status != "OK":
                continue
            for part in msg_data:
                if not isinstance(part, tuple):
                    continue
                meta_bytes, header_bytes = part
                uid_m = re.search(rb"UID (\d+)", meta_bytes)
                if not uid_m:
                    continue
                flags = imaplib.ParseFlags(meta_bytes) or ()
                flags = [f.decode() if isinstance(f, bytes) else f for f in flags]
                msg = email.message_from_bytes(header_bytes)
                envelope = {
                    "id": uid_m.group(1).decode(),
                    "flags": flags,
                    "subject": decode_mime_words(msg.get("Subject")),
                    "from": decode_mime_words(msg.get("From")),
                    "date": parse_date(msg.get("Date")),
                    "message_id": (msg.get("Message-ID") or "").strip(),
                }
                if not filters or envelope_matches(envelope, filters):
                    envelopes.append(envelope)

        envelopes.sort(key=lambda e: int(e["id"]))
        if args.limit:
            envelopes = envelopes[-args.limit:]

        print(json.dumps(envelopes, indent=2))
    finally:
        conn.logout()


# ── read ──────────────────────────────────────────────────────────────────

def extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        text_plain, text_html = None, None
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                continue
            if ctype == "text/plain" and text_plain is None:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                text_plain = payload.decode(charset, errors="replace")
            elif ctype == "text/html" and text_html is None:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                text_html = payload.decode(charset, errors="replace")
        if text_plain:
            return text_plain
        if text_html:
            text = re.sub(r"<[^>]+>", " ", text_html)
            return re.sub(r"\s+", " ", text).strip()
        return "(no readable body)"
    else:
        payload = msg.get_payload(decode=True)
        if payload is None:
            return str(msg.get_payload())
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")


def format_read_output(msg: email.message.Message) -> str:
    """Render headers + decoded body for `mail-read`. Pure/testable: takes an
    already-parsed message, no network.

    In-Reply-To/References are only printed when present (unlike the other
    headers, which always print, even empty) since most messages aren't
    replies and the blank lines would just be noise.
    """
    lines = [
        f"Subject: {decode_mime_words(msg.get('Subject'))}",
        f"From: {decode_mime_words(msg.get('From'))}",
        f"To: {decode_mime_words(msg.get('To'))}",
        f"Date: {msg.get('Date', '')}",
        f"Message-ID: {(msg.get('Message-ID') or '').strip()}",
    ]
    if msg.get("In-Reply-To"):
        lines.append(f"In-Reply-To: {msg.get('In-Reply-To').strip()}")
    if msg.get("References"):
        lines.append(f"References: {msg.get('References').strip()}")
    lines.append("")
    lines.append(extract_body(msg))
    return "\n".join(lines)


def cmd_read(args: argparse.Namespace) -> None:
    conn, _ = connect(args.account)
    try:
        folder = resolve_folder(args.folder)
        status, _ = conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            die(f"cannot select folder '{folder}'")

        status, msg_data = conn.uid("fetch", args.uid, "(RFC822)")
        if status != "OK" or not msg_data or msg_data[0] is None:
            die(f"no message with id {args.uid} in folder '{folder}'")

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        print(format_read_output(msg))
    finally:
        conn.logout()


# ── folders ───────────────────────────────────────────────────────────────

def cmd_folders(args: argparse.Namespace) -> None:
    conn, _ = connect(args.account)
    try:
        status, data = conn.list()
        if status != "OK":
            die("IMAP LIST failed")
        folders = []
        for line in data:
            if isinstance(line, bytes):
                line = line.decode(errors="replace")
            parts = line.rsplit('"', 2)
            if len(parts) >= 2:
                folders.append(parts[-2])
        print(json.dumps(folders, indent=2))
    finally:
        conn.logout()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("-a", "--account", required=True)
    p_list.add_argument("--folder", default="inbox")
    p_list.add_argument("--after")
    p_list.add_argument("--limit", type=int)
    p_list.add_argument("filters", nargs="*", help="key=value or key~=value, e.g. subject~=meeting")
    p_list.set_defaults(func=cmd_list)

    p_read = sub.add_parser("read")
    p_read.add_argument("-a", "--account", required=True)
    p_read.add_argument("--folder", default="inbox")
    p_read.add_argument("uid")
    p_read.set_defaults(func=cmd_read)

    p_folders = sub.add_parser("folders")
    p_folders.add_argument("-a", "--account", required=True)
    p_folders.set_defaults(func=cmd_folders)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
