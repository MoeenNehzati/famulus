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

> **Design correction (2026-07-28):** The audit this task's original design was
> based on described `InvocationError` as already having `caller_module_id`/
> `target_module_id` fields and a documented "Temporary compatibility for v4
> consumers" property — that description does not match the real file. As of
> this correction, `src/officina/dispatcher/core.py` defines:
> ```python
> class InvocationError(Exception):
>     """Raised when a dispatcher request is invalid."""
> ```
> a bare `Exception` subclass with no fields, and ~20 raise sites (grep
> `raise InvocationError(` — currently between lines 244 and 680, re-check at
> implementation time) that all pass a single plain string message, e.g.
> `raise InvocationError(f"module id \`{target}\` is not callable")`. There is
> no `caller_skill`/`target_skill` v4-compat property anywhere in this file —
> nothing to preserve compatibility with beyond the bare exception type and
> its string messages. The real CLI (`src/officina/dispatcher/cli.py`) takes
> `--caller-skill` (required), a positional `target_or_skill`, and
> `rest: REMAINDER` — not an `invoke <module> <interface>` subcommand form —
> and its existing `except InvocationError` block returns exit code **2**, not
> 1. The design below is corrected against this real shape. Do not assume any
> of the original snippets in this section (now replaced) — they described
> code that does not exist in this repo.

**Files:**
- Create: `src/officina/dispatcher/errors.py`
- Modify: `src/officina/dispatcher/core.py` (replace `raise InvocationError("...")` string-message raises with typed subclass raises — re-grep for the current raise-site list at implementation time, do not trust a stale line count)
- Modify: `src/officina/dispatcher/cli.py` (add `--error-format json`; the existing `except InvocationError as exc: ...; return 2` block gains a JSON branch, exit code stays 2)
- Test: `tests/test_dispatcher_errors.py`, extend the real dispatcher CLI test file (find its actual name/location — do not assume `tests/test_dispatcher_cli.py` exists under that exact name)

**Design decision (module_id vocabulary):** Even though there's no existing `caller_module_id`/`target_module_id` field to build on, still use that vocabulary (not `caller_skill`/`target_skill`) for the NEW structured payload's field names, since it matches this repo's real v5 terminology (module ids, not "skills") used everywhere else in this plan and the installer-runtime plan. The CLI's own `--caller-skill` flag name is a separate, pre-existing surface — do not rename it; just don't propagate that naming into the new payload schema.

**Backward compatibility note:** Since `InvocationError` has no existing fields/properties to preserve, this task's compatibility concern is narrower than originally assumed: (a) `str(exc)` for any raise site you convert must produce the SAME message text as before (some code may pattern-match error text — grep for `str(exc)` usage before changing message wording), (b) `isinstance(exc, InvocationError)` must still hold for every converted raise site (existing `except InvocationError` handlers, including `script_dispatcher`'s re-export, must keep working), (c) the CLI's exit code for an `InvocationError` stays 2, not 1 as the original (incorrect) task text assumed.

- [ ] **Step 1: Write failing tests for the error hierarchy**

```python
import pytest

from officina.dispatcher.errors import (
    DispatcherError,
    InterfaceNotFoundError,
    UnauthorizedCallerError,
)
from officina.dispatcher.core import InvocationError


def test_dispatcher_error_has_schema_version_and_code():
    err = InterfaceNotFoundError(
        caller_module_id="install-assistant-tools",
        target_module_id="install",
        interface_id="install.interface.does-not-exist",
    )
    payload = err.as_payload()
    assert payload["schema_version"] == 1
    assert payload["code"] == "dispatcher.interface_not_found"
    assert payload["caller_module_id"] == "install-assistant-tools"
    assert payload["target_module_id"] == "install"


def test_dispatcher_error_payload_never_contains_credentials():
    err = UnauthorizedCallerError(
        caller_module_id="rogue-module",
        target_module_id="common",
        interface_id="common.interface.famulus-paths",
    )
    payload = err.as_payload()
    dumped = str(payload)
    assert "token" not in dumped.lower()
    assert "secret" not in dumped.lower()


def test_dispatcher_error_is_still_an_invocation_error_subclass():
    err = InterfaceNotFoundError(
        caller_module_id="a", target_module_id="b", interface_id="c",
    )
    assert isinstance(err, InvocationError)


def test_dispatcher_error_str_is_a_readable_message():
    err = InterfaceNotFoundError(
        caller_module_id="a", target_module_id="b", interface_id="b.interface.missing",
    )
    assert "b.interface.missing" in str(err)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q -o pythonpath=src tests/test_dispatcher_errors.py -v`
Expected: FAIL — `officina.dispatcher.errors` doesn't exist yet.

- [ ] **Step 3: Implement the error hierarchy**

Plain `__init__` methods, not `@dataclass` (mixing `@dataclass` with `Exception` subclassing is fragile — `Exception.__init__`/`args`/pickling interact awkwardly with dataclass-generated `__init__`; a hand-written `__init__` calling `super().__init__(message)` is simpler and avoids that pitfall entirely):

```python
"""Structured dispatcher failures: every raise site produces a typed error
with a stable machine-readable code and safe context, never raw credentials
or tracebacks in the payload. Every class here remains an InvocationError
subclass so existing `except InvocationError` handlers (including
script_dispatcher's re-export) keep working unchanged."""
from __future__ import annotations

from officina.dispatcher.core import InvocationError

SCHEMA_VERSION = 1


class DispatcherError(InvocationError):
    code = "dispatcher.error"

    def __init__(self, message: str, *, caller_module_id: str = "", target_module_id: str = "") -> None:
        super().__init__(message)
        self.caller_module_id = caller_module_id
        self.target_module_id = target_module_id

    def as_payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "code": self.code,
            "caller_module_id": self.caller_module_id,
            "target_module_id": self.target_module_id,
            "message": str(self),
        }


class InterfaceNotFoundError(DispatcherError):
    code = "dispatcher.interface_not_found"

    def __init__(self, *, caller_module_id: str, target_module_id: str, interface_id: str) -> None:
        self.interface_id = interface_id
        super().__init__(
            f"interface not found: {interface_id} (requested by {caller_module_id})",
            caller_module_id=caller_module_id,
            target_module_id=target_module_id,
        )

    def as_payload(self) -> dict:
        payload = super().as_payload()
        payload["interface_id"] = self.interface_id
        return payload


class UnauthorizedCallerError(DispatcherError):
    code = "dispatcher.unauthorized_caller"

    def __init__(self, *, caller_module_id: str, target_module_id: str, interface_id: str) -> None:
        self.interface_id = interface_id
        super().__init__(
            f"{caller_module_id} is not an allowed caller of {interface_id}",
            caller_module_id=caller_module_id,
            target_module_id=target_module_id,
        )

    def as_payload(self) -> dict:
        payload = super().as_payload()
        payload["interface_id"] = self.interface_id
        return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -q -o pythonpath=src tests/test_dispatcher_errors.py -v`
Expected: PASS

- [ ] **Step 5: Re-derive the raise-site inventory and replace string-message raises with typed subclasses**

Grep `src/officina/dispatcher/core.py` for `raise InvocationError(` (re-run this now — do not trust any specific line-number list, it will have drifted). For each site, read the surrounding code to determine which failure phase it represents (interface not found, unauthorized caller, malformed request, certification failure, etc.) and either reuse `InterfaceNotFoundError`/`UnauthorizedCallerError` or add a new subclass following the exact same pattern (one per distinct failure phase, `code` string following `dispatcher.<snake_case_reason>`). Preserve each site's exact existing message text (or as close as reasonably possible — do not silently reword messages other code might pattern-match against; grep the repo for `str(exc)` / substring-matching against dispatcher error text before changing wording). Not every raise site necessarily needs a bespoke subclass — a generic `DispatcherError(message, caller_module_id=..., target_module_id=...)` is an acceptable fallback for phases that don't have enough callers/callers-in-this-plan to justify a dedicated subclass; use judgment rather than forcing exactly one subclass per raise site.

- [ ] **Step 6: Add `--error-format json` to the CLI**

Read `cli.py`'s current argument parser and `except InvocationError` block first (real shape: `--caller-skill` required flag, positional `target_or_skill`, `rest: REMAINDER`, existing except block returns 2). Add `--error-format {text,json}` (default `text`) as a new optional flag. Modify the except block to branch on it:

```python
    except InvocationError as exc:
        if args.error_format == "json" and hasattr(exc, "as_payload"):
            print(json.dumps(exc.as_payload()), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
```

(Exit code stays `2`, matching the existing behavior — do not change it to `1`.) `json` needs importing if not already imported in this file.

- [ ] **Step 7: Write and run CLI-level tests**

Find the real dispatcher CLI test file (search for existing tests that call `cli.main()` or invoke the dispatcher CLI programmatically — do not assume a filename) and add tests matching the REAL argv shape and exit code:

```python
def test_cli_error_format_json_emits_structured_payload(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["dispatcher", "--caller-skill", "test-caller", "--error-format", "json", "nonexistent-module.interface.missing"])
    exit_code = cli.main()
    assert exit_code == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["schema_version"] == 1
    assert "code" in payload


def test_cli_default_error_format_is_unchanged_text(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["dispatcher", "--caller-skill", "test-caller", "nonexistent-module.interface.missing"])
    exit_code = cli.main()
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error:")
```

(Match the real `main()`/entry-point invocation convention used by existing tests in that file — this is illustrative, adjust to fit.)

Run: `python3 -m pytest -q -o pythonpath=src tests/test_dispatcher_errors.py <the real CLI test file> -v`
Expected: PASS, no regressions in existing dispatcher CLI tests.

- [ ] **Step 8: Run the full dispatcher test suite**

Run: `python3 -m pytest -q -o pythonpath=src tests/ -k dispatcher -v` and also `python3 -m pytest -q -o pythonpath=src script_dispatcher/ 2>/dev/null || true` (check whether `script_dispatcher` has its own test suite that re-exports `InvocationError` and needs to keep passing — the implementer who first investigated this task found `script_dispatcher/src/script_dispatcher/{__init__,core,cli}.py` re-exports dispatcher symbols; confirm `script_dispatcher.InvocationError is officina.dispatcher.core.InvocationError` still holds after this change, since subclassing doesn't touch that identity but verify anyway).
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/officina/dispatcher/errors.py src/officina/dispatcher/core.py src/officina/dispatcher/cli.py tests/test_dispatcher_errors.py <the real CLI test file>
git commit -m "feat(dispatcher): structured typed failures with module_id context and --error-format json"
```

---

## Explicitly out of scope

- Re-fixing cloud-files' `args_prefix` contract — already fixed by the v5 migration (`a32a6bb`). Task 1 only adds the generic guard rail.
- Migrating `caller_skill`/`target_skill` compatibility properties off `InvocationError` entirely — those remain for v4 consumers per `core.py:173-182`'s own documented contract; this plan only ensures new structured payloads use `module_id` naming, it doesn't remove the compat shim.
