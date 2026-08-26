# Blueprint Discovery Metadata

Discoverable modules declare compact metadata for generated documentation and
repository navigation. The configured vocabulary lives in
`references/blueprint-schema/config.yaml`; the canonical module schema injects those
values as enums. Configuration controls allowed spellings. This document
controls their meaning.

## Selection rules

- Choose exactly one `catalog.domain`: the module's primary direct user outcome.
- List every `catalog.topic` the module directly serves, but not subjects merely
  used by its implementation or dependencies.
- Set `catalog.visibility` from documentation policy, not implementation size.
- List only `activated_by` mechanisms that can actually initiate the module.
- Set `persistent_modifier` only when invocation intentionally changes assistant
  behavior after that invocation ends.

Dependencies, implementation languages, and internal architecture do not
determine catalog metadata. When two domains seem plausible, choose the one that
best answers “why would a user seek this module?” and use topics for the other
directly served concerns.

## Domains

| Value | Use when the primary direct outcome is |
| --- | --- |
| `personal-assistance` | Managing a user's plans, communications, personal information, or everyday actions. |
| `research` | Producing or checking scholarly reasoning, evidence, mathematics, or research documents. |
| `software-development` | Creating, understanding, testing, reviewing, or maintaining software and repositories. |
| `assistant-development` | Creating or changing assistant skills, standards, architecture, assurance, or installation machinery. |
| `assistant-operations` | Operating assistant runtimes, scheduled automation, integrations, storage, synchronization, or host maintenance. |
| `assistant-interaction` | Controlling how a user and assistant collaborate across a request or session. |

## Topics

| Value | Direct capability represented |
| --- | --- |
| `planning` | Constructing or revising plans and schedules. |
| `communications` | Reading, composing, organizing, or acting on messages. |
| `personal-organization` | Maintaining personal tasks, lists, files, or routines. |
| `mathematical-reasoning` | Proving, auditing, or structurally analyzing mathematics. |
| `research-writing` | Drafting or revising scholarly arguments and exposition. |
| `scholarly-documents` | Processing citations, PDFs, LaTeX, or publication artifacts. |
| `visualization` | Producing or interacting with visual representations of structured information. |
| `repository-workflow` | Managing source-control, review, testing, or repository change workflows. |
| `assistant-authoring` | Creating or editing assistant-facing skills, hooks, prompts, or standards. |
| `assistant-architecture` | Designing assistant module boundaries, contracts, and dependency structure. |
| `assistant-assurance` | Validating, certifying, auditing, or detecting drift in assistant components. |
| `assistant-installation` | Installing or propagating assistant components and host integration. |
| `external-integrations` | Connecting assistant behavior to external services or APIs. |
| `storage-and-sync` | Persisting, retrieving, or synchronizing data across locations. |
| `task-automation` | Running repeatable or scheduled work without step-by-step user control. |
| `system-maintenance` | Diagnosing or repairing host-level operational state. |
| `session-management` | Starting, ending, resuming, or handing off assistant sessions. |
| `reasoning-control` | Intentionally changing the assistant's reasoning or collaboration mode. |

## Visibility

| Value | Generated-documentation behavior |
| --- | --- |
| `featured` | Prominently present the module because it is a primary supported entry point. |
| `listed` | Include the module in inventories without giving it primary prominence. |
| `hidden` | Omit it from ordinary generated indexes while retaining valid metadata for tooling. |

## Activation

| Value | Evidence required by the claim |
| --- | --- |
| `user-request` | The host can discover and invoke the skill directly from a matching user request. |
| `skill-workflow` | Another skill can intentionally invoke it through a declared repository interface. |
| `scheduled-job` | A configured scheduler can initiate it without a contemporaneous user request. |

The schema checks configured values, nonempty unique lists, booleans, and the
rule that a persistent modifier includes `reasoning-control`. Repository graph
loading preserves the complete declaration. Documentation tooling validates
the same configured vocabulary and exposes domain, topics, visibility,
activation, and persistence. Claims whose truth depends on runtime behavior or
workflow intent require semantic review; accepting arbitrary valid labels would
give false confidence rather than accurate metadata.
