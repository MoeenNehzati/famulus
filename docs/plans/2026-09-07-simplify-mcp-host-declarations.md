# MCP Host Declaration Simplification Plan

**Goal:** Keep exactly one MCP declaration per host and one host-neutral shared contract, and remove unsupported development activation.

**Design:** `mcp-core.json` remains authoritative for shared dispatcher facts. Claude's thin adapter stays inline in `.claude-plugin/plugin.json`; Codex's thin adapter lives in `.mcp.json`, referenced by `.codex-plugin/plugin.json`. Delete redundant `mcp.json` and the checkout-only activation surface.

**Out of scope:** Renaming `mcp-core.json` and rewriting historical plans or lessons.

## Constraints

- Preserve the existing uncommitted launcher/runtime work; establish ownership or move it to an isolated worktree before implementation.
- Both adapters must launch `mcp_launcher.py` and pass normalized `FAMULUS_HOST` and `FAMULUS_PLUGIN_DATA` values.
- Installed-host behavior is the acceptance boundary.

### Task 1: Verify and consolidate

**Files:** `.mcp.json`, `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, `mcp.json`, `tests/test_famulus_mcp.py`

- [x] Confirm from the Agent Plugins contract that Codex supplies `PLUGIN_DATA`; discard the earlier disposable result because the changed declaration was not reloaded reliably.
- [x] Add failing tests that treat `mcp-core.json` as the shared authority and require exactly two host adapters with the correct launcher and normalized context.
- [x] Delete the unreferenced `mcp.json` declaration while retaining Codex's manifest-referenced `.mcp.json` and Claude's inline adapter.
- [x] Normalize Codex's `PLUGIN_DATA` to `FAMULUS_PLUGIN_DATA` in `.mcp.json`, matching Claude's JSON-owned normalization.
- [x] Run `./repo_checks.py --task tests:shared --selector tests/test_famulus_mcp.py --jobs 8`; require success.

### Task 2: Remove development activation

**Delete:** `.envrc`, `tools/dev-code`, `tools/dev-code.cmd`, `skills/dev-activation/`, `tests/test_officina_development_activation.py`

- [x] Remove the activation skill, wrappers, tests, validator allowlists, install-context registrations, and generated dependency metadata.
- [x] Remove dev-activation entries from `docs/skills.md` and `docs/contributors/README.md`; update any other active setup or contributor guidance found by the final search.
- [x] Run `./repo_checks.py --task tests:shared --selector tests/test_install_context_consumers.py --selector tests/test_platform_neutral_validator.py --jobs 8`; require success.

### Task 3: Verify the simplified system

- [x] Search with `rg -n --hidden --glob '!.git/**' 'mcp-core\.json|mcp\.json|dev-activation|development activation|dev mode' .`; migrate the active weak-agent replay plan to local plugin refresh, with remaining deleted-surface references confined to historical plans and lessons.
- [x] Install fresh isolated plugin copies for both hosts, inspect their projected declarations, and exercise each normalized child through the real MCP integration test.
- [x] In a fresh isolated Codex installation, require `codex mcp get famulus_dispatcher` to show the launcher plus both normalized environment variables; exercise `common.interface.famulus-paths-get` version `1` through the focused real-MCP suite and require the selected plugin-data path.

Commit only named paths after reviewing the cached diff; do not absorb unrelated worktree changes.

## Verification evidence

- Focused MCP suite: 40 passed, 1 skipped.
- Removal and documentation suite: 20 passed.
- Validators: 29 passed.
- Full test suite: 3,546 passed, 22 skipped, with one unrelated baseline failure in `tests/test_docstrings_validator.py::test_validate_staged_uses_test_production_and_base_profiles`; the same failure reproduces on `master` at the shared base commit.
