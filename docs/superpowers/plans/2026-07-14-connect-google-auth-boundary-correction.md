# Connect Google Authentication Boundary Correction Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reverse the Google integration dependency so service skills invoke `connect-google` only for shared OAuth-client preparation and `connect-google` cannot invoke or inspect service interfaces.

**Architecture:** `connect-google` retains its client-status, client-installation, and Cloud Console guidance surfaces. cloud-files, g-calendar, and email-client retain complete ownership of account selection, OAuth exchange, credential storage, verification, and Google operations. Remove the connector-owned service bridge and every connector-to-service machine edge; retain only service-LLM-to-connector-LLM edges for authentication preparation.

**Tech Stack:** Famulus typed blueprint graph, dispatcher contracts, Markdown LLM interfaces, Python 3, pytest.

## Global Constraints

- Preserve every unrelated typed-blueprint and personal-preference migration change in the working tree.
- Do not stage a mixed-origin untracked sidecar merely to capture one session-owned line.
- `connect-google` must not invoke service setup, account-management, verification, mail, calendar, or Drive interfaces.
- cloud-files, g-calendar, and email-client own their accounts, OAuth tokens, credential storage, verification, and operational API access.
- `connect-google` owns only the canonical Desktop OAuth client and the procedure for creating or installing it.
- Tokens must never cross a dispatcher interface or be printed.
- Use tests first and verify the intended RED failure before changing production contracts.
- Do not commit without explicit user approval.

---

### Task 1: Lock the reversed dependency in contract tests

**Files:**
- Modify: `skills/connect-google/tests/test_service_delegation.py`
- Modify: `skills/connect-google/tests/test_connect_google_llm_routing.py`
- Delete: `skills/connect-google/tests/test_service_bridge.py`

**Interfaces:**
- Consumes: typed root and subordinate blueprint YAML.
- Produces: tests enforcing service-to-connector ownership and the absence of a connector service bridge.

- [x] **Step 1: Replace connector-caller assertions with negative ownership assertions**

In `test_service_delegation.py`, assert that email-client's `accounts-list`,
`accounts-add`, `accounts-update`, `accounts-setup-oauth`, and `live-smoke`
sidecars do not include `connect-google` in `allowed_callers`:

```python
def test_email_interfaces_are_not_exposed_to_connect_google() -> None:
    for relative in EMAIL_INTERFACE_SIDECARS:
        node = load(SKILLS / "email-client" / relative)
        assert "connect-google" not in node.get("allowed_callers", [])
```

Retain the assertions that cloud-files, g-calendar, and email-client default
LLM interfaces use `connect-google.llm.default`. Remove install-assistant-tools
from that list because installation is not a Google service.

- [x] **Step 2: Assert the connector graph contains no service machine edges**

Add a routing-contract assertion that recursively collects
`uses_interfaces` from connect-google's LLM and machine sidecars and rejects
every ID beginning with `cloud-files.machine.`, `g-calendar.machine.`, or
`email-client.machine.`. Assert that the root exposes only client-status and
install-client machine interfaces.

- [x] **Step 3: Remove bridge-specific tests**

Delete `test_service_bridge.py`; its expected interface is forbidden by the
revised design rather than a supported compatibility surface.

- [x] **Step 4: Run focused tests and verify RED**

Run:

```text
python3 -m pytest -q skills/connect-google/tests/test_service_delegation.py skills/connect-google/tests/test_connect_google_llm_routing.py
```

Expected: FAIL because the current connector graph still exposes
`service-bridge` and email-client sidecars still allow `connect-google`.

---

### Task 2: Remove connector-to-service behavior

**Files:**
- Delete: `skills/connect-google/_rtx/_delegation_gateway.py`
- Delete: `skills/connect-google/_rtx/._delegation_gateway.py.blueprint.yaml`
- Modify: `skills/connect-google/blueprint.yaml`
- Modify: `skills/connect-google/.SKILL.md.blueprint.yaml`
- Modify: `skills/connect-google/llm_interfaces/.connect-services.md.blueprint.yaml`
- Modify: `skills/connect-google/llm_interfaces/connect-services.md`
- Modify: `skills/connect-google/SKILL.md` through blueprint synchronization
- Modify: `skills/email-client/_rtx/._email_accounts.py.accounts-list.blueprint.yaml`
- Modify: `skills/email-client/_rtx/._email_accounts.py.accounts-add.blueprint.yaml`
- Modify: `skills/email-client/_rtx/._email_accounts.py.accounts-update.blueprint.yaml`
- Modify: `skills/email-client/_rtx/._email_accounts.py.accounts-setup-oauth.blueprint.yaml`
- Modify: `skills/email-client/_rtx/._email_smoke.py.blueprint.yaml`
- Modify: `skills/cloud-files/_rtx/._oauth_bootstrap.py.blueprint.yaml`
- Modify: `skills/g-calendar/_rtx/._oauth_bootstrap.py.blueprint.yaml`

**Interfaces:**
- Consumes: `connect-google.machine.client-status@1` and `connect-google.machine.install-client@1`.
- Produces: a connector graph with no service machine calls.

- [x] **Step 1: Delete the service bridge implementation and locator**

Remove the bridge runtime, bridge sidecar, and its root locator. The remaining
machine interfaces are exactly:

```text
connect-google.machine.client-status@1
connect-google.machine.install-client@1
```

- [x] **Step 2: Remove service edges from connect-services**

The connect-services LLM sidecar must use only the two connector-owned machine
interfaces. Its authored Markdown must prepare the canonical client, recommend
Drive/Calendar/Gmail with subset selection, and hand each selection to its
owning skill. It must explicitly forbid account lookup, account mutation,
service verification, and service machine calls.

- [x] **Step 3: Restore email-client interface ownership**

Remove only this session's `connect-google` entry from the five email-client
`allowed_callers` lists and from the Drive and Calendar setup sidecars. Leave
every other field from the concurrent typed migration untouched.

- [x] **Step 4: Remove the connector dependency from installation**

Restore install-assistant-tools' default LLM contract and authored Phase 2
wording so it may suggest connecting services conversationally but does not
invoke `connect-google`. Do not touch the concurrent personal-preference
filter implementation or tests.

- [x] **Step 5: Run focused tests and verify GREEN**

Run the Task 1 command again.

Expected: PASS.

---

### Task 3: Align public guidance and generated contracts

**Files:**
- Modify: `README.md`
- Modify: `docs/installation.md`
- Modify: `docs/superpowers/specs/2026-07-14-connect-google-design.md`
- Modify: `docs/superpowers/plans/2026-07-14-connect-google.md` with a supersession note
- Modify generated files only when their diff is attributable to this correction.

**Interfaces:**
- Consumes: corrected connector and service contracts from Task 2.
- Produces: documentation that describes service-owned connection and connector-owned authentication preparation.

- [x] **Step 1: Correct public wording**

State that `connect-google` creates or installs the shared OAuth client and
that each selected service skill then performs its own authorization and owns
its credentials. Remove claims that `connect-google` coordinates service
operations.

- [x] **Step 2: Mark the original plan's orchestration direction as superseded**

Add a short note after the original plan header pointing to this correction
plan. Do not rewrite its completed historical task descriptions.

- [x] **Step 3: Synchronize generated blueprint artifacts**

Run:

```text
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check
```

Inspect every generated change. Do not stage repository-wide artifacts whose
diff also contains the unrelated typed migration.

- [x] **Step 4: Run focused verification**

Run:

```text
python3 -m pytest -q skills/connect-google/tests skills/cloud-files/tests/test_oauth_transaction.py skills/g-calendar/tests/test_calendar_oauth_transaction.py tests/test_officina_oauth_json.py
validators/runner.py
git diff --check
```

Expected: all focused tests and validators pass. If a repository-wide
validator fails solely because the unstaged concurrent migration is
incomplete, report the exact failure without absorbing that migration into
this change.

- [x] **Step 5: Restage the corrected session scope**

Clear the index while preserving the working tree, then stage only wholly
session-owned files and attributable tracked-file hunks. Leave mixed-origin
untracked blueprint sidecars unstaged. Verify the staged list and run
`git diff --cached --check`.
