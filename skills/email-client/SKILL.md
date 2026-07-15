---
name: email-client
description: Use when reading, listing, searching, or sending email for the user. Covers any account nickname registered in the account registry (run accounts-list to see what's configured).
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: productivity-general-assistant

Skill Version: 3

Uses Interfaces:
- `email-client.llm.default -> connect-google.llm.default@1`
- `email-client.llm.default -> email-client.machine.accounts-setup-oauth@1`

Public Interfaces:
- `email-client.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `accounts-add` — Register a new account nickname. Gmail IMAP/SMTP settings are the default; pass explicit host/port flags for other providers. App-password auth is the default; use --auth gmail-oauth for Gmail OAuth.
  - `dispatcher --caller-skill email-client email-client.machine.accounts-add --nickname <nick> --email <addr> [--display-name <name>] [--imap-host H] [--imap-port P] [--smtp-host H] [--smtp-port P] [--starttls] [--auth app-password|gmail-oauth]`
- `accounts-list` — List registered account nicknames with their email/display name (no secrets).
  - `dispatcher --caller-skill email-client email-client.machine.accounts-list`
- `accounts-remove` — Remove an account nickname from the registry; optionally purge its stored credentials too.
  - `dispatcher --caller-skill email-client email-client.machine.accounts-remove --nickname <nick> [--purge-credentials]`
- `accounts-set-password` — Store the IMAP or SMTP credential for an account in the host credential store. The secret is read from stdin, never a CLI argument.
  - `dispatcher --caller-skill email-client email-client.machine.accounts-set-password --nickname <nick> --purpose imap|smtp`
- `accounts-setup-oauth` — Complete Gmail OAuth setup for an account using a Google desktop OAuth client JSON file. Stores refresh token and client secret in the host credential store.
  - `dispatcher --caller-skill email-client email-client.machine.accounts-setup-oauth --nickname <nick> --client-config <path> [--no-open-browser]`
- `accounts-update` — Update fields on an existing account nickname.
  - `dispatcher --caller-skill email-client email-client.machine.accounts-update --nickname <nick> [--email <addr>] [--display-name <name>] [--imap-host H] [--imap-port P] [--smtp-host H] [--smtp-port P] [--auth app-password|gmail-oauth]`
- `live-smoke` — Run explicit live provider smoke checks for one account. --imap and --smtp-auth authenticate without sending; --send-self sends a test email to the account's own address.
  - `dispatcher --caller-skill email-client email-client.machine.live-smoke -a <nickname> [--imap] [--smtp-auth] [--send-self] [--body <text>]`
- `mail-attachments` — List attachment metadata for one or more emails as JSON. Returns one record per requested UID with attachment entries containing filename, content_type, size_bytes, size_human, and disposition.
  - `dispatcher --caller-skill email-client email-client.machine.mail-attachments -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid> [<uid> ...]`
- `mail-folders` — List IMAP folders for an account (JSON).
  - `dispatcher --caller-skill email-client email-client.machine.mail-folders -a <nickname>`
- `mail-list` — List email envelopes for an account as JSON (fields: id, flags, subject, from, date, message_id). --folder accepts aliases inbox|sent|drafts|trash|all or any literal IMAP folder name (default inbox). --after narrows server-side by day (IMAP SINCE). Filters are key=value (exact, comma-separated=OR) or key~=value (regex, case-insensitive) over id/subject/from/date/message_id/flags, ANDed across distinct keys, applied client-side after fetch. Unfiltered + undated scans the whole folder (slow on large mailboxes) — pair filters with --after.
  - `dispatcher --caller-skill email-client email-client.machine.mail-list -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] [--after YYYY-MM-DD] [key=value|key~=value ...] [--limit N]`
- `mail-read` — Read one email by UID (the "id" field from mail-list). Prints Subject/From/To/ Date/Message-ID, then In-Reply-To/References only if the message is a reply, then an Attachments section (none or one line per attachment with filename, MIME type, and size), then a blank line, then the decoded body (text/plain preferred; falls back to HTML with tags stripped).
  - `dispatcher --caller-skill email-client email-client.machine.mail-read -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid>`
- `mail-save-attachments` — Save attachments from one or more emails into a directory. Use --all to save every attachment, or repeat --name to save only selected filenames. Returns JSON describing the saved files.
  - `dispatcher --caller-skill email-client email-client.machine.mail-save-attachments -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid> [<uid> ...] --out <dir> (--all | --name <filename> [--name <filename> ...])`
- `send-email` — Send an email via SMTP; body comes from stdin.
  - `dispatcher --caller-skill email-client email-client.machine.send-email --from <nickname> --to <addr> [--to <addr>...] --subject <subject> [--attach /path[:DisplayName]] [--in-reply-to <msg-id>] [--references <refs>]`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
# Email

Accounts are nicknames registered in a small local registry, not hardcoded in this
skill. Run `accounts-list` to see what's configured — nicknames and their default
routing rules (e.g. "work stuff goes through the `work` account") live in the user's
own memory/preferences, not in this skill.

Reading and sending both go through plain IMAP/SMTP in Python. Credentials are
looked up through the shared `officina.common.secret_store` boundary.

## Reading and attachments — `mail-list` / `mail-read` / `mail-attachments` / `mail-save-attachments` / `mail-folders`

All return JSON except `mail-read`, which prints a readable text view: headers, an
`Attachments:` section, then the decoded body.

```bash
# List recent envelopes (folder defaults to inbox)
mail-list -a work
mail-list -a work --folder sent
mail-list -a work --after 2026-07-01
mail-list -a work 'subject~=meeting'
mail-list -a work --limit 20

# Read a message by UID (the "id" field from mail-list output)
mail-read -a work 42

# Machine-readable attachment listing for one or more messages
mail-attachments -a work 42
mail-attachments -a work 42 43 44

# Save every attachment from a message
mail-save-attachments -a work 42 --out /tmp/mail-attachments --all

# Save only selected filenames, even across multiple messages
mail-save-attachments -a work 42 43 --out /tmp/mail-attachments \
  --name lessons.zip --name screenshot.png

# List folders
mail-folders -a work
```

Folder aliases: `inbox`, `sent`, `drafts`, `trash`, `all` map to the right Gmail
special-use folder; any other string is passed through as a literal IMAP folder name
(e.g. a Gmail label like `github`).

**Filters** use the same `key=value`/`key~=value` DSL as other list-filtering interfaces
in this toolkit — one filtering language, not a second one just for mail: `key=value`
(exact, comma-separated = OR) or `key~=value` (regex search, case-insensitive), multiple
filters ANDed together. Fields: `id`, `subject`, `from`, `date`, `message_id`, `flags`.

```bash
mail-list -a work 'subject~=ICML 2026'                    # phrase, quote it
mail-list -a work 'from~=icml\.cc' 'subject~=CHECKIN'      # AND across fields
mail-list -a work 'flags~=Answered'                        # already-replied messages
```

IMAP `SEARCH` itself can't do regex, so filters are applied client-side in Python
against fetched headers — `--after` still narrows the candidate set on the server first
(day-level, via IMAP `SINCE`), so pair a filter with `--after` when you can; an unfiltered,
un-dated `mail-list` scans the whole folder (tens of seconds on a large mailbox).

Every envelope from `mail-list` includes `message_id` — no separate lookup needed for
replies (see below).

Use the interfaces this way:

- `mail-read` — human-oriented read path; always shows attachment names/metadata so you
  can see what the email carries without a second command.
- `mail-attachments` — JSON attachment metadata when a caller needs structured output or
  wants to inspect several message UIDs at once.
- `mail-save-attachments` — download path. `--all` saves every attachment; repeated
  `--name <filename>` restricts to selected filenames. If two saved files would collide,
  the later one gets a numeric suffix like `notes-2.pdf`.

## Sending — `send-email`

Use the `send-email` interface. Body comes from stdin.

Flags: `--from <nickname>` (required), `--to <addr>` (repeatable, required), `--subject <subject>` (required), `--attach /path[:DisplayName]` (repeatable, optional), `--in-reply-to <msg-id>` (optional), `--references <refs>` (optional).

## Replying to a thread

`mail-list` already returns `message_id` for every envelope — grab it from there and pass
straight to `send-email`, no extra lookup:

1. Find the envelope in `mail-list` output; take its `message_id`.
2. Call `send-email` with `--in-reply-to <msg-id>` set to that value; body from stdin.

- `--in-reply-to <message-id>` — sets `In-Reply-To`; `References` defaults to the same value
- `--references <refs>` — override `References` explicitly (deep threads with multiple ancestors)
- Subject should be `Re: <original subject>` to match the thread in Gmail

## Managing accounts — `accounts-list` / `accounts-add` / `accounts-update` / `accounts-remove` / `accounts-set-password` / `accounts-setup-oauth`

The registry lives at `~/.config/email-client/accounts.json` (outside the skill
directory — it's per-machine and not source-controlled). Passwords, OAuth client
secrets, and OAuth refresh tokens are never stored there; they stay in the host
credential store.

For initial Google setup or Gmail OAuth reauthorization, use
`connect-google.llm.default` to prepare the shared Desktop client, then return
here. Select or register the Gmail nickname, then invoke
`email-client.machine.accounts-setup-oauth` with that nickname and
`--client-config ~/.config/connect-google/client.json`. email-client alone
lists, registers, updates, authorizes, and verifies Gmail accounts. Keep
non-Google account and app-password setup here as well.

```bash
accounts-list

# Add a Gmail account (IMAP/SMTP settings default to Gmail's)
accounts-add --nickname work --email me@company.com --display-name "Me at Work"

# Add a non-Gmail account — pass explicit host/port
accounts-add --nickname other --email me@example.com \
  --imap-host imap.example.com --imap-port 993 \
  --smtp-host smtp.example.com --smtp-port 587 --starttls

# Set the credential for each purpose (secret read from stdin, never a CLI arg)
echo -n '<app-password>' | accounts-set-password --nickname work --purpose imap
echo -n '<app-password>' | accounts-set-password --nickname work --purpose smtp

accounts-update --nickname work --display-name "New Display Name"
accounts-remove --nickname work --purge-credentials
```

Each account gets its own secret keys (`<nickname>:imap` and `<nickname>:smtp`) so
credentials never collide across accounts. Existing `imap_service`/`smtp_service`
registry fields are still honored as secondary secret keys during migration.

Gmail OAuth uses the `https://mail.google.com/` scope because Gmail's IMAP/SMTP
XOAUTH2 mechanism requires that full-mail scope. `accounts-setup-oauth` uses a
local loopback browser flow and stores the resulting refresh token through
`officina.common.secret_store`.

## Live smoke checks — `live-smoke`

Use `live-smoke -a <nickname> --imap --smtp-auth` to verify that the configured
account can authenticate to both providers without sending mail. `--send-self`
is the only smoke mode that sends a message, and it sends to the account's own
address.

## Config Files

- Account registry: `~/.config/email-client/accounts.json`
- App passwords, OAuth client secrets, and OAuth refresh tokens stored in the host credential store via `officina.common.secret_store`
