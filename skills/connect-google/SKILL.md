---
name: connect-google
description: >-
  Use when the user needs to set up or restore Google authentication for Famulus. Do not use for ordinary Google-service operations.
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

Setup Requires Setup Of: none
Setup Order:
1. `connect-google.interface.setup`

Public Interfaces:
- `connect-google.interface.connect-services`
- `connect-google.interface.create-client`
- `connect-google.interface.default`
- `connect-google.interface.setup`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `connect-google.interface.connect-services` — Install or reuse a Google Desktop OAuth client and hand selected Google services to their owning skills.
- `connect-google.interface.create-client` — Guide a user through creating and privately downloading a Google Desktop OAuth client for selected Famulus services.
- `connect-google.interface.default` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
- `connect-google.interface.setup` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
<!-- END BLUEPRINT INTERFACES -->
Skill: connect-google

This is the shared router for Google OAuth-client preparation.

1. Use `connect-google._rtx.interface.client-status` before asking the user for a file.
2. If the stored client is valid, use `connect-google.interface.connect-services`.
3. If no valid client is installed, ask whether the user already has a Google
   Desktop OAuth client JSON, naming both ways to have one: they created it in
   their own Google Cloud project, or the Famulus maintainer added them to a
   shared project and sent them the file. If status reports legacy candidates,
   ask before importing one; when candidates differ, ask which one to use. A
   confirmed or supplied file routes to
   `connect-google.interface.connect-services`; otherwise route to
   `connect-google.interface.create-client`.

When a user without a client asks why this step cannot be automated, explain
that Google requires every application reaching an account to be registered in
a Cloud project, and that Famulus runs locally with no server to hold a shared
identity. Bundling one client would put every user under a single app identity,
where one user's misuse suspends the app for everyone. Reproduce the following
two sentences verbatim; do not paraphrase, soften, or add specifics such as
costs or timelines:

> Drive, Calendar, and Gmail require Google's restricted scopes, and verifying
> an app for those requires an annual third-party security assessment.
> Until an app passes it, Google's OAuth user cap allows at most 100 manually
> listed test users, and their refresh tokens expire after seven days, so those
> users must authorize again.

Owning a project removes that cap for its owner; joining the maintainer's
project is subject to it. `instructions/create-client.md` repeats the second
sentence at the configuration step; keep both copies identical.

Apply the same route to initial setup and reconnect requests. Recommend Drive,
Calendar, and Gmail while allowing the user to choose a subset. The connection
route creates one timestamped credential file and binds that exact path through
service-owned machine interfaces. Treat only `complete: true` as success; retry
an incomplete binding with `bind-credential-file` and the same file.

Never commit, publish, quote, or log the client JSON. Do not request, inspect,
or move service access tokens, refresh tokens, account records, or user data.
The deterministic coordinator invokes only the declared service-owned binders.

@./personal-preferences/default.md
