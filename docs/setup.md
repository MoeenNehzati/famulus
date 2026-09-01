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

### 1.2 Verify the core runtime

Confirm that the selected `python` is Python 3.11 or newer and that
`python -m pip` can install into that environment. If Famulus reports a missing
core dependency or its shared tool is unavailable, ask the assistant to use
`setup-python-environment` and review any requested package changes before
approving them.

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
