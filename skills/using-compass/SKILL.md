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
- `using-compass.source.gateway -> rutter.interface.bound-operations@1`

Public Interfaces:
- `using-compass.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `using-compass.interface.default` — Operate one authorized bound Rutter by classifying every public continuation result before requesting one current string instruction.
<!-- END BLUEPRINT INTERFACES -->
# Using Compass

`Use compass on <rutter-name>`.

Use one invoker-provided bound Rutter instance supplied with the activation
request. If that binding is missing, report a public-interface gap and stop.
Do not search for construction authority.

## Operating loop

1. At binding, first call `advance(continue_=True)` to settle callable work and
   effect recovery. An `RutterValidationError` whose public report contains
   `input_required` means the current coordinate already requires an LLM
   result; continue to step 3. Do not call `get_instruction()` before this
   settling attempt. Never use `continue_=False` in the Compass loop.
2. After every successful `advance(...)`, consume its returned successor and
   inspect the bound Rutter's public `fix` before making another call:
   - For `fix.lifecycle == "complete"`, report the returned successor and
     terminal status, then stop.
   - For `fix.lifecycle == "faulted"`, report the public fault diagnostics,
     then stop.
   - For `fix.effect.disposition == "uncertain"`, stop for manual
     reconciliation and report its public recovery authority.
   - For `fix.lifecycle == "active"`, continue only from the returned
     successor and displayed public effect authority.
   - For any other authority, report a public-interface gap and stop.

   The first three branches call neither `advance()` nor `get_instruction()`
   again. Only the active branch permits another public call. If `advance(...)`
   raises `RutterStateError`, inspect the public `fix` once and apply the same
   complete, faulted, or uncertain branch. If none matches, report a
   public-interface gap and stop. Do not retry `advance()` or call
   `get_instruction()` after that exception.
3. Call `get_instruction()` only when the settling attempt reported
   `input_required` or step 2 left an active, settled string state. Interpret
   its public value:
   - For a string, perform exactly that one authorized instruction. Create,
     question, wait for, or close subagents only when this string explicitly
     authorizes those operations.
   - For `callable`, call `advance(continue_=True)` as the settling operation
     and classify its returned successor under step 2 without performing the
     callable work yourself.
   - For `effectful_callable`, call
     `advance(continue_=True)` as the settling operation and classify its
     returned successor under step 2 without performing the callable work
     yourself.
   - For `pending_effect`, follow its `authorized_operation` only when it names
     `advance(continue_=True)`, then classify its returned successor under
     step 2.
   - For `terminal`, report the terminal status and stop.
   - For `fault`, report its diagnostics and stop.
   - For `uncertain_effect`, stop for manual reconciliation. No public recovery
     transition is authorized.
   - For any other structured value, stop. Do not infer a transition; report a
     public-interface gap.
4. After performing a string instruction, construct finite JSON with exactly
   these three fields, using the displayed revision and a JSON object for
   evidence:

   ```json
   {
     "revision": <displayed integer>,
     "outcome": "<declared outcome or unexpected>",
     "evidence": {<finite JSON object>}
   }
   ```

5. Call `validate(result)`. If validation is invalid, do not advance; repair
   only from its public issues or report a public-interface gap.
6. Call `advance(result, continue_=True)`, classify the returned successor
   under step 2, and call `get_instruction()` only if that classification
   permits it.

`dry_run=True` only previews one supplied result edge and does not invoke
instructions or authorize effects. Never use dry run as permission to perform
work.

## Diagnose a mismatch

Use only the current instruction, validation report, structured status, and
public Charter, Fix, or Reckoning values. Do not inspect Rutter source or any
registry, codec, lock, or storage internals.

If no declared outcome fits, return the reserved `unexpected` outcome with
exactly this evidence object; all four values must be non-empty strings:

```json
{
  "observed": "what was observed",
  "conflict": "what declared behavior it conflicts with",
  "why_no_outcome_fits": "why every declared outcome is inapplicable",
  "uncertainty": "what remains unknown"
}
```

Do not guess a route. If those public values do not provide enough information,
report a public-interface gap and stop.
