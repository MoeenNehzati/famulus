---
name: using-compass
description: Use when a user or another skill directs the agent to use a named compass.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: task-automation, session-management; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 5

Uses Interfaces:
- `using-compass.source.gateway -> rutter.interface.bound-operations@3`

Public Interfaces:
- `using-compass.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `using-compass.interface.default` — Settle one authorized bound Rutter to its deepest active leaf, follow one two-part Message, and submit its Response through the sole advancing operation.
<!-- END BLUEPRINT INTERFACES -->
# Using Compass

`Use compass on <rutter-name>`.

Use one invoker-provided bound Rutter instance supplied with the activation
request. If that binding is missing, report a public-interface gap and stop.
The binding exposes only `get_instruction`, `validate`, `next`, and
`get_current_node`.

## Operating loop

1. First call `next(continue_=True)` to settle automatic work and resume the
   deepest active node. Do not call `get_instruction()` before this settling
   call. Rutter owns automatic Python work, hooks, diagnostics, nesting, and
   durable traversal; never execute an internal instruction and never
   manipulate child traversal.
2. Classify the returned node. If the initial response-free settling call
   raises `RutterValidationError` with `Prompt response is required`, this is
   the `response-required` boundary, not `invalid-input`, and does not return a
   `ValidationReport`. Call `get_current_node()` to obtain the unchanged
   immutable active-leaf view, then call `get_instruction()` and perform the
   LLM instruction. No later validation failure grants instruction authority;
   repair it through step 5 or report a public-interface gap.
   - For `ready`, proceed to step 3.
   - For `terminal`, report the terminal result and stop.
   - For `fault`, report the public fault and stop.
   - For `uncertain`, stop for manual reconciliation and report the public
     condition.

   Only `ready` permits `get_instruction()`. Any unrecognized condition is a
   public-interface gap and stops the loop. With continuation enabled, `next`
   returns only the final entered `NodeView`; durable history records every
   intermediate traversal. Do not reconstruct that path from conversation
   history.
3. Call `get_instruction()`. It must return one Message with exactly two
   top-level parts:

   ```json
   {
     "instructions": {"text": "...", "answer": {}},
     "data": {"state": {}, "payload": {}}
   }
   ```

   Perform exactly `instructions.text`. Use `instructions.answer` as the answer
   contract. Treat `data.state` as engine-owned identity and revision data and
   `data.payload` as the state-specific input. If the returned value is absent
   or has another shape, report a public-interface gap and stop.
4. Construct the finite JSON response required by `instructions.answer`, with
   the authoritative revision from `data.state`:

   ```json
   {
     "revision": 7,
     "outcome": "declared-outcome",
     "evidence": {}
   }
   ```

5. Call `validate(response)`. Validation is read-only. An invalid response
   leaves the current node unchanged; repair only from the returned public
   issues, then validate again. If the report cannot guide a valid repair,
   report a public-interface gap and stop.
6. Call `next(response, continue_=True)` only after validation succeeds, then
   repeat from step 2. Every LLM response enters traversal through this call.

`next(response, dry_run=True)` is an immediate parent-edge preview. It never
performs work or authorizes work. Do not use it in the normal Compass loop.
