# Dispatcher Contracts (v5 Rebase) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix feedback items 5, 12 (shared with the downstream-workflows rebase), and 21 from `docs/plans/osx_feedback_fix/README.md` — a generic duplicate-subcommand validator and structured dispatcher failures — using v5's real `module_id` vocabulary and multi-blueprint-per-module layout, superseding `docs/plans/osx_feedback_fix/02-dispatcher-contracts.md`.

**Architecture:** Add a reusable blueprint validator that rejects duplicated fixed subcommand tokens across a module's interfaces (the cloud-files instance of this bug is already fixed by the v5 migration — this task only adds the generic guard so it can't regress or recur elsewhere). Separately, replace the dispatcher's single flat `InvocationError` with a small structured-failure hierarchy carrying phase, module IDs, and safe context, surfaced identically through both the CLI's human output and a new `--error-format json` machine path.

**Tech Stack:** Python 3.11+, pytest, JSON Schema (blueprint schema v5), the repo's blueprint validation tooling.

---

## Task 1: Generic duplicate-fixed-subcommand validator (feedback item 5)

**Files:**
- Create: `validators/duplicate_subcommand_tokens.py` (or the equivalent location used by other validators under `validators/` — confirm the real convention by reading an existing validator, e.g. `validators/cross_platform.py`, before creating this file)
- Test: `tests/test_duplicate_subcommand_tokens.py`

**Context check before starting:** `skills/cloud-files/_rtx/blueprints/rtx-ensure-oauth.yaml` and `rtx-write-config.yaml` already have correct, non-duplicated `args_prefix`/`min_positionals`/`max_positionals` contracts (fixed incidentally by the "Implement nested module v5 cutover" commit `a32a6bb`, not by this plan). Do not attempt to re-fix cloud-files — write the validator, confirm it passes clean on cloud-files as-is, and add a synthetic fixture proving it *would* catch the bug if reintroduced.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from validators.duplicate_subcommand_tokens import find_duplicate_fixed_subcommands


def test_no_duplicates_in_clean_module():
    interfaces = {
        "a": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
        "b": {"args_prefix": ["write-config"], "min_positionals": 0, "max_positionals": 0},
    }
    assert find_duplicate_fixed_subcommands(interfaces) == []


def test_detects_duplicate_fixed_subcommand_token():
    interfaces = {
        "a": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
        "b": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
    }
    duplicates = find_duplicate_fixed_subcommands(interfaces)
    assert duplicates == [("ensure-oauth", ["a", "b"])]


def test_real_cloud_files_module_has_no_duplicates():
    import yaml
    from pathlib import Path
    payload = yaml.safe_load(Path("skills/cloud-files/_rtx/blueprints/rtx-ensure-oauth.yaml").read_text())
    # Adjust extraction to however interfaces are actually nested in this file.
    assert find_duplicate_fixed_subcommands(payload.get("interfaces", {})) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_duplicate_subcommand_tokens.py -v`
Expected: FAIL — `validators.duplicate_subcommand_tokens` doesn't exist yet.

- [ ] **Step 3: Implement the validator**

```python
"""Detect interfaces within one module whose fixed args_prefix collides,
so the dispatcher cannot route a caller's tokens unambiguously."""
from __future__ import annotations


def find_duplicate_fixed_subcommands(interfaces: dict) -> list[tuple[str, list[str]]]:
    by_token: dict[tuple, list[str]] = {}
    for interface_id, contract in interfaces.items():
        prefix = tuple(contract.get("args_prefix", []))
        if not prefix:
            continue
        by_token.setdefault(prefix, []).append(interface_id)
    return [(" ".join(prefix), ids) for prefix, ids in by_token.items() if len(ids) > 1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q tests/test_duplicate_subcommand_tokens.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire into the repo's blueprint validation runner**

Read `validators/runner.py`'s discovery mechanism (how existing validators like `cross_platform.py` register) and add this validator the same way, scoped per-module (iterate each module's merged interface set, not cross-module — different modules are allowed to reuse the same subcommand token).

- [ ] **Step 6: Run the full validator suite**

Run: `python3 -m validators.runner` (confirm exact invocation from repo docs first) across all skills.
Expected: no new failures; cloud-files passes clean (already fixed by v5 migration).

- [ ] **Step 7: Commit**

```bash
git add validators/duplicate_subcommand_tokens.py tests/test_duplicate_subcommand_tokens.py
git commit -m "feat(validators): reject duplicated fixed dispatcher subcommand tokens within a module"
```

---

## Task 2: Structured dispatcher failures (feedback items 12, 21)

**Files:**
- Create: `src/officina/dispatcher/errors.py`
- Modify: `src/officina/dispatcher/core.py` (raise-site inventory — currently ~20 `raise InvocationError(...)` sites between line 456 and line 1109; re-derive exact line numbers at implementation time since Task 1's changes don't touch this file but time will have passed)
- Modify: `src/officina/dispatcher/cli.py` (add `--error-format json`, replace the flat `except InvocationError as exc: print(f"error: {exc}")` at line 79-80)
- Test: `tests/test_dispatcher_errors.py`, extend `tests/test_dispatcher_cli.py` or equivalent

**Design decision from the audit (apply this, don't re-derive it):** Use v5's real `module_id` vocabulary (`caller_module_id`/`target_module_id`, already the primary fields on `InvocationError` per `core.py:103-104`) as the canonical field names in structured payloads — not the legacy `caller_skill`/`target` naming the original 2026-07-24 plan used, which only exists today as a documented "Temporary compatibility for v4 consumers" property (`core.py:173-182`). Cross-reference the module/blueprint split (one `blueprint.yaml` plus per-source `blueprints/<id>.yaml` files) rather than assuming one blueprint file per skill when populating any `sources`/`target_blueprint` context field.

- [ ] **Step 1: Write failing tests for the error hierarchy**

```python
import pytest

from officina.dispatcher.errors import (
    DispatcherError,
    InterfaceNotFoundError,
    UnauthorizedCallerError,
)


def test_dispatcher_error_has_schema_version_and_code():
    err = InterfaceNotFoundError(
        caller_module_id="install-assistant-tools-rtx",
        target_module_id="officina-install",
        interface_id="officina-install.interface.does-not-exist",
    )
    payload = err.as_payload()
    assert payload["schema_version"] == 1
    assert payload["code"] == "dispatcher.interface_not_found"
    assert payload["caller_module_id"] == "install-assistant-tools-rtx"
    assert payload["target_module_id"] == "officina-install"


def test_dispatcher_error_payload_never_contains_credentials():
    err = UnauthorizedCallerError(
        caller_module_id="rogue-module",
        target_module_id="officina-common",
        interface_id="common.interface.famulus-paths",
    )
    payload = err.as_payload()
    dumped = str(payload)
    assert "token" not in dumped.lower()
    assert "secret" not in dumped.lower()


def test_dispatcher_error_is_still_an_invocation_error_subclass():
    from officina.dispatcher.core import InvocationError
    err = InterfaceNotFoundError(
        caller_module_id="a", target_module_id="b", interface_id="c",
    )
    assert isinstance(err, InvocationError)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q tests/test_dispatcher_errors.py -v`
Expected: FAIL — `officina.dispatcher.errors` doesn't exist yet.

- [ ] **Step 3: Implement the error hierarchy**

```python
"""Structured dispatcher failures: every raise site produces a typed error
with a stable machine-readable code and safe context, never raw credentials
or tracebacks in the payload."""
from __future__ import annotations

from dataclasses import dataclass, field

from officina.dispatcher.core import InvocationError

SCHEMA_VERSION = 1


@dataclass
class DispatcherError(InvocationError):
    code: str = field(init=False)
    caller_module_id: str = ""
    target_module_id: str = ""

    def as_payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "code": self.code,
            "caller_module_id": self.caller_module_id,
            "target_module_id": self.target_module_id,
            "message": str(self),
        }


@dataclass
class InterfaceNotFoundError(DispatcherError):
    interface_id: str = ""

    def __post_init__(self) -> None:
        self.code = "dispatcher.interface_not_found"
        super().__init__(
            f"interface not found: {self.interface_id} (requested by {self.caller_module_id})"
        )

    def as_payload(self) -> dict:
        payload = super().as_payload()
        payload["interface_id"] = self.interface_id
        return payload


@dataclass
class UnauthorizedCallerError(DispatcherError):
    interface_id: str = ""

    def __post_init__(self) -> None:
        self.code = "dispatcher.unauthorized_caller"
        super().__init__(
            f"{self.caller_module_id} is not an allowed caller of {self.interface_id}"
        )

    def as_payload(self) -> dict:
        payload = super().as_payload()
        payload["interface_id"] = self.interface_id
        return payload
```

Adjust the exact dataclass/inheritance mechanics to match `InvocationError`'s real constructor signature in `core.py` (it currently takes `caller_module_id`/`target_module_id`/`caller_skill` as keyword args with compatibility shimming — read `core.py:120-185` before finalizing this shape, since a naive dataclass subclass may conflict with its existing `__init__`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q tests/test_dispatcher_errors.py -v`
Expected: PASS

- [ ] **Step 5: Re-derive the raise-site inventory and replace flat `InvocationError` raises**

Grep `src/officina/dispatcher/core.py` for `raise InvocationError(` (re-run this at implementation time — the audit found roughly 20 sites between lines 456 and 1109, but exact numbers will have drifted). For each, replace with the matching typed subclass (add new subclasses beyond `InterfaceNotFoundError`/`UnauthorizedCallerError` as needed — e.g. `MalformedRequestError`, `ModuleNotCertifiedError` — following the same pattern, one per distinct failure phase).

- [ ] **Step 6: Add `--error-format json` to the CLI**

Modify `cli.py`'s argument parser to add `--error-format {text,json}` (default `text`). Replace the `except InvocationError as exc: print(f"error: {exc}", file=sys.stderr)` block (currently `cli.py:79-80`) with:

```python
    except InvocationError as exc:
        if args.error_format == "json" and hasattr(exc, "as_payload"):
            print(json.dumps(exc.as_payload()), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
```

- [ ] **Step 7: Write and run CLI-level tests**

```python
def test_cli_error_format_json_emits_structured_payload(capsys):
    exit_code = run_cli(["--error-format", "json", "invoke", "nonexistent-module", "some-interface"])
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["schema_version"] == 1
    assert "code" in payload


def test_cli_default_error_format_is_unchanged_text(capsys):
    exit_code = run_cli(["invoke", "nonexistent-module", "some-interface"])
    captured = capsys.readouterr()
    assert captured.err.startswith("error:")
```

(Match the real CLI test harness/entry-point function name — read the existing dispatcher CLI test file first.)

Run: `python3 -m pytest -q tests/test_dispatcher_errors.py tests/test_dispatcher_cli.py -v`
Expected: PASS, no regressions in existing dispatcher CLI tests.

- [ ] **Step 8: Run the full dispatcher test suite**

Run: `python3 -m pytest -q src/officina/dispatcher/ tests/ -k dispatcher -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/officina/dispatcher/errors.py src/officina/dispatcher/core.py src/officina/dispatcher/cli.py tests/test_dispatcher_errors.py tests/test_dispatcher_cli.py
git commit -m "feat(dispatcher): structured typed failures with module_id context and --error-format json"
```

---

## Explicitly out of scope

- Re-fixing cloud-files' `args_prefix` contract — already fixed by the v5 migration (`a32a6bb`). Task 1 only adds the generic guard rail.
- Migrating `caller_skill`/`target_skill` compatibility properties off `InvocationError` entirely — those remain for v4 consumers per `core.py:173-182`'s own documented contract; this plan only ensures new structured payloads use `module_id` naming, it doesn't remove the compat shim.
