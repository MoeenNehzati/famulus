# Connect Google Design

**Date:** 2026-07-14
**Status:** Revised design awaiting review

## Purpose

Famulus currently requires users to configure Google OAuth independently for
cloud-files, g-calendar, and email-client. The service skills repeat parts of
the same Google Cloud setup, and a user who wants all three services must
manually place and reuse the same OAuth client configuration.

Create a dedicated `connect-google` skill as the shared Google-authentication
boundary. A user should be able to say "Connect Google" and be guided through
either using an existing Desktop OAuth client JSON or creating one. The skill
then recommends Drive, Calendar, and Gmail while allowing any subset, but the
corresponding service skills own and initiate their service connections.

This design improves both supported distribution paths:

- A known private-pilot user can receive the project owner's client JSON
  privately and connect without creating a Google Cloud project.
- A public GitHub user can create their own Google Cloud project and then use
  exactly the same connection workflow.

The client JSON must never be committed to the public Famulus repository.

## User Experience

The skill triggers on requests such as:

- "Connect Famulus to Google."
- "Set up Google services."
- "Reconnect Google Calendar."
- "Add another Gmail account."

The default LLM interface checks whether a canonical client configuration is
already installed. If none exists, it asks one routing question: whether the
user already has a Google OAuth Desktop client JSON.

- If the user has a JSON file, route to `connect-services`.
- If the user does not, route to `create-client`.
- After `create-client` produces a downloaded client JSON, route directly to
  `connect-services` without restarting the conversation.

The connection route recommends all three services and explicitly says that
the user may select only a subset. After the shared client is ready, each
selected service skill initiates its own authorization so that each token
receives only that service's scope.

## Skill And Interface Structure

```text
skills/connect-google/
  SKILL.md
  llm_interfaces/
    create-client.md
    connect-services.md
```

`SKILL.md` is a short router and owns policy shared by both routes:

- never publish or commit OAuth client credentials;
- never expose or move user access tokens between services;
- detect an already installed canonical client before asking for a file;
- route setup and reauthorization through the same workflow;
- preserve successful connections when another selected service fails.

`create-client` owns the Google Cloud Console procedure:

1. Select or create a Google Cloud project.
2. Configure an External audience.
3. Explain Testing versus In production behavior.
4. Add test-user email addresses when the app is in Testing.
5. Enable the Drive and Calendar APIs when those services are selected. The
   current Gmail integration uses IMAP/SMTP XOAUTH2 and requires the Gmail
   OAuth scope, not the Gmail REST API.
6. Register the current service scopes under Data Access.
7. Create and download a Desktop OAuth client JSON.
8. Hand the downloaded path to `connect-services`.

`connect-services` owns shared-client preparation and service handoff:

1. Locate an existing canonical client or ask for a client JSON path.
2. Validate the file before changing local state.
3. Install a private canonical copy.
4. Recommend Drive, Calendar, and Gmail and collect the selected subset.
5. Hand each selected service to its owning skill.
6. Let that service invoke `connect-google` when it needs the canonical client.
7. Report the handoffs without inspecting service accounts or service data.

The LLM interfaces own decisions and coordination. Generated blueprint blocks
own dispatcher syntax and argument descriptions; hand-authored Markdown must
not duplicate those invocation templates.

## Machine And Ownership Boundaries

`connect-google` owns validation and canonical storage of the shared OAuth
client configuration. It does not declare dispatcher dependencies on service
setup, account-management, verification, or operational interfaces.

It exposes two owner-facing machine interfaces:

- `client-status` inspects whether the canonical client exists and is valid,
  returning only non-secret status and client-type information;
- `install-client` validates an explicitly supplied file and atomically
  installs it as the canonical client.

Service skills may invoke these interfaces when authentication requires a
valid canonical client. OAuth token exchange and token storage remain inside
the calling service; no token crosses the `connect-google` interface.

The service skills retain their low-level machine interfaces:

- cloud-files owns Drive authorization, Drive credentials, and Drive checks;
- g-calendar owns Calendar authorization, Calendar credentials, and Calendar
  checks;
- email-client owns Gmail account registration, Gmail OAuth credentials, and
  IMAP/SMTP checks.

The service setup interfaces must not be deleted or exposed to
`connect-google`. The service skills invoke `connect-google` for shared client
preparation, then run their own setup and verification interfaces. What moves
out of those skills is only the duplicated Google Cloud Console tutorial;
service-specific account collection, OAuth exchange, credential storage,
verification, and recovery remain with each service.

`install-assistant-tools` remains responsible only for installation. Its
conversational Phase 2 offer may invoke `connect-google`, but the installer
must not duplicate Google setup logic or declare service dependencies merely
to describe the follow-up.

Cross-skill calls remain dispatcher-first and cwd-independent. Machine-call
direction is service skill to `connect-google`, never `connect-google` to a
service skill.

## Client Configuration Storage

After validation, install the client configuration at:

```text
~/.config/connect-google/client.json
```

Requirements:

- Store a regular private file rather than cross-platform symlinks.
- Restrict access to the current user where the platform supports file
  permissions.
- Write atomically so an interrupted import cannot destroy a working client.
- Validate that the input has the expected Google OAuth client structure and
  does not contain `access_token`, `refresh_token`, or generated service
  credential records. The normal OAuth endpoint field `token_uri` is not a
  user token and must remain valid input.
- Require a Desktop OAuth client for the supported public workflow.
- Do not delete the source download automatically.
- Do not place the file, its contents, or user tokens in logs or reports.

The canonical file is reusable for later reauthorization. Service setup
interfaces receive it through their declared inputs. Generated service
credentials remain in each service's existing private storage; the canonical
file never becomes a shared token store.

## Account Model

Version one intentionally matches current service capabilities:

- cloud-files supports one active Google Drive account;
- g-calendar supports one active Google Calendar account;
- email-client supports multiple named Gmail accounts.

Before replacing an existing Drive or Calendar connection, the workflow must
identify that an active credential exists and obtain confirmation. It must not
silently overwrite either single-account slot.

For Gmail, email-client asks for the email address and a local nickname before
authorization. A repeated run may add another nickname. If a nickname already
exists, email-client asks whether to update that account or choose a different
nickname. `connect-google` neither lists nor modifies those records.

The browser account picker determines which Google account authorizes each
service. The skill must not assume that all selected services use the same
Google account. It should state the intended account before opening each
authorization flow so the user can choose correctly.

Multi-account Drive and Calendar support is outside version-one scope.

## Authorization And Scope Model

The OAuth client identifies the Famulus installation to Google; it does not
grant access by itself. Each selected service performs its own browser
authorization and stores its own token.

The initial implementation preserves the scopes currently required by the
service implementations:

- Drive: `https://www.googleapis.com/auth/drive`
- Calendar: `https://www.googleapis.com/auth/calendar`
- Gmail IMAP/SMTP: `https://mail.google.com/`

The workflow must accurately explain these permissions before authorization.
Future scope reduction, including possible Drive app-data or file-scoped
storage, is separate work and must not be implied by this onboarding change.

For an External app in Testing, only registered test users can authorize and
refresh tokens can expire after seven days. The create-client route must state
that limitation. If Google reports that the selected account is not permitted,
the failure guidance should tell the user that the project owner must add the
exact account email under Test users.

## Detailed Data Flow

1. Route the user's request into `connect-google`.
2. Check for the canonical client configuration.
3. If it is absent, ask whether the user already has a client JSON.
4. If necessary, guide Cloud project creation and client download.
5. Validate the supplied client file without writing local state.
6. Atomically install the canonical private copy.
7. Present the recommended three-service selection with subset opt-out and
   hand each selection to its owning service skill.
8. For Drive:
   - detect an existing active credential;
   - confirm replacement when necessary;
   - cloud-files invokes `connect-google` to obtain the canonical client;
   - authorize through cloud-files;
   - run a non-mutating Drive connection check.
9. For Calendar:
   - detect an existing active credential;
   - confirm replacement when necessary;
   - g-calendar invokes `connect-google` to obtain the canonical client;
   - authorize through g-calendar;
   - list accessible calendars as the non-mutating check.
10. For each requested Gmail account:
    - email-client collects the email address and nickname;
    - email-client adds or intentionally updates the registry entry;
    - email-client invokes `connect-google` to obtain the canonical client;
    - authorize through email-client;
    - authenticate to IMAP and SMTP without sending mail.
11. Each service reports its own result; the conversational caller may combine
    those reports without giving `connect-google` access to service state.

Authorization is sequential because browser interaction is user-driven and
because later choices may depend on an earlier denial or account-selection
mistake. Successful earlier services remain connected if a later service
fails.

## Failure And Recovery Behavior

### Invalid client input

Reject malformed JSON, unsupported OAuth client types, missing required client
fields, and files containing token-like credential fields before modifying the
canonical client or any service state. Report the reason without printing
credential values.

### Existing canonical client

Reuse a valid stored client automatically. Replacing it requires an explicit
user choice because the replacement affects future reauthorization for every
Google service. Validate the new file before replacing the old one.

### OAuth denial or cancellation

Mark only that service as skipped or failed. Do not treat denial as consent to
retry immediately, and do not roll back other successful service connections.

### Test-user rejection

Explain that the exact Google account email must be present in the project's
test-user list. Distinguish this from an invalid client and from a Workspace
administrator policy block.

### Workspace policy block

Report the Google error as an organization-level restriction. Do not claim
that adding the account as a test user will override a Workspace administrator
policy.

### Existing single-account connection

Do not overwrite Drive or Calendar credentials without confirmation. An
approved replacement must be transactional: retain the old credential until
the new authorization and verification both succeed, then atomically replace
it. If a current service setup interface cannot provide that behavior, extend
that service-owned interface as part of implementation rather than accepting
credential loss on a failed replacement.

### Partial completion

The final report must distinguish:

- connected and verified;
- connected but verification failed;
- skipped by user choice;
- authorization denied;
- configuration or provider error.

It must include the next recovery action for every non-success state.

## Documentation Changes

Implementation should update:

- the generated skill index through the normal documentation generator;
- README or installation documentation with the prompt "Connect Google";
- install-assistant-tools Phase 2 wording to route the Google case to
  `connect-google` without copying its procedure;
- cloud-files, g-calendar, and email-client LLM guidance so they invoke
  `connect-google` for shared client preparation during setup or
  reauthorization;
- maintainer documentation only where a new validation or test surface needs
  to be named.

The public documentation must clearly distinguish private distribution of a
shared client JSON from public GitHub distribution. It must not tell users to
commit OAuth client credentials.

## Testing Strategy

### Unit tests

- valid Desktop client recognition;
- malformed JSON and missing-field rejection;
- non-Desktop client rejection;
- input containing `access_token` or `refresh_token` rejection without
  rejecting a normal `token_uri`;
- canonical atomic-write and replacement behavior;
- private-file permission behavior on supported platforms;
- service-selection and account-model decisions.

### Contract tests

- blueprint LLM interfaces match the router and interface files;
- declared service-to-connector dependencies resolve through dispatcher
  dry-runs;
- `connect-google` declares no service machine-interface dependencies;
- email account-management and smoke-test interfaces do not allow
  `connect-google` as a caller;
- service setup interfaces remain service-owned;
- no hand-authored Markdown duplicates generated dispatcher syntax;
- documentation and skill indexes regenerate cleanly.

### Integration tests

- no-client route hands a resulting file to the existing-client route;
- selected subsets are handed only to the selected service skills;
- existing Drive and Calendar connections require confirmation;
- multiple Gmail nicknames remain supported;
- one service failure preserves successful prior connections without making
  service state visible to `connect-google`;
- final summaries classify all outcomes correctly;
- a clean temporary home has no dependency on the developer's existing
  `client.json` or credentials.

### Live smoke test

With explicit operator approval, use a clean temporary home and an allowlisted
test account to verify:

1. client import;
2. browser authorization;
3. Drive check;
4. Calendar listing;
5. Gmail IMAP and SMTP authentication without sending mail.

Live tests must not run in ordinary CI and must never use or print committed
credentials.

## Non-Goals

- Publishing OAuth client credentials in the public repository.
- Removing Google verification requirements or the unverified-app user cap.
- Hosting a Famulus token broker or centralized user backend.
- Replacing Google OAuth with Pipedream, Nylas, or another third party.
- Supporting multiple simultaneous Drive or Calendar accounts.
- Redesigning Drive, Calendar, or Gmail operational APIs.
- Reducing the services' current OAuth scopes as part of this feature.
- Adding a new top-level `famulus` terminal CLI.

## Success Criteria

The design is successfully implemented when:

- a user can say "Connect Google" and reach the correct route without knowing
  individual service skill names;
- a private-pilot user with a supplied client JSON does not need Google Cloud
  Console access;
- a public user without a client JSON receives one canonical project-creation
  procedure and then joins the same connection flow;
- the user is encouraged to connect all three services but can choose any
  subset;
- one Drive account, one Calendar account, and multiple Gmail accounts work as
  specified;
- client configuration is stored privately and user tokens remain separated
  by their owning services;
- service skills invoke `connect-google` for shared authentication setup, and
  `connect-google` never invokes service account, setup, verification, or data
  interfaces;
- existing service skills no longer duplicate Google Cloud project setup;
- partial failures do not erase successful connections;
- all repository validators, focused tests, and documentation checks pass.
