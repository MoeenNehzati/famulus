---
name: bootstrap-dispatcher-runtime
description: >-
  Use when the Famulus dispatcher cannot start, or cannot run an interface, because its Python runtime is missing, too old, or lacks a declared package. Symptoms include "python: command not found", a Python older than 3.11, ModuleNotFoundError for mcp, yaml, or jsonschema, and a feature reporting that its own package is unavailable. Also use when that runtime must be rebuilt or a declared package added to it. Do not use for general Python installation.
tools:
  - python
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Used Interfaces: none
<!-- END BLUEPRINT INTERFACES -->
Skill: bootstrap-dispatcher-runtime

## What the dispatcher runtime is

The dispatcher is the server that runs every Famulus interface, and it launches each one with its own interpreter. That interpreter is this skill's whole subject: not Famulus as a product, and not the user's Python, but the one runtime the dispatcher server executes in.

One end state, and nothing in this skill matters except reaching it:

**A dedicated Python 3.11 or newer interpreter, at a known absolute path, with the required packages installed, and actually used by the dispatcher server every time the host launches it.**

Five requirements, each separately checkable:

1. **Right version, known path.** Python 3.11 or newer, at a known absolute path. A command name is not a path.
2. **Dedicated to the dispatcher.** Not the user's system interpreter. The user's systemwide Python may be changed, upgraded, or replaced at any time for reasons that have nothing to do with Famulus, and Famulus's packages must not be installed into an environment the user owns. What form this takes depends on the system: usually a virtual environment, but a fresh interpreter installed for Famulus alone satisfies it equally. The test is consequence, not shape: installing into it must not change anything else the user runs. The converse does not hold as strongly for every form, and do not claim it does: a virtual environment's interpreter is a symlink to the interpreter that built it, so removing or replacing that one breaks the environment, while a separately installed interpreter is immune. Where it is a virtual environment, `prefix` differing from `base_prefix` confirms it is the environment and not its base.
3. **Required packages installed in it.** The declared packages importable from that interpreter, and from no other. This is why a feature's own packages are in scope: the dispatcher runs that feature's interface with this interpreter, so anything the interface imports must be installed here. The core declaration in `mcp-core.json` is what the server needs to start at all; a caller-owned declaration is what one interface needs to run.
4. **The dispatcher actually runs on it.** When the host starts the dispatcher server, the interpreter that server executes in is this one. That is the requirement; no particular mechanism is. Read the current mechanism out of the plugin manifest and the session hook rather than assuming one: at the time of writing they start the server through the bare command `python`, which is met by making that command resolve to this interpreter *for the launched server* — not by changing what `python` means in the user's own shell. Checkable: the running server reports this absolute path as its own `sys.executable`.
5. **It keeps happening.** On the next launch, and after a host restart, and after a plugin upgrade. Anything that holds only inside your process, or only in the shell you are in now, has not met requirement 4; neither has an interpreter placed where a plugin upgrade will delete it.

## How to work

This is the MCP-independent prerequisite escape hatch. Use only shell-free process execution, each displayed array element as one argument. Never use a shell command string.

Each step states a requirement and, where the method is open, how it is usually met. The requirement binds. The usual method is a starting point you may leave when the requirement is better served. Displayed command arrays are not open: run them exactly as shown.

**The latitude never extends to these.** Adapting the method is not permission to:

- install Python, or have a package manager install it;
- change the PATH, aliases, or shims of the user's own shell, or write any user or host configuration file yourself; arranging what the launched dispatcher sees is in scope, changing what the user's own commands mean is not;
- substitute `uv`, a pip bootstrap, an externally-managed bypass, a user or root install target, an executable fallback, or any wrapper for the declared pip flow;
- install into any interpreter other than `${canonical_executable}`;
- report a requirement met without the evidence named for it in the closing section.

When a requirement cannot be met inside these limits, stop and tell the user exactly what you need from them. That is a correct outcome of this skill, not a failure of it.

## Which route you are in

Decide this before anything else. You are in the **repair route** only if a calling feature identified itself and supplied both its exact package declaration and an absolute interpreter. Everything else is the **core setup route**.

If you cannot tell, treat it as the repair route and do not prompt. The two mistakes are not symmetric: guessing repair when it was core stops early and asks nothing, while guessing core when it was repair hangs an unattended job waiting for an answer nobody will give.

**Core setup route.** The dispatcher has no usable runtime. Resolve the installed skill location supplied by the host to its owning plugin root, then read that root's `mcp-core.json`. Its `core_packages` array is the only package authority; reject a caller-supplied replacement. This route may ask the user questions.

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

**Requirement.** Requirement 2 above: an interpreter dedicated to the dispatcher, at a location that persists across plugin upgrades, writable without elevation, and confirmed by the user.

**Usually.** A virtual environment built by `${host_python}`, placed in the Famulus state root. Offer that as the default and accept an explicit path instead. On a system where a dedicated interpreter is better obtained another way, do that instead and satisfy the same requirement.

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

**Requirement.** Requirements 4 and 5 above: the dispatcher runs on `${canonical_executable}` when the host launches it, and does so again on later launches. The step is complete when a newly launched server reports `${canonical_executable}` as its own `sys.executable`, and not before.

**Usually.** Determine what the manifest and hook actually invoke, then arrange for that invocation to reach this interpreter, scoped to the launched server. Report the exact change and who must make it. Never edit the user's shell configuration yourself, never widen the change beyond the dispatcher, and never treat your own process environment as evidence: you cannot change the environment of the host that launched you.

## Close against the five requirements

Finish by walking the requirements in order and reporting each one: whether it is met, the evidence that it is met, and how it was achieved. The method was open, so the user cannot infer what you did — state it.

The core setup route reports all five. The repair route reports requirements 1 and 3 only, and says which environment it repaired; it did not choose the interpreter, publish it, or own where it lives.

Report a requirement as met only on evidence, never on the strength of an action you took. Having run a command is not evidence that its goal holds.

1. **Right version, known path.** Evidence: the fingerprint of `${canonical_executable}`. Report the absolute path and version.
2. **Dedicated to the dispatcher.** Evidence: the form you chose and why that form was right for this system, plus `prefix` and `base_prefix` where it is a virtual environment. Report what would and would not affect this interpreter now.
3. **Required packages installed in it.** Evidence: the pip report's `install` records, or that the declaration was already satisfied. Report the exact declared package set.
4. **The dispatcher actually runs on it.** You cannot confirm this from inside this session, and must not claim it: the server you are talking to was launched before you built anything. Report it as outstanding, with the exact change and who must make it.
5. **It keeps happening.** Met only once a newly launched server reports `${canonical_executable}` as its `sys.executable`. Report it as pending that restart, and say how the user can check it.

If any requirement is unmet, name it and stop there rather than reporting overall success.

When every applicable requirement is met, allow the host to retry its packaged `famulus_dispatcher` MCP declaration. Never start a private server path directly.

## Red flags

Each of these means you are about to break the skill. Stop.

| Thought | Reality |
|---|---|
| "Nothing is on PATH, I will just install Python." | Installing Python is outside the latitude. Report what you tried and ask for a path. |
| "No caller identified itself, so someone must be here to ask." | Absence of a caller is not presence of a person. If unsure, do not prompt. |
| "I printed the change, so it is set up." | Requirement 4 is met by the dispatcher running on it, not by your output. |
| "The venv is the requirement, so any venv will do." | Requirement 2 is dedication and survival. A venv where an upgrade deletes it fails. |
| "`python` works here, so it is fine." | A command name is not a path, and the user may repoint it tomorrow. |
