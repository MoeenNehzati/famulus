# Managed Wakeup Launchers Design

## Goal

Install `llm-wakeup` and its `lw` alias through the same managed-runtime
resolver mechanism as `dispatcher` on Linux, macOS, and Windows.

## Architecture

Phase 1 continues to build and activate the managed Officina release before
scaffold writes any public launcher. Scaffold adds one required wakeup launcher
bundle beside the existing dispatcher and invoke-skill bundles.

The bundle contains two equivalent public commands:

- Linux and macOS: executable `llm-wakeup` and `lw` Python shims.
- Windows: `llm-wakeup.bat` and `lw.bat` batch shims.

Each shim invokes the fixed managed-runtime resolver with
`-m officina.wakeup.cli` and forwards all user arguments. It does not embed a
repository checkout, active release path, or release-specific interpreter.

The dispatcher and wakeup shims share module-launcher content generation so
their resolver behavior cannot drift independently. macOS retains the existing
Linux launcher contract; Windows retains its concrete install-time Python
resolution for launching the resolver.

## Installation Lifecycle

The wakeup bundle is a required scaffold capability. Therefore it participates
in existing dry-run output, capability reporting, PATH setup, manifest
recording, idempotent reinstall, and manifest-driven uninstall without a new
lifecycle mechanism.

This change installs commands only. Native scheduler installation and wakeup
policy configuration remain out of scope.

## Testing

Tests are added before implementation and must first fail because the wakeup
launcher API and files do not exist. Coverage includes:

- Linux command names, executable bits, resolver path, target module, argument
  forwarding, and absence of checkout/interpreter embedding.
- macOS inheritance of the Unix launcher contract.
- Windows `.bat` names, resolver path, target module, argument forwarding, and
  absence of extensionless commands.
- Scaffold installation and dry-run capability reporting for the wakeup bundle.
- Manifest-driven uninstall through existing lifecycle coverage.

Existing staged changes to installer manifest/uninstall tests are preserved and
are not edited unless the new behavior exposes a specific missing assertion.

## Non-goals

- Do not rely only on `pyproject.toml` console scripts; those live inside the
  managed release rather than the public user-bin directory.
- Do not introduce a generic command registry or dynamically consume
  `command-aliases.json`.
- Do not install or modify systemd, launchd, or Task Scheduler jobs.
