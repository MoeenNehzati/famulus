---
name: email-client
description: Use when reading, listing, searching, or sending email for the user across any nickname registered in the account registry.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: communications, external-integrations; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `email-client.source.gateway -> connect-google.interface.default@1`

Public Interfaces:
- `email-client.interface.accounts-add`
- `email-client.interface.accounts-list`
- `email-client.interface.accounts-remove`
- `email-client.interface.accounts-set-password`
- `email-client.interface.accounts-setup-oauth`
- `email-client.interface.accounts-update`
- `email-client.interface.accounts-use-google-credential`
- `email-client.interface.default`
- `email-client.interface.live-smoke`
- `email-client.interface.mail-attachments`
- `email-client.interface.mail-folders`
- `email-client.interface.mail-list`
- `email-client.interface.mail-read`
- `email-client.interface.mail-save-attachments`
- `email-client.interface.send-email`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `email-client.interface.accounts-add` — Register a new account nickname. Gmail IMAP/SMTP settings are the default; pass explicit host/port flags for other providers. App-password auth is the default; use --auth gmail-oauth for Gmail OAuth.
  - `dispatcher --caller-skill email-client email-client.interface.accounts-add --nickname <nick> --email <addr> [--display-name <name>] [--imap-host H] [--imap-port P] [--smtp-host H] [--smtp-port P] [--starttls] [--auth app-password|gmail-oauth]`
- `email-client.interface.accounts-list` — List registered account nicknames with their email/display name (no secrets).
  - `dispatcher --caller-skill email-client email-client.interface.accounts-list`
- `email-client.interface.accounts-remove` — Remove an account nickname from the registry; optionally purge its stored credentials too.
  - `dispatcher --caller-skill email-client email-client.interface.accounts-remove --nickname <nick> [--purge-credentials]`
- `email-client.interface.accounts-set-password` — Store the IMAP or SMTP credential for an account in the host credential store. The secret is read from stdin, never a CLI argument.
  - `dispatcher --caller-skill email-client email-client.interface.accounts-set-password --nickname <nick> --purpose imap|smtp`
- `email-client.interface.accounts-setup-oauth` — Complete Gmail OAuth setup for an account using a Google desktop OAuth client JSON file. Prints the authorization URL and completion status, stores refresh-token and client-secret keys, and persists Gmail OAuth metadata in accounts.json.
  - `dispatcher --caller-skill email-client email-client.interface.accounts-setup-oauth --nickname <nick> --client-config <path> [--no-open-browser]`
- `email-client.interface.accounts-update` — Update fields on an existing account nickname.
  - `dispatcher --caller-skill email-client email-client.interface.accounts-update --nickname <nick> [--email <addr>] [--display-name <name>] [--imap-host H] [--imap-port P] [--smtp-host H] [--smtp-port P] [--auth app-password|gmail-oauth]`
- `email-client.interface.accounts-use-google-credential` — Bind one account nickname to a shared connect-google credential_id after validating it carries Gmail scope, storing only the opaque identifier (never the client secret or refresh token) on that account's own registry record. Other accounts and other fields on this account are untouched. The pre-existing per-account Gmail OAuth path (accounts-setup-oauth) remains the unchanged fallback for accounts that have not adopted the shared credential.
  - `dispatcher --caller-skill email-client email-client.interface.accounts-use-google-credential --nickname <nick> --credential-id <id> --home <dir>`
- `email-client.interface.live-smoke` — Run explicit live provider smoke checks for one account. --imap and --smtp-auth authenticate without sending; --send-self sends a test email to the account's own address.
  - `dispatcher --caller-skill email-client email-client.interface.live-smoke -a <nickname> [--imap] [--smtp-auth] [--send-self] [--body <text>]`
- `email-client.interface.mail-attachments` — List attachment metadata for one or more emails as JSON. Returns one record per requested UID with attachment entries containing filename, content_type, size_bytes, size_human, and disposition.
  - `dispatcher --caller-skill email-client email-client.interface.mail-attachments -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid> [<uid> ...]`
- `email-client.interface.mail-folders` — List IMAP folders for an account (JSON).
  - `dispatcher --caller-skill email-client email-client.interface.mail-folders -a <nickname>`
- `email-client.interface.mail-list` — List email envelopes for an account as JSON (fields: id, flags, subject, from, date, message_id). --folder accepts aliases inbox|sent|drafts|trash|all or any literal IMAP folder name (default inbox). --after narrows server-side by day (IMAP SINCE). Filters are key=value (exact, comma-separated=OR) or key~=value (regex, case-insensitive) over id/subject/from/date/message_id/flags, ANDed across distinct keys, applied client-side after fetch. Unfiltered + undated scans the whole folder (slow on large mailboxes) — pair filters with --after.
  - `dispatcher --caller-skill email-client email-client.interface.mail-list -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] [--after YYYY-MM-DD] [key=value|key~=value ...] [--limit N]`
- `email-client.interface.mail-read` — Read one email by UID (the "id" field from mail-list). Prints Subject/From/To/ Date/Message-ID, then In-Reply-To/References only if the message is a reply, then an Attachments section (none or one line per attachment with filename, MIME type, and size), then a blank line, then the decoded body (text/plain preferred; falls back to HTML with tags stripped).
  - `dispatcher --caller-skill email-client email-client.interface.mail-read -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid>`
- `email-client.interface.mail-save-attachments` — Save attachments from one or more emails into a directory. Use --all to save every attachment, or repeat --name to save only selected filenames. Returns JSON describing the saved files.
  - `dispatcher --caller-skill email-client email-client.interface.mail-save-attachments -a <nickname> [--folder inbox|sent|drafts|trash|all|<literal>] <uid> [<uid> ...] --out <dir> (--all | --name <filename> [--name <filename> ...])`
- `email-client.interface.send-email` — Send an email via SMTP; body comes from stdin.
  - `dispatcher --caller-skill email-client email-client.interface.send-email --from <nickname> --to <addr> [--to <addr>...] --subject <subject> [--attach /path[:DisplayName]] [--in-reply-to <msg-id>] [--references <refs>]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `email-client.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# Email

Accounts are selected by registered nickname. If the nickname is unknown, invoke
`email-client.interface.accounts-list` and use its JSON result; do not infer an
account from an address or provider.

## Reading and attachments

Use `email-client.interface.mail-list` to obtain message UIDs and envelope metadata.
Prefer an `after` date when filtering a large folder because filtering without a date
may scan the entire folder. Folder aliases and the filter grammar are defined by the
interface contract.

Use `email-client.interface.mail-read` for a human-readable message. Use
`email-client.interface.mail-attachments` when structured attachment metadata is
needed, and `email-client.interface.mail-save-attachments` only when files should be
written. Use `email-client.interface.mail-folders` when the target folder is unknown.

## Sending and replies

Use `email-client.interface.send-email`; provide the body on stdin. For a reply, first
obtain the original `message_id` from `email-client.interface.mail-list`, pass it as
`in-reply-to`, and use a reply subject. Override `references` only when the caller has
the complete ancestor chain.

## Managing accounts

Use `email-client.interface.accounts-list`, `email-client.interface.accounts-add`,
`email-client.interface.accounts-update`, and `email-client.interface.accounts-remove`
for registry changes. Use `email-client.interface.accounts-set-password` for
app-password credentials; the secret must be supplied on stdin and never as an
argument or ordinary configuration value.

For shared Google setup or Gmail reauthorization, first invoke
`connect-google.interface.default`. Inspect its combined-authorization result. When
Gmail was granted, bind the returned opaque `credential_id` to the selected registered
nickname with `email-client.interface.accounts-use-google-credential`, using the same
credential-registry home.

Use `email-client.interface.accounts-setup-oauth` only as the pre-existing
per-account fallback for an account that has not adopted a shared credential. It
requires a Google Desktop-client configuration path and performs a separate
per-account authorization flow.

## Live checks

Use `email-client.interface.live-smoke` for explicit provider checks. The IMAP and
SMTP-auth modes authenticate without sending. The send-self mode sends a real message
to the account's address, so invoke it only when the user requested a delivery check.
