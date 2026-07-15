# Prepare Google authentication

Install or reuse the canonical Desktop OAuth client. Recommend all three
services—Drive, Calendar, and Gmail—and tell the user that any subset is valid.

## Client preparation

Check `connect-google.machine.client-status`. If it is valid, reuse it. If it is
missing or invalid and reports legacy candidates, ask before importing one with
`connect-google.machine.install-client`. If multiple candidates differ, ask the
user which one to import. Otherwise obtain a local Desktop client JSON path and
use `connect-google.machine.install-client`. Replacing a different canonical
client requires explicit confirmation. Never expose file contents or tokens.

## Service-owned handoff

After the canonical client is ready, hand off every selected service to its
owning skill:

- Drive belongs to the Drive storage service's default LLM interface.
- Calendar belongs to the Calendar service's default LLM interface.
- Gmail accounts belong to the email service's default LLM interface.

The service skill owns account selection, OAuth exchange, refresh-token
storage, verification, recovery, and Google API use. It may invoke
`connect-google` again when it needs to confirm or replace the canonical client.

This interface does not list, add, update, inspect, or test service accounts.
It does not invoke any service machine interface and does not receive service
tokens or user data. Report only whether the shared client is ready and which
service-owned handoffs the user selected.

If Google rejects a Testing user during a later service-owned authorization,
the project owner must add that exact account email under Test users. A
Workspace administrator policy can still block authorization.

@../personal-preferences/connect-services.md
