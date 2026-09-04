---
name: install-launchers
description: Install or repair an explicit subset of the optional assistant, collab, coauthor, and tw launchers.
tools:
  - python
---


<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `install-launchers._rtx.interface.agent-launchers` — Install or repair only an explicit interactive launcher subset using caller-selected Python and plugin values.
  - Caller: `install-launchers`
  - Version: 1
  - Alternative: `setup`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--agents": "LIST", "--bin-dir": "DIR", "--canonical-python": "FILE", "--claude-home": "DIR", "--codex-home": "DIR", "--default-llm": "claude|codex", "--dry-run": true, "--home": "DIR", "--mode": "development|plugin", "--plugin-root": "DIR"}, "positionals": [], "stdin": null}
    Required options: ["--canonical-python", "--plugin-root"]; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `bootstrap-dispatcher-runtime.interface.repair-selected-packages@1` — Repair the core or one caller-owned package declaration in the exact dispatcher runtime without MCP.
<!-- END BLUEPRINT INTERFACES -->
Skill: install-launchers

Require an explicit subset of `assistant`, `collab`, `coauthor`, and `tw`. If the
request names none, list those choices or ask; do not mutate anything and do not
default to installing all launchers.

Use the host-loaded
`bootstrap-dispatcher-runtime.interface.repair-selected-packages` procedure for
feature `install-launchers` and the exact empty selected package declaration
`[]`. Run its initial literal-`python` fingerprint, pip availability check,
target writability check, and final literal-`python` fingerprint. For this exact
empty declaration, treat package install and install dry run as successful
no-ops: do not invoke either `pip install` command or fabricate a pip report.
Retain the complete byte-equal fingerprint and its canonical absolute
executable.

Resolve the current selected plugin root from the host-loaded location of this
skill. Invoke `install-launchers._rtx.interface.agent-launchers` with only that
selected plugin root, the canonical executable, and the explicit launcher
subset. Do not fingerprint again, choose another Python, widen the selection,
or install a dependency owned by another feature.

Rerunning repairs only the selected launchers and refreshes their captured
plugin root. After a plugin-cache update, rerun `install-launchers`; there is no
generic updater.
