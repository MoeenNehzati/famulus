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

- [ ] Install a disposable Codex plugin and prove `${PLUGIN_ROOT}` and `${PLUGIN_DATA}` expand inside `.mcp.json`; stop if they do not.
- [ ] Add failing tests that treat `mcp-core.json` as the shared authority and require exactly two host adapters with the correct launcher and normalized context.
- [ ] Move Codex's host-specific fields into `.mcp.json`, retain Claude's inline fields, and delete `mcp.json`.
- [ ] Run `./repo_checks.py --task tests:shared --selector tests/test_famulus_mcp.py --jobs 8`; require success.

### Task 2: Remove development activation

**Delete:** `.envrc`, `tools/dev-code`, `tools/dev-code.cmd`, `skills/dev-activation/`, `tests/test_officina_development_activation.py`

- [ ] Remove the activation skill, wrappers, tests, validator allowlists, install-context registrations, and generated dependency metadata.
- [ ] Remove dev-activation entries from `docs/skills.md` and `docs/contributors/README.md`; update any other active setup or contributor guidance found by the final search.
- [ ] Run `./repo_checks.py --task tests:shared --selector tests/test_install_context_consumers.py --selector tests/test_platform_neutral_validator.py --jobs 8`; require success.

### Task 3: Verify the simplified system

- [ ] Search with `rg -n --hidden --glob '!.git/**' 'mcp-core\.json|mcp\.json|dev-activation|development activation|dev mode' .`; classify historical matches and require no active references to deleted surfaces.
- [ ] Install fresh plugin copies for both hosts and verify each child receives its host and plugin-data values.
- [ ] In a fresh Codex session, require `codex mcp get famulus_dispatcher` to show the launcher plus both environment variables, then invoke `common.interface.famulus-paths-get` version `1` with positional `setup-status` and require an absolute plugin-data path.

Commit only named paths after reviewing the cached diff; do not absorb unrelated worktree changes.
