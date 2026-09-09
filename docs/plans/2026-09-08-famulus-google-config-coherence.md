# Famulus Google Configuration Coherence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `famulus_paths` the sole owner of durable skill configuration directories, then remove the two obsolete Google authentication models so `credential_file` is the only Google authorization representation before the first release.

**Architecture:** Deliver three separately reviewable changes. Change 1 adds one small composition helper over the existing Famulus config-root calculation and routes existing path construction through it; this is the only change planned for today. Change 2 deletes the shared `credential_id` registry and bindings. Change 3 deletes service-local OAuth bootstrap and fallback code. No migration, compatibility reader, cleanup command, new registry, or replacement authentication mechanism is introduced.

**Tech Stack:** Python 3, pytest, JSON Schema, Officina blueprint YAML, generated skill interfaces.

**Spec:** `docs/plans/2026-09-08-famulus-config-path-ownership.md`

## Global constraints

- Apply the changes in order, but review and commit each independently. Today means Change 1 only.
- Reuse `resolve_famulus_paths`' existing platform branches, `FamulusPathsError` conventions, atomic writers, secret store, credential-file loader, service binders, blueprint regeneration, and repository validators.
- Do not add a second platform resolver, path registry, per-skill fields on `FamulusPaths`, or centralized leaf filenames.
- Do not read configuration or credentials from `FAMULUS_PLUGIN_DATA` or an installed plugin/cache directory.
- Do not migrate, unlink, overwrite, or clear any existing legacy credential file or keyring entry.
- Generated files have no handwritten LOC budget and must be regenerated, never hand-edited.
- More precisely, only generated regions of `SKILL.md` and the complete
  `references/blueprint-schema/runtime_dependencies.json` are excluded; all
  authored `SKILL.md` prose and every blueprint YAML line remain budgeted.
- A per-file addition cap is a stop condition, not a target. If it is insufficient, stop for scope review instead of moving lines to another file.
- Any increase to a per-file or summary LOC cap requires explicit user approval
  before the plan or implementation budget changes. Reallocation within an
  unchanged approved summary cap remains allowed when scope is unchanged.
- Deletion allowances are ceilings for partial-file cleanup. Whole-file deletion counts are the verified current line counts.

## Budget summary

| Change | New production Python | New test Python | Authored contract/docs additions | Planned deletion allowance |
|---|---:|---:|---:|---:|
| 1. Centralize paths | **78** | **86** | **83** | 0 whole files; replacement cleanup only |
| 2. Remove `credential_id` | **10** | **15** | **20** | 1,890 handwritten code/schema/test lines |
| 3. Remove service-local OAuth | **10** | **15** | **20** | 2,190 handwritten code/schema/test lines, including 1,192 whole-file lines |
| **All three** | **98** | **116** | **123** | **4,080** handwritten code/schema/test lines |

Task 1's raw-diff cap is 164 production/test lines because every repeated path
replacement counts as an addition. The centralized machinery itself remains
bounded to 27 lines in `famulus_paths`; the remainder is thin imports/calls and
existing test-fixture replacement. Changes 2 and 3 should normally consume zero
of their small addition allowances; those caps cover only cleanup glue or one
missing sole-model assertion. Contract and documentation additions are reported
separately because they are not executable machinery.

For each task, record `TASK_BASE=$(git rev-parse HEAD)` before its first edit.
Measure every tracked authored change with this exact command:

```bash
git diff --numstat "$TASK_BASE" -- . ':(exclude)skills/*/SKILL.md' ':(exclude)references/blueprint-schema/runtime_dependencies.json'
```

The first column is the budgeted addition count, and replacements consume that
count. The second column is the deletion count. Binary output or a `-` count
fails the budget check, and any authored path absent from the relevant task's
tables fails scope review. For affected mixed files, run
`git diff "$TASK_BASE" -- skills/connect-google/SKILL.md skills/cloud-files/SKILL.md skills/online-calendar/SKILL.md skills/email-client/SKILL.md`
and require all changed lines outside `BEGIN BLUEPRINT INTERFACES` / `END
BLUEPRINT INTERFACES` to be listed and budgeted as authored prose. Report those
generated blocks and the complete runtime-dependency manifest separately.

---

### Task 1: Centralize durable skill configuration paths — today

**Outcome:** Every runtime path for `connect-google`, `cloud-files`, `online-calendar`, and `email-client` is rooted in a directory returned by `resolve_skill_config_dir`. Authentication behavior and accepted configuration fields remain unchanged.

**Interface produced:**

```python
def resolve_skill_config_dir(
    skill_name: str,
    *,
    platform: str,
    home: Path,
    environ: Mapping[str, str],
) -> Path:
    """Return the durable Famulus configuration directory for one skill."""
```

The helper extracts/reuses the existing application config-root calculation and appends `skill_name`. It performs no filesystem I/O. It accepts the repository's lowercase hyphenated skill names and rejects empty names, absolute names, `.`, `..`, names containing separators, and Windows reserved basenames through `FamulusPathsError`/`ValueError` conventions. It validates only the home and config override used by the selected platform; unrelated plugin/data/state variables cannot block config lookup. The common helper is purely explicit. Existing runtime adapters may snapshot `Path.home()`, `sys.platform`, and `os.environ` at their current boundary, including an existing module-level default; Task 1 does not thread environment arguments through unrelated runtime call graphs.

#### Production Python files and exact addition caps: 78 total

| File | Change | Cap |
|---|---|---:|
| `src/officina/common/famulus_paths/__init__.py` | Extract the current config-root branch into one private calculation, add/export `resolve_skill_config_dir`, and keep `resolve_famulus_paths` calling the same calculation. | 27 |
| `src/officina/credentials/google.py` | Replace its `config_root / "connect-google"` constructions with the helper; retain all current credential models in this change. | 5 |
| `skills/connect-google/_rtx/_selected_credential.py` | Resolve `selected-credential.json` below `resolve_skill_config_dir("connect-google", ...)`. | 2 |
| `skills/connect-google/_rtx/_client_config.py` | Route temporary legacy client discovery through each service's helper-owned directory; retain discovery behavior. | 3 |
| `skills/cloud-files/_rtx/_ensure_oauth.py` | Route `client.json`, `credentials.json`, and `config.json` through `resolve_skill_config_dir("cloud-files", ...)`; preserve all fallbacks. | 11 |
| `skills/cloud-files/_rtx/_drive_gateway.py` | Route default `config.json` and legacy `credentials.json` through the same helper. | 3 |
| `skills/cloud-files/_rtx/_oauth_bootstrap.py` | Route the temporary legacy client/credentials defaults through the helper; retain OAuth behavior. | 5 |
| `skills/online-calendar/_rtx/_ensure_oauth.py` | Route all three service files through `resolve_skill_config_dir("online-calendar", ...)`. | 10 |
| `skills/online-calendar/_rtx/_gcal_client.py` | Route config and legacy credential defaults through the helper. | 3 |
| `skills/online-calendar/_rtx/_oauth_bootstrap.py` | Route the temporary legacy client/credentials defaults through the helper; retain OAuth behavior. | 5 |
| `skills/email-client/_rtx/_email_accounts.py` | Replace import-time `EMAIL_CLIENT_CONFIG_DIR`/`~/.config` ownership with one import-time call to the explicit helper using the module's current ambient snapshot; keep load/save call signatures unchanged. | 3 |
| `skills/email-client/_rtx/_imap_gateway.py` | Correct the runtime docstring to name the helper-owned account path. | 1 |
| **Total** |  | **78** |

`_loopback_oauth.py` already obtains the canonical path through
`officina.credentials.google` and needs no direct edit. Temporary legacy paths
remain behaviorally available until Task 3, but Task 1 still routes their
locations through the common owner.

#### Test files and exact addition caps: 86 total

| File | Assertion added or tightened | Cap |
|---|---|---:|
| `tests/test_officina_famulus_paths.py` | Linux/XDG, macOS, and Windows results; invalid skill names; no ambient environment or plugin-context dependency; no filesystem creation. | 29 |
| `conftest.py` | Isolate Windows config and home inputs for every repository and skill test. | 6 |
| `tests/test_officina_google_credential_files.py` | Canonical client and descriptor paths remain under the helper result. | 0 |
| `skills/connect-google/_rtx/tests/test_selected_credential.py` | Selection path uses the same root. | 0 |
| `skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py` | Existing path expectations move under `<config-root>/famulus/cloud-files`. | 9 |
| `skills/cloud-files/_rtx/tests/test_cloud_files.py` | Gateway defaults use the helper-owned directory. | 4 |
| `skills/online-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py` | Existing path expectations use the helper-owned directory. | 6 |
| `skills/online-calendar/_rtx/tests/test_gcal.py` | Client defaults use the helper-owned directory. | 2 |
| `skills/email-client/_rtx/tests/test_accounts.py` | Inject explicit resolver inputs instead of `EMAIL_CLIENT_CONFIG_DIR`; assert helper-owned `accounts.json`. | 4 |
| `skills/connect-google/_rtx/tests/test_credential_file_end_to_end.py` | Replace the email config override fixture with the canonical config-root override. | 3 |
| `skills/connect-google/_rtx/tests/test_client_config.py` | Replace legacy discovery path expectations with helper-owned service paths. | 4 |
| `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py` | Replace the authored client-discovery path contract expectation. | 2 |
| `skills/email-client/_rtx/tests/test_gmail_credential_file_binding.py` | Replace the email config override fixture with the canonical config-root override. | 1 |
| `skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py` | Adjust existing helper-owned path fixtures. | 8 |
| `skills/cloud-files/_rtx/tests/test_oauth_transaction.py` | Existing fixtures remain valid without additions. | 0 |
| `skills/cloud-files/_rtx/tests/test_setup_oauth.py` | Existing expectations remain valid without additions. | 0 |
| `skills/online-calendar/_rtx/tests/test_calendar_credential_file_binding.py` | Adjust existing helper-owned path fixtures. | 8 |
| `skills/online-calendar/_rtx/tests/test_calendar_oauth_transaction.py` | Existing fixtures remain valid without additions. | 0 |
| **Total** |  | **86** |

Replacements consume the per-file allowance. Add new test cases only for the
new helper invariants; update existing assertions and fixtures everywhere else.

#### Authored contracts/docs and exact addition caps: 83 total

| File | Change | Cap |
|---|---|---:|
| `src/officina/common/blueprints/famulus-paths.yaml` | Describe the new helper argument, validation precondition, result, and refusal outcome in the existing Python API contract. | 15 |
| `src/officina/common/blueprint.yaml` | Add `connect-google._rtx`, `cloud-files._rtx`, `online-calendar._rtx`, and `email-client._rtx` to the existing `common.interface.famulus-paths` caller allowlist. | 4 |
| `skills/connect-google/_rtx/blueprints/rtx-selected-credential.yaml` | Declare the common path dependency and helper-owned selection path. | 1 |
| `skills/connect-google/_rtx/blueprints/rtx-client-config.yaml` | Replace temporary legacy discovery paths with helper-owned service paths. | 3 |
| `skills/cloud-files/_rtx/blueprint.yaml` | Replace hardcoded logical config path and declare the existing common interface dependency. | 1 |
| `skills/cloud-files/_rtx/blueprints/rtx-init.yaml` | Declare the common path dependency for owned `_drive_gateway.py`. | 1 |
| `skills/cloud-files/_rtx/blueprints/rtx-ensure-oauth.yaml` | Declare the common dependency and replace literal paths. | 11 |
| `skills/cloud-files/_rtx/blueprints/rtx-drive-readiness.yaml` | Replace literal configuration paths. | 2 |
| `skills/cloud-files/_rtx/blueprints/rtx-oauth-bootstrap.yaml` | Replace temporary legacy defaults and declare the common dependency. | 4 |
| `skills/online-calendar/_rtx/blueprint.yaml` | Replace hardcoded logical config path and declare the dependency. | 2 |
| `skills/online-calendar/_rtx/blueprints/rtx-ensure-oauth.yaml` | Declare the common dependency and replace literal paths. | 9 |
| `skills/online-calendar/_rtx/blueprints/rtx-gcal-client.yaml` | Replace literal paths and declare the common dependency. | 3 |
| `skills/online-calendar/_rtx/blueprints/rtx-oauth-bootstrap.yaml` | Replace temporary legacy defaults and declare the common dependency. | 3 |
| `skills/email-client/_rtx/blueprint.yaml` | Inspect only; no addition expected. | 0 |
| `skills/email-client/_rtx/blueprints/rtx-email-accounts.yaml` | Declare the common dependency and replace literal paths. | 3 |
| `skills/cloud-files/SKILL.md` (authored prose only) | Replace the two Linux-shaped config path statements; generated interface block remains excluded. | 2 |
| `docs/security-and-privacy.md` | Describe all four locations as children of the platform Famulus config root, not Linux-only `~/.config/<skill>`. | 8 |
| `docs/plans/logical-resource-addressing.md` | Replace the affected logical examples with `<CONFIG>/<skill>/...`. | 5 |
| `docs/plans/logical-resource-registry.yaml` | Replace affected `$home/.config/<skill>` resource addresses with the canonical config-root notation. | 6 |
| **Total** |  | **83** |

All blueprint YAML above is authored and must be edited directly. Regenerate only
the injected interface regions of affected `SKILL.md` files and the complete
`references/blueprint-schema/runtime_dependencies.json`; report that churn
separately.

- [ ] Add only the new helper-invariant tests, update the listed existing path
  expectations/fixtures, and run them to observe failures caused by the missing
  helper/current Linux-shaped paths.
- [ ] Implement the common helper within its 27-line cap, using one extracted config-root calculation shared with `resolve_famulus_paths`.
- [ ] Replace each caller's path construction without changing authentication branches or schemas.
- [ ] Update the authored contracts/docs and regenerate derived artifacts.
- [ ] Invoke `famulus_dispatcher.invoke` with caller `skill-maker`, interface
  `skill-maker._rtx.interface.sync-blueprints`, version `1`, and arguments
  `{"options": {}, "positionals": [], "stdin": null}`.
- [ ] Run:

```bash
python3 repo_checks.py --task tests:shared --selector tests/test_officina_famulus_paths.py --selector tests/test_officina_google_credential_files.py --selector skills/connect-google/_rtx/tests/test_selected_credential.py --selector skills/connect-google/_rtx/tests/test_credential_file_end_to_end.py --selector skills/connect-google/_rtx/tests/test_client_config.py --selector skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py --selector skills/cloud-files/_rtx/tests/test_cloud_files.py --selector skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py --selector skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py --selector skills/cloud-files/_rtx/tests/test_oauth_transaction.py --selector skills/cloud-files/_rtx/tests/test_setup_oauth.py --selector skills/online-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py --selector skills/online-calendar/_rtx/tests/test_gcal.py --selector skills/online-calendar/_rtx/tests/test_calendar_credential_file_binding.py --selector skills/online-calendar/_rtx/tests/test_calendar_oauth_transaction.py --selector skills/email-client/_rtx/tests/test_accounts.py --selector skills/email-client/_rtx/tests/test_gmail_credential_file_binding.py --repository-view working
python3 repo_checks.py --suite validators --repository-view working
python3 repo_checks.py --suite precommit --repository-view working
rg -n "Path\.home\(\).*\.config|\.config/(connect-google|cloud-files|online-calendar|email-client)" src/officina/credentials/google.py skills/connect-google/_rtx skills/cloud-files/_rtx skills/online-calendar/_rtx skills/email-client/_rtx --glob '*.py'
```

The final `rg` must return no matches. Confirm the diff contains no
credential-model removal, no whole-file deletion, no migration logic, and no
more than 78 production plus 86 test addition lines.

---

### Task 2: Remove the shared `credential_id` registry

**Outcome:** Timestamped `credential_file` descriptors remain fully operational, while `<CONFIG>/connect-google/credentials.json`, `GoogleCredentialRef`, registry locking/publication, ID-based refresh, and all service `credential_id` bindings cease to be runtime or schema authorities. Service-local OAuth remains temporarily available until Task 3.

#### New-line caps

Production additions total exactly 10: `src/officina/credentials/google.py` 2, `skills/cloud-files/_rtx/_ensure_oauth.py` 2, `skills/online-calendar/_rtx/_ensure_oauth.py` 2, `skills/email-client/_rtx/_email_accounts.py` 2, and `skills/email-client/_rtx/_oauth_tokens.py` 2. All other production files have a zero-addition cap.

Test additions total exactly 15: `tests/test_officina_google_credential_files.py` 5, `skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py` 3, `skills/online-calendar/_rtx/tests/test_calendar_credential_file_binding.py` 3, and `skills/email-client/_rtx/tests/test_gmail_credential_file_binding.py` 4. Add a line only if the surviving suite does not already prove the sole-model rule; otherwise consume zero.

Authored contract/docs additions total exactly 20, allocated in the exhaustive
authored-file table below. Prefer deletion and sentence replacement.

#### Partial-file deletion allowances: 1,890 total

| File | Delete | Allowance |
|---|---|---:|
| `src/officina/credentials/google.py` | Registry path, registry/ref datatypes, locking/publication helpers, `store_google_credential`, `load_credential`, and ID-based `refresh_access_token`; retain all `*credential_file*` APIs and token exchange. | 390 |
| `tests/test_officina_google_credentials.py` | Registry storage, load, locking, rollback, and ID-refresh tests; retain tests shared by surviving token exchange behavior. | 690 |
| `src/officina/configuration/schema.json` | Only the `credential_id` alternative; retain `credentials_path` until Task 3. | 4 |
| `skills/cloud-files/_rtx/_ensure_oauth.py` | `use_google_credential`, CLI branch, and ID handling in prior-binding checks. | 55 |
| `skills/cloud-files/_rtx/_drive_gateway.py` | `credential_id` field/loading/token branch and registry import; retain `credentials_path` fallback until Task 3. | 26 |
| `skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py` | ID binder fixtures/tests. | 85 |
| `skills/cloud-files/_rtx/tests/test_cloud_files.py` | ID parsing, refresh, and cache tests. | 125 |
| `skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py` | Compatibility expectations for deleting ID/path fields. | 20 |
| `skills/online-calendar/_rtx/_ensure_oauth.py` | `use_google_credential`, CLI branch, and ID prior-binding lookup. | 55 |
| `skills/online-calendar/_rtx/_gcal_client.py` | ID loader and ID-refresh branch. | 30 |
| `skills/online-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py` | Registry fixtures and ID binder tests. | 85 |
| `skills/online-calendar/_rtx/tests/test_gcal.py` | ID refresh test. | 25 |
| `skills/online-calendar/_rtx/tests/test_calendar_credential_file_binding.py` | Compatibility expectations for deleting `credential_id`. | 20 |
| `skills/email-client/_rtx/_email_accounts.py` | ID binder, CLI branch, and ID prior-binding lookup. | 60 |
| `skills/email-client/_rtx/_oauth_tokens.py` | ID-based token refresh branch and imports; retain credential-file and temporary service-local OAuth paths. | 30 |
| `skills/email-client/_rtx/tests/test_accounts.py` | Registry fixtures and ID binder tests. | 125 |
| `skills/email-client/_rtx/tests/test_oauth_tokens.py` | ID refresh tests. | 40 |
| `skills/email-client/_rtx/tests/test_gmail_credential_file_binding.py` | Compatibility expectations for deleting `credential_id`. | 25 |
| **Total** |  | **1,890** |

#### Exhaustive authored contract/docs files: 20 added lines total

| File | Change | Addition cap |
|---|---|---:|
| `src/officina/credentials/blueprints/google.yaml` | Remove registry/ref outputs and describe the surviving file API. | 3 |
| `skills/cloud-files/blueprint.yaml` | Remove the ID-binder dependency/interface. | 1 |
| `skills/cloud-files/_rtx/blueprint.yaml` | Remove the ID-binder interface. | 1 |
| `skills/cloud-files/_rtx/blueprints/rtx-ensure-oauth.yaml` | Remove the ID-binder contract and ID references. | 2 |
| `skills/cloud-files/_rtx/blueprints/rtx-init.yaml` | Remove compatibility wording for `credential_id`. | 1 |
| `skills/online-calendar/blueprint.yaml` | Remove the ID-binder dependency/interface. | 1 |
| `skills/online-calendar/_rtx/blueprint.yaml` | Remove the ID writer/interface and its reason. | 1 |
| `skills/online-calendar/_rtx/blueprints/rtx-ensure-oauth.yaml` | Remove the ID-binder contract and ID references. | 2 |
| `skills/online-calendar/_rtx/blueprints/rtx-gcal-client.yaml` | Remove the ID fallback statement. | 1 |
| `skills/email-client/blueprint.yaml` | Remove the ID-binder dependency/interface. | 1 |
| `skills/email-client/_rtx/blueprint.yaml` | Remove the ID-binder interface. | 1 |
| `skills/email-client/_rtx/blueprints/rtx-email-accounts.yaml` | Remove the ID-binder contract and ID references. | 2 |
| `skills/email-client/_rtx/blueprints/rtx-init.yaml` | Remove compatibility wording for `credential_id`. | 1 |
| `tests/fixtures/blueprint_schemas/v6/facade-cutover.json` | Delete the three ID-binder facade entries. | 0 |
| `docs/security-and-privacy.md` | Remove ID-reference wording from the binding table. | 2 |
| **Total** |  | **20** |

No setup instruction or connect-google delegation file changes in Task 2: their
current routes already name the retained `use-google-credential-file` binders.
Regenerate the interface regions of `skills/cloud-files/SKILL.md`,
`skills/online-calendar/SKILL.md`, and `skills/email-client/SKILL.md`, plus
`references/blueprint-schema/runtime_dependencies.json`.

- [ ] Delete registry-specific tests first and retain the credential-file end-to-end tests unchanged where possible.
- [ ] Delete the shared registry implementation and exports without adding adapters or tombstones.
- [ ] Delete each service's ID binder, runtime fallback, CLI/parser route, schema field, and interface declaration.
- [ ] If the surviving suite lacks a direct invariant, add at most one focused assertion that `credential_file` is the only accepted Google binding field.
- [ ] Regenerate affected skill and runtime-dependency artifacts.
- [ ] Invoke `famulus_dispatcher.invoke` with caller `skill-maker`, interface
  `skill-maker._rtx.interface.sync-blueprints`, version `1`, and arguments
  `{"options": {}, "positionals": [], "stdin": null}`.
- [ ] Run:

```bash
python3 repo_checks.py --task tests:shared --selector tests/test_officina_google_credentials.py --selector tests/test_officina_google_credential_files.py --selector skills/cloud-files/_rtx/tests/test_cloud_files.py --selector skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py --selector skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py --selector skills/online-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py --selector skills/online-calendar/_rtx/tests/test_gcal.py --selector skills/online-calendar/_rtx/tests/test_calendar_credential_file_binding.py --selector skills/email-client/_rtx/tests/test_accounts.py --selector skills/email-client/_rtx/tests/test_oauth_tokens.py --selector skills/email-client/_rtx/tests/test_gmail_credential_file_binding.py --repository-view working
python3 repo_checks.py --suite validators --repository-view working
python3 repo_checks.py --suite precommit --repository-view working
rg -n "credential_id|use-google-credential([^ -]|$)|connect-google/credentials\.json" src/officina/credentials src/officina/configuration/schema.json skills/cloud-files skills/online-calendar skills/email-client --glob '!*.pooled-blueprint-review.yaml' --glob '!SKILL.md'
```

The final `rg` must return no matches. The retained
`use-google-credential-file` name does not match the expression.

---

### Task 3: Remove service-local OAuth

**Outcome:** Google OAuth setup is owned only by connect-google. Cloud-files, online-calendar, and email-client consume a verified timestamped `credential_file`; their former local `client.json`, `credentials.json`, OAuth setup interfaces, and fallback token stores are no longer read or written. Existing user files and keyring entries are left untouched.

#### New-line caps

Production additions total exactly 10: `skills/cloud-files/_rtx/_ensure_oauth.py` 2, `skills/cloud-files/_rtx/_drive_gateway.py` 1, `skills/online-calendar/_rtx/_ensure_oauth.py` 2, `skills/online-calendar/_rtx/_gcal_client.py` 1, `skills/email-client/_rtx/_email_accounts.py` 2, and `skills/email-client/_rtx/_oauth_tokens.py` 2. Every other production file has a zero-addition cap.

Test additions total exactly 15: `skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py` 4, `skills/online-calendar/_rtx/tests/test_calendar_credential_file_binding.py` 4, and `skills/email-client/_rtx/tests/test_gmail_credential_file_binding.py` 7. These lines are available only for a missing no-fallback/sole-owner assertion.

Authored contract/docs additions total exactly 20, allocated in the exhaustive
authored-file table below. Prefer deletion.

#### Whole-file deletions: exactly 1,192 lines

| File | Lines |
|---|---:|
| `skills/cloud-files/_rtx/_oauth_bootstrap.py` | 228 |
| `skills/cloud-files/_rtx/blueprints/rtx-oauth-bootstrap.yaml` | 249 |
| `skills/cloud-files/_rtx/tests/test_oauth_transaction.py` | 100 |
| `skills/cloud-files/_rtx/tests/test_setup_oauth.py` | 54 |
| `skills/online-calendar/_rtx/_oauth_bootstrap.py` | 197 |
| `skills/online-calendar/_rtx/blueprints/rtx-oauth-bootstrap.yaml` | 264 |
| `skills/online-calendar/_rtx/tests/test_calendar_oauth_transaction.py` | 100 |
| **Total** | **1,192** |

#### Partial-file deletion allowances: 998 lines; Task 3 total 2,190

| File | Delete | Allowance |
|---|---|---:|
| `src/officina/configuration/schema.json` | Remove the now-obsolete `credentials_path` alternative. | 8 |
| `skills/cloud-files/_rtx/_ensure_oauth.py` | `ensure-oauth`, service-local `credentials.json` fallback, client discovery, and parser branch; retain credential-file binder and non-auth `write-config`. | 85 |
| `skills/cloud-files/_rtx/_drive_gateway.py` | `credentials_path`, service-local `credentials.json` loading/fallback, and related guidance. | 65 |
| `skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py` | Local readiness/setup tests, retaining binder/write-config coverage. | 65 |
| `skills/cloud-files/_rtx/tests/test_cloud_files.py` | Local credential fallback and `credentials_path` tests. | 45 |
| `skills/online-calendar/_rtx/_ensure_oauth.py` | `ensure-oauth`, service-local `credentials.json` fallback, client discovery, and parser branch; retain credential-file binder. | 80 |
| `skills/online-calendar/_rtx/_gcal_client.py` | Local `credentials.json` refresh/fallback and setup guidance. | 40 |
| `skills/online-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py` | Local readiness/setup tests. | 55 |
| `skills/online-calendar/_rtx/tests/test_gcal.py` | Local credential refresh/fallback tests. | 40 |
| `skills/email-client/_rtx/_email_accounts.py` | `setup-oauth` command/handler, OAuth prior-state branch, and only the call to `clear_oauth_credentials`; retain IMAP/SMTP app-password purge. | 50 |
| `skills/email-client/_rtx/_oauth_tokens.py` | Local OAuth setup, local refresh-token storage/clearer, and fallback; retain auth-mode helpers and credential-file token refresh used by IMAP/SMTP. | 212 |
| `skills/email-client/_rtx/tests/test_accounts.py` | Local setup-oauth tests. | 20 |
| `skills/email-client/_rtx/tests/test_oauth_tokens.py` | Local setup/refresh/fallback tests; retain credential-file behavior tests. | 115 |
| `skills/connect-google/_rtx/_client_config.py` | `_legacy_candidates` and service-local client discovery handling. | 25 |
| `skills/connect-google/_rtx/tests/test_client_config.py` | Delete the two legacy-discovery tests. | 42 |
| `skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py` | Legacy client-discovery path expectations. | 20 |
| `skills/connect-google/_rtx/tests/test_service_delegation.py` | Assertions for removed local OAuth interfaces. | 20 |
| `skills/email-client/_rtx/tests/test_mail.py` | Patch/assertion for the deleted legacy refresh helper. | 5 |
| `skills/setup-interface-manager/_rtx/_setup_dispatches.py` | Stale prohibition/reference to `accounts-setup-oauth`; retain credential-file routes. | 3 |
| `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py` | Corresponding stale setup-dispatch expectation. | 3 |
| **Total partial** |  | **998** |
| **Task 3 total** |  | **2,190** |

#### Exhaustive authored contract/docs files: 20 added lines total

| File | Change | Addition cap |
|---|---|---:|
| `skills/cloud-files/blueprint.yaml` | Remove `ensure-oauth`/`setup-oauth` dependencies and interfaces. | 1 |
| `skills/cloud-files/_rtx/blueprint.yaml` | Remove local OAuth interfaces/source registration. | 1 |
| `skills/cloud-files/_rtx/blueprints/rtx-init.yaml` | State credential-file-only initialization. | 1 |
| `skills/cloud-files/_rtx/blueprints/rtx-ensure-oauth.yaml` | Remove ensure/local-fallback contract while retaining binder/write-config contracts. | 2 |
| `skills/cloud-files/_rtx/blueprints/rtx-drive-readiness.yaml` | Remove local credentials fallback description. | 1 |
| `skills/online-calendar/blueprint.yaml` | Remove `ensure-oauth`/`setup-oauth` dependencies and interfaces. | 1 |
| `skills/online-calendar/_rtx/blueprint.yaml` | Remove local OAuth interfaces/source registration. | 1 |
| `skills/online-calendar/_rtx/blueprints/rtx-ensure-oauth.yaml` | Remove ensure/local-fallback contract while retaining binder. | 2 |
| `skills/online-calendar/_rtx/blueprints/rtx-gcal-client.yaml` | Remove local credentials fallback description. | 1 |
| `skills/email-client/blueprint.yaml` | Remove `accounts-setup-oauth` dependency/interface. | 1 |
| `skills/email-client/_rtx/blueprint.yaml` | Remove `accounts-setup-oauth` interface. | 1 |
| `skills/email-client/_rtx/blueprints/rtx-init.yaml` | State credential-file-only Google OAuth behavior. | 1 |
| `skills/email-client/_rtx/blueprints/rtx-email-accounts.yaml` | Remove local setup contract while retaining account/password/file-binder contracts. | 2 |
| `skills/connect-google/_rtx/blueprint.yaml` | Remove declared callers to service-local setup interfaces. | 1 |
| `skills/connect-google/_rtx/blueprints/rtx-client-config.yaml` | Remove service-local client discovery paths/contract. | 1 |
| `tests/fixtures/blueprint_schemas/v6/facade-cutover.json` | Delete removed local OAuth facade entries. | 0 |
| `docs/security-and-privacy.md` | Remove service-local OAuth path inventory. | 2 |
| `skills/email-client/instructions/setup.md` | Delete the stale `accounts-setup-oauth` reference. | 0 |
| `skills/connect-google/instructions/connect-services.md` | Delete legacy service-local client import guidance. | 0 |
| `docs/plans/logical-resource-addressing.md` | Delete service-local client/credentials resources. | 0 |
| `docs/plans/logical-resource-registry.yaml` | Delete the same resources and reader relationship. | 0 |
| `skills/connect-google/SKILL.md` (authored prose only) | Delete legacy client-discovery guidance. | 0 |
| `skills/cloud-files/SKILL.md` (authored prose only) | Delete legacy local-OAuth guidance. | 0 |
| `skills/online-calendar/SKILL.md` (authored prose only) | Delete legacy local-OAuth guidance. | 0 |
| `skills/email-client/SKILL.md` (authored prose only) | Delete legacy local-OAuth guidance. | 0 |
| **Total** |  | **20** |

Cloud Files and Online Calendar setup instructions and all three
`blueprints/setup.yaml` sources already use the retained credential-file routes
and remain unchanged. Email Client's setup instruction receives only the
zero-addition deletion listed above. Regenerate the interface regions of `skills/connect-google/SKILL.md`,
`skills/cloud-files/SKILL.md`, `skills/online-calendar/SKILL.md`, and
`skills/email-client/SKILL.md`, plus
`references/blueprint-schema/runtime_dependencies.json`.

- [ ] Delete the seven whole files and their source/interface registrations.
- [ ] Delete service-local readers, writers, setup parsers, fallbacks, and guidance while retaining credential-file binders and email app-password behavior.
- [ ] Delete obsolete tests; add no replacement tests unless the surviving binder suites lack a direct no-fallback assertion.
- [ ] Verify the existing setup routes already use connect-google followed by the retained `use-google-credential-file` binders, then regenerate derived artifacts.
- [ ] Invoke `famulus_dispatcher.invoke` with caller `skill-maker`, interface
  `skill-maker._rtx.interface.sync-blueprints`, version `1`, and arguments
  `{"options": {}, "positionals": [], "stdin": null}`.
- [ ] Run:

```bash
python3 repo_checks.py --task tests:shared --selector skills/connect-google/_rtx/tests/test_client_config.py --selector skills/connect-google/_rtx/tests/test_connect_google_llm_routing.py --selector skills/connect-google/_rtx/tests/test_service_delegation.py --selector skills/cloud-files/_rtx/tests/test_cloud_files.py --selector skills/cloud-files/_rtx/tests/test_cloud_files_ensure_oauth.py --selector skills/cloud-files/_rtx/tests/test_drive_credential_file_binding.py --selector skills/online-calendar/_rtx/tests/test_g_calendar_ensure_oauth.py --selector skills/online-calendar/_rtx/tests/test_gcal.py --selector skills/online-calendar/_rtx/tests/test_calendar_credential_file_binding.py --selector skills/email-client/_rtx/tests/test_accounts.py --selector skills/email-client/_rtx/tests/test_oauth_tokens.py --selector skills/email-client/_rtx/tests/test_gmail_credential_file_binding.py --selector skills/email-client/_rtx/tests/test_mail.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --repository-view working
python3 repo_checks.py --suite validators --repository-view working
python3 repo_checks.py --suite precommit --repository-view working
rg -n "\.config/(cloud-files|online-calendar|email-client)/(client|credentials)\.json|accounts-setup-oauth|(^|[^-])setup-oauth|ensure-oauth|credentials_path" skills/cloud-files skills/online-calendar skills/email-client skills/connect-google skills/setup-interface-manager src/officina/configuration/schema.json docs/security-and-privacy.md docs/plans/logical-resource-addressing.md docs/plans/logical-resource-registry.yaml --glob '!*.pooled-blueprint-review.yaml'
```

The final `rg` must return no matches. `credential_file` and
`use-google-credential-file` remain required and are not matched.

## Final acceptance checks

- [ ] `resolve_skill_config_dir` is the single composition point for all four skill configuration directories and shares the existing config-root calculation with `resolve_famulus_paths`.
- [ ] Changing `FAMULUS_PLUGIN_DATA`, `FAMULUS_HOST`, or an unrelated module
  location cannot change any Google descriptor or service binding path; this is
  established by explicit-input path tests and the absence of plugin/cache
  inputs from the helper, not by an installed-copy integration test.
- [ ] `credential_file` is the only Google authorization representation accepted by service binders and runtime clients.
- [ ] Only connect-google owns Google OAuth client/authorization descriptors and their keyring references.
- [ ] No legacy file or keyring entry is deleted as a side effect.
- [ ] Handwritten additions remain within every per-file cap and summary total; generated churn and deletions are reported separately.
- [ ] `python3 repo_checks.py --suite precommit --repository-view working` passes.

## Execution boundary

Implement Task 1 today. Tasks 2 and 3 are approved cleanup designs but remain separate review/commit units; do not fold them into Task 1 merely because the plan records them together.
