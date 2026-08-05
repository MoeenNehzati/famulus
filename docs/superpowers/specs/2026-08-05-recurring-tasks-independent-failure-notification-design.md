# Independent Recurring-Tasks Failure Notification Design

Status: approved (design phase). Date: 2026-08-05.

## Problem

`recurring-tasks` uses systemd user timers for managed jobs on this Linux host
and a separate cron entry to check their health every four hours. That
separation is intentional: cron may remain operational when the systemd user
manager, generated units, or their runtime configuration fail.

The current cron entry points directly into the mutable skill tree. After the
runtime moved under `_rtx`, cron continued firing but failed before the health
check started. Because the health check owned the notification behavior, no
desktop popup was possible. `MAILTO=""` and log redirection made the launch
failure visible only in the health-check log.

## Goals

- Preserve cron as a scheduler-independent sentinel for Linux systemd jobs.
- Keep the existing four-hour health-check interval.
- Produce a desktop popup on every failed health-check invocation.
- Report failures that occur before the health-check implementation starts,
  including a missing launcher or broken route.
- Detect systemd-manager, timer, service, freshness, and recorded job failures
  through the health check.
- Keep all changes inside `recurring-tasks` and its installation surface.
- Avoid a chain of watchdogs.

## Non-Goals

- Do not monitor unrelated user cron jobs, including the rclone jobs.
- Do not add a monitor for cron itself.
- Do not guarantee notification while the machine is off or while the user's
  desktop D-Bus session is unavailable.
- Do not add deduplication, cooldowns, or recovery notifications.
- Do not replace cron with a systemd timer.
- Do not claim the same independent-scheduler mechanism on macOS or Windows;
  this design adds a Linux backend safeguard.

## Chosen Architecture

The cron registration, rather than the health-check implementation, owns the
final failure notification.

Every four hours cron performs this sequence:

1. Invoke a stable, installed `recurring-tasks` health-check launcher.
2. Append the launcher's output to the owning skill's
   `logs/healthcheck/run.log`.
3. If the launcher is missing or exits nonzero, invoke `/usr/bin/notify-send`
   directly with an explicit user-session D-Bus environment.
4. Show one generic popup stating that the recurring-tasks health check failed
   and naming the log to inspect.

The fallback is expressed in the cron registration itself. It therefore still
runs when the health-check launcher, managed runtime resolver, Python entry, or
health-check implementation cannot start. It depends only on cron's shell,
`/usr/bin/notify-send`, and the user's desktop D-Bus session.

The health check becomes a pure checker: it writes diagnostics and returns
zero for health or nonzero for failure. It does not send its own popup, which
prevents duplicate notifications when cron observes the same nonzero exit.
Manual health-check invocations continue to print and log their result but do
not produce a desktop popup.

## Stable Launcher Boundary

The cron entry must not reference a file inside `skills/recurring-tasks/`.
Setup installs a small, stable launcher in the managed user bin directory and
registers that absolute launcher path with cron. The launcher reaches the
public recurring-tasks health-check interface through the installed dispatch
surface.

The cron fallback does not depend on the launcher being present. A missing
launcher produces the shell's ordinary nonzero command result, which triggers
the direct notification command.

The installed launcher may change internally as runtimes evolve, but its path
and command contract remain stable:

- no arguments;
- exit `0` only when the health check passes;
- exit nonzero for launch, routing, or health failures;
- write human-readable diagnostics to standard output and error.

## Health-Check Responsibilities

The checker verifies at least:

- the systemd user manager is reachable;
- required recurring-task runtime configuration resolves;
- every enabled job has its expected timer and service registration;
- installed schedule and runtime targets correspond to current job
  configuration;
- each enabled job has a sufficiently fresh structured run record;
- the latest run satisfies its configured success contract.

Any failed check contributes a diagnostic and causes a nonzero exit. This
allows cron to report both ordinary job failures and structural drift such as
stale generated units.

## Notification Contract

Each failed four-hour cron invocation produces one popup. Repeated failures
produce repeated popups; there is no stateful suppression.

The notification is intentionally generic because the checker may not have
started:

- title: `Recurring tasks need attention`
- body: `The recurring-tasks health check failed. See its health-check log.`

Cron supplies the user runtime directory and session-bus address explicitly.
The existing live cron diagnostic has established that `notify-send` reaches
the desktop with those values and fails without them.

Notification output is appended to the same health-check log when that log is
available. The notification command itself does not depend on successful log
redirection, so a missing log path cannot suppress the popup. No further
watchdog is added.

## Setup And Migration

`recurring-tasks` setup owns the complete installation idempotently:

1. install or refresh the stable health-check launcher;
2. create the skill-owned health-check log directory when absent;
3. replace the existing cron line marked `ai-recurring-healthcheck`;
4. preserve the four-hour schedule;
5. retain unrelated crontab content byte-for-byte;
6. remove direct references to host-specific skill-tree paths;
7. verify the installed cron entry after writing it.

Re-running setup updates the one managed entry rather than adding another.
Syncing ordinary job units must not remove or duplicate the independent cron
sentinel.

## Failure Boundaries

This design covers:

- systemd user-manager unavailability;
- missing, disabled, failed, or stale managed units;
- stale or incorrect generated unit contents;
- failed or missing job run records;
- a missing health-check launcher;
- resolver, interpreter, or health-check startup failure;
- a checker-detected unhealthy result.

It does not cover:

- cron daemon failure;
- deletion of the managed crontab entry;
- machine shutdown or suspension across the observation window;
- loss of both filesystem access and the desktop notification path;
- an unavailable desktop session bus.

Those exclusions are the deliberate stopping point that avoids an endless
watchdog chain.

## Verification

Automated tests must verify:

- setup replaces the legacy skill-tree cron command with the stable launcher;
- setup is idempotent and preserves unrelated crontab lines;
- the installed schedule remains every four hours;
- a successful checker run does not invoke notification;
- a checker nonzero exit invokes notification once;
- a missing launcher invokes notification once;
- the notification command receives the explicit D-Bus environment;
- health-check diagnostics and notification errors reach the configured log;
- structural drift between job configuration and installed units is unhealthy.

A live Linux test must then exercise the actual cron-compatible command in the
user session for three cases:

1. healthy checker: no popup and zero exit;
2. forced checker failure: one visible popup;
3. unavailable launcher target: one visible popup.

The test must restore the real installed registration and temporary failure
state afterward.
