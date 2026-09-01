# Milestone logging setup

1. Invoke `common.interface.famulus-paths-get@1` with positional argument `logging-path` and bind the returned path as `<logging_root>`.
2. Invoke the same interface with positional argument `setup-status` and bind the returned path as `<status_file>`.
3. Read `<status_file>` and require one JSON object with `schema_version` equal to `1`, `status` equal to `ready`, and `host` equal to the supported plugin host declared for this process.
4. Continue only when `<logging_root>` is absolute and the readiness record is valid. The plugin MCP process already projects this path through `ASSISTANT_LOGS`; do not add a client writable-directory setting.
