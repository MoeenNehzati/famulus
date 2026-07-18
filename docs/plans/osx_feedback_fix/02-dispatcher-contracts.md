# Dispatcher Contracts and Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or, with explicit delegation approval, `superpowers:subagent-driven-development`.

**Goal:** Prevent fixed-token contract duplication and give every dispatcher-owned rejection a stable, safe, machine-readable error.

**Architecture:** Skill-maker validates blueprint semantics before generated contracts are accepted. Dispatcher rejection sites raise typed exceptions that render stable text or schema-versioned JSON without wrapping target-process output.

**Tech Stack:** Python 3.11, Famulus blueprint schemas, dispatcher, pytest.

## Global constraints

- Inherit only program-wide security constraints and sequencing from the [umbrella](README.md). This subplan is authoritative for dispatcher validation and error contracts.
- Skill and blueprint changes use `skill-maker`; generated blocks are never hand-edited.
- JSON errors never contain raw argv, stdin, environment values, credentials, authorization URLs, target output, or tracebacks.
- Once a target process starts, its stdout, stderr, and exit code pass through unchanged.

## Source feedback owned here

Items 5, 12, and 21 in the umbrella traceability table.

---

### Task 1: Reject duplicated fixed subcommands and repair cloud-files contracts

**Files:**
- Modify through `skill-maker`: `skills/cloud-files/blueprint.yaml`
- Modify: `skills/cloud-files/tests/test_cloud_files_ensure_oauth.py`
- Modify: `skills/skill-maker/tests/test_blueprint_tools.py`
- Modify: `tests/validate_blueprints.py`
- Regenerate: `skills/cloud-files/SKILL.md`
- Regenerate: `references/blueprint/runtime_dependencies.json`

**Interfaces:**
- Changes: `cloud-files.machine.ensure-oauth` and `cloud-files.machine.write-config` accept zero fixed subcommand positionals; their Python binding supplies the fixed subcommand through `args_prefix` exactly once.
- Produces: a skill-maker semantic validation error when a required literal positional duplicates a literal token already supplied by `args_prefix`.

- [ ] **Step 1: Add failing semantic-validator fixtures**

Add a fixture whose binding has `args_prefix: [write-config]` and whose only pattern requires positional zero to match `^write-config$`. Assert the validator reports:

```text
fixed args_prefix token 'write-config' duplicates required positional 0
```

Add a control fixture where `args_prefix` supplies a flag/value pair and caller positionals remain independent; it must pass.

- [ ] **Step 2: Run validator tests and verify RED**

Run: `python3 -m pytest -q skills/skill-maker/tests/test_blueprint_tools.py tests/validate_blueprints.py`

Expected: the duplicate fixture is not rejected.

- [ ] **Step 3: Implement the semantic check and repair cloud-files contracts**

The check applies only when all are true:

1. `args_prefix` contains a non-flag literal token;
2. a pattern requires that positional index;
3. its regex is an anchored literal equal to the prefix token.

Use `skill-maker` to change both cloud-files contracts to `min_positionals: 0`, `max_positionals: 0`, with no positional regex. Regenerate the injected SKILL block and assert the usage lines no longer repeat `ensure-oauth` or `write-config` after the interface ID.

- [ ] **Step 4: Run the repaired contract slice**

Run: `python3 -m pytest -q skills/skill-maker/tests/test_blueprint_tools.py tests/validate_blueprints.py skills/cloud-files/tests/test_cloud_files_ensure_oauth.py`

Expected: all pass and generated usage supplies each fixed subcommand exactly once.

- [ ] **Step 5: Commit after review**

Commit only the validator, cloud-files contract, generated artifacts, and tests with message `fix: reject duplicated fixed interface tokens`.

---

### Task 2: Add structured dispatcher failures

**Files:**
- Create: `src/officina/dispatcher/errors.py`
- Create: `tests/test_officina_dispatcher_errors.py`
- Modify: `src/officina/dispatcher/__init__.py`
- Modify: `src/officina/dispatcher/cli.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `script_dispatcher/src/script_dispatcher/__init__.py`
- Modify: `script_dispatcher/src/script_dispatcher/core.py`
- Modify: `script_dispatcher/src/script_dispatcher/cli.py`
- Modify: `tests/test_officina_dispatcher.py`

**Interfaces:**
- Produces: `DispatcherError.as_payload() -> dict[str, object]`, plus phase-level and stable-code subclasses.
- Preserves: `InvocationError` as the compatibility superclass for dispatcher-owned pre-execution failures during migration.
- Preserves: `script_dispatcher` as a compatibility import/CLI surface whose exported exception objects are identical to the canonical `officina.dispatcher` classes.
- Produces: `render_dispatcher_error_text(error: DispatcherError) -> str` and `render_dispatcher_error_json(error: DispatcherError) -> str`.
- Changes: dispatcher CLI accepts `--error-format {text,json}`; JSON errors are one flat object on stderr and dispatcher failures exit 2.

- [ ] **Step 1: Write failing structured-error and rendering tests**

In `tests/test_officina_dispatcher_errors.py`, construct one representative leaf exception and require the approved flat payload:

```python
error = InterfaceNotFoundError(
    "The requested machine interface is not declared.",
    request={
        "caller_skill": "caller",
        "target": "target.machine.missing",
        "platform": "macos",
    },
    sources=[
        {
            "role": "target_blueprint",
            "path": "/tmp/repo/skills/target/blueprint.yaml",
            "version": 3,
        }
    ],
    details={"requested_interface": "missing"},
    hints=["Check the target skill's declared machine interfaces."],
)

assert error.as_payload() == {
    "schema_version": 1,
    "code": "dispatcher.interface_not_found",
    "message": "The requested machine interface is not declared.",
    "phase": "resolve",
    "request": {
        "caller_skill": "caller",
        "target": "target.machine.missing",
        "platform": "macos",
    },
    "sources": [
        {
            "role": "target_blueprint",
            "path": "/tmp/repo/skills/target/blueprint.yaml",
            "version": 3,
        }
    ],
    "details": {"requested_interface": "missing"},
    "hints": ["Check the target skill's declared machine interfaces."],
}
assert isinstance(error, InvocationError)
assert str(error) == error.message
```

Add CLI tests requiring default text to start with `error [dispatcher.interface_not_found]:` and `--error-format json` to emit exactly one JSON object to stderr with exit code 2. Pass sentinel secrets through arguments, stdin, and environment; assert none appears in either rendering.

Add compatibility tests requiring `script_dispatcher.DispatcherError is officina.dispatcher.DispatcherError`, `script_dispatcher.InvocationError is officina.dispatcher.InvocationError`, and the same identity for supported phase/leaf exports. Existing imports from `script_dispatcher.core` must keep working. Assert representative legacy `str(exc)` values and message substrings are unchanged. Exercise both CLI entrypoints and require byte-identical text/JSON failures and exit codes.

Because the dispatcher parser forwards the target tail, either require `--error-format` before the target interface and document/test that grammar, or deliberately redesign parsing and test both positions. Do not leave the option apparently global while silently forwarding it to the target.

Run: `python3 -m pytest -q tests/test_officina_dispatcher_errors.py tests/test_officina_dispatcher.py`

Expected: collection fails because `officina.dispatcher.errors` and `--error-format` do not exist.

- [ ] **Step 2: Implement the exception hierarchy and renderers**

Create `src/officina/dispatcher/errors.py` around this base contract:

```python
import json
from collections.abc import Mapping, Sequence


class DispatcherError(Exception):
    schema_version = 1
    code = "dispatcher.error"
    phase = "unknown"
    exit_code = 2

    def __init__(
        self,
        message: str,
        *,
        request: Mapping[str, object] | None = None,
        sources: Sequence[Mapping[str, object]] = (),
        details: Mapping[str, object] | None = None,
        hints: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.request = dict(request or {})
        self.sources = tuple(dict(source) for source in sources)
        self.details = dict(details or {})
        self.hints = tuple(hints)
        for field, value in (
            ("request", self.request),
            ("sources", self.sources),
            ("details", self.details),
            ("hints", self.hints),
        ):
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise TypeError(f"dispatcher error {field} must be JSON-safe") from exc

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "code": self.code,
            "message": self.message,
            "phase": self.phase,
        }
        if self.request:
            payload["request"] = dict(self.request)
        if self.sources:
            payload["sources"] = [dict(source) for source in self.sources]
        if self.details:
            payload["details"] = dict(self.details)
        if self.hints:
            payload["hints"] = list(self.hints)
        return payload
```

Use this inheritance tree:

```text
DispatcherError
└── InvocationError
    ├── DispatcherRequestError
    │   └── InvalidRequestError
    ├── DispatcherLoadError
    │   └── BlueprintNotFoundError
    ├── DispatcherValidationError
    │   └── BlueprintInvalidError
    ├── DispatcherResolutionError
    │   ├── InterfaceNotFoundError
    │   ├── PatternNotMatchedError
    │   └── AmbiguousPatternError
    ├── DispatcherPolicyError
    │   ├── PlatformUnsupportedError
    │   ├── CallerForbiddenError
    │   └── UndeclaredInterfaceUseError
    └── DispatcherBuildError
        ├── RuntimeInvalidError
        └── RuntimeUnavailableError
```

Phase classes define `phase` as `request`, `load`, `validate`, `resolve`, `policy`, or `build`. Leaf classes define stable codes under the `dispatcher.` namespace. Do not add a leaf class unless it has a distinct stable code or structured-detail contract.

Use these exact leaf-code assignments:

| Exception | Code |
|---|---|
| `InvalidRequestError` | `dispatcher.invalid_request` |
| `BlueprintNotFoundError` | `dispatcher.blueprint_not_found` |
| `BlueprintInvalidError` | `dispatcher.blueprint_invalid` |
| `InterfaceNotFoundError` | `dispatcher.interface_not_found` |
| `PatternNotMatchedError` | `dispatcher.pattern_no_match` |
| `AmbiguousPatternError` | `dispatcher.pattern_ambiguous` |
| `PlatformUnsupportedError` | `dispatcher.platform_unsupported` |
| `CallerForbiddenError` | `dispatcher.caller_forbidden` |
| `UndeclaredInterfaceUseError` | `dispatcher.interface_use_undeclared` |
| `RuntimeInvalidError` | `dispatcher.runtime_invalid` |
| `RuntimeUnavailableError` | `dispatcher.runtime_unavailable` |

`DispatcherError`, `InvocationError`, and the phase classes are catch boundaries, not directly raised failure codes after migration.

The text renderer starts with `error [<code>]: <message>` and conditionally renders safe request, source, detail, and hint lines. The JSON renderer uses `json.dumps(error.as_payload(), sort_keys=True)` and emits no preamble. Reject non-JSON-safe payload values at exception construction rather than falling back to `repr`.

- [ ] **Step 3: Migrate dispatcher call sites and preserve process behavior**

Move `InvocationError` out of `core.py` and re-export it from canonical `core.py` and package `__init__.py` for compatibility. Make `script_dispatcher` a thin re-export/delegation shim; it must not define a second exception hierarchy or renderer. Replace string-only raises with the narrowest appropriate subclass. Inventory every current `raise InvocationError`/dispatcher-owned pre-execution rejection and map it to a leaf while preserving its existing human message. Populate only facts already available at the rejection site: canonical target, caller, platform, resolved blueprint path/version, rejecting declaration, and safe expected/actual values.

Add `--error-format` to `src/officina/dispatcher/cli.py`, catch `DispatcherError`, and render text or JSON to stderr. Successful dry-run JSON remains unchanged. Once a target process starts, its stdout, stderr, and return code pass through unchanged and are never wrapped as dispatcher JSON.

Never include raw argv, stdin, environment values, credentials, authorization URLs, target-process output, or tracebacks in `request`, `sources`, `details`, `hints`, or `message`.

- [ ] **Step 4: Run focused dispatcher tests**

Run: `python3 -m pytest -q tests/test_officina_dispatcher_errors.py tests/test_officina_dispatcher.py tests/test_dispatcher_route_smoke.py`

Expected: default dispatcher errors are structured text, JSON mode emits the approved flat object, target-process behavior is unchanged, and no sentinel secret appears.

- [ ] **Step 5: Commit after review**

Commit only dispatcher error runtime/tests with message `feat: structure dispatcher errors`.

---
