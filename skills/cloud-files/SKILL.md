---
name: cloud-files
description: >-
  Use when the user or another skill needs to read from or write to the configured LLM root of a remote. Do not use for local files or remote paths outside that LLM root.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `cloud-files._rtx.interface.lists-delete` — Delete a file from cloud storage under the lists/ directory.
  - Caller: `cloud-files`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["lists/<path>"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `cloud-files._rtx.interface.lists-read` — Read a file from cloud storage under the lists/ directory.
  - Caller: `cloud-files`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["lists/<path>"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `cloud-files._rtx.interface.lists-write` — Write content (from stdin) to a file in cloud storage under the lists/ directory.
  - Caller: `cloud-files`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["lists/<path>"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: permitted
- `cloud-files._rtx.interface.plans-delete` — Delete a file from cloud storage under the plans/ directory.
  - Caller: `cloud-files`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["plans/<path>"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `cloud-files._rtx.interface.plans-read` — Read a file from cloud storage under the plans/ directory.
  - Caller: `cloud-files`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["plans/<path>"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `cloud-files._rtx.interface.plans-write` — Write content (from stdin) to a file in cloud storage under the plans/ directory.
  - Caller: `cloud-files`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["plans/<path>"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: permitted
- `cloud-files._rtx.interface.write-config` — Write ~/.config/cloud-files/config.json with the given remote LLM root. Owned by cloud-files.
  - Caller: `cloud-files`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--dry-run": true, "--home": "dir", "--remote-llm-root": "path"}, "positionals": [], "stdin": null}
    Required options: ["--home"]; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `connect-google.interface.default@1` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
- `setup-dispatcher-runtime.interface.repair-selected-packages@1` — Repair the core or one caller-owned package declaration in the exact dispatcher runtime without MCP.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: cloud-files

## 0. Boundary

This skill owns Google Drive transport. Other skills should call this skill's
scripts rather than speaking to the Drive API directly.

Before any Drive or credential action, use the host-loaded
`setup-dispatcher-runtime.interface.repair-selected-packages` procedure for
feature `cloud-files` and its exact declaration `["keyring"]`. Require its
complete selected-Python preflight and byte-equal final fingerprint. On failure,
stop before OAuth, network, configuration, or other owner activity; never repair
another feature's declaration.

Install-time config lives at `~/.config/cloud-files/config.json`.
Legacy OAuth credentials live at `~/.config/cloud-files/credentials.json`.

For shared Google setup or Drive reauthorization, first invoke
`connect-google.interface.default`. Its deterministic coordinator creates a
credential file, asks Drive's owner to probe live access, and stores the path
only after verification. Treat only `complete: true` as successful setup; report
an incomplete Drive result and retry through connect-google with the same file.

Existing legacy Drive credentials remain runtime-readable until a verified
credential-file binding replaces them. Do not offer legacy setup as a new route.

## 1. Preapproved LLM-root operations

Use `lists-read`, `lists-write`, `lists-delete` for routine list storage and `plans-read`, `plans-write`, `plans-delete` for plan storage within their respective directories.

Each interface takes a single positional path argument constrained to its directory prefix (`lists/` or `plans/`). Write interfaces read file content from stdin.

## 2. Separately prompted broader reads

A broader read from the Google Drive root is available via a script not registered as a dispatcher interface. It is intentionally not listed in `blueprint.yaml:suggested_permissions`.

If a script exits nonzero, report the visible error and do not infer remote
state beyond what the successful output established.
