# Compass, Voyage dispensers, and Rutter

Rutter owns durable algorithm traversal. A `VoyageDispenser` is the
process-safe public boundary for creating and operating an authorized
collection of Voyages. Compass is the thin LLM-facing controller that follows
that dispenser contract.

The boundary is deliberate:

- Rutter owns evolution entry, validation, routing, machine work, transition
  hooks, nested Rutters, faults, recovery, and durable history.
- A configured dispenser owns initialization modes, the arguments required by
  each mode, run isolation, Voyage discovery, and release of terminal working
  directories.
- Compass invokes only the dispenser's versioned process interface. It assigns
  each returned Voyage ID to one agent and never passes Python `Rutter` or
  `Voyage` objects through the prompt boundary.
- Each Voyage agent follows only the public status, validation, and advance
  results for its assigned ID. It does not infer progress from conversation
  history or inspect Rutter internals.

## Self-describing dispenser interface

The invoker supplies one authorized `VoyageDispenser` process binding. Every
dispenser has the same operations:

| Operation | Purpose |
|---|---|
| `help` | Explain the complete multi-agent operating workflow and the dispenser's modes. |
| `modes` | Return the default mode and every mode's explanation and required arguments. |
| `initiate [mode]` | Create one run's durable Voyages and return their opaque IDs. |
| `list` | List all currently authorized Voyage IDs. |
| `list --run-prefix PREFIX` | List only the Voyages initialized for one prefix. |
| `status VOYAGE_ID` | Read one Voyage's current public state. |
| `validate VOYAGE_ID` | Validate a response without mutating the Voyage. |
| `advance VOYAGE_ID` | Advance one Voyage, with a validated response when required. |
| `release VOYAGE_ID` | Delete one terminal Voyage's working directory. |

Compass begins with `help`; it does not depend on Rutter-specific Python
docstrings or runtime object introspection. The dispenser's blueprint controls
which callers may invoke the process binding, while `help` and `modes` explain
how an authorized caller should use it. Runtime self-description does not grant
access or replace versioned dependency pins.

## Modes and initialization

A dispenser declares one or more initialization modes. Each mode has a
caller-facing explanation and a complete set of required arguments. The first
declared mode is the default, so omitting the positional mode selects it.
Compass must inspect `modes` rather than guess a mode or its inputs.

Initialization inputs describe the work to create, not the dispenser's
internal storage. For example, an inventory dispenser may require a document
entrypoint and chunk count in its default mode, while a debug mode additionally
requires a gold-standard path so it can attach diagnostic hooks.

`initiate` is run-scoped. It fails if that run prefix is already initialized,
and it returns only the Voyage IDs created for the selected run. A mode's
required arguments must be supplied exactly; missing or unexpected arguments
are usage errors.

## Run prefixes and Voyage IDs

`--run-prefix` isolates independently initialized runs of the same dispenser.
When it is omitted, the selected mode name is the prefix. A caller can therefore
use the conventional `default` and `debug` runs directly, or supply a distinct
prefix for repeated or concurrent work.

Every Voyage ID begins with its run prefix, for example
`debug-voyage-7f3a...`. The complete ID remains the authority used by `status`,
`validate`, `advance`, and `release`; those operations do not take a separate
prefix.

A bare `list` is a global inventory across the dispenser's active runs.
`list --run-prefix PREFIX` is the scoped discovery operation for one run.
Compass must not treat an unscoped list as the assignment set for a newly
requested run.

## Compass operating loop

The controller follows this sequence:

1. Invoke `help`, then `modes` when initialization may be required.
2. Select the requested run prefix, or let `initiate` default it to the selected
   mode.
3. Invoke the prefix-scoped `list`. If that run is not initialized and all
   advertised mode arguments are available, invoke `initiate` exactly once.
4. Assign exactly one independent agent to every Voyage ID returned for that
   run. Agents do not share or switch IDs.
5. Each agent reads `status`. For a Message, it performs the instruction,
   validates its response, and advances only after successful validation. For
   ready automatic work, it advances without a response.
6. Each agent reads fresh status after every successful advance and stops on a
   terminal result, fault, uncertain result, malformed result, or unknown
   status.
7. After retaining a terminal result, the agent invokes `release` unless it has
   an explicit reason to preserve the working directory. Nonterminal or
   uncertain Voyages must not be released.
8. The controller finishes only after every assigned agent has stopped.

If the dispenser binding, initialization inputs, returned IDs, or public
results are missing or malformed, Compass reports a public-interface gap. It
does not repair the gap by inspecting storage or constructing private runtime
objects.

## Durable storage and resume

The dispenser implementation owns its storage location. Callers do not supply
a run directory to lifecycle operations. A configured dispenser may organize
its state as `voyages/<run-prefix>/<voyage-id>/` and its durable products as
`artifacts/<run-prefix>/`, but those paths remain implementation details rather
than Compass arguments.

The Voyage's persisted Reckoning, not session memory, selects the active
evolution after a process or interaction restart. Because Voyage IDs are
globally resolvable within the dispenser, a fresh process can reopen an assigned
ID and continue through the same `status`, `validate`, and `advance` interface.
Release is the explicit end of that durable working-directory lifetime.
