---
name: setup-python-environment
description: >-
  Use when Famulus MCP cannot start because its selected Python or a bundled core package is unavailable. Do not use for optional feature dependencies or general Python installation.
tools:
  - python
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: assistant-installation, system-maintenance; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 1

Uses Interfaces: none

Public Interfaces:
- `setup-python-environment.interface.repair-selected-packages`
- `setup-python-environment.interface.setup`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `setup-python-environment.interface.repair-selected-packages` — Repair the core or one caller-owned package declaration in the exact selected Python environment without MCP.
- `setup-python-environment.interface.setup` — Repair the core or one caller-owned package declaration in the exact selected Python environment without MCP.
<!-- END BLUEPRINT INTERFACES -->
Skill: setup-python-environment

This is the MCP-independent prerequisite escape hatch. Use only shell-free process execution with each displayed array element as one argument. Never use a shell command string.

For the core setup route, resolve the installed skill location supplied by the host to its owning plugin root, then read that root's `mcp-core.json`. Its `core_packages` array is the only package authority; reject a caller-supplied replacement.

For the owner-selected repair route, require the calling feature to identify itself and supply its exact already-selected package declaration. Repair only that declaration. Do not locate, infer, widen, or combine declarations from any other feature.

In both routes, call the selected declaration `${selected_packages}` below. Do not inspect skill blueprints or any repository-wide dependency inventory.

## Select and fingerprint

Run the initial fingerprint exactly. A missing command or a version below 3.11 is a terminal prerequisite failure. Do not try another command, install Python, create or activate an environment, or bootstrap pip.

<!-- command:initial-fingerprint -->
```json
["python", "-c", "import json,sys;print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'version':list(sys.version_info[:2])},separators=(',',':')))" ]
```

Require an absolute `executable` and retain the complete object as the selected fingerprint. Substitute that value as one `${canonical_executable}` token below, even when it contains spaces.

## Complete non-mutating preflight

Run every check below before installation. Stop on the first failure and report it without mutation. Never add a user target, root target, externally-managed bypass, pip bootstrap, virtual environment, uv command, executable fallback, or wrapper.

<!-- command:pip-check -->
```json
["${canonical_executable}", "-m", "pip", "--version"]
```

The target check examines every normal selected-environment scheme destination without creating a probe file. A nonzero result is a definite refusal. A zero result means only that the selected Python's effective access check found every existing destination, or the nearest existing parent of a missing destination, writable; do not describe it as proof against unusual ACL, elevation, mount, quota, or race behavior. The complete pip dry run remains a separate required refusal boundary.

<!-- command:target-check -->
```json
["${canonical_executable}", "-c", "import os,sys,sysconfig;p={sysconfig.get_path(k) for k in ('purelib','platlib','scripts','data')};bad=[]\nfor x in p:\n q=x\n while not os.path.exists(q): q=os.path.dirname(q)\n if not os.access(q,os.W_OK): bad.append(x)\nprint('normal install target is not writable: '+', '.join(sorted(bad)) if bad else 'normal install target is writable');raise SystemExit(bool(bad))"]
```

Expand `${selected_packages}` to the route's exact ordered declaration, one process argument per item. This dry run is the complete pip preflight and must succeed before mutation; its failure includes externally-managed refusal and resolution failure.

<!-- command:pip-preflight -->
```json
["${canonical_executable}", "-m", "pip", "install", "--dry-run", "--quiet", "--report", "-", "--disable-pip-version-check", "--no-input", "--no-cache-dir", "${selected_packages}"]
```

## Repair and verify

If the dry-run report has an empty `install` array, report that the environment already satisfies the declaration and skip installation. Otherwise run the same declared requirements without `--dry-run`. Pip's default satisfied-requirement behavior prevents reinstalling packages already present. Parse the report's `install` records to report exactly what changed.

<!-- command:pip-install -->
```json
["${canonical_executable}", "-m", "pip", "install", "--quiet", "--report", "-", "--disable-pip-version-check", "--no-input", "--no-cache-dir", "${selected_packages}"]
```

Rerun the exact-command fingerprint and require Python 3.11 or newer plus byte-for-byte equality of all four fingerprint fields. A mismatch or version regression is a terminal failure: do not repair another environment or accept the MCP launch.

<!-- command:final-fingerprint -->
```json
["python", "-c", "import json,sys;print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'version':list(sys.version_info[:2])},separators=(',',':')))" ]
```

On success, report the stable fingerprint, the exact declared package set, and either the installed records or that nothing changed. Then allow the host to retry its packaged `famulus` MCP declaration; do not start a private server path directly.
