# Cross-Platform Transparent Google Authorization Design

**Date:** 2026-08-10
**Status:** Approved after revision 4 semantic and simplicity audits
**Supersedes:** Only the browser-launch, loopback-callback, endpoint-trust, and
credential-publication behavior in `2026-07-14-connect-google-design.md`

## Goal

Use one small Python authorization flow on Windows, macOS, and Linux. On a
desktop it attempts the default local browser and always provides a manual URL.
On a remote host with a usable secret backend, the same flow works through an
operator-created, same-port SSH tunnel.

The implementation is deliberately linear: validate, bind one IPv4 loopback
listener, print instructions, optionally start one isolated browser helper,
wait for one valid callback, exchange, identify, and publish.

## Requirements

1. Use one host-neutral Python path. Do not call `xdg-open`, macOS `open`, or
   Windows `start` directly.
2. Print a manual authorization URL before attempting the default browser.
3. Support `--no-open-browser` and `--callback-port`.
4. Use `127.0.0.1` and the same selected port in the listener, redirect URI,
   authorization request, token exchange, diagnostics, and SSH instructions.
5. Apply a five-minute monotonic deadline to consent and callback receipt.
   Apply separate finite timeouts and response-size bounds to Google requests.
6. Emit live JSONL diagnostics on stderr. On success, emit exactly one JSON
   result on stdout; on failure, leave stdout empty.
7. Keep the authorization URL user-private. Never emit codes, a verifier,
   client secrets, access tokens, refresh tokens, or provider response bodies.
8. Publish registry metadata only after the new refresh secret exists. A
   publication failure must leave the previously published credential usable;
   cleanup is attempted only after the visible registry state is identified.
9. Preserve the current service-selection and partial-grant semantics.

## Non-Goals

- Mobile, Chrome-app, UWP, device-flow, or deprecated out-of-band flows.
- A hosted callback broker or automatic creation of the operator's SSH tunnel.
- IPv6 support without evidence that a supported host lacks IPv4 loopback.
- Automatic Google Cloud client provisioning or enforcement of an owner's
  per-platform client-ID policy.
- A hard wall-clock guarantee across OS DNS, OS keyring, or provider behavior.
- A transaction spanning the filesystem and an OS keyring.
- Changes to Drive, Calendar, or Gmail data operations or scope definitions.

## User Flow

### Desktop

1. The operator selects the existing recommended service set or an explicit
   subset.
2. Authorization validates the installed Desktop client and reads its referenced
   client secret before opening a socket.
3. It binds `127.0.0.1:P`, prints the URL and exact listener details, and then
   starts an isolated helper that asks Python's `webbrowser` module to open the
   default browser.
4. If no browser appears, the operator opens the already printed URL.
5. The exact loopback callback is validated. The command exchanges the code,
   identifies the account, publishes the credential, and returns one JSON result.

### Headless remote host

Successful remote authorization requires a supported secret backend on the
remote host that can read the installed client secret and store a refresh token
in that login session. Authorization preflights readability of the installed
secret before binding. A missing, locked-on-read, null, or failing-on-read
backend produces a stable actionable failure before any browser URL is created.
This read preflight cannot prove a later write will succeed; a write failure is
handled as a credential-publication failure without exposing a broken record.
This design does not add a new headless secret backend.

Run remotely:

```text
dispatcher --caller-skill connect-google \
  connect-google._rtx.interface.authorize-services \
  --services drive,calendar,gmail --no-open-browser
```

The command binds remote port `P` and prints:

```text
ssh -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:P:127.0.0.1:P user@remote-host
```

In another local terminal, the operator replaces only `user@remote-host`, runs
the tunnel, and opens the printed URL locally. If local port `P` is occupied,
the operator stops the attempt, chooses a port free on both hosts, and reruns
authorization with `--callback-port Q`. The remote process cannot create the
local tunnel because it does not own the local SSH configuration or credentials.

Without a usable remote secret backend, only the callback transport is viable;
successful credential publication is not claimed.

## Installed Client Contract

Google Cloud provisioning and any per-platform client-ID policy remain owner
responsibilities. Runtime uses the one canonical Google Desktop client installed
on the current host. It does not infer an operating system from the client,
store a platform tag, require `project_id`, or maintain a client-ID manifest.
Raw `sys.platform` continues to be passed unchanged to the existing path
resolver.

Before binding, authorization requires:

- a regular canonical file containing a top-level `installed` object;
- a nonempty `client_id` and `client_secret_ref`;
- no plaintext client secret or token field in the canonical file; and
- successful lookup of the referenced client secret through a usable backend.

An existing redacted canonical file is valid without migration when its secret
reference resolves. A plaintext canonical file returns `needs-migration` and
the exact remediation command:

```text
dispatcher --caller-skill connect-google \
  connect-google._rtx.interface.install-client \
  --from-json PRIVATE_DOWNLOADED_CLIENT.json --replace
```

The private source file must remain operator-supplied; the command never writes
its plaintext secret into the canonical file. The canonical redacted file is
written with POSIX mode `0600` where POSIX mode bits apply. On Windows it is
stored under the current user's configuration directory; this design does not
claim an ACL guarantee that the implementation does not provide.

## Trusted Google Endpoints

Downloaded endpoint fields are compatibility data, not network policy. Both
legacy and current Google Desktop-client exports remain installable. Runtime
ignores their authorization and token endpoint values and uses only:

- authorization: `https://accounts.google.com/o/oauth2/v2/auth`
- token: `https://oauth2.googleapis.com/token`
- UserInfo: `https://openidconnect.googleapis.com/v1/userinfo`

Refresh also uses the pinned token constant. Legacy registry `token_uri` fields
remain readable but are ignored. Google HTTP operations reject redirects, cap
success and error bodies at 64 KiB, parse only JSON objects, and never expose a
raw response body. Rejecting redirects prevents an authorization header or
secret-bearing POST from moving to another origin.

## Loopback And Browser Contract

Authorization binds only `127.0.0.1`, using port `0` unless
`--callback-port P` is supplied. Bind collisions, permissions errors, and an
unavailable IPv4 loopback fail clearly; they do not silently select a different
address. The redirect URI is exactly `http://127.0.0.1:P/`, matching Google's
documented numeric loopback form. The listener never uses `localhost`, a
wildcard, `SO_REUSEADDR`, or parallel listeners.

Each attempt creates fresh high-entropy state and a PKCE verifier/challenge.
The authorization URL is printed before browser launch. The parent starts a
small Python helper process with stdin, stdout, and stderr isolated: the URL is
sent through its stdin, and its stdout/stderr are `DEVNULL`. The helper does
only one `webbrowser.open()` call and exits `0` for true, `1` for false, or `2`
for an exception. Passing the URL over stdin keeps it out of the helper's
command line. The parent may report only that sanitized exit class.

The helper cannot write into the command's machine streams. A blocked helper
cannot delay callback handling: the parent terminates it after callback receipt,
failure, or timeout and does not wait indefinitely for termination. Under
`--no-open-browser`, no helper is created. Manual opening is always the fallback;
browser outcome never terminates authorization.

## Callback And Timeout Contract

The five-minute monotonic deadline covers consent and callback receipt. A small
socket loop, rather than a general HTTP server, recomputes remaining time before
each accept and each `recv`. An accepted connection gets a timeout no greater
than the remaining global deadline. Request-line and header input is read only
until `\r\n\r\n`, capped at 16 KiB, with the monotonic deadline checked after
every read. Silent and slow-drip clients therefore cannot extend the global
deadline. Only `GET /` can terminate the callback phase. A terminal request has
exactly one matching `state` and exactly one of:

- one nonempty `code`; or
- one nonempty Google `error`.

Wrong paths, methods, states, blank values, duplicate values, code-plus-error,
favicon requests, and unrelated traffic receive static sanitized responses and
do not consume the attempt. A valid Google denial ends the attempt with an
allowlisted error code. A valid code is accepted once; the listener closes
before exchange.

Response HTML has no external resources, echoes no query values, and sets
`Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and a restrictive
Content Security Policy.

Token exchange and UserInfo are separate from the callback deadline. Each uses
a 30-second socket timeout, redirect rejection, a 64 KiB body cap, and bounded
JSON/error parsing. These controls prevent application-controlled unbounded
reads, but they are not advertised as a hard wall-clock bound across DNS or all
OS networking behavior.

## Exact Diagnostic Contract

After successful dispatch, the dispatcher CLI lets the child's stdout and
stderr inherit directly so records are visible while authorization runs; it
does not capture and replay them after exit. Programmatic dispatcher calls
retain their existing captured output behavior. Dispatcher warnings and
pre-launch failures retain their existing formats and are outside the OAuth
child sequence.

The authorization child authors every byte in its own stream sequence. Its
stderr records are flushed JSON Lines; its stdout is empty until the single
successful result. Every diagnostic contains exactly `schema_version`, `event`,
`status`, plus the fields listed below. `schema_version` is the JSON integer `1`.

| Event | Status | Additional fields and JSON types | Emission condition |
|---|---|---|---|
| `oauth.client_ready` | `ready` | `services`: array of strings | once after client and secret-read preflight |
| `oauth.listener_ready` | `ready` | `address`: string equal to `127.0.0.1`; `port`: integer 1..65535; `callback_deadline_seconds`: integer | once after bind |
| `oauth.authorization_url` | `available` | `url`: string | once after URL construction |
| `oauth.ssh_tunnel` | `available` | `command`: string | once after the URL, on desktop and remote runs |
| `oauth.browser_launch` | `disabled`, `started`, or `failed` | none | once after tunnel instructions |
| `oauth.browser_result` | `opened`, `unavailable`, or `error` | none | at most once, only if a started helper exits before the terminal event |
| `oauth.awaiting_callback` | `waiting` | none | once before the first accept |
| `oauth.callback_received` | `code` or `denied` | none | once after a valid terminal callback |
| `oauth.token_exchange` | `started` | none | once before exchange after a code callback |
| `oauth.userinfo` | `started` | none | once before UserInfo after exchange |
| `oauth.credential_publish` | `started` | none | once before secret/metadata publication |
| `oauth.complete` | `authorized` or `partial_grant` | `granted_services`: array of strings; `denied_services`: array of strings; `warnings`: array of strings | exactly once on success |
| `oauth.failed` | `error` | `phase`: string enum below; `code`: string enum below | exactly once on failure |

The nonterminal events occur in table order except that `oauth.browser_result`
may occur any time after `oauth.browser_launch` and before the terminal event.
An event is absent when failure occurs before its condition. Exactly one of
`oauth.complete` and `oauth.failed` is emitted, it is the last OAuth stderr
record, and the child emits no later diagnostics. Browser helper status is
polled and emitted only before the terminal event; a later helper exit is
silent. There is no cross-stream ordering guarantee between the last OAuth
stderr record and the success object on stdout.

`phase` is one of `client`, `listener`, `callback`, `token_exchange`,
`userinfo`, `account_check`, or `credential_publish`. Stable failure `code` is
one of `client_invalid`, `secret_store_unavailable`, `listener_bind_failed`,
`callback_timeout`, `access_denied`, `token_exchange_failed`,
`userinfo_failed`, `account_mismatch`, `no_service_scope_granted`,
`credential_publish_failed`, `publication_uncertain`, or `internal_error`.
Completion warnings are `registry_durability_warning` and
`secret_cleanup_warning`.

The full manual URL is sensitive because it embeds state. It may occur only as
the `url` value in the single `oauth.authorization_url` record; state is never
emitted as a separate field or in any other output. The command does not persist
the URL. Callers and blueprints mark the entire stderr diagnostic stream
user-private and must not persist or log the URL field.

Success stdout is one JSON object containing only `schema_version` (the integer
`1`), `account`, `credential_id`, `requested_services`, `granted_services`, and
`denied_services`, followed by a newline. Failure stdout is empty.

## Credential Publication And Compatibility

`credential_id` remains `google:<subject>`. Registry schema 2 adds
`refresh_secret_ref` to newly published records. A schema-1 or schema-2 record
without that field remains readable by deriving the legacy reference
`<credential_id>:refresh-token`. Legacy `token_uri` is accepted as ignored
compatibility data; all refreshes use the pinned token endpoint.

New refresh references have exactly the form
`google-refresh:<32 lowercase hexadecimal characters>`. A prior reference is
eligible for cleanup only if it has that generated form or is exactly the
legacy reference derived for the replaced credential. Before clearing it, the
publisher derives or reads the reference for every remaining registry record
and confirms none uses the candidate. Unknown or malformed explicit references
are never cleared automatically.

Publication is atomic only at the metadata visibility boundary:

1. Generate a unique refresh-secret reference for this authorization.
2. Store the new refresh token under that reference.
3. Under the existing registry lock, reload the registry and remember the
   previous record and its explicit or derived secret reference.
4. Publish the complete schema-2 record using the shared secure atomic JSON
   writer. Preserve unrelated and legacy records.
5. If publication raises, reread under the lock. If the previous record is still
   visible, clear the new reference best-effort and fail. If the exact new record
   is visible, retain both old and new secrets, skip cleanup, and complete with
   `registry_durability_warning`. If the state cannot be read or identified,
   retain both secrets and fail with `publication_uncertain`.
6. Only when the atomic writer returns normally, clear the validated,
   now-unreferenced previous reference best-effort when it differs from the new
   reference. Cleanup failure adds `secret_cleanup_warning`; it never
   invalidates the published record.

A crash may leave an unreachable orphan secret, but a registry record never
points to the new secret before it exists. Cleanup failure is reported through
the exact completion warning above and does not roll back a successfully
published usable credential. The common credential blueprint describes this as
atomic publication, not cross-backend atomicity.

If no selected service scope was granted, publish nothing. A partial nonempty
grant is published and reported; only granted service owners bind it.

## Scope And Ownership Boundaries

The existing instruction to recommend Drive, Calendar, and Gmail while allowing
an explicit subset is unchanged. `connect-google` owns combined consent and
credential publication. Service skills retain their account configuration,
provider checks, opaque credential binding, and data operations.

## Implementation Surface

Expected changes are limited to:

- `skills/connect-google/_rtx/_loopback_oauth.py`
- one small isolated browser-helper source beside it
- `skills/connect-google/_rtx/_client_config.py`
- connect-google blueprints, instructions, and focused tests
- `src/officina/common/google_credentials.py`, its blueprint, and focused tests
- `src/officina/dispatcher/cli.py` and CLI-focused tests

Programmatic dispatcher capture, service-owned OAuth fallbacks and operations,
installer architecture, and unrelated standards remain unchanged.

## Required Tests

- Observe a diagnostic through dispatcher CLI while the child is still alive;
  prove programmatic dispatch still captures by default.
- Assert successful stdout is exactly one JSON object and failure stdout empty.
- Assert exact diagnostic fields, statuses, order, cardinality, and terminal
  silence, including a browser helper that returns, raises, or blocks.
- Assert authorization's blueprint declares the schema-complete interaction:
  `mode: interactive`, `channel: tty`, and `unattended_outcome: failed`, plus
  the user-private direct stderr JSONL surface.
- Assert a sentinel state occurs only inside the one authorization URL field;
  code, verifier, secrets, tokens, and provider bodies occur nowhere.
- Accept legacy/current client endpoint fields but prove runtime never contacts
  them; accept absent `project_id`; reject plaintext canonical secrets.
- Fail an unavailable/missing client secret backend before socket creation.
- Prove numeric IPv4, port `0`, explicit-port collision, redirect `...:P/`, and
  exact same-port SSH rendering.
- Prove valid code/denial callbacks terminate; every malformed, duplicate,
  wrong-path, wrong-method, wrong-state, and unrelated request does not.
- Prove no request, a silent connection, and a byte-at-a-time slow-drip client
  all reach the same monotonic callback deadline; reject input over 16 KiB.
- Prove browser true, false, exception, blocked, and disabled cases cannot write
  command streams or delay callback handling.
- Prove redirects are rejected, network timeouts are finite, and success/error
  bodies exceeding 64 KiB are rejected for exchange, UserInfo, and refresh.
- Fault-inject secret write, registry load/write/replace, and cleanup. No normal
  failure exposes a broken credential; replacement preserves the old credential
  until new metadata publication succeeds.
- Fault after replace and after directory synchronization; distinguish old,
  exact-new, and unreadable/ambiguous registry states before any secret cleanup;
  exact-new after a raised write retains both old and new secrets.
- Never clear malformed, unknown, or still-referenced refresh-secret references.
- Load schema-1 legacy records through derived secret references; publish schema
  2 without dropping unrelated legacy records; ignore legacy `token_uri`.
- Preserve zero-grant, partial-grant, account-hint, and service-subset behavior.
- Run focused and portability suites on Linux, Windows, and macOS; add opt-in
  native browser/keyring desktop and disposable same-port SSH smoke tests.

## Acceptance Criteria

1. One implementation passes deterministic Windows, macOS, and Linux path and
   process tests; native smoke tests certify supported host environments.
2. Desktop browser failure leaves an immediately usable manual URL.
3. A remote host with a usable secret backend completes through the exact
   printed same-port tunnel.
4. Consent/callback waits and application-controlled network reads are bounded
   according to the explicit contracts above.
5. Diagnostics are live, schema-stable, and expose only the one documented
   user-private URL field.
6. Exact callback validation, pinned endpoints, PKCE, and state checks hold.
7. Metadata publication never exposes a new record before its secret exists,
   and legacy credentials remain usable.
8. Focused, full, and portability tests are green except separately documented
   pre-existing external-distribution failures.
9. No unrelated dirty state is modified.
