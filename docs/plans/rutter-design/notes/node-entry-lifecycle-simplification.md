# Node-entry lifecycle simplification

Status: design direction to preserve; not a full design or implementation plan.

The only persisted **control state** is node entrance, identified by a unique
entrance ID even on self-loops. Validation, evaluation,
transition selection, and case execution are operations from that node, not
additional persisted states or phases.

```text
enter node and persist it
-> if it is an LLM node, persist its exact open request with the entrance
-> `get_instruction` returns that request and remains at the node
-> validation is read-only and remains at the node
-> reject an invalid/unacceptable response and remain at the node
-> on `next(response)`, record an accepted response and initiate transition
-> `then` chooses the destination
-> CaseMakers inspect the accepted-response context and chosen destination
-> pool selected child Rutters
-> fault on multiplicity when policy permits only one
-> otherwise run children sequentially
-> after all children complete, enter and persist the destination node
```

Persistent requests, accepted responses, action results, and child completions
are history facts, not lifecycle states. Completion records bind to their source
entrance. On recovery, pure/versioned `then` and CaseMakers recompute from the
immutable history prefix before the accepted response, which is supplied
separately. The engine uses later history only to skip stable completed
maker/edge identities.

A child Rutter follows the same rule recursively. The deepest active entered
node is where `next` starts.

## `next` return semantics

`next(response, continue_=True)` automatically runs Python instructions and
child Rutters, then returns only the final entered node that cannot proceed
automatically (normally an LLM, terminal, fault, or uncertain-effect node). It
does not duplicate the traversed path in its return value: that path is already
available from the durable context history.

With `continue_=False`, `next` returns the first node actually entered: a child
start when one intervenes, otherwise the parent-edge target. With
`dry_run=True`, it previews only the parent-edge target using supplied or
already durable results and pure callbacks. It does not persist, evaluate
CaseMakers, run Actions, or start children; unavailable previews fail explicitly
and the actual current node remains unchanged.
