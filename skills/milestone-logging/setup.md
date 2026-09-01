# Milestone logging setup

1. Invoke `common.interface.famulus-paths-get@1` with positional argument `logging-path` and bind the returned path as `<logging_root>`.
2. Permanently add `<logging_root>` to the LLM client's writable directories.
