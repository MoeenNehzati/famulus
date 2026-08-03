# Prepare Google authentication

Install or reuse the canonical Desktop OAuth client. Recommend all three
services—Drive, Calendar, and Gmail—and tell the user that any subset is valid.

## Client preparation

Check `connect-google.interface.client-status`. If it is valid, reuse it. If it is
missing or invalid and reports legacy candidates, ask before importing one with
`connect-google.interface.install-client`. If multiple candidates differ, ask the
user which one to import. Otherwise obtain a local Desktop client JSON path and
use `connect-google.interface.install-client`. Replacing a different canonical
client requires explicit confirmation. Never expose file contents or tokens.

## Combined authorization, then service-owned handoff

After the canonical client is ready, request one combined grant for every
selected service with `connect-google.interface.authorize-services`, passing
all selected services together. This opens a single consent screen covering
the full scope union instead of a separate OAuth round trip per service, and
returns one opaque `credential_id` plus which services were actually granted
versus denied.

Hand off that `credential_id` to each granted service's owning skill so it
can call its own `use-google-credential` interface:

- Drive belongs to the Drive storage service's default LLM interface.
- Calendar belongs to the Calendar service's default LLM interface.
- Gmail accounts belong to the email service's default LLM interface.

The service skill owns account selection, verification, recovery, and Google
API use, but no longer performs its own independent OAuth exchange -- it
consumes the credential this interface already authorized. A service skill
may invoke `connect-google` again when it needs to confirm or replace the
canonical client, or when the user wants to add a service that was denied or
never requested in the original combined grant.

This interface does not list, add, update, inspect, or test service accounts.
It does not invoke any service machine interface and does not receive service
tokens or user data beyond the opaque credential_id. Report only whether the
shared client is ready, the combined-authorization result, and which
service-owned handoffs the user selected.

If Google rejects a Testing user during a later service-owned authorization,
the project owner must add that exact account email under Test users. A
Workspace administrator policy can still block authorization.

@../personal-preferences/connect-services.md
