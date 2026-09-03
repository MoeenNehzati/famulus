# Famulus Python venv bootstrap

Design for replacing the hard systemwide `python` requirement with a
Famulus-owned virtual environment that `setup-python-environment` builds and
that the MCP server, hooks, and launchers all run from.

## Problem

Famulus requires a command literally named `python` on PATH:

- `mcp.json:6` and `mcp-core.json:4` launch the server with `"command": "python"`.
- `hooks/hooks.json:9` runs `python -c "..."`.

On any machine that ships only `python3` (Debian, Ubuntu, macOS), the MCP
server never starts and the SessionStart hook never runs. Nothing recovers
from this automatically:

- `setup-python-environment` treats the missing command as terminal by design
  ("A missing command or a version below 3.11 is a terminal prerequisite
  failure. Do not try another command, install Python, create or activate an
  environment, or bootstrap pip.").
- `setup-interface-manager` cannot help, because every one of its interfaces is
  invoked through the very MCP server that failed to start.

`README.md:83,119` and `docs/setup.md:14-16,47-50` state the requirement but
never say what to do when it is unmet, so the user is left to work it out.

A second problem compounds it: `setup-python-environment` installs
`mcp-core.json`'s `core_packages` into whatever environment `python` resolves
to. That is a shared, user-owned environment. A user who needs a different
systemwide Python, or who does not want Famulus's dependencies in their global
site-packages, has no supported option.

## Goals

The end state, as the skill now states it: an absolute path to a dedicated
Python 3.11 or newer interpreter with the required packages installed,
available as `FAMULUS_PYTHON` in the terminal environment that launches
Famulus, and still there in later sessions. That decomposes into five
separately checkable requirements: right version and known path; dedicated to
Famulus; required packages installed in it; reachable as a variable; surviving
across shells, host restarts, and plugin upgrades.

- A machine with any Python >= 3.11, under any command name, can run Famulus.
- Famulus's dependencies live in an interpreter Famulus owns, isolated from
  whatever the user's systemwide `python` is or later becomes. A virtual
  environment is the usual form, not the requirement: a fresh interpreter
  installed for Famulus alone satisfies it equally.
- When no usable interpreter exists, the user is guided to a fix rather than
  handed an error.
- Existing installations that already work keep working with no user action.

## Non-goals

- Famulus does not install Python. It discovers, or asks.
- No automatic mutation of the user's shell configuration or host settings.
  The skill reports exactly what to add; the user applies it.
- No change to which packages are installed. `mcp-core.json`'s `core_packages`
  remains the only package authority.

## Design

### The venv

`setup-python-environment` gains a build step. Its core setup route:

1. **Discover** candidate interpreters >= 3.11 (`python`, `python3`,
   versioned names such as `python3.13`, `py -3` on Windows), fingerprinting
   each with the existing fingerprint contract.
2. **Confirm** the interpreter with the user, offering the best discovered
   candidate as the default and accepting an explicit path as an override.
   If nothing is discovered, report that plainly and ask the user to install
   Python and supply the path. This replaces the current terminal failure.
3. **Confirm** the venv location, defaulting to the Famulus state root and
   offering the plugin-relative location as an alternative.
4. **Build** the venv and install `core_packages` into it, reusing the existing
   pip preflight (target writability, `--dry-run` report) unchanged.
5. **Report** the resulting interpreter path, the exact persistence line the
   user should add, and the requirement to restart the host.

### Where the venv lives

Three candidates were evaluated:

| Location | Derivable without MCP | Survives plugin update | Nameable in a static manifest |
|---|---|---|---|
| `${PLUGIN_ROOT}/venv` | yes | no, the marketplace cache is replaced | yes |
| `${PLUGIN_DATA}/venv` | no, host-computed and only reaches Famulus via `mcp.json`'s `env` | yes | only if expansion works in `command` |
| Famulus state root | yes, via the same projection as `resolve_famulus_paths` | yes | no |

The Famulus state root is the default: it is Famulus-owned, persists across
plugin upgrades, and the skill can compute it without a running server, exactly
as `src/officina/common/famulus_paths/__init__.py` already does. Because a
static manifest cannot spell that path, the location is communicated by a
pointer rather than hardcoded.

### The pointer

The venv's absolute interpreter path is published as an environment variable
(working name `FAMULUS_PYTHON`) that the host has in its environment at launch.
Consumers become:

- `mcp.json`: `"command": "${FAMULUS_PYTHON:-python}"`
- `hooks/hooks.json`: `"${FAMULUS_PYTHON:-python}" -c "..."`, expanded by the
  shell that already runs the hook command string
- `--canonical-python` for `install-launchers`, `recurring-tasks`, and
  `llm-wakeup` becomes that same path

The `:-python` default is what preserves existing installations: with the
variable unset, every consumer behaves exactly as it does today.

An absolute path in a variable also sidesteps the platform difference a static
path cannot express: a venv exposes `bin/python` on POSIX and
`Scripts/python.exe` on Windows, and Windows will not execute an extensionless
absolute path.

This aligns the whole system on one interpreter. `install-launchers`,
`recurring-tasks`, and `llm-wakeup` already pin a `--canonical-python`
absolute path into what they generate
(`skills/install-launchers/_rtx/_agent_launchers.py:66`); this design changes
only where that value comes from.

### Instruction style

Each step in the skill is authored in three parts, and they are not of equal
weight. Precision belongs in the objective, not in the procedure.

1. **The requirement, in as much detail as it takes.** What must be true when
   the step is done, stated precisely enough to be checked, plus why the rest
   of the system needs it. This is where effort goes. An agent that knows
   exactly what it is aiming at, and what depends on hitting it, can construct
   a method; an agent given a method but a vague target cannot recognise
   success, and cannot tell a legitimate deviation from a failure.
2. **How it is usually achieved, briefly.** A starting point so the agent does
   not invent one from nothing. This is a hint, not a specification: the agent
   is expected to depart from it whenever the requirement is better served,
   and departing is not an error.
3. **Nothing beyond that.** No enumeration of platforms, package managers, or
   failure cases. Enumerating in advance is both incomplete and brittle, and
   it buries the objective under cases that may never occur.

The skill's existing rigidity is retained wherever determinism matters, and
that is a separate axis from the above. Fingerprint commands, the pip
availability check, target writability, the `--dry-run` preflight, and the
final verification stay as literal argv arrays with no agent latitude, because
their value is that they are byte-identical every run. Discovery, venv
placement, and persistence are authored in the three-part form. Applied:

**Discovery.** *Requirement:* an interpreter of version >= 3.11 whose absolute
path is known, that can create a virtual environment, and that the user has
confirmed. *Usually:* fingerprint `python`, `python3`, and versioned
names on PATH and offer the newest. Finding none is an expected outcome, not a
failure: report it and ask the user to install Python and supply the path.

**Placement.** *Requirement:* a directory that Famulus owns, that persists
across plugin upgrades, that is writable without elevation, and that the user
has confirmed. *Usually:* the Famulus state root.

**Persistence.** *Requirement:* the venv interpreter's absolute path is present
in the environment of the process that launches Famulus, on every future
session, under the name `FAMULUS_PYTHON`, because `mcp.json` and
`hooks/hooks.json` invoke it as `${FAMULUS_PYTHON:-python}`. The step is done
when a newly launched host starts the server from that interpreter.
*Usually:* report the exact line for the user to add to
their shell profile or host settings, then restart. The skill never writes it
itself.

### Route split

`setup-python-environment` exports two interfaces
(`skills/setup-python-environment/blueprint.yaml`):

- `setup-python-environment.interface.setup` — the core route. May prompt.
- `setup-python-environment.interface.repair-selected-packages` — called by
  other features, including unattended ones. Must never prompt; on a missing
  or unusable environment it fails with a message naming the core route.

`install-launchers` calls the repair route, and `recurring-tasks` and
background runs execute with nobody available to answer, so prompting there
would hang a job. The blueprint already separates these exports, so this is a
behavioral split along an existing seam rather than a new boundary.

## Open risk, to resolve first

**Does a plugin's `mcp.json` expand an arbitrary `${FAMULUS_PYTHON}` in
`command`, on Claude Code and on Codex?**

What is verified:

- Claude Code expands `${VAR}` and `${VAR:-default}` in `.mcp.json`'s
  `command`, `args`, and `env` (code.claude.com/docs/en/mcp).
- `${PLUGIN_ROOT}` demonstrably expands in a plugin manifest's `args`, since
  `mcp.json:7` relies on it in production.

What is not verified: that a plugin manifest under the agent-plugins schema
takes the same expansion path for `command`, on either host. The published
schema documents `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` only for `cwd` and defines
no interpolation for `command`.

The first implementation step is a spike: set `mcp.json`'s `command` to
`${FAMULUS_PYTHON:-python}` in a dev checkout, set the variable, restart, and
confirm the server starts under both backends. If expansion does not happen,
the venv must move to a plugin-relative location and Windows path shape has to
be solved separately, which changes the design materially.

## Consequences to handle

- **Verification contract.** The current final check requires byte-for-byte
  equality of `sys.executable`, `sys.prefix`, `sys.base_prefix`, and version
  against the initial fingerprint. Inside a venv, `prefix` and `base_prefix`
  differ by definition. The check must be rewritten to assert the venv's own
  identity, not rejected-as-drift.
- **Restart required.** Publishing the variable only takes effect on the next
  host launch. The flow ends by telling the user to restart, and verifies on
  the following run.
- **Docs.** `README.md` step 2 and `docs/setup.md` currently assert the
  `python` requirement with no remedy. Both need rewriting around "any Python
  >= 3.11, discovered or supplied" and the venv flow.
- **Tests.** `tests/test_setup_python_environment_skill.py` encodes the current
  contract, and `tests/test_officina_setup_requirements.py`,
  `tests/test_install_launchers_skill.py`, and
  `tests/test_setup_interface_manager_coverage.py` touch the surrounding
  behavior. All need review against the new routes.

## Rejected alternatives

- **Point `FAMULUS_PYTHON` at a system interpreter, no venv.** Solves the
  naming problem but not isolation: a user who later needs a different
  systemwide Python is back to the original problem, and Famulus's packages
  still sit in a shared environment.
- **Auto-install Python via the system package manager.** Platform-specific,
  generally needs privileges the agent should not assume, and has the largest
  blast radius of any option considered.
- **Create a `python` shim on PATH.** Mutates the user's environment to fix
  Famulus's own requirement, and PATH for a host spawned from a GUI is not
  guaranteed to include the shim directory.
