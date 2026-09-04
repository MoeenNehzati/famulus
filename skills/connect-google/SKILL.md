---
name: connect-google
description: >-
  Use when the user needs to set up or restore Google authentication for Famulus. Do not use for ordinary Google-service operations.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

### Managed setup gate

Activate this gate only for an invocation of this skill's interfaces or an exact managed lifecycle entry below. Generic setup prose does not activate this gate.
Keep the original caller, interface, version, arguments, and stdin outside the ledger; the manager receives only its public continuation identity.

Managed lifecycle entries:
- Setup `connect-google.interface.setup@1` routes to `begin(setup, connect-google.interface.setup, ORIGINAL_CALLER, ORIGINAL_INTERFACE, ORIGINAL_VERSION)`.

For an ordinary invocation, use this exact sequence:

1. Call `setup-interface-manager._rtx.interface.status@1` for the original target interface. If it is `unmanaged`, run the original request normally. If it is `setup_busy`, follow only its recovery result.
2. If it is `setup_required`, obtain permission, then call `setup-interface-manager._rtx.interface.begin@1` as `begin(setup, ROOT_SETUP_INTERFACE, ORIGINAL_CALLER, ORIGINAL_INTERFACE, ORIGINAL_VERSION)`, where `ROOT_SETUP_INTERFACE` is the returned root setup interface.
3. Follow only the returned exact structured current step: call `setup-interface-manager._rtx.interface.run-markdown@1` for a Markdown step, follow its returned instructions, then call `setup-interface-manager._rtx.interface.settle@1`; call `setup-interface-manager._rtx.interface.run-python@1` for a Python step. Repeat until the flow is ready.
4. Perform the ready recheck with `setup-interface-manager._rtx.interface.status@1` for the original target and require `ready`; then call `setup-interface-manager._rtx.interface.authorize@1` with the original target plus caller, interface, and version.
5. Retry the original request exactly once, with its original arguments and stdin, only when `authorize` returns `resume_original: true`.

For an exact managed setup or teardown invocation, do not launch it directly; use its listed `setup-interface-manager._rtx.interface.begin@1` route. A manager result that names an exact structured current step is the only bypass of this gate.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `connect-google._rtx.interface.bind-credential-file` — Retry service-owned binding with an existing credential descriptor; never invoke OAuth authorization.
  - Caller: `connect-google`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--allow-account-change": "comma-separated-list", "--credential-file": "path", "--gmail-nickname": "name", "--home": "dir", "--services": "comma-separated-list"}, "positionals": [], "stdin": null}
    Required options: ["--credential-file", "--home", "--services"]; positional arity: 0..0; stdin: forbidden
- `connect-google._rtx.interface.client-status` — Report whether the canonical Google Desktop OAuth client is missing, valid, invalid, or needs migration from plaintext, including whether its opaque client-secret reference resolves, without exposing secrets.
  - Caller: `connect-google`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--home": "dir"}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `connect-google._rtx.interface.connect-services` — Run one combined OAuth authorization and bind its new credential file through the fixed service-owner map.
  - Caller: `connect-google`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account-hint": "email", "--allow-account-change": "comma-separated-list", "--callback-port": "port", "--gmail-nickname": "name", "--home": "dir", "--no-open-browser": true, "--services": "comma-separated-list"}, "positionals": [], "stdin": null}
    Required options: ["--home", "--services"]; positional arity: 0..0; stdin: forbidden
- `connect-google._rtx.interface.install-client` — Validate a Google Desktop OAuth client JSON and atomically install a private canonical copy.
  - Caller: `connect-google`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--from-json": "client-json", "--home": "dir", "--replace": true}, "positionals": [], "stdin": null}
    Required options: ["--from-json"]; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `bootstrap-dispatcher-runtime.interface.repair-selected-packages@1` — Repair the core or one caller-owned package declaration in the exact dispatcher runtime without MCP.
- `connect-google.source.instructions-connect-services.interface.connect-services@1` — Install or reuse a Google Desktop OAuth client and hand selected Google services to their owning skills.
- `connect-google.source.instructions-create-client.interface.create-client@1` — Guide a user through creating and privately downloading a Google Desktop OAuth client for selected Famulus services.
<!-- END BLUEPRINT INTERFACES -->
Skill: connect-google

This is the shared router for Google OAuth-client preparation.

Before client inspection, authorization, or service binding, use the host-loaded
`bootstrap-dispatcher-runtime.interface.repair-selected-packages` procedure for
feature `connect-google` and its exact declaration `["keyring"]`. Complete the
initial fingerprint, pip and target checks, conditional repair, and byte-equal
final fingerprint; stop without Google or credential activity on any failure.
For each explicitly selected service, run that same procedure separately for
its fixed owner and exact declaration `["keyring"]` before invoking it: Drive is
`cloud-files`, Calendar is `online-calendar`, and Gmail is `email-client`. Do not
inspect or repair an unselected owner. A later satisfied declaration is a
no-install result, so shared keyring is installed at most once.

1. Use `connect-google._rtx.interface.client-status` before asking the user for a file.
2. If the stored client is valid, use `connect-google.interface.connect-services`.
3. If no valid client is installed and status reports legacy candidates, ask
   before importing one; when candidates differ, ask which one to use. A
   confirmed candidate routes to `connect-google.interface.connect-services`.
4. If no candidate is selected, explain that Famulus needs a Google Cloud
   project to register its Desktop OAuth client. Present the two options:
   - use the developer's experimental Google Cloud project; or
   - use their own Google Cloud project.
   Then ask whether the Google account they want to connect has been added by
   the developer to the experimental project's OAuth test-user list.
   - If yes, ask for the local path to the Desktop OAuth client JSON provided
     by the developer, then route it to `connect-google.interface.connect-services`.
     If the user was added but does not have the file, tell them to obtain the
     JSON from the developer; never request its contents.
   - If no, route to `connect-google.interface.create-client`. After the user
     creates their own project and downloads its Desktop client JSON, obtain
     its local path and continue through `connect-google.interface.connect-services`.
   - If the user is unsure, tell them to confirm with the developer; do not
     assume enrollment.

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

Owning a project removes that cap for its owner; using the developer's
experimental project is subject to it. `instructions/create-client.md` repeats
the second sentence at the configuration step; keep both copies identical.

Apply the same route to initial setup and reconnect requests. Recommend Drive,
Calendar, and Gmail while allowing the user to choose a subset. The connection
route creates one timestamped credential file and binds that exact path through
service-owned machine interfaces. Treat only `complete: true` as success; retry
an incomplete binding with `bind-credential-file` and the same file.

Never commit, publish, quote, or log the client JSON. Do not request, inspect,
or move service access tokens, refresh tokens, account records, or user data.
The deterministic coordinator invokes only the declared service-owned binders.

@./personal-preferences/default.md
