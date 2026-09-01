# Famulus plugin-data persistence plan

**Goal:** Persist Famulus-owned state below each client-managed `plugin_data` directory and expose named paths to skills through Dispatcher.

**Task base:** `17aba16e3680f65bb80e16697dda31e9307d6d11`

## Logic and contract

Each client launches its own Famulus MCP subprocess with its own environment. The client-specific manifest maps that client's persistent directory to `FAMULUS_PLUGIN_DATA`; it is not a shell-global value shared by active plugins.

```text
plugin_data/
├── milestones/       # logging_path
└── setup/status.json
```

`FamulusPaths` gains optional `assistant_host`, `plugin_data`, `logging_path`, and `setup_status` fields. Existing non-plugin paths do not move. All four plugin-context fields resolve together or remain `None`.

```python
@classmethod
def get(
    cls,
    name: Literal["plugin-data", "logging-path", "setup-status"],
    *,
    platform: str,
    home: Path,
    environ: Mapping[str, str],
) -> Path: ...
```

`get()` constructs one instance through `resolve_famulus_paths()` and selects from one explicit mapping; it never accepts arbitrary attribute names. Resolution and `get()` are read-only.

The public executable interface is:

```text
common.interface.famulus-paths-get@1 NAME
```

`NAME` is exactly `plugin-data`, `logging-path`, or `setup-status`; stdout is one absolute path and stdin is unused.

Host provenance is explicit, not inferred. Claude's native declaration supplies `claude`; the root Agent Plugins declaration is intentionally the Codex route and supplies `codex`. This supports the two clients in scope, but the root declaration must not be described as client-neutral host detection. A future third Agent Plugins client requires its own honest host value or an optional/extended host contract.

At MCP startup, plugin context causes Famulus to:

1. resolve paths once;
2. override the subprocess-local `ASSISTANT_LOGS` with `logging_path`;
3. atomically write mode-`0600` `setup/status.json` below `plugin_data`;
4. then start FastMCP.

No plugin context preserves current behavior and writes nothing. Milestone runtime code remains unchanged because Dispatcher subprocesses inherit the MCP environment.

## Owned files and 3D budget

`D/N/M` means pure deletions, pure additions, and modified lines. For `git diff --numstat` additions `A` and removals `R`: `M=min(A,R)`, `N=A-M`, `D=R-M`. Caps include the pre-existing dirty/untracked hunks because measurement is against the pinned task base. Every file is owned once.

| Task | File | D | N | M |
|---|---|---:|---:|---:|
| P | `docs/plans/2026-08-31-plugin-data-persistence.md` | 0 | 230 | 0 |
| 1 | `plugin.json` | 0 | 45 | 0 |
| 1 | `mcp.json` | 0 | 20 | 0 |
| 1 | `.claude-plugin/plugin.json` | 0 | 8 | 2 |
| 2 | `src/officina/common/__init__.py` | 0 | 6 | 0 |
| 2 | `src/officina/common/famulus_paths/__init__.py` | 2 | 140 | 8 |
| 2 | `src/officina/common/famulus_paths/_get_interface.py` | 0 | 90 | 0 |
| 2 | `src/officina/common/blueprints/famulus-paths.yaml` | 10 | 100 | 30 |
| 2 | `src/officina/common/blueprints/famulus-paths-get.yaml` | 0 | 180 | 0 |
| 2 | `src/officina/common/blueprint.yaml` | 0 | 18 | 2 |
| 2 | `tests/test_officina_famulus_paths.py` | 5 | 150 | 15 |
| 3 | `mcp_server.py` | 2 | 85 | 8 |
| 3 | `tests/test_famulus_mcp.py` | 20 | 220 | 65 |
| 3 | `skills/milestone-logging/setup.md` | 0 | 25 | 0 |
| 3 | `skills/milestone-logging/blueprint.yaml` | 1 | 18 | 3 |
| 3 | `skills/milestone-logging/blueprints/setup.yaml` | 0 | 120 | 0 |
| 3 | `skills/milestone-logging/_rtx/blueprints/rtx-milestone-writer.yaml` | 4 | 8 | 12 |
| 3 | `skills/milestone-logging/_rtx/blueprints/rtx-agent-timeline.yaml` | 4 | 8 | 12 |
| 3 | `skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py` | 3 | 45 | 10 |
| 3 | `docs/security-and-privacy.md` | 1 | 25 | 5 |
| 4 | `scripts/sync-release-version.py` | 0 | 3 | 2 |
| 4 | `tests/test_sync_release_version.py` | 4 | 25 | 12 |
| 4 | `tests/test_repository_test_checks.py` | 4 | 20 | 10 |
| 4 | `tests/test_officina_development_activation.py` | 8 | 45 | 25 |
| **Cap** | **24 files** | **68** | **1634** | **221** |

Equivalent cap: at most 1,855 added and 289 removed lines, for net growth of 1,566. Excluding the plan's 230-line allowance, implementation is capped at 1,404 pure new lines and net growth of 1,336. The three expanded Python N caps reserve only structured docstrings required by staged validation. The two added test rows cover compatibility failures first exposed by complete precommit; production scope does not expand. A binary diff, an unlisted file, duplicate ownership, or any exceeded per-file dimension stops implementation until this plan is revised and re-audited.

Protected zero-diff files:

- `.codex-plugin/plugin.json`, `.mcp.json`, `.claude-plugin/marketplace.json`, `mcp-core.json`, and `officina.toml`;
- `skills/milestone-logging/SKILL.md`, `skills/milestone-logging/blueprints/gateway.yaml`, and `skills/milestone-logging/_rtx/blueprint.yaml`;
- milestone Python runtimes and interfaces;
- `skills/dev-activation/_rtx/_development_activation.py`;
- `references/blueprint-schema/runtime_dependencies.json`, certificates, standards, installers, launchers, and unrelated feature-state implementations.

Dirty overlap rule: preserve unrelated hunks. In `src/officina/common/blueprint.yaml` and the three existing milestone setup files, own only the source/export/use changes named below. Explicitly remove the currently dirty direct `milestone-logging` allowance on `common.interface.famulus-paths` if it is superseded by `famulus-paths-get`; do not silently retain unused authority.

## Exact changes

### Task 1: Client declarations

- Create root `plugin.json` and `mcp.json` using Agent Plugins v1.
- In root `mcp.json`, map `${PLUGIN_DATA}` to `FAMULUS_PLUGIN_DATA` and set `FAMULUS_HOST=codex` for the scoped Codex route.
- In `.claude-plugin/plugin.json`, map `${CLAUDE_PLUGIN_DATA}` to `FAMULUS_PLUGIN_DATA` and set `FAMULUS_HOST=claude`.
- Keep legacy `.codex-plugin/plugin.json` and `.mcp.json` byte-for-byte unchanged.

### Task 2: `FamulusPaths` and executable getter

- Test first: both hosts; all three names; unknown name; absent, partial, empty, relative, and unknown-host context; unchanged legacy roots; and no filesystem mutation by resolve/get.
- Resolve only normalized `FAMULUS_HOST` and `FAMULUS_PLUGIN_DATA`. Derive `logging_path` and `setup_status` exactly as shown above.
- Implement the requested `FamulusPaths.get()` classmethod with a single canonical name mapping shared by the argparse choices.
- Add `_get_interface.py` with `entry: Interface` using `PythonArgvMachineInterface`.
- Add `common.source.famulus-paths-get`, interface `common.source.famulus-paths-get.interface.get`, export `common.interface.famulus-paths-get`, and dependency/use of `common.source.famulus-paths@1` / `common.interface.famulus-paths@1`.
- Give the process binding exactly one finite positional, no stdin, and the `_get_interface.py` gateway. Register/export it with `allow_all_modules: true` in the common aggregate and inventory it in `src/officina/common/__init__.py`.

### Task 3: Persist setup and milestone state

- Test first: invalid context fails before partial output; exact status bytes and mode; both parent directories; inherited `ASSISTANT_LOGS` canary untouched; real stdio MCP writes status and milestone below the selected root.
- In `mcp_server.py`, resolve once before FastMCP, set subprocess-local `ASSISTANT_LOGS`, and atomically write deterministic status with schema version, status, and declared host. Do nothing outside plugin context.
- Change milestone setup to call `common.interface.famulus-paths-get@1` only for the paths it consumes (`logging-path` and `setup-status`), then validate readiness. Do not call `plugin-data` redundantly.
- Declare the getter use only in the setup source. Update writer/timeline contract text only for the projected-log-root semantics; they do not call the getter and must not gain false use edges. Keep direct-run `ASSISTANT_LOGS` compatibility.
- Document the two persisted locations and no migration of old `~/.assistant-logs` or unrelated state.

### Task 4: Validation

- Add root `plugin.json` to release-version synchronization and its missing/malformed/rollback tests using a strict RED/GREEN cycle.
- Update the real precommit-hook fixture to include the now-required root manifest.
- Update development-activation assertions to distinguish legacy path fields from absent plugin-context metadata; production activation behavior remains unchanged.

Focused tests and graph checks:

```bash
./repo_checks.py --task tests:shared \
  --selector tests/test_officina_famulus_paths.py \
  --selector tests/test_famulus_mcp.py \
  --selector tests/test_sync_release_version.py \
  --selector skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py \
  --jobs 1 --repository-view working -v

dispatcher --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints@1 --check
dispatcher --caller-skill milestone-logging --dry-run common.interface.famulus-paths-get@1 plugin-data
./repo_checks.py --suite validators --jobs 1 --repository-view working
git diff --check
```

Then stage the complete candidate, enforce every budget row against the task base, assert all protected paths are zero-diff, and run:

```bash
./repo_checks.py --suite precommit --jobs 8 --repository-view staged
```

Real-host smoke uses unique canaries and recorded absolute paths:

- Claude: strict-validate; start with `--plugin-dir <checkout>`; invoke all three exact getter names through Famulus; record a tagged milestone; restart identically; compare paths/status bytes and verify the tagged JSONL remains.
- Codex: use the already authenticated live Codex home only after verifying that marketplace `nullkit` resolves to `<checkout>` and `famulus@nullkit` is enabled. Do not change plugin configuration. Retain that same home for both restart legs, start Codex, and repeat the same calls/restart check. `codex -C` alone is not plugin selection; abort this smoke if either precondition is false.
- Assert the Claude and Codex roots differ, both are absolute, both persist across restart, and both inherited canary directories remain unchanged.

## Non-goals

- No migration or merging of pre-existing logs.
- No changes to credentials, schedules, email, lists, wakeup, launchers, or direct milestone Python runtime.
- No generic client-detection heuristic and no arbitrary path-name lookup.
