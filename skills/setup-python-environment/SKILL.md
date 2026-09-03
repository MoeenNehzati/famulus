---
name: setup-python-environment
description: >-
  Use when Famulus MCP cannot start because no usable Python is available, its selected environment is missing, or a bundled core package is unavailable. Symptoms include "python: command not found", a Python older than 3.11, and ModuleNotFoundError for mcp, yaml, or jsonschema. Do not use for optional feature dependencies.
tools:
  - python
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Used Interfaces: none
<!-- END BLUEPRINT INTERFACES -->
Skill: setup-python-environment

## What Famulus needs

One end state, and nothing in this skill matters except reaching it:

**An absolute path to a dedicated Python 3.11 or newer interpreter with the required packages installed, available as the variable `FAMULUS_PYTHON` in the terminal environment that launches Famulus, and still there in later sessions.**

Five requirements, each separately checkable:

1. **Right version, known path.** Python 3.11 or newer, at a known absolute path. A command name is not a path.
2. **Dedicated to Famulus.** Not the user's system interpreter. The user's systemwide Python may be changed, upgraded, or replaced at any time for reasons that have nothing to do with Famulus, and Famulus's packages must not be installed into an environment the user owns. What form this takes depends on the system: usually a virtual environment, but a fresh interpreter installed for Famulus alone satisfies it equally. The test is consequence, not shape: installing into it must not change anything else the user runs, and nothing the user does to their own Python may change it. Where it is a virtual environment, `prefix` differing from `base_prefix` confirms that much.
3. **Required packages installed in it.** The declared packages importable from that interpreter, and from no other.
4. **Reachable as a variable.** Famulus's MCP declaration and its hooks invoke `${FAMULUS_PYTHON:-python}`. If the variable is unset they silently fall back to whatever `python` means on that machine, which is the failure this skill exists to end.
5. **Surviving.** Across shells, across host restarts, and across plugin upgrades. A value that lives only in your process, or only in the shell you are in now, has not met the requirement; neither has an environment placed where a plugin upgrade will delete it.

## How to work

This is the MCP-independent prerequisite escape hatch. Use only shell-free process execution, each displayed array element as one argument. Never use a shell command string.

Each step states a requirement and, where the method is open, how it is usually met. The requirement binds. The usual method is a starting point you may leave when the requirement is better served. Displayed command arrays are not open: run them exactly as shown.

**The latitude never extends to these.** Adapting the method is not permission to:

- install Python, or have a package manager install it;
- change PATH, create an alias or shim, or write any user or host configuration file;
- substitute `uv`, a pip bootstrap, an externally-managed bypass, a user or root install target, an executable fallback, or any wrapper for the declared pip flow;
- install into any interpreter other than `${canonical_executable}`;
- report a requirement met without the evidence named for it in the closing section.

When a requirement cannot be met inside these limits, stop and tell the user exactly what you need from them. That is a correct outcome of this skill, not a failure of it.

## Which route you are in

Decide this before anything else. You are in the **repair route** only if a calling feature identified itself and supplied both its exact package declaration and an absolute interpreter. Everything else is the **core setup route**.

If you cannot tell, treat it as the repair route and do not prompt. The two mistakes are not symmetric: guessing repair when it was core stops early and asks nothing, while guessing core when it was repair hangs an unattended job waiting for an answer nobody will give.

**Core setup route.** Famulus has no usable environment. Resolve the installed skill location supplied by the host to its owning plugin root, then read that root's `mcp-core.json`. Its `core_packages` array is the only package authority; reject a caller-supplied replacement. This route may ask the user questions.

**Owner-selected repair route.** Repair only the supplied declaration, in only the supplied environment. Do not locate, infer, widen, or combine declarations from any other feature. Its callers include scheduled and background runs with nobody available to answer, so this route must never prompt. If the supplied environment is missing or unusable, stop and report that the core setup route is required.

In both routes, call the selected declaration `${selected_packages}` and the interpreter being installed into `${canonical_executable}`. Do not inspect skill blueprints or any repository-wide dependency inventory. The repair route enters at "Preflight" with `${canonical_executable}` already supplied.

Everything below is the usual way to reach the end state. If the machine in front of you differs, keep the objective, adapt the method, and stay inside the limits above.

## Select an interpreter

*Core setup route only.*

**Requirement.** An interpreter of version 3.11 or newer, whose absolute path is known, capable of producing the dedicated interpreter of the next step, and confirmed by the user. This one may be the user's system Python: nothing is installed into it.

**Usually.** Fingerprint the interpreters on PATH and offer the newest as the default.

Fingerprint each candidate exactly, substituting the candidate command or path for `${candidate}`:

<!-- command:candidate-fingerprint -->
```json
["${candidate}", "-c", "import json,sys;print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'version':list(sys.version_info[:2])},separators=(',',':')))" ]
```

Require an absolute `executable`. Discovering no usable interpreter is an expected outcome, not a failure: report exactly what was tried and ask the user to install Python 3.11 or newer and give you the path to it. Do not install Python yourself.

Call the confirmed interpreter `${host_python}`.

## Provide a dedicated interpreter

*Core setup route only.*

**Requirement.** Requirement 2 above: an interpreter dedicated to Famulus, at a location that persists across plugin upgrades, writable without elevation, and confirmed by the user.

**Usually.** A virtual environment built by `${host_python}`, placed in the Famulus state root. Offer that as the default and accept an explicit path instead. On a system where a dedicated interpreter is better obtained another way, take that route and satisfy the same requirement.

Call the confirmed location `${venv_root}`, whatever form the dedicated interpreter takes. When that form is a virtual environment, create it with:

<!-- command:create-venv -->
```json
["${host_python}", "-m", "venv", "${venv_root}"]
```

**Verify.** The absolute path of the dedicated interpreter, confirmed to be the one just created. Run the fingerprint against it and require version 3.11 or newer and an absolute `executable` under `${venv_root}`. When it is a virtual environment, also require `prefix` to differ from `base_prefix`: that inequality is what proves you are holding the environment rather than the interpreter that built it.

When the form is not a virtual environment, verify the same way and additionally state what makes it dedicated, since `prefix` and `base_prefix` will not show it.

Retain the complete object as the selected fingerprint, and use this interpreter as `${canonical_executable}` from here on, substituting it as one token even when it contains spaces.

## Preflight

Run every check below before installation. Stop on the first failure and report it without mutation. The limits under "How to work" apply in full here.

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

## Install and verify

If the dry-run report has an empty `install` array, report that the environment already satisfies the declaration and skip installation. Otherwise run the same declared requirements without `--dry-run`. Pip's default satisfied-requirement behavior prevents reinstalling packages already present. Parse the report's `install` records to report exactly what changed.

<!-- command:pip-install -->
```json
["${canonical_executable}", "-m", "pip", "install", "--quiet", "--report", "-", "--disable-pip-version-check", "--no-input", "--no-cache-dir", "${selected_packages}"]
```

Rerun the fingerprint against `${canonical_executable}` and require byte-for-byte equality of all four fields with the selected fingerprint. A mismatch or version regression is a terminal failure: do not repair another environment or accept the MCP launch.

## Publish the interpreter

*Core setup route only.*

**Requirement.** Requirements 4 and 5 above: `${canonical_executable}` reachable as `FAMULUS_PYTHON` in the launching environment, and surviving later sessions. The step is complete when a newly launched host starts the server from `${canonical_executable}`, and not before.

**Usually.** Report the exact line for the user to add to their shell profile or host settings, then have them restart the host. Never write that configuration yourself, and never treat your own process environment as evidence: you cannot set a variable for the host that launched you.

## Close against the five requirements

Finish by walking the requirements in order and reporting each one: whether it is met, the evidence that it is met, and how it was achieved. The method was open, so the user cannot infer what you did — state it.

The core setup route reports all five. The repair route reports requirements 1 and 3 only, and says which environment it repaired; it did not choose the interpreter, publish it, or own where it lives.

Report a requirement as met only on evidence, never on the strength of an action you took. Having run a command is not evidence that its goal holds.

1. **Right version, known path.** Evidence: the fingerprint of `${canonical_executable}`. Report the absolute path and version.
2. **Dedicated to Famulus.** Evidence: the form you chose and why that form was right for this system, plus `prefix` and `base_prefix` where it is a virtual environment. Report what would and would not affect this interpreter now.
3. **Required packages installed in it.** Evidence: the pip report's `install` records, or that the declaration was already satisfied. Report the exact declared package set.
4. **Reachable as a variable.** You cannot confirm this from inside this session, and must not claim it. Report it as outstanding, with the exact line the user must add and where to add it.
5. **Surviving.** Met only when a newly launched host starts the server from `${canonical_executable}`. Report it as pending that restart, and say how the user will know it worked.

If any requirement is unmet, name it and stop there rather than reporting overall success.

When every applicable requirement is met, allow the host to retry its packaged `famulus` MCP declaration. Never start a private server path directly.

## Red flags

Each of these means you are about to break the skill. Stop.

| Thought | Reality |
|---|---|
| "Nothing is on PATH, I will just install Python." | Installing Python is outside the latitude. Report what you tried and ask for a path. |
| "No caller identified itself, so someone must be here to ask." | Absence of a caller is not presence of a person. If unsure, do not prompt. |
| "I printed the export line, so it is set up." | Requirement 4 is met by the launching environment, not by your output. |
| "The venv is the requirement, so any venv will do." | Requirement 2 is dedication and survival. A venv where an upgrade deletes it fails. |
| "`python` works here, so it is fine." | A command name is not a path, and the user may repoint it tomorrow. |
