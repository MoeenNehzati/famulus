---
name: email-client
description: Use when reading, listing, searching, or sending email for the user. Covers any account nickname registered in the account registry (run accounts-list to see what's configured).
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: communications, external-integrations; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `email-client.source.gateway -> connect-google.interface.default@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-add@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-list@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-remove@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-set-password@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-setup-oauth@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-update@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-use-google-credential@1`
- `email-client.source.gateway -> email-client._rtx.interface.live-smoke@1`
- `email-client.source.gateway -> email-client._rtx.interface.mail-attachments@1`
- `email-client.source.gateway -> email-client._rtx.interface.mail-folders@1`
- `email-client.source.gateway -> email-client._rtx.interface.mail-list@1`
- `email-client.source.gateway -> email-client._rtx.interface.mail-read@1`
- `email-client.source.gateway -> email-client._rtx.interface.mail-save-attachments@1`
- `email-client.source.gateway -> email-client._rtx.interface.send-email@1`

Public Interfaces:
- `email-client.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `email-client.interface.default` — Primary LLM-facing skill instructions.
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
`connect-google.interface.default` to prepare the shared Desktop client, then return
here. Select or register the Gmail nickname, then invoke
`email-client._rtx.interface.accounts-setup-oauth` with that nickname and
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
