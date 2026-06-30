---
name: email-client
description: Use when reading, listing, searching, or sending email for the user. Covers both nyu (sn3379@nyu.edu) and personal (smnehzati@gmail.com) Gmail accounts.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: automation

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
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

## Sending — email-send.sh

**Never use `himalaya message send` or `himalaya template send` — both are broken** (v1.2.0 bugs; see below).

Use `scripts/email-send.sh`. Body comes from stdin.

```bash
# Plain text
echo "Hi there." | scripts/email-send.sh \
  --from nyu \
  --to recipient@example.com \
  --subject "Hello"

# Multiple recipients + attachments (path:DisplayName — DisplayName optional)
cat <<'EOF' | scripts/email-send.sh \
  --from nyu \
  --to alice@example.com \
  --to bob@example.com \
  --subject "Papers" \
  --attach /path/to/file.pdf:"paper.pdf" \
  --attach /path/to/slides.pdf
Body text here.
EOF
```

**Accounts:** `--from nyu` or `--from personal`.

## Replying to a thread

Himalaya's formatted output omits `Message-ID`. Use `email-get-message-id.sh` to fetch it, then pass it to `email-send.sh`:

```bash
# 1. Get the Message-ID of the message being replied to
MSG_ID=$(scripts/email-get-message-id.sh -a nyu --folder "[Gmail]/All Mail" <envelope-id>)

# 2. Send the threaded reply
cat <<'BODY' | scripts/email-send.sh \
  --from nyu \
  --to sender@example.com \
  --subject "Re: Original Subject" \
  --in-reply-to "$MSG_ID"
Reply body here.
BODY
```

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
