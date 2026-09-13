# Connect Google setup

Use the current `run-markdown` flow id as `setup_flow_id` on every executable
Famulus interface call below.

1. Call `connect-google._rtx.interface.shared-credential`. If it succeeds and
   grants Drive, Calendar, and Gmail, setup is complete.
2. Otherwise call `connect-google._rtx.interface.client-status`.
3. If the Desktop OAuth client is absent, follow the existing Connect Google
   client-creation instructions and install the downloaded JSON with
   `connect-google._rtx.interface.install-client`.
4. Call `connect-google._rtx.interface.authorize-services` with exactly
   `--services drive,calendar,gmail`. Require all three grants.
5. Call `connect-google._rtx.interface.select-shared-credential` with the returned
   `credential_file`.
6. Re-run `shared-credential` and require the same file plus all three grants.

Settle only after step 1 or step 6 succeeds.
Do not call Calendar, Cloud Files, or Email Client binders.
