---
name: email-client
description: >-
  Use when the user asks to access or manage email or a registered email account. Do not use when the primary request is inbox triage or shared Google authentication setup.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `email-client._rtx.interface.accounts-add` — Register a new account nickname. Gmail IMAP/SMTP settings are the default; pass explicit host/port flags for other providers. App-password auth is the default; use --auth gmail-oauth for Gmail OAuth.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--auth": "app-password|gmail-oauth", "--display-name": "name", "--email": "addr", "--imap-host": "H", "--imap-port": "P", "--nickname": "nick", "--smtp-host": "H", "--smtp-port": "P", "--starttls": true}, "positionals": [], "stdin": null}
    Required options: ["--email", "--nickname"]; positional arity: 0..0; stdin: forbidden
- `email-client._rtx.interface.accounts-list` — List registered account nicknames with their email/display name (no secrets).
  - Caller: `email-client`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `email-client._rtx.interface.accounts-remove` — Remove an account nickname from the registry; optionally purge its stored credentials too.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--nickname": "nick", "--purge-credentials": true}, "positionals": [], "stdin": null}
    Required options: ["--nickname"]; positional arity: 0..0; stdin: forbidden
- `email-client._rtx.interface.accounts-set-password` — Store the IMAP or SMTP credential for an account in the host credential store. The secret is read from stdin, never a CLI argument.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--nickname": "nick", "--purpose": "imap|smtp"}, "positionals": [], "stdin": null}
    Required options: ["--nickname", "--purpose"]; positional arity: 0..0; stdin: permitted
- `email-client._rtx.interface.accounts-update` — Update fields on an existing account nickname.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--auth": "app-password|gmail-oauth", "--display-name": "name", "--email": "addr", "--imap-host": "H", "--imap-port": "P", "--nickname": "nick", "--smtp-host": "H", "--smtp-port": "P"}, "positionals": [], "stdin": null}
    Required options: ["--nickname"]; positional arity: 0..0; stdin: forbidden
- `email-client._rtx.interface.live-smoke` — Run explicit live provider smoke checks for one account. --imap and --smtp-auth authenticate without sending; --send-self sends a test email to the account's own address.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `short-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--body": "text", "--imap": true, "--send-self": true, "--smtp-auth": true, "-a": "nickname"}, "positionals": [], "stdin": null}
    Required options: ["-a"]; positional arity: 0..0; stdin: forbidden
  - Alternative: `long-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account": "nickname", "--body": "text", "--imap": true, "--send-self": true, "--smtp-auth": true}, "positionals": [], "stdin": null}
    Required options: ["--account"]; positional arity: 0..0; stdin: forbidden
- `email-client._rtx.interface.mail-attachments` — List attachment metadata for one or more emails as JSON. Returns one record per requested UID with attachment entries containing filename, content_type, size_bytes, size_human, and disposition.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `short-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--folder": "inbox|sent|drafts|trash|all|<literal>", "-a": "nickname"}, "positionals": ["uid", "uid..."], "stdin": null}
    Required options: ["-a"]; positional arity: 1..unbounded; stdin: forbidden
  - Alternative: `long-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account": "nickname", "--folder": "inbox|sent|drafts|trash|all|<literal>"}, "positionals": ["uid", "uid..."], "stdin": null}
    Required options: ["--account"]; positional arity: 1..unbounded; stdin: forbidden
- `email-client._rtx.interface.mail-folders` — List IMAP folders for an account (JSON).
  - Caller: `email-client`
  - Version: 1
  - Alternative: `short-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"-a": "nickname"}, "positionals": [], "stdin": null}
    Required options: ["-a"]; positional arity: 0..0; stdin: forbidden
  - Alternative: `long-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account": "nickname"}, "positionals": [], "stdin": null}
    Required options: ["--account"]; positional arity: 0..0; stdin: forbidden
- `email-client._rtx.interface.mail-list` — List email envelopes for an account as JSON (fields: id, flags, subject, from, date, message_id). --folder accepts aliases inbox|sent|drafts|trash|all or any literal IMAP folder name (default inbox). --after narrows server-side by day (IMAP SINCE). Filters are key=value (exact, comma-separated=OR) or key~=value (regex, case-insensitive) over id/subject/from/date/message_id/flags, ANDed across distinct keys, applied client-side after fetch. Unfiltered + undated scans the whole folder (slow on large mailboxes) — pair filters with --after.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `short-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--after": "YYYY-MM-DD", "--folder": "inbox|sent|drafts|trash|all|<literal>", "--limit": "N", "-a": "nickname"}, "positionals": ["key=value|key~=value..."], "stdin": null}
    Required options: ["-a"]; positional arity: 0..unbounded; stdin: forbidden
  - Alternative: `long-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account": "nickname", "--after": "YYYY-MM-DD", "--folder": "inbox|sent|drafts|trash|all|<literal>", "--limit": "N"}, "positionals": ["key=value|key~=value..."], "stdin": null}
    Required options: ["--account"]; positional arity: 0..unbounded; stdin: forbidden
- `email-client._rtx.interface.mail-read` — Read one email by UID (the "id" field from mail-list). Prints Subject/From/To/ Date/Message-ID, then In-Reply-To/References only if the message is a reply, then an Attachments section (none or one line per attachment with filename, MIME type, and size), then a blank line, then the decoded body (text/plain preferred; falls back to HTML with tags stripped).
  - Caller: `email-client`
  - Version: 1
  - Alternative: `short-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--folder": "inbox|sent|drafts|trash|all|<literal>", "-a": "nickname"}, "positionals": ["uid"], "stdin": null}
    Required options: ["-a"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `long-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account": "nickname", "--folder": "inbox|sent|drafts|trash|all|<literal>"}, "positionals": ["uid"], "stdin": null}
    Required options: ["--account"]; positional arity: 1..1; stdin: forbidden
- `email-client._rtx.interface.mail-save-attachments` — Save attachments from one or more emails into a directory. Use --all to save every attachment, or repeat --name to save only selected filenames. Returns JSON describing the saved files.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `short-account-all`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--all": true, "--folder": "inbox|sent|drafts|trash|all|<literal>", "--out": "dir", "-a": "nickname"}, "positionals": ["uid", "uid..."], "stdin": null}
    Required options: ["--all", "--out", "-a"]; positional arity: 1..unbounded; stdin: forbidden
  - Alternative: `long-account-all`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account": "nickname", "--all": true, "--folder": "inbox|sent|drafts|trash|all|<literal>", "--out": "dir"}, "positionals": ["uid", "uid..."], "stdin": null}
    Required options: ["--account", "--all", "--out"]; positional arity: 1..unbounded; stdin: forbidden
  - Alternative: `short-account-name`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--folder": "inbox|sent|drafts|trash|all|<literal>", "--name": "filename...", "--out": "dir", "-a": "nickname"}, "positionals": ["uid", "uid..."], "stdin": null}
    Required options: ["--name", "--out", "-a"]; positional arity: 1..unbounded; stdin: forbidden
  - Alternative: `long-account-name`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account": "nickname", "--folder": "inbox|sent|drafts|trash|all|<literal>", "--name": "filename...", "--out": "dir"}, "positionals": ["uid", "uid..."], "stdin": null}
    Required options: ["--account", "--name", "--out"]; positional arity: 1..unbounded; stdin: forbidden
- `email-client._rtx.interface.send-email` — Send an email via SMTP; body comes from stdin.
  - Caller: `email-client`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--attach": "/path[:DisplayName]", "--from": "nickname", "--in-reply-to": "msg-id", "--references": "refs", "--subject": "subject", "--to": "addr..."}, "positionals": [], "stdin": null}
    Required options: ["--from", "--subject", "--to"]; positional arity: 0..0; stdin: permitted

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `bootstrap-dispatcher-runtime.interface.repair-selected-packages@1` — Repair the core or one caller-owned package declaration in the exact dispatcher runtime without MCP.
- `connect-google.interface.default@1` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
<!-- END BLUEPRINT INTERFACES -->
# Email

Before any Gmail, credential, IMAP, or SMTP action, use the host-loaded
`bootstrap-dispatcher-runtime.interface.repair-selected-packages` procedure for
feature `email-client` and its exact declaration `["keyring"]`. Require its
complete selected-Python preflight and byte-equal final fingerprint. On failure,
stop before OAuth, network, configuration, or other owner activity; never repair
another feature's declaration.

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
