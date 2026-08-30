# Help-Compass Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an exact Rutter process binding sufficient for weak-agent execution: `using-compass` supplies only host orchestration, while authoritative structured `help-compass` data supplies every controller and Voyage operation rule. Then pass one controlled `gpt-5.4-mini` reproduction of the `quicktest` scenario.

**Architecture:** `rutter.source.dispenser` returns one application-independent Compass state machine as finite JSON from `help-compass`. The controller and every contextless worker read it from the exact binding they operate. `using-compass` only checks the binding, reserves and gates host agents, passes each worker `{binding, voyage_id}`, waits, and collects reports. A separate inventory-owned change makes a missing worker file a public validation issue without changing worker ownership, packet semantics, or Voyage transitions.

**Tech Stack:** Python 3.11+, `argparse`, JSON, pytest, Officina schema-v6 blueprints, generated SKILL contract blocks.

**Spec:** This document, especially “Design Contract” and “Global Constraints” below.

**Target baseline:** implement in repository worktree `.worktrees/rutter-node-entry-core` at `3c950eee3a673253d067c4204de6b2ef61f71ae1` or a verified descendant that still contains every File Map path. Before editing, verify that the live versions are Rutter module 10, dispenser 5, `using-compass` 12, inventory dispenser/instruction 11, private runtime 85, math-graph gateway 87/default interface 79, and root module 112. Stop and rebase this plan if any path or version differs.

## Design Contract

The new process operation is exactly `help-compass`. It returns one top-level `compass_protocol` object containing exact argv templates, result paths, ordered controller and worker states, assignment and final-report schemas, and stop/release rules. Ordinary `help` remains CLI-oriented. A Message mutates through one atomic `advance --response-file ... --responding-to ...`; separate `validate` is optional diagnosis only. A ready `null` or `MachineInstruction` advances without a response.

`using-compass` requires one binding, reads `help-compass`, reserves all required agents, creates them behind a WAIT/READY barrier, passes only `{binding, voyage_id}`, starts them together, waits, and collects their protocol-defined reports. It fails closed before Voyage work if the binding, protocol version, capacity, or barrier is unavailable. It contains no Rutter command, status, validation, recovery, or release knowledge. These instructions centralize protocol ownership; they do not claim to enforce model compliance.

## Global Constraints

- Do not change `instructions/inventory.md`, inventory schemas, packet semantics, worker-owned file semantics, or Voyage transitions.
- The only inventory validation change is to translate missing or malformed worker-file reads into a structured `invalid-inventory` issue; transport must not create the worker-owned file.
- Do not add controller identity, worker leases, authentication, or new Rutter core state.
- `help-compass` must be generic and identical for every `VoyageDispenser`; applications cannot override it.
- The protocol must be structured JSON, not a prose blob.
- `using-compass` must never search for alternate interfaces or inspect private implementation when the supplied binding fails.
- Preserve all unrelated dirty changes, particularly installer/runtime files already modified in this worktree.
- Public interface and consumer pins must be updated as one closed versioned dependency change; Tasks 1–5 are one staging/commit unit.
- Tasks 4 and 5 are one atomic inventory-dispenser version-12 unit: do not regenerate or describe Task 4 as complete without Task 5.
- Use repository skills for blueprint regeneration; do not invoke private blueprint scripts directly.
- Do not commit unless the user explicitly authorizes commits.

---

## File Map

- `src/officina/rutter/dispenser.py`: owns the generic protocol value, the `help_compass()` public method, parser registration, and CLI JSON projection.
- `src/officina/rutter/compass_protocol.schema.json`: closed public schema for the `{"compass_protocol": ...}` help response.
- `src/officina/rutter/tests/test_rutter_dispenser.py`: specifies ordinary `help` versus structured `help-compass` behavior.
- `src/officina/rutter/blueprints/dispenser.yaml`: declares the new operation and dispenser interface version 6.
- `src/officina/rutter/blueprint.yaml`: advances the Rutter module version and exports the updated dispenser source.
- `src/officina/rutter/tests/test_blueprint_contract.py`: verifies the public interface/version closure.
- `skills/using-compass/SKILL.md`: becomes the thin host adapter.
- `skills/using-compass/blueprints/gateway.yaml`: advances the skill source/interface to version 13 and consumes dispenser interface version 6.
- `skills/using-compass/blueprint.yaml`: advances the skill module to version 13.
- `skills/using-compass/tests/test_using_compass_instructions.py`: prevents Voyage lifecycle details from returning to the skill.
- `skills/distill-to-rutters/{blueprint.yaml,SKILL.md,blueprints/gateway.yaml,blueprints/instructions-design-implementation.yaml}` and `tests/{test_distill_to_rutters_routing.py,test_runtime_compatibility.py}`: advance the other live `using-compass` consumer as one generated chain.
- `skills/math-dependency-graph/_rtx/blueprints/rtx-inventory-voyage-dispenser.yaml`: admits `help-compass`, consumes dispenser interface 6, and advances the inventory dispenser interface to 12.
- `skills/math-dependency-graph/_rtx/blueprint.yaml`: advances the private runtime module and re-exports inventory dispenser interface 12.
- `skills/math-dependency-graph/blueprints/instructions-inventory-voyages.yaml`: consumes inventory dispenser 12 and `using-compass` 13; advances to 12.
- `skills/math-dependency-graph/instructions/inventory-voyages.md`: names the exact injected binding and forbids alias or source-interface substitution.
- `skills/math-dependency-graph/blueprints/gateway.yaml`: advances its dependency on `inventory-voyages` and its source/interface versions.
- `skills/math-dependency-graph/blueprint.yaml`: advances the module/namespace versions and exported pins.
- `skills/math-dependency-graph/SKILL.md`: receives regenerated contract/interface blocks only.
- `skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py`: verifies the complete consumer pin closure.
- `skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_support.py`: translates an unavailable or malformed worker inventory file into a public validation issue.
- `skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py`: specifies the missing-file diagnostic without weakening worker ownership.
- `~/.codex/sessions/`: contains the live `gpt-5.4-mini` replay transcript generated by the host; it is never added to the repository.

### Task 1: Expose executable structured `help-compass`

**Files:**

- Modify: `src/officina/rutter/tests/test_rutter_dispenser.py`
- Modify: `src/officina/rutter/dispenser.py`
- Create: `src/officina/rutter/compass_protocol.schema.json`

**Interfaces:**

- Produces: `VoyageDispenser.help_compass() -> dict[str, object]`
- Produces: CLI operation `help-compass` with output `{"compass_protocol": <protocol>}`
- Preserves: existing `VoyageDispenser.help() -> str`

- [ ] **Step 1: Replace the mixed-help test with executable-protocol tests**

Replace `test_cli_help_explains_one_agent_per_voyage` with tests that assert:

```python
assert voyage_dispenser_cli(dispenser_a, ["help-compass"]) == 0
assert json.loads(capsys.readouterr().out)["compass_protocol"] == dispenser_a.help_compass()
assert voyage_dispenser_cli(dispenser_a, ["help"]) == 0
ordinary_help = json.loads(capsys.readouterr().out)["help"]
assert all(command in ordinary_help for command in ("modes", "list", "initiate", "status", "validate", "advance", "release"))
assert not any(term in ordinary_help for term in ("compass_protocol", "controller", "worker", "WAIT/READY", "assignment"))
assert dispenser_a.help_compass() == dispenser_b.help_compass()
assert not {"protocol", "help_compass"} & set(inspect.signature(VoyageDispenser).parameters)

protocol = dispenser_a.help_compass()
assert protocol["version"] == 1
assert set(protocol["operations"]) == {
    "modes", "initiate", "list", "status", "advance-automatic",
    "advance-message", "validate-diagnostic", "release",
}
assert all("argv" in route for route in protocol["operations"].values())
assert protocol["assignment"]["worker_input"] == ["binding", "voyage_id"]
assert protocol["worker"]["starts_with"] == "help-compass"
```

Also add one table-driven test that expands every typed argv schema and passes it through the real CLI parser. Validate the full `{"compass_protocol": ...}` wrapper against `compass_protocol.schema.json`; remove each required field in turn and assert rejection. Assert the complete worker loop, Message paths, fresh-status transitions, and terminal receipt handshake. In this test file, define a test-only `CompassProtocolHarness(protocol, host, timeout_seconds)` that expands typed segments and interprets controller/worker states against fake host events; it is not production code. Use it in `test_compass_protocol_negative_host_cases` with a 0.01-second test timeout for capacity/barrier failure, second-spawn failure, crash, timeout, malformed report, release failure, and mixed outcomes; assert exact reports, sibling draining, and no force release.

- [ ] **Step 2: Run the focused tests and confirm the new command fails**

Run:

```bash
pytest -q src/officina/rutter/tests/test_rutter_dispenser.py -k 'help'
```

Expected: FAIL because `help-compass` and the executable protocol do not exist.

- [ ] **Step 3: Split CLI guidance from the Compass protocol**

In `src/officina/rutter/dispenser.py`, replace `_USAGE_GUIDANCE` with this CLI-only value:

```python
_USAGE_GUIDANCE = (
    "Discover or initialize one run, then operate its Voyages by ID.\n\n"
    "Commands:\n"
    "  help: explain dispenser commands and initialization modes.\n"
    "  help-compass: return the structured Compass controller/worker protocol.\n"
    "  modes: list initialization modes and required arguments.\n"
    "  list [--run-prefix]: list authorized Voyage IDs.\n"
    "  initiate [mode]: initialize one run exactly once.\n"
    "  status <voyage-id>: read one Voyage's public status.\n"
    "  validate <voyage-id>: validate one Message response without mutation.\n"
    "  advance <voyage-id>: advance one validated response or automatic step.\n"
    "  release <voyage-id>: release one terminal Voyage; --force abandons it."
)
```

Add one immutable module-level `_COMPASS_PROTOCOL` and return a deep copy from `VoyageDispenser.help_compass()`. Its exact value is:

```python
{
    "version": 1,
    "template_rules": {
        "segment_kinds": ["literal", "required", "optional", "repeated-options"],
        "optional": "omit-the-whole-segment-when-source-is-null",
        "repeated-options": "for-each-advertised-mode-argument-in-returned-order-emit---plus-name.replace('_','-')-then-exactly-one-value",
        "repeated_options_validation": "reject-missing-or-extra-values-before-initiate",
        "selected_mode": "activation.mode-when-present-else-$.default_mode",
    },
    "operations": {
        "modes": {"argv": [{"kind": "literal", "value": "modes"}], "success": {"result": "$", "next": "initiate"}},
        "initiate": {"argv": [{"kind": "literal", "value": "initiate"}, {"kind": "optional", "source": "activation.mode"}, {"kind": "optional", "flag": "--run-prefix", "source": "activation.run_prefix"}, {"kind": "repeated-options", "names_from": "$.modes.<selected>.arguments", "values_from": "activation.mode_arguments"}], "success": {"result": "$.voyage_ids", "next": "reserve-workers"}},
        "list": {"argv": [{"kind": "literal", "value": "list"}, {"kind": "optional", "flag": "--run-prefix", "source": "activation.run_prefix"}], "success": {"result": "$.voyage_ids", "next": "report-recovery"}, "use": "recovery-only"},
        "status": {"argv": [{"kind": "literal", "value": "status"}, {"kind": "required", "source": "assignment.voyage_id"}], "success": {"result": "$", "next": "classify-status"}},
        "advance-automatic": {"argv": [{"kind": "literal", "value": "advance"}, {"kind": "required", "source": "assignment.voyage_id"}], "success": {"result": "$", "next": "classify-status"}},
        "advance-message": {
            "argv": [{"kind": "literal", "value": "advance"}, {"kind": "required", "source": "assignment.voyage_id"}, {"kind": "required", "flag": "--response-file", "source": "worker.response_file"}, {"kind": "required", "flag": "--responding-to", "source": "$.evolution.evolution_entry_id"}],
            "atomic": "load-once-validate-and-advance-same-value",
            "success": {"result": "$", "next": "classify-status"},
        },
        "validate-diagnostic": {"argv": [{"kind": "literal", "value": "validate"}, {"kind": "required", "source": "assignment.voyage_id"}, {"kind": "required", "flag": "--response-file", "source": "worker.response_file"}, {"kind": "required", "flag": "--responding-to", "source": "$.evolution.evolution_entry_id"}], "success": {"result": "$.validation", "next": "no-mutation"}, "normative": False},
        "release": {"argv": [{"kind": "literal", "value": "release"}, {"kind": "required", "source": "assignment.voyage_id"}], "success": {"result": "$.released", "equals": True, "next": "release-confirmed"}},
    },
    "errors": {"path": "$.error", "invalid-response": "repair-only-from-$.error.report.issues-at-most-3-times", "stop_codes": ["usage-error", "input-error", "unknown-voyage", "not-initialized", "already-initialized", "unknown-mode", "not-terminal", "internal-error"], "stop_action": "report-public-error-without-guessing-or-release"},
    "controller": {
        "sequence": ["modes", "initiate-exactly-once", "use-only-returned-voyage-ids", "reserve-all-workers", "create-workers-waiting", "wait-all-ready", "start-all", "wait-all-reports"],
        "may_operate_voyage": False,
        "capacity_failure": "preserve-and-report-returned-voyage-ids",
        "worker_timeout_seconds": 1800,
        "worker_failure": "controller-generates-typed-report-and-drains-siblings-without-force-release",
        "report_validation": "reject-malformed-report-and-preserve-voyage-id",
    },
    "assignment": {
        "one_worker_per_voyage": True,
        "worker_input": ["binding", "voyage_id"],
        "worker_loads_protocol": True,
        "start_barrier": "WAIT-READY-START",
    },
    "worker": {
        "starts_with": "help-compass",
        "sequence": ["help-compass", "status", "classify-status", "act", "consume-returned-fresh-status", "classify-status-until-stop", "final-report"],
        "status_paths": {"condition": "$.evolution.condition", "entry_id": "$.evolution.evolution_entry_id", "instruction": "$.instruction", "terminal_result": "$.terminal_result", "fault": "$.fault"},
        "message": {"text": "$.instruction.instructions.text", "payload": "$.instruction.data.payload", "response_schema": "$.instruction.instructions.response_schema", "response": "write-one-finite-JSON-object-with-nonempty-stable-string-outcome-plus-all-response_schema-fields", "outcome_source": "explicit-token-advertised-by-response_schema-or-instruction-text-else-stop-insufficient-validation-detail"},
        "branches": [
            {"when": "condition=ready and instruction=null", "do": "advance-automatic", "next": "classify-returned-status"},
            {"when": "condition=ready and instruction.kind=machine", "do": "advance-automatic", "next": "classify-returned-status"},
            {"when": "condition=ready and instruction.kind=message", "do": "construct-response-then-advance-message", "next": "classify-returned-status"},
            {"when": "condition=terminal and terminal_result!=null", "do": "send-terminal-evidence-await-matching-controller-receipt-release-send-confirmation", "next": "final-report"},
            {"when": "fault!=null or condition in [fault,uncertain]", "do": "report-without-release", "next": "final-report"},
            {"when": "status-or-report-cannot-be-decoded-or-condition-is-unrecognized", "do": "report-malformed-or-unknown-without-release", "next": "final-report"},
        ],
        "invalid_response": "repair-only-from-public-issues-else-insufficient-validation-detail",
        "terminal_receipt": {"request": ["voyage_id", "terminal_result", "last_status"], "ack": "matching-result-digest", "on_missing_or_mismatch": "preserve-without-release"},
    },
    "final_report": {"required": {"voyage_id": "assigned-nonempty-string", "outcome": ["terminal", "fault", "uncertain", "malformed", "unknown", "insufficient-validation-detail", "worker-lost", "timeout", "controller-failure"], "failure_phase": "null-unless-controller-failure-else-capacity|barrier|spawn|report|release", "failure_code": "null-unless-controller-failure-else-nonempty-stable-string", "last_status": "object-or-null-when-no-status-was-observed", "terminal_result": "object-for-terminal-or-release-failure-else-null", "error": "null-only-for-successful-terminal-else-public-object", "release_state": ["released", "preserved", "not-eligible"]}, "invariants": ["released-only-after-matching-terminal-receipt", "release-failure-preserves-acknowledged-result-and-error", "nonterminal-never-released"]},
    "prohibitions": ["binding-substitution", "controller-voyage-operation", "worker-id-switch", "private-inspection", "generic-error-guessing", "force-release-nonterminal"],
}
```

Keep each line formatted by the repository formatter; the compact display above defines content, not mandatory wrapping. The closed schema requires every shown field, rejects extras, and is referenced by the public blueprint output. The protocol version is rejected, not guessed around, when unknown or malformed.

Do not accept a protocol constructor or override callback in `VoyageDispenser.__init__`; the protocol is generic Rutter behavior.

- [ ] **Step 4: Register and project the new command**

Add the parser entry:

```python
commands.add_parser(
    "help-compass",
    help="Return the authoritative Compass controller and worker protocol.",
)
```

Add the CLI branch immediately after ordinary `help`:

```python
elif arguments.command == "help-compass":
    payload = {"compass_protocol": dispenser.help_compass()}
```

- [ ] **Step 5: Run the complete dispenser tests**

Run:

```bash
pytest -q src/officina/rutter/tests/test_rutter_dispenser.py
```

Expected: PASS.

- [ ] **Step 6: Prepare a review checkpoint**

Review only:

```bash
git diff -- src/officina/rutter/dispenser.py src/officina/rutter/tests/test_rutter_dispenser.py
```

Do not stage or commit; Tasks 1–5 close together.

### Task 2: Version and document the dispenser interface

**Files:**

- Modify: `src/officina/rutter/blueprints/dispenser.yaml`
- Modify: `src/officina/rutter/blueprint.yaml`
- Modify: `src/officina/rutter/tests/test_blueprint_contract.py`

**Interfaces:**

- Consumes: `VoyageDispenser.help_compass() -> dict[str, object]`
- Produces: `rutter.source.dispenser.interface.python-api@6`
- Produces: exported `rutter.interface.dispenser@6`

- [ ] **Step 1: Add failing blueprint-contract expectations**

In `test_blueprint_contract.py`, update the dispenser assertions to require:

```python
assert dispenser_source["version"] == 6
assert dispenser_source["interfaces"][
    "rutter.source.dispenser.interface.python-api"
]["version"] == 6
operations = {
    value["value"]
    for value in dispenser_source["interfaces"][
        "rutter.source.dispenser.interface.python-api"
    ]["contract"]["arguments"]["operation"]["type"]["values"]
}
assert "help-compass" in operations
```

Also advance the expected Rutter module version from 10 to 11 and assert a dedicated `compass-described` outcome returns only a `compass-protocol` output conforming to `compass_protocol.schema.json`; the existing generic result remains for other operations.

- [ ] **Step 2: Run the focused contract test and confirm version failures**

Run:

```bash
pytest -q src/officina/rutter/tests/test_blueprint_contract.py
```

Expected: FAIL on dispenser source/interface version 5, Rutter module 10, and missing `help-compass` operation.

- [ ] **Step 3: Update the source blueprint**

In `blueprints/dispenser.yaml`:

- change the source version from 5 to 6;
- change `rutter.source.dispenser.interface.python-api` from 5 to 6;
- add operation enum value `help-compass` with description `Return the authoritative structured Compass controller and worker protocol`;
- add `help-compass` to the CLI operation description;
- update the source/interface descriptions to distinguish ordinary help from Compass protocol discovery.
- add `compass_protocol.schema.json` to source/module content; add dedicated `compass-protocol` output and effect-free `compass-described` success outcome for `help-compass`, referencing the wrapper schema; leave the generic result/output contract for other operations unchanged.

In `src/officina/rutter/blueprint.yaml`, change module version 10 to 11. Do not change access policy or add a new exported interface; `rutter.interface.dispenser` remains the export.

- [ ] **Step 4: Run the focused blueprint contract test**

Run:

```bash
pytest -q src/officina/rutter/tests/test_blueprint_contract.py
```

Expected interim failures are only the exact consumer pins listed in Tasks 3–4. Record them and proceed; this test must pass after Task 5 regeneration.

- [ ] **Step 5: Prepare a review checkpoint**

Review only the three Task 2 files. Do not stage or commit; Tasks 1–5 close together.

### Task 3: Reduce `using-compass` to a thin host adapter

**Files:**

- Modify: `skills/using-compass/tests/test_using_compass_instructions.py`
- Modify: `skills/using-compass/SKILL.md`
- Modify: `skills/using-compass/blueprints/gateway.yaml`
- Modify: `skills/using-compass/blueprint.yaml`
- Modify: `skills/distill-to-rutters/blueprints/instructions-design-implementation.yaml`
- Modify: `skills/distill-to-rutters/blueprints/gateway.yaml`
- Modify: `skills/distill-to-rutters/blueprint.yaml`
- Modify: `skills/distill-to-rutters/SKILL.md`
- Modify: `skills/distill-to-rutters/tests/test_distill_to_rutters_routing.py`
- Modify: `skills/distill-to-rutters/tests/test_runtime_compatibility.py`

**Interfaces:**

- Consumes: `rutter.interface.dispenser@6` operation `help-compass`
- Produces: `using-compass.interface.default@13`

- [ ] **Step 1: Replace lifecycle-copy tests with thin-adapter tests**

Replace `test_authored_body_assigns_one_agent_to_each_dispensed_voyage` with:

```python
def test_authored_body_is_only_a_host_adapter() -> None:
    text = _normalized_body()

    assert "invoker-provided authorized `VoyageDispenser` process binding" in text
    assert "invoke `help-compass` on that exact binding" in text.lower()
    assert "pass only `{binding, voyage_id}`" in text.lower()
    assert "wait/ready/start" in text.lower()
    assert "every worker first invokes `help-compass`" in text.lower()
    assert "public-interface gap" in text
    assert "do not reconstruct the protocol" in text.lower()


def test_authored_body_does_not_duplicate_voyage_protocol() -> None:
    text = _authored_body()
    forbidden = (
        "`modes`",
        "`list`",
        "`initiate`",
        "`status`",
        "`validate`",
        "`advance`",
        "`release`",
        "terminal result",
        "responding_to",
    )

    assert not [token for token in forbidden if token in text]
```

Update blueprint expectations from skill/source/interface version 12 to 13 and dispenser dependency version 5 to 6. Test the canonical gateway fields and complete regenerated SKILL, not only the authored body.

- [ ] **Step 2: Run the skill tests and confirm failures**

Run:

```bash
pytest -q skills/using-compass/tests/test_using_compass_instructions.py
```

Expected: FAIL because the authored body and gateway still duplicate Voyage behavior and pin versions 12/5.

- [ ] **Step 3: Replace only the authored SKILL body**

Keep generated blocks intact until regeneration. Replace the text after `<!-- END BLUEPRINT INTERFACES -->` with:

```markdown
# Using Compass

`Use compass on <rutter-name>`.

Use the invoker-provided authorized `VoyageDispenser` process binding. If no
binding is supplied, report a public-interface gap and stop.

Invoke `help-compass` on that exact binding and follow the returned Compass
controller protocol. This must be the first call on the binding, before modes,
initialization, listing, or Voyage work. Reject a malformed or unknown protocol
version.

Reserve every required host agent and create them behind a WAIT/READY/START
barrier. If capacity or the barrier is unavailable, report a public-interface
gap, preserve any returned Voyage IDs, and start no Voyage work. Pass only
`{binding, voyage_id}` to each agent; every worker first invokes `help-compass`
on that binding and follows only its returned worker protocol.

Start all ready workers, wait for every protocol-defined final report, and
distinguish completed terminal results from safe nonterminal stops.

Do not reconstruct the protocol, inspect private implementation files, search
for alternate interfaces, or add Voyage behavior not returned by
`help-compass`.
```

- [ ] **Step 4: Update the canonical skill blueprints**

In `blueprints/gateway.yaml`:

- advance source/interface version 12 to 13;
- advance both `rutter.interface.dispenser` pins from 5 to 6;
- describe the source as loading the authoritative protocol and mapping abstract assignments to host agents;
- update consistency language so it references compliance with the returned protocol instead of restating lifecycle rules.

In `blueprint.yaml`:

- advance module version 12 to 13;
- change the module description to `Maps the Compass protocol returned by an authorized VoyageDispenser binding to independent host agents.`

- [ ] **Step 5: Regenerate and re-run the skill tests**

Update the gateway description, usage, consistency, effects, and direct-I/O text so none restates Voyage operations. Load and use `famulus:regenerate-blueprints` for `using-compass`; review its `/tmp` output and apply only the generated contract/interface block changes to `SKILL.md`. Then run:

```bash
pytest -q skills/using-compass/tests/test_using_compass_instructions.py
```

Expected: PASS.

Close the other live consumer in the same task: advance `distill-to-rutters.source.design-implementation` and its interface 1 → 2 while changing both `using-compass` pins 12 → 13; advance its gateway source/interface 1 → 2 and design dependency 1 → 2; advance its root module 1 → 2; update both routing and runtime-compatibility expected pins 12 → 13; regenerate `distill-to-rutters/SKILL.md`; and run `pytest -q skills/distill-to-rutters/tests/test_distill_to_rutters_routing.py skills/distill-to-rutters/tests/test_runtime_compatibility.py`.

- [ ] **Step 6: Prepare a review checkpoint**

Review only the Task 3 `using-compass` and `distill-to-rutters` files. Do not stage or commit; Tasks 1–5 close together.

### Task 4: Close the math-graph consumer version chain

**Files:**

- Modify: `skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py`
- Modify: `skills/math-dependency-graph/_rtx/blueprints/rtx-inventory-voyage-dispenser.yaml`
- Modify: `skills/math-dependency-graph/_rtx/blueprint.yaml`
- Modify: `skills/math-dependency-graph/blueprints/instructions-inventory-voyages.yaml`
- Modify: `skills/math-dependency-graph/instructions/inventory-voyages.md`
- Modify: `skills/math-dependency-graph/blueprints/gateway.yaml`
- Modify: `skills/math-dependency-graph/blueprint.yaml`
- Modify: `skills/math-dependency-graph/SKILL.md`

**Interfaces:**

- Consumes: `rutter.interface.dispenser@6`
- Consumes: `using-compass.interface.default@13`
- Produces: `math-dependency-graph._rtx.interface.inventory-voyage-dispenser@12`
- Produces: `math-dependency-graph.interface.inventory-voyages@12`

- [ ] **Step 1: Advance the failing contract assertions first**

In `_rtx/tests/test_blueprint_contract.py`, change expectations as follows:

```python
assert source["version"] == 12
assert source_interface["version"] == 12
assert instruction["version"] == 12
assert instruction_interface["version"] == 12
assert instruction["uses_interfaces"][0] == {
    "interface": "math-dependency-graph._rtx.interface.inventory-voyage-dispenser",
    "version": 12,
}
assert instruction["uses_interfaces"][1] == {
    "interface": "using-compass.interface.default",
    "version": 13,
}
```

Advance the expected private runtime module 85 to 86, gateway source 87 to 88, gateway default interface 79 to 80, and root module 112 to 113. Require namespace export version 86 and inventory-dispenser surface version 12. Require generated SKILL mappings to end in `@12` for inventory-voyages and the private dispenser.

Also update every live routing and dependency pin in this test file:

```python
# In all three _resolve_host_dispatch_metadata routing tests:
target_version=12

# In test_inventory_voyage_dispenser_declares_its_exact_dependencies:
("rutter.source.dispenser", 6)
{"interface": "rutter.interface.dispenser", "version": 6}
```

The four `target_version=11` occurrences are across three tests: one in implicit-default initialization, two in run-prefix routing, and one in forced-release routing. Both copies of `expected_interfaces` consume the same updated list object, so changing its dispenser entry updates the source-interface assertion too.

- [ ] **Step 2: Run the focused contract test and confirm all stale pins are visible**

Run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py
```

Expected: FAIL on the old 5/11/12 pins and old module/source versions.

- [ ] **Step 3: Update the inventory dispenser blueprint**

In `rtx-inventory-voyage-dispenser.yaml`:

- advance source/interface version 11 to 12;
- advance `rutter.source.dispenser` dependency 5 to 6;
- advance both `rutter.interface.dispenser` uses from 5 to 6;
- change usage prefix to `<help|help-compass|modes>`;
- change the discovery process-binding regex from `^(help|modes)$` to `^(help|help-compass|modes)$`.

Do not change `_voyage_dispenser.py` or `_voyage_support.py`; the generic CLI supplies the new command.

- [ ] **Step 4: Update the instruction and module pins**

Apply this exact version closure:

- `_rtx/blueprint.yaml`: module 85 → 86; exported inventory dispenser source resolves to interface 12.
- `blueprints/instructions-inventory-voyages.yaml`: source/interface 11 → 12; private dispenser 11 → 12; `using-compass` 12 → 13.
- `instructions/inventory-voyages.md`: replace its one-line body with the exact text below so the binding is unambiguous before `help-compass` can run:

```markdown
Load `using-compass` and apply it to the invoker-supplied process binding
`math-dependency-graph._rtx.interface.inventory-voyage-dispenser@12`.
That exact export is the authorized binding. Do not resolve, substitute, or
search for an alias or source interface.
```
- `blueprints/gateway.yaml`: source 87 → 88; default interface 79 → 80; inventory-voyages dependency 11 → 12.
- `blueprint.yaml`: module 112 → 113; namespace `_rtx` 85 → 86; inventory-dispenser surface 11 → 12; inventory-voyages source/export pins 11 → 12.

- [ ] **Step 5: Record the required generated mappings and defer regeneration**

Do not regenerate yet because version 12 also includes Task 5's observable diagnostic behavior. Record that the final generated block must contain:

```text
math-dependency-graph.source.gateway -> math-dependency-graph.interface.inventory-voyages@12
math-dependency-graph.source.instructions-inventory-voyages -> math-dependency-graph._rtx.interface.inventory-voyage-dispenser@12
```

- [ ] **Step 6: Run the focused CLI consumer test**

Run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py -k 'help or modes or initiate or list'
```

Expected: PASS. Add exact routing and CLI assertions for `help-compass` through `math-dependency-graph._rtx.interface.inventory-voyage-dispenser@12`; defer the blueprint-contract test until Task 5 regenerates the final version-12 SKILL blocks.

- [ ] **Step 7: Prepare a review checkpoint**

Review the Task 4 blueprint and instruction edits provisionally, then proceed directly to Task 5. Version 12 is incomplete until Task 5 passes; do not regenerate final SKILL blocks, stage, commit, or describe Task 4 as complete on its own.

### Task 5: Return public diagnostics for unavailable or malformed worker inventory

**Files:**

- Modify: `skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py`
- Modify: `skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_support.py`

**Interfaces:**

- Preserves: the worker creates and atomically updates `inventory_file`
- Produces: invalid `ValidationReport` issue `invalid-inventory` with message `worker inventory file is unavailable`

- [ ] **Step 1: Add a failing missing-file diagnostic test**

Add this test beside the existing worker-file validation tests, plus parameterized invalid-JSON and wrong-shape cases. Every case must return `invalid-inventory`, preserve Voyage state, and leave the worker file uncreated or unchanged:

```python
def test_missing_worker_inventory_file_returns_actionable_validation_issue(
    inventory_run: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        doc_entrypoint=str(doc_entrypoint), chunk_count="1"
    )
    introduction = inventory_dispenser.get_status(voyage_id)
    inventory_dispenser.advance(
        voyage_id,
        {"outcome": "ready"},
        responding_to=introduction.current_evolution.evolution_entry_id,
    )
    report = _advance_to_message(inventory_dispenser, voyage_id)

    validation = inventory_dispenser.validate(
        voyage_id,
        _empty_inventory(),
        responding_to=report.current_evolution.evolution_entry_id,
    )

    assert not validation.valid
    assert validation.issues[0].path == ("inventory",)
    assert validation.issues[0].code == "invalid-inventory"
    assert validation.issues[0].message == "worker inventory file is unavailable"
    assert not _worker_inventory_path(run_dir, voyage_id).exists()
```

- [ ] **Step 2: Run the test and confirm the current opaque failure**

Run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py::test_missing_worker_inventory_file_returns_actionable_validation_issue
```

Expected: FAIL because `RutterDefinitionError("worker inventory file is unavailable")` escapes `assess_report` and the dispenser reports generic contextual-validation failure.

- [ ] **Step 3: Translate the definition error at the inventory assessment boundary**

In `_voyage_support.py`, isolate worker-file loading from the existing assessment block exactly as follows:

```python
def assess_report(context: LLMResponseContext) -> ValidationReport:
    """Validate the worker-owned file and its newly appended diagnostic records."""

    packet_count = len(context.evolution.history.turns(_REPORT_EVOLUTION)) + 1
    try:
        worker_inventory = _read_inventory_file(context.evolution)
    except (RutterDefinitionError, ValueError) as error:
        return _invalid(("inventory",), "invalid-inventory", str(error))
    try:
        _canonicalize_inventory_file(
            context.evolution, worker_inventory, packet_count=packet_count
        )
        prior_counts = _prior_counts(context.evolution)
        for field in ("nodes", "edges", "gaps"):
            records = worker_inventory.get(field)
            reported = context.response.get(field)
            if not isinstance(records, list) or not isinstance(
                reported, (list, tuple)
            ):
                raise ValueError(f"inventory {field} are invalid")
            prior_count = prior_counts[field]
            if len(records) < prior_count:
                raise ValueError(f"inventory file dropped prior {field}")
            if _plain_json(reported) != records[prior_count:]:
                raise ValueError(
                    f"reported {field} must equal the new inventory-file suffix"
                )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        return _invalid(("inventory",), "invalid-inventory", str(error))
    return ValidationReport(True)
```

Replace the current function with this exact body. The first catch covers only worker-file loading; do not catch `RutterDefinitionError` from canonicalization or prior-history invariants, and do not create or initialize `inventory_file` in transport or machine code.

- [ ] **Step 4: Run focused diagnostic and ownership tests**

Run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py -k 'worker_inventory_file or worker_owns_cumulative_inventory_file'
```

Expected: PASS.

- [ ] **Step 5: Prepare a separate review checkpoint**

Review the two Task 5 files together with every Task 4 version and consumer-pin file. After the diagnostic tests pass, load and use `famulus:regenerate-blueprints` for `math-dependency-graph`, review its `/tmp` output, and apply only the generated contract/interface block changes to `SKILL.md`. Then run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py
pytest -q skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py -k 'help or modes or initiate or list or worker_inventory_file or worker_owns_cumulative_inventory_file'
```

Expected: PASS with version 12 covering both `help-compass` and the public worker-file diagnostics. Review every Task 1–5 path as one change. Do not stage or commit without explicit authorization; if later authorized, stage only those exact session-owned paths in one commit.

### Task 6: Replay the `quicktest` scenario with the weak-model tier

**Files:**

- Read: `~/.codex/sessions/2026/08/25/rollout-2026-08-25T20-07-50-01a03b65-0364-7d40-a7e3-bc50d041d6bb.jsonl`
- Generate outside repository: the new session transcript selected from `~/.codex/sessions/` by its session metadata ID

**Interfaces:**

- Consumes: completed Tasks 1–5 installed in the development Codex context
- Produces: one live behavioral acceptance record using `gpt-5.4-mini`

- [ ] **Step 1: Start a fresh development Codex VS Code session**

First load `famulus:install-assistant-tools` and select development mode with the exact target checkout. Run its dry run, obtain explicit mutation approval, apply once, follow its environment-reload instruction, and run its development diagnosis. Stop as `HARNESS_INVALID` unless pointer, source, runtime, command origin, and repository configuration all resolve to the target SHA. Then use the same worktree and model recorded by the original session metadata:

```text
cwd: repository root of the target worktree
source: codex_vscode
model: gpt-5.4-mini
```

Before sending the scenario prompt, run this exact non-mutating preflight from the worktree:

```bash
dispatcher --caller-skill math-dependency-graph --dry-run math-dependency-graph._rtx.interface.inventory-voyage-dispenser@12 help-compass
dispatcher --caller-skill math-dependency-graph math-dependency-graph._rtx.interface.inventory-voyage-dispenser@12 help-compass
```

The dry run must compile an argv ending in `help-compass`. The live read must return protocol version 1, every operation argv template, controller sequence, assignment schema, worker branches, and final-report schema. Record exact Git SHA/diff, repository configuration and resolved dispatcher provenance, CLI/model versions, protocol checksum, input hash, available agent capacity, and main session ID. If any preflight fact is absent or mismatched, classify the attempt `HARNESS_INVALID` and do not try aliases or private launchers.

- [ ] **Step 2: Send the original prompt with repository-relative paths and an isolated run prefix**

```text
follow
skills/math-dependency-graph/instructions/inventory-voyages.md
Arguments:
\- mode: default
\- run prefix: spark3-replay-<unique-attempt-id>
\- document entrypoint: skills/math-dependency-graph/assets/inference-from-random-restarts/appendix.tex
\- chunk count: 2
```

Generate and record a unique attempt ID, verify its prefix has no existing run, and use it exactly once. Use contextless workers and no corrective follow-ups; if the host cannot disable inherited lifecycle guidance for workers, classify the harness invalid.

- [ ] **Step 3: Evaluate the generated transcript against exact pass criteria**

Record the controller and every child session path, spawn prompt, and agent-to-Voyage mapping. The scenario passes only if all of these are visible across those transcripts:

1. The controller's first dispatcher call on the exact injected inventory export is `help-compass`; it tries no alias or source-interface spelling and has no inherited lifecycle guidance.
2. The controller invokes `help-compass`; each contextless worker receives only `{binding, voyage_id}` and invokes `help-compass` before its first Voyage operation.
3. Two workers enter READY before one shared START and before either operates its Voyage.
4. The controller invokes no per-Voyage `status`, `validate`, `advance`, or `release` operation.
5. Every Message mutation uses one `advance --response-file <file> --responding-to <current-evolution-entry-id>` invocation. That route must visibly carry both arguments; its implementation loads the response once, validates it, and advances the exact loaded value in the same invocation.
6. Neither worker reads private implementation files or guesses after a generic validation error.
7. For each worker, ordered evidence shows terminal status, a terminal-evidence message, controller validation/persistence plus matching-digest receipt, release only after that receipt, successful release, and a final report matching the acknowledged result.

Record one result per criterion. A defined nonterminal stop may pass `safe_stop_conformance` but fails `scenario_success`; overall PASS requires all seven criteria. Distinguish `PASS`, `PRODUCT_FAIL`, and `HARNESS_INVALID`.

Run `pytest -q src/officina/rutter/tests/test_rutter_dispenser.py::test_compass_protocol_negative_host_cases` and retain its case table as replay evidence. Each case must attempt no alias/private fallback, perform no unauthorized Voyage operation, preserve and report any created Voyage IDs, and never force-release a nonterminal Voyage.

- [ ] **Step 4: Compare against the original failure modes**

Report separately whether the revision fixed:

```text
binding substitution attempts
controller operating Voyages directly
missing response/responding_to correlation
missing inventory-file diagnostic
private implementation inspection
second Voyage left untouched
```

If criteria 2–4 fail while public help is complete, classify the residual defect as host/model compliance rather than adding Rutter details to `using-compass`.

### Task 7: Verify the closed change without absorbing unrelated work

**Files:**

- Verify: all files listed in Tasks 1–5
- Preserve every pre-existing unrelated path reported by the initial `git status`, especially installation, recurring-task, documentation, and managed-runtime work.

**Interfaces:**

- Consumes: completed Tasks 1–6
- Produces: verified `help-compass` behavior, actionable inventory diagnostics, closed blueprint dependency graph, and one weak-model replay result

- [ ] **Step 1: Run the complete focused test set**

Run:

```bash
pytest -q src/officina/rutter/tests skills/using-compass/tests skills/distill-to-rutters/tests/test_distill_to_rutters_routing.py skills/distill-to-rutters/tests/test_runtime_compatibility.py skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py
```

Expected: PASS.

- [ ] **Step 2: Run generated-contract and repository validators**

Run the public regeneration/check routes required by `famulus:regenerate-blueprints`, then run:

```bash
./repo_checks.py --suite validators
```

Expected: PASS. If the sandbox blocks a validator, report the exact sandbox failure separately from product results.

- [ ] **Step 3: Search for stale public versions and duplicated Compass protocol**

Run:

```bash
rg -n 'status|validate|advance|release|terminal result|responding_to' skills/using-compass/SKILL.md
```

Expected: the lifecycle search is empty. Use the structured blueprint graph validator and the focused Rutter, math-graph, and distill contract tests—not regex—to prove that no live consumer retains the old pins.

- [ ] **Step 4: Check whitespace and exact ownership**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. Every pre-existing unrelated path remains untouched; session-owned implementation changes are limited to the plan's Rutter, Compass, distill, and math-graph files.

- [ ] **Step 5: Perform the plan self-review**

Confirm:

- every Design Contract statement maps to a passing test;
- `help-compass` is structured JSON;
- `using-compass` contains no Voyage lifecycle copy;
- `instructions/inventory.md` and worker ownership are unchanged;
- missing, invalid-JSON, and wrong-shape worker inventory files produce an actionable `invalid-inventory` issue without state mutation;
- all interface versions and consumer pins form one closed dependency chain;
- the `gpt-5.4-mini` replay has actor-attributed evidence, explicit results for every Task 6 criterion, and separate scenario-success and safe-stop verdicts;
- every implementation instruction names an exact file, symbol, value, or command.

- [ ] **Step 6: Stop at a verified checkpoint**

Do not stage or commit without explicit authorization. Report focused test results, validator results, the replay matrix, exact changed paths, and any unrelated failures separately. Regardless of outcome, report only that this one controlled replay passed or failed; do not claim prevention or a reduced failure probability.
