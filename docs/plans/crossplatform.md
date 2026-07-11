# Cross-Platform Reliability Plan

## Goal

Make Famulus work cleanly across Linux, macOS, and Windows as the project evolves, not just after ad hoc fixes when CI fails.

This plan separates:

- immediate product fixes we should land soon
- longer fixes that require redesign or substantial coding
- standards that should shape new work
- tests and validation layers that should catch regressions early
- per-item status, incident description, completed work, and future prevention work

The key principle is:

> A simple one-time fix is not enough if the same class of problem can re-enter silently in normal development.

## Scope

This plan is about shared project behavior, especially:

- shared skills intended to work across hosts
- installer and launcher behavior
- subprocess and dispatcher boundaries
- generated artifacts and installed outputs
- filesystem, path, shell, and encoding semantics

It is not limited to Windows. Windows exposed many of the failures first, but several are really cross-OS or generally portability-sensitive.

## Desired Outcome

We should be able to:

- develop mostly on Linux without repeatedly shipping Linux-specific assumptions
- rely on local validation to catch a meaningful fraction of portability regressions before CI
- use CI as confirmation and native-host smoke coverage, not as the first serious portability check
- add new shared skills without reintroducing shell-first or host-assumption-heavy designs

## Current Sequence

- current completed step: `Category 2 / Longer Fix 3` now has Python-first launcher installation, a recurring-tasks scheduler backend boundary, Python recurring-task setup/job execution, native scheduler generation for Linux/systemd, macOS/launchd, and Windows Task Scheduler, and a live Linux/systemd schedule/unschedule smoke pass
- current repo status: recurring-tasks tests pass after the scheduler/job-executor slice; focused installer launcher/scaffold tests pass after the Windows `invoke-skill.bat` slice; full pytest still has environment/unrelated failures for native secret storage, GitHub/network plugin install, read-only `.git/config`, and unrelated blueprint/schema migration dirt
- recommended next item: watch the new native scheduler smoke in CI on macOS and Windows, then record whether those hosts pass or need backend fixes before declaring `Category 2 / Longer Fix 3` fully done
- emphasis for the next slice: keep separating product/runtime fixes from native-host scheduler support and test-harness-only host access problems

Why this is next:

- launcher installation no longer depends on POSIX shell as the primary product surface
- recurring-task Linux/systemd scheduling works through the new Python executor path
- macOS and Windows scheduler backends now generate native scheduler entries, and CI has an opt-in live smoke step for those native hosts
- the next decision is narrower than a broad rewrite: inspect the native CI smoke results and fix any launchd/Task Scheduler host-specific failures

## Tracking Format

Each actionable item in this document should carry four fields:

- `Status` — `not started`, `in progress`, `partially done`, `done`, `blocked`, or `decided not to do`
- `Description` — what broke or what risk exists
- `What was done` — concrete changes already implemented, if any
- `Prevention` — how the same class of issue should be prevented from re-entering

Important:

- a one-time patch may move `What was done` forward without meaning the problem is fully solved
- `Prevention` is the long-term reliability work: standards, validation, tests, design constraints, or architectural changes
- some items will have `What was done: none yet` and still be important planning entries

## Category 1: Immediate Fixes

These are changes that are important, concrete, and should not wait for a larger redesign.

### 1. Fix unsafe profile rewriting in `install-assistant-tools`

Target:

- `skills/install-assistant-tools/_rtx/_agent_launchers.py`

Status:

- done

Description:

- the current `re.sub(..., replacement_string, ...)` path rewrite is unsafe
- Windows paths trigger it reliably, but the underlying mistake is portable

What was done:

- replaced the regex replacement string with a replacement function so filesystem paths are inserted literally
- encoded the rewritten `model_instructions_file` value as a TOML-compatible basic string, preserving backslashes instead of letting TOML treat them as escapes
- added a focused launcher test that runs on Linux but feeds the rewrite helper a Windows-style backslash path and verifies the installed profile parses back to the same path value
- adjusted the launcher tests to use a temp home for install manifests, so the test suite does not touch the real user manifest
- added `officina.common.toml_io` as the controlled TOML text boundary for runtime code
- refactored `install-assistant-tools` profile rewriting and Codex hook config edits to use `toml_io.open(...)` and TOML scalar helpers
- added `validators/toml_io_boundary.py`, which rejects production `.toml` filename mentions unless they are the direct filename argument to `toml_io.open(...)`, and rejects computed filename arguments such as variables or string concatenation
- documented the TOML IO boundary in `references/skill-guidelines.md`

Prevention:

- keep the targeted Windows-style path test in `skills/install-assistant-tools/tests/test_launchers.py`
- keep `tests/test_officina_toml_io.py` and `tests/validate_toml_io_boundary.py` in the validator suite so generated TOML is UTF-8, parse-checked, and centrally accessed
- require new production TOML access to go through `officina.common.toml_io`; add named helpers there for new TOML filename patterns instead of constructing TOML paths at call sites
- treat “embedding filesystem paths or other dynamic values into config syntax” as a portability-sensitive code path in reviews

### 2. Stop relying on GNU-only date formatting in shared runtime code

Targets:

- `skills/daily-plan/_rtx/_day_model.py`
- any other shared code using GNU/BSD-only `strftime` or shell date behavior

Status:

- done

Description:

- shared runtime code relied on GNU/POSIX-specific `strftime` no-padding modifiers (`%-m`, `%-d`)
- those modifiers are passed through to the host C library by Python and fail on Windows
- the broader assumption class is non-portable date/time formatting at IO boundaries

What was done:

- added `officina.common.dates` helpers for the repo's compact `M-D-YY` date-key IO contract: `format_date_key()`, `parse_date_key()`, and `get_today_date_key()`
- replaced the daily-plan `M-D-YY` key generation with that shared helper, preserving unpadded month/day and zero-padded two-digit year behavior
- added focused shared-helper and daily-plan tests for exact date-key behavior, including a one-digit month/day and a two-digit year with a leading zero
- removed the stale daily-plan `date` permission from `blueprint.yaml` and regenerated-equivalent `permissions.json`
- added `validators/portable_dates.py`, a repo validator that flags host-specific `strftime` padding modifiers in runtime Python
- added validator tests covering GNU-style `%-d` and Windows-style `%#d`, including a `cross_platform: false` skill so this class is not hidden by broad opt-outs
- updated `references/skill-guidelines.md` to document the date/time IO formatting rule, point authors to `officina.common.dates`, and link the mechanical validator

Prevention:

- keep exact behavioral tests for date-key helpers rather than smoke tests that only prove the code runs on Linux
- run `tests/test_officina_dates.py`, `tests/validate_portable_dates.py`, and `validators/portable_dates.py` with the validator suite so project-owned date formats and GNU/BSD/Windows-only `strftime` modifiers are caught before CI/native-host failures
- prefer `officina.common.dates` for project-owned date/time IO formats; add named helpers there when a format becomes part of a cross-skill storage, display, or protocol contract
- keep the validator focused on mechanically detecting non-portable `strftime` directives rather than banning broader date/time implementation choices

### 3. Make subprocess text encoding explicit where user text crosses process boundaries

Targets:

- `src/officina/dispatcher/core.py`
- scripts that print non-ASCII user-facing content

Status:

- done

Description:

- subprocess text boundaries currently rely too much on ambient host encoding behavior
- the lessons include both crash behavior and silent mojibake/data-corruption risk

What was done:

- the archive isolated the bad and good remediation patterns
- in particular, it showed that process-wide environment forcing can hide crashes while still corrupting text
- updated `officina.dispatcher.dispatch()` so dispatcher text mode always uses `encoding="utf-8"` and `errors="strict"` instead of the host locale/codepage
- updated Python module runtime resolution so dispatcher-launched Python children receive `PYTHONIOENCODING=utf-8:strict` locally in the child environment
- added dispatcher regression tests that assert the UTF-8 strict subprocess kwargs, assert the Python child IO environment, and round-trip non-ASCII stdin/stdout through a real dispatcher invocation
- added `validators/subprocess_text_encoding.py`, which rejects production/validator `subprocess` text mode unless both `encoding` and `errors` are explicit
- updated existing runtime and validator subprocess text calls to declare local encoding/error policy; git file-list validators use UTF-8 with `surrogateescape`
- documented the subprocess text-boundary rule in `references/skill-guidelines.md`

Prevention:

- keep dispatcher text mode pinned to UTF-8 strict rather than ambient host encoding
- keep Python child output encoding local to dispatcher-launched Python module runtimes; do not rely on process-wide environment forcing as the only fix
- keep `tests/test_officina_dispatcher.py`, including the non-ASCII round-trip fixture
- keep `validators/subprocess_text_encoding.py` and `tests/validate_subprocess_text_encoding.py` in the validator suite so new `subprocess.run(..., text=True)` usage cannot re-enter without explicit `encoding` and `errors`
- keep binary subprocess capture allowed when callers intentionally handle bytes and decode explicitly at a narrower boundary

### 4. Declare known runtime dependencies up front

Targets:

- `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- relevant skill docs and manifests

Status:

- done

Description:

- shared skills rely on Python packages and external binaries that are not always declared where installation and validation can see them

What was done:

- the missing dependency class was identified clearly in the lessons archive
- made `dependencies` mandatory on executable blueprint machine interfaces; interfaces with no non-stdlib Python package or external executable requirements must say `dependencies: []`
- dependency entries now require `kind`, `name`, and `reason`, with `kind` limited to `python` or `binary`
- documented that dependency declarations are factual runtime requirements and are separate from developer-chosen `suggested_permissions`
- added schema and sync-validator coverage so missing or malformed executable-interface dependencies fail before runtime
- added generated `references/blueprint/runtime_dependencies.json`, produced by `skills/skill-maker/_rtx/_blueprint_syncer.py` from blueprint declarations
- updated `skills/install-assistant-tools/_rtx/_install_scaffold.py` to install Python packages from the generated JSON manifest using stdlib JSON, so end-user installs do not need PyYAML
- moved current runtime dependency declarations onto `interfaces.machine` entries, including `dateparser`, `PyYAML`, `jsonschema`, `rich`, `bibtexparser`, `marker-pdf`, and known external binaries such as `rclone`, `curl`, `jq`, `msmtp`, `secret-tool`, `systemctl`, and `journalctl`
- removed the hardcoded installer Python package fallback; installer package selection now comes from the generated JSON manifest

Prevention:

- keep dependency declarations on each executable `interfaces.machine` entry, including explicit empty lists
- keep `python3 skills/skill-maker/_rtx/_blueprint_syncer.py --check` in the validation path so generated dependency manifests cannot drift from blueprint YAML
- keep installer tests that verify scaffold consumes `references/blueprint/runtime_dependencies.json` for Python packages and ignores binary dependencies for pip installation
- keep schema, sync-validator, and generated-doc tests rejecting the removed `script_interfaces` key so executable contracts stay on `interfaces.machine`

### 5. Fail loudly when a required capability is skipped on a host

Targets:

- `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- installer UX and reporting

Status:

- done

Description:

- silent or low-signal installer skips defer real failures into unrelated runtime surfaces

What was done:

- the dependency chain and the misleading downstream error shape were documented in the lessons archive
- the implementation slice is now scoped: `dispatcher` is a required scaffold capability, `invoke-skill` is required for recurring automation, and skipped required capabilities should produce a structured install-time report instead of a low-signal note
- the preferred behavior is an aggregated nonzero scaffold result rather than fail-fast, so users see all missing required capabilities and the dependent workflows affected by each one
- `_install_scaffold.py` now returns structured capability results for `dispatcher` and `invoke-skill`, prints an aggregated scaffold capability report, and exits nonzero when a required capability is skipped
- `_phase_entry.py` now stops the phase-1 install before `dev-link` or launcher installation if scaffold reports a required capability failure
- focused installer tests cover successful Unix scaffold, dry-run capability reporting, unsupported-host required-capability failures, affected workflow names, and phase-entry stopping on scaffold failure
- `docs/installation.md` and `skills/install-assistant-tools/SKILL.md` now document the required-capability report and nonzero failure behavior

Prevention:

- if a host skips installation of a required shared capability such as `dispatcher`, the installer should clearly say which dependent workflows are broken
- strengthen installer tests so skipped foundational capabilities fail validation rather than remaining a documentation detail
- add scaffold tests that monkeypatch an unsupported host, assert the installer reports the skipped capability by name, assert it names affected workflows such as machine-interface dispatch or recurring automation, and assert normal install does not silently succeed when a required capability is missing
- keep dry-run behavior informative: it should report the same capability status without writing files

## Category 2: Longer Fixes Requiring Redesign or Significant Coding

These are not just patches. They change architecture, runtime surfaces, or support contracts.

### 1. Rewrite `g-calendar` out of Bash

Targets:

- `skills/g-calendar/_rtx/_gcal_client.py`
- `skills/g-calendar/blueprint.yaml`

Status:

- done

Description:

- the old Bash runtime was the core portability problem, and that logic moved to Python first
- the remaining issue was that the exported `scripts-gcal` entrypoint was still Bash-based even after the implementation body moved to Python
- this is true regardless of concurrency concerns

What was done:

- the lessons archive documented and validated a Python rewrite approach
- this repo now has a stdlib Python calendar runtime in `skills/g-calendar/_rtx/_gcal_client.py`
- `scripts-gcal` now uses a dispatcher `python_machine_interface` runtime instead of a command runtime pointing at a shell wrapper
- the shell wrapper was removed from the tracked skill runtime files
- `g-calendar` is now marked `cross_platform: true`
- the generated permission artifact no longer asks for a Bash approval for the calendar query tool
- the public skill text now describes the interface and setup interface instead of naming private runtime files
- parallel all-calendar event fetching and retained event fields such as summary, time, location, description, status, and link were preserved in the Python path
- the Python runtime now caps all-calendar worker fanout and skips thread-pool setup when the calendar list is empty
- focused tests cover date-range resolution, merged multi-calendar fetches, empty-calendar behavior, worker-cap behavior, create/get/update/delete/move command behavior, and the Python interface help surface
- local timing and request-breakdown measurements were run against the live calendar account to confirm that request latency and calendar-list discovery dominate runtime, not Python thread-pool overhead
- skill-local verification passed:
  - `python3 -m pytest -q skills/g-calendar/tests`
  - `python3 skills/skill-maker/_rtx/_blueprint_syncer.py --check`

Prevention:

- keep `scripts-gcal` bound to a dispatcher `python_machine_interface` runtime rather than a shell wrapper
- keep `g-calendar` enrolled in the cross-platform validator so new tracked shell scripts or shell-script blueprint permissions fail validation
- treat Bash-first shared runtimes as exceptions that require justification
- keep focused unit tests on the Python runtime so future feature work does not drift back toward shell-only assumptions
- keep exported-interface smoke coverage on the Python module help path
- if faster repeated reads become important, add access-token reuse and calendar-list caching rather than adding more threads; measurements showed fixed serial API costs dominate

### 2. Redesign `email-client` around host-specific secret and send backends behind one stable interface

Targets:

- `skills/email-client/_rtx/_email_accounts.py`
- `skills/email-client/_rtx/_imap_gateway.py`
- `skills/email-client/_rtx/_smtp_transport.py`
- `skills/email-client/_rtx/_oauth_tokens.py`
- `skills/email-client/_rtx/_email_smoke.py`
- `src/officina/common/secret_store.py`

Status:

- done

Description:

- the current email-client runtime is Linux-first and exposes host-specific mechanisms too directly to shared runtime code

What was done:

- the lessons archive produced a concrete example of both the needed abstraction and a bad partial merge shape that would have broken other hosts
- `email-client` account storage, IMAP credential lookup, and SMTP credential lookup now go through `officina.common.secret_store` instead of shelling out to `secret-tool`
- send-email moved from the shell `_smtp_transport.sh`/`msmtp` path to a Python machine interface that composes MIME with `email`/`mimetypes` and sends with stdlib `smtplib`/`ssl`
- the public dispatcher interfaces are preserved, including stdin body handling, repeated `--to`, attachments, and reply threading headers
- `imap_service`/`smtp_service` registry fields remain honored as secondary secret keys during migration, but through the shared secret-store namespace rather than raw host commands
- `blueprint.yaml`, generated `SKILL.md`, `permissions.json`, and `references/blueprint/runtime_dependencies.json` now declare `keyring` for secret-using email-client interfaces and no longer declare `secret-tool`, `msmtp`, `base64`, or `file` for email-client send/read paths
- focused tests cover secret-store writes/clears, IMAP canonical and secondary credential lookup, SMTP message construction, attachment display names, STARTTLS versus SMTP_SSL selection, and send/login behavior
- Gmail OAuth is available through `accounts-setup-oauth`; app-password auth remains the default and the public read/send interfaces select the right auth path from account metadata
- `live-smoke` provides explicit IMAP and SMTP-auth provider checks that do not send mail unless `--send-self` is requested

Prevention:

- keep skills behind `officina.common.secret_store` rather than importing `keyring` directly or shelling out to host credential commands
- keep send-mail behavior behind the stable `send-email` dispatcher interface while using Python stdlib SMTP primitives internally
- keep email-client enrolled in blueprint/runtime dependency checks so old host-specific binaries cannot return as undeclared assumptions
- keep OAuth enrollment as an explicit account-management action, not an implicit change to existing app-password accounts
- keep live provider checks behind explicit smoke-test flags so verification does not accidentally send mail

### 3. Redesign launcher and automation surfaces that currently assume POSIX shell

Targets:

- `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- recurring-tasks setup and job execution surfaces
- installed `dispatcher` / `invoke-skill` behavior

Status:

- partially done

Description:

- host launcher and automation behavior still assumes POSIX shell and systemd-oriented execution in places where the project wants broader host support

What was done:

- the lessons archive mapped which failures came from launcher surfaces versus core Python logic
- added an installer-local launcher bundle layer under `skills/install-assistant-tools/_rtx/_install_launcher/`, with Linux, macOS, and Windows implementations selected behind one neutral `platform_launcher_installer()` boundary
- moved generated/static launcher file placement out of scaffold and agent launcher orchestration, so `dispatcher`, `invoke-skill`, agent launchers, `.bat` wrappers, and `tw` install through platform-specific launcher bundle contracts
- made the scaffold install a Windows `dispatcher.bat` launcher instead of skipping the required dispatcher capability on Windows
- initially changed Windows `invoke-skill` handling from a required scaffold failure to an explicit unsupported automation capability while recurring-tasks was still systemd/Unix-only
- changed Windows agent launcher file installation to copy the launcher bundle instead of relying on symlink creation, avoiding Developer Mode/admin symlink requirements for supported primary launchers
- changed Windows `tw` handling to skip launcher installation up front rather than only skipping verification
- added focused tests for generated launcher bundles, copy/link static launcher behavior, platform installer selection, Windows dispatcher installation, Windows copied agent launchers, and Windows `tw` skip behavior
- updated install and launcher docs to describe platform-specific launcher bundle installation and the current recurring-automation support boundary
- added a private recurring-tasks scheduler backend package under `skills/recurring-tasks/_rtx/_schedule_backend/`, first preserving Linux/systemd behavior behind the interface and then adding macOS/launchd and Windows Task Scheduler implementations
- moved recurring-tasks unit sync, job test/status, and scheduler healthcheck probes behind the backend interface instead of direct `systemctl` calls in the command scripts
- added focused recurring-tasks backend tests that pin Linux/systemd unit generation, macOS launchd plist/command generation, Windows Task Scheduler command generation, and platform backend selection
- replaced the Linux/macOS generated `dispatcher` and `invoke-skill` shell launchers with extensionless Python launchers; `invoke-skill` no longer depends on a recurring-tasks `_agent_invoker.sh`
- replaced recurring-tasks setup shell with a Python setup runner and removed stale `scripts-invoke-agent`, `_agent_invoker.sh`, `_run_job.sh`, and `_agent_env.sh` surfaces
- changed generated systemd services to call a Python job executor instead of `/bin/bash -c` with shell redirection, and added tests that reject shell service generation
- committed the main launcher/scheduler slice as `292f38b` (`Make recurring task launchers Python-first`)
- ran a live Linux/systemd implementation smoke test using a temporary `ai-codex-live-smoke-*` timer/service generated with the new unit shape
- the first live smoke test proved the timer was scheduled and fired, but exposed a real product bug: the direct systemd execution environment could not import `officina` because `_job_executor.py` assumed repo `src` was already on `PYTHONPATH`
- fixed `_job_executor.py` so direct systemd execution bootstraps the repo `src` path itself, and added a regression test that invokes the executor directly with `PYTHONPATH` removed
- reran the live smoke test successfully: the temporary timer became enabled/active, fired at the next minute boundary, wrote the marker through the Python executor, then cleanup disabled/removed the timer and service so post-checks reported `inactive`, `not-found`, and no remaining unit files
- reran `python3 -m pytest -q skills/recurring-tasks/tests`, which passed with 114 tests after the executor bootstrap fix
- implemented the macOS backend with per-user launchd LaunchAgent plist generation, `launchctl bootstrap`/`bootout`, `launchctl kickstart` testing, and launchd status/health checks
- implemented the Windows backend with Task Scheduler `schtasks.exe` create/delete/run/query behavior under the root-level `Famulus-AI-ai-*` task-name prefix
- added Linux-run unit tests for macOS launchd plist generation, launchd cron conversion, launchd load/remove/kickstart commands, Windows Task Scheduler cron conversion, task create/delete/run commands, and stale task cleanup
- installed Windows recurring automation support at the launcher layer by generating `invoke-skill.bat` instead of reporting Windows `invoke-skill` as unsupported
- updated the Python job executor to resolve Windows launcher shims such as `invoke-skill.bat` through PATH/PATHEXT before launching without a shell
- updated install docs and installer tests so Windows expects both `dispatcher.bat` and `invoke-skill.bat`
- added an opt-in live scheduler smoke test that creates one unique temporary scheduler entry, waits for the Python executor to write a marker file, then removes only that entry
- wired GitHub Actions to run that live scheduler smoke on macOS and Windows after the normal full Python suite

Still remaining:

- macOS launchd scheduling has not been live-tested on a macOS host in this repo session; the new CI smoke should provide that result on push/PR
- Windows Task Scheduler scheduling has not been live-tested on a Windows host in this repo session; the new CI smoke should provide that result on push/PR
- the current local Linux-run tests validate generated native scheduler files/commands, but local Linux cannot prove launchd or Task Scheduler accepts and runs those entries on real hosts

Prevention:

- treat host launchers as a supported product surface
- keep supported host launcher installation behind `_rtx/_install_launcher/` instead of scattering `sys.platform` branches across installer phases
- keep launcher bundle specs rather than single-file assumptions, so future generated launchers can include helper files without redesigning scaffold or launcher orchestration
- require installer and launcher tests to verify promised host surfaces, including Windows `dispatcher.bat`, copied Windows agent launcher bundles, and explicit unsupported statuses for platform-scoped automation
- keep recurring automation entrypoints behind the scheduler backend interface; add macOS launchd and Windows Task Scheduler behavior inside backend implementations rather than new top-level `sys.platform` branches
- keep backend tests as the place where host scheduler commands, generated files, and unsupported platform statuses are pinned
- keep recurring-tasks POSIX regression tests checking blueprint runtime kinds, `_rtx` shell files, generated service content, and generated launcher content so CI fails on the host where a platform-specific assumption re-enters
- keep a Linux/systemd live smoke test available for manual or CI use that creates one unique temporary timer/service, waits for execution, and verifies cleanup removes only that timer/service
- keep the direct-executor import regression test so systemd-style execution cannot silently depend on the interactive shell's `PYTHONPATH`
- keep the native-host smoke tests for macOS launchd and Windows Task Scheduler creating one unique temporary scheduler entry, waiting for execution, then verifying cleanup removes only that entry

### 4. Reduce shell-first shared runtime design in new and existing skills

Targets:

- shared skills marked or intended as cross-platform

Status:

- in progress as a project direction, but not enforced strongly enough

Description:

- shell wrappers and external toolchains still appear too often as primary runtime surfaces for shared logic

What was done:

- some parts of the repo already follow a Python-first cross-platform direction
- installer tests are already Python-based, which is the right shape

Prevention:

- migrate high-value shared runtimes from shell wrappers and external toolchains toward Python-first implementations
- make “shared cross-platform skills should be Python-first” an explicit development standard
- reflect that standard in validators, code review, and new skill scaffolding

## Category 3: Standards To Follow From Now On

These are working rules for future development. They should influence design before bugs are written.

### 1. Shared cross-platform skills should be Python-first by default

Status:

- proposed standard

Description:

- shared skills keep inheriting portability problems when Bash or shell-first design is treated as normal

What was done:

- this plan now records the standard explicitly

Meaning:

- if a skill is expected to work across Linux, macOS, and Windows, its primary runtime interface should not be Bash

Allowed exception:

- a skill may be explicitly platform-scoped, but that should be declared intentionally rather than by accident

### 2. Host-specific behavior belongs behind stable interfaces

Status:

- proposed standard

Description:

- direct host-specific coupling in shared callers makes future portability work brittle

What was done:

- the lessons archive provided a concrete negative example and this plan records the policy

Meaning:

- callers should ask for generic actions such as "store credential" or "send mail"
- callers should not know whether the host backend uses `secret-tool`, Windows Credential Manager, Keychain, `msmtp`, or a pure Python SMTP client

### 3. Treat subprocess encoding as an interface contract

Status:

- proposed standard

Description:

- text encoding problems are easy to dismiss as environment quirks until they become correctness or corruption bugs

What was done:

- the plan now elevates this from implementation detail to explicit interface rule

Meaning:

- if text crosses a subprocess boundary, encoding must be explicit where the boundary is portability-sensitive
- do not rely on ambient locale or console defaults

### 4. Do not use GNU or POSIX extensions casually in shared runtime code

Status:

- proposed standard

Description:

- GNU and POSIX assumptions enter shared code easily on Linux and are then discovered late on other hosts

What was done:

- this plan now makes that assumption class explicit

Meaning:

- avoid assuming `date -d`, `timedatectl`, `chmod` semantics, POSIX exec bits, shell path behavior, or `bash` availability in shared runtime paths

### 5. Declare runtime dependencies where installation and validation can see them

Status:

- proposed standard

Description:

- dependency drift makes portability look like runtime instability when it is often really install contract drift

What was done:

- the plan now records declaration visibility as a design rule

Meaning:

- do not let a skill assume that `jq`, `curl`, `msmtp`, or a Python package is present without declaring that requirement somewhere authoritative

### 6. Silent host degradation is worse than explicit unsupported status

Status:

- proposed standard

Description:

- hidden degradation produces confusing downstream failures and wastes debugging time

What was done:

- the plan now records this as an explicit product rule

Meaning:

- if a host is not supported for a capability, say so clearly
- do not quietly skip a foundational component and let failures surface several layers later

### 7. `cross_platform: false` should be intentional, narrow, and reviewed

Status:

- proposed standard

Description:

- opt-out flags are useful, but they can also hide drift if not treated as conscious contract decisions

What was done:

- the plan now records this as a review expectation

Meaning:

- the flag should not become a hiding place for portability debt in broadly useful shared skills
- if a skill is excluded, we should know whether that is temporary debt or a permanent contract choice

### 8. Prefer behavioral guarantees over weakened assertions

Status:

- proposed standard

Description:

- when a Unix-specific assertion fails, the wrong reaction is often to reduce the test to something vacuous

What was done:

- the plan now records that tests should be made portable, not diluted

Meaning:

- if a test fails on Windows because the assertion is Unix-specific, replace it with a cross-platform behavioral guarantee
- do not weaken the test into “command exited successfully” if a stronger portable guarantee is possible

## Category 4: Tests And Validation To Add

The goal here is not just more tests. It is earlier and more meaningful detection.

### A. Unit Tests

Add targeted unit tests for portability-sensitive helpers and transformations.

Examples:

- Windows-style path replacement tests for launcher/profile rewriting
- TOML IO tests that write Windows-style paths, parse the result with `tomllib`, and assert round-trip equality
- date-key formatting tests that do not depend on host-specific `strftime`
- launcher-selection tests for `.bat` vs extensionless launchers
- path-join and path-normalization tests using Windows-style inputs

Why this matters:

- many portability bugs are small, local, and cheap to catch if we test the exact transformation

Limitation:

- these tests catch local logic bugs
- they do not replace real subprocess or installed-layout testing

### B. Contract Tests

Add validators that enforce portability rules at the repo level.

Examples:

- enforce the TOML IO boundary: production `.toml` filenames may appear only as direct `toml_io.open(...)` filename arguments, and the filename argument must be a visible literal or f-string
- detect undeclared external binary dependencies in shared skills
- detect undeclared Python package dependencies
- flag risky subprocess text usage without explicit encoding where appropriate
- flag GNU/POSIX shell assumptions in code paths that are meant to be cross-platform
- require justification for `cross_platform: false`

Why this matters:

- prevention is harder than the one-time fix
- contract tests make prevention part of normal development rather than memory

### C. Integration Tests Runnable On Linux

Add real subprocess-boundary tests that run on Linux but simulate portability-sensitive behavior.

Examples:

- real `script_dispatcher.dispatch()` round-trip tests using stub skills
- non-ASCII stdin/stdout round-trip tests across dispatcher boundaries
- nested child-process tests that verify environment propagation such as `PYTHONPATH`
- `daily-plan` integration tests using stub `cloud-files`, `g-calendar`, and `list-manager` skills through real subprocess calls rather than mocked lambdas

Why this matters:

- many failures are invisible to pure unit tests
- Linux-only integration coverage can still catch a large fraction of portability bugs if the test is built around the real boundary

### D. Native Cross-Platform Smoke Tests In CI

Keep CI matrix coverage, but strengthen what each host actually proves.

Examples:

- Windows installer smoke that requires a working installed `dispatcher`
- Windows launcher smoke that verifies `.bat` execution paths
- Windows and macOS smoke tests for one real shared workflow, not just metadata validation
- calendar smoke once `g-calendar` is rewritten in Python
- email-client smoke once the backend abstraction exists

Why this matters:

- some behavior cannot be simulated faithfully on Linux
- CI should remain the place where native host behavior is confirmed

### E. Installer And Launcher Tests

Treat installed outputs as a product surface.

Examples:

- assert installed artifacts are correct for the host
- verify behavior, not just presence
- verify generated launchers contain the correct host-specific command routing
- verify install-time failure messages are informative when a required capability is unsupported

Why this matters:

- many of the late failures were not deep algorithm bugs
- they were broken installed surfaces

### F. Filesystem And Path-Semantics Tests

Add explicit coverage for OS-sensitive filesystem behavior.

Examples:

- symlink availability and failure-path behavior
- path separator handling
- filename case-collision audits
- line-ending-sensitive generated files
- permission-bit versus actual invocability assertions
- packaged archive extract-install tests

Why this matters:

- filesystem assumptions are one of the main ways Linux-only development misses real host differences

## Priority Order

This is the recommended implementation order.

### First

- fix unsafe path replacement
- fix date-format portability issues
- make dispatcher text encoding explicit
- make installer failure on skipped foundational capability much clearer
- write the tests that lock those fixes in

### Second

- add contract tests for undeclared dependencies and risky portability patterns
- add real dispatcher round-trip integration tests with non-ASCII fixtures
- strengthen installer and launcher assertions

### Third

- rewrite `g-calendar` in Python
- redesign `email-client` host backend boundaries
- redesign non-POSIX automation and launcher surfaces that are meant to be supported on Windows

## What This Plan Does Not Fully Capture

Some issues do not fit cleanly into the four categories above.

### 1. Operational cleanup after already-corrupted external data

If a portability bug has already written bad data to external storage, the product fix and prevention work do not automatically clean up the affected data.

### 2. Unconfirmed or partially diagnosed issues

Some observed failures may still need deeper root-cause confirmation before they deserve major design commitments.

### 3. Deliberately platform-specific features

Some workflows may remain host-specific by design. Those need an explicit contract, not accidental drift.

## Success Criteria

This plan is succeeding when:

- shared cross-platform skills are no longer shell-first by default
- portability regressions are increasingly caught by local validation or Linux-run integration tests before CI
- CI failures on Windows/macOS are more often host-specific edge cases than foundational install/runtime breaks
- adding a new shared skill naturally follows the project’s portability standards instead of fighting them
