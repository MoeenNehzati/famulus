---
name: connect-google
description: Use when a Google service needs a shared OAuth client prepared, or when the user asks to prepare Google authentication for Famulus.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: workflow-general-assistant

Skill Version: 2

Uses Interfaces:
- `connect-google.source.gateway -> connect-google.source.instructions-connect-services.interface.connect-services@1`
- `connect-google.source.gateway -> connect-google.source.instructions-create-client.interface.create-client@1`
- `connect-google.source.instructions-create-client -> connect-google.source.instructions-connect-services.interface.connect-services@1`

Public Interfaces:
- `connect-google.interface.authorize-services`
- `connect-google.interface.client-status`
- `connect-google.interface.connect-services`
- `connect-google.interface.create-client`
- `connect-google.interface.default`
- `connect-google.interface.install-client`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `connect-google.interface.authorize-services` — Request one combined Google OAuth 2.0 PKCE grant across every selected service and store exactly the scopes Google actually granted.
  - `dispatcher --caller-skill connect-google connect-google.interface.authorize-services --services <list> [--account-hint <email>] [--home <dir>]`
- `connect-google.interface.client-status` — Report whether the canonical Google Desktop OAuth client is missing, valid, or invalid without exposing its secrets.
  - `dispatcher --caller-skill connect-google connect-google.interface.client-status [--home <dir>]`
- `connect-google.interface.install-client` — Validate a Google Desktop OAuth client JSON and atomically install a private canonical copy.
  - `dispatcher --caller-skill connect-google connect-google.interface.install-client --from-json <client-json> [--replace] [--home <dir>]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `connect-google.interface.connect-services` — Install or reuse a Google Desktop OAuth client and hand selected Google services to their owning skills.
- `connect-google.interface.create-client` — Guide a user through creating and privately downloading a Google Desktop OAuth client for selected Famulus services.
- `connect-google.interface.default` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
<!-- END BLUEPRINT INTERFACES -->
Skill: connect-google

This is the shared router for Google OAuth-client preparation.

1. Use `connect-google.interface.client-status` before asking the user for a file.
2. If the stored client is valid, use `connect-google.interface.connect-services`.
3. If no valid client is installed, ask whether the user already has a Google
   Desktop OAuth client JSON. If status reports legacy candidates, ask before
   importing one; when candidates differ, ask which one to use. A confirmed or
   supplied file routes to `connect-google.interface.connect-services`; otherwise
   route to `connect-google.interface.create-client`.

Apply the same route to initial setup and reconnect requests. Recommend Drive,
Calendar, and Gmail while allowing the user to choose a subset, then hand each
selection to its owning service skill.

Never commit, publish, quote, or log the client JSON. Do not request, inspect,
or move service access tokens, refresh tokens, account records, or user data.
Do not invoke service-owned process interfaces; the service skills invoke this skill
for client preparation and retain the entire authorization workflow.

@./personal-preferences/default.md
