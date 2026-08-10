# Cross-Platform Transparent Google Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blocking, opaque Google desktop OAuth path with one
cross-platform flow that exposes a manual URL, safely attempts the local
browser, supports same-port SSH forwarding, pins Google endpoints, and
publishes backward-compatible credentials safely.

**Architecture:** Keep product orchestration in connect-google and shared
credential/HTTP policy in `officina.common.google_credentials`. A small browser
helper isolates `webbrowser.open()` from the OAuth machine streams; a bounded
socket parser handles only the loopback callback subset needed by OAuth. The
dispatcher CLI inherits child streams while the programmatic dispatcher keeps
capturing by default.

**Tech Stack:** Python 3.11+ standard library (`socket`, `subprocess`,
`urllib`, `secrets`, `json`), Officina Python machine interfaces, shared secret
store and atomic OAuth JSON writer, schema-version-6 blueprints, pytest.

## Global Constraints

- Approved design:
  `docs/superpowers/specs/2026-08-10-cross-platform-transparent-google-auth-design.md`.
- Use one Python implementation on Windows, macOS, and Linux; pass raw
  `sys.platform` to the existing path resolver.
- Bind only `127.0.0.1`; default port `0`; redirect URI exactly
  `http://127.0.0.1:P/`.
- The five-minute monotonic deadline covers consent and callback receipt;
  Google requests use a 30-second timeout, no redirects, and a 64 KiB cap.
- The authorization URL may appear only in the user-private
  `oauth.authorization_url.url` field. No other secret-bearing value appears on
  stdout, stderr, HTML, exceptions, or persisted metadata.
- Keep `credential_id = google:<subject>` and load schema-1 registries.
- Preserve recommended-all/allow-subset and partial-grant behavior.
- Use `officina.common.secret_store`; never import `keyring` from skill code.
- Edit authored blueprint sources, then synchronize generated blocks; never
  hand-edit generated sections.
- Every production behavior begins with a focused failing test and an observed
  expected failure. Fix regressions before starting the next task.
- Use pytest fixtures for repeated expensive or stateful setup (secret backends,
  installed clients, response factories, loopback listeners, child processes,
  and cleanup), parametrization for input/error matrices, and built-in
  `tmp_path`, `monkeypatch`, `capsys`/`capfd`, and skip/mark facilities at their
  natural boundaries. Keep one-off literal inputs inline so failures remain
  transparent and easy to debug.
- Do not commit or push; the user has not authorized a Git commit.
- The two known full-suite `install-assistant-tools` marketplace failures are
  pre-existing external distribution drift and remain out of scope.

---

### Task 1: Pinned, bounded Google HTTP boundary

**Files:**

- Modify: `src/officina/common/google_credentials.py`
- Modify: `tests/test_officina_google_credentials.py`
- Modify: `src/officina/common/blueprints/google-credentials.yaml`

**Interfaces:**

- Produces constants `GOOGLE_AUTHORIZATION_URL`, `GOOGLE_TOKEN_URL`, and
  `GOOGLE_USERINFO_URL`.
- Produces `_open_google_json(request, *, urlopen=None, timeout_s=30.0) -> dict`.
- Preserves public `exchange_authorization_code(...)` and
  `refresh_access_token(...)`. Exchange adds optional
  `client_secret_ref: str | None = None`; `None` derives the legacy
  `oauth-client:<client_id>:client-secret` key. The legacy `token_uri`
  argument/field remains accepted but never selects a network target.

- [ ] **Step 1: Add failing endpoint and response-boundary tests.**

  Add literal assertions based on observed requests, not source text:

  ```python
  def test_exchange_ignores_client_token_uri_and_uses_pinned_endpoint(tmp_path):
      backend = FakeSecretBackend()
      backend.store("connect-google", "custom-client-secret-reference", "secret")
      seen = []

      def urlopen(request, *, timeout):
          seen.append((request.full_url, timeout))
          return FakeResponse({"access_token": "at", "refresh_token": "rt", "scope": "openid email"})

      exchange_authorization_code(
          client_id="abc", code="code", code_verifier="verifier",
          redirect_uri="http://127.0.0.1:43123/",
          token_uri="https://attacker.invalid/token", urlopen=urlopen,
          client_secret_ref="custom-client-secret-reference",
          secret_backend=backend,
      )
      assert seen == [("https://oauth2.googleapis.com/token", 30.0)]
  ```

  Add sibling tests proving refresh ignores registry `token_uri`, a redirect is
  rejected by the production opener, response/error input over 65,536 bytes is
  rejected without echoing the body, non-object JSON is rejected, and the
  request timeout is finite. Store the client secret under a non-derived
  reference and prove exchange uses that exact reference; separately prove an
  omitted reference preserves the legacy derived-key behavior.

- [ ] **Step 2: Run the new HTTP tests and observe RED.**

  Run:

  ```text
  pytest -q tests/test_officina_google_credentials.py -k 'pinned or redirect or bounded or oversized'
  ```

  Expected: failures show attacker-supplied `token_uri` is contacted, no timeout
  is passed, and reads are unbounded.

- [ ] **Step 3: Add the minimal shared HTTP implementation.**

  Implement constants and a no-redirect opener. Read at most 65,537 bytes so an
  oversized response is distinguishable from an exact-limit response:

  ```python
  GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
  GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
  GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
  GOOGLE_HTTP_TIMEOUT_S = 30.0
  GOOGLE_HTTP_MAX_BODY_BYTES = 64 * 1024

  class _RejectRedirects(urllib.request.HTTPRedirectHandler):
      def redirect_request(self, req, fp, code, msg, headers, newurl):
          raise GoogleCredentialError("Google endpoint redirect refused")

  def _default_urlopen(request, *, timeout):
      return urllib.request.build_opener(_RejectRedirects()).open(request, timeout=timeout)

  def _read_bounded_json(response) -> dict:
      data = response.read(GOOGLE_HTTP_MAX_BODY_BYTES + 1)
      if len(data) > GOOGLE_HTTP_MAX_BODY_BYTES:
          raise GoogleCredentialError("Google endpoint response exceeded 65536 bytes")
      try:
          payload = json.loads(data)
      except (UnicodeDecodeError, json.JSONDecodeError) as exc:
          raise GoogleCredentialError("Google endpoint returned invalid JSON") from exc
      if not isinstance(payload, dict):
          raise GoogleCredentialError("Google endpoint returned a non-object response")
      return payload
  ```

  Convert `HTTPError` and `URLError` to stable, sanitized domain errors. Do not
  include exception bodies or provider URLs. Pass the timeout as a keyword to
  injected openers.

- [ ] **Step 4: Run the focused HTTP tests and the existing credential suite.**

  ```text
  pytest -q tests/test_officina_google_credentials.py
  ```

  Expected: PASS with all fake openers updated to accept `timeout` and all
  endpoint/error assertions green.

### Task 2: Registry schema 2 and publication-state safety

**Files:**

- Modify: `src/officina/common/google_credentials.py`
- Modify: `tests/test_officina_google_credentials.py`
- Modify: `src/officina/common/blueprints/google-credentials.yaml`

**Interfaces:**

- Extend `GoogleCredentialRef` with `refresh_secret_ref: str` and
  `publication_warnings: tuple[str, ...] = ()`.
- Preserve `store_google_credential(...) -> GoogleCredentialRef` and
  `load_credential(...) -> GoogleCredentialRef`.
- New references use `google-refresh:<uuid4 hex>`; legacy records derive
  `<credential_id>:refresh-token`.
- Add `GoogleCredentialPublicationUncertain(GoogleCredentialError)` as the
  typed signal for an unreadable/ambiguous post-write registry state. Task 5
  maps this type to phase `credential_publish`, code `publication_uncertain`;
  no caller parses exception text.

- [ ] **Step 1: Add failing compatibility and fault-injection tests.**

  Add tests with literal schema-1 fixtures and a backend that actually deletes:

  ```python
  def test_load_schema_1_derives_refresh_reference_and_ignores_token_uri(tmp_path):
      write_registry(tmp_path, {
          "schema_version": 1,
          "credentials": {"google:sub1": {
              "subject": "sub1", "account": "user@example.test",
              "client_id": "abc", "token_uri": "https://attacker.invalid/token",
              "granted_scopes": ["openid", "email"],
          }},
      })
      ref = load_credential("google:sub1", home=tmp_path, platform="linux")
      assert ref.refresh_secret_ref == "google:sub1:refresh-token"
  ```

  Add cases for: schema-2 round trip; preserve unrelated legacy records; initial
  registry read/load failure after the new secret is stored clears the new
  secret best-effort; new secret-store failure leaves registry unchanged;
  writer fails before replace with an existing exact-old record and attempts
  new-secret cleanup; writer fails before replace with no previous record and
  leaves the registry absent while attempting new-secret cleanup; writer raises
  after replace with exact-new visible both with and without a previous record,
  retaining every potentially needed secret plus
  `registry_durability_warning`; unreadable or ambiguous post-error state raises
  `GoogleCredentialPublicationUncertain` and retains both; normal success clears
  only an unreferenced valid previous ref; cleanup failure yields
  `secret_cleanup_warning`; malformed/shared refs are never cleared.

- [ ] **Step 2: Run the new publication tests and observe RED.**

  ```text
  pytest -q tests/test_officina_google_credentials.py -k 'schema_1 or schema_2 or publication or refresh_reference or cleanup'
  ```

  Expected: current deterministic keying and pre-publication secret replacement
  violate the new assertions.

- [ ] **Step 3: Implement schema-2 loading and state-aware publication.**

  Add pure helpers:

  ```python
  _GENERATED_REFRESH_REF = re.compile(r"^google-refresh:[0-9a-f]{32}$")

  def _refresh_ref(credential_id: str, record: Mapping[str, object]) -> str:
      value = record.get("refresh_secret_ref")
      if value is None:
          return f"{credential_id}:refresh-token"
      if not isinstance(value, str) or not value:
          raise GoogleCredentialError("credential has invalid refresh_secret_ref")
      return value

  def _record_for(..., refresh_secret_ref: str) -> dict[str, object]:
      return {
          "subject": subject, "account": account, "client_id": client_id,
          "granted_scopes": sorted(granted_scopes),
          "refresh_secret_ref": refresh_secret_ref,
      }
  ```

  Inject `registry_writer=write_oauth_json` only as a keyword-only test seam.
  Store the unique new secret, publish under the existing lock, reread after any
  writer exception, and classify exact-old/exact-new/ambiguous before cleanup.
  Only a normal writer return permits previous-secret cleanup.

- [ ] **Step 4: Use each loaded ref for refresh and rerun tests.**

  `refresh_access_token` must require `ref.refresh_secret_ref`, never rebuild its
  own key. Run:

  ```text
  pytest -q tests/test_officina_google_credentials.py
  ```

  Expected: PASS, including existing concurrent writers and client rotation.

### Task 3: Canonical client validation and secret readability preflight

**Files:**

- Modify: `skills/connect-google/_rtx/_client_config.py`
- Modify: `skills/connect-google/_rtx/tests/test_client_config.py`
- Modify: `skills/connect-google/_rtx/blueprints/rtx-client-config.yaml`

**Interfaces:**

- `validate_client_payload` continues validating private downloaded files.
- `client_status(home, *, secret_backend=None)` returns `valid`, `missing`,
  `invalid`, or `needs-migration` without exposing a secret or backend detail.
- `load_authorization_client(home, *, platform=sys.platform,
  secret_backend=None) -> dict[str, str]` returns only `client_id` and
  `client_secret_ref` after the read preflight.

- [ ] **Step 1: Add failing canonical-status tests.**

  Cover: current redacted file plus resolving ref is valid; missing ref,
  plaintext canonical secret, token fields, symlink/non-regular path, and
  fail/null backend are rejected before any listener factory can be called;
  absent `project_id` remains valid; legacy/current/malicious endpoint fields
  are accepted as ignored input and never returned as network policy.

- [ ] **Step 2: Run the new client tests and observe RED.**

  ```text
  pytest -q skills/connect-google/_rtx/tests/test_client_config.py
  ```

  Expected: current `client-status` accepts plaintext canonical clients and
  does not check the referenced secret.

- [ ] **Step 3: Implement separate private-source and canonical validation.**

  Keep downloaded-file validation responsible for requiring `client_secret`.
  Add canonical validation that requires `client_secret_ref`, rejects plaintext
  `client_secret`, calls `secret_store.require`, and returns no endpoint values.
  Map plaintext canonical state to `needs-migration` with the exact
  `install-client --from-json ... --replace` remediation described by the spec.

- [ ] **Step 4: Run client and common credential tests.**

  ```text
  pytest -q skills/connect-google/_rtx/tests/test_client_config.py tests/test_officina_google_credentials.py
  ```

  Expected: PASS; existing downloaded client formats remain installable.

### Task 4: Bounded loopback callback and isolated browser helper

**Files:**

- Create: `skills/connect-google/_rtx/_browser_helper.py`
- Modify: `skills/connect-google/_rtx/_loopback_oauth.py`
- Modify: `skills/connect-google/_rtx/blueprint.yaml`
- Modify: `skills/connect-google/_rtx/tests/test_authorize_services.py`
- Modify: `skills/connect-google/_rtx/blueprints/rtx-authorize-services.yaml`

**Interfaces:**

- `_browser_helper.py` reads one UTF-8 URL line from stdin and exits `0`, `1`,
  or `2`; it prints nothing.
- `CallbackResult` is exactly `@dataclass(frozen=True)` with
  `kind: Literal["code", "denied"]` and `value: str`; `value` is the code or
  allowlisted Google denial value and is never emitted. The complete denial
  allowlist is `_GOOGLE_DENIAL_ERRORS = frozenset({"access_denied"})`; unknown
  nonempty `error` values receive a static response and do not terminate.
- `_wait_for_callback(listener, *, state, deadline, monotonic=time.monotonic)
  -> CallbackResult` accepts only the bounded HTTP subset in the design.
- `_start_browser_helper(url, *, popen=subprocess.Popen) -> subprocess.Popen`
  passes the URL through stdin and uses `DEVNULL` for helper stdout/stderr.
- `_emit_diagnostic(event, status, **fields)` writes one flushed JSON line to
  an injected diagnostic stream.

- [ ] **Step 1: Add failing parser/deadline tests against real loopback sockets.**

  Use a real `127.0.0.1` listener and literal requests. Cover exact valid code
  and denial; wrong path/method/state; missing, blank, duplicate, and mixed
  query values; a single unknown nonempty `error` followed by a valid code or
  `access_denied` callback; favicon/unrelated traffic followed by success;
  16 KiB overflow; no client; silent connection; and a byte-at-a-time slow-drip
  connection that cannot exceed the injected monotonic deadline.

- [ ] **Step 2: Run callback tests and observe RED.**

  ```text
  pytest -q skills/connect-google/_rtx/tests/test_authorize_services.py -k 'callback or deadline or slow_drip or oversized_request'
  ```

  Expected: failures show `localhost`, first-request consumption, and no
  deadline.

- [ ] **Step 3: Implement the minimal socket parser.**

  Bind with:

  ```python
  listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  listener.bind(("127.0.0.1", callback_port))
  listener.listen()
  ```

  Before every `accept` and `recv`, compute `remaining = deadline - monotonic()`
  and fail at `remaining <= 0`. Cap bytes before parsing the ASCII request line;
  discard headers after detecting the first `\r\n\r\n`; send only static HTML
  with the three security headers. Malformed/nonterminal requests continue the
  outer accept loop.

- [ ] **Step 4: Add failing browser-helper isolation tests.**

  Test helper true/false/exception exits, URL-via-stdin rather than argv,
  `DEVNULL` child streams, and a blocked helper terminated without delaying a
  valid callback. Assert URL/state are not present in any parent-authored field
  except the authorization URL record. The CLI `--no-open-browser` assertion
  belongs to Task 5 after that flag exists.

- [ ] **Step 5: Run browser tests and observe RED.**

  ```text
  pytest -q skills/connect-google/_rtx/tests/test_authorize_services.py -k 'browser or diagnostic or redaction'
  ```

- [ ] **Step 6: Implement helper lifecycle and exact diagnostic writer.**

  Start `[sys.executable, helper_path]` with binary stdin pipe and `DEVNULL`
  output; write the URL bytes, close stdin, poll without blocking, and terminate
  on every terminal path. Shutdown is `terminate()` plus `wait(timeout=1.0)`;
  on `TimeoutExpired`, use `kill()` plus a second `wait(timeout=1.0)`. A second
  timeout is sanitized and never blocks authorization exit. The blocked-helper
  test asserts the child is reaped, not merely that callback handling continued.
  Parent events use exactly the schema/status/field table in the approved design
  and emit nothing after `oauth.complete` or `oauth.failed`.

  Add `_browser_helper\.py` to the implementation module's `content` and to
  the authorize source's `content`. Add an explicit authorize-source dependency
  on `connect-google._rtx.source.rtx-client-config`, because orchestration calls
  its `load_authorization_client` helper.

- [ ] **Step 7: Run the focused authorization tests.**

  ```text
  pytest -q skills/connect-google/_rtx/tests/test_authorize_services.py
  ```

  Expected: PASS with no live internet or browser dependency.

### Task 5: End-to-end authorize-services orchestration

**Files:**

- Modify: `skills/connect-google/_rtx/_loopback_oauth.py`
- Modify: `skills/connect-google/_rtx/tests/test_authorize_services.py`
- Modify: `skills/connect-google/_rtx/blueprints/rtx-authorize-services.yaml`

**Interfaces:**

- `authorize_services(..., callback_port=0, open_browser=True,
  diagnostic_stream=sys.stderr, ...) -> AuthorizationResult`.
- CLI flags add `--no-open-browser` and `--callback-port`.
- Success stdout preserves `schema_version: 1`; errors return nonzero with one
  terminal OAuth error record and empty child stdout.

- [ ] **Step 1: Add failing orchestration tests.**

  Prove pinned authorization/UserInfo/token endpoints despite malicious client
  metadata; manual URL precedes browser launch; redirect and SSH command share
  the selected port; `--callback-port` collision is a stable listener failure;
  zero service grants publish nothing; partial grants publish/report only the
  subset; account mismatch publishes nothing; publication warning strings flow
  into `oauth.complete.warnings`; every failure maps to the design's exact phase
  and code.

  Define one local typed terminal contract:

  ```python
  class AuthorizationFailure(GoogleCredentialError):
      def __init__(self, *, phase: str, code: str) -> None:
          self.phase = phase
          self.code = code
          super().__init__(f"{phase}: {code}")
  ```

  Callback, token, UserInfo, account, and publication boundaries raise or map to
  this type by exception class and explicit branch. In particular,
  `GoogleCredentialPublicationUncertain` maps directly to
  `AuthorizationFailure(phase="credential_publish",
  code="publication_uncertain")`; no message matching is allowed.

- [ ] **Step 2: Run orchestration tests and observe RED.**

  ```text
  pytest -q skills/connect-google/_rtx/tests/test_authorize_services.py
  ```

- [ ] **Step 3: Implement the linear orchestration.**

  The order is fixed: normalize services; client/secret preflight; bind; build
  PKCE/state and pinned URL; emit URL/tunnel; start or disable browser helper;
  wait; close listener/helper; exchange; UserInfo; account/scope checks;
  publish; emit terminal; return result. At each effect boundary, map explicit
  result branches and specific exception types into `AuthorizationFailure`.
  Never categorize a broad `GoogleCredentialError` by parsing its message.
  Truly unexpected exceptions map to `internal_error` only at the CLI boundary;
  do not include exception text in diagnostics.

  Pass the exact `client_secret_ref` returned by
  `load_authorization_client` into `exchange_authorization_code`. Add an
  end-to-end test whose canonical client resolves a non-derived secret reference
  and prove exchange succeeds without consulting the derived key.

- [ ] **Step 4: Add CLI stream-contract tests and implement flags.**

  Call `run_authorize_services` with injected streams/collaborators. Assert one
  JSON result on success, empty stdout on failure, and the exact terminal JSONL
  rule without claiming dispatcher warnings are JSON. Assert
  `--no-open-browser` creates no helper process while leaving the manual URL
  event usable.

- [ ] **Step 5: Run every connect-google focused test.**

  ```text
  pytest -q skills/connect-google/_rtx/tests
  ```

  Expected: PASS.

### Task 6: Live dispatcher CLI streams without changing programmatic capture

**Files:**

- Modify: `src/officina/dispatcher/cli.py`
- Create or modify: `tests/test_dispatcher_cli.py`
- Modify if necessary: `tests/test_dispatcher_direct_authorization.py`

**Interfaces:**

- CLI `main()` calls `_dispatch_host(..., capture_output=False)` and returns the
  child return code without replay.
- `_dispatch_host` programmatic default and explicit `capture_output=True`
  behavior remain unchanged.

- [ ] **Step 1: Add failing live-output and preservation tests.**

  Use an authorized fixture interface whose child writes one flushed stderr
  line, waits on a synchronization file/event, then writes stdout. Start the
  dispatcher CLI as a subprocess and assert the first stderr line is readable
  while the process is still alive. Separately assert `_dispatch_host(...,
  capture_output=True)` returns captured stdout/stderr exactly as before and
  dispatcher warnings/pre-launch errors retain their current formats.

- [ ] **Step 2: Run dispatcher tests and observe RED.**

  ```text
  pytest -q tests/test_dispatcher_cli.py tests/test_dispatcher_direct_authorization.py
  ```

  Expected: live-read test blocks/fails because CLI buffers the child.

- [ ] **Step 3: Switch only CLI execution to inherited streams.**

  Pass `capture_output=False`. Remove the now-dead replay block only after tests
  prove no CLI path needs it. Do not modify `direct_runtime` capture defaults.

- [ ] **Step 4: Rerun focused dispatcher tests.**

  ```text
  pytest -q tests/test_dispatcher_cli.py tests/test_dispatcher_direct_authorization.py tests/test_dispatcher_errors.py
  ```

  Expected: PASS.

### Task 7: Blueprint and instruction contract synchronization

**Files:**

- Modify: `skills/connect-google/_rtx/blueprints/rtx-authorize-services.yaml`
- Modify: `skills/connect-google/_rtx/blueprints/rtx-client-config.yaml`
- Modify: `skills/connect-google/_rtx/blueprint.yaml`
- Modify: `src/officina/common/blueprints/google-credentials.yaml`
- Modify: `skills/connect-google/instructions/connect-services.md`
- Regenerate: blueprint-owned blocks in `skills/connect-google/SKILL.md` and
  repository manifests through the canonical synchronizer
- Modify tests only for consumer behavior, not generated text formatting:
  `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py`

**Interfaces:**

- `authorize-services` process binding permits `--no-open-browser` and
  `--callback-port`.
- Interaction is exactly `mode: interactive`, `channel: tty`,
  `unattended_outcome: failed`.
- Direct I/O declares user-private stderr JSONL and success-only stdout.

- [ ] **Step 1: Add a failing blueprint consumer test.**

  Load the compiled interface and assert its interaction, allowed flags,
  stdout/error cardinality, pinned endpoints, user-private stderr, atomic
  publication wording, and failure/uncertainty outcomes. The break caught is a
  caller receiving a stale executable contract, not a source-text change.

- [ ] **Step 2: Run routing/blueprint tests and observe RED.**

  ```text
  pytest -q skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py
  ```

- [ ] **Step 3: Update authored blueprints and concise user instructions.**

  Keep implementation details in blueprints/runtime docstrings. Instructions
  tell the agent to surface the already-emitted manual URL, explain
  `--no-open-browser`, and give the exact same-port tunnel command when the user
  is remote. Preserve the existing recommended-all/allow-subset route.

- [ ] **Step 4: Synchronize generated artifacts.**

  Run from the worktree root:

  ```text
  env PYTHONPATH="$PWD/src" \
    python3 -P -m officina.dispatcher.cli \
    --repository-config "$PWD/officina.toml" \
    --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints
  env PYTHONPATH="$PWD/src" \
    python3 -P -m officina.dispatcher.cli \
    --repository-config "$PWD/officina.toml" \
    --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints --check
  ```

  Expected: synchronization and check exit 0. Inspect the diff to confirm only
  blueprint-derived artifacts changed.

- [ ] **Step 5: Run connect-google and blueprint-focused tests.**

  ```text
  pytest -q skills/connect-google/_rtx/tests tests/test_officina_google_credentials.py tests/test_direct_blueprint_v6_schemas.py
  ```

  Expected: PASS.

### Task 8: Opt-in native browser and same-port SSH smoke tests

**Files:**

- Create: `skills/connect-google/_rtx/tests/test_google_oauth_native_smoke.py`

**Interfaces:**

- Native browser smoke runs only with `FAMULUS_GOOGLE_BROWSER_SMOKE=1`.
- Same-port SSH smoke runs only with `FAMULUS_GOOGLE_SSH_SMOKE_TARGET` and
  `FAMULUS_GOOGLE_SSH_SMOKE_PORT` set by the operator.
- Skipped smokes do not count as native certification evidence.

- [ ] **Step 1: Add the opt-in native browser smoke.**

  Start a real local `127.0.0.1` HTTP server, pass its nonce URL over stdin to
  the real `_browser_helper.py`, and wait at most 30 seconds for the browser GET.
  Assert helper stdout/stderr are empty and the exact nonce was received. Always
  close the listener and terminate/kill/reap the helper in `finally`. Skip with
  a precise reason unless `FAMULUS_GOOGLE_BROWSER_SMOKE=1`.

- [ ] **Step 2: Add the opt-in disposable same-port SSH smoke.**

  Read target and integer port from the two SSH environment variables. Start:

  ```text
  ssh -o ExitOnForwardFailure=yes \
    -L 127.0.0.1:P:127.0.0.1:P TARGET \
    python3 -m http.server P --bind 127.0.0.1
  ```

  Poll local `http://127.0.0.1:P/` for at most 30 seconds and require HTTP 200.
  In `finally`, terminate/wait then kill/wait the SSH process with one-second
  bounds. This foreground remote command makes SSH-session teardown clean up the
  disposable remote server without a remote `pkill`.

- [ ] **Step 3: Run deterministic skip behavior, then available native smokes.**

  ```text
  pytest -q skills/connect-google/_rtx/tests/test_google_oauth_native_smoke.py
  FAMULUS_GOOGLE_BROWSER_SMOKE=1 pytest -q \
    skills/connect-google/_rtx/tests/test_google_oauth_native_smoke.py -k browser
  FAMULUS_GOOGLE_SSH_SMOKE_TARGET=TARGET \
    FAMULUS_GOOGLE_SSH_SMOKE_PORT=P pytest -q \
    skills/connect-google/_rtx/tests/test_google_oauth_native_smoke.py -k ssh
  ```

  Record native evidence only for commands actually run successfully. The
  deterministic three-OS tests remain the portable gate when macOS, Windows, a
  desktop browser, or an SSH target is unavailable in the current environment.

### Task 9: Cross-platform and repository verification

**Files:**

- Modify only if a new failure is proven in scope by a failing test.
- Inspect all task diffs and the approved design.

**Interfaces:**

- Consumes every prior task deliverable.
- Produces verification evidence; it does not commit, install, or publish.

- [ ] **Step 1: Run the focused combined suite.**

  ```text
  pytest -q skills/connect-google/_rtx/tests tests/test_officina_google_credentials.py tests/test_dispatcher_cli.py tests/test_dispatcher_direct_authorization.py tests/test_dispatcher_errors.py tests/test_officina_secret_store.py tests/test_officina_famulus_paths.py
  ```

- [ ] **Step 2: Run portability and blueprint synchronization checks.**

  ```text
  pytest -q tests/validate_cross_platform.py tests/validate_platform_neutral.py tests/validate_skill_runtime_files.py tests/validate_blueprints.py
  env PYTHONPATH="$PWD/src" \
    python3 -P -m officina.dispatcher.cli \
    --repository-config "$PWD/officina.toml" \
    --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints --check
  ```

- [ ] **Step 3: Run repository full verification.**

  ```text
  "$PWD/repo_checks.py" --suite full
  ```

  Expected: all in-scope checks pass. Separately identify only the already
  baselined external marketplace drift if those two unrelated tests still fail.

- [ ] **Step 4: Run diff integrity checks.**

  ```text
  git diff --check
  git status --short
  ```

  Confirm the main checkout is untouched and every changed worktree path belongs
  to the approved implementation surface.

- [ ] **Step 5: Audit implementation against the design until green.**

  Give independent reviewers the approved design, exact diff, focused/full test
  evidence, known baseline failures, and the requirements: cross-platform,
  local-browser-with-manual-fallback, same-port SSH, simplicity, transparency,
  secret safety, and backward compatibility. Resolve every concrete blocker
  with a new failing regression test before code changes, rerun verification,
  and repeat until both semantic and simplicity reviewers return GREEN.
