# Compass and Rutter

Rutter owns durable algorithm traversal. Compass is the thin LLM-facing
operator for one invoker-provided bound Rutter.

The boundary is deliberate:

- Rutter owns node entry, validation, routing, automatic Python work, hooks,
  diagnostic children, nested Rutters, faults, recovery, and durable history.
- Compass settles automatic work, follows the current LLM Message, returns one
  finite Response, and stops at public stopping conditions.

Compass does not infer progress from the conversation, execute automatic work,
or manipulate nesting.

## Public operating interface

A bound Rutter exposes four operations:

```python
rutter.get_instruction()
rutter.validate(response)
rutter.next(response=MISSING, continue_=True, dry_run=False)
rutter.get_current_node()
```

`get_instruction()` is read-only. After automatic continuation settles, it
returns the exact Message stored for the active leaf. It returns no Message at
terminal, faulted, uncertain, or automatic work.

`validate(response)` is read-only. It checks the proposed Response against the
stored Message and contextual validator. Invalid input leaves the entered node
and durable history unchanged.

`next(...)` is the only advancing operation. It revalidates under the bound
Rutter's authority, records accepted work, enters the selected destination,
runs selected hooks, and settles child traversal.

`get_current_node()` is read-only. It returns an immutable view of the deepest
active node, including its public condition.

## Message and Response

Every LLM-facing Message has exactly two top-level parts:

```json
{
  "instructions": {
    "text": "State-specific invariant instructions",
    "answer": {}
  },
  "data": {
    "state": {"id": "report", "entry_id": "entry-...", "revision": 7},
    "payload": {"chunk": "..."}
  }
}
```

`instructions.text` says what Compass performs. `instructions.answer` defines
the answer contract. Rutter owns `data.state`; the active Prompt supplies
`data.payload`.

Compass returns:

```json
{
  "revision": 7,
  "outcome": "reported",
  "evidence": {}
}
```

The revision comes from `data.state`. Outcome and evidence must satisfy
`instructions.answer`. The exact delivered Message and accepted Response
remain part of the durable traversal authority.

## Compass operating loop

Compass uses one already-bound Rutter:

1. Call `next(continue_=True)` before reading an instruction. This resumes the
   deepest active node and settles automatic Python work, hooks, diagnostics,
   and nested traversal.
2. Classify the returned node. A ready node permits
   `get_instruction()`; terminal, fault, and uncertain conditions stop.
3. Read the Message, perform only `instructions.text`, and construct the finite
   Response required by `instructions.answer`.
4. Call `validate(response)`. Repair invalid input only from the returned
   public issues; the current node has not advanced.
5. Call `next(response, continue_=True)` after validation succeeds and repeat
   from the returned node.

If the initial response-free `next` reports that an LLM Response is required,
`get_current_node()` identifies the unchanged ready leaf. Compass may then read
its Message. No other validation failure grants advancing authority.

Compass never retrieves an instruction before the settling call, runs an
automatic instruction itself, manipulates a child traversal, or inspects
another progress coordinate.

## Continuation and stopping

With `continue_=True`, `next` continues through automatic Python and nested
work until it reaches an LLM Message, terminal node, fault, or uncertainty. It
returns only that final entered node. Durable history retains every
intermediate node and child traversal, so callers do not reconstruct the
hidden path from conversation history or expect it in the return value.

The public conditions are interpreted as follows:

| Condition | Compass action |
|---|---|
| `ready` | Read one Message and produce one Response. |
| `terminal` | Report the terminal result and stop. |
| `fault` | Report the public fault and stop. |
| `uncertain` | Stop for manual reconciliation. |

Any unsupported condition or malformed Message is a public-interface gap.
Compass stops instead of inventing a transition.

`next(..., continue_=False)` is a diagnostic stepping surface: it returns the
first node actually entered, which may be a child start. It is not part of the
normal Compass loop.

`next(response, dry_run=True)` is read-only and previews only the immediate
parent-edge destination. It does not enter a node, run automatic work, start a
child, or authorize the previewed work.

## Durable resume

The bound Rutter's durable authority, not session memory, selects the deepest
active leaf after an interaction restart. Compass repeats the same settling
call and continues from the returned public node. A continuation limit also
leaves the run anchored at its entered node, so a later call can resume without
creating a second workflow coordinate.
