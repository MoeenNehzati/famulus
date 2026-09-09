# Setup Busy Owner Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `setup_busy` identify its process-backed owner and provide conservative stale recovery plus an explicit `--force` override.

**Architecture:** Each newly started managed setup receives a dispatcher-generated flow ID and a distinct advisory lease lock derived from that ID. The dispatcher holds the lease for the flow lifetime; the setup manager persists diagnostic owner metadata, reports an exact recovery route, and reuses its existing atomic cancellation logic after either proving the lease is free or receiving `--force`.

**Tech Stack:** Python 3.11, existing `officina.common.atomic_files.exclusive_file_lock`, strict JSON setup ledger, Officina blueprints, pytest/repository checks.

**Spec:** `docs/plans/2026-09-09-setup-busy-owner-recovery.md#global-constraints`

## Global Constraints

- Implement exactly four behaviors: process-backed flow ownership, informative `setup_busy`, conservative stale recovery, and `--force` recovery.
- Reuse `exclusive_file_lock(..., blocking=False)`, ledger compare-and-update, and the existing cancellation transition.
- Do not add TTLs, heartbeats, automatic expiry, PID-only liveness, a general process-management abstraction, or changes to `atomic_files.py`.
- Use a per-flow lease path so a forced clear is not blocked by a still-live old owner.
- Treat owner metadata as diagnostic; lock acquisition is the authoritative liveness test.
- Preserve all existing setup, teardown, verifier, receipt, and continuation semantics.
- Do not commit unless the user separately authorizes it.

---

### Task 1: Process-backed ownership for new active flows

**Files:**
- Modify: `mcp_server.py`
- Modify: `skills/setup-interface-manager/_rtx/_setup_state.py`
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_state.py`
- Test: `tests/test_famulus_mcp.py`

**Interfaces:**
- Produces: `FlowOwner(host: Literal["codex", "claude", "unknown"], pid: int, started_at: str)` persisted on `ActiveFlow`; `pid >= 1` and `started_at` is UTC RFC 3339.
- Produces: dispatcher-private lease registry keyed by `flow_id`.
- Produces: manager start calls that accept the dispatcher-generated `flow_id` and owner metadata instead of generating an unleased flow internally.
- Consumes: `exclusive_file_lock(path, allowed_root=..., blocking=False)`.

- [ ] **Step 1: Add failing state tests**

  Assert that the next ledger schema strictly encodes and decodes `owner`, migrates an older ownerless active flow as `owner=None`, rejects malformed owner fields, and keeps empty/receipt-only ledgers canonical.

- [ ] **Step 2: Run the focused state tests and require failure**

  Run: `./repo_checks.py --task tests:shared --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --jobs 8`

- [ ] **Step 3: Add the minimal state model**

  Add only:

  ```python
  @dataclass(frozen=True)
  class FlowOwner:
      host: str
      pid: int
      started_at: str

  class ActiveFlow:
      ...
      owner: FlowOwner | None = None
  ```

  Bump the ledger schema once, strictly validate these public diagnostic fields, and preserve migration of schema-v2 data.

- [ ] **Step 4: Add a failing MCP lifetime test**

  In a temporary plugin-data root, start an MCP dispatcher, begin a flow with a predetermined flow ID, assert that a second process cannot acquire `.<flow_id>.setup.lock`, terminate the dispatcher, and assert that the same nonblocking lock becomes acquirable.

- [ ] **Step 5: Add the private dispatcher lease registry**

  Generate the flow ID before `begin` or `teardown-all`, acquire its confined per-flow lock before the manager persists the flow, and pass the flow ID plus owner metadata into the manager start call. Retain the context manager in a process-local dictionary, release it after a validated terminal manager response, and release all retained leases at dispatcher shutdown. If the start call fails, release immediately.

- [ ] **Step 6: Run the two focused test files**

  Run: `./repo_checks.py --task tests:shared --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --jobs 8`

  Run: `./repo_checks.py --task tests:shared --selector tests/test_famulus_mcp.py --jobs 8`

---

### Task 2: Informative `setup_busy` response

**Files:**
- Modify: `mcp_server.py`
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Test: `tests/test_mcp_setup_preflight.py`

**Interfaces:**
- Consumes: `ActiveFlow.owner` from Task 1.
- Produces: a validated busy payload containing `flow_id`, setup identity, current step, owner metadata, message, and the exact `recover-busy` route.

- [ ] **Step 1: Add failing manager and MCP response tests**

  Require this bounded shape for an owned setup flow:

  ```json
  {
    "code": "setup_busy",
    "flow_id": "flow-1",
    "root_setup_interface": "connect-google.interface.setup",
    "current_step": "connect-google.interface.setup",
    "owner": {"host": "codex", "pid": 1234, "started_at": "..."},
    "message": "Setup connect-google.interface.setup is busy by codex process 1234.",
    "recovery": {
      "interface": "setup-interface-manager._rtx.interface.recover-busy",
      "arguments": {"positionals": ["flow-1"], "options": {}, "stdin": null}
    }
  }
  ```

  Ownerless migrated flows must say ownership is unknown and expose only forced recovery.

- [ ] **Step 2: Run the focused MCP preflight tests and require failure**

  Run: `./repo_checks.py --task tests:shared --selector tests/test_mcp_setup_preflight.py --jobs 8`

- [ ] **Step 3: Return and validate only the required fields**

  Extend manager `status()` to read the active flow once and return its root/current step/owner. Extend `_validate_manager_response()` and `_ordinary_preflight()` to validate, redact, and render the exact public structure above. Do not expose continuation arguments or request data.

- [ ] **Step 4: Run the focused MCP preflight tests**

  Run: `./repo_checks.py --task tests:shared --selector tests/test_mcp_setup_preflight.py --jobs 8`

---

### Task 3: Conservative `recover-busy`

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Modify: `skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml`
- Modify: `skills/setup-interface-manager/_rtx/blueprint.yaml`
- Modify: `skills/setup-interface-manager/blueprint.yaml`
- Regenerate: `skills/setup-interface-manager/SKILL.md`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`
- Test: `tests/test_setup_interface_manager_coverage.py`

**Interfaces:**
- Produces: `setup-interface-manager._rtx.interface.recover-busy FLOW_ID`.
- Consumes: the per-flow lease path and the existing cancellation transition.

- [ ] **Step 1: Add failing manager tests**

  Cover exactly: wrong flow ID fails without mutation; free lease proves staleness and clears the matching flow; held lease returns `setup.owner_active` without mutation; ownerless legacy flow returns `setup.owner_unknown`; a ledger change between check and clear returns the existing active-flow-changed error.

- [ ] **Step 2: Run the focused manager tests and require failure**

  Run: `./repo_checks.py --task tests:shared --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --jobs 8`

- [ ] **Step 3: Extract and reuse the existing cancellation transform**

  Move the atomic body currently nested in `recover(..., "cancel")` into one private helper. Keep verifier-first ordinary recovery unchanged. `recover_busy(flow_id, force=False)` must acquire the per-flow lease nonblocking before calling that helper; `AtomicLockUnavailable` returns `setup.owner_active`.

- [ ] **Step 4: Declare and regenerate the interface**

  Add `recover-busy` with one positional flow ID and optional Boolean `--force`; update only the setup-manager source/module/root surfaces and regenerate the `SKILL.md` interface block. Do not create another public interface.

- [ ] **Step 5: Run manager and coverage tests**

  Run: `./repo_checks.py --task tests:shared --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --jobs 8`

  Run: `./repo_checks.py --task tests:shared --selector tests/test_setup_interface_manager_coverage.py --jobs 8`

---

### Task 4: Explicit `--force` and user guidance

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Regenerate: `skills/setup-interface-manager/SKILL.md`
- Modify: `tests/test_mcp_setup_preflight.py`
- Modify: `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`
- Modify: `docs/setup.md`

**Interfaces:**
- Consumes: `recover-busy` and the shared cancellation helper from Task 3.
- Produces: `recover-busy FLOW_ID --force` with exact-flow atomic invalidation.

- [ ] **Step 1: Add failing force tests**

  Hold the old flow lease, invoke `recover-busy flow-1 --force`, and require the exact flow to clear. Then submit a late operation for `flow-1` and require flow mismatch. Begin `flow-2` and require that the still-held `flow-1` lease does not block it.

- [ ] **Step 2: Run the focused tests and require failure**

  Run: `./repo_checks.py --task tests:shared --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --jobs 8`

- [ ] **Step 3: Implement the force branch**

  Skip only the lease-liveness test. Retain exact flow-ID comparison, live-step validation, and atomic cancellation. Return `forced: true`; never terminate another process or delete its lock file.

- [ ] **Step 4: Update the workflow wording**

  Document: first invoke `recover-busy` without force; if it reports `setup.owner_active`, tell the user the owner appears live; invoke `--force` only after the user explicitly confirms interruption. Remove the old statement that every `setup_busy` has no recovery route.

- [ ] **Step 5: Run the complete focused slice**

  Run: `./repo_checks.py --task tests:shared --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --jobs 8`

  Run: `./repo_checks.py --task tests:shared --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --jobs 8`

  Run: `./repo_checks.py --task tests:shared --selector tests/test_mcp_setup_preflight.py --jobs 8`

  Run: `./repo_checks.py --task tests:shared --selector tests/test_famulus_mcp.py --jobs 8`

  Run: `./repo_checks.py --task tests:shared --selector tests/test_setup_interface_manager_coverage.py --jobs 8`

  Run: `git diff --check -- mcp_server.py skills/setup-interface-manager docs/setup.md tests/test_mcp_setup_preflight.py tests/test_famulus_mcp.py tests/test_setup_interface_manager_coverage.py`

## 3D LOC budgets

`3D = additions + updated lines + deletions`. These are implementation ceilings; stop and ask before exceeding any file or overall budget.

| File | Add | Update | Delete | 3D ceiling |
|---|---:|---:|---:|---:|
| `mcp_server.py` | 60 | 15 | 5 | 80 |
| `skills/setup-interface-manager/_rtx/_setup_state.py` | 45 | 15 | 5 | 65 |
| `skills/setup-interface-manager/_rtx/_setup_manager.py` | 65 | 20 | 10 | 95 |
| `skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml` | 30 | 5 | 0 | 35 |
| `skills/setup-interface-manager/_rtx/blueprint.yaml` | 2 | 1 | 0 | 3 |
| `skills/setup-interface-manager/blueprint.yaml` | 2 | 1 | 0 | 3 |
| `skills/setup-interface-manager/SKILL.md` | 12 | 6 | 2 | 20 |
| `skills/setup-interface-manager/_rtx/tests/test_setup_state.py` | 35 | 10 | 0 | 45 |
| `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py` | 60 | 15 | 5 | 80 |
| `tests/test_mcp_setup_preflight.py` | 45 | 10 | 5 | 60 |
| `tests/test_famulus_mcp.py` | 40 | 5 | 0 | 45 |
| `tests/test_setup_interface_manager_coverage.py` | 6 | 2 | 0 | 8 |
| `docs/setup.md` | 12 | 8 | 4 | 24 |
| **Overall** | **414** | **113** | **36** | **563** |

The plan document itself is not part of the implementation budget. No other runtime, test, blueprint, or documentation file is in scope.
