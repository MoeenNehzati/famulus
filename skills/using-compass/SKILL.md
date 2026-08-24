---
name: using-compass
description: Use when a user or another skill directs the agent to use a named compass.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: task-automation, session-management; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 11

Uses Interfaces:
- `using-compass.source.gateway -> rutter.interface.dispenser@4`

Public Interfaces:
- `using-compass.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `using-compass.interface.default` — Read the dispenser help, initialize or select one run prefix, assign one agent per returned Voyage ID, and keep every agent scoped to its assignment.
<!-- END BLUEPRINT INTERFACES -->
# Using Compass

`Use compass on <rutter-name>`.

Use one invoker-provided authorized `VoyageDispenser` process binding supplied
with the activation request. If it is absent, report a public-interface gap and
stop.

First invoke `help` and follow the returned operating contract. If the activation
supplies a run prefix, use that same prefix for both `list` and any required
`initiate`; otherwise let `initiate` default the prefix to its selected mode.
Invoke the appropriately scoped `list`. When that run is not initialized and
the activation supplies the advertised mode arguments, invoke `modes`, then
`initiate` exactly once and use only the IDs it returns. If initialization inputs
are absent, report a public-interface gap instead of inventing them. Act as the
controller: assign exactly one agent to each returned `voyage_id`, giving it the
dispenser binding and its assigned `voyage_id`.
Agents must not share or switch Voyage IDs. Do not start any Voyage agent until
every returned ID has an assigned agent. If an independent agent cannot be
assigned to every Voyage, report a public-interface gap and stop.

Each Voyage agent invokes `status` only with its assigned ID. For a ready
Message, it performs the instructions and payload, produces a response
satisfying the optional response schema, invokes `validate`, and invokes
`advance` only after validation succeeds. For ready automatic work, it invokes
`advance` without a response. It reads a fresh `status` after every successful
advance and stops on terminal, fault, uncertain, malformed, or unknown status.
After reading and retaining a terminal result, it invokes `release` for its
assigned Voyage unless there is an explicit reason to preserve that Voyage's
working directory. It reports that reason when it preserves the directory and
never releases a ready, faulted, uncertain, malformed, or unknown Voyage.
The controller must wait for every Voyage agent to stop before finishing.
