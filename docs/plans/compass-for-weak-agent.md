# Help-Compass Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the generic controller/worker Voyage protocol out of `using-compass`, expose it as authoritative structured `help-compass` data on every `VoyageDispenser` process binding, and pass one controlled `gpt-5.4-mini` reproduction of the `quicktest` scenario.

**Architecture:** `rutter.source.dispenser` owns one versioned, application-independent Compass protocol and returns it as finite JSON from `help-compass`. `using-compass` becomes a thin host adapter: it invokes `help-compass` on the exact supplied binding, maps the returned independent-worker assignments to host agents, and refuses when the binding or agent capacity is absent. This instruction surface cannot enforce host-agent spawning, so a live weak-model replay is an explicit acceptance gate. A separate inventory-owned change makes a missing worker file a public validation issue without changing worker ownership, packet semantics, or Voyage state transitions.

**Tech Stack:** Python 3.11+, `argparse`, JSON, pytest, Officina schema-v6 blueprints, generated SKILL contract blocks.

**Spec:** This document, especially “Design Contract” and “Global Constraints” below.

## Design Contract

The new process operation is exactly `help-compass`. It returns one top-level `compass_protocol` object with `version`, `controller`, `assignment`, `worker`, and `stop_conditions`. Ordinary `help` remains CLI-oriented and no longer owns the one-agent-per-Voyage instructions. For a `Message`, the protocol identifies the current `evolution_entry_id` as `responding_to`, validates a response with that pair, and advances with the exact same response and `responding_to` value. Response-free advance is permitted only for automatic `MachineInstruction` work.

`using-compass` retains only four responsibilities: require one invoker-supplied binding, invoke `help-compass` on that exact binding, map abstract independent workers to the current host's agents, and fail closed when the binding or sufficient agent capacity is absent. After activation it waits for every worker and collects either a retained terminal result or a defined stop condition for every Voyage before returning. It must not duplicate `modes`, `list`, `initiate`, `status`, `validate`, `advance`, `release`, terminal handling, or release rules. These instructions centralize protocol ownership and reduce duplication; they do not claim to enforce model compliance.

## Global Constraints

- Do not change `instructions/inventory.md`, inventory schemas, packet semantics, worker-owned file semantics, or Voyage transitions.
- The only inventory validation change is to translate `RutterDefinitionError` from worker-file reading into a structured `invalid-inventory` issue; transport must not create the worker-owned file.
- Do not add controller identity, worker leases, authentication, or new Rutter core state.
- `help-compass` must be generic and identical for every `VoyageDispenser`; applications cannot override it.
- The protocol must be structured JSON, not a prose blob.
- `using-compass` must never search for alternate interfaces or inspect private implementation when the supplied binding fails.
- Preserve all unrelated dirty changes, particularly installer/runtime files already modified in this worktree.
- Public interface and consumer pins must be updated as one closed versioned dependency change.
- Tasks 4 and 5 are one atomic inventory-dispenser version-11 unit: do not regenerate, review as complete, stage, or commit Task 4 without the Task 5 diagnostic behavior.
- Use repository skills for blueprint regeneration; do not invoke private blueprint scripts directly.
- Do not commit unless the user explicitly authorizes commits.

---

## File Map

- `src/officina/rutter/dispenser.py`: owns the generic protocol value, the `help_compass()` public method, parser registration, and CLI JSON projection.
- `src/officina/rutter/tests/test_rutter_dispenser.py`: specifies ordinary `help` versus structured `help-compass` behavior.
- `src/officina/rutter/blueprints/dispenser.yaml`: declares the new operation and dispenser interface version 5.
- `src/officina/rutter/blueprint.yaml`: advances the Rutter module version and exports the updated dispenser source.
- `src/officina/rutter/tests/test_blueprint_contract.py`: verifies the public interface/version closure.
- `skills/using-compass/SKILL.md`: becomes the thin host adapter.
- `skills/using-compass/blueprints/gateway.yaml`: advances the skill source/interface to version 12 and consumes dispenser interface version 5.
- `skills/using-compass/blueprint.yaml`: advances the skill module to version 12.
- `skills/using-compass/tests/test_using_compass_instructions.py`: prevents Voyage lifecycle details from returning to the skill.
- `skills/math-dependency-graph/_rtx/blueprints/rtx-inventory-voyage-dispenser.yaml`: admits `help-compass`, consumes dispenser interface 5, and advances the inventory dispenser interface to 11.
- `skills/math-dependency-graph/_rtx/blueprint.yaml`: advances the private runtime module and re-exports inventory dispenser interface 11.
- `skills/math-dependency-graph/blueprints/instructions-inventory-voyages.yaml`: consumes inventory dispenser 11 and `using-compass` 12; advances to 11.
- `skills/math-dependency-graph/instructions/inventory-voyages.md`: names the exact injected binding and forbids alias or source-interface substitution.
- `skills/math-dependency-graph/blueprints/gateway.yaml`: advances its dependency on `inventory-voyages` and its source/interface versions.
- `skills/math-dependency-graph/blueprint.yaml`: advances the module/namespace versions and exported pins.
- `skills/math-dependency-graph/SKILL.md`: receives regenerated contract/interface blocks only.
- `skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py`: verifies the complete consumer pin closure.
- `skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_support.py`: translates an unavailable or malformed worker inventory file into a public validation issue.
- `skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py`: specifies the missing-file diagnostic without weakening worker ownership.
- `~/.codex/sessions/`: contains the live `gpt-5.4-mini` replay transcript generated by the host; it is never added to the repository.

### Task 1: Expose the structured `help-compass` operation

**Files:**

- Modify: `src/officina/rutter/tests/test_rutter_dispenser.py`
- Modify: `src/officina/rutter/dispenser.py`

**Interfaces:**

- Produces: `VoyageDispenser.help_compass() -> dict[str, object]`
- Produces: CLI operation `help-compass` with output `{"compass_protocol": <protocol>}`
- Preserves: existing `VoyageDispenser.help() -> str`

- [ ] **Step 1: Replace the existing mixed-help test with separate CLI and Compass tests**

Replace `test_cli_help_explains_one_agent_per_voyage` with these two tests:

```python
def test_cli_help_describes_commands_without_owning_compass_protocol(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dispenser = _dispenser(tmp_path)

    assert voyage_dispenser_cli(dispenser, ["help"]) == 0
    help_text = json.loads(capsys.readouterr().out)["help"]

    assert "modes" in help_text
    assert "list" in help_text
    assert "initiate" in help_text
    assert "status" in help_text
    assert "validate" in help_text
    assert "advance" in help_text
    assert "release" in help_text
    assert "one agent per Voyage" not in help_text


def test_cli_help_compass_returns_authoritative_structured_protocol(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dispenser = _dispenser(tmp_path)

    assert voyage_dispenser_cli(dispenser, ["help-compass"]) == 0
    protocol = json.loads(capsys.readouterr().out)["compass_protocol"]

    assert protocol == {
        "version": 1,
        "controller": {
            "commands": ["modes", "list", "initiate"],
            "uses_exact_supplied_binding": True,
            "may_operate_voyage": False,
            "wait_for_all_workers": True,
            "collect_per_voyage": "terminal-result-or-defined-stop-condition",
        },
        "assignment": {
            "workers": "one-independent-agent-per-voyage",
            "exclusive": True,
            "activate_after_all_assigned": True,
            "controller_may_substitute": False,
        },
        "worker": {
            "commands": ["status", "validate", "advance", "release"],
            "uses_only_assigned_voyage_id": True,
            "message_response": {
                "responding_to": "current-evolution-entry-id",
                "validate_with": ["response", "responding_to"],
                "advance_with": [
                    "exact-validated-response",
                    "same-responding_to",
                ],
            },
            "response_free_advance": "automatic-machine-instruction-only",
            "loop": [
                "read-status",
                "perform-ready-message-and-required-effects",
                "validate-response-with-current-entry-id",
                "advance-with-exact-validated-response-and-same-entry-id",
                "advance-response-free-only-for-automatic-work",
                "read-fresh-status",
            ],
        },
        "stop_conditions": [
            "terminal",
            "fault",
            "uncertain",
            "malformed",
            "unknown",
            "insufficient-validation-detail",
        ],
        "release": "after-retaining-terminal-result",
        "prohibitions": [
            "controller-operating-a-voyage",
            "worker-switching-voyage-id",
            "private-interface-inspection",
            "guessing-from-generic-errors",
        ],
    }
```

- [ ] **Step 2: Run the focused tests and confirm the new command fails**

Run:

```bash
pytest -q src/officina/rutter/tests/test_rutter_dispenser.py -k 'help'
```

Expected: the ordinary-help assertion fails because it still contains the Compass workflow, and `help-compass` fails as an invalid command.

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

Add this public method to `VoyageDispenser`:

```python
def help_compass(self) -> dict[str, object]:
    """Return the generic Compass controller and worker protocol."""

    return {
        "version": 1,
        "controller": {
            "commands": ["modes", "list", "initiate"],
            "uses_exact_supplied_binding": True,
            "may_operate_voyage": False,
            "wait_for_all_workers": True,
            "collect_per_voyage": "terminal-result-or-defined-stop-condition",
        },
        "assignment": {
            "workers": "one-independent-agent-per-voyage",
            "exclusive": True,
            "activate_after_all_assigned": True,
            "controller_may_substitute": False,
        },
        "worker": {
            "commands": ["status", "validate", "advance", "release"],
            "uses_only_assigned_voyage_id": True,
            "message_response": {
                "responding_to": "current-evolution-entry-id",
                "validate_with": ["response", "responding_to"],
                "advance_with": [
                    "exact-validated-response",
                    "same-responding_to",
                ],
            },
            "response_free_advance": "automatic-machine-instruction-only",
            "loop": [
                "read-status",
                "perform-ready-message-and-required-effects",
                "validate-response-with-current-entry-id",
                "advance-with-exact-validated-response-and-same-entry-id",
                "advance-response-free-only-for-automatic-work",
                "read-fresh-status",
            ],
        },
        "stop_conditions": [
            "terminal",
            "fault",
            "uncertain",
            "malformed",
            "unknown",
            "insufficient-validation-detail",
        ],
        "release": "after-retaining-terminal-result",
        "prohibitions": [
            "controller-operating-a-voyage",
            "worker-switching-voyage-id",
            "private-interface-inspection",
            "guessing-from-generic-errors",
        ],
    }
```

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

Do not commit without explicit authorization. If authorized, stage exactly those two files and use commit message `feat(rutter): expose help-compass protocol`.

### Task 2: Version and document the dispenser interface

**Files:**

- Modify: `src/officina/rutter/blueprints/dispenser.yaml`
- Modify: `src/officina/rutter/blueprint.yaml`
- Modify: `src/officina/rutter/tests/test_blueprint_contract.py`

**Interfaces:**

- Consumes: `VoyageDispenser.help_compass() -> dict[str, object]`
- Produces: `rutter.source.dispenser.interface.python-api@5`
- Produces: exported `rutter.interface.dispenser@5`

- [ ] **Step 1: Add failing blueprint-contract expectations**

In `test_blueprint_contract.py`, update the dispenser assertions to require:

```python
assert dispenser_source["version"] == 5
assert dispenser_source["interfaces"][
    "rutter.source.dispenser.interface.python-api"
]["version"] == 5
operations = {
    value["value"]
    for value in dispenser_source["interfaces"][
        "rutter.source.dispenser.interface.python-api"
    ]["contract"]["arguments"]["operation"]["type"]["values"]
}
assert "help-compass" in operations
```

Also advance the expected Rutter module version from 9 to 10.

- [ ] **Step 2: Run the focused contract test and confirm version failures**

Run:

```bash
pytest -q src/officina/rutter/tests/test_blueprint_contract.py
```

Expected: FAIL on dispenser source/interface version 4 and missing `help-compass` operation.

- [ ] **Step 3: Update the source blueprint**

In `blueprints/dispenser.yaml`:

- change the source version from 4 to 5;
- change `rutter.source.dispenser.interface.python-api` from 4 to 5;
- add operation enum value `help-compass` with description `Return the authoritative structured Compass controller and worker protocol`;
- add `help-compass` to the CLI operation description;
- update the source/interface descriptions to distinguish ordinary help from Compass protocol discovery.

In `src/officina/rutter/blueprint.yaml`, change module version 9 to 10. Do not change access policy or add a new exported interface; `rutter.interface.dispenser` remains the export.

- [ ] **Step 4: Run the focused blueprint contract test**

Run:

```bash
pytest -q src/officina/rutter/tests/test_blueprint_contract.py
```

Expected: PASS after consumer expectations are updated in later tasks; if it fails only on still-pinned consumers, record those exact pins and proceed to Tasks 3–4 without weakening assertions.

- [ ] **Step 5: Prepare a review checkpoint**

Review only the three Task 2 files. Do not commit without explicit authorization. If authorized, combine Tasks 1–2 in one commit because the public code and its interface declaration are one reviewable unit.

### Task 3: Reduce `using-compass` to a thin host adapter

**Files:**

- Modify: `skills/using-compass/tests/test_using_compass_instructions.py`
- Modify: `skills/using-compass/SKILL.md`
- Modify: `skills/using-compass/blueprints/gateway.yaml`
- Modify: `skills/using-compass/blueprint.yaml`

**Interfaces:**

- Consumes: `rutter.interface.dispenser@5` operation `help-compass`
- Produces: `using-compass.interface.default@12`

- [ ] **Step 1: Replace lifecycle-copy tests with thin-adapter tests**

Replace `test_authored_body_assigns_one_agent_to_each_dispensed_voyage` with:

```python
def test_authored_body_loads_compass_protocol_from_exact_binding() -> None:
    text = _normalized_body()

    assert "invoker-provided authorized `VoyageDispenser` process binding" in text
    assert "invoke `help-compass` on that exact binding" in text.lower()
    assert "map each independent-worker assignment" in text.lower()
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

Update blueprint expectations from skill/source/interface version 11 to 12 and dispenser dependency version 4 to 5.

- [ ] **Step 2: Run the skill tests and confirm failures**

Run:

```bash
pytest -q skills/using-compass/tests/test_using_compass_instructions.py
```

Expected: FAIL because the authored body still duplicates the Voyage lifecycle and blueprints still pin versions 11/4.

- [ ] **Step 3: Replace only the authored SKILL body**

Keep generated blocks intact until regeneration. Replace the text after `<!-- END BLUEPRINT INTERFACES -->` with:

```markdown
# Using Compass

`Use compass on <rutter-name>`.

Use the invoker-provided authorized `VoyageDispenser` process binding. If no
binding is supplied, report a public-interface gap and stop.

Invoke `help-compass` on that exact binding and follow the returned Compass
protocol exactly.

Map each independent-worker assignment in that protocol to an independent agent
provided by the current host. If the host cannot supply every required agent,
report a public-interface gap and stop.

Do not reconstruct the protocol, inspect private implementation files, search
for alternate interfaces, or add Voyage behavior not returned by
`help-compass`.
```

- [ ] **Step 4: Update the canonical skill blueprints**

In `blueprints/gateway.yaml`:

- advance source/interface version 11 to 12;
- advance both `rutter.interface.dispenser` pins from 4 to 5;
- describe the source as loading the authoritative protocol and mapping abstract assignments to host agents;
- update consistency language so it references compliance with the returned protocol instead of restating lifecycle rules.

In `blueprint.yaml`:

- advance module version 11 to 12;
- change the module description to `Maps the Compass protocol returned by an authorized VoyageDispenser binding to independent host agents.`

- [ ] **Step 5: Regenerate and re-run the skill tests**

Load and use `famulus:regenerate-blueprints` for `using-compass`; review its `/tmp` output and apply only the generated contract/interface block changes to `SKILL.md`. Then run:

```bash
pytest -q skills/using-compass/tests/test_using_compass_instructions.py
```

Expected: PASS.

- [ ] **Step 6: Prepare a review checkpoint**

Review only the four Task 3 files. Do not commit without explicit authorization. If authorized, use commit message `refactor(compass): load voyage protocol from binding`.

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

- Consumes: `rutter.interface.dispenser@5`
- Consumes: `using-compass.interface.default@12`
- Produces: `math-dependency-graph._rtx.interface.inventory-voyage-dispenser@11`
- Produces: `math-dependency-graph.interface.inventory-voyages@11`

- [ ] **Step 1: Advance the failing contract assertions first**

In `_rtx/tests/test_blueprint_contract.py`, change expectations as follows:

```python
assert source["version"] == 11
assert source_interface["version"] == 11
assert instruction["version"] == 11
assert instruction_interface["version"] == 11
assert instruction["uses_interfaces"][0] == {
    "interface": "math-dependency-graph._rtx.interface.inventory-voyage-dispenser",
    "version": 11,
}
assert instruction["uses_interfaces"][1] == {
    "interface": "using-compass.interface.default",
    "version": 12,
}
```

Advance the expected private runtime module 84 to 85, gateway source 86 to 87, gateway default interface 78 to 79, and root module 111 to 112. Require namespace export version 85 and inventory-dispenser surface version 11. Require generated SKILL mappings to end in `@11` for inventory-voyages and `@11` for the private dispenser.

Also update every live routing and dependency pin in this test file:

```python
# In all three _resolve_host_dispatch_metadata routing tests:
target_version=11

# In test_inventory_voyage_dispenser_declares_its_exact_dependencies:
("rutter.source.dispenser", 5)
{"interface": "rutter.interface.dispenser", "version": 5}
```

The four `target_version=10` occurrences are across three tests: one in implicit-default initialization, two in run-prefix routing, and one in forced-release routing. Both copies of `expected_interfaces` consume the same updated list object, so changing its dispenser entry updates the source-interface assertion too.

- [ ] **Step 2: Run the focused contract test and confirm all stale pins are visible**

Run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py
```

Expected: FAIL on the old 4/10/11 pins and old module/source versions.

- [ ] **Step 3: Update the inventory dispenser blueprint**

In `rtx-inventory-voyage-dispenser.yaml`:

- advance source/interface version 10 to 11;
- advance `rutter.source.dispenser` dependency 4 to 5;
- advance both `rutter.interface.dispenser` uses from 4 to 5;
- change usage prefix to `<help|help-compass|modes>`;
- change the discovery process-binding regex from `^(help|modes)$` to `^(help|help-compass|modes)$`.

Do not change `_voyage_dispenser.py` or `_voyage_support.py`; the generic CLI supplies the new command.

- [ ] **Step 4: Update the instruction and module pins**

Apply this exact version closure:

- `_rtx/blueprint.yaml`: module 84 → 85; exported inventory dispenser source resolves to interface 11.
- `blueprints/instructions-inventory-voyages.yaml`: source/interface 10 → 11; private dispenser 10 → 11; `using-compass` 11 → 12.
- `instructions/inventory-voyages.md`: replace its one-line body with the exact text below so the binding is unambiguous before `help-compass` can run:

```markdown
Load `using-compass` and apply it to the invoker-supplied process binding
`math-dependency-graph._rtx.interface.inventory-voyage-dispenser@11`.
That exact export is the authorized binding. Do not resolve, substitute, or
search for an alias or source interface.
```
- `blueprints/gateway.yaml`: source 86 → 87; default interface 78 → 79; inventory-voyages dependency 10 → 11.
- `blueprint.yaml`: module 111 → 112; namespace `_rtx` 84 → 85; inventory-dispenser surface 10 → 11; inventory-voyages source/export pins 10 → 11.

- [ ] **Step 5: Record the required generated mappings and defer regeneration**

Do not regenerate yet because version 11 also includes Task 5's observable diagnostic behavior. Record that the final generated block must contain:

```text
math-dependency-graph.source.gateway -> math-dependency-graph.interface.inventory-voyages@11
math-dependency-graph.source.instructions-inventory-voyages -> math-dependency-graph._rtx.interface.inventory-voyage-dispenser@11
```

- [ ] **Step 6: Run the focused CLI consumer test**

Run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py -k 'help or modes or initiate or list'
```

Expected: PASS. Defer the blueprint-contract test until Task 5 regenerates the final version-11 SKILL blocks.

- [ ] **Step 7: Prepare a review checkpoint**

Review the Task 4 blueprint and instruction edits provisionally, then proceed directly to Task 5. Version 11 is incomplete until Task 5 passes; do not regenerate final SKILL blocks, stage, commit, or describe Task 4 as complete on its own.

### Task 5: Return a public diagnostic when the worker inventory file is absent

**Files:**

- Modify: `skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py`
- Modify: `skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_support.py`

**Interfaces:**

- Preserves: the worker creates and atomically updates `inventory_file`
- Produces: invalid `ValidationReport` issue `invalid-inventory` with message `worker inventory file is unavailable`

- [ ] **Step 1: Add a failing missing-file diagnostic test**

Add this test beside the existing worker-file validation tests:

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
    except RutterDefinitionError as error:
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

Replace the current function with this exact body. Do not catch `RutterDefinitionError` from canonicalization or prior-history invariants, and do not create or initialize `inventory_file` in transport or machine code.

- [ ] **Step 4: Run focused diagnostic and ownership tests**

Run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py -k 'missing_worker_inventory_file or worker_owns_cumulative_inventory_file'
```

Expected: PASS.

- [ ] **Step 5: Prepare a separate review checkpoint**

Review the two Task 5 files together with every Task 4 version and consumer-pin file. After the diagnostic tests pass, load and use `famulus:regenerate-blueprints` for `math-dependency-graph`, review its `/tmp` output, and apply only the generated contract/interface block changes to `SKILL.md`. Then run:

```bash
pytest -q skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py
pytest -q skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py -k 'help or modes or initiate or list or missing_worker_inventory_file or worker_owns_cumulative_inventory_file'
```

Expected: PASS with version 11 covering both `help-compass` and the public missing-file diagnostic. Do not commit without explicit authorization. If authorized, stage the exact Task 4–5 files together and use commit message `feat(math-graph): consume compass protocol with actionable validation`.

### Task 6: Replay the `quicktest` scenario with the weak-model tier

**Files:**

- Read: `~/.codex/sessions/2026/08/25/rollout-2026-08-25T20-07-50-01a03b65-0364-7d40-a7e3-bc50d041d6bb.jsonl`
- Generate outside repository: the new session transcript selected from `~/.codex/sessions/` by its session metadata ID

**Interfaces:**

- Consumes: completed Tasks 1–5 installed in the development Codex context
- Produces: one live behavioral acceptance record using `gpt-5.4-mini`

- [ ] **Step 1: Start a fresh development Codex VS Code session**

Use the same worktree and model recorded by the original session metadata:

```text
cwd: repository root of the target worktree
source: codex_vscode
model: gpt-5.4-mini
```

Before sending the scenario prompt, run this exact non-mutating preflight from the worktree:

```bash
dispatcher --caller-skill math-dependency-graph --dry-run math-dependency-graph._rtx.interface.inventory-voyage-dispenser@11 help-compass
dispatcher --caller-skill math-dependency-graph math-dependency-graph._rtx.interface.inventory-voyage-dispenser@11 help-compass
```

The dry run must exit zero and compile an argv ending in `help-compass`. The live read must exit zero and return `compass_protocol.version == 1`, `controller.uses_exact_supplied_binding == true`, and `controller.wait_for_all_workers == true`. Record both outputs with the replay evidence. Stop as a registry/binding failure if either command resolves another interface/version or returns `interface not found`; do not try aliases.

- [ ] **Step 2: Send the original prompt with repository-relative paths and an isolated run prefix**

```text
follow
skills/math-dependency-graph/instructions/inventory-voyages.md
Arguments:
\- mode: default
\- run prefix: spark3-replay
\- document entrypoint: skills/math-dependency-graph/assets/inference-from-random-restarts/appendix.tex
\- chunk count: 2
```

The original prefix was `spark3`; use `spark3-replay` only to prevent collision with persisted state. Do not add corrective follow-up messages during the replay.

- [ ] **Step 3: Evaluate the generated transcript against exact pass criteria**

The replay passes only if all of these are visible in the transcript:

1. The controller uses the exact injected inventory dispenser export without trying aliases or source-interface spellings.
2. The controller invokes `help-compass` before per-Voyage work.
3. Two worker agents are assigned, one to each Voyage, before either worker operates its Voyage.
4. The controller invokes no per-Voyage `status`, `validate`, `advance`, or `release` operation.
5. Every Message mutation uses one `advance --response-file <file> --responding-to <current-evolution-entry-id>` invocation. That route must visibly carry both arguments; its implementation loads the response once, validates it, and advances the exact loaded value in the same invocation.
6. Neither worker reads private implementation files or guesses after a generic validation error.
7. Both workers reach terminal completion and release, or return one of the structured protocol's defined stop conditions.

Record the new session path and a pass/fail result for each criterion. A failure is a product finding; do not reinterpret instruction-only architecture as enforcement.

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

If criteria 3 or 4 still fail while `help-compass` was read correctly, classify the residual defect as host/model compliance rather than adding inventory or VoyageDispenser details to `using-compass`.

### Task 7: Verify the closed change without absorbing unrelated work

**Files:**

- Verify: all files listed in Tasks 1–5
- Preserve untouched: `docs/dependency-and-bootstrap-audit.md`
- Preserve untouched: `docs/officina/installation.md`
- Preserve untouched: `skills/install-assistant-tools/SKILL.md`
- Preserve untouched: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Preserve untouched: `skills/install-assistant-tools/_rtx/blueprint.yaml`
- Preserve untouched: `skills/install-assistant-tools/_rtx/blueprints/rtx-install-scaffold.yaml`
- Preserve untouched: `skills/install-assistant-tools/_rtx/blueprints/rtx-phase-entry.yaml`
- Preserve untouched: `skills/install-assistant-tools/_rtx/tests/test_install.py`
- Preserve untouched: `skills/install-assistant-tools/blueprint.yaml`
- Preserve untouched: `skills/install-assistant-tools/blueprints/gateway.yaml`
- Preserve untouched: `skills/recurring-tasks/SKILL.md`
- Preserve untouched: `skills/recurring-tasks/blueprint.yaml`
- Preserve untouched: `skills/recurring-tasks/blueprints/gateway.yaml`
- Preserve untouched: `src/officina/install/blueprint.yaml`
- Preserve untouched: `src/officina/install/blueprints/managed-runtime.yaml`
- Preserve untouched: `src/officina/install/managed_runtime.py`
- Preserve untouched: `tests/test_officina_managed_runtime.py`

**Interfaces:**

- Consumes: completed Tasks 1–6
- Produces: verified `help-compass` behavior, actionable inventory diagnostics, closed blueprint dependency graph, and one weak-model replay result

- [ ] **Step 1: Run the complete focused test set**

Run:

```bash
pytest -q src/officina/rutter/tests skills/using-compass/tests skills/math-dependency-graph/_rtx/tests/test_blueprint_contract.py skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py
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
rg -n 'rutter\.interface\.dispenser.*version: 4|rutter\.interface\.dispenser@4|inventory-voyage-dispenser@10|using-compass\.interface\.default.*version: 11' src skills tests
rg -n 'status|validate|advance|release|terminal result|responding_to' skills/using-compass/SKILL.md
```

Expected: both searches return no stale pins or duplicated lifecycle terms. Blueprint history or changelog text is out of scope only if it is clearly historical rather than an active pin.

- [ ] **Step 4: Check whitespace and exact ownership**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. Every pre-existing documentation, installer, recurring-task, and managed-runtime path listed above remains present and untouched; session-owned implementation changes are limited to the plan's Rutter, Compass, and math-graph files.

- [ ] **Step 5: Perform the plan self-review**

Confirm:

- every Design Contract statement maps to a passing test;
- `help-compass` is structured JSON;
- `using-compass` contains no Voyage lifecycle copy;
- `instructions/inventory.md` and worker ownership are unchanged;
- missing worker inventory files produce an `invalid-inventory` issue rather than a generic contextual-validation error;
- all interface versions and consumer pins form one closed dependency chain;
- the `gpt-5.4-mini` replay has an explicit pass/fail result for every Task 6 criterion;
- every implementation instruction names an exact file, symbol, value, or command.

- [ ] **Step 6: Stop at a verified checkpoint**

Do not stage or commit without explicit authorization. Report focused test results, validator results, the replay matrix, exact changed paths, and any unrelated failures separately. Regardless of outcome, report only that this one controlled replay passed or failed; do not claim prevention or a reduced failure probability.
