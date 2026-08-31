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

### 1.3 Setup is demand-driven

A Famulus skill is an Officina node. Its blueprint may expose a setup interface
that points to the instructions for determining and establishing the node's
required state. A setup interface may also declare other setup interfaces as
prerequisites through `setup_requires_setup_of`.

The generated skill contract lists these setup interfaces in prerequisite-first
order. When setup is needed, the assistant follows that contract: it establishes
the prerequisites before setting up the node that depends on them, then returns
to the requested workflow.

You therefore do not configure the whole system in advance. Setup follows
actual use: a capability asks for its accounts, credentials, or platform
integration only when a requested workflow reaches it.

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
