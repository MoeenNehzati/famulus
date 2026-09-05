# Setting up Famulus

Famulus is set up progressively. The initial installation establishes the
common machinery required by every skill. Accounts, credentials, and recurring
jobs are configured later, when a workflow first needs them.

This division keeps the initial installation small. If you need only the
research and writing skills, you do not have to configure Google or a native
job scheduler. You can add those capabilities later without reinstalling the
system.

## 1. Install the core system

Famulus needs the host plugin plus a Python 3.11 or newer interpreter with a
functional `pip`.

Famulus runs every skill through the dispatcher server, and the dispatcher
executes each skill's code with its own interpreter. That interpreter is the
dispatcher runtime, and `bootstrap-dispatcher-runtime` is the skill that provides
it. Keeping it separate means the Python you use for your own work is never
modified, and upgrading or replacing that Python cannot break Famulus.

Two things follow from the dispatcher running everything with one interpreter.
The packages in `mcp-core.json` are what the server needs to start at all. A
skill's own dependency — `marker-pdf` for PDF conversion, say — is installed
into the same runtime, because that is the interpreter the dispatcher will run
that skill with. Both belong to `bootstrap-dispatcher-runtime`.

It also runs without MCP. That is what lets it repair the very thing MCP
needs in order to start.

What it does: finds an interpreter of a usable version, asks you to confirm it
and where the runtime should live, builds it, installs only the declared
packages, verifies the result, and reports what remains for you to do.

What it does not do: install Python, alias or shim any command, edit your shell
profile or host settings, bootstrap pip, use `uv`, or install anything into an
interpreter other than the dispatcher's. When it cannot finish inside those
limits it stops and tells you exactly what it needs from you, which for a
machine with no suitable Python means installing one and giving it the path.

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

### 1.2 Verify the dispatcher runtime

Confirm that `python` is Python 3.11 or newer and that `python -m pip` works.
Ask the assistant to use `bootstrap-dispatcher-runtime` when the command is missing
entirely, when Famulus reports a missing dependency, or when its shared tool is
unavailable, and review any requested package changes before approving them.

The plugin manifest and the session hook start Famulus through the bare command
`python`. The dispatcher runtime satisfies that on its own, because a virtual
environment provides a `python` of its own; what the skill has to arrange is
that the command reaches that interpreter when the host launches the server.
It reports the exact change and who must make it, and the change takes effect
on the next host start.

A previous successful setup does not by itself show that the shared tool is
reachable in the current host session. The
[dispatcher reference](officina/dispatcher.md) explains routing and
authorization, while the
[security and privacy boundary](security-and-privacy.md) explains what setup
state is retained.

### 1.3 Let workflows request their own setup

Installing Famulus does not connect accounts or enable recurring jobs. Setup is
demand-driven: when a requested workflow needs an unconfigured capability,
Famulus guides you through only that capability and its prerequisites, verifies
the result, and continues the original request.

If setup is interrupted or another setup operation is already in progress,
follow the reported recovery guidance rather than editing internal state.
Configure and operate persistent features through their owning skills, and use
the [Personal Assistance Quickstart](quickstarts/personal-assistance.md) and
[Automation Quickstart](quickstarts/automation.md) for normal workflow order.

Everything else is opt-in and demand-driven. A Famulus skill is an Officina
node. A public export named exactly `.interface.setup` is managed automatically
for Boolean whole-module readiness with no separate opt-in required.

A managed setup establishes only state owned by that node. Required setup of
another managed owner is declared with `setup_requires_setup_of`; the manager
runs that prerequisite first in the same finite flow. Production ownership is:

- `connect-google.interface.setup`: shared Google Desktop OAuth client and one
  selected Drive+Calendar+Gmail credential.
- `online-calendar.interface.setup`: Calendar-owned binding and live Calendar
  verification; requires Connect Google.
- `cloud-files.interface.setup`: Drive-owned binding, `assistant` root config,
  and root existence; requires Connect Google.
- `email-client.interface.setup`: Gmail account binding plus IMAP and SMTP-auth
  verification; requires Connect Google.
- `list-manager.interface.setup`: canonical `todo` and `triage` cloud lists;
  requires Cloud Files.
- `llm-wakeup._rtx.interface.setup`: feature-owned wakeup integration.

A Markdown setup is an active manager step while its instructions run. Ordinary
managed calls would therefore see `setup_busy`. For only the reviewed helper
calls named by the current Markdown production binding, the caller passes the
current flow id as `setup_flow_id` to `famulus_dispatcher.invoke`. MCP asks the
manager to authorize that exact `(flow, interface, version)` and then executes
the already-authorized dispatcher target. No nested setup flow is created, and
calls without `setup_flow_id` keep the normal preflight behavior.

Generic discussion about setup, installation, configuration, or teardown does
not activate the manager.

When an ordinary managed interface is requested, its generated gate and the
Famulus MCP preflight use the same lifecycle:

1. `status@1` classifies the target as `unmanaged`, `ready`, `setup_required`, or
   `setup_busy` without changing claims.
2. A pending target switches through `begin@1` to the prerequisite-first stack.
   `run-markdown@1` returns the exact declared instructions; `run-python@1` runs
   its fixed action and verifier. `settle@1` runs a verifier when one exists; a
   verifier-free Markdown step records completion when the caller settles only
   after its instructions succeeded.
3. After the flow reports ready, the caller rechecks `status@1`, calls
   `authorize@1`, and resumes the original request exactly once only when
   `resume_original` is true.

The caller retains original arguments and stdin during this switch. The manager
persists only continuation identity and setup receipts; helper-call arguments,
stdin, environment, verifier output, and dispatcher diagnostics are not stored
in the setup ledger.

You therefore do not configure the whole system in advance. Setup follows
actual use and asks only for state needed by the capability being reached.

### 1.4 Persisted state and live MCP readiness are different

The only persisted setup state is the manager's schema-versioned JSON ledger at
the single absolute `setup-status` path returned by
`common.interface.famulus-paths-get@1`. The manager creates and updates that
file through its confined atomic adapter; no public manager route accepts a
ledger path. An exact interface/version receipt means the declared verifier
passed. Its `required_by` roots record which managed workflows still claim that
state. MCP never reads or writes this ledger directly; setup-interface-manager
remains its sole authority.

The ledger does not say whether the shared Famulus MCP process is currently
reachable. Live `famulus_dispatcher.invoke` availability is the MCP readiness signal, and
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
`llm-wakeup` configures due-session delivery; Connect Google owns shared
authorization and service skills own service configuration. Each installs only
its own residual declared packages.

### 1.6 Tear down all managed setup state

Call `setup-interface-manager._rtx.interface.teardown-all@1` with no arguments
and no stdin to tear down every valid managed setup receipt in the selected
repository context. The manager plans dependents before prerequisites, runs
each receipt's declared teardown and verifier, and retains the canonical empty
ledger when the operation completes. Its response always has `original: null`
and `resume_original: false`.

An interrupted operation remains recoverable through `recover@1`; retry checks
the current verifier before rerunning, while cancel follows the persisted-flow
rules above. To tear down only one managed root and its unshared closure, keep
using `begin@1` with the `teardown` operation and that root's setup interface.

This route covers only valid managed setup receipts in the selected context.
It is not plugin/runtime uninstall or general purge, and it does not remove
unmanaged, host, credential, remote-authority, historical, or irreversible
effects. Each admitted owner separately proves effect reversal, repeat safety,
and recovery; manager verifier success alone proves none of those properties.
No production managed owner is currently admitted, so the accessible route is
a retained-empty-ledger no-op until an owner completes that admission contract.

## 2. Connect personal-assistant services

The personal-assistant subsystem is organized around persistent `todo` and
`triage` lists. Google Drive stores those lists and daily plans, Gmail supplies
messages from which `email-triage` can identify actions, and Google Calendar
supplies commitments for `daily-plan`. The
[Personal Assistance Quickstart](quickstarts/personal-assistance.md) explains
how these services feed the shared list-centered system and gives the
recommended starting sequence.

Ask the assistant:

```text
Connect Famulus to Google.
```

Famulus currently supports Google for these connections: Gmail, Google
Calendar, and Google Drive. Connect Google supplies shared authorization used by
the service-owned setups.
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
