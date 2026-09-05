# Cloud Files setup

`connect-google.interface.setup` has already completed. Use the current
`run-markdown` flow id as `setup_flow_id` on every executable Famulus call below.

1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for
   owner `cloud-files` and exact declaration `["keyring"]`. Stop on failure.
2. Call `connect-google._rtx.interface.shared-credential`.
3. Call `cloud-files._rtx.interface.use-google-credential-file
   --credential-file <credential_file> --home <current home>`; require
   `bound: true` and `verified: true`.
4. Call `cloud-files._rtx.interface.write-config` with
   `--remote-llm-root assistant` and the current home.
5. Call `cloud-files._rtx.interface.ensure-assistant-root`; require
   `{"exists": true, "root": "assistant"}`.

Settle only after steps 3 and 5 succeed.
Do not run OAuth or create List Manager lists.
