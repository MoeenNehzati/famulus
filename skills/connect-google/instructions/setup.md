# Connect Google setup

Use the current `run-markdown` flow id as `setup_flow_id` on every executable
Famulus interface call below.

1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for
   owner `connect-google` and exact declaration `["keyring"]`. Stop on failure.
2. Call `connect-google._rtx.interface.shared-credential`. If it succeeds and
   grants Drive, Calendar, and Gmail, setup is complete.
3. Otherwise call `connect-google._rtx.interface.client-status`.
4. If the Desktop OAuth client is absent, follow the existing Connect Google
   client-creation instructions and install the downloaded JSON with
   `connect-google._rtx.interface.install-client`.
5. Call `connect-google._rtx.interface.authorize-services` with exactly
   `--services drive,calendar,gmail`. Require all three grants.
6. Call `connect-google._rtx.interface.select-shared-credential` with the returned
   `credential_file`.
7. Re-run `shared-credential` and require the same file plus all three grants.

Settle only after step 2 or step 7 succeeds.
Do not call Calendar, Cloud Files, or Email Client binders.
