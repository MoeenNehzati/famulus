# Milestone logging setup

1. Invoke `common.interface.famulus-paths-get@1` with positional argument `logging-path` and require exactly one absolute returned path.
2. Create that directory and any missing parents idempotently. Preserve all existing contents.
3. Do not read or write the legacy `setup-status` path, change MCP configuration, or use MCP availability as setup evidence.
4. Setup is complete only when the independent setup verifier returns exactly `{"set_up": true}`. A false result means the directory is still not set up.
