#!/usr/bin/env python3
"""Read-side email client: list/read/folders over IMAP, stdlib only.

IMAP read client using only imaplib/email/ssl from Python's standard
library — no pip installs, no external binary, one less moving part to
break. Sending (see email-send.sh) uses msmtp the same way, directly.

Accounts are resolved by nickname through accounts.py's registry
(~/.config/email-client/accounts.json), not hardcoded here. Credentials come
from the GNOME keyring via secret-tool, using the imap_service name recorded
for that account.

Subcommands:
  list    -a <nickname> [--folder FOLDER] [--after YYYY-MM-DD] [FILTER...] [--limit N]
  read    -a <nickname> [--folder FOLDER] <uid>
  attachments -a <nickname> [--folder FOLDER] <uid> [<uid> ...]
  save-attachments -a <nickname> [--folder FOLDER] <uid> [<uid> ...] --out DIR (--all | --name NAME [...])
  folders -a <nickname>

Structured output is JSON so callers don't need to parse a text table.
Every envelope from `list` includes message_id, so a separate lookup (as
email-get-message-id.sh used to require) is no longer needed for replies.
`read` is the exception: it prints a readable text view with headers,
attachment names/metadata, then the decoded body.

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
        [sys.executable, str(SCRIPT_DIR / "_email_accounts.py"), "resolve", "--nickname", nickname],
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


def format_size(size_bytes: int) -> str:
    if size_bytes < 1000:
        return f"{size_bytes} B"
    size = float(size_bytes)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1000.0
        if size < 999.5 or unit == "TB":
            if size >= 10:
                return f"{round(size):.0f} {unit}"
            return f"{size:.1f} {unit}"
    return f"{size_bytes} B"


def clean_attachment_name(raw_name: str | None) -> str:
    if not raw_name:
        return ""
    decoded = decode_mime_words(raw_name).replace("\x00", "").strip()
    if not decoded:
        return ""
    return re.split(r"[\\/]+", decoded)[-1]


def attachment_payload_bytes(part: email.message.Message) -> bytes:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    if payload is None:
        raw = part.get_payload()
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            charset = part.get_content_charset() or "utf-8"
            return raw.encode(charset, errors="replace")
    return b""


def collect_attachments(msg: email.message.Message) -> list[dict]:
    if not msg.is_multipart():
        return []

    attachments = []
    unnamed_count = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        raw_name = part.get_filename()
        if disposition != "attachment" and not raw_name:
            continue

        name = clean_attachment_name(raw_name)
        if not name:
            unnamed_count += 1
            name = f"attachment-{unnamed_count}"
        payload = attachment_payload_bytes(part)
        size_bytes = len(payload)
        attachments.append(
            {
                "name": name,
                "content_type": part.get_content_type(),
                "size_bytes": size_bytes,
                "size_human": format_size(size_bytes),
                "disposition": disposition,
                "_payload": payload,
            }
        )
    return attachments


def public_attachment_record(record: dict) -> dict:
    return {
        "name": record["name"],
        "content_type": record["content_type"],
        "size_bytes": record["size_bytes"],
        "size_human": record["size_human"],
        "disposition": record["disposition"],
    }


def render_attachment_lines(msg: email.message.Message) -> list[str]:
    attachments = [public_attachment_record(record) for record in collect_attachments(msg)]
    if not attachments:
        return ["Attachments: none"]

    lines = ["Attachments:"]
    for attachment in attachments:
        lines.append(
            f"- {attachment['name']} "
            f"({attachment['content_type']}, {attachment['size_human']})"
        )
    return lines


def unique_output_path(out_dir: Path, filename: str) -> Path:
    candidate = out_dir / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem or filename
    suffix = candidate.suffix
    index = 2
    while True:
        candidate = out_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def save_attachment_records(
    attachments: list[dict],
    out_dir: Path,
    *,
    selected_names: set[str] | None = None,
    uid: str | None = None,
    subject: str | None = None,
) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for record in attachments:
        if selected_names is not None and record["name"] not in selected_names:
            continue
        dest = unique_output_path(out_dir, record["name"])
        dest.write_bytes(record["_payload"])
        saved.append(
            {
                "uid": uid,
                "subject": subject,
                "attachment": record["name"],
                "content_type": record["content_type"],
                "size_bytes": record["size_bytes"],
                "size_human": record["size_human"],
                "disposition": record["disposition"],
                "saved_to": str(dest),
            }
        )
    return saved


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
    lines.extend(render_attachment_lines(msg))
    lines.append("")
    lines.append(extract_body(msg))
    return "\n".join(lines)


def fetch_message(conn: imaplib.IMAP4_SSL, uid: str, folder: str) -> email.message.Message:
    status, msg_data = conn.uid("fetch", uid, "(RFC822)")
    if status != "OK" or not msg_data:
        die(f"no message with id {uid} in folder '{folder}'")
    for part in msg_data:
        if isinstance(part, tuple) and len(part) >= 2 and part[1] is not None:
            return email.message_from_bytes(part[1])
    die(f"no message with id {uid} in folder '{folder}'")


def cmd_read(args: argparse.Namespace) -> None:
    conn, _ = connect(args.account)
    try:
        folder = resolve_folder(args.folder)
        status, _ = conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            die(f"cannot select folder '{folder}'")

        msg = fetch_message(conn, args.uid, folder)
        print(format_read_output(msg))
    finally:
        conn.logout()


def cmd_attachments(args: argparse.Namespace) -> None:
    conn, _ = connect(args.account)
    try:
        folder = resolve_folder(args.folder)
        status, _ = conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            die(f"cannot select folder '{folder}'")

        records = []
        for uid in args.uids:
            msg = fetch_message(conn, uid, folder)
            records.append(
                {
                    "uid": uid,
                    "subject": decode_mime_words(msg.get("Subject")),
                    "attachments": [
                        public_attachment_record(record) for record in collect_attachments(msg)
                    ],
                }
            )
        print(json.dumps(records, indent=2))
    finally:
        conn.logout()


def cmd_save_attachments(args: argparse.Namespace) -> None:
    conn, _ = connect(args.account)
    try:
        folder = resolve_folder(args.folder)
        status, _ = conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            die(f"cannot select folder '{folder}'")

        out_dir = Path(args.out)
        selected_names = None if args.all else set(args.name or [])
        saved = []
        for uid in args.uids:
            msg = fetch_message(conn, uid, folder)
            saved.extend(
                save_attachment_records(
                    collect_attachments(msg),
                    out_dir,
                    selected_names=selected_names,
                    uid=uid,
                    subject=decode_mime_words(msg.get("Subject")),
                )
            )
        if not saved:
            die("no attachments matched the requested selection")
        print(json.dumps(saved, indent=2))
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

    p_attachments = sub.add_parser("attachments")
    p_attachments.add_argument("-a", "--account", required=True)
    p_attachments.add_argument("--folder", default="inbox")
    p_attachments.add_argument("uids", nargs="+")
    p_attachments.set_defaults(func=cmd_attachments)

    p_save = sub.add_parser("save-attachments")
    p_save.add_argument("-a", "--account", required=True)
    p_save.add_argument("--folder", default="inbox")
    p_save.add_argument("uids", nargs="+")
    p_save.add_argument("--out", required=True)
    selection = p_save.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--name", action="append")
    p_save.set_defaults(func=cmd_save_attachments)

    p_folders = sub.add_parser("folders")
    p_folders.add_argument("-a", "--account", required=True)
    p_folders.set_defaults(func=cmd_folders)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
