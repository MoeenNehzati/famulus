---
name: email-client
description: Use when reading, listing, searching, or sending email for the user. Covers both nyu (sn3379@nyu.edu) and personal (smnehzati@gmail.com) Gmail accounts.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: general-assistant

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `get-message-id` — Fetch the raw Message-ID header of an IMAP envelope by its numeric ID.
  - `dispatcher --caller-skill email-client email-client get-message-id [-a nyu|personal] [--folder <folder>] <envelope-id>`
- `send-email` — Send an email via msmtp; body comes from stdin.
  - `dispatcher --caller-skill email-client email-client send-email --from nyu|personal --to <addr> [--to <addr>...] --subject <subject> [--attach /path[:DisplayName]] [--in-reply-to <msg-id>] [--references <refs>]`
<!-- END BLUEPRINT INTERFACES -->
# Email

Two Gmail accounts: **nyu** (`sn3379@nyu.edu`) and **personal** (`smnehzati@gmail.com`).

**Default account by context:**
- `nyu` — anything academic: research, professors, coursework, NYU/university contacts, conference emails
- `personal` — everything else: personal correspondence, shopping, services, non-academic contacts

## Reading — himalaya (IMAP only)

himalaya v1.2.0 IMAP works correctly. **SMTP/send is broken** — see Known Bugs.

```bash
# List recent envelopes (default account = personal)
himalaya envelope list
himalaya envelope list -a nyu
himalaya envelope list -a nyu --folder "[Gmail]/Sent Mail"

# Read a message by ID
himalaya message read -a nyu 42

# Search
himalaya envelope list --query "subject:\"meeting\""

# List folders
himalaya folder list -a nyu
```

Gmail folder names: `INBOX`, `[Gmail]/Sent Mail`, `[Gmail]/Drafts`, `[Gmail]/Trash`, `[Gmail]/All Mail`.

## Sending — `send-email`

**Never use `himalaya message send` or `himalaya template send` — both are broken** (v1.2.0 bugs; see below).

Use the `send-email` interface. Body comes from stdin.

Flags: `--from nyu|personal` (required), `--to <addr>` (repeatable, required), `--subject <subject>` (required), `--attach /path[:DisplayName]` (repeatable, optional), `--in-reply-to <msg-id>` (optional), `--references <refs>` (optional).

**Accounts:** `--from nyu` or `--from personal`.

## Replying to a thread

Himalaya's formatted output omits `Message-ID`. Use `get-message-id` to fetch it, then pass it to `send-email`:

1. Call `get-message-id` with `-a nyu|personal`, `--folder <folder>`, and the envelope ID to obtain the raw `Message-ID`.
2. Call `send-email` with `--in-reply-to <msg-id>` set to that value; body from stdin.

- `--in-reply-to <message-id>` — sets `In-Reply-To`; `References` defaults to the same value
- `--references <refs>` — override `References` explicitly (deep threads with multiple ancestors)
- Subject should be `Re: <original subject>` to match the thread in Gmail

Passwords come from GNOME keyring via `secret-tool`. Keys:
- `secret-tool lookup account nyu service himalaya-smtp`
- `secret-tool lookup account personal service himalaya-smtp`
- `secret-tool lookup account nyu service himalaya-imap`
- `secret-tool lookup account personal service himalaya-imap`

## Known Bugs — himalaya v1.2.0

1. **SMTP backend hangs** after Gmail's `CertificateRequest` in TLS 1.3 (rustls bug). msmtp/curl handle it fine.
2. **Sendmail backend hangs** at "building new sendmail context" — never calls the external command. Affects even trivially configured commands like `cat`. No workaround; use email-send.sh directly.

These bugs apply regardless of how himalaya is configured. Do not attempt to fix by reconfiguring himalaya.

## Config Files

- himalaya: `~/.config/himalaya/config.toml`
- msmtp: `~/.config/msmtp/config` (accounts: `nyu`, `personal`)
- App Passwords stored in GNOME keyring via `secret-tool`
