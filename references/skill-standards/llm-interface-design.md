# LLM Interface Design

Use this guide when deciding whether a skill's hand-authored `SKILL.md` should
stay as one default LLM interface or be decomposed into named interfaces under
`skills/<skill-name>/llm_interfaces/`.

The goal is not shorter files for their own sake. The goal is modular
instruction design: visible entrypoints, one owner for each block of logic, and
the smallest useful prompt context for the average routed task.

## Core Model

An LLM interface is a prompt surface with a public contract. It owns a coherent
use case: routing conditions, required context, operating logic, failure
handling, output shape, and dependency behavior.

`SKILL.md` is the default LLM interface. When it is the only LLM interface, it
may contain the full workflow. When a skill has any non-default LLM interface,
`SKILL.md` should become a router plus shared parent policy:

- identify the available named interfaces
- explain how to choose among them
- state constraints that apply to every interface
- name any runtime state that must be inspected before routing
- avoid restating each interface's detailed procedure

The selected non-default interface should contain the detailed procedure for
its use case. After restructuring the Markdown, update `blueprint.yaml` and
refresh generated blocks so the declared interfaces match the new file layout.

## Markdown Scope

Hand-authored Markdown is for LLM decision logic, not generated interface
catalogs. Do not list dispatcher syntax, machine-interface argument templates,
or generated interface descriptions in `SKILL.md` or `llm_interfaces/*.md`.
Those details belong in `blueprint.yaml` and the generated blocks.

This restriction applies to hand-authored Markdown only. Generated blueprint
blocks injected into `SKILL.md` are out of scope for this guide; do not edit
around them by hand.

LLM Markdown may name the interface it intends to use, but it should not
re-explain that interface. For example, an LLM interface may say to use the
mail-reading interface after choosing the account and date filter, but it
should not copy the interface's invocation form or option list.

## Context Loading Rule

Design for routing before loading detailed instructions. A normal routed task
should need:

1. `SKILL.md` shared policy and router
2. exactly the selected `llm_interfaces/<name>.md`
3. only the shared reference files needed by that selected interface

Use an `@` include only for a reference required by every route through the
binding LLM interface. For route-specific material, name the file in the
route's instructions and state the observable condition for reading it. Do not
use `@` for conditional loading because it loads the file before routing.

Do not design a split where every task still needs to read every interface
file. That preserves file modularity but loses the main runtime benefit:
reducing irrelevant prompt context.

Some tasks genuinely need multiple interfaces. When that is expected, make the
composition explicit in the router. Do not rely on a model discovering hidden
cross-file dependencies by reading the whole skill directory.

When one routed interface produces facts needed by another, make that state
explicit. The downstream interface should say what facts it expects, such as a
diagnosis summary, selected account, approved plan, target file list, or prior
command output. Do not require it to reload sibling interface files to infer
what state should have been produced.

For staged workflows, prefer a report/apply split:

- one interface performs read-only diagnosis, audit, or planning, and produces
  the proposed facts or changes
- a second interface applies approved changes and states exactly what prior
  report, selected items, or approval it expects
- shared safety policy, such as approval-before-write, stays in `SKILL.md`

## Split By Use Case Logic

Create a separate `llm_interfaces/<name>.md` file when a use case needs
distinct logic for more than a few paragraphs.

Separate logic includes:

- different preconditions or setup state
- different user questions before work can begin
- different required background files or sub-skills
- different read-only versus mutating behavior
- different failure handling
- different output shape or success criteria
- different security, privacy, network, or filesystem posture
- different reasons to change over time

Do not split merely because a section is long. Split when the section has its
own operational contract or when changes to that logic should not require
reviewing unrelated workflows.

## Single Owner For Logic

A behavioral rule, checklist, failure policy, routing condition, output
contract, or procedure must have one canonical home. Do not copy the same logic
between `SKILL.md` and one or more interface files.

Use this placement rule:

- logic used by exactly one interface belongs in that interface file
- logic used by all interfaces belongs in `SKILL.md`
- logic used by some, but not all, interfaces belongs in the nearest shared
  parent, or in a skill-local `references/` file loaded by each interface that
  needs it
- repo-wide conventions belong in shared `references/` material, not in a
  skill-local copy

References are better than paraphrases. If two files need the same rule, one
file should own the rule and the other should point to it. Otherwise one copy
will eventually change without the other, producing inconsistent behavior.

## LLM Interfaces Over Machine Interfaces

Do not create a non-default LLM interface that is only a wrapper around one
machine interface and adds no decision logic. If the whole Markdown file would
only say "use interface X", the split adds routing overhead without reducing
instruction complexity.

Create an LLM interface over one or more machine interfaces when the LLM layer
owns real choices around that call, such as:

- deciding whether the operation is appropriate
- collecting or checking required state before the call
- choosing among nearby machine interfaces
- selecting inputs while preserving user intent and safety constraints
- asking for approval before a mutating action
- interpreting output and deciding the next routed step
- composing several machine interfaces into one user-facing workflow

In that case, the LLM interface owns the decision procedure around the
capability, while the generated interface material owns invocation details.
Keep that boundary visible: the Markdown explains when and why to use the
capability, not how to invoke it.

## Visibility And Routing Descriptions

Every non-default LLM interface should be discoverable without opening its full
Markdown body. Its short description in the router should be good enough to
choose it on a first attempt.

The description should state:

- the use case it owns
- when to choose it instead of nearby interfaces
- whether it is read-only or mutating
- the key input or runtime state it expects
- the output or decision it returns

Avoid vague names such as `advanced`, `misc`, `flow`, or `mode`. Prefer names
that reveal the contract, such as `install-tooling`, `uninstall-tooling`,
`diagnose-failure`, `apply-repair`, `create-skill`, or `edit-skill`.

## Interface File Shape

Each `llm_interfaces/<name>.md` should work when loaded out of sequence after
`SKILL.md`. It should not depend on the model having read sibling interfaces.

A useful interface file normally states:

- what this interface does and does not own
- required context to inspect before acting
- the workflow or decision procedure
- side effects and approval points
- failure handling
- expected output shape
- reference files it relies on

Keep shared policy out of the interface file unless this interface is the only
consumer of that policy. Keep generated invocation details out of the interface
file even when the workflow uses a machine interface.

## Examples Of Good Splits

Installation and removal are separate interfaces when they require different
logic. An install interface may check prerequisites, bootstrap credentials,
create files, and verify availability. An uninstall interface may stop timers,
remove generated files, preserve user data, and report cleanup limits.

Read-only diagnosis and repair are separate interfaces when repair changes
state. Diagnosis can gather evidence, identify likely causes, and recommend a
path. Repair can ask for approval, write files, run migrations, or update
configuration.

Creation and editing are separate interfaces when they ask different questions
or enforce different invariants. Creating a new artifact may require naming,
initial structure, and bootstrap choices. Editing an existing artifact may
require preserving local conventions, respecting unrelated dirty state, and
checking compatibility with existing contracts.

Provider-specific workflows are separate interfaces when provider behavior
changes the procedure, required state, or failure handling. Keep shared account
or safety policy in the parent; keep provider-specific logic in the provider
interface.

## When Not To Split

Keep logic in `SKILL.md` when the use cases share one procedure and differ only
by small parameter choices, output verbosity, or examples. Keep shared policy
in `SKILL.md` when every interface must obey it.

Keep short shared-policy skills default-only. If the hand-authored body is a
compact rule list and a split would produce files that only name an operation or
say "use interface X", do not split it yet.

Avoid thin interfaces that only say "use the default interface" or duplicate
another interface with minor wording changes. A named LLM interface should
remove meaningful routing, loading, or maintenance complexity.

Avoid speculative splits. If the router cannot explain when to choose the new
interface, or if every task would still need all interface files, the split is
probably premature.

## Review Questions

Ask these before adding substantial instructions to `SKILL.md`:

- Is this a new use case or shared policy?
- Would a routed task be better if it loaded only this procedure instead of the
  whole skill?
- Does this use case have its own setup, questions, side effects, failure
  handling, or output contract?
- Is this logic likely to change independently from the rest of the skill?
- Is the same rule already stated somewhere else?
- If several interfaces need this rule, what is their nearest shared parent?
- Can the router choose this interface without reading its full body?

If the answer is yes to several of these, define a named LLM interface. If the
answer is yes only because the prose is long, tighten the prose before adding a
new interface.
