# Online Calendar setup

`connect-google.interface.setup` has already completed. Use the current
`run-markdown` flow id as `setup_flow_id` on every executable Famulus call below.

1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for
   owner `online-calendar` and exact declaration `["keyring"]`. Stop on failure.
2. Call `connect-google._rtx.interface.shared-credential`.
3. Call `online-calendar._rtx.interface.use-google-credential-file
   --credential-file <credential_file> --home <current home>`.
4. Require `bound: true` and `verified: true`.

Settle only after step 4 succeeds. The binder already performs the live Calendar probe.
Do not run OAuth or add another Calendar probe.
