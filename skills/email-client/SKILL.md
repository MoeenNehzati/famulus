---
name: email-client
description: >-
  Use when the user asks to access or manage email or a registered email account. Do not use when the primary request is inbox triage or shared Google authentication setup.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `email-client._rtx.interface.accounts-add@1` — Register a new account nickname. Gmail IMAP/SMTP settings are the default; pass explicit host/port flags for other providers. App-password auth is the default; use --auth gmail-oauth for Gmail OAuth.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.accounts-add --nickname <nick> --email <addr> [--display-name <name>] [--imap-host H] [--imap-port P] [--smtp-host H] [--smtp-port P] [--starttls] [--auth app-password|gmail-oauth]`
- `email-client._rtx.interface.accounts-list@1` — List registered account nicknames with their email/display name (no secrets).
  - `dispatcher --caller-skill email-client email-client._rtx.interface.accounts-list`
- `email-client._rtx.interface.accounts-remove@1` — Remove an account nickname from the registry; optionally purge its stored credentials too.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.accounts-remove --nickname <nick> [--purge-credentials]`
- `email-client._rtx.interface.accounts-set-password@1` — Store the IMAP or SMTP credential for an account in the host credential store. The secret is read from stdin, never a CLI argument.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.accounts-set-password --nickname <nick> --purpose imap|smtp`
- `email-client._rtx.interface.accounts-update@1` — Update fields on an existing account nickname.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.accounts-update --nickname <nick> [--email <addr>] [--display-name <name>] [--imap-host H] [--imap-port P] [--smtp-host H] [--smtp-port P] [--auth app-password|gmail-oauth]`
- `email-client._rtx.interface.live-smoke@1` — Run explicit live provider smoke checks for one account. --imap and --smtp-auth authenticate without sending; --send-self sends a test email to the account's own address.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.live-smoke -a <nickname> [--imap] [--smtp-auth] [--send-self] [--body <text>]`
- `email-client._rtx.interface.mail-attachments@1` — List attachment metadata for one or more emails as JSON. Returns one record per requested UID with attachment entries containing filename, content_type, size_bytes, size_human, and disposition.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.mail-attachments -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid> [<uid> ...]`
- `email-client._rtx.interface.mail-folders@1` — List IMAP folders for an account (JSON).
  - `dispatcher --caller-skill email-client email-client._rtx.interface.mail-folders -a <nickname>`
- `email-client._rtx.interface.mail-list@1` — List email envelopes for an account as JSON (fields: id, flags, subject, from, date, message_id). --folder accepts aliases inbox|sent|drafts|trash|all or any literal IMAP folder name (default inbox). --after narrows server-side by day (IMAP SINCE). Filters are key=value (exact, comma-separated=OR) or key~=value (regex, case-insensitive) over id/subject/from/date/message_id/flags, ANDed across distinct keys, applied client-side after fetch. Unfiltered + undated scans the whole folder (slow on large mailboxes) — pair filters with --after.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.mail-list -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] [--after YYYY-MM-DD] [key=value|key~=value ...] [--limit N]`
- `email-client._rtx.interface.mail-read@1` — Read one email by UID (the "id" field from mail-list). Prints Subject/From/To/ Date/Message-ID, then In-Reply-To/References only if the message is a reply, then an Attachments section (none or one line per attachment with filename, MIME type, and size), then a blank line, then the decoded body (text/plain preferred; falls back to HTML with tags stripped).
  - `dispatcher --caller-skill email-client email-client._rtx.interface.mail-read -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid>`
- `email-client._rtx.interface.mail-save-attachments@1` — Save attachments from one or more emails into a directory. Use --all to save every attachment, or repeat --name to save only selected filenames. Returns JSON describing the saved files.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.mail-save-attachments -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid> [<uid> ...] --out <dir> (--all | --name <filename> [--name <filename> ...])`
- `email-client._rtx.interface.send-email@1` — Send an email via SMTP; body comes from stdin.
  - `dispatcher --caller-skill email-client email-client._rtx.interface.send-email --from <nickname> --to <addr> [--to <addr>...] --subject <subject> [--attach /path[:DisplayName]] [--in-reply-to <msg-id>] [--references <refs>]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `connect-google.interface.default@1` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
<!-- END BLUEPRINT INTERFACES -->
# Email

Accounts are selected by registered nickname. If the nickname is unknown, invoke
`email-client._rtx.interface.accounts-list` and use its JSON result; do not infer an
account from an address or provider.

## Reading and attachments

Use `email-client._rtx.interface.mail-list` to obtain message UIDs and envelope metadata.
Prefer an `after` date when filtering a large folder because filtering without a date
may scan the entire folder. Folder aliases and the filter grammar are defined by the
interface contract.

Use `email-client._rtx.interface.mail-read` for a human-readable message. Use
`email-client._rtx.interface.mail-attachments` when structured attachment metadata is
needed, and `email-client._rtx.interface.mail-save-attachments` only when files should be
written. Use `email-client._rtx.interface.mail-folders` when the target folder is unknown.

## Sending and replies

Use `email-client._rtx.interface.send-email`; provide the body on stdin. For a reply, first
obtain the original `message_id` from `email-client._rtx.interface.mail-list`, pass it as
`in-reply-to`, and use a reply subject. Override `references` only when the caller has
the complete ancestor chain. To send local files, repeat `--attach` with an absolute
`/path[:DisplayName]` value. Attach only the files the user reviewed or requested.

## Managing accounts

Use `email-client._rtx.interface.accounts-list`, `email-client._rtx.interface.accounts-add`,
`email-client._rtx.interface.accounts-update`, and `email-client._rtx.interface.accounts-remove`
for registry changes. Use `email-client._rtx.interface.accounts-set-password` for
app-password credentials; the secret must be supplied on stdin and never as an
argument or ordinary configuration value.

For shared Google setup or Gmail reauthorization, first invoke
`connect-google.interface.default` with the selected registered nickname. Its
deterministic coordinator creates a credential file, asks Gmail's owner to probe
the account profile, and stores the path only after verification. Treat only
`complete: true` as successful setup; report an incomplete Gmail result and retry
through connect-google with the same file.

Existing legacy per-account Gmail OAuth credentials remain runtime-readable
until a verified credential-file binding replaces them. Do not offer legacy
setup as a new route.

## Live checks

Use `email-client._rtx.interface.live-smoke` for explicit provider checks. The IMAP and
SMTP-auth modes authenticate without sending. The send-self mode sends a real message
to the account's address, so invoke it only when the user requested a delivery check.
