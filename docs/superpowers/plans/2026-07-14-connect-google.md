# Connect Google Implementation Plan

> **Architecture correction:** The connector-to-service orchestration direction
> in this original plan is superseded by
> `2026-07-14-connect-google-auth-boundary-correction.md`. Service skills invoke
> `connect-google` for shared OAuth-client preparation; `connect-google` does
> not invoke service interfaces.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one prompt-routed `connect-google` skill that accepts or helps create a Google Desktop OAuth client, privately installs it once, and coordinates optional Drive, Calendar, and multi-account Gmail authorization through service-owned interfaces.

**Architecture:** `connect-google` is a schema-version-2 orchestration skill with a short default router and two named LLM interfaces: `create-client` and `connect-services`. Two local machine interfaces own canonical client status and installation; service-specific OAuth exchange, credentials, and smoke checks remain inside cloud-files, g-calendar, and email-client and are reached only through declared dispatcher interfaces.

**Tech Stack:** Python 3.6+ stdlib, Famulus typed blueprint graph, `officina.runtime.python_machine_interface`, dispatcher, pytest, Google OAuth loopback flow.

## Global Constraints

- Never commit or publish a Google OAuth client JSON, refresh token, access token, or generated service credential.
- Store the canonical client at `~/.config/connect-google/client.json`; store a regular file, not a symlink.
- Accept only Google Desktop OAuth JSON with an `installed` section; reject `web` clients and input containing `access_token` or `refresh_token` keys.
- `token_uri` is a normal client field and must not be rejected as a token.
- Keep one active Drive account, one active Calendar account, and multiple named Gmail accounts.
- Recommend Drive, Calendar, and Gmail while allowing any subset.
- Authorize services separately and preserve successful connections when another service fails.
- Drive and Calendar replacement must retain the previous credential until new authorization and non-mutating verification succeed.
- Existing service setup machine interfaces remain service-owned; only duplicated user-facing Cloud Console guidance moves to `connect-google`.
- All cross-skill calls are version-pinned, dispatcher-first, and cwd-independent.
- Support Linux, macOS, and Windows without new third-party runtime dependencies.
- Do not modify the pre-existing dirty file `skills/cloud-files/tests/test_setup_oauth.py`; add focused tests in new files.

---

### Task 1: Shared OAuth JSON writer

**Files:**
- Create: `src/officina/common/oauth_json.py`
- Create: `tests/test_officina_oauth_json.py`

**Interfaces:**
- Consumes: `officina.common.atomic_files.atomic_replace_bytes(path, data, allowed_root, mode)` on POSIX.
- Produces: `write_oauth_json(path: Path, payload: Mapping[str, object]) -> None`.

- [ ] **Step 1: Write failing tests for atomic OAuth JSON writes**

Create tests that verify UTF-8 JSON with a trailing newline, parent creation, mode `0600` where meaningful, atomic replacement of an existing regular file, and refusal to replace a symlink:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from officina.common.oauth_json import OAuthJsonError, write_oauth_json


def test_write_oauth_json_creates_parent_and_private_file(tmp_path: Path) -> None:
    path = tmp_path / "config" / "client.json"
    write_oauth_json(path, {"installed": {"client_id": "cid"}})
    assert json.loads(path.read_text(encoding="utf-8"))["installed"]["client_id"] == "cid"
    assert path.read_bytes().endswith(b"\n")
    if os.name == "posix":
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600


def test_write_oauth_json_atomically_replaces_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    write_oauth_json(path, {"new": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}


def test_write_oauth_json_rejects_symlink_destination(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "client.json"
    link.symlink_to(target)
    with pytest.raises(OAuthJsonError, match="symbolic link"):
        write_oauth_json(link, {"new": True})
    assert target.read_text(encoding="utf-8") == "{}\n"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m pytest -q tests/test_officina_oauth_json.py`

Expected: collection fails because `officina.common.oauth_json` does not exist.

- [ ] **Step 3: Implement the shared writer**

Implement a compact wrapper that serializes once, creates the parent, rejects a symlink destination, uses the existing fail-closed descriptor writer on POSIX, and uses a same-directory temporary file plus `os.replace` on Windows:

```python
"""Cross-platform atomic writes for private OAuth JSON files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from . import atomic_files


class OAuthJsonError(OSError):
    pass


def write_oauth_json(path: Path, payload: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.is_symlink():
        raise OAuthJsonError(f"destination is a symbolic link: {destination}")
    data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    if os.name == "posix":
        try:
            atomic_files.atomic_replace_bytes(
                destination,
                data,
                allowed_root=destination.parent,
                mode=0o600,
            )
        except atomic_files.AtomicWriteError as exc:
            raise OAuthJsonError(str(exc)) from exc
        return
    descriptor, temporary = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if destination.is_symlink():
            raise OAuthJsonError(f"destination is a symbolic link: {destination}")
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: Run focused and existing atomic-file tests**

Run: `python3 -m pytest -q tests/test_officina_oauth_json.py tests/test_officina_atomic_files.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/officina/common/oauth_json.py tests/test_officina_oauth_json.py
git commit -m "feat: add OAuth JSON writer"
```

---

### Task 2: Connect Google skill and canonical client interfaces

**Files:**
- Create: `skills/connect-google/_rtx/__init__.py`
- Create: `skills/connect-google/_rtx/_client_config.py`
- Create: `skills/connect-google/_rtx/._client_config.py.status.blueprint.yaml`
- Create: `skills/connect-google/_rtx/._client_config.py.install.blueprint.yaml`
- Create: `skills/connect-google/tests/test_client_config.py`
- Create: `skills/connect-google/blueprint.yaml`
- Create: `skills/connect-google/.SKILL.md.blueprint.yaml`
- Create: `skills/connect-google/SKILL.md`
- Create: `skills/connect-google/llm_interfaces/create-client.md`
- Create: `skills/connect-google/llm_interfaces/.create-client.md.blueprint.yaml`
- Create: `skills/connect-google/llm_interfaces/connect-services.md`
- Create: `skills/connect-google/llm_interfaces/.connect-services.md.blueprint.yaml`
- Create: `skills/connect-google/tests/test_llm_routing.py`

**Interfaces:**
- Consumes: `write_oauth_json(path, payload)` from Task 1.
- Produces:
  - `validate_client_payload(payload: object) -> dict[str, object]`
  - `client_status(home: Path) -> dict[str, str]`
  - `install_client(source: Path, home: Path, replace: bool) -> dict[str, str]`
  - `connect-google.machine.client-status [--home DIR]`
  - `connect-google.machine.install-client --from-json PATH [--replace] [--home DIR]`
  - `connect-google.llm.default@1`
  - `connect-google.llm.create-client@1`
  - `connect-google.llm.connect-services@1`

- [ ] **Step 1: Write failing validation and installation tests**

Cover a valid `installed` payload, malformed JSON, `web`-only JSON, missing required fields, recursive `access_token`/`refresh_token` rejection, accepted `token_uri`, missing/valid/invalid status JSON, idempotent reinstall, different-client refusal without `--replace`, replacement with `--replace`, canonical mode, and source-file preservation.

Use this valid fixture shape in every positive test:

```python
def desktop_client(client_id: str = "cid") -> dict[str, object]:
    return {
        "installed": {
            "client_id": client_id,
            "project_id": "famulus-test",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "secret",
            "redirect_uris": ["http://localhost"],
        }
    }
```

Assert that status output never contains `client_secret` or its value.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m pytest -q skills/connect-google/tests/test_client_config.py`

Expected: collection fails because the new runtime does not exist.

- [ ] **Step 3: Implement client validation and installation**

Use exact forbidden keys and exact required fields:

```python
FORBIDDEN_KEYS = {"access_token", "refresh_token"}
REQUIRED_INSTALLED_FIELDS = {
    "client_id",
    "client_secret",
    "auth_uri",
    "token_uri",
    "redirect_uris",
}


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold() in FORBIDDEN_KEYS
            or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False
```

`validate_client_payload` must require a mapping-valued `installed` section,
reject a `web` section, require non-empty string credentials and endpoint
fields, require a non-empty string list for `redirect_uris`, and return a
plain JSON-compatible dictionary. `install_client` validates before examining
or replacing the destination; equal content is idempotent; unequal content
requires `replace=True`.

Expose two `PythonArgvMachineInterface` classes from the same runtime file:

```python
class ClientStatusInterface(PythonArgvMachineInterface):
    prog = "client-status"

    def run(self, argv: list[str]) -> int:
        return run_client_status(argv)


class InstallClientInterface(PythonArgvMachineInterface):
    prog = "install-client"

    def run(self, argv: list[str]) -> int:
        return run_install_client(argv)
```

Both commands print JSON containing only `status`, `client_type`, and `path`.
Installation errors go to argparse/SystemExit without printing client values.

- [ ] **Step 4: Add typed machine sidecars**

The `client-status` sidecar binds `ClientStatusInterface`, reads the canonical
credential, writes a JSON status to stdout, owns no files, and supports all
three platforms. The `install-client` sidecar binds
`InstallClientInterface`, reads `<client-json>`, writes and owns
`$home/.config/connect-google/client.json`, and grants read access to:

```yaml
allowed_readers:
  - connect-google.machine.client-status
  - cloud-files.machine.setup-oauth
  - g-calendar.machine.setup-oauth
  - email-client.machine.accounts-setup-oauth
```

Both sidecars declare `dependencies: []`, `behavior_sources: []`, explicit
`direct_io`, and exact platform booleans. `install-client` accepts only the
declared flags; neither interface allows stdin.

- [ ] **Step 5: Run the focused tests**

Run: `python3 -m pytest -q skills/connect-google/tests/test_client_config.py`

Expected: all tests pass.

- [ ] **Step 6: Write failing routing-contract tests**

Parse the root and three LLM sidecars and assert:

```python
assert root["id"] == "connect-google"
assert root["category"] == "workflow-general-assistant"
assert root["role"] == "integration"
assert root["kind"] == "setup"
assert default["uses_interfaces"] == [
    {"interface": "connect-google.llm.create-client", "version": 1},
    {"interface": "connect-google.llm.connect-services", "version": 1},
]
assert create_client["uses_interfaces"] == [
    {"interface": "connect-google.llm.connect-services", "version": 1}
]
```

Assert that `connect-services` declares exact version-1 edges to:

```text
connect-google.machine.client-status
connect-google.machine.install-client
cloud-files.machine.setup-oauth
g-calendar.machine.setup-oauth
email-client.machine.accounts-list
email-client.machine.accounts-add
email-client.machine.accounts-update
email-client.machine.accounts-setup-oauth
email-client.machine.live-smoke
```

Also assert the authored Markdown contains the approved routing, selection,
single-account replacement, multiple-Gmail, partial-success, and private-file
policies and contains no dispatcher command, private runtime name, client
secret value, or instruction to commit the JSON.

- [ ] **Step 7: Run the routing tests and verify RED**

Run: `python3 -m pytest -q skills/connect-google/tests/test_llm_routing.py`

Expected: FAIL because the root, sidecars, and Markdown do not exist.

- [ ] **Step 8: Author the typed root and LLM sidecars**

Use schema version 2 throughout. The root declares the two LLM and two machine
sidecars, portable suggested permissions for the machine runtime, and this
skill contract:

```yaml
skill_interface:
  inputs:
    - A request to connect or reconnect Famulus Google services.
    - An optional Google Desktop OAuth client JSON path.
  outputs:
    - Guided Google Cloud client creation or a per-service connection summary.
  side_effects:
    - May install a private OAuth client configuration and authorize selected Google services.
```

The default interface reads the prompt and writes the routed response. The two
named interfaces write responses but inherit transitive machine and network IO
only through their `uses_interfaces` edges; do not copy callee IO into their
`direct_io`.

- [ ] **Step 9: Author the short router**

`SKILL.md` must:

- trigger on connect, setup, add-account, and reauthorization requests for
  Google Drive, Calendar, or Gmail;
- begin with `Skill: connect-google`;
- inspect `client-status` before asking whether a JSON exists;
- route a valid stored or supplied JSON to `connect-services`;
- route a missing JSON to `create-client`;
- keep shared security and partial-success policy in the router;
- contain no Cloud Console procedure or service invocation syntax.

- [ ] **Step 10: Author `create-client`**

The route guides project selection/creation, External audience, Testing test
users, Drive/Calendar API enablement, exact current scopes, Desktop client
creation, private download handling, and handoff of the resulting path to
`connect-services`. State that Testing refresh tokens typically expire after
seven days, that test-user allowlisting does not distribute the JSON, and that
Workspace administrators can still block authorization.

- [ ] **Step 11: Author `connect-services`**

The route must:

1. validate/install or reuse the canonical client;
2. recommend all three services and allow a subset;
3. confirm before replacing configured Drive or Calendar;
4. state the intended Google account before each browser flow;
5. add or intentionally update Gmail nicknames;
6. run Gmail IMAP and SMTP auth smoke checks without sending mail;
7. classify each result as connected-and-verified, connected-but-unverified,
   skipped, denied, or provider/configuration error;
8. preserve earlier successes after later failures.

- [ ] **Step 12: Synchronize generated blueprint blocks and run tests**

Run: `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints`

Run: `python3 -m pytest -q skills/connect-google/tests`

Run: `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check`

Expected: generated blocks are current and focused tests pass.

- [ ] **Step 13: Commit Task 2**

```bash
git add skills/connect-google references/blueprint/runtime_dependencies.json
git commit -m "feat: add routed Google connection skill"
```

---

### Task 3: Transactional Drive and Calendar authorization

**Files:**
- Modify: `skills/cloud-files/_rtx/_oauth_bootstrap.py`
- Create: `skills/cloud-files/tests/test_oauth_transaction.py`
- Modify: `skills/cloud-files/blueprint.yaml`
- Modify: `skills/g-calendar/_rtx/_oauth_bootstrap.py`
- Create: `skills/g-calendar/tests/test_oauth_transaction.py`
- Modify: `skills/g-calendar/blueprint.yaml`

**Interfaces:**
- Consumes: `write_oauth_json` from Task 1 and the canonical client path passed through existing `--from-json` arguments.
- Produces: existing setup interfaces with verified-before-replace semantics and `connect-google` caller access.

- [ ] **Step 1: Write failing Drive transaction tests in the new test file**

Mock token exchange and Drive verification separately. Assert that:

- missing `access_token` fails without changing old credentials;
- verification failure fails without changing old credentials;
- successful verification atomically replaces old credentials with only
  `client_id`, `client_secret`, and `refresh_token`;
- Drive verification calls
  `https://www.googleapis.com/drive/v3/about?fields=user` with a Bearer token.

Do not edit `skills/cloud-files/tests/test_setup_oauth.py`.

- [ ] **Step 2: Write failing Calendar transaction tests**

Assert the same preservation/replacement behavior and verify against:

```text
https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1
```

- [ ] **Step 3: Run both new test files and verify RED**

Run: `python3 -m pytest -q skills/cloud-files/tests/test_oauth_transaction.py skills/g-calendar/tests/test_oauth_transaction.py`

Expected: FAIL because verification helpers and atomic writes do not exist.

- [ ] **Step 4: Implement Drive verified replacement**

Add:

```python
DRIVE_VERIFY_URL = "https://www.googleapis.com/drive/v3/about?fields=user"


def verify_access_token(access_token: str) -> None:
    request = urllib.request.Request(
        DRIVE_VERIFY_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request) as response:
        json.load(response)
```

Require both `refresh_token` and `access_token`, verify first, then call
`write_oauth_json(CREDS_PATH, creds)`. Never print token values.

- [ ] **Step 5: Implement Calendar verified replacement**

Add the equivalent `CALENDAR_VERIFY_URL` and Bearer request, require both
tokens, verify first, and atomically write only after success.

- [ ] **Step 6: Expand only the setup interface contracts**

In both legacy blueprints:

- add `connect-google` to `allowed_callers` for `setup-oauth`;
- add the service verification request to `direct_io.network`;
- keep the interface at version 1 because its invocation contract is
  unchanged;
- keep credential ownership with the service setup interface.

- [ ] **Step 7: Run service tests and blueprint checks**

Run: `python3 -m pytest -q skills/cloud-files/tests skills/g-calendar/tests`

Run: `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints`

Run: `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check`

Expected: all selected tests and blueprint synchronization pass.

- [ ] **Step 8: Commit Task 3 without the unrelated dirty test**

```bash
git add skills/cloud-files/_rtx/_oauth_bootstrap.py skills/cloud-files/tests/test_oauth_transaction.py skills/cloud-files/blueprint.yaml skills/cloud-files/SKILL.md skills/g-calendar/_rtx/_oauth_bootstrap.py skills/g-calendar/tests/test_oauth_transaction.py skills/g-calendar/blueprint.yaml skills/g-calendar/SKILL.md references/blueprint/runtime_dependencies.json
git commit -m "feat: verify Google credentials before replacement"
```

Before committing, verify `git diff --cached --name-only` does not contain
`skills/cloud-files/tests/test_setup_oauth.py`.

---

### Task 4: Gmail delegation and service-facing routing

**Files:**
- Modify: `skills/email-client/blueprint.yaml`
- Modify: `skills/email-client/SKILL.md`
- Modify: `skills/cloud-files/blueprint.yaml`
- Modify: `skills/cloud-files/SKILL.md`
- Modify: `skills/g-calendar/blueprint.yaml`
- Modify: `skills/g-calendar/SKILL.md`
- Modify: `skills/install-assistant-tools/blueprint.yaml`
- Modify: `skills/install-assistant-tools/SKILL.md`
- Create: `skills/connect-google/tests/test_service_delegation.py`

**Interfaces:**
- Consumes: `connect-google.llm.default@1` and the service interfaces declared by Task 2.
- Produces: one user-facing owner for Google setup while retaining all service machine interfaces.

- [ ] **Step 1: Write failing delegation tests**

Assert that:

- email-client allows caller `connect-google` on `accounts-list`,
  `accounts-add`, `accounts-update`, `accounts-setup-oauth`, and `live-smoke`;
- the cloud-files, g-calendar, email-client, and install-assistant-tools default
  LLM interfaces declare a version-1 edge to `connect-google.llm.default` when
  their authored body delegates Google onboarding;
- cloud-files, g-calendar, and email-client hand-authored setup guidance directs
  initial Google setup and reauthorization to `connect-google`;
- none of those service bodies retains a Google Cloud project creation
  tutorial;
- install-assistant-tools Phase 2 names `connect-google` as the conversational
  Google onboarding workflow without declaring or copying machine calls;
- the three service setup machine interfaces still exist.

- [ ] **Step 2: Run delegation tests and verify RED**

Run: `python3 -m pytest -q skills/connect-google/tests/test_service_delegation.py`

Expected: FAIL because caller permissions and routing prose are unchanged.

- [ ] **Step 3: Expand Gmail caller permissions**

Add `connect-google` to `allowed_callers` on the five required email-client
machine interfaces. Do not expose mail reading, attachment, or send-email
interfaces to `connect-google`.

- [ ] **Step 4: Replace duplicated service setup tutorials**

In each service body, retain operational behavior, config locations, scopes,
and service-specific recovery details, but replace first-time Cloud Console
steps with a short instruction to use `connect-google` for initial setup or
reauthorization. Do not remove or rename machine interfaces.

Add a version-1 `uses_interfaces` edge from each delegating legacy default LLM
interface to `connect-google.llm.default`; the authored prose and graph must
name the same dependency.

- [ ] **Step 5: Route installer Phase 2 to the new skill**

Keep installation mechanically independent. Change only the conversational
follow-through so a Google connection request invokes `connect-google`; retain
the existing rule that install-assistant-tools does not duplicate remote setup
logic.

- [ ] **Step 6: Synchronize and run focused tests**

Run: `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints`

Run: `python3 -m pytest -q skills/connect-google/tests skills/email-client/tests`

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add skills/connect-google/tests/test_service_delegation.py skills/email-client/blueprint.yaml skills/email-client/SKILL.md skills/cloud-files/blueprint.yaml skills/cloud-files/SKILL.md skills/g-calendar/blueprint.yaml skills/g-calendar/SKILL.md skills/install-assistant-tools/blueprint.yaml skills/install-assistant-tools/SKILL.md references/blueprint/runtime_dependencies.json
git commit -m "docs: route Google setup through connect-google"
```

---

### Task 5: Public documentation and clean-home verification

**Files:**
- Modify: `README.md`
- Modify: `docs/installation.md`
- Modify generated documentation produced by `scripts/generate-doc-artifacts.py`
- Modify: `docs/superpowers/plans/2026-07-14-connect-google.md` only to mark completed checkboxes during execution.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: discoverable public onboarding and repository-wide verification evidence.

- [ ] **Step 1: Add concise public onboarding documentation**

Document the prompt:

```text
Connect Famulus to Google.
```

Explain the two paths without embedding credentials:

- private pilot: obtain the owner-provided Desktop client JSON privately;
- public user: let `connect-google` guide creation of a personal Cloud project.

State explicitly that the JSON must not be committed to GitHub and that user
tokens stay local to the corresponding service.

- [ ] **Step 2: Regenerate documentation artifacts**

Run: `python3 scripts/generate-doc-artifacts.py`

Expected: skill indexes and generated documentation include `connect-google`
without manual count edits.

- [ ] **Step 3: Run dispatcher contract dry-runs**

Run each command independently:

```bash
dispatcher --dry-run --caller-skill connect-google connect-google.machine.client-status
dispatcher --dry-run --caller-skill connect-google connect-google.machine.install-client --from-json /tmp/client.json
dispatcher --dry-run --caller-skill connect-google cloud-files.machine.setup-oauth --from-json /tmp/client.json
dispatcher --dry-run --caller-skill connect-google g-calendar.machine.setup-oauth --from-json /tmp/client.json
dispatcher --dry-run --caller-skill connect-google email-client.machine.accounts-setup-oauth --nickname test --client-config /tmp/client.json
```

Expected: every invocation resolves to its declared interface; no command
executes OAuth during dry-run.

- [ ] **Step 4: Run focused and repository validation**

Run: `python3 -m pytest -q skills/connect-google/tests skills/cloud-files/tests skills/g-calendar/tests skills/email-client/tests tests/test_officina_oauth_json.py`

Run: `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check`

Run: `validators/runner.py`

Run: `python3 scripts/run-python-tests.py --suite precommit`

Expected: all commands exit 0. If the pre-existing dirty cloud-files test
fails, prove whether the failure is present on the untouched baseline rather
than editing or discarding that file.

- [ ] **Step 5: Exercise canonical client status in a clean temporary home**

Create a valid fake Desktop client JSON under `/tmp`, invoke `client-status`
with `--home /tmp/connect-google-home` and expect `missing`, invoke
`install-client` with that home and expect `installed`, then invoke status and
expect `valid`. Inspect the created file and verify no service credential file
was created. Do not run browser authorization in this automated check.

- [ ] **Step 6: Review the complete diff and credential hygiene**

Run: `git diff --check`

Run: `git status --short`

Run the repository secret scan through the normal commit hook. Confirm no
client IDs, client secrets, refresh tokens, access tokens, or local config
files are tracked.

- [ ] **Step 7: Commit Task 5**

```bash
git add README.md docs/installation.md docs/skills.md docs/user/general.md docs/superpowers/plans/2026-07-14-connect-google.md
git commit -m "docs: document Google connection onboarding"
```

Stage generated files by exact name after inspecting generator output; do not
use `git add -A` or include unrelated plan files.

- [ ] **Step 8: Optional live smoke test with explicit operator participation**

Only after all automated checks pass, use an allowlisted test account and a
clean temporary home to authorize Drive, Calendar, and one Gmail nickname.
Verify Drive and Calendar through their setup-time non-mutating checks and
Gmail through IMAP and SMTP authentication without sending mail. Never print
or commit the client JSON or generated credentials.
