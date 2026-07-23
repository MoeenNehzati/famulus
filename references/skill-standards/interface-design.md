# Interface Design

Use this guide when designing a skill's source-owned interfaces, especially
when deciding whether hand-authored `SKILL.md` should remain the gateway of one
instruction source or route to additional instruction sources under
`skills/<skill-name>/instructions/`.

The goal is coherent interface contracts and modular instruction design:
visible entrypoints, one owner for each block of logic, and the smallest useful
prompt context for the average routed task.

## Core Model

An interface is a named contract owned by one `behavioral_source`. Gateway
language and process binding are orthogonal to that contract; they do not
create interface node types or type-specific namespaces. A module may export a
source-owned interface without copying its contract.

An instruction source is a behavioral source whose gateway language is natural
language. It owns the routing conditions, required context, operating logic,
failure handling, and output decisions that its interfaces rely on. The source
blueprint owns each interface contract. Hand-authored instructions implement
the model-facing decision procedure around that contract.

`SKILL.md` is the module gateway and the gateway file of its primary
instruction source. When that is the only instruction source, it may contain
the full workflow. When a module has routed instruction sources, `SKILL.md`
should become a router plus shared parent policy:

- identify the available routed instruction sources
- explain how to choose among them
- state constraints that apply to every route
- name runtime state that must be inspected before routing
- avoid restating each source's detailed procedure

The selected instruction source should contain the detailed procedure for its
use case. After restructuring the Markdown, update the module and source
blueprints, then refresh generated blocks so containment, gateways,
source-owned interfaces, and exports match the file layout.

## Interface Contracts And Markdown Scope

A source-owned interface contract must state what callers need without
implementation inspection. The source blueprint owns its identity, version,
description, inputs, outputs, preconditions, outcomes, effects, lifecycle, and
optional binding. The module blueprint owns only any public export and its
access policy.

Hand-authored Markdown is for model decision logic, not a second interface
catalog. Do not list dispatcher syntax, process-binding argument templates, or
generated interface descriptions in `SKILL.md` or `instructions/*.md`. Those
facts belong in the authored blueprint graph and its generated views.

Instruction Markdown may name the interface it intends to use, but it should
not re-explain the contract. For example, an instruction source may say to use
the mail-reading interface after choosing the account and date filter, but it
should not copy the interface's invocation form or option list.

## Context Loading Rule

Design for routing before loading detailed instructions. A normal routed task
should need:

1. `SKILL.md` shared policy and router
2. exactly the selected `instructions/<name>.md`
3. only the reference files needed by that selected source

Use an `@` include only when every instruction route through the source-owned
interface needs the reference. For route-specific material, name the file in
the route's instructions and state the observable condition for reading it. Do
not use `@` for conditional loading because it loads the file before routing.

Do not design a split where every task still needs to read every instruction
file. That preserves file modularity but loses the main runtime benefit:
reducing irrelevant prompt context.

Some tasks genuinely need multiple instruction sources. When that is expected,
make the composition explicit in the router. Do not rely on a model discovering
hidden cross-file dependencies by reading the whole skill directory.

When one routed source produces facts needed by another, make that state
explicit. The downstream source should state what facts it expects, such as a
diagnosis summary, selected account, approved plan, target file list, or prior
command output. Do not require it to reload sibling instruction files to infer
what state should have been produced.

For staged workflows, prefer a report/apply split:

- one source performs read-only diagnosis, audit, or planning and produces the
  proposed facts or changes
- a second source applies approved changes and states exactly what prior
  report, selected items, or approval it expects
- shared safety policy, such as approval-before-write, stays in `SKILL.md`

## Split By Use-Case Logic

Create a separate `instructions/<name>.md` gateway when a use case needs
distinct logic for more than a few paragraphs. Separate logic includes:

- different preconditions or setup state
- different user questions before work can begin
- different required background files or dependencies
- different read-only versus mutating behavior
- different failure handling
- different output shape or success criteria
- different security, privacy, network, or filesystem posture
- different reasons to change over time

Do not split merely because a section is long. Split when the section has its
own operational contract or when changes to that logic should not require
reviewing unrelated workflows.

Source decomposition and interface decomposition are related but distinct. A
new instruction gateway requires a new behavioral source. That source may
declare one or more interfaces. Do not invent a new interface type merely to
represent the gateway language.

## Single Owner For Logic

A behavioral rule, checklist, failure policy, routing condition, output
contract, or procedure must have one canonical home. Do not copy the same logic
between `SKILL.md`, instruction files, and interface descriptions.

Use this placement rule:

- interface contract facts belong in the owning source blueprint
- logic used by exactly one routed source belongs in that source's gateway file
- logic used by every route belongs in `SKILL.md`
- logic used by some sources belongs in the nearest shared parent or a
  module-local `references/` file read by each source that needs it
- repo-wide conventions belong in shared `references/` material

References are better than paraphrases. If two files need the same rule, one
file should own it and the other should point to it.

## Instruction Sources Around Process-Bound Interfaces

Do not create a non-gateway instruction source that is only a wrapper around
one process-bound interface and adds no decision logic. If the whole Markdown
file would only say "use interface X", the split adds routing overhead without
reducing instruction complexity.

Create an instruction source around one or more process-bound interfaces when
the model layer owns real choices around the call, such as:

- deciding whether the operation is appropriate
- collecting or checking required state before the call
- choosing among nearby source-owned interfaces
- selecting inputs while preserving user intent and safety constraints
- asking for approval before a mutating action
- interpreting output and deciding the next routed step
- composing several interfaces into one user-facing workflow

The instruction source owns that decision procedure. The source blueprint owns
the interface contract and binding, and generated material presents those
facts. Keep the boundary visible: Markdown explains when and why to use the
capability, not how to invoke it.

## Visibility And Routing Descriptions

Every routed instruction source should be discoverable without opening its full
gateway body. Its router description should state:

- the use case it owns
- when to choose it instead of nearby routes
- whether it is read-only or mutating
- the key input or runtime state it expects
- the output or decision it returns

Each source-owned interface needs a similarly precise blueprint description.
Avoid vague names such as `advanced`, `misc`, `flow`, or `mode`. Prefer names
that reveal the contract, such as `install-tooling`, `uninstall-tooling`,
`diagnose-failure`, `apply-repair`, `create-skill`, or `edit-skill`.

## Instruction Gateway File Shape

Each `instructions/<name>.md` should work when loaded out of sequence after
`SKILL.md`. It should not depend on the model having read sibling sources. A
useful instruction gateway normally states:

- what this source does and does not own
- required context to inspect before acting
- the workflow or decision procedure
- side effects and approval points
- failure handling
- expected output shape
- reference files it relies on

Keep shared policy out of the instruction file unless this source is the only
consumer. Keep generated invocation details out even when the workflow uses a
process-bound interface.

## Examples Of Good Splits

Installation and removal are separate sources when they require different
logic. An install source may check prerequisites, bootstrap credentials, create
files, and verify availability. An uninstall source may stop timers, remove
generated files, preserve user data, and report cleanup limits.

Read-only diagnosis and repair are separate sources when repair changes state.
Diagnosis can gather evidence, identify likely causes, and recommend a path.
Repair can ask for approval, write files, run migrations, or update
configuration.

Creation and editing are separate sources when they ask different questions or
enforce different invariants. Creating an artifact may require naming, initial
structure, and bootstrap choices. Editing one may require preserving local
conventions, respecting unrelated dirty state, and checking compatibility with
existing contracts.

Provider-specific workflows are separate sources when provider behavior
changes the procedure, required state, or failure handling. Keep shared account
or safety policy in the parent; keep provider-specific logic in its source.

## When Not To Split

Keep logic in `SKILL.md` when the use cases share one procedure and differ only
by small parameter choices, output verbosity, or examples. Keep shared policy
there when every route must obey it.

Keep short shared-policy skills gateway-only. Avoid thin sources that only say
"use interface X", duplicate another source with minor wording changes, or
leave every task needing every instruction file. If the router cannot explain
when to choose the new source, tighten the prose before adding one.

## Review Questions

Ask these before adding substantial instructions to `SKILL.md`:

- Is this a new use case, a new interface contract, or shared policy?
- Would a routed task benefit from loading only this procedure?
- Does this use case have its own setup, questions, side effects, failure
  handling, or output contract?
- Is this logic likely to change independently?
- Is the same rule already stated somewhere else?
- If several sources need this rule, what is their nearest shared parent?
- Can the router choose this source without reading its full body?
- Does every interface remain owned by exactly one behavioral source?

If several answers support a split, define a routed instruction source and its
source-owned interface contracts. If the only issue is prose length, tighten
the prose first.
