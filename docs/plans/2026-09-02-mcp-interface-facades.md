# MCP Interface Facades: First-Release Implementation Plan

> Plan only. Implement with `superpowers:executing-plans` or
> `superpowers:subagent-driven-development`; do not broaden the release.

## Outcome

Every eligible public blueprint export is its own Famulus MCP tool, so a host can
grant, deny, or record permission per interface. A future eligible interface
needs only its normal blueprint declaration and generated skill block—no
MCP-specific Python registration.

There are no permanent tools. `invoke` becomes a local function that is
registered only under an explicit environment opt-in; `reload` is not a tool at
all, only the internal rebuild the server runs when its discovery cache is
stale. Refreshing the catalog after a blueprint change means restarting the
server.

## Fixed contract

### Tools

- The published tool surface is exactly the facade catalog. Nothing else is
  registered by default.
- `invoke(caller, interface, version, arguments, dry_run)` keeps its current
  signature and behavior as a module-level function, registered as a tool only
  when `FAMULUS_MCP_INVOKE` is set in the environment. It is the debugging
  escape hatch, never part of the normal surface. Do not delete it: a failed
  catalog build must still leave a reachable server.
- Generated tool `I`: `I(arguments, dry_run=False)`. Its closure binds the
  interface ID; that hidden binding must not appear in its input schema.
- A facade carries only its generated name, the generic `arguments` and
  `dry_run` schema, and empty/default metadata. It copies no blueprint prose,
  examples, annotations, or interface-specific schema.
- The facade delegates to the same private invocation engine as `invoke` with
  owner-as-caller, `version=None`, and `host_caller=False`. After authorization,
  all setup/preflight/result paths use `authorized.export.version`. Facades—
  including dry-run and setup-manager facades—always take the direct owner-self
  authorization path; they must not enter the host-caller `resolve_dispatch`
  shortcut.

### Naming

- One `facade_name(interface_id) -> str` and its inverse `interface_id(name)` in
  `src/officina/common/facade_names.py` are the single authority. `mcp_server.py`,
  `skills/skill-maker/_rtx/_blueprint_syncer.py`, and the repository validator all
  import from there. None may reimplement or approximate the transform.
- Transform: drop the `.interface.` marker, then replace every `.` with `--`.
  Hyphens inside segments are preserved. The dot is the only character illegal in
  an MCP tool name; `-` is legal and already carries word separation.

  ```text
  bib-audit._rtx.interface.scripts-bib-similarity
    -> bib-audit--_rtx--scripts-bib-similarity
  ```

- Decoding is a plain `name.split("--")`; the last element is the local name and
  the rest rejoin with `.` as the module path. This works only because no segment
  may contain `--`, which the grammar below enforces. Verified over the current
  206 exports: 206 unique names, zero round-trip failures, maximum length 64.
  Property-test the round trip over every export.
- Do not drop the `._rtx` segment. It collides
  (`connect-google._rtx.interface.connect-services` against
  `connect-google.interface.connect-services`). It costs 5 characters on 111 of
  the 206 IDs; that cost is accepted.

### ID segment grammar

- Every segment of every node, module, source, and interface ID must match
  `_?[a-z0-9]+(-[a-z0-9]+)*`. This bans a doubled `--`, a non-leading `_`, a
  leading or trailing `-`, and uppercase.
- The `--` ban is load-bearing, not hygiene: `--` is the encoding separator, so a
  segment containing `--` would make decoding ambiguous.
- Enforce it in the repository validator, not only in `facade_names`. A blueprint
  that violates it must fail validation, so a future ID can never silently break
  the encoding.
- Verified across all 497 node, module, source, and interface IDs currently in
  the graph (330 distinct segments): zero violations. The rule is free to adopt.

### Name length

- There is no truncation path. An ID whose encoded name exceeds the budget is an
  authoring error: the validator fails and names the offending interface ID.
  Never silently hash, truncate, or mangle a name — a committed `SKILL.md` and a
  recorded permission rule both depend on the name being exactly derivable.
- `BUDGET` is a fixed constant in `facade_names.py`, never derived at runtime.
  Generated `SKILL.md` files are committed, so a budget that varied with the
  observed host prefix would rewrite every tool name on an install-mode change,
  invalidating every recorded permission rule and every generated skill block.
- `UNRESOLVED, blocks step 1.` The allowed character class `[A-Za-z0-9_-]` is
  settled. The maximum length is either 64 or 128 and this plan does not know
  which. Claude Code bundle 2.1.258 contains both `^[a-zA-Z0-9_-]{1,64}$` and
  `^[a-zA-Z0-9_-]{1,128}$`, the latter applied to a server name and tool name
  together. Do not take 64 on the author's earlier word; it was asserted from
  memory, and the bundled `claude-api` skill documents no tool-name pattern.
- Step 0 resolves it and sets `BUDGET`:
  - **Cap 128** -> `BUDGET = 99`. Zero IDs are over. Step 1a below is skipped.
  - **Cap 64** -> the supported install mode is the direct one, prefix
    `mcp__famulus__`, so `BUDGET = 50`. Four IDs are over and are renamed in step
    1a.
- Measured rename pressure by budget, so a later prefix change can be priced:
  budget 50 -> 4 over; 45 -> 11; 40 -> 23; 35 -> 60.
- Do not attempt to support the plugin prefix `mcp__plugin_famulus_famulus__`
  (29 characters) at cap 64. That is `BUDGET = 35`, where 60 IDs are over and 40
  remain over even after stripping every `scripts-` prefix and every module word
  repeated in a local name. The survivors are ordinary names such as
  `email-client--_rtx--accounts-set-password`; reaching 35 means damaging them.
  If the cap turns out to be 64, the plugin install mode is out of scope for this
  release and the plan must say so in `mcp-core.json`.

### Eligibility

- Eligible means a public graph export owned beneath a module root configured by
  `officina.toml`, whose selected source has a Python gateway and process
  binding. Repository fixtures and other non-module-root blueprints are never
  exposed.
- A name collision after transform is a catalog build failure, never a silent
  omission. So is an otherwise eligible export whose name cannot be made legal.
  Report the offending interface IDs.

### Cache

- Location `${FAMULUS_PLUGIN_DATA}/mcp-interface-facades-v1.json`. With no plugin
  provenance, rebuild in memory and write nothing to the checkout.
- Contents: only `schema_version: 1`, `source_digest`, and a sorted, unique array
  of `{interface_id, tool_name}` pairs. It is discovery data, never route or
  authorization authority. A stale-missing facade costs a restart; a stale-extra
  facade fails cleanly at dispatch. Nothing can be wrongly authorized by a bad
  cache.
- Staleness is SHA-256 over the canonical inventory's sorted repository-relative
  blueprint paths and exact bytes beneath the currently configured module roots,
  length-delimited. A module-root selection change changes that path set without
  hashing configuration bytes. Only eligible blueprint paths and bytes affect the
  digest; fixture blueprints, Python, skills, tests, docs, mtimes, and Git state
  do not.
- Startup registers a strict matching cache. Missing, invalid, or stale cache
  runs the internal rebuild before stdio begins, then writes the cache with a
  temp file plus `os.replace`. A write failure is a warning; the in-memory
  catalog still publishes.
- No `notifications/tools/list_changed`, no compare-and-swap, no race
  convergence, no rollback, and no change to the pinned `mcp` version range.
  Measured: full graph load 2.2 s, digest over the 82 blueprint files 1.5 ms.
- A rebuild failure leaves a runnable server with a stable diagnostic and, when
  `FAMULUS_MCP_INVOKE` is set, `invoke`.

### Generated skills

- Skill-generated executable-interface and managed-setup instructions name exact
  facade tool names without `@version`; rendered Arguments JSON becomes the
  facade's `arguments`. No caller/interface/version wrapper or catalog is
  generated, and no generated instruction may reference `invoke`.
- The setup refusal payloads in `mcp_server.py` (`_setup_managed`,
  `_ordinary_preflight`) hand the model a follow-up route. Those routes must name
  facade tool names, and the four `setup-interface-manager` interfaces must be in
  the catalog, or managed setup breaks once `invoke` is unregistered.

## Gross LOC budget

Gross LOC means additions plus deletions in the final diff. Every row is a hard
per-file ceiling. Do not add an unlisted split file; revise this plan first if a
listed budget is insufficient.

| File | Budget | Purpose |
|---|---:|---:|
| `docs/plans/2026-09-02-mcp-interface-facades.md` | 450 | This implementation plan. |
| `src/officina/blueprints/inventory.py` | 45 | Public iterator over canonical paths within selected module roots. |
| `tests/test_blueprint_inventory.py` | 70 | Prove the iterator preserves canonical exclusions/order/no-follow behavior. |
| `src/officina/common/facade_names.py` (new) | 60 | `facade_name`, its inverse, `BUDGET`, the segment grammar, and the collision check. |
| `tests/test_facade_names.py` (new) | 110 | Transform, round-trip property, grammar, collision, and legality tests. |
| Repository ID-grammar validator | 55 | Enforce `_?[a-z0-9]+(-[a-z0-9]+)*` on every ID segment and `BUDGET` on every encoded name. |
| Over-budget ID renames (step 1a) | 120 | Four interface renames across blueprints, callers, and generated blocks; skipped if the cap is 128. |
| `mcp_facades.py` (new) | 90 | Digest, strict cache read/write, graph projection, catalog model. |
| `tests/test_mcp_facades.py` (new) | 130 | Digest, cache, and projection unit tests. |
| `mcp_server.py` | 170 | Shared invocation engine, facade factory, startup registration, opt-in `invoke`. |
| `tests/test_famulus_mcp.py` | 220 | Facade schema, equivalence, startup cache hit/miss/stale, opt-in gate. |
| `mcp-core.json` | 40 | Facade and cache contract; no host-policy essay. |
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

Do not change `mcp.json`, `.claude-plugin/plugin.json`, `pyproject.toml`, or
`requirements-ci.txt`. Preserve unrelated worktree changes, including the four
pre-existing deleted plans.

## Implementation sequence

### 0. Resolve the host name cap

Established by reading Claude Code bundle 2.1.258 on this machine (2026-09-02):

- The host builds `mcp__${sanitize(serverName)}__${toolName}`, where `sanitize`
  is `s.replace(/[^a-zA-Z0-9_-]/g, "_")`. `Verified`: the server name is
  sanitized; the tool name is interpolated verbatim.
- `Likely`: no truncation or sanitization is applied to the tool segment
  anywhere in the listing path. A 215 MB minified bundle cannot prove absence,
  but the constructor is explicit and no candidate sanitizer was found.
- Consequence: raw dotted interface IDs would reach the Messages API violating
  the allowed character class. The transform is mandatory.
- The observed prefix in a plugin install is `mcp__plugin_famulus_famulus__`
  (29 characters); a direct install gives `mcp__famulus__` (14).

The one open question is the maximum length, 64 or 128. Register a throwaway
facade whose full prefixed name is about 100 characters and start the server in
the host actually used. If it appears in the tool list and can be called, the cap
is 128: record that, delete the truncation path from the Name length section, and
carry no budget constant. If it is rejected or absent, the cap is 64: record that
and implement truncation against the fixed budget of 35. Remove the throwaway
facade before continuing.

### 1. Naming, discovery, and cache

1. Add failing `facade_names` tests first: the transform on representative IDs,
   the encode/decode round trip as a property over every export, rejection of a
   collision, legality of every produced name against the character class and the
   cap resolved in step 0, and — only if that cap is 64 — truncation at the
   budget boundary, hash-suffix determinism, and the decoder refusing a truncated
   name rather than returning a wrong ID. Then implement.
2. Add the segment-grammar check to the repository validator with a failing test
   first: a blueprint whose ID segment violates
   `_?[a-z0-9]+(-[a-z0-9]+)*` must fail validation. Confirm the existing 497 IDs
   still pass.
3. Add failing inventory tests, then expose a public iterator which reuses
   `_ignored_paths`, `_canonical_module_roots`, and `_blueprint_paths`; do not
   create a second filesystem walk. It accepts the configuration's selected
   module roots and returns only their accepted paths in canonical order without
   parsing YAML. Test that fixture changes are ignored and that changing the
   selected roots changes the path set and resulting cache decision.
4. Add failing `mcp_facades` tests for deterministic path+byte hashing;
   blueprint add/remove/rename/content staleness; Python and skill changes
   ignored; strict cache rejection of a missing, malformed, or wrong-schema file;
   and confined temp-file publication.
5. Implement those primitives with `read_regular_file_bytes` and a temp file
   plus `os.replace`.
6. Project the catalog from the existing validated graph machinery using graph
   exports, source gateway data, and `gateway_language_name`, then apply
   `facade_name` and the collision check. Restrict owners to configured module
   roots and test that `tests/fixtures` exports are absent.

Focused gate:

```bash
pytest -q tests/test_facade_names.py tests/test_blueprint_inventory.py tests/test_mcp_facades.py
```

### 1a. Bring over-budget IDs under the cap

Runs after step 1, which supplies `facade_names` and the validator that
identifies the violations. Skip entirely if step 0 resolved the cap to 128.

At `BUDGET = 50` exactly four exports are over. All four carry removable
redundancy — a `scripts-` prefix that says nothing, and a module name repeated
verbatim inside its own local name:

```text
math-dependency-graph._rtx.interface.scripts-build-math-dependency-graph   (64)
math-dependency-graph._rtx.interface.scripts-extract-mathjax-macros        (59)
email-client._rtx.interface.accounts-use-google-credential-file            (55)
math-dependency-graph._rtx.interface.scripts-read-tex-labels               (52)
```

Rename them with `relocate-nodes` if it covers interface renames, otherwise by
hand. Each rename must update the owning `blueprint.yaml`, every caller, the
generated `SKILL.md` block, and any ledger or milestone reference. Verify with a
repository-wide grep for the old ID returning nothing outside history. Do not
invent abbreviations: drop redundant words rather than shorten real ones.

After the renames, assert in the validator test that every current export encodes
within `BUDGET`, so the release cannot regress.

### 2. Facades, opt-in invoke, and startup

1. First write MCP tests for the minimal facade schema, current-version
   invocation equivalence against `invoke`, dry-run, managed setup, setup-manager
   routing, direct owner-self facade behavior, cache hit/miss/stale startup, the
   `FAMULUS_MCP_INVOKE` gate in both states, and a rebuild failure that still
   leaves a runnable server.
2. Extract one private authorized invocation engine from `invoke`; preserve all
   existing return payloads and special branches. `invoke` supplies its explicit
   version. A facade authorizes with `None`, then rebinds every later use to the
   selected export version.
3. Add `build_server`, minimal closure-based facade creation, and startup
   registration. Register `invoke` only under the environment opt-in.
4. Rewrite the follow-up routes in `_setup_managed` and `_ordinary_preflight` to
   name facade tool names, and assert the four manager interfaces are present in
   any successfully built catalog.
5. Update `mcp-core.json` to describe the facade and cache contract. Its
   `core_packages` bound is unchanged.

Focused gate:

```bash
pytest -q tests/test_mcp_facades.py tests/test_famulus_mcp.py tests/test_mcp_setup_preflight.py tests/test_setup_interface_manager_integration.py
```

### 3. Cut skills over to facade names

1. Add failing syncer and validator tests. Change the executable-interface
   preamble and `generated_setup_gate()` constants and wording so every
   executable call names the exact facade tool with `arguments` and optional
   `dry_run`. Reject a private process-interface use with `BlueprintError`
   because no public facade can represent it; continue rendering private
   instruction interfaces. For `setup_required`, obtain `ORIGINAL_CALLER`,
   `ORIGINAL_INTERFACE`, and `ORIGINAL_VERSION` from the facade result's
   `original` object. For `setup_busy`, obtain them from the manager recovery
   result. Generated instructions must never ask the model to retain hidden
   wrapper parameters or mention `invoke`.
2. Add the drift test: for every eligible export, the tool name rendered into
   `SKILL.md` equals the name the server registers. This is the test that keeps
   the two call sites of `facade_name` from diverging.
3. Regenerate through the `skill-maker._rtx.interface.sync-blueprints` facade
   with empty positionals and options. Accept changes only inside
   `BEGIN/END BLUEPRINT INTERFACES`; do not invoke the private sync script.

Focused gate:

```bash
pytest -q skills/skill-maker/_rtx/tests/test_blueprint_tools.py tests/validate_skill_md_dispatch.py tests/test_famulus_mcp.py
```

### 4. Permission acceptance

In the host actually used, with `FAMULUS_MCP_INVOKE` unset:

1. Grant exactly one facade tool. Confirm a skill that names it runs without a
   permission prompt.
2. Confirm a second, ungranted facade still prompts.
3. Confirm no `invoke` tool is offered, and that setting `FAMULUS_MCP_INVOKE`
   makes it appear again.
4. Run one managed-setup flow end to end through facades only.

This is the release's primary acceptance test. A facade catalog that cannot be
permissioned per interface is not a release.

### 5. Final release gate

Run the repository's supported focused Python checks and validator runner, then:

```bash
git status --short
git diff --numstat
git diff --no-index --numstat /dev/null mcp_facades.py
git diff --no-index --numstat /dev/null tests/test_mcp_facades.py
git diff --no-index --numstat /dev/null src/officina/common/facade_names.py
git diff --no-index --numstat /dev/null tests/test_facade_names.py
git diff --check
```

This plan is tracked; count it with the ordinary `git diff --numstat` above.
For every planned file, verify `added + deleted` is within its table budget;
reject any unplanned path. (An isolated candidate worktree may instead stage
exact planned paths and use `git diff --cached --numstat`.) Inspect all 29
generated files and prove their diffs are marker-bounded. Record one startup
timing sample on a warm cache; it must stay a small fraction of a second on this
checkout, against the measured 2.2 s cold rebuild.

Release is green only when all focused tests and validators pass, the step 4
permission checks pass, the cache survives restart and refreshes after a
blueprint-only change, a facade matches `invoke`, every changed file meets its
gross LOC ceiling, and no unrelated worktree change is staged or modified.
