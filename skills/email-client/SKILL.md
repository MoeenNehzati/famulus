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
- `email-client.source.gateway -> email-client._rtx.interface.accounts-add@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-list@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-remove@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-set-password@1`
- `email-client.source.gateway -> email-client._rtx.interface.accounts-update@1`
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
the complete ancestor chain.

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
