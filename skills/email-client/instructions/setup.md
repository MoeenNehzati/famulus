# Email Client setup

`connect-google.interface.setup` has already completed. Use the current
`run-markdown` flow id as `setup_flow_id` on every executable Famulus call below.

1. Call `connect-google._rtx.interface.shared-credential`; retain its file and email.
2. Call `email-client._rtx.interface.accounts-list`.
3. Reuse the unique exact-email match. If none exists, ask for a nickname, then call
   `email-client._rtx.interface.accounts-add --email <email> --nickname <nickname> --auth gmail-oauth`.
   If multiple matches exist, ask which nickname to use.
4. Call `email-client._rtx.interface.accounts-use-google-credential-file
   --nickname <nickname> --credential-file <credential_file> --home <current home>`;
   require `bound: true` and `verified: true`.
5. Run `email-client._rtx.interface.live-smoke -a <nickname> --imap --smtp-auth`;
   require both checks to succeed.

Settle only after steps 4 and 5 succeed.
Do not use `--send-self` or `accounts-setup-oauth`.
