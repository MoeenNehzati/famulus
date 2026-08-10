---
name: connect-google
description: Use when a Google service needs a shared OAuth client prepared, or when the user asks to prepare Google authentication for Famulus.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: external-integrations; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `connect-google.source.gateway -> connect-google._rtx.interface.bind-credential-file@1`
- `connect-google.source.gateway -> connect-google._rtx.interface.client-status@1`
- `connect-google.source.gateway -> connect-google._rtx.interface.connect-services@1`
- `connect-google.source.gateway -> connect-google._rtx.interface.install-client@1`
- `connect-google.source.gateway -> connect-google.source.instructions-connect-services.interface.connect-services@1`
- `connect-google.source.gateway -> connect-google.source.instructions-create-client.interface.create-client@1`
- `connect-google.source.instructions-connect-services -> connect-google._rtx.interface.bind-credential-file@1`
- `connect-google.source.instructions-connect-services -> connect-google._rtx.interface.client-status@1`
- `connect-google.source.instructions-connect-services -> connect-google._rtx.interface.connect-services@1`
- `connect-google.source.instructions-connect-services -> connect-google._rtx.interface.install-client@1`
- `connect-google.source.instructions-create-client -> connect-google.source.instructions-connect-services.interface.connect-services@1`

Public Interfaces:
- `connect-google.interface.connect-services`
- `connect-google.interface.create-client`
- `connect-google.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `connect-google.interface.connect-services` — Install or reuse a Google Desktop OAuth client and hand selected Google services to their owning skills.
- `connect-google.interface.create-client` — Guide a user through creating and privately downloading a Google Desktop OAuth client for selected Famulus services.
- `connect-google.interface.default` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
<!-- END BLUEPRINT INTERFACES -->
Skill: connect-google

This is the shared router for Google OAuth-client preparation.

1. Use `connect-google._rtx.interface.client-status` before asking the user for a file.
2. If the stored client is valid, use `connect-google.interface.connect-services`.
3. If no valid client is installed, ask whether the user already has a Google
   Desktop OAuth client JSON. If status reports legacy candidates, ask before
   importing one; when candidates differ, ask which one to use. A confirmed or
   supplied file routes to `connect-google.interface.connect-services`; otherwise
   route to `connect-google.interface.create-client`.

Apply the same route to initial setup and reconnect requests. Recommend Drive,
Calendar, and Gmail while allowing the user to choose a subset. The connection
route creates one timestamped credential file and binds that exact path through
service-owned machine interfaces. Treat only `complete: true` as success; retry
an incomplete binding with `bind-credential-file` and the same file.

Never commit, publish, quote, or log the client JSON. Do not request, inspect,
or move service access tokens, refresh tokens, account records, or user data.
The deterministic coordinator invokes only the declared service-owned binders.

@./personal-preferences/default.md
