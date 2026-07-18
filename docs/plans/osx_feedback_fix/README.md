# macOS Installation Feedback Remediation Umbrella Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or, when the user explicitly authorizes delegation, `superpowers:subagent-driven-development`. Execute one linked subplan at a time and stop at its review gate.

**Goal:** Turn the July 17 macOS installation failures into a cross-platform installer architecture, accepted on Linux, macOS, and Windows with an additional native macOS LaunchAgent smoke for the reported path.

**Document profile:** Internal implementation-program umbrella for Famulus maintainers and fresh agentic implementers. This file owns source traceability, settled decisions, cross-plan contracts, dependency order, and integrated acceptance. The linked files own task-level tests and edits.

**Source:** `assistant-tools-installation-feedback.md`, dated 2026-07-17, from a macOS Apple Silicon plugin-mode installation using Apple Command Line Tools Python 3.9. The portable implementation record is the traceability table below; execution must not depend on the reporter's Downloads path.

**Architecture:** A dependency-light bootstrap installs pinned `uv` and builds a versioned Famulus runtime containing managed CPython 3.11, dependencies, and the complete installed plugin payload. A small atomic `current.json` pointer activates a fully verified candidate; launchers, hooks, schedulers, update, rollback, and uninstall resolve through that pointer. Mutable configuration and state live outside installed/plugin trees. A script-owned wizard defaults to plugin mode and drives all deterministic setup. Dispatcher is the only cross-skill machine boundary. Google authorization, recurring automation, and downstream workflow repairs are independent subplans with explicit shared contracts.

**Tech Stack:** POSIX shell, PowerShell, `uv` 0.11.29, CPython 3.11, Python stdlib, Famulus dispatcher/Python machine interfaces, host secret stores, Google OAuth 2.0/OpenID Connect, launchd, systemd user services, Windows Task Scheduler, PyYAML, pytest, GitHub Actions.

## Program-wide constraints

- Product bootstrap never invokes or mutates the user's ambient `python` or `python3`. Repository test commands may use the developer/CI Python and do not define the product bootstrap contract.
- Preferred managed Python is `3.11`; supported managed Python is `>=3.11,<3.12`; `uv` is pinned to `0.11.29` in `install-info.toml` and mirrored only where a pre-Python wrapper cannot parse TOML.
- Plugin mode is the recommended/default mode. Developer mode requires explicit selection and a live checkout path.
- The LLM may explain and request permission to launch checked-in bootstrap code. Scripts own mode, launcher, backend, Google-service, Gmail nickname, recurring-automation, validation, confirmation, recovery, and final reporting.
- Interactive and non-interactive routes produce the same typed `InstallSelections`. Non-interactive mode never reads stdin.
- All post-bootstrap cross-skill calls use declared dispatcher interfaces. No installer imports or executes another skill's private runtime.
- Plugin-mode mutable state and public commands never live under a plugin cache, Documents, Desktop, or Downloads.
- Installed plugin/runtime trees are immutable after activation. Mutable configuration lives under the Famulus config root and mutable operational state lives under the Famulus state root on every supported platform.
- Install and update build and smoke-test a new versioned release without modifying the active release. Activation is one atomic replacement of `current.json`; failure leaves the old pointer and release usable, and the immediately previous release remains available for rollback.
- Plugin-mode launcher installation does not write under `$CODEX_HOME` or `$CLAUDE_HOME`; developer-mode host integration is explicit and rerunnable.
- User-home installation never recommends or uses `sudo`.
- Credentials, tokens, authorization URLs, raw argv, stdin, environment values, target output, and tracebacks never enter dispatcher/install JSON or logs.
- Any skill/blueprint change uses `skill-maker`; generated contract blocks and dependency artifacts are regenerated, not hand-edited.
- `skill-maker` writes both the canonical runtime-dependency manifest and the byte-identical installer bootstrap copy in one operation; any blueprint dependency change that stages one must stage the other.
- The worktree must be inspected at execution time. Session-specific dirty paths are not durable plan constraints; overlapping user changes require a merge decision before editing.

## Contract ownership

The umbrella records ownership and dependency order; it does not restate implementation contracts. When two subplans interact, the producing subplan below is authoritative and consumers link to it.

| Contract | Authoritative owner | Consumers |
|---|---|---|
| Managed Python policy, platform paths, versioned releases, `current.json`, and stable resolver | [Installer runtime Task 1](01-installer-runtime.md#task-1-add-canonical-install-metadata-the-pinned-uv-bootstrap-and-the-managed-runtime-model) | recurring, Google configuration, acceptance |
| Installer selections, defaults, and interactive/non-interactive parity | [Installer runtime Task 2](01-installer-runtime.md#task-2-move-installation-decisions-from-the-llm-into-a-script-owned-wizard) | Google and recurring onboarding |
| Final install result, retry reporting, and exit codes | [Installer runtime Task 3](01-installer-runtime.md#task-3-execute-the-script-owned-installation-plan-inside-the-managed-environment) | Google and recurring onboarding, acceptance |
| Manifest v2, activation recovery, legacy migration, and uninstall/purge ownership | [Installer runtime Slice 4C](01-installer-runtime.md#slice-4c-migrate-manifest-v2-and-make-uninstall-reference-safe) | Google credentials, recurring state, acceptance |
| Structured dispatcher failures and compatibility exports | [Dispatcher Task 2](02-dispatcher-contracts.md#task-2-add-structured-dispatcher-failures) | all dispatcher callers |
| Google identity, scope classification, secret transaction, and partial grants | [Google Tasks 1-2](03-google-onboarding.md#task-1-unify-canonical-client-discovery-and-add-the-shared-credential-model) | installer and service consumers |
| Scheduler context, stable resolver invocation, and mutable recurring paths | [Recurring Task 1](04-recurring-reliability.md#task-1-make-scheduler-defaults-and-environments-backend-owned) | installer and acceptance |
| Triage source identity, transactional finalization, and mutable state paths | [Downstream Task 2](05-downstream-workflows.md#task-2-prevent-triage-lost-updates-and-make-finalization-retry-safe) | recurring health and acceptance |

## Subplans and dependency order

| Order | Subplan | Independent deliverable | Depends on |
|---|---|---|---|
| 1 | [Installer runtime and launchers](01-installer-runtime.md) | Managed bootstrap, script wizard, stable command closure, plugin/developer separation | none |
| 2 | [Dispatcher contracts and diagnostics](02-dispatcher-contracts.md) | Valid contracts and safe structured dispatcher failures | none |
| 3 | [Shared Google onboarding](03-google-onboarding.md) | One authorization per account and service-owned credential consumers | 1, 2 |
| 4 | [Recurring reliability](04-recurring-reliability.md) | Backend-native scheduling and verified task outcomes | 1, 2 |
| 5 | [Downstream workflow repairs](05-downstream-workflows.md) | Robust email/list initialization, mutation, and rescan behavior | 2 |
| 6 | [Cross-platform and macOS integrated acceptance](06-macos-acceptance.md) | Three-platform lifecycle plus real macOS LaunchAgent completion path | 1-5 |

```text
Installer runtime ──────┬──> Google onboarding ──┐
                       └──> Recurring reliability ├──> integrated acceptance
Dispatcher contracts ──┬──> Google onboarding ───┘
                       ├──> Recurring reliability
                       └──> Downstream repairs ─────> integrated acceptance
```

Downstream repairs do not block the native LaunchAgent smoke, but their external-state and schema contracts are required for the full integrated lifecycle gate. They retain focused suites in addition to that gate.

## Feedback traceability

| # | Reported problem or recommendation | Owning subplan |
|---:|---|---|
| 1 | First install required a dispatcher that did not exist | 01 installer runtime |
| 2 | Real Python floor contradicted Python 3.6+/3.9 behavior | 01 installer runtime |
| 3 | Dependencies were undeclared/best-effort and not resumable | 01 installer runtime |
| 4 | Plugin workers/state were placed in immutable cache paths | 01 installer runtime |
| 5 | Fixed dispatcher subcommands were duplicated by contracts | 02 dispatcher contracts |
| 6 | Shared Google client discovery was inconsistent | 03 Google onboarding |
| 7 | Scheduler directories/environments were Linux-owned | 04 recurring reliability |
| 8 | macOS public commands lived under Documents | 01 installer runtime |
| 9 | No clean macOS LaunchAgent acceptance path | 06 cross-platform/macOS acceptance |
| 10 | Health ignored last exit and real task outcome | 04 recurring reliability |
| 11 | Scheduler documentation was systemd-only | 04 recurring reliability |
| 12 | Generated interface usage disagreed with runtime | 02 dispatcher contracts and 05 downstream repairs |
| 13 | Health returned zero while reporting failure | 04 recurring reliability |
| 14 | `scripts-test` reported trigger acceptance as success | 04 recurring reliability |
| 15 | Append-only logs lacked run boundaries/results | 04 recurring reliability |
| 16 | Daily plan was scheduled hourly | 04 recurring reliability |
| 17 | Default jobs contained Linux-only environment values | 04 recurring reliability |
| 18 | `invoke-skill` did not guarantee delegated `assistant` | 01 installer runtime |
| 19 | Launchers generated stale vendor/cache lookup paths | 01 installer runtime |
| 20 | Inner interface failure did not fail the task contract | 04 recurring reliability |
| 21 | Dispatcher rejection lacked resolved source context | 02 dispatcher contracts |
| 22 | Health lacked a task-level artifact/status contract | 04 recurring reliability |
| 23 | Fresh todo/triage lists were unusable | 05 downstream repairs |
| 24 | Concurrent cloud writes lost list updates | 05 downstream repairs |
| 25 | Triage instructions allowed unsafe parallel mutations | 05 downstream repairs |
| 26 | Missing email Subject crashed envelope decoding | 05 downstream repairs |
| 27 | No supported historical rescan preserving watermark | 05 downstream repairs |
| 28 | Metrics usage was wrong and finalization was nontransactional | 05 downstream repairs |

## Integrated acceptance

- Clean Linux, macOS, and Windows plugin installs require no system Python and modify no ambient Python environment.
- On all three platforms, first install, successful update, failed-update rollback, default uninstall, and purge are exercised against a temporary home. A failed candidate never changes `current.json`; an activated release remains usable after the source plugin/cache path is removed.
- Blank mode input visibly selects recommended plugin mode; developer mode requires explicit selection and a checkout.
- `dispatcher`, `invoke-skill`, mandatory `assistant`, `_agent_launch.py`, wrappers, and selected launchers share the stable user bin.
- Plugin cache, activated release, `$CODEX_HOME`, and `$CLAUDE_HOME` remain immutable in plugin mode. Only explicitly selected shell PATH and scheduler integration changes host configuration; recurring and email-triage mutation succeeds with the installed/plugin tree read-only because all mutable files resolve under config/state roots.
- One stubbed Google authorization supplies one opaque credential reference to all selected same-account services.
- Recurring setup receives the completed install's absolute stable bin/uv/resolver/pointer paths, config/state paths, and selected default LLM through its declared dispatcher interface; native definitions resolve managed Python through the pointer at run time and wait for process plus task completion.
- Explicit optional failures produce exit `3`, preserve core install, and include exact safe retry commands.
- Native macOS smoke proves a real temporary LaunchAgent executes from the installed path, records a successful task contract, and is removed in cleanup.
- Lifecycle acceptance proves v1-manifest and legacy-path migration is install-new/verify/remove-old, reference-safe, and rollback-safe.
- Downstream suites independently prove missing-Subject tolerance, usable list initialization, atomic mutations, deduplicated rescan, and ordered finalization.

## Explicitly out of scope

- Replacing the user's preferred Python or shell environment.
- Falling back to system Python.
- Supporting multiple managed Python minor versions concurrently.
- System-wide installation or `sudo`.
- Silent migration/deletion of legacy per-service refresh tokens.
- Combining intentionally different Google accounts into one grant.
- Treating an LLM process exit code as proof of task success.
- Reopening `docs/plans/completed/crossplatform.md`.
