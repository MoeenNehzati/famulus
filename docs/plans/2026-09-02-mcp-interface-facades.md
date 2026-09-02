# MCP Interface Facades: First-Release Implementation Plan

> Plan only. Implement with `superpowers:executing-plans` or
> `superpowers:subagent-driven-development`; do not broaden the release.

## Outcome

Every public blueprint export backed by a Python process interface is available
from Famulus MCP under its exact interface ID. `invoke` remains the execution
engine and escape hatch; `reload` rebuilds the facade catalog. A future eligible
interface therefore needs only its normal blueprint declaration and generated
skill block—no MCP-specific Python registration.

The first supported deferred client is Codex through `mcp.json`. Famulus still
returns its complete catalog from MCP `tools/list`; Codex must keep that catalog
out of initial model context and resolve the exact tool named by the active
skill. Do not claim this context property for Claude or another host without the
same acceptance test.

## Fixed contract

- Permanent tools: `invoke(caller, interface, version, arguments, dry_run)` and
  `reload()`.
- Generated tool `I`: `I(arguments, dry_run=False)`. Its closure binds `I`; the
  hidden binding must not appear in its input schema.
- A facade has only its exact interface-ID name, the generic `arguments` and
  `dry_run` schema, and empty/default metadata. It copies no blueprint prose,
  examples, annotations, or interface-specific schema.
- The facade delegates to the same private invocation engine as `invoke` with
  owner-as-caller, `version=None`, and `host_caller=False`. After authorization,
  all setup/preflight/result paths use `authorized.export.version`. Public
  `invoke` keeps its existing signature, `host_caller=True`, and current
  dry-run/setup-manager shortcut. Facades—including dry-run and setup-manager
  facades—always take the direct owner-self authorization path; they must not
  enter the host-caller `resolve_dispatch` shortcut.
- Eligible means a public graph export owned beneath a module root configured by
  `officina.toml`, whose selected source has a Python gateway and process binding.
  Repository fixtures and other non-module-root blueprints are never exposed.
  Exclude `invoke`, `reload`, invalid MCP names, and collisions; fail reload
  rather than silently omit an otherwise eligible collision.
- Cache location: `${FAMULUS_PLUGIN_DATA}/mcp-interface-facades-v1.json`. With
  no plugin provenance, rebuild in memory and write nothing to the checkout.
- Cache JSON contains only `schema_version: 1`, `source_digest`, and a sorted,
  unique `interfaces` array. It is discovery data, never route or authorization
  authority.
- Staleness is SHA-256 over the canonical inventory's sorted repository-relative
  blueprint paths and exact bytes beneath the currently configured module roots,
  length-delimited. A module-root selection change changes that path set without
  hashing configuration bytes. Only eligible blueprint paths/bytes affect the
  digest; fixture blueprints, Python, skills, tests, docs, mtimes, and Git state
  do not.
- Startup registers a strict matching cache; missing, invalid, or stale cache
  runs the same rebuild used by `reload` before stdio begins.
- Manual reload is serialized. Build and preconstruct the whole next catalog
  first. Then, without an `await`, synchronously replace generated registrations
  and atomically CAS-write the cache. On mutation or write failure, restore the
  prior generated registrations; a failed atomic write leaves prior cache bytes.
  Await `notifications/tools/list_changed` only after commit. Notification
  failure is reported as a warning and does not roll back committed state.
- Treat a cache CAS conflict as another server winning the race: re-read and use
  the winner only if schema, digest, and catalog match the just-built result;
  otherwise retry the complete snapshot/graph build once, then return a stable
  failure. Startup and manual reload use this same converge-or-retry rule.
- Use a small MCP-SDK adapter for stdio initialization with
  `NotificationOptions(tools_changed=True)`. The first release tests and pins
  the live-verified floor `mcp>=1.29.1,<2` in all dependency authorities and runs
  the focused MCP suite once with exactly `mcp==1.29.1`.
- Skill-generated executable-interface and managed-setup instructions name exact
  facade IDs without `@version`; rendered Arguments JSON becomes the facade's
  `arguments`. No caller/interface/version wrapper or catalog is generated.

## Gross LOC budget

Gross LOC means additions plus deletions in the final diff. Every row is a hard
per-file ceiling. Do not add an unlisted split file; revise this plan first if a
listed budget is insufficient.

| File | Budget | Purpose |
|---|---:|---|
| `docs/plans/2026-09-02-mcp-interface-facades.md` | 300 | This implementation plan. |
| `src/officina/blueprints/inventory.py` | 45 | Public iterator over canonical paths within selected module roots. |
| `tests/test_blueprint_inventory.py` | 70 | Prove the iterator preserves canonical exclusions/order/no-follow behavior. |
| `mcp_facades.py` (new) | 260 | Digest, strict cache, graph projection, facade catalog model, atomic publication. |
| `tests/test_mcp_facades.py` (new) | 300 | Digest/cache/projection/rollback unit tests. |
| `mcp_server.py` | 300 | Shared invocation path, permanent reload, facade factory/registration, startup, SDK adapter. |
| `tests/test_famulus_mcp.py` | 300 | MCP schemas, equivalence, startup/reload, notification, stdio tests. |
| `mcp-core.json` | 60 | Permanent/generated tool and cache contract; no host-policy essay. |
| `pyproject.toml` | 4 | Tested MCP minimum version. |
| `requirements-ci.txt` | 4 | Match the runtime dependency bound. |
| `tests/test_google_dependency_ownership.py` | 10 | Match the MCP dependency expectation. |
| `tests/test_setup_python_environment_skill.py` | 10 | Match the MCP dependency expectation. |
| `tests/test_repository_test_checks.py` | 10 | Match the MCP dependency expectation. |
| `skills/skill-maker/_rtx/_blueprint_syncer.py` | 70 | Exact facade wording, including managed-setup tool names. |
| `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` | 100 | Generation tests for ordinary and managed interfaces. |
| `validators/skill/skill_md_dispatch.py` | 50 | Enforce facade-form generated blocks. |
| `tests/validate_skill_md_dispatch.py` | 80 | Validator regression tests. |
| Each generated `skills/*/SKILL.md` listed below | 20 each | Marker-bounded regeneration only. |

Generated files (the current 29-file set):

```text
skills/bib-audit/SKILL.md
skills/ci-debug/SKILL.md
skills/cloud-files/SKILL.md
skills/connect-google/SKILL.md
skills/daily-plan/SKILL.md
skills/dev-activation/SKILL.md
skills/distill-to-rutters/SKILL.md
skills/email-client/SKILL.md
skills/email-triage/SKILL.md
skills/find-handoff-candidates/SKILL.md
skills/get-weather/SKILL.md
skills/initialize-tdd/SKILL.md
skills/install-launchers/SKILL.md
skills/list-manager/SKILL.md
skills/llm-wakeup/SKILL.md
skills/math-dependency-graph/SKILL.md
skills/milestone-logging/SKILL.md
skills/node-certify/SKILL.md
skills/node-drift/SKILL.md
skills/online-calendar/SKILL.md
skills/pdf-to-markdown/SKILL.md
skills/recurring-tasks/SKILL.md
skills/refactor-node/SKILL.md
skills/regenerate-blueprints/SKILL.md
skills/relocate-nodes/SKILL.md
skills/send-feedback/SKILL.md
skills/setup-interface-manager/SKILL.md
skills/skill-maker/SKILL.md
skills/wrap-up/SKILL.md
```

Do not change `mcp.json` or `.claude-plugin/plugin.json`. Preserve unrelated
worktree changes, including the four pre-existing deleted plans.

## Implementation sequence

### 1. Canonical discovery and cache

1. Add failing inventory tests, then expose a public iterator which reuses
   `_ignored_paths`, `_canonical_module_roots`, and `_blueprint_paths`; do not
   create a second filesystem walk. It accepts the configuration's selected
   module roots and returns only their accepted paths in canonical order without
   parsing YAML. Test that fixture changes are ignored and changing the selected
   roots changes the path set and resulting cache decision.
2. Add failing `mcp_facades` tests for deterministic path+byte hashing; blueprint
   add/remove/rename/content staleness; Python/skill changes ignored; strict cache
   rejection; and confined CAS publication.
3. Implement those primitives with `read_regular_file_bytes` and
   `atomic_compare_and_replace_bytes`.
4. Project IDs from the existing validated graph machinery using graph exports,
   source gateway data, and `gateway_language_name`. Restrict owners to configured
   module roots and test that `tests/fixtures` exports are absent. Use snapshot A, the existing
   graph load, then snapshot B; publish only when both digests match (retry the
   whole build once, then fail). All three operations thereby reuse canonical
   inventory discovery without changing the graph API or duplicating traversal.

Focused gate:

```bash
pytest -q tests/test_blueprint_inventory.py tests/test_mcp_facades.py
```

### 2. Permanent reload, facades, and startup

1. First write MCP tests for permanent tools, minimal facade schema, current-
   version invocation equivalence, dry-run, managed setup, setup-manager routing,
   cache hit/miss/stale startup, same-data and divergent CAS races, rollback,
   direct owner-self facade dry-run/setup-manager behavior, and one post-commit
   list-change notification.
2. Extract one private authorized invocation engine from `invoke`; preserve all
   existing return payloads and special branches. Permanent `invoke` supplies its
   explicit version. A facade authorizes with `None`, then rebinds every later
   use to the selected export version.
3. Add `build_server`, minimal closure-based facade creation, generated-name
   tracking, and the serialized commit described in the fixed contract. Permanent
   tools can never be removed.
4. Add startup cache selection and use the identical rebuild function behind
   `reload`. Invalid cache or initial rebuild failure leaves a runnable server
   with `invoke` and `reload` and a stable diagnostic.
5. Isolate the MCP 1.x initialization/notification compatibility code, pin its
   `mcp>=1.29.1,<2` minimum in `pyproject.toml`, `requirements-ci.txt`, and
   `mcp-core.json`'s `core_packages`; update the three exact-expectation tests in
   the budget table and keep the rest of that contract compact.

Focused gate:

```bash
pytest -q tests/test_mcp_facades.py tests/test_famulus_mcp.py tests/test_mcp_setup_preflight.py tests/test_setup_interface_manager_integration.py
pytest -q tests/test_google_dependency_ownership.py tests/test_setup_python_environment_skill.py tests/test_repository_test_checks.py
```

### 3. Prove client deferral, then cut skills over

1. Before changing generated skills, create the named-interface fixture only in
   the temporary packaged acceptance tree, then start a fresh Codex session
   through `mcp.json`. Capture the model-facing tool surface:
   initially it may contain the minimal Famulus server descriptor but no facade
   names. Activate a fixture skill naming one exact facade; Codex must resolve
   and successfully call only that facade without injecting the full catalog.
   Eager names or failed exact resolution are release blockers.
2. Add failing syncer/validator tests. Change the executable-interface preamble
   and `generated_setup_gate()` constants/wording so every executable call uses
   the exact unversioned facade name with `arguments` and optional `dry_run`.
   Reject a private process-interface use with `BlueprintError` because no
   public facade can represent it; continue rendering private instruction
   interfaces. For `setup_required`, obtain `ORIGINAL_CALLER`,
   `ORIGINAL_INTERFACE`, and `ORIGINAL_VERSION` from the facade result's
   `original` object. For `setup_busy`, obtain them from the manager recovery
   result. Generated instructions must never ask the model to retain hidden
   wrapper parameters. Cover both setup paths and private-process rejection with
   synthetic regressions.
3. Regenerate through the new
   `skill-maker._rtx.interface.sync-blueprints` facade with empty positionals and
   options. Accept changes only inside `BEGIN/END BLUEPRINT INTERFACES`; do not
   invoke the private sync script.
4. Repeat the fresh Codex acceptance test with a real regenerated skill.

Focused gate:

```bash
pytest -q skills/skill-maker/_rtx/tests/test_blueprint_tools.py tests/validate_skill_md_dispatch.py tests/test_famulus_mcp.py
```

### 4. Final release gate

Run the repository's supported focused Python checks and validator runner, then:
repeat the focused MCP suite in a clean environment containing exactly
`mcp==1.29.1` before running the scope commands below.

```bash
git status --short
git diff --numstat
git diff --no-index --numstat /dev/null mcp_facades.py
git diff --no-index --numstat /dev/null tests/test_mcp_facades.py
git diff --check
```

Also count this plan with the same `--no-index` form while it remains untracked.
For every planned file, verify `added + deleted` is within its table budget;
reject any unplanned path. (An isolated candidate worktree may instead stage
exact planned paths and use `git diff --cached --numstat`.) Inspect all 29
generated files and prove their diffs are marker-bounded. Record one startup digest timing sample on the release tree;
the check must remain a small fraction of a second on this checkout (target
under 200 ms, not a cross-machine benchmark guarantee).

Release is green only when all focused tests and validators pass, the two fresh
Codex deferral checks pass, the cache survives restart and refreshes after a
blueprint-only change, a facade matches `invoke`, every changed file meets its
gross LOC ceiling, and no unrelated worktree change is staged or modified.
