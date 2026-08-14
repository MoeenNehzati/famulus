# System and Automation Workflows

This page covers the system-facing skills: bounded cloud storage, recurring automation, PDF ingestion support, and machine-level repair utilities.

Google integrations and agent-driven recurring workflows are experimental in
the first public release. Recurring jobs are not installed by the core setup;
they require an explicit scheduling request. Review the
[security and privacy boundary](../security-and-privacy.md) before enabling
either surface.

## When To Use These Skills

These skills are usually supporting infrastructure for the more visible workflows in the rest of the library. Reach for them when you need to configure storage, schedule recurring runs, or repair an underlying system dependency.

Example prompts:

- `Set up recurring daily planning.`
- `Check whether cloud-files OAuth is working.`
- `Convert this research PDF to markdown.`
- `Diagnose this bisync failure.`

## Notes

- Some of these workflows depend on external systems such as Google Drive, systemd, or local repair tooling.
- The user-facing planning and research docs link back to these skills when the workflow crosses into infrastructure setup.

<!-- BEGIN AUTO-GENERATED DOCS: assistant-operations -->
> Generated from live blueprints. Do not edit this block by hand.

- `cloud-files` — Bounded read/write of plain files under a configured Google Drive root
- `connect-google` — The user needs to set up or restore Google authentication for Famulus
- `fix-bisync` — Diagnose and repair rclone bisync failures
- `install-assistant-tools` — Install or update launchers, wiring, hooks, and environment on a machine
- `recurring-tasks` — Manage recurring AI jobs through the host's native per-user scheduler
<!-- END AUTO-GENERATED DOCS: assistant-operations -->
