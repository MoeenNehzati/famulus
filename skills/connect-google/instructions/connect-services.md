# Prepare Google authentication

Install or reuse the canonical Desktop OAuth client. Recommend all three
services—Drive, Calendar, and Gmail—and tell the user that any subset is valid.

## Client preparation

Check `connect-google._rtx.interface.client-status`. If it is valid, reuse it. If it is
missing or invalid and reports legacy candidates, ask before importing one with
`connect-google._rtx.interface.install-client`. If multiple candidates differ, ask the
user which one to import. When this route receives a local Desktop client JSON
path from `connect-google.interface.default` or
`connect-google.interface.create-client`, use
`connect-google._rtx.interface.install-client`. Otherwise return to
`connect-google.interface.default`; do not request a JSON path directly from
this route. Replacing a different canonical client requires explicit
confirmation. Never expose file contents or tokens.

## Combined authorization and service-owned binding

Before a Gmail-inclusive request, obtain the Gmail account nickname. Then call
`connect-google._rtx.interface.connect-services` once with every selected service.
It opens one consent screen for the combined scope union, creates one timestamped
credential file, and passes that exact absolute path directly to each granted
service's service-owned binder.

The authorization command prints the manual authorization URL and its callback
address before it tries to open a browser. If a local browser is available, let
the isolated helper open it; callback handling does not depend on that helper
succeeding. On a headless or remote machine, pass `--no-open-browser` and choose
an explicit `--callback-port`. Keep that same port at both ends of the SSH
forward, open the printed URL on the local machine, and leave the remote command
running until Google redirects to `http://127.0.0.1:<port>/`. Follow the exact
SSH command printed in the diagnostic stream rather than guessing the port.

The fixed coordinator route passes the returned `credential_file` to:

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

If Google rejects a Testing user during authorization, the Google Cloud project
administrator must add that exact account email under Test users. A Workspace
administrator policy can still block authorization.

@../personal-preferences/google-service-connection.md
