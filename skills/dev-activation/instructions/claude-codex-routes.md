# Claude and Codex development routes

Request a Claude launch from `dev-activation.interface.default` for the absolute checkout and any extra arguments. The returned exact-`python` route selects the checkout through `--plugin-dir`; do not pass the root Codex-format `.mcp.json` as Claude project configuration.

Use the same command with `--host codex` for Codex. The route starts Codex with the checkout as its explicit working root so the checkout plugin metadata owns skills and MCP discovery.
