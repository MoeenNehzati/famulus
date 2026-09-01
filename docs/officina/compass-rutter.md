# Compass, Voyage dispensers, and Rutter

Some algorithms cannot safely live in one LLM conversation. They have durable
state, constrained transitions, machine work between decisions, and a need to
resume after the process or conversation ends. Rutter is the subsystem that
owns that problem. It is not required for basic Officina onboarding.

Four concepts define the relationship:

1. A **Rutter** defines the allowed evolutions, transition behavior,
   validation, machine work, faults, and recovery for one durable algorithm.
2. A **Voyage** is one persisted traversal of that Rutter. Its stored
   Reckoning—not conversation history—determines where execution resumes.
3. A **VoyageDispenser** is the process-safe authority for creating and
   operating an authorized collection of Voyages through opaque IDs.
4. **Compass** is the thin LLM-facing controller. It follows the dispenser
   contract, assigns Voyages to agents, and never reaches into Rutter storage or
   passes Python runtime objects through the prompt boundary.

The flow is therefore `Compass -> VoyageDispenser -> Voyage -> Rutter`.
Compass sees only versioned dispenser operations and public Voyage results.
The dispenser owns run isolation and storage; the Voyage owns persisted
progress; the Rutter owns which transition is valid. A fresh process can resume
an existing Voyage by its ID without reconstructing state from the transcript.

For the main documentation path, see the [Overview](README.md) and [Getting
Started](getting-started.md). See [Dispatcher](dispatcher.md) for the authorized
process boundary and the [Utility Map](utility-map.md) for current implementation
ownership.

## Authority boundaries

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

Every `initiate` call creates a fresh run and returns only the Voyage IDs for
that run. Reusing a prefix creates another run inside the same caller-selected
group. A mode's required arguments must be supplied exactly; missing or
unexpected arguments are usage errors.

## Run prefixes and Voyage IDs

`--run-prefix` isolates independently initialized runs of the same dispenser.
It is an optional grouping label, not a run identifier. The dispenser creates a
fresh `r-<uuid>` run for every initiation. Without a prefix, Voyage IDs have the
form `r-<uuid>/<numeric-index>`; with one, they have the form
`<prefix>/r-<uuid>/<numeric-index>`.

The complete Voyage ID remains the authority used by `status`, `validate`,
`advance`, and `release`; those operations do not take a separate prefix.

A bare `list` is a global inventory across the dispenser's active runs.
`list --run-prefix PREFIX` is recovery and discovery for all retained runs in
one prefix group. It may contain several runs, so neither form is the assignment
set for a fresh initiation.

## Compass operating loop

The controller follows this sequence:

1. Invoke `help`, then `modes` when initialization may be required.
2. Select the mode, its required arguments, and an optional grouping prefix.
3. Invoke `initiate` exactly once and retain the Voyage IDs it returns. Use
   `list`, optionally scoped by prefix, only to recover or inspect retained work.
4. Assign exactly one independent agent to every Voyage ID returned by that
   initiation. Agents do not share or switch IDs.
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
