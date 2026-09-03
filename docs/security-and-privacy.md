# Security and privacy

This document describes the implemented Famulus trust boundary originally
audited at commit `777b8c03a103` on 2026-08-13 and delta-reviewed through
commit `a7d2fb28` on 2026-08-22. The first delta review, through `e74b8ad7`,
covered the credential-module relocation, process-local Drive access-token
caching and error reporting, the dedicated `background_run` agent and
unattended launch path, and the selected Python environment. The
second covered `51c06606`, which unified the installation contexts, and
`a68a6389`, which grants a managed assistant the state roots described under
[Roots granted to a managed assistant](#roots-granted-to-a-managed-assistant). It is an implementation inventory,
not a security certification. The limitations near the end are release work,
not features that are already mitigated.

For private vulnerability reporting, see the repository's
[security policy](../SECURITY.md).

## First-release support status

Google integrations and agent-driven recurring workflows are experimental in
the first public release. Their implemented permissions and limitations are
documented below, but they are not part of the hardened unattended-use surface.
Core installation does not connect a Google account or create recurring jobs.
Each requires a separate, explicit user request; review the requested services,
job definition, and schedule before enabling it.

Credential-free research, writing, handoff, and local document-review skills
do not require these experimental integrations. The final supported workflow
set will be fixed separately by the public release contract.

## Boundary in one paragraph

Famulus is a collection of LLM instructions and local programs. It does not add
a separate Famulus account or hosted runtime service. The audited source has no
Famulus-operated telemetry endpoint. It does, however, connect the user's host
agent to services the user selects: an LLM provider, Google Drive, Google
Calendar, Gmail or another IMAP/SMTP server, Open-Meteo, and the local operating
system. Data returned to Claude or Codex becomes part of that provider's model
session. Famulus skill text guides the model, but is not a security sandbox;
the host agent's permissions and the deterministic checks in Famulus runtime
code are the enforceable boundaries.

## Relevant skills and authority

The following skills form the security-relevant path for the advertised
personal-assistant workflows.

| Skill | Reads or receives | May change or disclose |
| --- | --- | --- |
| `connect-google` | A Google Desktop OAuth client and the Google identity returned during authorization | Stores the OAuth client secret and refresh-token references; coordinates grants for selected Google services |
| `cloud-files` | Files below the configured Drive root | Reads, creates, replaces, and deletes files in the `lists/` and `plans/` subdirectories exposed to LLM callers |
| `online-calendar` | Calendar lists, events, and availability | Creates, updates, moves, and deletes events within its public interface |
| `email-client` | Account metadata, messages, headers, bodies, and attachments | Sends mail, saves requested attachments locally, and adds, changes, or removes local account records |
| `email-triage` | New mail and the `todo` and `triage` lists | Classifies messages and writes list entries; it does not send mail or create calendar events |
| `list-manager` | Cloud-backed lists | Adds, changes, completes, rejects, and deletes list entries through `cloud-files` |
| `daily-plan` | Calendar data, weather, lists, and existing plans | Writes plans and plan metadata to Drive; list-changing requests can update master lists |
| `wrap-up` | Plans, lists, and session context | Updates plans and lists after a consolidated user review |
| `recurring-tasks` | Selected durable job definitions, run status, and captured job output | Reconciles one shared per-user scheduler set from explicitly enabled jobs; enabled jobs may invoke other skills without a new interactive prompt |
| `install-launchers` | Selected plugin and launcher configuration | Installs only feature-owned launcher commands and the unattended `background_run` prerequisite; it does not enable jobs |
| `get-weather` | A location supplied by the user or another workflow | Sends that location to Open-Meteo geocoding and forecast services; it uses no credential |
| `send-feedback` | A reviewed, redacted diagnostic report | Publishes the report as a public issue on the configured project, or sends it by email, only after preview and explicit approval; it refuses vulnerability reports and routes them to the private security channel |

Research, writing, handoff, and document-review skills can read or write the
local files the user places in scope. When file content is quoted, summarized,
or otherwise returned to the agent, that content also enters the active model
session. Those skills do not require a Famulus-managed remote credential by
default.

## Google permissions

`connect-google` lets the user authorize only the service subset they select,
but each implemented service scope is broad:

| Service | OAuth scope requested by the code | Effective grant |
| --- | --- | --- |
| Drive | `https://www.googleapis.com/auth/drive` | See, create, edit, and delete all files in the authorized Google Drive |
| Calendar | `https://www.googleapis.com/auth/calendar` | See, edit, share, and permanently delete calendars the account can access |
| Gmail | `https://mail.google.com/` | Read, compose, send, and permanently delete mail |
| Identity | `openid`, `email` | Identify the authorized Google account and obtain its email address |

These descriptions follow Google's current
[OAuth scope catalog](https://developers.google.com/identity/protocols/oauth2/scopes).
The grants are wider than the advertised runtime operations. In particular,
`cloud-files` deterministically rejects absolute paths, parent traversal, and
backslashes and exposes only `lists/` and `plans/` under the configured remote
root (default `assistant/`) to its LLM callers. That path restriction does not
reduce the underlying OAuth token's full-Drive authority. The Calendar and
Gmail grants are likewise broader than their public Famulus interfaces.

## Where credentials and data live

Famulus uses these platform roots for newer shared configuration and state:

| Platform | `<CONFIG>` | `<STATE>` |
| --- | --- | --- |
| Linux | `$XDG_CONFIG_HOME/famulus`, or `~/.config/famulus` | `$XDG_STATE_HOME/famulus`, or `~/.local/state/famulus` |
| macOS | `~/Library/Application Support/Famulus/config` | `~/Library/Application Support/Famulus/state` |
| Windows | `%LOCALAPPDATA%\Famulus\config` | `%LOCALAPPDATA%\Famulus\state` |

These are the `standard` context roots. A `development` context instead keeps
its config, data, state, assistant homes, jobs, and logs under the selected
checkout's `.famulus/` tree. This prevents contexts from sharing mutable state;
it does not sandbox the assistant from the rest of the operating-system
account. Backend ownership is durable in each context's `launchers.json`.
`ASSISTANT_DEFAULT` and `ASSISTANT_LOGS` are process-local overrides only.
`AI` and `FAMULUS_REPO_ROOT` are not installation selectors and must not be
persisted.

When a supported host starts the Famulus plugin, that host supplies a private
`plugin_data` directory to the Famulus MCP subprocess. Famulus stores milestone
logs below `<plugin_data>/milestones/`; MCP startup creates and confines that
directory and publishes it to the subprocess as `ASSISTANT_LOGS`. It does not
write a host-readiness record. The hidden setup manager separately owns its
schema-versioned receipt ledger at the one absolute `setup-status` path returned
by `common.interface.famulus-paths-get@1`. In a supported host context that
getter currently selects `<plugin_data>/setup/status.json`. The ledger records
verified managed setup state and active lifecycle flow, not whether the MCP
process is currently reachable; live `famulus_dispatcher.invoke` availability is the MCP
readiness signal. Claude and Codex roots remain separate, and no pre-existing
`~/.assistant-logs` content is migrated or merged.

### Roots granted to a managed assistant

Both hosts confine an agent to directories the user has approved, and a Famulus
skill that cannot write its own state root fails at the point of use. Selected
feature setup may write the state roots it needs into the host's own access
configuration: Claude's `permissions.additionalDirectories`, and the
equivalent Codex access roots.

Seven roots are granted, and they are state directories rather than working
directories: the assistant log root, the recurring-job config and state roots,
the email-triage state root, the `list-manager` lock and cache directories, and
the `llm-wakeup` state root. `resolve_assistant_access_roots` derives them from
the selected context, never from process overrides, so `ASSISTANT_LOGS` and its
siblings cannot widen the grant.

What the grant refuses is the more useful half. A resolved root that overlaps
the credential root, either assistant home, or feature-owned
state root raises `AssistantAccessBoundaryError` and the apply stops. The
assistant is therefore granted the state it writes and denied the state that
would let it rewrite its own installation or read the Google credentials.

This narrows nothing that the host granted independently. It is an addition to
the agent's writable set, not a sandbox around it, and a `development` context
grants the same roots under the checkout's `.famulus/` tree instead.

`~` below means the account running the host agent. `<PLUGIN>` means the
installed Famulus plugin directory or checkout. Plugin directories are
replaceable and should not contain durable private state, although current
paths listed below still do.

### Credential inventory

| Credential or record | Implemented location | Contains |
| --- | --- | --- |
| Canonical Google OAuth client record | `<CONFIG>/connect-google/client.json` | Client ID and a secret reference, not the raw client secret |
| Canonical Google authorization descriptors | `<CONFIG>/connect-google/credentials/*.json` | Google subject/email, granted scopes, and secret references, not raw tokens |
| Shared Google credential registry | `<CONFIG>/connect-google/credentials.json` | Credential IDs, account metadata, scopes, and secret references |
| Google client secret | Python `keyring`, service `Famulus:connect-google`, username `oauth-client:<client-id>:client-secret` | Raw OAuth client secret |
| Google refresh token for a descriptor | Python `keyring`, service `Famulus:connect-google`, username `credential-file:<descriptor-stem>:refresh-token` | Raw refresh token |
| Google refresh token for a registry record | Python `keyring`, service `Famulus:connect-google`, username referenced by the registry, normally `google-refresh:<uuid>` (older records may use `<credential-id>:refresh-token`) | Raw refresh token |
| Drive binding | `~/.config/cloud-files/config.json` | Remote root, timeout, and credential descriptor or ID reference |
| Calendar binding | `~/.config/online-calendar/config.json` | Credential descriptor or ID reference |
| Email account registry | `~/.config/email-client/accounts.json` | Email address, display name, server settings, auth mode, and credential reference; no new-route password or refresh token |
| IMAP/SMTP app passwords | Python `keyring`, service `Famulus:email-client`, usernames `<nickname>:imap` and `<nickname>:smtp` | Raw app passwords |
| Legacy email OAuth secrets | Python `keyring`, service `Famulus:email-client`, usernames `<nickname>:oauth:client-secret` and `<nickname>:oauth:refresh-token` | Raw client secret and refresh token |

The shared secret API delegates to the installed Python `keyring` backend. It
rejects fail/null and non-positive-priority backends, but the current code does
not prove that every accepted backend is an encrypted native credential store.
Users should inspect their selected keyring backend when that distinction
matters.

Canonical client and descriptor files are written with owner-only file modes
on POSIX and reject symbolic-link destinations. Access tokens are normally
short-lived values obtained during a request and are not persisted by the
canonical path. The `online-calendar` executable does have an explicit `token` mode
that prints an access token to standard output; its skill instructions reserve
that mode for explicitly requested direct API access.

### Legacy credential files

The runtime still accepts older files at:

- `~/.config/cloud-files/client.json`
- `~/.config/cloud-files/credentials.json`
- `~/.config/online-calendar/client.json`
- `~/.config/online-calendar/credentials.json`

Those files may contain raw OAuth client secrets and refresh tokens. They are
migration compatibility, not the recommended setup. Their continued support is
a known hardening gap.

### User data and operational state

| Data | Implemented location or recipient |
| --- | --- |
| Lists and plans | Google Drive below the configured remote root, normally `assistant/lists/` and `assistant/plans/` |
| Calendar events | The authorized Google Calendar account; selected event data can enter the model session |
| Email | The registered IMAP/SMTP account; selected headers and bodies can enter the model session |
| Saved attachments | A user-selected local directory; filenames are reduced to a basename before writing |
| Email-triage state | `<STATE>/email-triage/` |
| Plugin milestone logs | `<plugin_data>/milestones/` for the active Claude or Codex plugin instance |
| Managed setup ledger | The absolute `setup-status` path returned by `common.interface.famulus-paths-get@1`; currently `<plugin_data>/setup/status.json` in a supported host context. Contains verified interface/version receipts, root claims, and at most one active lifecycle flow; it is not an MCP-readiness record. |
| Email-triage classification log | `<PLUGIN>/skills/email-triage/_rtx/triage.log`; includes account, message ID, sender, subject, decision, and reason |
| List-manager category cache | `<PLUGIN>/skills/list-manager/_rtx/tmp/categories.<list>.yaml`; contains list category paths and cache counters, not list entries |
| Daily-plan run status | `<PLUGIN>/skills/daily-plan/state/status.json` |
| Recurring-task definitions | The selected durable recurring configuration root; all selected contexts share one native per-user scheduler set, owned by the last successful scheduling operation |
| Recurring-task output and outcome records | The selected durable recurring state root; command output is captured and logs rotate after 5 MiB with one prior copy retained |
| Weather queries | Location and forecast parameters sent to Open-Meteo |
| Feedback reports | Email recipient configured by the project, only after the user reviews and approves the report |

Standard recurring state lives below platform Famulus roots. Development
recurring state lives below the selected checkout's `.famulus/` tree. Package
cache replacement therefore does not own or discard either context's mutable
jobs or history.

## What reaches Claude or Codex

Famulus does not send the whole mailbox, calendar, Drive, or filesystem to the
model automatically. It sends the content selected by a requested or scheduled
workflow when that content is returned through the host agent's tool boundary.
That can include:

- email sender, subject, date, flags, body, and attachment metadata;
- calendar titles, times, attendees, locations, and descriptions;
- list entries, daily plans, and selected Drive file content;
- weather location and forecast data;
- local document or repository content placed in scope; and
- command output, errors, and diagnostics surfaced to the agent.

The model provider's account type and data controls determine retention and
model-improvement use; Famulus does not override them. Review the applicable
[OpenAI Codex data controls](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
or [Anthropic Claude data-use policy](https://privacy.claude.com/en/articles/10023580-is-my-data-used-for-model-training)
before processing sensitive data. For consumer accounts, both providers expose
settings that can affect whether coding-session content is used for model
improvement; commercial terms differ.

## Authorization and confirmation boundaries

The implemented public workflows use the following boundaries:

- Read-only calendar, email, Drive-list, Drive-plan, weather, and user-selected
  file operations may proceed when the user asks for the corresponding result.
- Calendar creation, update, or deletion may proceed when the user supplied the
  required details. Adding attendees requires confirmation because it sends
  invitations. Deleting an event with attendees or far in the future requires
  confirmation. Moving an event requires an explicit move request. Calendar
  writes are read back for verification.
- Email sending is a consequential external action. `email-client` accepts
  recipients, subject, and attachment paths as command arguments and the body
  on standard input. The skill must act only on the user's send request; there
  is not yet a repository-wide deterministic confirmation gate for all sends.
- `send-feedback` requires a redacted preview and explicit approval before it
  sends anything. Its default route publishes the report where anyone can read
  it, so the preview names the route, the destination project, and the account
  that would file the report. It refuses to prepare a public report for a
  security vulnerability or for evidence that cannot be redacted, and directs
  those to the private route in [SECURITY.md](../SECURITY.md) instead. When the
  issue-filing command is unavailable, it only prepares a submission link and
  nothing is published until the user submits it themselves.
- List and plan mutations may proceed when the user requests them. `wrap-up`
  gathers proposed changes for one consolidated review before applying them.
- A scheduled job is authorized when the user enables its recurring-task
  definition. Later runs can read data and perform that job's declared writes
  without a new prompt. Disabling the job removes that continuing authority.
- Google authorization and replacing an account binding require interactive
  account selection or the skill's explicit account-change confirmation path.
- The Officina dispatcher restricts declared cross-skill machine calls, but it
  is not an operating-system sandbox. The host agent still controls filesystem,
  network, subprocess, and approval policy.

### Unattended recurring execution

Selected feature setup may prepare non-interactive agent support, but does not
create or enable a scheduled job. A job runs only after the user explicitly
asks `recurring-tasks` to create or enable it.

Once enabled, a recurring agent invocation uses Claude's
`bypassPermissions` mode or Codex's
`--dangerously-bypass-approvals-and-sandbox` mode. This prevents an unattended
process from stalling on an approval prompt, but it also means there is no
interactive approval or Codex sandbox boundary during that run. The effective
boundary is therefore the operating-system account, host-agent credentials,
job definition, selected skill interface, working directory, and the
`background_run` instructions. Review all of those before enabling a job;
disable the job before changing its skill, permissions, or connected account.

### External content is untrusted

Email bodies, calendar descriptions, Drive files, web pages, attachments, and
documents can contain instructions written by someone other than the user.
They must be treated as data, not as authorization to send messages, delete or
move data, reveal credentials, run subprocesses, change permissions, or widen
the task.

This rule is not yet enforced consistently by deterministic code. In
particular, `email-triage` places selected email bodies in model context and can
write list entries based on them; its current instructions do not contain a
complete prompt-injection isolation rule. Its declared interface cannot send
email or create calendar events, which limits impact, but it is not a complete
untrusted-content boundary. The corresponding release-readiness checklist item
therefore remains open.

## Disconnect and manual cleanup

These are distinct operations:

1. **Disable automation through its feature owner.** Use `recurring-tasks` to
   disable selected jobs and remove its selected scheduler registration. This
   preserves credentials and remote data.
2. **Revoke Google access.** Remove the OAuth connection in the Google Account
   [third-party connections page](https://myaccount.google.com/connections).
   Revocation ends the token's server-side authority. Deleting local files
   alone does not revoke it.
3. **Remove local service bindings and secrets.** Removing an email account
   with `--purge-credentials` clears the email client's IMAP/SMTP and legacy
   Gmail OAuth keys. It does not clear a shared `connect-google` client secret
   or descriptor refresh token used by another service. Shared Google secrets
   and descriptors currently require reference-aware manual cleanup.
4. **Decide what to do with remote data.** Removing credentials does not delete
   lists, plans, mail, attachments already saved locally, calendar events, or
   data already retained by a model provider. Delete those separately only
   after identifying their exact owner and desired retention.
5. **Treat old-install cleanup as manual historical work.** Famulus has no
   general uninstaller, managed runtime, or purge command. A non-executing
   checklist may identify old `dispatcher`, `invoke-skill`, or wakeup
   launchers, `famulus-env`, `current.json`, and old PATH blocks, but those are
   not current setup surfaces or an automated deletion procedure.

No local cleanup action revokes Google access or deletes remote data.

## Telemetry and third parties

No first-party analytics or telemetry client was found in the audited Famulus
source. Local logs and status records still exist as listed above. Network
requests occur only as part of selected functionality or installation:

- the chosen Claude or Codex provider processes model-session content;
- Google processes OAuth, Drive, Calendar, Gmail, and identity requests;
- a registered non-Gmail mail provider processes IMAP/SMTP requests;
- Open-Meteo receives weather locations and query parameters;
- GitHub or another configured source may serve installation files; and
- the configured feedback mailbox receives reports the user approves.

Those services, the host agent, the operating system, and the selected Python
keyring backend have their own logging and retention behavior.

## Known release limitations

The audit identified these unresolved items:

1. Google scopes are broader than the public runtime operations.
2. `online-calendar` has a token-to-standard-output mode.
3. Server-side revocation, local secret cleanup, and remote-data deletion are
   separate operations.
4. Legacy plaintext OAuth credential files remain readable.
5. The accepted Python keyring backend is not guaranteed by current code to be
   an encrypted native credential store.
6. Untrusted external content is not isolated from model instructions by a
   complete deterministic authorization layer.
7. Some email-triage, list-manager, and daily-plan private working copies or
   logs still have paths inside replaceable package content; recurring mutable
   jobs and logs have moved to context-owned state.
8. Email recipients, subjects, and attachment paths are present in local
   process arguments; only the email body is passed through standard input.
9. Enabled recurring jobs deliberately run without interactive host approvals
   or the Codex sandbox; their safety depends on the pre-reviewed job and skill
   boundary rather than per-action confirmation.

These limitations are accepted only for explicitly experimental integrations.
The affected workflow must receive the relevant hardening before it is
promoted as supported for unattended use.
