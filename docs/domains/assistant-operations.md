# Assistant Operations

This domain covers supporting infrastructure: bounded cloud storage, Google
authentication, recurring automation, installation tooling, and synchronization
repair.

Google integrations and agent-driven recurring workflows are experimental in
the first public release. Recurring jobs are not installed by the core setup;
they require an explicit scheduling request. Review the
[security and privacy boundary](../security-and-privacy.md) before enabling
either surface.

Example prompts:

- `Set up recurring daily planning.`
- `Connect Famulus to Google.`
- `Check whether cloud-files OAuth is working.`
- `Diagnose this bisync failure.`

Some workflows depend on external systems such as Google Drive, a native
per-user scheduler, or local repair tooling.

<!-- BEGIN AUTO-GENERATED DOCS: assistant-operations -->
> Generated from live blueprints. Do not edit this block by hand.

- `cloud-files` — Bounded read/write of plain files under a configured Google Drive root
- `connect-google` — The user needs to set up or restore Google authentication for Famulus
- `install-assistant-tools` — Install or update launchers, wiring, hooks, and environment on a machine
- `recurring-tasks` — Manage recurring AI jobs through the host's native per-user scheduler
<!-- END AUTO-GENERATED DOCS: assistant-operations -->
