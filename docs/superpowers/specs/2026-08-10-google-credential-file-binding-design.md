# Google Credential File Binding Design

## Goal

Make Google authorization reusable and explicit without a central credential-ID
binding layer. Each authorization creates a distinct credential file. Calendar,
Drive, and email accounts store the path of the credential file they use.

The workflow must not report successful setup while a selected service still
uses stale credentials.

## Current problem

`connect-google` currently creates a shared credential and returns an opaque
`credential_id`. Binding that credential to Calendar, Drive, or Gmail is a later,
LLM-mediated handoff. The authorization can therefore finish while a selected
service continues reading an older credential.

The current credential ID is also account-derived, so authorizing the same Google
account again replaces that account's previous registry record rather than
creating a distinct authorization artifact.

## Design

### One file per authorization

Every successful `connect-google` authorization creates a new immutable file:

```text
<Famulus config root>/connect-google/credentials/
  2026-08-10T14-52-10Z-<short-id>.json
```

The timestamp makes creation order visible; the short generated ID prevents
collisions. Reauthorizing the same account creates another file and never
overwrites an earlier one.

The file contains only the information required to refresh and validate the
credential:

- schema version and creation time;
- Google account identity;
- OAuth client ID and token endpoint;
- granted services and scopes; and
- references to secrets held by the approved secret store.

Raw client secrets, refresh tokens, and access tokens are never written to the
file. Access tokens remain ephemeral and are generated when a service makes a
request.

### Services bind to a path

Calendar, Drive, and each Gmail account store one `credential_file` path in their
own configuration. They read that file when obtaining a fresh access token.

Different services may point to different credential files. Different email
nicknames may also point to different files. No symlink or shared mutable access
file is needed.

Each service-owned binding interface:

1. accepts a credential-file path;
2. verifies that the file exists and has the service's required scopes;
3. stores the normalized absolute path without copying credential contents; and
4. verifies live service access after binding.

### `connect-google` completes the handoff

`connect-google` remains responsible for obtaining consent and creating the
credential file. After authorization, it hands the returned path to every service
that was both selected and granted.

The owning service performs its own binding and verification. `connect-google`
coordinates those declared public interfaces but does not edit service
configuration directly.

Setup is complete only when every selected, granted service is bound and verified.
If a service cannot be bound, the credential file remains valid and the result
identifies that service as incomplete. A retry reuses the same file rather than
opening another consent flow.

For Gmail, the user must identify the target email nickname before binding. No
nickname is guessed.

## Replacement behavior

A new authorization updates only the services selected for that authorization.
Unselected services keep their existing paths. Existing credential files remain
unchanged until the user explicitly removes or revokes them.

Replacing a service binding with a credential for a different Google account
requires confirmation. Rebinding the same account may proceed without an
additional confirmation.

## Compatibility

Existing `credential_id` and legacy OAuth configurations remain readable during
migration. They are replaced only when the corresponding service is successfully
bound to a generated credential file. There is no bulk migration or silent
fallback from a failed file binding to stale credentials.

## Errors and reporting

The authorization result reports:

- the generated credential-file path;
- requested, granted, and denied services;
- bound and verified services; and
- incomplete services with their errors.

Missing files, insufficient scopes, account mismatches, and failed live checks are
explicit failures. A service must not silently fall back to an older credential
after a `credential_file` has been configured.

## Verification

Focused tests must establish that:

1. repeated authorization of the same account creates distinct files;
2. generated files contain no raw secrets or access tokens;
3. each selected service stores the returned path and verifies live access;
4. unselected services are unchanged;
5. different services and email nicknames can use different files;
6. partial handoff is reported as incomplete rather than successful;
7. retrying a failed handoff reuses the existing file; and
8. legacy bindings continue to work until explicitly replaced.

Graph and authorization tests must also verify that cross-service calls use only
declared public interfaces and preserve each service's ownership of its own
configuration and API verification.

## Non-goals

- Managing or automatically deleting old credential files.
- Supporting multiple named Calendar or Drive profiles.
- Storing raw OAuth secrets in ordinary files.
- Changing service behavior unrelated to Google credential selection.
