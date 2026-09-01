# Setting up Famulus

Famulus is set up progressively. The initial installation establishes the
common machinery required by every skill. Accounts, credentials, and recurring
jobs are configured later, when a workflow first needs them.

This division keeps the initial installation small. If you need only the
research and writing skills, you do not have to configure Google or a native
job scheduler. You can add those capabilities later without reinstalling the
system.

## 1. Install the core system

Famulus uses the host plugin plus the exact environment reached by `python`.
That command must resolve to Python 3.11 or newer with a functional
`python -m pip`. Famulus does not install or alias Python, create an environment,
bootstrap pip, use `uv`, or create a managed runtime.

### 1.1 Install the host plugin

In Claude Code, the plugin can be installed from inside a session:

```text
/plugin marketplace add MoeenNehzati/famulus
/plugin install famulus@nullkit
```

It can also be installed from a terminal:

```bash
claude plugin marketplace add MoeenNehzati/famulus
claude plugin install famulus@nullkit
```

For Codex, use:

```bash
codex plugin marketplace add MoeenNehzati/famulus --json
codex plugin add famulus@nullkit --json
```

Restart the host after installing the plugin so that it discovers the new
skills.

### 1.2 Prepare the selected Python

Confirm that `python -m pip` can install packages into the selected environment.
If the packaged `famulus` MCP server reports a missing core dependency, ask the
host to use `setup-python-environment`. It validates that exact environment
before repairing only the declared core packages; missing pip, an externally
managed environment, or a non-writable target fails before package mutation.

The session-start hook acts as the core setup sentinel: it instructs the
assistant to check that `famulus.invoke` is available. If the tool is absent or
an invocation fails, the assistant reports that Famulus MCP is unavailable and
asks permission before following `setup-python-environment`. Node-specific
setup remains demand-driven through the setup interfaces described below.

The plugin declares one shared `famulus` MCP server. Executable skill
interfaces use its generated JSON invocation projection; instruction interfaces
are read and followed directly. The [dispatcher reference](officina/dispatcher.md)
explains routing and authorization.

### 1.3 Setup follows the requested workflow

The session-start hook bootstraps only the common runtime. It checks live
`famulus.invoke` availability and, with permission, can direct the assistant to
the unmanaged `setup-python-environment` workflow. That bootstrap does not
create a managed setup receipt and is never controlled by the setup manager.

Everything else is opt-in and demand-driven. A Famulus skill is an Officina
node, but merely exporting a setup interface does not make it managed. Its
blueprint must declare `setup_management` for Boolean whole-node state, with
fixed setup and teardown interfaces and read-only verifiers. It may declare
prerequisites through `setup_requires_setup_of`. Generic discussion about
setup, installation, configuration, or teardown does not activate the manager.

When an ordinary managed interface is requested, its generated gate and the
Famulus MCP preflight use the same lifecycle:

1. `setup-interface-manager._rtx.interface.status@1` classifies the target as
   `unmanaged`, `ready`, `setup_required`, or `setup_busy` without changing
   claims.
2. A pending target switches through
   `setup-interface-manager._rtx.interface.begin@1` to the returned
   prerequisite-first stack. `run-markdown@1` returns the exact declared
   instructions and `settle@1` runs its verifier; `run-python@1` runs the fixed
   action and verifier together. Only verifier success records a receipt.
3. After the flow reports ready, the caller rechecks `status@1`, calls
   `authorize@1`, and resumes the original request exactly once only when
   `resume_original` is true.

The caller retains the original arguments and stdin throughout this switch.
The manager stores only caller, interface, and version as continuation identity.
For a Python setup action it accepts one JSON object on stdin and projects only
arguments declared by that fixed managed binding; undeclared, missing, or
malformed input is refused. Arguments, stdin, environment, verifier output, and
dispatcher diagnostics are never persisted in the ledger or echoed in manager
responses.

You therefore do not configure the whole system in advance. Setup follows
actual use: a capability asks for its accounts, credentials, or platform
integration only when a requested workflow reaches it.

### 1.4 Persisted state and live MCP readiness are different

The only persisted setup state is the manager's schema-versioned JSON ledger at
the single absolute `setup-status` path returned by
`common.interface.famulus-paths-get@1`. The manager creates and updates that
file through its confined atomic adapter; no public manager route accepts a
ledger path. An exact interface/version receipt means the declared verifier
passed. Its `required_by` roots record which managed workflows still claim that
state.

The ledger does not say whether the shared Famulus MCP process is currently
reachable. Live `famulus.invoke` availability is the MCP readiness signal, and
MCP startup does not overwrite the setup ledger. A malformed or unsupported
ledger fails closed instead of being treated as ready.

Missing or stale prerequisites rerun only the affected dependent suffix. For a
`leaf -> parent -> root` setup order, a stale parent reruns parent and root while
retaining the exact leaf. While no flow is active,
`setup-interface-manager._rtx.interface.invalidate@1` removes a selected
receipt and every managed dependent receipt; the next use rebuilds the current
closure from verified state.

### 1.5 Teardown and recovery

Exact managed setup and teardown calls are redirected to `begin@1`; they never
launch a lifecycle interface directly. Teardown walks the setup order in
reverse. It runs an external teardown only after the last root releases a
receipt. If another root still claims a shared dependency, the manager removes
only the current root's claim. A teardown verifier must return success before
the receipt is removed, and teardown never resumes an ordinary request.

Only one ledger-mutating flow can be active. A second action receives
`setup_busy` with the existing flow identity. After an interruption,
`setup-interface-manager._rtx.interface.recover@1` accepts only `retry` or
`cancel`: retry checks the verifier before rerunning the exact current step;
cancel removes claims added by completed steps and clears the flow without
guessing whether the interrupted external action finished. Invalidation is
refused until the active flow is recovered or cancelled.

Persistent features remain independently owned: `install-launchers` configures
interactive launchers, `recurring-tasks` configures recurring work,
`llm-wakeup` configures due-session delivery, and `connect-google` configures
Google services. Each installs only its own residual declared packages.

## 2. Connect personal-assistant services

The personal-assistant skills can use email, calendar, and remote storage.
Together these connections allow Famulus to triage incoming email, plan a day
around calendar events, and persist todo and triage lists through cloud-backed
storage. The [Personal Assistance Quickstart](quickstarts/personal-assistance.md)
describes the workflows and the recommended starting sequence.

Ask the assistant:

```text
Connect Famulus to Google.
```

Famulus currently supports Google for these connections: Gmail, Google
Calendar, and Google Drive. The `connect-google` skill coordinates their setup.
Google authorization is backed by a Google Cloud project whose OAuth
configuration makes the requested permissions explicit. Setup uses the
downloaded JSON file for a Desktop OAuth client.

There are two routes to that client:

1. The Famulus developer maintains an experimental Google Cloud project. An
   OAuth project in Testing mode permits at most 100 manually listed test
   users, and their refresh tokens expire after seven days, so they must
   authorize again. If the developer has personally added your Google account
   and provided the Desktop OAuth client JSON, give `connect-google` the local
   path to that file when asked.
2. Otherwise, `connect-google` guides you through creating a Google Cloud
   project and Desktop OAuth client. Download that project's JSON and give the
   skill its local path instead.

The JSON contains a client secret. It must remain local: do not paste its
contents into a model session, commit it to a repository, or attach it to an
issue. The OAuth client identifies the application but grants no account access
until you complete Google's authorization flow. Review the
[security and privacy boundary](security-and-privacy.md) before granting access.

## 3. Schedule recurring skill runs

### 3.1 Set up and test the job

The `recurring-tasks` skill turns an already configured skill workflow into a
repeated job. Typical uses include triaging email and preparing a daily plan.
Configure and test the underlying skill interactively before enabling its
recurring job.

The [Automation Quickstart](quickstarts/automation.md) explains the unattended
execution boundary and the normal setup and verification sequence.

### 3.2 Diagnose platform problems

Recurring scheduling is implemented for Linux, macOS, and Windows, but it has
been tested most thoroughly on Linux. Platform-specific scheduler setup,
triggering, and health checks may therefore be less polished on macOS and
Windows.

If a recurring job fails, preserve the exact error and ask the assistant to
diagnose and repair it. Also ask the assistant to use `send-feedback` so that
the developer can learn from the failure. That skill prepares a redacted report
and sends it only after you review and approve the complete message.
