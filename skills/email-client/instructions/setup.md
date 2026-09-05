# Email Client setup

`connect-google.interface.setup` has already completed. Use the current
`run-markdown` flow id as `setup_flow_id` on every executable Famulus call below.

1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for
   owner `email-client` and exact declaration `["keyring"]`. Stop on failure.
2. Call `connect-google._rtx.interface.shared-credential`; retain its file and email.
3. Call `email-client._rtx.interface.accounts-list`.
4. Reuse the unique exact-email match. If none exists, ask for a nickname, then call
   `email-client._rtx.interface.accounts-add --email <email> --nickname <nickname> --auth gmail-oauth`.
   If multiple matches exist, ask which nickname to use.
5. Call `email-client._rtx.interface.accounts-use-google-credential-file
   --nickname <nickname> --credential-file <credential_file> --home <current home>`;
   require `bound: true` and `verified: true`.
6. Run `email-client._rtx.interface.live-smoke -a <nickname> --imap --smtp-auth`;
   require both checks to succeed.

Settle only after steps 5 and 6 succeed.
Do not use `--send-self` or `accounts-setup-oauth`.
