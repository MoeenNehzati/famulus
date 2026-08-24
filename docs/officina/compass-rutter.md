# Compass and Rutter

Rutter owns durable algorithm traversal. Compass is the thin LLM-facing
operator for one invoker-provided `Voyage`.

The boundary is deliberate:

- Rutter owns evolution entry, validation, routing, machine work, transition
  hooks, nested Rutters, faults, recovery, and durable history.
- Compass follows only the public operating contract supplied by its authorized
  Voyage. It does not infer progress from conversation history or inspect
  Rutter internals.

## Self-describing operating interface

`Voyage` is the only runtime object Compass operates. Compass first invokes
`voyage.help()`, which returns the bound signatures and normalized docstrings of
the methods listed by `Voyage.compass_facing_methods`, in declared order.

Those method docstrings are the source of truth for the operating loop,
validation, continuation, stopping, and preview behavior. Compass uses only the
advertised methods and stops with a public-interface gap if the binding or help
text is missing or malformed.

The formal blueprint interface still controls which modules may receive and
operate a Voyage. Runtime self-description explains authorized use; it does not
grant access or replace versioned dependency pins.

## Durable resume

The Voyage's persisted authority, not session memory, selects the active
evolution after an interaction restart. Its advertised operating contract
continues from that durable state without exposing storage, child traversal, or
recovery internals to Compass.
