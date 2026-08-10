# Prepare Google authentication

Install or reuse the canonical Desktop OAuth client. Recommend all three
services—Drive, Calendar, and Gmail—and tell the user that any subset is valid.

## Client preparation

Check `connect-google._rtx.interface.client-status`. If it is valid, reuse it. If it is
missing or invalid and reports legacy candidates, ask before importing one with
`connect-google._rtx.interface.install-client`. If multiple candidates differ, ask the
user which one to import. Otherwise obtain a local Desktop client JSON path and
use `connect-google._rtx.interface.install-client`. Replacing a different canonical
client requires explicit confirmation. Never expose file contents or tokens.

## Combined authorization and service-owned binding

Before a Gmail-inclusive request, obtain the Gmail account nickname. Then call
`connect-google._rtx.interface.connect-services` once with every selected service.
It opens one consent screen for the combined scope union, creates one timestamped
credential file, and passes that exact absolute path directly to each granted
service's service-owned binder.

The fixed coordinator route is:

- Drive to the Drive storage service's credential-file binder.
- Calendar to the Calendar service's credential-file binder.
- Gmail to the email service's account credential-file binder.

Treat only `complete: true` as success. If a grant is denied or one service
binder fails, report the per-service result and retain the returned credential
file path. Retry incomplete granted services through
`connect-google._rtx.interface.bind-credential-file`, passing the same file;
do not open a second consent flow merely to retry binding.

The coordinator never copies access or refresh tokens between services. Each
service owns account-change confirmation, persists only its configured
credential-file path, verifies a live API call before success, and reports a
stable machine result. An installer may omit a Gmail nickname; in that case
Gmail remains incomplete while other granted services still bind.

If Google rejects a Testing user during a later service-owned authorization,
the project owner must add that exact account email under Test users. A
Workspace administrator policy can still block authorization.

@../personal-preferences/google-service-connection.md
