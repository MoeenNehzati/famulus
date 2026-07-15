---
name: connect-google
description: Use when a Google service needs a shared OAuth client prepared, or when the user asks to prepare Google authentication for Famulus.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: workflow-general-assistant

Skill Version: 1

Uses Interfaces:
- `connect-google.llm.connect-services -> connect-google.machine.client-status@1`
- `connect-google.llm.connect-services -> connect-google.machine.install-client@1`
- `connect-google.llm.create-client -> connect-google.llm.connect-services@1`
- `connect-google.llm.default -> connect-google.llm.connect-services@1`
- `connect-google.llm.default -> connect-google.llm.create-client@1`
- `connect-google.llm.default -> connect-google.machine.client-status@1`

Public Interfaces:
- `connect-google.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `client-status` — Report whether the canonical Google Desktop OAuth client is missing, valid, or invalid without exposing its secrets.
  - `dispatcher --caller-skill connect-google connect-google.machine.client-status [--home <dir>]`
- `install-client` — Validate a Google Desktop OAuth client JSON and atomically install a private canonical copy.
  - `dispatcher --caller-skill connect-google connect-google.machine.install-client --from-json <client-json> [--replace] [--home <dir>]`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `connect-services` — Install or reuse a Google Desktop OAuth client and hand selected Google services to their owning skills.
  - binding: relative markdown path `llm_interfaces/connect-services.md`
- `create-client` — Guide a user through creating and privately downloading a Google Desktop OAuth client for selected Famulus services.
  - binding: relative markdown path `llm_interfaces/create-client.md`
- `default` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
Skill: connect-google

This is the shared router for Google OAuth-client preparation.

1. Use `connect-google.machine.client-status` before asking the user for a file.
2. If the stored client is valid, use `connect-google.llm.connect-services`.
3. If no valid client is installed, ask whether the user already has a Google
   Desktop OAuth client JSON. If status reports legacy candidates, ask before
   importing one; when candidates differ, ask which one to use. A confirmed or
   supplied file routes to `connect-google.llm.connect-services`; otherwise
   route to `connect-google.llm.create-client`.

Apply the same route to initial setup and reconnect requests. Recommend Drive,
Calendar, and Gmail while allowing the user to choose a subset, then hand each
selection to its owning service skill.

Never commit, publish, quote, or log the client JSON. Do not request, inspect,
or move service access tokens, refresh tokens, account records, or user data.
Do not invoke service machine interfaces; the service skills invoke this skill
for client preparation and retain the entire authorization workflow.

@./personal-preferences/default.md
