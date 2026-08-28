# Connect Google Ship-Readiness Changes

**Goal:** Make `connect-google` semantically accurate, deterministic wherever
the repository can decide mechanically, and clear enough that users can give
informed consent and recover from failures without LLM guesswork.

**Release verdict:** Do not distribute the current skill. Release only after
all blocking changes and release gates below pass against the exact checkout
being packaged.

**Scope:** The `connect-google` router, its two authored instruction sources,
its `_rtx` coordinator and client-status results, the shared Google service
scope registry in `src/officina/credentials/google.py`, their tests and
blueprints, the narrowly required Famulus-path and managed-assistant-access
projection for setup journals, the service-owned binding preflight/CAS
contracts required for safe target-bound replacement, the Gmail API migration
prerequisite below, and the exact dispatcher/package verification needed to
distribute them.

## Design rules

- Put stable classification, sequencing, result interpretation, and retry
  routing in machine interfaces.
- Leave informed choices and irreversible approvals to the user. The LLM may
  present those choices, but it must not infer them.
- Do not automate Google Cloud changes unless a future interface has explicit
  Google Cloud authority, a selected project, and a reviewable mutation plan.
- Treat Google policy and UI wording as changeable external facts. State only
  what the workflow needs, cite Google's current documentation, and avoid
  mandatory prose whose truth depends on several publishing states.
- Preserve service ownership: `connect-google` prepares shared credentials;
  each service owns account-change confirmation, persistence, and live
  verification.

## Terminology and exact interface set

| Term | Meaning |
| --- | --- |
| interface version | The version on an Officina interface/export edge. Existing four `_rtx` compatibility interfaces remain version 1; every new interface below starts at version 1. |
| result `schema_version` | The JSON result schema for the new setup family. It starts at 1 and is independent of the interface and skill versions. |
| Skill Version | The generated `SKILL.md` contract version. This change moves `connect-google` from 2 to 3. |
| `status` | The compatibility `client-status@1` top-level value. Its current values and result shape remain unchanged. |
| `reason` | The detailed machine classification returned by `client-status-detail@1`. |
| `next_action` | The only permitted next coordinator transition for the current state. |
| `needs_user_action` | A typed request for a choice or approval. It is never evidence that the user has approved it. |
| declaration | A value supplied by the user, such as audience or publishing state. It is not a verified Google Cloud fact. |

Add these machine interfaces:

| Interface | Arguments | Purpose |
| --- | --- | --- |
| `connect-google._rtx.interface.client-status-detail@1` | optional `--home DIR` | Return the detailed client reason without changing compatibility `client-status@1`. |
| `connect-google._rtx.interface.connection-plan@1` | optional `--services CSV`, `--audience-declaration VALUE`, `--publishing-declaration VALUE`, `--home DIR` | Return the canonical scope/access/input plan. An omitted/empty selection returns all catalog entries unselected so the user can choose. It performs no mutation and makes no service binding-status claim. |
| `connect-google._rtx.interface.setup-prepare@1` | optional `--run-id ID`, `--services CSV`, `--client-file PATH`, `--legacy-candidate-id ID`, `--audience-declaration VALUE`, `--publishing-declaration VALUE`, `--gmail-nickname NAME`, `--account-hint EMAIL|none`, repeatable `--approval ID=approve|decline`, `--browser-mode open-browser|manual-url`, `--callback-port automatic|PORT`, `--home DIR` | Create or iteratively update one bounded setup run. Every call is validation-only: it records declarations and choices, returns the exact connection plan and target-bound approvals, and reaches `ready` only after all required user review. Omission means unresolved, never an implicit choice. |
| `connect-google._rtx.interface.setup-apply@1` | `--run-id ID`, `--attempt-id ID`; optional `--home DIR` | Revalidate a ready run and execute exactly one recorded authorization attempt. It accepts no new service, client, audience, publishing, nickname, account, browser, port, or approval choice. |
| `connect-google._rtx.interface.setup-resume@1` | `--run-id ID`; optional `--home DIR` | Retry only known-safe incomplete bindings using the exact credential descriptor and approvals recorded by iterative prepare. It accepts no fresh choice or approval and cannot invoke OAuth. |
| `connect-google._rtx.interface.setup-cleanup@1` | optional `--home DIR`, `--max-records N` (default and maximum 32) | Lock, revalidate, and delete only eligible expired/terminal setup journals; return inspected/deleted/skipped counts and IDs. It never deletes credential descriptors or secrets. |

Positionals are not introduced. Service IDs are the existing `drive`,
`calendar`, and `gmail` values. `--services` is normalized and deduplicated by
the existing canonical service normalizer.

## Versioned setup result

The four `setup-*` interfaces return this envelope; fields that do not apply are
empty objects or lists rather than omitted:

```json
{
  "schema_version": 1,
  "operation": "prepare|apply|resume|cleanup",
  "overlay": "repair-host|null",
  "run_id": "random opaque identifier",
  "attempt_id": "opaque identifier or null",
  "state": "awaiting-user|awaiting-approval|awaiting-new-authorization|ready|applying|resumable|blocked-uncertain|complete|failed|expired|cleanup-pending",
  "complete": false,
  "next_action": "prepare|apply|resume|cleanup|new-prepare|none",
  "needs_user_action": [
    {
      "id": "run-unique target-bound action identifier",
      "kind": "select-services|select-client|declare-audience|declare-publishing|confirm-scope-review|confirm-client-migration|confirm-client-replacement|confirm-account-change|supply-gmail-nickname|supply-account-hint|choose-browser-mode|choose-callback-port|complete-browser-consent|confirm-new-authorization-after-uncertain-consent|repair-host",
      "target": {"$ref": "#/$defs/action-target-by-kind"},
      "allowed_values": {"$ref": "#/$defs/action-allowed-values-by-kind"}
    }
  ],
  "client": {"$ref": "#/$defs/setup-client"},
  "connection_plan": {"$ref": "connection-plan.schema.json"},
  "authorization": {"$ref": "#/$defs/setup-authorization"},
  "bindings": {"$ref": "#/$defs/setup-bindings"},
  "cleanup": {"$ref": "#/$defs/setup-cleanup"},
  "error": {"$ref": "#/$defs/setup-error"}
}
```

`complete` is true only in state `complete`. Unknown keys in a caller-supplied
choice or approval are rejected. Approval IDs are single-use and possession of
an ID is not consent: callers must supply exactly `ID=approve` or `ID=decline`.
Terminal results never contain client JSON,
tokens, client secrets, authorization URLs, or raw exception text.

The formerly abbreviated nested objects are closed as follows:

```json
{
  "client": {
    "status": "valid|missing|invalid|needs-migration",
    "reason": "the closed client-status-detail reason enum",
    "next_action": "the closed client-status-detail action enum",
    "canonical_path": "normalized path or null",
    "canonical_identity": "sha256 or null",
    "selected_candidate_id": "opaque ID or null",
    "replacement_required": false
  },
  "authorization": {
    "attempt_id": "opaque ID or null",
    "started": false,
    "outcome": "not-started|awaiting-browser|granted|partially-granted|denied|failed|uncertain",
    "subject": "opaque Google subject or null",
    "account": "normalized account email or null",
    "requested": [{"service": "drive|calendar|gmail", "scopes": ["catalog URI"]}],
    "granted": [{"service": "drive|calendar|gmail", "scopes": ["catalog URI"]}],
    "missing": [{"service": "drive|calendar|gmail", "scopes": ["catalog URI"]}],
    "descriptor": {"path": "normalized path or null", "identity": "sha256 or null", "retained": false}
  },
  "bindings": {
    "complete": [{"service": "drive|calendar|gmail", "target_id": "normalized ID", "post_state_id": "sha256"}],
    "incomplete": [{"service": "drive|calendar|gmail", "target_id": "normalized ID or null", "current_state_id": "sha256 or null", "code": "closed preflight/CAS/binder code", "safe_to_resume": false}]
  },
  "cleanup": {
    "inspected_count": 0,
    "inspected_ids": ["opaque ID"],
    "deleted_count": 0,
    "deleted_ids": ["opaque ID"],
    "skipped": [{"run_id": "opaque ID", "reason": "active|changed|locked|ineligible|delete-failed"}],
    "cursor_before": "opaque ID or null",
    "cursor_after": "opaque ID or null"
  },
  "error": {
    "phase": "closed phase or null",
    "code": "phase-valid code or null",
    "retry_class": "none|binding-only|needs-user-action",
    "remediation_id": "closed remediation or null",
    "retry_operation": "prepare|apply|resume|cleanup|null",
    "retry_run_id": "opaque ID or null",
    "retry_attempt_id": "opaque ID or null",
    "retry_arguments_sha256": "sha256 or null"
  }
}
```

Every object has `additionalProperties: false`; arrays are de-duplicated and in
catalog order, scopes are in catalog order, paths are absolute/normalized, and
all union placeholders above become JSON null when inapplicable. `error.phase`
is JSON null when there is no error and otherwise exactly
`client|journal|listener|callback|token_exchange|userinfo|account_check|credential_publish|preflight|binding|cleanup|internal`.
`error.code` is JSON null when there is no error and otherwise exactly one code enumerated in the total outcome table
below for that phase; `remediation_id` is null or exactly that row's
remediation. `retry_class` and all action targets/allowed values are likewise
the closed table values, never free strings. Generated JSON Schemas and
independent fixtures materialize these enums; production data does not define
the test inventory.
Cleanup counts equal their corresponding array lengths; inspected/deleted IDs
are unique, and each skipped run is inspected but not deleted.
Table sources `preflight`, `CAS`, and legacy `binder` map respectively to error
phases `preflight`, `binding`, and `binding`; source prefixes are not duplicated
inside `error.code`.

State invariants are exact: pre-authorization states have `started: false`, no
subject/account/descriptor, and empty granted/missing/bindings; `applying` may
carry only durably recorded phase data; `resumable` requires a granted or
partially-granted outcome, non-null descriptor identity, at least one complete
or incomplete binding, and no uncertain error; `complete` requires every
selected service fully granted and present exactly once in bindings.complete;
`blocked-uncertain` requires an uncertain authorization/binding/journal code
or cleanup/`cursor-integrity-failed`; resetting that unauthenticated cursor
could conceal rollback. Cleanup otherwise has the null/empty special case already stated. Schema and
state-invariant tests cover every state and reject every cross-state field.

For cleanup, `run_id` and `attempt_id` are JSON null. Full success uses state
`complete`, `complete: true`, and `next_action: none`; a recoverable deletion
failure uses `cleanup-pending`, `complete: false`, and `next_action: cleanup`.
Only `cleanup` carries operation-specific data. For non-cleanup operations, `cleanup` contains zero
counts, empty collections, and null cursors. JSON null—not a placeholder string—is also used for an
inapplicable attempt ID.

The action contract is closed and fixture-versioned:

| Action kind | Exact target object (`additionalProperties: false`) | Allowed values / resolution |
| --- | --- | --- |
| `select-services` | `{catalog_version: integer, offered_services: service-id[]}` | nonempty subset of offered IDs; iterative prepare |
| `select-client` | `{candidates: [{id: opaque-id, identity: sha256, source: canonical\|legacy}]}` | one listed ID or a new validated `--client-file`; iterative prepare |
| `declare-audience` | `{run_id: opaque-id}` | `internal`, `external`, `unknown`; iterative prepare |
| `declare-publishing` | `{run_id: opaque-id}` | `testing`, `in-production`, `unknown`; iterative prepare |
| `confirm-scope-review` | `{catalog_version: integer, selected_services: service-id[], service_scopes: [{service: service-id, scopes: uri[]}], access_summary_hashes: [{service: service-id, sha256: sha256}], warning_records: [{code: warning-code, record_sha256: sha256}]}` | `approve`, `decline`; iterative prepare; any target-content/applicability change invalidates it |
| `confirm-client-migration` | `{candidate_id: opaque-id, legacy_identity: sha256, destination_path: normalized-path, destination_identity: sha256-or-null}` | `approve`, `decline`; iterative prepare |
| `confirm-client-replacement` | `{destination_path: normalized-path, current_identity: sha256, proposed_identity: sha256}` | `approve`, `decline`; iterative prepare |
| `confirm-account-change` | `{service: service-id, target_id: normalized-id, current_state_id: sha256, proposed_subject: nonempty-string, proposed_account: email, descriptor_identity: sha256}` | `approve`, `decline`; iterative prepare, then CAS resume |
| `supply-gmail-nickname` | `{service: "gmail", home: normalized-path}` | one nonempty string; iterative prepare; binder validates without enumeration |
| `supply-account-hint` | `{selected_services: service-id[], home: normalized-path}` | one normalized email or JSON null; iterative prepare |
| `choose-browser-mode` | `{platform: "linux"\|"macos"\|"windows", headless: boolean}` | `open-browser`, `manual-url`; iterative prepare |
| `choose-callback-port` | `{platform: "linux"\|"macos"\|"windows", host: "127.0.0.1"}` | integer 1024..65535 or `automatic`; iterative prepare |
| `complete-browser-consent` | `{attempt_id: opaque-id, selected_services: service-id[]}` | no assistant value; user acts in Google and apply records callback outcome |
| `confirm-new-authorization-after-uncertain-consent` | `{prior_attempt_id: opaque-id, selected_services: service-id[], explanation_code: "callback-outcome-unknown"}` | `approve`, `decline`; iterative prepare creates a new attempt only after approval |
| `repair-host` | `{remediation_id: closed-remediation-id, retry_operation: "prepare"\|"apply"\|"resume"\|"cleanup", retry_arguments_sha256: sha256}` | no approval value; repair, then exact retry contract |

Every listed property is required. `opaque-id`, `sha256`, normalized path/ID,
URI, email, service ID, warning code, and nullable unions are shared `$defs` in
the proposed JSON Schema; arrays have `uniqueItems: true` and the catalog-order
constraint. `allowed_values` is an array of the exact scalar choices described
above and is empty only for browser consent or host repair.

The initial prepare with no services necessarily returns `select-services`;
services supplied on the initial call are treated as a proposal and still
require `confirm-scope-review`. An action ID is valid only for the exact canonical target serialized in its
journal entry. Any changed target creates a new ID and invalidates the old ID.
Decline is terminal for that action and cannot be replayed as approval.

The state/operation contract is total:

| State | Required/allowed action | `next_action` | Accepted coordinator call |
| --- | --- | --- | --- |
| `awaiting-user` | named non-approval choices or `repair-host` | `prepare` | iterative prepare with only named fields |
| `awaiting-approval` | target-bound approval actions | `prepare` | iterative prepare with exact action dispositions |
| `awaiting-new-authorization` | duplicate-consent warning approval | `prepare` | iterative prepare; approval creates a new attempt |
| `ready` | none | `apply` | apply with current run/attempt IDs |
| `applying` | none; operation/recovery owns progress | `none` | duplicate apply returns recorded state; stale apply invokes internal reconciliation |
| `resumable` | none | `resume` | resume with run ID only; a discovered binder choice atomically returns an awaiting state |
| `blocked-uncertain` | none in this design | `none` | cleanup only after retention; a new run requires explicit diagnosis |
| `complete` | none | `none` | idempotent result read or cleanup |
| `failed` | none | `none` or `new-prepare` as recorded | a new prepare without the old run ID when explicitly permitted |
| `expired` | none | `new-prepare` | a new prepare without the old run ID |
| `cleanup-pending` | none | `cleanup` | cleanup recovery only; no other setup operation is accepted |

No operation accepts fields outside its row. State transitions and returned
`next_action` are one immutable coordinator table tested as a complete
state/operation rejection matrix.

`repair-host` is a response-only action overlay, never a durable state and an
explicit exception to the durable matrix above:

| Overlay | Reported state | Required action | `next_action` | Accepted retry |
| --- | --- | --- | --- | --- |
| `repair-host` with authenticated journal | last authenticated durable state | exact `repair-host` target | original operation | exact retry fields/digest below |
| `repair-host` without authenticatable journal | response sentinel `failed` | exact `repair-host` target | original operation | exact retry fields/digest below |

While `overlay` is non-null, durable-row action restrictions do not apply and
only this overlay branch is valid; no other action or operation is accepted. If the
last journal/head pair can be authenticated, the response reports that durable
state unchanged. If authentication cannot be attempted, it reports
`state: failed` only as a response sentinel, with no claim that the journal was
transitioned. Initial key creation failure has `run_id: null`; failure for an
existing call echoes only the caller-supplied run ID and attempt ID without
trusting journal contents. These overlays have no retention rule because they
create no state; retention continues from the last authenticated journal.

The retry contract is exact: failed initial prepare repeats the original
prepare choices; iterative prepare repeats the same run ID and named choices;
apply repeats `--run-id`, `--attempt-id`, and home; resume repeats run ID/home;
cleanup repeats home/max-records. The response error adds
`retry_operation`, `retry_run_id`, `retry_attempt_id`, and
`retry_arguments_sha256`; all are null when retry is prohibited. The digest
binds the normalized original argv without returning private arguments.
Two-phase recovery runs before a repeated operation and either continues an
unstarted committed transition or returns its recorded result; no effect is
blindly repeated.

`client-status-detail@1` has its own exact result schema:

```json
{
  "schema_version": 1,
  "status": "valid|missing|invalid|needs-migration",
  "reason": "valid|missing|migration-required|secret-store-unavailable|client-secret-missing|malformed-client|unsafe-client|unsupported-client-type",
  "next_action": "reuse|select-client|confirm-migration|restore-secret-store|restore-client-secret|replace-client|select-desktop-client",
  "path": "normalized canonical path",
  "client_type": "desktop|none|unknown",
  "canonical_identity": "sha256 or null",
  "legacy_candidates": [
    {"id": "run-independent sha256-derived ID", "service": "service ID", "path": "normalized path", "identity": "sha256"}
  ],
  "legacy_candidates_match": true
}
```

The reason/action mapping is one-to-one in the order shown above: `valid` to
`reuse`, `missing` to `select-client`, `migration-required` to
`confirm-migration`, `secret-store-unavailable` to `restore-secret-store`,
`client-secret-missing` to `restore-client-secret`, `malformed-client` and
`unsafe-client` to `replace-client`, and `unsupported-client-type` to
`select-desktop-client`. `legacy_candidates_match` is false when candidates
differ and false when fewer than two exist. Paths and identities are
user-private. Compatibility `status` is `valid` only for reason `valid`,
`missing` only for `missing`, `needs-migration` only for
`migration-required`, and `invalid` for every other detailed reason.

Client identity is SHA-256 of canonical validated client JSON bytes, so the
secret-bearing payload is an input to the one-way identity even though no
secret value is returned or journaled. Tests prove the result is a fixed-size
digest and never contains any payload field. This identity detects a changed
secret as well as a changed client ID; the plan does not make the inaccurate
claim that secret values are absent from the digest input.

`connection-plan@1` returns:

```json
{
  "schema_version": 1,
  "catalog_version": 1,
  "audience": {"value": "internal|external|unknown", "provenance": "user-declared|default-unknown"},
  "publishing": {"value": "testing|in-production|unknown", "provenance": "user-declared|default-unknown"},
  "selected_services": ["drive"],
  "services": [
    {
      "id": "drive",
      "display_name": "Google Drive",
      "scopes": ["exact URI"],
      "access_summary": "versioned reviewed text",
      "access_summary_sha256": "sha256",
      "source_url": "official Google URL",
      "policy_reviewed_on": "YYYY-MM-DD",
      "requires_api": "drive|calendar|gmail|none",
      "required_inputs": [],
      "selected": true
    }
  ],
  "warnings": [{"code": "stable code", "text": "versioned reviewed warning", "applies_to": ["service ID"], "applies_when": "closed predicate ID", "source_url": "official Google URL", "policy_reviewed_on": "YYYY-MM-DD", "warning_record_sha256": "sha256"}],
  "needs_user_action": [{"kind": "select-services|declare-audience|declare-publishing", "allowed_values": {"$ref": "#/$defs/plan-action-values-by-kind"}}],
  "scope_review_required": true
}
```

`services` always contains all three catalog entries in stable order. An empty
`selected_services` is valid only for read-only planning or an awaiting-user
prepare; `apply` requires a nonempty selection and exactly controls each
`selected` boolean. Allowed service IDs, declaration values, required-input names,
warning codes, and API values are closed enums enforced by JSON fixtures.
Unknown declarations produce their corresponding user action and conservative
warning; declarations are never presented as observed project facts.
For `select-services`, allowed values are the three service IDs; for audience
they are `internal`, `external`, `unknown`; for publishing they are `testing`,
`in-production`, `unknown`. No standalone plan action has an empty values list.
The warning-code enum is exactly `audience-unknown`, `publishing-unknown`,
`testing-test-user-limit`, `testing-seven-day-authorization`,
`unverified-new-user-cap`, `restricted-scope-verification-possible`,
`sensitive-scope-verification-possible`, `shared-oauth-project`, and
`gmail-api-required`; applicability is a tested truth table over declarations
and selected services.
Each warning code maps to one normative versioned `text`;
`warning_record_sha256` is SHA-256 of canonical JSON containing code, exact
UTF-8 text, catalog-ordered `applies_to`, closed `applies_when` predicate ID, source URL,
and review date. The LLM presents that returned text without
paraphrasing it as a stronger claim. `confirm-scope-review.warning_records`
exactly equals the selected plan warnings, and any text/hash/applicability
change invalidates approval. Policy-ledger validation checks the text, hash,
source URL, review date, and applicability together.

The authoritative UTF-8 records live at
`skills/connect-google/_rtx/policy/google_oauth_warnings.v1.json`; this table is
their human-readable projection. Predicate IDs and texts are exact:

| Code / predicate | `applies_to` | Exact warning text | Source | Reviewed | Expected SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `audience-unknown` / `audience==unknown` | `[]` | Tell Famulus whether the Google Auth Platform audience is Internal or External; Famulus cannot determine it from the Desktop client file. | <https://support.google.com/cloud/answer/15549945> | 2026-08-26 | `a71b248e7e8416b3675fe75ba916b18ecce679049bd18b1c1991a73fc5fa3e72` |
| `publishing-unknown` / `audience==external && publishing==unknown` | `[]` | Tell Famulus whether the External app is in Testing or In production; Famulus cannot determine publishing status from the Desktop client file. | <https://support.google.com/cloud/answer/15549945> | 2026-08-26 | `c3d6e8dfb818386f8b47a98c57b198da3f74ddc79885e63fe7e6e33b619fff65` |
| `testing-test-user-limit` / `audience==external && publishing==testing && selected_services!=empty` | `["drive","calendar","gmail"]` | For these non-basic scopes, an External Testing app can be authorized only by listed test users, up to 100; a Brand Account can authorize when managed by a listed test user. | <https://support.google.com/cloud/answer/15549945> | 2026-08-26 | `3dc33f101d7b81c5de22063fb352f5ca3230900a3fa93c7e0f850637f50b6a7d` |
| `testing-seven-day-authorization` / `audience==external && publishing==testing && selected_services!=empty` | `["drive","calendar","gmail"]` | For this External Testing app and these non-basic scopes, a test user's authorization expires after seven days. | <https://support.google.com/cloud/answer/15549945> | 2026-08-26 | `d966dc829069f50d874eae8738d9f4fe6128ced8cdc51e95ba2503e2fa330bb2` |
| `unverified-new-user-cap` / `audience==external && publishing==in-production` | `["drive","calendar","gmail"]` | An External app in production that remains unverified may be subject to Google's lifetime 100-new-user cap. | <https://support.google.com/cloud/answer/13464323> | 2026-08-26 | `d29b836631414c0755778846995e3fd0646b71b1f634a669dced11188baf4be1` |
| `restricted-scope-verification-possible` / `selected_services intersects {drive,gmail}` | `["drive","gmail"]` | The selected Drive scope or Gmail read scope is restricted; verification requirements depend on audience, data handling, and Google's applicable exceptions. | <https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification> | 2026-08-26 | `aa3cf1f9c44b3cf6cd27dd298c9e9195074d507ae9d42a69d1a95b22c1cc53e3` |
| `sensitive-scope-verification-possible` / `selected_services intersects {calendar,gmail}` | `["calendar","gmail"]` | The selected Calendar scope or Gmail send scope is sensitive and may require sensitive-scope verification. | <https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification> | 2026-08-26 | `98978e97d5b7087ad73efbba0bf13c5f2da3579fd900409bea40efdd42d39dcc` |
| `shared-oauth-project` / `selected_services!=empty` | `["drive","calendar","gmail"]` | Everyone using this OAuth client shares one application identity and its project-level availability and quotas. | <https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance> | 2026-08-26 | `dab2315b5f4623d16f0f0922483644b370b20f43f2ba67aeef451ba11580dcdc` |
| `gmail-api-required` / `gmail in selected_services` | `["gmail"]` | Selecting Gmail requires the Gmail API to be enabled in the chosen Google Cloud project. | <https://developers.google.com/workspace/gmail/api/quickstart/python> | 2026-08-26 | `ded69d6b43c2c1ebf2962612c18e314758273993cfb7dd5f3dcbece37c3e2aa1` |

The fixture also stores `applies_to` (`[]` for declaration-level warnings,
otherwise the exact affected service subset), source URL, and release-refreshed
review date. Its schema closes predicate IDs to the nine expressions above;
arbitrary executable predicates are forbidden. Any record change increments
`catalog_version`, changes its record hash, and invalidates prior scope-review
actions. Tests recalculate all hashes from this fixture and independently
assert the complete truth table from
`skills/connect-google/_rtx/tests/fixtures/google_oauth_warning_truth.v1.json`.

Hash bytes are reproducible without library defaults: serialize exactly one
record with keys in this lexicographic order—`applies_to`, `applies_when`,
`code`, `policy_reviewed_on`, `source_url`, `text`—using the array order shown,
JSON double-quote/backslash/control escaping, no ASCII escaping for other
Unicode, comma/colon separators with no whitespace, UTF-8 without BOM, and no
terminal newline. Hash those bytes with SHA-256. A validator compares all nine
results to the fixed hashes above; fixture order or serializer drift is red.

A selected service is `granted` only when every catalog scope for that service
appears in Google's returned grant. A subset of Gmail's two scopes leaves Gmail
incomplete and unbound, while independently fully granted services may bind.
The result reports exact granted and missing scope arrays per service; it never
upgrades a partial per-service grant.

The catalog's user-facing access text is normative, source-mapped, and dated:

| Service | Requested scopes after prerequisites | Required access summary | Official source |
| --- | --- | --- | --- |
| Drive | `https://www.googleapis.com/auth/drive` | View and manage all files in the user's Google Drive. | Google Drive API scope guide |
| Calendar | `https://www.googleapis.com/auth/calendar` | See, edit, share, and permanently delete calendars the user can access. | Google Calendar API scope guide |
| Gmail | `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/gmail.send` | View and download the user's Gmail messages and attachments, view Gmail settings, and send email on the user's behalf. | Google Gmail API scope guide |

Each catalog record contains `source_url`, `policy_reviewed_on`, and the exact
access-summary fixture hash. Warning records carry the same source metadata.
The release ledger enumerates every user-facing Google-policy and scope claim
from catalog and instructions; a validator fails on an unmapped source,
missing review date, changed fixture hash, or claim not adjudicated against its
official source. The review date must be refreshed for the release candidate;
it is evidence of review, not a claim that Google policy cannot later change.
Gmail's catalog entry has `requires_api: gmail`; the create-client route and
live-case health check explicitly require the Gmail API to be enabled whenever
Gmail is selected. `scope_review_required` is advisory in the standalone
read-only plan and is not an action kind. Only setup emits the target-bound
`confirm-scope-review` action and setup cannot become ready without resolving
it.

## Setup-run ownership, staleness, and replay rules

`setup-prepare` owns a non-secret journal under the selected Famulus state root
at `connect-google/setup-runs/<run_id>.json`, created with mode `0600` and an
exclusive non-following create. The record contains:

- schema and run ID;
- creation and expiry times;
- selected home and platform;
- detailed client status;
- SHA-256 identities of the canonical client file and each legacy candidate,
  computed from canonical validated JSON without writing its secret values
  into the journal;
- the canonical service-catalog version;
- user declarations, always labelled `provenance: user-declared`;
- target-bound approval requests and their disposition;
- an append-only transition/outcome sequence; and
- after authorization, the normalized credential descriptor path, its SHA-256
  file identity, granted services, and incomplete bindings.

Every journal update is written to a same-directory temporary file, flushed,
atomically replaced, and followed by a directory `fsync` before any external
effect begins. The journal stores canonicalized validated-payload identities,
not raw client JSON. Transition records store normalized codes and identifiers,
not raw binder messages, diagnostic streams, or authorization URLs. Mode
`0600`, a mode-`0700` parent, no-follow opens, and assistant-root confinement
protect user-private paths and account metadata; no claim of encryption at
rest is made. They are not sufficient journal integrity. On first use the
coordinator creates a random journal-authentication key in the native secret
store under a dedicated namespace. Every journal is an HMAC-authenticated,
canonical JSON envelope; the HMAC covers all fields, including declarations,
action targets/dispositions, transitions, and descriptor identity. The key is
never stored under an assistant-writable root or returned by an interface.
Journal/head commits use a recoverable two-phase protocol. The coordinator
first writes a native-secret-store head containing the old committed
generation/MAC plus the proposed pending generation/MAC, then atomically
replaces/fsyncs the journal, then promotes pending to committed and clears the
pending fields. On recovery, a journal matching committed causes an abandoned
pending proposal to be cleared; one matching pending causes pending to be
promoted; anything else is rollback/tamper evidence. Tests crash after every
write/fsync/promotion boundary and prove one of those two outcomes.
Missing-key, invalid-MAC, rollback-counter, and malformed-envelope states are
terminal `journal-integrity-failed`; they cannot be repaired by accepting the
file. Tests mutate every security-relevant field and prove rejection. The
threat model trusts the coordinator/dispatcher and the user's OS account; it
does not claim to resist a hostile same-user process that can independently
extract native-keyring secrets.

Preparation expires after 30 minutes of inactivity while it is still
`awaiting-user`, `awaiting-approval`, `awaiting-new-authorization`, or `ready`. Iterative `setup-prepare`
updates the same locked run and recomputes the connection plan after each
choice. It creates authorization attempt 1 only when all required declarations,
target-bound `confirm-scope-review` approval, inputs, target-bound replacement approval, browser mode, and
callback choice are resolved; the result then supplies its opaque
`attempt_id`.

`setup-apply` locks the record,
recomputes all machine-observed identities, and rejects missing, expired,
tampered, or mismatched state before effects. An attempt ID is single-use and
is consumed by the first accepted apply call even if failure occurs before
OAuth. Starting apply moves the journal to `applying`; a repeated call with the
same attempt ID returns the recorded state and never starts a second OAuth
flow. Before each external effect the coordinator records intent,
and after it returns records the known outcome. An interrupted effect whose
outcome cannot be proven becomes `blocked-uncertain` and is never retried
automatically.

Callback timeout and access denial close that attempt epoch. Timeout is
classified as uncertain remote consent: Google may have recorded consent even
though no callback arrived. The result asks whether the user wants a new
consent attempt and explains the possible
duplicate remote grant. Only an explicit target-bound approval submitted to
iterative `setup-prepare` creates the next monotonically numbered attempt and a
new opaque attempt ID. Applying an old, current, concurrent, declined, or
already-consumed attempt ID cannot start OAuth.

Known pre-consent failures close the consumed attempt and return the same run
to `awaiting-user` with `next_action: prepare`. Iterative prepare reruns the
relevant validation after the user repairs or changes only the named target;
if every prerequisite is again valid, it issues a new monotonically numbered
attempt ID and returns `ready`. Failures after OAuth begins never use this
pre-consent rule. A row requiring `new-prepare` makes the current run terminal
and starts from a new run ID; `binding-only` preserves the authorization
attempt/descriptor and permits only resume. Thus no result tells the caller to
reuse a consumed attempt ID.

On recovery, an `applying` journal is reconciled under the same lock. An intent
with a recorded outcome returns or advances from that outcome; a state with no
recorded external intent may continue. Each effect has one explicit reconciler:

| Effect | Reconciler after intent without outcome | Result if not provable |
| --- | --- | --- |
| canonical client installation | rerun read-only client detail and compare the recorded proposed identity | return to awaiting-user if absent; `blocked-uncertain` if a different identity now occupies the target |
| OAuth browser consent or token exchange | no API proves whether remote consent/code use occurred | `blocked-uncertain`; this run offers no restart action, and external Google-account consent diagnosis/revocation is required before a separately initiated run |
| descriptor publication | load the exact recorded descriptor path and compare its authenticated subject, services, and file identity | continue if exact; `blocked-uncertain` otherwise |
| binder preflight | rerun the read-only preflight and compare current-state identity | continue only if exact; otherwise invalidate approval and return to awaiting-approval |
| binder compare-and-set mutation | rerun preflight and require either the recorded post-state identity or the unchanged pre-state identity; the CAS binder contract guarantees no other state | record success for exact post-state, safely retry for exact pre-state, `blocked-uncertain` otherwise |

No generic rule converts every missing outcome into the same state. Unknown
effects have no reconciler and become `blocked-uncertain`. Concurrent apply or
resume processes see the locked current state and cannot start duplicate work.

`setup-resume` accepts only a `resumable` run. It reloads the descriptor through
`load_credential_file`, verifies its normalized path and SHA-256 identity
against the journal, and rejects missing, changed, replayed, or mismatched
descriptors. It retries only incomplete binders whose prior result is a known
safe failure. A repeated completed resume returns the recorded terminal result.
When resume/preflight discovers a binder choice, that same locked operation
transitions `resumable` to `awaiting-user` or `awaiting-approval` and returns
`next_action: prepare`; prepare is never accepted directly from `resumable`.
Iterative `setup-prepare` may then record only the specific non-effect choice
named by its binder `needs_user_action`—for example, a corrected Gmail nickname
or account-change approval—and returns the run to `resumable` with
`next_action: resume`. It cannot change
services, client, audience, publishing declaration, Google account, browser
mode, callback port, descriptor, or completed binding outcomes.

An `applying` run becomes stale after 15 minutes without a durable transition;
the next coordinator call must reconcile each recorded effect as described
below, and cleanup may not delete it. A `resumable` run expires after 7 days of
inactivity and becomes `failed` while retaining its descriptor identity and
incomplete-binding notice. Expired preparation journals are deleted after 24
hours. Completed, failed, and blocked journals are retained for 30 days from
their last transition. Thus every state has either an inactivity transition or
an explicit retention deadline. `setup-prepare`
opportunistically invokes the same cleanup implementation before creating a
new run, bounded to 32 records; `setup-cleanup` exposes an explicit invocation
with the same hard maximum. Cleanup locks and revalidates each journal after
selection, skips active/changed/locked/ineligible records, deletes only one
eligible journal at a time, and records inspected/deleted/skipped IDs in its
result. Eligible deletion first commits an authenticated `cleanup-pending`
journal through the normal two-phase head protocol and atomically renames it
into a mode-`0700` `cleanup-pending/` subdirectory, then deletes that run's
head, then deletes/fsyncs the journal directory. Recovery with both artifacts
continues at head deletion; recovery with only the authenticated
`cleanup-pending` journal deletes the journal. Thus the run ID remains durable
until the head is gone and no orphan-head index is required. Failures remain
`cleanup-pending` and are retried by explicit or opportunistic cleanup.
Cleanup never deletes credential descriptors, refresh-token entries, or the
shared journal-authentication key.

Each cleanup call processes pending-subdirectory entries first, oldest mtime
then run ID. Remaining journal filenames are traversed in lexical order from a
durable HMAC-authenticated cursor stored with the global cleanup head; after at
most 32 inspections the cursor advances past the last inspected filename and
wraps at end. Insertions cannot reset it. Cursor update failure is
`cleanup/head-write-failed` and is recovered by the same committed/pending
protocol. Tests maintain more than 64 permanently active/ineligible records
around eligible records and prove that repeated calls reach every eligible and
pending entry within three full directory traversals. This provides bounded
per-call work without starvation.
Revocation and abandoned-credential
cleanup are explicitly deferred to a separate credential-lifecycle design;
the setup result must identify retained descriptors so the user is informed.

The journal root requires a new managed assistant-access root,
`connect_google_state_root`, derived as
`FamulusPaths.state_root / "connect-google"`. The installer must project exactly
that root—not the general state root—into Codex and Claude writable-root
configuration in both standard and development contexts. Installation,
doctor, relocation, uninstall, overlap/symlink rejection, and three-platform
path tests must cover it before the setup coordinator is enabled. This write
projection is treated as availability, not integrity; integrity depends on the
secret-store HMAC. The secret-store namespace and journal key lifecycle are
included in install/doctor/uninstall tests, but uninstall requires explicit
confirmation before deleting the key while retained journals exist.

## Audience and publishing declarations

The coordinator cannot observe Google Auth Platform audience or publishing
state from a Desktop client JSON. It therefore accepts only these declarations:

- audience: `internal`, `external`, or `unknown`;
- publishing: `testing`, `in-production`, or `unknown`.

Every supplied value is stored and returned as `user-declared`; omitted values
become `unknown`. Internal guidance is offered only when the user declares that
the project is owned by a Google Workspace or Cloud Identity organization and
all intended users belong to that same organization. Administrator approval
may still be required. Unknown values receive conservative generic guidance
and a question, never a claim about the project. Declarations live only in the
setup-run journal and are not reused as facts in a later run.

## Compatibility and migration decision

This is an additive cutover:

| Existing artifact or caller | Version-3 behavior | Removal rule |
| --- | --- | --- |
| `client-status@1` | Byte-compatible projection with existing `valid`, `missing`, `invalid`, and `needs-migration` statuses. New routes use `client-status-detail@1`; `migration-required` is only a detailed `reason`, not a renamed compatibility status. | Retain through Skill Version 3. Earliest removal is Version 4 after all reverse consumers migrate and a repository search proves zero pins. |
| `install-client@1` | Retained unchanged and used internally by setup apply. | Same Version-4/zero-consumer rule. |
| `connect-services@1` and `bind-credential-file@1` | Retained with their current output contracts. The new coordinator calls their underlying behavior but does not require old callers to parse new fields. | Same Version-4/zero-consumer rule. |
| credential descriptor `schema_version: 1` | Remains the only accepted descriptor. Apply creates it and resume validates it through the existing loader. | No descriptor migration in this work. |
| service-owned binder interfaces | Existing IDs/version 1 remain unchanged for old callers. Each service adds new read-only preflight and compare-and-set binder IDs, each starting at interface version 1, for the coordinator. | Old IDs follow the Version-4/zero-consumer rule; the coordinator never uses their non-atomic account-change flag. |
| affected email-client mail/send interfaces | Bump to version 2 with the provider-neutral success ID/order/date semantics and Google-path structured errors in the Gmail prerequisite; update every repository pin and publish a breaking migration note. Non-Google failure output remains the v1 compatibility contract. | Version 1 is not advertised as provider-neutral and is removed only after repository zero-pin evidence; existing installed v1 remains tied to its prior package. |
| public `connect-google.interface.default/setup/connect-services/create-client` | IDs and interface versions remain stable; authored instructions route new work through the new coordinator. | No public cutover. |

Tests must cover old caller/old result, new caller/new result, existing
descriptor, new descriptor, mixed compatibility-interface/new-coordinator use,
and zero-consumer evidence before any later removal. Regenerate all affected
blueprints, bump Skill Version to 3, update every reverse-consumer pin only if
its declared interface edge changes, and refresh certification only after the
behavior and generated artifacts pass.

### Service-owned binding precondition contract

Drive, Calendar, and Gmail retain persistence and live-verification ownership.
Each exports a v1 read-only binding preflight accepting the proposed descriptor
and its existing target selector (Gmail accepts a user-entered nickname). It
returns a secret-free `current_state_id`, proposed descriptor subject/account,
normalized target ID, whether replacement approval is required, and a
postcondition identity. The state ID is a service-owned SHA-256 over canonical
security-relevant binding state and configuration generation; it reveals no
token or password.

The compare-and-set binder requires `--expected-current-state-id`,
`--expected-proposed-subject`, and, only when preflight required it, the exact
target-bound approval receipt. Under the service's existing mutation lock it
recomputes the current identity, rejects stale preconditions, performs live
verification, persists once, and returns the postcondition identity. A changed
binding or configuration between preflight and mutation therefore cannot be
overwritten by an old approval. Unknown current identity is no longer an
approvable target: inability to compute it is a typed preflight failure.
Parameterized contract tests apply the same race, stale-approval, subject
mismatch, no-mutation-on-live-check-failure, and idempotent postcondition cases
to all three service implementations.

The exact new service exports are:

| Service | Read-only preflight | Compare-and-set binder |
| --- | --- | --- |
| Drive | `cloud-files._rtx.interface.preflight-google-credential-file@1` | `cloud-files._rtx.interface.use-google-credential-file-cas@1` |
| Calendar | `online-calendar._rtx.interface.preflight-google-credential-file@1` | `online-calendar._rtx.interface.use-google-credential-file-cas@1` |
| Gmail | `email-client._rtx.interface.accounts-preflight-google-credential-file@1` | `email-client._rtx.interface.accounts-use-google-credential-file-cas@1` |

These are additive IDs; every new export begins at interface version 1,
avoiding two incompatible contracts on one ID.

All six interfaces use options only. Preflight requires
`--credential-file FILE` and optional `--home DIR`; Gmail additionally requires
`--nickname NAME`. CAS requires those fields plus
`--expected-current-state-id ID`, `--expected-proposed-subject SUBJECT`,
`--expected-proposed-account EMAIL`, and
optional `--approval-receipt RECEIPT` (required exactly when preflight said
approval was required). Their exact version-1 results are:

```json
{
  "schema_version": 1,
  "operation": "preflight",
  "service": "drive|calendar|gmail",
  "status": "ready|approval-required|failed",
  "target_id": "normalized secret-free service target",
  "current_state_id": "sha256 or null",
  "proposed_subject": "validated opaque Google subject or null",
  "proposed_account": "validated account email or null",
  "proposed_descriptor_identity": "sha256",
  "expected_post_state_id": "sha256 or null",
  "requires_approval": false,
  "error": {"code": "none|invalid-descriptor|invalid-target|current-state-unavailable|invalid-service-config"}
}
```

```json
{
  "schema_version": 1,
  "operation": "bind-cas",
  "service": "drive|calendar|gmail",
  "status": "bound|failed|uncertain",
  "target_id": "normalized secret-free service target",
  "current_state_id": "sha256 or null",
  "post_state_id": "sha256 or null",
  "persisted": "true|false|null",
  "error": {"code": "none|invalid-descriptor|invalid-target|current-state-unavailable|approval-required|invalid-approval-receipt|stale-precondition|proposed-subject-mismatch|proposed-account-mismatch|live-check-failed|live-account-mismatch|cas-lock-failed|persistence-outcome-uncertain|postcondition-mismatch|invalid-service-config"}
}
```

Unknown keys/codes are rejected; an unknown/malformed result is represented by
the coordinator's existing terminal binder/`invalid-binding-result` row, not by
the service schema. `current-state-unavailable` never returns an
approvable target. `persisted` may be true only with `status: bound` and the
exact expected post-state ID; it is false for every known pre-persistence
failure; it is null exactly for `status: uncertain` with
`persistence-outcome-uncertain` or `postcondition-mismatch`. Status `failed`
cannot carry null. Tests cover every status/code/persisted combination.

Preflight invariants are total:

| Status | `requires_approval` | Required non-null identity fields | Error code |
| --- | --- | --- | --- |
| `ready` | false | target, current state, proposed subject/account, descriptor identity, expected post state | `none` |
| `approval-required` | true | the same complete identity set | `none` |
| `failed` | false | all six identity fields null | `invalid-descriptor` |
| `failed` | false | proposed subject/account and descriptor identity non-null; target, current state, expected post state null | `invalid-target` |
| `failed` | false | target, proposed subject/account, descriptor identity non-null; current state and expected post state null | `current-state-unavailable` |
| `failed` | false | target, proposed subject/account, descriptor identity non-null; current state and expected post state null | `invalid-service-config` |

The service JSON uses JSON null for absent identity/error fields; `error.code`
uses literal `none` only in this service-owned schema. JSON Schema `oneOf`
branches encode every row and reject every other combination.

An approval receipt is canonical JSON plus HMAC, encoded as base64url. Its
closed payload contains schema version, action/run IDs, disposition `approve`,
service, normalized target ID, current-state ID, proposed opaque subject,
proposed account email and descriptor identity, issued/expiry times, and random nonce. The coordinator
signs it with the journal-authentication key only after recording the matching
user action. The binder uses shared verification code that obtains the key
without exposing it; under its mutation lock it verifies signature, expiry,
disposition, both descriptor identity fields, every target field, expected
precondition, and nonce replay. On
success it persists the nonce with the new binding/post-state identity. A
concurrent replay then returns the exact idempotent postcondition; a receipt
cannot authorize another target or proposed subject. Direct dispatcher callers
can supply bytes but cannot fabricate a valid receipt.
Receipts remain inside the authenticated journal/coordinator-to-binder call;
they are never returned in the public setup envelope, diagnostics, or evidence
ledger.

## Exact expected file scope

Expected authored or implementation changes are limited to:

- `skills/connect-google/SKILL.md`;
- `skills/connect-google/instructions/create-client.md`;
- `skills/connect-google/instructions/connect-services.md`;
- `skills/connect-google/_rtx/_client_config.py`;
- `skills/connect-google/_rtx/_connect_services.py`;
- a new `skills/connect-google/_rtx/_service_catalog.py`;
- `skills/connect-google/_rtx/policy/google_oauth_warnings.v1.json` and its
  closed schema/hash tests, plus independently authored
  `skills/connect-google/_rtx/tests/fixtures/google_oauth_warning_truth.v1.json`;
- a new `skills/connect-google/_rtx/_setup_coordinator.py`;
- `src/officina/credentials/google.py`, only to keep the canonical scope map and
  expose the stable service-catalog version needed for equality checks;
- `src/officina/common/famulus_paths/__init__.py`, adding only
  `connect_google_state_root`;
- `src/officina/install/assistant_access.py` and
  `src/officina/install/doctor.py`, projecting and diagnosing only that new
  narrow root;
- `skills/install-assistant-tools/SKILL.md` and its `_rtx` install, doctor,
  assistant-access configuration, uninstall, and end-to-end tests where the
  enumerated managed-root contract changes;
- `tests/test_officina_famulus_paths.py`,
  `tests/test_officina_assistant_access.py`,
  `tests/test_assistant_access_blueprint_contracts.py`, and
  `tests/test_install_context_consumers.py`;
- focused tests under `skills/connect-google/_rtx/tests/` and shared credential
  tests under `tests/`;
- Drive, Calendar, and Gmail service blueprint/interface adapters and focused
  tests needed only for the exact preflight and compare-and-set exports above;
- `skills/email-client/_rtx/_imap_gateway.py`, `_smtp_transport.py`, new
  `_gmail_api_gateway.py`, `_gmail_api_transport.py`, `_provider_message_id.py`,
  and `_google_send_transport.py`, plus `_email_smoke.py`,
  `blueprints/rtx-gmail-api-gateway.yaml`,
  `blueprints/rtx-google-send-transport.yaml`, the parent `_rtx`/root blueprint
  sources, authored email-client instructions, shared gateway, and focused tests
  required by the Gmail prerequisite; non-Google IMAP/SMTP behavior remains
  unchanged;
- `skills/email-triage/_rtx/blueprints/rtx-mail-envelope-stream.yaml`, its
  parent `_rtx`/root blueprint pins, generated contracts/certificates, and
  focused mail-envelope-stream tests—the live zero-pin inventory identifies it
  as the repository reverse consumer of `mail-list@1`;
- versioned routing fixtures under
  `skills/connect-google/_rtx/tests/fixtures/llm-routing/` and the isolated,
  non-mutating runner under `test_support/connect_google_llm_qualification/`;
- the release-evidence runner/closed-schema validator under
  `test_support/connect_google_release/`, including POSIX executables and
  signed Windows `.cmd` launchers that pass arguments without shell rewriting;
- the root and `_rtx` blueprint sources/exports needed for these files; and
- generated `SKILL.md` contract blocks and certificates owned by those nodes.

`connection-plan` does not report current binding status; only service-owned
preflight may do so. No general account-list interface is added. If
implementation needs any broader service read or mutation capability, stop and
expand scope in a separately approved change rather than importing private
service state.

## 1. Correct the OAuth policy explanation

### Shortcoming

`SKILL.md` says Drive, Calendar, and Gmail all require restricted scopes and
that verification therefore requires an annual third-party security
assessment. It then combines the Testing test-user limit and seven-day token
expiry with the In-production unverified-app user cap, and says project
ownership removes the cap for the owner.

Those statements are not one valid Google policy state:

- the selected broad Drive scope and Gmail read scope are restricted, while
  the Calendar and Gmail send scopes follow sensitive-scope verification;
- an annual security assessment is conditional, notably on a restricted-scope
  app accessing data from or through third-party servers;
- Testing uses an allowlist of at most 100 test users and expires their
  authorizations after seven days when these service scopes are requested;
- an In-production unverified app does not use the manual test-user allowlist,
  but can remain subject to a lifetime 100-new-user cap; and
- project ownership does not generally waive the project's publishing-state
  rules.

Authoritative references:

- <https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification>
- <https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification>
- <https://support.google.com/cloud/answer/15549945>
- <https://support.google.com/cloud/answer/13464323>
- <https://support.google.com/cloud/answer/13464325>
- <https://support.google.com/cloud/answer/13463817>

### Suggested fix

Replace the mandatory two-sentence quotation and the project-owner claim with
state-specific guidance:

1. Explain that the requested scopes are sensitive or restricted and that an
   unverified consent screen may appear.
2. For **Testing**, state the manual test-user and seven-day authorization
   behavior.
3. For **In production but unverified**, state that the manual allowlist and
   seven-day Testing expiry no longer apply, while Google's unverified-app
   new-user cap can still apply.
4. Explain that verification requirements depend on scope, audience, data
   handling, and applicable exceptions; do not claim that Famulus necessarily
   requires an annual assessment.
5. Explain the maintainer-project trade-off narrowly: all recipients share one
   OAuth application identity and its project-level availability and quotas.
6. Keep external-policy assertions in one authored source and reference them
   from the other route so wording cannot drift.
7. Complete the in-scope Gmail API migration below. Do not request
   `https://mail.google.com/`: Famulus has no identified immediate
   permanent-delete operation that bypasses Gmail Trash, and technical
   dependence on IMAP or SMTP is not sufficient.

### How the fix covers the shortcoming

The replacement stops merging mutually different publishing states, removes
unsupported exceptions for project owners, and prevents a local desktop flow
from being described as if it necessarily handled data through a third-party
server. One authored policy source also removes the current duplicate-text
test, which preserves wording but not truth.

### Acceptance checks

- No instruction says Calendar uses a restricted scope.
- No instruction says an annual security assessment is categorically required.
- Testing and In-production behavior are explained separately.
- No instruction says project ownership removes a user cap or token expiry.
- Tests assert the state distinctions rather than exact prose duplication.
- The release ledger proves the Gmail API migration below and zero runtime
  requests for `https://mail.google.com/`; IMAP/SMTP dependence cannot pass.

### Blocking Gmail API migration prerequisite

Current inspection shows the Google email path reads through IMAP and sends
through SMTP; it does not expose a product requirement for immediate permanent
deletion bypassing Trash. Therefore `https://mail.google.com/` is not eligible
for this release. Public distribution remains red until the same candidate
implements and verifies this prerequisite:

1. Google-backed email accounts use Gmail API transports for the existing
   read, list/search, folder/label projection, attachment-download, and send
   interfaces. Non-Google accounts retain their current IMAP/SMTP transports.
2. Read operations request only
   `https://www.googleapis.com/auth/gmail.readonly`; send requests add only
   `https://www.googleapis.com/auth/gmail.send`. The candidate contains no
   runtime request for `https://mail.google.com/`, `gmail.modify`, or any other
   Gmail scope unless a later separately approved product operation requires
   it.
3. The Gmail binder persists the shared descriptor for these exact scopes and
   its live check calls Gmail API profile plus a non-mutating read probe. The
   send smoke remains an explicit mutating test; setup never sends mail.
4. The affected email-client mail-list/folders/read/attachments/
   save-attachments and send-email interfaces bump from version 1 to version 2;
   all repository consumers and authored routes migrate in the same candidate.
   Version 2 keeps success JSON/text shapes but defines `id`/`uid` as a
   provider-bound message identifier, not an IMAP UID. Version-1 callers are a
   declared breaking migration and receive release notes; zero remaining
   repository pins is a release gate.
5. A Google ID is exactly
   `g:<16 lowercase hex account fingerprint>:<unpadded base64url>`. The final
   component is unpadded base64url of the UTF-8 Gmail message ID and must
   decode/re-encode byte-for-byte to the same canonical spelling. The account
   fingerprint is the first 16 hex characters of SHA-256 over the normalized
   home, nickname, and bound credential subject. Read/attachment operations
   recompute it, reject cross-account IDs, reject padding/noncanonical/oversize
   encodings, and never accept a numeric IMAP UID for a Google account. An old
   ID returns exit 2 with the full common failure envelope/table entry below
   for `stale-provider-id`; success stdout remains unchanged.
6. Folder aliases map exactly to Gmail system labels: inbox=`INBOX`,
   sent=`SENT`, drafts=`DRAFT`, trash=`TRASH`, and all=no label filter. Literal
   labels resolve by exact UTF-8 name through `labels.list`; zero or multiple
   matches are typed errors. `mail-folders@2` always returns the five supported
   aliases `inbox`, `sent`, `drafts`, `trash`, `all` in that order (`all` is the
   defined no-label view, not a backing label), followed by user label names in
   Unicode code-point order, de-duplicated from system labels.
7. `mail-list@2` gives both providers one exact order: received timestamp UTC
   ascending, then provider-bound ID ascending; `--limit N` retains the newest
   N while preserving ascending output. `--after YYYY-MM-DD` means received
   timestamp at or after `00:00:00Z` on that date. Gmail walks all necessary
   page tokens, fetches metadata, applies that cutoff, builds the existing six
   envelope fields (`id`, `flags`, `subject`, `from`, `date`, `message_id`), and
   reuses the exact existing filter parser/matcher: arbitrary `key=value` or
   `key~=regex`, AND across keys, OR across repeated same-key conditions,
   comma alternatives for equality, case-insensitive regex with literal
   fallback for invalid regex. It filters before the final sort/limit and
   returns no provider page token. IMAP v2 adopts the same UTC/order semantics;
   parity fixtures cover every envelope key, operator, repetition, invalid
   regex, cutoff boundary, tie, empty page, and multi-page limit.
   Version 2 also closes `flags` to ordered values `seen`, `answered`,
   `flagged`, `draft`, `deleted`. IMAP maps `\\Seen`, `\\Answered`,
   `\\Flagged`, `\\Draft`, `\\Deleted`; Gmail maps absence of `UNREAD` to
   `seen`, `STARRED` to `flagged`, `DRAFT` to `draft`, and `TRASH` to `deleted`.
   Gmail emits no `answered` flag because Gmail API exposes no equivalent
   state. Output order is the vocabulary order, and the existing filter helper
   joins it with commas; fixtures cover every mapping and every `flags` equals/
   regex filter.
8. Read and attachment operations verify that the Gmail message currently has
   the requested alias/literal label (except `all`) before returning it, then
   call `messages.get(format=raw)` and feed the decoded RFC 2822 bytes through
   the existing MIME parser. Send serializes the existing envelope to RFC 2822
   bytes and calls `messages.send(raw=base64url)`; reply headers are preserved.
9. `_email_smoke.py` adds Google-only `--gmail-read-auth` and
   `--gmail-send-auth` checks. Gmail-read-auth calls profile plus a one-message
   non-mutating list. Gmail-send-auth refreshes an access token, calls Google's
   read-only token-info and userinfo endpoints without logging token-bearing
   URLs, and succeeds only when audience, expiry and scope set plus userinfo's
   opaque subject/account match the descriptor and include `gmail.send`; it performs no
   Gmail send call. Fixtures cover every mismatch, and live evidence records
   normalized claims only.
   `--send-self` remains the only delivery smoke. Legacy `--imap` and
   `--smtp-auth` reject Google accounts with `google-api-smoke-required` and
   continue unchanged for non-Google accounts. The two Gmail flags reject a
   non-Google account with `gmail-smoke-requires-google`. Mixing any Gmail flag
   with `--imap` or `--smtp-auth` is rejected before credential access as
   `mixed-provider-smoke-flags`; repeated boolean flags deduplicate. A valid
   combination runs, in order, Gmail-read-auth, Gmail-send-auth, then
   send-self, and returns result check names `gmail-read-auth`,
   `gmail-send-auth`, and `send-self` exactly once each. Setup uses only
   Gmail-read-auth; the live release ledger separately requires passing both
   non-mutating Gmail checks and an explicitly approved passing send-self.
10. Tests prove a Google account never opens IMAP/SMTP, a non-Google account
   never invokes Gmail API, the granted-scope set equals the two-scope catalog,
   and absent send scope yields a typed permission failure rather than another
   consent flow.

After account resolution identifies a Google-backed account, all affected
version-2 interfaces use one Google-path failure-only stderr envelope and emit
no success stdout on failure:

```json
{
  "schema_version": 1,
  "ok": false,
  "operation": "folders|list|read|attachments|save-attachments|send|gmail-read-auth|gmail-send-auth|send-self|smoke-selection",
  "code": "closed code below",
  "remediation_id": "closed remediation below",
  "retry_class": "none|after-user-action"
}
```

Every object rejects extra keys and raw provider text. Exit 2 means invalid or
stale user input, exit 3 credential/scope/authentication repair, exit 4 a
provider response requiring a user-decided later retry, and exit 5 a local
output failure. The exhaustive code table is:

This envelope is intentionally not claimed to normalize non-Google providers,
except for the two new provider-selection errors for Gmail-only smoke flags
listed below.
For a configured non-Google account, version 2 routes to the existing IMAP/
SMTP implementations and preserves their version-1 exit codes and stderr bytes
for every success/failure fixture; no Google remediation can appear. An unknown
nickname also follows the existing account-resolution error before a provider
contract is selected. Golden parity tests cover malformed filters/dates,
credential lookup, IMAP/SMTP authentication, DNS/connect/TLS/timeouts, protocol
status failures, folder/message absence, attachment output, invalid send
requests, and SMTP rejection. The only intentional non-Google v2 success
changes are the documented provider-neutral flag/order/date/ID semantics; they
do not rewrite legacy errors. Google-path classification otherwise begins only
after a known account has `auth.type: google-shared-credential`.
Legacy golden stderr/exit fixtures are captured from audited baseline commit
`a7cbdf86b966250c70bf74d2c745802751b01ae7`, separately on Linux, macOS, and
Windows. Comparison is raw bytes with no path, newline, locale, or platform
normalization; fixture setup fixes locale and replaces variable provider data
with deterministic adapters before baseline capture.

| Code | Operations | Exit | Remediation / retry class |
| --- | --- | --- | --- |
| `invalid-after-date` | list | 2 | `use-iso-date` / none |
| `invalid-limit` | list | 2 | `use-positive-limit` / none |
| `invalid-filter` | list | 2 | `repair-filter` / none |
| `invalid-folder` | list/read/attachments/save-attachments | 2 | `select-folder` / none |
| `label-not-found` | list/read/attachments/save-attachments | 2 | `select-folder` / none |
| `label-ambiguous` | list/read/attachments/save-attachments | 2 | `select-unique-folder` / none |
| `stale-provider-id` | read/attachments/save-attachments | 2 | `list-again` / none |
| `malformed-provider-id` | read/attachments/save-attachments | 2 | `use-listed-id` / none |
| `noncanonical-provider-id` | read/attachments/save-attachments | 2 | `use-listed-id` / none |
| `cross-account-provider-id` | read/attachments/save-attachments | 2 | `list-correct-account` / none |
| `provider-id-too-long` | read/attachments/save-attachments | 2 | `use-listed-id` / none |
| `message-not-found` | read/attachments/save-attachments | 2 | `list-again` / none |
| `message-not-in-folder` | read/attachments/save-attachments | 2 | `list-folder` / none |
| `invalid-send-request` | send/send-self | 2 | `repair-message` / none |
| `gmail-smoke-requires-google` | gmail-read-auth/gmail-send-auth | 2 | `use-imap-smtp-smoke` / none |
| `mixed-provider-smoke-flags` | smoke-selection | 2 | `choose-one-provider-smoke-family` / none |
| `send-scope-missing` | send/gmail-send-auth/send-self | 3 | `reconnect-gmail-send` / after-user-action |
| `token-audience-mismatch` | gmail-send-auth | 3 | `reconnect-google` / after-user-action |
| `token-expired` | gmail-send-auth | 3 | `reconnect-google` / after-user-action |
| `token-scope-mismatch` | gmail-send-auth | 3 | `reconnect-google` / after-user-action |
| `token-subject-mismatch` | gmail-read-auth/gmail-send-auth | 3 | `reconnect-google` / after-user-action |
| `token-account-mismatch` | gmail-read-auth/gmail-send-auth | 3 | `reconnect-google` / after-user-action |
| `credential-unavailable` | every operation | 3 | `restore-credential` / after-user-action |
| `provider-auth-failed` | every operation | 3 | `reconnect-google` / after-user-action |
| `gmail-api-disabled` | every Gmail API operation | 3 | `enable-gmail-api` / after-user-action |
| `provider-rate-limited` | every network operation | 4 | `wait-before-retry` / after-user-action |
| `provider-unavailable` | every network operation | 4 | `retry-later` / after-user-action |
| `provider-api-failed` | every network operation | 4 | `inspect-provider-failure` / after-user-action |
| `attachment-output-failed` | save-attachments | 5 | `choose-output-directory` / after-user-action |

“Every network operation” means all listed operations except purely local ID/
argument validation. The complete provider-bound ID is at most 1024 ASCII
characters; its decoded provider-ID component is 1..512 UTF-8 bytes and its
account fingerprint is exactly 16 lowercase hex characters. Validation order
is length, legacy all-decimal IMAP-UID detection (`stale-provider-id`), grammar,
canonical decode/re-encode, account fingerprint, folder
resolution, then provider lookup, making overlapping failures deterministic.
Parameterized tests cover every code for every applicable operation, exact
exit/envelope/remediation, forbidden combinations, and redaction.

Failure classification has this strict precedence, and the remote table is
evaluated top to bottom: validate all local argv,
provider IDs, send envelope, and output confinement first; load the bound
credential/scopes second; refresh/authenticate third; resolve remote labels
fourth; invoke the requested Gmail/token-info/userinfo endpoint last. An earlier
failure wins and later stages are not called. Remote outcomes map exactly:

| Remote outcome | Common code |
| --- | --- |
| local secret-store/descriptor load failure | `credential-unavailable` |
| OAuth refresh `invalid_grant`, `invalid_client`, HTTP 401, or a 401 Gmail/token-info/userinfo response | `provider-auth-failed` |
| HTTP 429, or Google reason `rateLimitExceeded`, `userRateLimitExceeded`, or `quotaExceeded` on any status | `provider-rate-limited` |
| Google reason `accessNotConfigured` or `serviceDisabled` | `gmail-api-disabled` |
| HTTP 403 reason `insufficientPermissions` for send/gmail-send-auth/send-self, after local descriptor scope check | `provider-auth-failed` |
| OAuth `temporarily_unavailable`; DNS, connect, TLS, or read timeout; HTTP 408/500/502/503/504 | `provider-unavailable` |
| HTTP 404 from `messages.get` for a validated ID | `message-not-found` |
| successful label list with zero/multiple exact-name matches | `label-not-found` / `label-ambiguous` |
| well-formed gmail-send-auth claims, checked in order, whose audience differs, expiry is not in the future, or exact scope set differs | `token-audience-mismatch`, then `token-expired`, then `token-scope-mismatch` |
| well-formed gmail-read-auth/gmail-send-auth userinfo whose opaque subject or normalized account differs | `token-subject-mismatch`, then `token-account-mismatch` |
| connection reset, premature EOF, broken pipe, write timeout, or other transport exception not already classified | `provider-unavailable` |
| redirect (automatic redirects disabled), unexpected HTTP status, 2xx response missing required typed fields, invalid JSON, other OAuth error, other 4xx/5xx, unknown Google reason, or any uncategorized client-library exception at the network boundary | `provider-api-failed` |

The top-to-bottom row order resolves conflicting status/reason pairs; local
validation and credential checks always precede the request. Response bodies are parsed only
for closed `error.errors[].reason` strings and never copied to the envelope.
Parameterized tests cross every listed status/reason/exception with every
applicable operation, including conflicting status/reason pairs, and assert
the precedence, exit code, remediation, call suppression, and redaction. A
residual-exception test injects one otherwise-unrecognized subclass at each
network boundary and proves the terminal catch-all; no exception escapes to
raw stderr.

This prerequisite is part of the release-candidate scope and gates the Gmail
catalog/live cases. It is not deferred to an unnamed future design. If it is
not implemented, the whole connect-google distribution stays red; the plan
does not silently ship only part of the advertised service set.

## 2. Select External versus Internal audiences correctly

### Shortcoming

`instructions/create-client.md` always tells the user to configure an External
audience. Organization-owned Google Workspace or Cloud Identity projects used
only by organization members can instead use Internal. The current instruction
can send eligible users through an unnecessary external test-user and
verification workflow.

### Suggested fix

Ask one audience question before giving the Cloud Console route:

- If the project is owned by a Workspace or Cloud Identity organization and
  all intended users belong to that same organization, offer **Internal** and
  warn that administrator approval may still be required.
- Otherwise use **External**, then explain Testing versus In-production.

The question and allowed answers should come from a script-owned setup-plan
result. The LLM should present the result and record the user's choice; it
should not infer organization eligibility from an email domain.

### How the fix covers the shortcoming

The workflow now represents both supported audience models and makes the
eligibility boundary explicit. It avoids imposing External-specific friction
while preventing an LLM from silently assuming that a domain is an eligible
organization.

### Acceptance checks

- Instruction tests cover Internal-eligible, External-required, and unknown
  eligibility routes.
- Unknown eligibility always produces a user question.
- No route infers Internal eligibility from the account address alone.

## 3. Return actionable client-status reasons

### Shortcoming

`ClientSecretStoreUnavailable` is a `ClientConfigError`, and `client_status()`
catches it as a generic invalid client. The router then asks the user for a new
client JSON even when the actual problem is an unavailable host keyring or
secret-store backend. Other malformed, unsafe, and missing-secret states are
also collapsed into `status: invalid`.

### Suggested fix

Make `client-status` return a versioned result with a stable reason and next
action. At minimum distinguish:

- `valid`;
- `missing`;
- `migration-required`;
- `secret-store-unavailable`;
- `client-secret-missing`;
- `malformed-client`;
- `unsafe-client`; and
- `unsupported-client-type`.

Include a machine `next_action` such as `reuse`, `select-client`,
`confirm-migration`, `restore-secret-store`, or `replace-client`. Do not emit a
shell command containing a placeholder path as remediation.

### How the fix covers the shortcoming

The router no longer guesses why validation failed. A keyring outage remains a
host-recovery problem, while an absent or unsafe client remains a client-file
problem. Users are not prompted to replace credentials unless the machine
result actually calls for replacement.

### Acceptance checks

- Each failure class has a focused result-schema test.
- A secret-store failure never routes to client replacement.
- Results contain no client secret, token, or authorization URL.
- Legacy candidate paths remain user-private and candidate import still
  requires confirmation.

## 4. Provide a deterministic informed-consent plan

### Shortcoming

The instructions recommend Drive, Calendar, and Gmail but do not give users a
plain-language summary of the broad access each selected scope requests. Scope
URIs are hard-coded in prose, so the LLM must connect service names, scopes,
API enablement, Gmail nickname requirements, and access implications.

### Suggested fix

Add a read-only `connection-plan` machine interface backed by the same
canonical service metadata used to build the OAuth scope union. For each
service it should return:

- stable service ID and display name;
- exact requested scope array;
- plain-language access summary;
- whether a Google API must be enabled;
- required user inputs, such as a Gmail nickname;
- warnings relevant to the selected audience/publishing state.

The router presents this plan before authorization and asks the user to select
a subset. It must not describe unselected services as being authorized.

Authoritative scope references:

- <https://developers.google.com/workspace/drive/api/guides/api-specific-auth>
- <https://developers.google.com/workspace/calendar/api/auth>
- <https://developers.google.com/workspace/gmail/api/auth/scopes>

### How the fix covers the shortcoming

Scope selection and explanations come from one deterministic source shared
with the authorization code. Users see the actual requested access before the
browser consent step, and the LLM no longer invents or omits permission
semantics.

### Acceptance checks

- Plan scopes exactly equal the scopes requested by the coordinator.
- Every supported service has the exact normative, source-mapped access
  description and input schema specified above.
- The user must explicitly select at least one service.
- Apply cannot receive an attempt ID until the target-bound scope-review action
  is approved; changing a service, scope, catalog version, summary, or warning
  invalidates that approval.
- The authorization call receives exactly the selected service IDs.

## 5. Add one deterministic setup/resume coordinator

### Shortcoming

The machine layer already validates and installs a client, performs OAuth,
creates one credential descriptor, binds services, and supports binding-only
retry. The LLM still owns the mechanical sequence between `client-status`,
`install-client`, `connect-services`, result classification, and
`bind-credential-file`. This duplicates control flow in prose and permits
skipped checks, a second unnecessary consent flow, or the wrong recovery call.

### Suggested fix

Expose the deterministic setup coordinator and journal specified above with
three execution operations plus the separate bounded cleanup operation:

1. `prepare` creates or updates the non-secret journal, validates each supplied
   declaration/choice/approval, and returns the exact connection plan plus a
   typed `needs_user_action` result. It performs no client installation,
   authorization, or service binding.
2. `apply` accepts only the ready run and current attempt IDs. It revalidates
   the recorded choices, then executes the valid transition: install/reuse,
   authorize once for that attempt epoch, bind granted services, and return
   either complete success or a typed resumable state.
3. `resume` consumes the credential file and incomplete-service state from
   `apply`. It may retry only binding work already proven safe to retry. It
   must never reopen OAuth solely to retry a binder and must not blindly retry
   an uncertain service mutation.

Keep these decisions outside the coordinator:

- which services the user wants;
- which differing legacy client to import;
- whether to replace a different canonical client;
- whether a service may change an existing account binding;
- the Gmail nickname;
- the Google account selected in the browser; and
- Google Cloud project mutations.

Every replacement approval is bound to the machine-identified current and
proposed client identities. Account-change approval is bound to the service
ID, normalized target, binder-returned current-state identity, and proposed
descriptor subject/account. If preflight cannot establish a current identity,
the coordinator stops; an `unknown` binding is not approvable. Generic advance
approval is invalid, and an approval captured before the browser-selected
proposed account or binder precondition is known cannot authorize an account
change. The compare-and-set binder atomically rechecks both identities before
mutation.

### How the fix covers the shortcoming

The coordinator owns every transition derivable from machine state while
stopping at explicit user-choice and approval boundaries. The same state model
drives normal setup, reconnect, partial grants, and binding recovery, so the LLM
cannot accidentally restart or reorder the workflow.

### Acceptance checks

- Transition-table tests cover every status/reason and incomplete-service
  state.
- Apply rejects missing, expired, tampered, replayed, or stale preparation
  data according to the setup-run rules above.
- Resume accepts only the original validated credential descriptor.
- Recovery tests cover pre-intent interruption, recorded success, recorded
  failure, uncommitted external intent, concurrent calls, and terminal replay.
- Partial grants bind only granted services and remain incomplete.
- Account-change and replacement states cannot proceed without explicit
  approval values.
- Every non-approval action scalar has exactly one accepted prepare-option
  encoding; omission remains unresolved and every alternate spelling is
  rejected.
- No result can cause a blind retry of an uncertain mutation.

## 6. Preserve structured failure semantics

### Shortcoming

`ConnectServicesInterface` catches every exception and returns the single code
`authorization-failed`. More precise phase and code values are emitted on the
diagnostic stream, leaving the caller or LLM to correlate two channels and
infer whether the user should retry, repair the keyring, change the callback
port, select another account, or stop.

### Suggested fix

Return the stable authorization failure in the primary JSON result:

- `phase`;
- `code`;
- `retry_class`: `none`, `binding-only`, or
  `needs-user-action`;
- `needs_user_action`, when applicable; and
- a secret-free remediation identifier.

Keep the diagnostic JSONL stream for progress, the manual authorization URL,
browser status, and the exact SSH-forward command. Do not require it to
interpret the terminal result.

No retry class authorizes an automatic retry. `binding-only` means only
`setup-resume` is permitted. `needs-user-action` stops until the named action
is resolved through iterative prepare; resume accepts no fresh approval. `none`
is terminal for that run.

The coordinator owns this total mapping:

Every value in the last column has a total state/attempt rule. `prepare
(pre-consent)` closes the consumed attempt, enters `awaiting-user`, and issues
a new attempt only after revalidation. `prepare (new-consent)` closes a known
timeout epoch, enters `awaiting-new-authorization`, and issues a new attempt
only after the target-bound duplicate-consent warning is approved. `prepare
(binder-choice)` preserves the
original authorization attempt and descriptor, enters `awaiting-user` or
`awaiting-approval`, and returns to `resumable`; it is followed only by
`resume`. `new-prepare` makes the current run terminal and creates neither a
run nor an attempt until the caller explicitly invokes prepare without the old
run ID. `resume` preserves the attempt/descriptor. `none` enters `failed` for a
known non-mutating failure or `blocked-uncertain` for an outcome explicitly
marked uncertain. Parameterized tests assert this rule for every row in
addition to its phase/code.

| Source outcome | Retry class | Remediation/action | Permitted next operation |
| --- | --- | --- | --- |
| `client/client_invalid` | `needs-user-action` | `repair-or-select-client` | `prepare (pre-consent)` |
| `client/secret_store_unavailable` | `needs-user-action` | `restore-secret-store` | `prepare (pre-consent)` |
| `listener/listener_bind_failed` | `needs-user-action` | `choose-callback-port-or-repair-host` | `prepare (pre-consent)` |
| `callback/callback_timeout` | `needs-user-action` | `confirm-new-authorization-after-uncertain-consent` | `prepare (new-consent)` |
| `callback/access_denied` | `needs-user-action` | `review-selected-services` | `new-prepare` |
| `token_exchange/secret_store_unavailable` | `needs-user-action` | `restore-secret-store` | `new-prepare`; never reuse the authorization code |
| `token_exchange/token_exchange_failed` | `needs-user-action` | `repeat-browser-consent` | `new-prepare` |
| `userinfo/userinfo_failed` | `needs-user-action` | `repeat-browser-consent` | `new-prepare` |
| `account_check/account_mismatch` | `needs-user-action` | `supply-account-hint-in-new-run` | `new-prepare` |
| `credential_publish/no_service_scope_granted` | `needs-user-action` | `review-selected-services` | `new-prepare` |
| `credential_publish/secret_store_unavailable` | `needs-user-action` | `restore-secret-store` | `new-prepare`; token exchange is not replayed |
| `credential_publish/credential_publish_failed` | `none` | `inspect-credential-publication` | none; a new run requires explicit diagnosis |
| any phase/`internal_error` or unknown authorization pair | `none` | `internal-failure` | none |
| journal/`secret-store-unavailable` | `needs-user-action` | `restore-secret-store` | response-only repair overlay; retry only the original operation after recovery |
| journal/`hmac-key-create-failed` | `needs-user-action` | `restore-secret-store` | response-only repair overlay, then retry initial prepare; run ID is null |
| journal/`head-read-failed` | `needs-user-action` | `restore-secret-store` | response-only repair overlay; two-phase recovery runs before the original operation |
| journal/`head-write-failed` | `needs-user-action` | `restore-secret-store` | response-only repair overlay; two-phase recovery proves committed or pending before retry |
| journal/`integrity-failed` | `none` | `journal-integrity-failed` | `blocked-uncertain`; never reinterpret missing key/head or invalid MAC as a fresh run |
| cleanup/`head-delete-failed` | `needs-user-action` | `restore-secret-store` | remain `cleanup-pending`, `next_action: cleanup` after host repair |
| cleanup/`journal-delete-failed` | `needs-user-action` | `repair-state-root` | remain `cleanup-pending`, `next_action: cleanup` after host repair |
| cleanup/`head-read-failed` | `needs-user-action` | `restore-secret-store` | response-only repair overlay; retry cleanup and recover committed/pending head before selection |
| cleanup/`head-write-failed` | `needs-user-action` | `restore-secret-store` | response-only repair overlay; retry cleanup and recover cursor/head before advancing |
| cleanup/`cursor-integrity-failed` | `none` | `cleanup-cursor-integrity-failed` | `blocked-uncertain`; do not reset traversal and mask rollback |
| denied requested service after a partial grant | `needs-user-action` | `review-partial-grant` | `new-prepare` for any new grant; already granted bindings remain recorded |
| preflight/`invalid-descriptor` | `none` | `credential-descriptor-invalid` | none; state `failed`, no mutation |
| preflight/`invalid-target` | `none` | `service-target-invalid` | none; separate service configuration diagnosis required |
| preflight/`current-state-unavailable` | `none` | `binding-state-unavailable` | none; never offer account-change approval |
| preflight/`invalid-service-config` | `needs-user-action` | `repair-service-config` | `prepare (binder-choice)`, then `resume` |
| CAS/`approval-required` | `needs-user-action` | `confirm-account-change` with fresh preflight target | `prepare (binder-choice)`, then `resume` |
| CAS/`invalid-descriptor` | `none` | `credential-descriptor-invalid` | none; state `failed`, no mutation |
| CAS/`invalid-target` | `none` | `service-target-invalid` | none; separate service configuration diagnosis required |
| CAS/`current-state-unavailable` | `none` | `binding-state-unavailable` | none; never accept an approval receipt |
| CAS/`invalid-approval-receipt` | `none` | `approval-receipt-invalid` | none; state `failed`, security diagnosis required |
| CAS/`stale-precondition` | `needs-user-action` | `review-changed-binding-target` | rerun preflight, invalidate old receipt, `prepare (binder-choice)`, then `resume` |
| CAS/`proposed-subject-mismatch` | `none` | `proposed-subject-mismatch` | none; state `failed`, no mutation |
| CAS/`proposed-account-mismatch` | `none` | `proposed-account-mismatch` | none; state `failed`, no mutation |
| CAS/`live-check-failed` | `binding-only` | `retry-live-verification` | caller-initiated `resume`; no persistence occurred |
| CAS/`live-account-mismatch` | `none` | `live-identity-mismatch` | none; state `failed`, no persistence occurred |
| CAS/`cas-lock-failed` | `binding-only` | `retry-binding-lock` | caller-initiated `resume`; lock acquisition precedes mutation |
| CAS/`persistence-outcome-uncertain` | `none` | `binding-outcome-uncertain` | reconcile pre/post state; remain `blocked-uncertain` if neither exact identity is provable |
| CAS/`postcondition-mismatch` | `none` | `binding-postcondition-uncertain` | reconcile pre/post state; remain `blocked-uncertain` unless exact post-state is proven |
| CAS/`invalid-service-config` | `needs-user-action` | `repair-service-config` | `prepare (binder-choice)`, then `resume` |
| binder/`missing-gmail-nickname` | `needs-user-action` | `supply-gmail-nickname` | `prepare (binder-choice)`, then `resume` |
| binder/`invalid-credential-file` | `none` | `credential-descriptor-invalid` | none; descriptor identity/invariant diagnosis is required |
| binder/`insufficient-scope` | `needs-user-action` | `reauthorize-required-scope` | `new-prepare`; current descriptor is not rebound |
| binder/`unknown-account` | `needs-user-action` | `supply-gmail-nickname` | `prepare (binder-choice)`, then `resume` |
| binder/`invalid-account-email` | `needs-user-action` | `repair-gmail-account-config` | `resume` after separate email configuration repair |
| binder/`account-email-mismatch` | `needs-user-action` | `supply-gmail-nickname` | `prepare (binder-choice)`, then `resume`, or `new-prepare` for another Google account |
| binder/`account-change-confirmation-required` | `needs-user-action` | `confirm-account-change` bound to the preflight current-state identity and proposed subject | `prepare (binder-choice)`, then `resume` |
| binder/`live-account-mismatch` | `none` | `live-identity-mismatch` | none; stop without persisting the proposed binding |
| binder/`live-check-failed` | `binding-only` | `retry-live-verification` | caller-initiated `resume`; binder contracts prove persistence occurs only after the check |
| binder/`invalid-service-config` | `needs-user-action` | `repair-service-config` | `resume` after separate service diagnosis/repair; binder contracts prove failure precedes mutation |
| binder/`binding-dispatch-failed` | `none` | `binding-outcome-uncertain` | none; state is `blocked-uncertain` |
| binder/`invalid-binding-result` | `none` | `binding-outcome-uncertain` | none; state is `blocked-uncertain` |
| unknown binder error | `none` | `unknown-binding-failure` | none |

The table is represented once as immutable coordinator data. Parameterized
tests consume the same rows but must also assert independently expected
phase/code coverage, so deleting a production row cannot silently delete the
test obligation. A completeness test enumerates every live
`AuthorizationFailure` constructor and every normalized binder outcome and
fails if any lacks a row; unknown values always select the terminal default.

### How the fix covers the shortcoming

The primary result becomes sufficient for deterministic routing. The LLM can
explain a known outcome to the user without parsing exception text or joining
stderr events to stdout.

### Acceptance checks

- Every `AuthorizationFailure` phase/code survives the coordinator boundary.
- Every live authorization and binder outcome is covered by the total mapping,
  and unknown values select `retry_class: none`.
- Unknown internal failures remain redacted and non-retryable by default.
- Tests prove that raw exception text cannot introduce tokens or client
  secrets into the result.
- The terminal result identifies binding-only recovery without requesting a
  second consent flow.

## 7. Replace wording-presence tests with semantic route tests

### Shortcoming

Current LLM-routing tests require phrases such as `restricted scopes` and
`annual third-party security assessment` to remain present. These tests make
incorrect policy prose durable. Most route assertions check word presence
rather than the state, user question, machine call, and result interpretation
that define correct behavior.

### Suggested fix

Replace policy phrase assertions with scenario-based contract tests covering:

- Testing External, In-production External, Internal-eligible, and unknown
  audience states;
- every client-status reason;
- client selection, import, replacement, and keyring recovery;
- service subset selection and informed-consent plan presentation;
- Gmail nickname collection;
- local-browser and headless callback routes;
- complete, partial-grant, denied, account-mismatch, secret-store,
  listener/callback, and per-service binding failures; and
- binding-only resume with the original credential file.

Split verification at the deterministic boundary:

1. **Automated machine tests** exercise status detail, the service catalog,
   setup-run persistence, the total transition table, approval binding,
   authorization, binder aggregation, compatibility projections, and terminal
   result schemas. Structured JSON fixtures are the oracle.
2. **Automated instruction structure tests** assert that each machine
   `needs_user_action.kind` has a matching instruction route, that instructions
   prohibit inference and automatic retry, and that no obsolete policy claim
   remains. They do not claim to test LLM behavior.
3. **Manual isolated-LLM qualification** is required because the broader
   `docs/plans/isolated-llm-testing.md` harness is still proposed. This work
   adds a narrow non-mutating fixture harness under
   `test_support/connect_google_llm_qualification/`. It creates a disposable
   home, denies network, mounts no user state, exposes only a fixture dispatcher
   that records allowed interface IDs/arguments and returns versioned synthetic
   JSON, and rejects every real dispatcher path or unlisted subprocess. No
   fixture interface can write outside the disposable home or perform OAuth.
   Run every fixture from
   `skills/connect-google/_rtx/tests/fixtures/llm-routing/` in a fresh context
   against the packaged `SKILL.md`; retain the complete response, recorder
   calls, machine fixture outputs, and human-adjudicated pass/fail ledger. A
   case passes only if the model asks every required user question, proposes
   only the permitted next operation, reports incomplete states, and does not
   invent Google policy or secrets. This is manual release qualification, not
   an automated test claim.

The minimum qualification matrix is:

| Host | Model | Configuration |
| --- | --- | --- |
| Codex CLI | `gpt-5.6-sol` | reasoning `medium`; no memories, MCP servers, apps, network, or tools except the fixture recorder |
| Claude Code | `claude-opus-5` | effort `high`; no memories, MCP servers, network, or tools except the fixture recorder |

The release ledger records the exact Codex CLI and Claude Code binary versions,
model IDs, effective configuration hashes, candidate SHA/archive hash, fixture
version, and run IDs. Model aliases and `latest` host packages are rejected.
If either pinned model or an exact host version is unavailable, qualification
is red until this plan is explicitly revised; it is not skipped or silently
substituted.

The runner exposes one executable entry point, `run-qualification`, and owns
host-specific invocation syntax so release operators do not improvise it. From
the exact candidate checkout run:

```text
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_llm_qualification/run-qualification --host codex --model gpt-5.6-sol --reasoning medium --candidate /ABSOLUTE/RELEASE/CANDIDATE --fixtures /ABSOLUTE/RELEASE/CANDIDATE/skills/connect-google/_rtx/tests/fixtures/llm-routing --output /ABSOLUTE/RELEASE/EVIDENCE/llm-codex
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_llm_qualification/run-qualification --host claude-code --model claude-opus-5 --effort high --candidate /ABSOLUTE/RELEASE/CANDIDATE --fixtures /ABSOLUTE/RELEASE/CANDIDATE/skills/connect-google/_rtx/tests/fixtures/llm-routing --output /ABSOLUTE/RELEASE/EVIDENCE/llm-claude
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_llm_qualification/validate-ledger --evidence /ABSOLUTE/RELEASE/EVIDENCE/llm-codex --evidence /ABSOLUTE/RELEASE/EVIDENCE/llm-claude --candidate /ABSOLUTE/RELEASE/CANDIDATE
```

The runner fails closed unless its disposable-home, network-denial, empty
connector/plugin inventory, exact binary/model/config capture, fixture-only
dispatcher, subprocess allowlist, and complete-case assertions all pass. The
validator requires an adjudication for every fixture and rejects leaked
secrets, authorization URLs, missing recorder calls, extra machine calls, and
unrecorded host substitutions.

Retain the existing secret-redaction, PKCE, callback-validation, immutable
credential-file, and service-delegation tests. Keep the native browser and SSH
smokes opt-in, but require a recorded native smoke on each supported release
platform before distribution.

### How the fix covers the shortcoming

The tests enforce observable workflow semantics instead of freezing prose.
Policy language can then be corrected without weakening the router contract,
and future regressions are caught at user-decision and machine-transition
boundaries.

### Acceptance checks

- No test requires a policy phrase merely to occur.
- Each automated machine scenario asserts the exact transition and required
  user action; isolated-LLM qualification separately checks presentation and
  invocation behavior.
- Focused tests pass both in the ordinary suite and outside socket-restricted
  sandboxes where required.
- Native browser and headless/SSH evidence is recorded for the release.

## Release verification and environment correction

### Shortcoming

During the audit, dispatcher dry-runs from the repository root resolved
`connect-google` and `standards.interface.query-standard` implementations from
`.worktrees/rutter-node-entry-core`, a different checkout and commit. Source
tests passed in the requested checkout, but that does not prove the public
dispatcher will execute the packaged source.

### Suggested fix

Before release, activate or install the exact candidate checkout and retain
dry-runs for:

- `client-status`;
- `install-client`;
- `connection-plan` and setup `prepare`/`apply`/`resume`;
- `connect-services` and `bind-credential-file` while compatibility exports
  remain; and
- all applicable node-standard queries.

Each dry-run must identify the candidate repository root, expected caller and
target, and expected implementing source. Reject another checkout rather than
using its passing results.

The repository-wide release process is not implemented and `docs/releasing.md`
does not exist. Therefore this plan explicitly blocks public distribution until
that release process is implemented. The release candidate is the exact commit
later tagged `vMAJOR.MINOR.PATCH`; the public artifact is GitHub's generated
source archive for that immutable tag, not a locally assembled archive.

### Exact local and installed-candidate gate

Run from the release-candidate checkout, substituting only its absolute path:

```text
/ABSOLUTE/RELEASE/CANDIDATE/repo_checks.py --suite tests --task tests:shared --selector skills/connect-google/_rtx/tests/test_authorize_services.py --selector skills/connect-google/_rtx/tests/test_client_config.py --selector skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py --selector skills/connect-google/_rtx/tests/test_connect_services.py --selector skills/connect-google/_rtx/tests/test_credential_file_end_to_end.py --selector skills/connect-google/_rtx/tests/test_google_oauth_native_smoke.py --selector skills/connect-google/_rtx/tests/test_service_delegation.py --selector skills/connect-google/_rtx/tests/test_service_catalog.py --selector skills/connect-google/_rtx/tests/test_setup_coordinator.py --selector skills/connect-google/_rtx/tests/test_binding_cas_contract.py --selector skills/email-client/_rtx/tests/test_gmail_api_transport.py --selector skills/email-client/_rtx/tests/test_google_transport_routing.py --selector skills/email-client/_rtx/tests/test_gmail_provider_compatibility.py --selector skills/email-client/_rtx/tests/test_email_smoke.py --selector tests/test_officina_google_credentials.py --selector tests/test_officina_google_credential_files.py --selector tests/test_officina_famulus_paths.py --selector tests/test_officina_assistant_access.py --selector tests/test_assistant_access_blueprint_contracts.py --selector tests/test_install_context_consumers.py --jobs 1 --repository-view working
/ABSOLUTE/RELEASE/CANDIDATE/repo_checks.py --suite tests --task tests:shared --selector skills/email-triage/_rtx/tests/test_fetch_filtered_envelopes.py --jobs 1 --repository-view working
/ABSOLUTE/RELEASE/CANDIDATE/repo_checks.py --suite validators --jobs 1 --repository-view working
/ABSOLUTE/RELEASE/CANDIDATE/repo_checks.py --suite full --verbose --jobs 1 --repository-view working
```

Socket-dependent focused tests must be rerun unchanged outside a restricted
sandbox if the only failure is loopback denial. Before the canonical staged
gate, stage only the release-owned files listed above and run:

```text
/ABSOLUTE/RELEASE/CANDIDATE/.githooks/pre-commit
```

This is the required secret scan and staged repository gate; no `--no-verify`
substitute is accepted.

Activate the candidate through the declared installer, first as a dry run and
then through its interactive development-context route:

```text
dispatcher --caller-skill install-assistant-tools --dry-run install-assistant-tools._rtx.interface.scripts-install --dev-mode --repo-path /ABSOLUTE/RELEASE/CANDIDATE
dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-install --dev-mode --repo-path /ABSOLUTE/RELEASE/CANDIDATE
dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-doctor --mode development --checkout /ABSOLUTE/RELEASE/CANDIDATE --json
```

Do not add `--yes`; the maintainer must review the resolved installation
context. After activation, retain `dispatcher --dry-run` JSON for every new and
compatibility interface listed above. Every `cwd`, implementing source, caller,
target, and terminal module must resolve to the candidate commit.

Run and retain these literal dry-runs; the named release runner creates the
fixture client/descriptor paths, captures stdout separately, and fails if any
resolved source is outside the candidate:

```text
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.client-status-detail --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.connection-plan --services drive,calendar,gmail --audience-declaration external --publishing-declaration testing --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.setup-prepare --services drive,calendar,gmail --client-file /ABSOLUTE/QUALIFICATION/CLIENT.json --audience-declaration external --publishing-declaration testing --gmail-nickname qualification --account-hint qualification@example.invalid --browser-mode manual-url --callback-port 8765 --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.setup-apply --run-id FIXTURE_RUN --attempt-id FIXTURE_ATTEMPT --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.setup-resume --run-id FIXTURE_RUN --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.setup-cleanup --home /ABSOLUTE/QUALIFICATION/HOME --max-records 32
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.client-status --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.install-client --from-json /ABSOLUTE/QUALIFICATION/CLIENT.json --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.connect-services --services drive,calendar,gmail --no-open-browser --callback-port 8765 --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run connect-google._rtx.interface.bind-credential-file --credential-file /ABSOLUTE/QUALIFICATION/DESCRIPTOR.json --services drive,calendar,gmail --gmail-nickname qualification --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run cloud-files._rtx.interface.preflight-google-credential-file --credential-file /ABSOLUTE/QUALIFICATION/DESCRIPTOR.json --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run cloud-files._rtx.interface.use-google-credential-file-cas --credential-file /ABSOLUTE/QUALIFICATION/DESCRIPTOR.json --expected-current-state-id FIXTURE_CURRENT --expected-proposed-subject fixture-subject-123 --expected-proposed-account qualification@example.invalid --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run online-calendar._rtx.interface.preflight-google-credential-file --credential-file /ABSOLUTE/QUALIFICATION/DESCRIPTOR.json --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run online-calendar._rtx.interface.use-google-credential-file-cas --credential-file /ABSOLUTE/QUALIFICATION/DESCRIPTOR.json --expected-current-state-id FIXTURE_CURRENT --expected-proposed-subject fixture-subject-123 --expected-proposed-account qualification@example.invalid --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run email-client._rtx.interface.accounts-preflight-google-credential-file --nickname qualification --credential-file /ABSOLUTE/QUALIFICATION/DESCRIPTOR.json --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill connect-google --dry-run email-client._rtx.interface.accounts-use-google-credential-file-cas --nickname qualification --credential-file /ABSOLUTE/QUALIFICATION/DESCRIPTOR.json --expected-current-state-id FIXTURE_CURRENT --expected-proposed-subject fixture-subject-123 --expected-proposed-account qualification@example.invalid --home /ABSOLUTE/QUALIFICATION/HOME
dispatcher --caller-skill email-client --dry-run email-client._rtx.interface.mail-folders -a qualification
dispatcher --caller-skill email-client --dry-run email-client._rtx.interface.mail-list -a qualification --folder inbox --after 2026-01-01 --limit 1
dispatcher --caller-skill email-client --dry-run email-client._rtx.interface.mail-read g:0123456789abcdef:Zml4dHVyZS1tc2c -a qualification --folder inbox
dispatcher --caller-skill email-client --dry-run email-client._rtx.interface.mail-attachments g:0123456789abcdef:Zml4dHVyZS1tc2c -a qualification --folder inbox
dispatcher --caller-skill email-client --dry-run email-client._rtx.interface.mail-save-attachments g:0123456789abcdef:Zml4dHVyZS1tc2c -a qualification --folder inbox --out /ABSOLUTE/QUALIFICATION/ATTACHMENTS --all
dispatcher --caller-skill email-client --dry-run email-client._rtx.interface.send-email --from qualification --to qualification@example.invalid --subject qualification
dispatcher --caller-skill email-client --dry-run email-client._rtx.interface.live-smoke -a qualification --gmail-read-auth
dispatcher --caller-skill email-client --dry-run email-client._rtx.interface.live-smoke -a qualification --gmail-send-auth
```

The dry-run manifest must show interface version 2 for the six mail/send
surfaces and the updated smoke source. The release runner then executes the
installed Gmail surfaces without delegating sequencing to an LLM:

```text
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_release/run-gmail-installed-surface --candidate /ABSOLUTE/RELEASE/CANDIDATE --nickname qualification --home /ABSOLUTE/QUALIFICATION/HOME --attachment-output /ABSOLUTE/QUALIFICATION/ATTACHMENTS --body-file /ABSOLUTE/QUALIFICATION/send-self-body.txt --approve-send-self --evidence /ABSOLUTE/RELEASE/EVIDENCE/gmail-installed.json
```

It invokes folders, list, read, attachments, and save-attachments; captures a
provider-bound ID from list internally; runs gmail-read-auth and gmail-send-auth; and only
then performs the separately approved send-self using the body file (never
stdin synthesized by the LLM). Its closed evidence record contains candidate/
interface identities, sanitized argv, result hashes, folder/order/filter/ID
predicates, attachment counts/paths confined to the disposable output,
normalized gmail-read-auth/token-info/userinfo claims, gmail-send-auth scope predicate, and
send-self recipient/message-ID—not body or message content. The validator
requires one passing record for every surface and rejects skips, extra sends,
unapproved recipients, raw mail, tokens, URLs, or paths outside the disposable
home.
Before live success calls, the runner also executes secret-free negative cases
for invalid date/limit/filter/folder, malformed/noncanonical/cross-account/
oversize IDs, stale numeric IDs, missing/ambiguous labels, wrong-folder
membership, invalid send request, mixed smoke families, and a Gmail smoke flag
against a configured non-Google fixture account; fixtures cover
the remaining credential/provider/output failures. The evidence validator
requires exact exit/envelope/remediation records and proves no negative case
made a send call.

Query exactly the four standards that govern the changed authored/Python
module/source nodes. For each command run the dry-run first, verify candidate
resolution, then run it without `--dry-run`; any unknown or unmet material
requirement is red:

```text
dispatcher --caller-skill refactor-node --dry-run standards.interface.query-standard references/node-standards/instruction-module.standard.yaml --repo-root /ABSOLUTE/RELEASE/CANDIDATE --facts-json {"task":{"kind":"refactor"}} --view requirements
dispatcher --caller-skill refactor-node --dry-run standards.interface.query-standard references/node-standards/instruction-behavioral-source.standard.yaml --repo-root /ABSOLUTE/RELEASE/CANDIDATE --facts-json {"task":{"kind":"refactor"}} --view requirements
dispatcher --caller-skill refactor-node --dry-run standards.interface.query-standard references/node-standards/python-module.standard.yaml --repo-root /ABSOLUTE/RELEASE/CANDIDATE --facts-json {"task":{"kind":"refactor"}} --view requirements
dispatcher --caller-skill refactor-node --dry-run standards.interface.query-standard references/node-standards/python-behavioral-source.standard.yaml --repo-root /ABSOLUTE/RELEASE/CANDIDATE --facts-json {"task":{"kind":"refactor"}} --view requirements
```

The shell must pass each `facts-json` value as one literal argument; the
release runner owns platform-specific quoting. The evidence manifest records
the compiled argv rather than relying on the display spelling above.

### Platform qualification matrix

The exact candidate SHA must pass the repository's current eight-element
GitHub Actions matrix described in `docs/ci-handbook.md`: Ubuntu combined;
macOS validators, shared, and performance; Windows validators, shared,
performance, and browser. Record repository, workflow, ref, 40-character SHA,
run ID, and every `.repo-checks/*.json` artifact.

In addition:

- Linux, macOS, and Windows each run the focused connect-google selectors,
  including real loopback socket behavior, with no unexpected skips.
- All three OSes run `native:keyring`; Linux does so in a graphical user
  session with SecretService available. Use the exact commands below. A skip,
  fallback backend, prompt-less locked
  collection, or nonzero result is red:

```text
FAMULUS_REQUIRE_NATIVE_KEYRING=1 /ABSOLUTE/RELEASE/CANDIDATE/repo_checks.py --suite full --task native:keyring --jobs 1 --timing-output /ABSOLUTE/RELEASE/CANDIDATE/.repo-checks/native-keyring-linux.json
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_release/run-platform-qualification --os macos --candidate /ABSOLUTE/RELEASE/CANDIDATE --evidence /ABSOLUTE/RELEASE/EVIDENCE/macos-keyring.json --native-keyring
& 'C:\ABSOLUTE\RELEASE\CANDIDATE\test_support\connect_google_release\run-platform-qualification.cmd' --os windows --candidate 'C:\ABSOLUTE\RELEASE\CANDIDATE' --evidence 'C:\ABSOLUTE\RELEASE\EVIDENCE\windows-keyring.json' --native-keyring
```
- One interactive desktop on each supported OS runs
  `FAMULUS_GOOGLE_BROWSER_SMOKE=1` for the native browser helper. These three
  recorded release smokes may not be replaced by the ordinary skip.
- One Linux host with a disposable SSH target runs
  `FAMULUS_GOOGLE_SSH_SMOKE_TARGET` and
  `FAMULUS_GOOGLE_SSH_SMOKE_PORT`. This qualifies the documented Unix
  headless/SSH route; no Windows SSH claim is made.
- One disposable Google project/account performs the live OAuth qualification
  procedure below.

On each OS, `run-platform-qualification` invokes the focused selector command
from the local gate verbatim, adds `--timing-output
/ABSOLUTE/RELEASE/EVIDENCE/platform-OS.json`, and fails on any skip or deselected
case. The three desktop browser smokes and Linux SSH smoke use these exact
entry points:

```text
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_release/run-platform-qualification --os linux --candidate /ABSOLUTE/RELEASE/CANDIDATE --evidence /ABSOLUTE/RELEASE/EVIDENCE/linux-browser.json --browser-smoke
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_release/run-platform-qualification --os macos --candidate /ABSOLUTE/RELEASE/CANDIDATE --evidence /ABSOLUTE/RELEASE/EVIDENCE/macos-browser.json --browser-smoke
& 'C:\ABSOLUTE\RELEASE\CANDIDATE\test_support\connect_google_release\run-platform-qualification.cmd' --os windows --candidate 'C:\ABSOLUTE\RELEASE\CANDIDATE' --evidence 'C:\ABSOLUTE\RELEASE\EVIDENCE\windows-browser.json' --browser-smoke
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_release/run-platform-qualification --os linux --candidate /ABSOLUTE/RELEASE/CANDIDATE --evidence /ABSOLUTE/RELEASE/EVIDENCE/linux-ssh.json --ssh-smoke --ssh-target qualification-host --ssh-port 8765
/ABSOLUTE/RELEASE/CANDIDATE/test_support/connect_google_release/validate-evidence --candidate /ABSOLUTE/RELEASE/CANDIDATE --evidence-root /ABSOLUTE/RELEASE/EVIDENCE
& 'C:\ABSOLUTE\RELEASE\CANDIDATE\test_support\connect_google_release\validate-evidence.cmd' --candidate 'C:\ABSOLUTE\RELEASE\CANDIDATE' --evidence-root 'C:\ABSOLUTE\RELEASE\EVIDENCE'
```

The first, second, fourth, and fifth commands are POSIX shell commands; the
third and sixth are literal PowerShell commands. The runner sets the three
`FAMULUS_GOOGLE_*` variables internally from validated flags, so no Windows
shell is asked to interpret POSIX environment-assignment syntax. Platform
tests execute every displayed argv through its named shell.

The runner requires a real desktop session for browser mode and a disposable,
preflighted SSH target for SSH mode. Its closed evidence schema includes OS,
host release, architecture, candidate SHA/install root, command argv and
environment-name allowlist (never values containing secrets), start/end time,
pytest node IDs, selected/passed/failed/skipped/deselected counts, return code,
and artifact SHA-256. Required smoke results are exactly one selected, one
passed, and zero failed/skipped/deselected. The validator also requires all
focused selectors on each OS, the native-keyring artifact, every dispatcher and
standards record, LLM ledgers, live-OAuth ledgers, and exact-SHA CI artifacts;
missing or unknown records fail closed.

### Disposable-project live OAuth qualification

Use a dedicated External/Testing Google Cloud project, dedicated test users,
and a Desktop client created only for this release qualification. Enable the
three selected APIs and place the client JSON outside the repository and
retained evidence directory. Each case runs in a newly provisioned disposable
VM snapshot with a unique OS user, native keyring collection, and Famulus home;
no case reuses a user home, keyring namespace, descriptor, or service config.
Destroy that VM after collecting the sanitized result ledger, and revoke its
test user's grant from a separate operator session. This makes reset executable
without relying on the credential-lifecycle cleanup explicitly outside this
change. Never copy a token, client secret, authorization URL, raw client JSON,
setup journal, VM image, or keyring into evidence.

Run these cases through the installed candidate coordinator:

| Case | Required oracle |
| --- | --- |
| Drive only | terminal `complete`; selected, granted, and bound sets are exactly `{drive}`; one descriptor identity is recorded |
| Calendar only | terminal `complete`; selected, granted, and bound sets are exactly `{calendar}` |
| Gmail only | terminal `complete`; selected, granted, and bound sets are exactly `{gmail}` and the chosen configured nickname is the bound target |
| All services | terminal `complete`; selected, granted, and bound sets are exactly `{drive, calendar, gmail}` |
| Partial denial | terminal `complete: false`; granted/bound services equal only services whose full scope arrays were browser-granted, missing scopes are exact per incomplete service, and no second OAuth starts |
| Account mismatch | stable `account_check/account_mismatch`; no descriptor is published and no service binding changes |

The operator invokes `setup-prepare`, records each required declaration or
approval only after reviewing its target, invokes `setup-apply` once with the
returned run/attempt IDs, and invokes `setup-resume` only where the case calls
for it. A fixture health check before each case proves the APIs are
enabled, the account is a project test user, native keyring access works, and
the fresh VM/user/home is active. The provisioning ledger records a unique
image instance ID and empty candidate state; the teardown ledger records grant
revocation and VM destruction.

Binding-only resume is qualified separately and deterministically without
remote-state improvisation: the coordinator test suite supplies a
versioned service-owned CAS-binder fixture whose first pre-persistence live
check returns `live-check-failed` and whose second returns the exact approved
postcondition. The test asserts one OAuth fixture call, one descriptor
path/SHA-256 and attempt ID across both operations, no persisted first binding,
and terminal completion after resume. Production code has no failpoint or
environment switch for this case.

For every case retain one secret-free JSON evidence record with: schema
version, case ID, candidate 40-character SHA, tagged-archive SHA-256 when
available, OS and candidate-install identity, sanitized input-choice IDs,
setup result SHA-256, expected predicate, actual normalized fields, pass/fail,
operator action timestamps, and reviewer identity. The ledger validator rejects
unknown fields and scans for URI query strings, OAuth codes, tokens, client
secrets, raw client JSON, and setup-journal contents. Any missing case,
unexplained human intervention, failed reset, or oracle mismatch is red.

### Tagged artifact identity

After the repository-wide release process exists, its `docs/releasing.md` commands
must build the release commit, annotated tag, and GitHub Release. Download
GitHub's generated source archive for that tag into a fresh temporary
directory, record its SHA-256, and inspect its file list. Require:

- archive commit/tag identity equals the candidate SHA and version;
- `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, and
  `pyproject.toml` versions match;
- every expected connect-google source, generated blueprint, test fixture,
  runtime lock, notice, and license file is present;
- no credential/client JSON, setup-run journal, token, local state, cache, or
  `__pycache__` entry is present; and
- fresh public Claude and Codex marketplace installs from the tag resolve the
  dispatcher interfaces to files extracted from that archive.

Any missing operative `docs/releasing.md` command, skipped required platform
smoke, mismatched dispatcher root, or archive mismatch keeps the verdict red.

### How the fix covers the shortcoming

Release evidence then applies to the artifact users will receive, not a
similarly named worktree or stale development pointer. It also unblocks the
required whole-node standards audit.

### Acceptance checks

- All retained dispatcher dry-runs resolve inside the release candidate.
- Instruction-module, instruction-source, Python-module, and Python-source
  requirements are queried from that same checkout with no material unknowns.
- Focused `connect-google` tests, repository validators, applicable
  portability/native checks, the exact-SHA matrix, and the staged gate pass.
- The tagged source archive hash, manifest, and fresh public install identities
  are retained and match the audited commit.
- The repository-wide release process and `docs/releasing.md` are implemented; until
  then public distribution remains blocked.

## Implementation order

1. Correct/source-map policy semantics and add red catalog/scope-review tests.
2. Define and validate the closed status, connection-plan, setup, journal,
   preflight/CAS, approval-receipt, and evidence schemas before implementation.
3. Add the narrow managed journal root, native-key lifecycle, two-phase
   journal/head transaction, cleanup-pending recovery, fair cursor, and their
   crash/access/installer/doctor/uninstall tests.
4. Add service-owned preflight/CAS interfaces and shared receipt verification,
   with red cross-service race/replay/failure-inventory contract tests.
5. Implement the Gmail API transport migration and email interface-version
   migration, including provider IDs, complete list/filter/folder/read/
   attachment/send semantics, smoke routes, reverse-consumer pins, and parity
   tests. The old Google IMAP/SMTP path must be unreachable before proceeding.
6. Implement the canonical service catalog and read-only client-status/detail
   and connection-plan layers; prove scopes equal the migrated transports.
7. Add red coordinator state/action/attempt/recovery tests, then implement
   setup prepare/apply/resume/cleanup using only the completed dependencies.
8. Rewrite authored instruction routes around typed machine results and
   explicit user decisions; replace obsolete phrase tests with scenario tests.
9. Implement and adversarially test the isolated-LLM runner and release
   platform/evidence runners, including POSIX and Windows launchers.
10. Regenerate blueprints/certificates, update declared pins, and pass focused
    selectors plus validators/full/staged gates in the candidate checkout.
11. Activate the exact candidate, run all dry-runs/standards queries, platform
    and isolated-LLM qualification, and disposable-project live cases.
12. Implement the repository-wide release process, verify the tagged archive/fresh
    public installs, and only then change the release verdict to green.

## Release completion criteria

- Every Google policy statement is state-specific and supported by current
  authoritative documentation.
- Users see exact service access implications before authorization.
- Every mechanically decidable route is encoded in a versioned machine result
  and deterministic coordinator.
- The workflow stops for every service choice, credential replacement,
  account-change approval, and browser consent that belongs to the user.
- No LLM must infer a failure cause, retry class, scope union, or next machine
  interface.
- Secrets, tokens, client JSON contents, and authorization URLs remain outside
  terminal machine results and ordinary logs.
- All tests and release gates pass against the exact packaged checkout.

## Non-goals

- Automatically creating or mutating a Google Cloud project without explicit
  Cloud authority and a separate reviewed design.
- Moving Drive, Calendar, or Gmail binding ownership into `connect-google`.
- Automatically approving credential replacement or account changes.
- Automatically retrying uncertain external mutations.
- Replacing the browser's Google consent decision with an assistant decision.
