# Online Calendar setup

`connect-google.interface.setup` has already completed. Use the current
`run-markdown` flow id as `setup_flow_id` on every executable Famulus call below.

1. Call `connect-google._rtx.interface.shared-credential`.
2. Call `online-calendar._rtx.interface.use-google-credential-file
   --credential-file <credential_file> --home <current home>`.
3. Require `bound: true` and `verified: true`.

Settle only after step 3 succeeds. The binder already performs the live Calendar probe.
Do not run OAuth or add another Calendar probe.
